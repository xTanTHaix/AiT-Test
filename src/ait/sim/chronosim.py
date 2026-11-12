"""Deterministic Simulation Testing (DST) engine using pure Python stdlib."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import importlib.util
import inspect
import random
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple


@dataclasses.dataclass(frozen=True)
class FaultProfile:
    """Deterministic fault injection parameters controlled by a single RNG seed."""

    drop_rate: float = 0.0
    timeout_rate: float = 0.0
    reorder_rate: float = 0.0
    min_delay_ms: float = 1.0
    max_delay_ms: float = 25.0
    max_steps: int = 10_000


@dataclasses.dataclass(frozen=True)
class SimResult:
    """Outcome of a single deterministic simulation run."""

    passed: bool
    seed: int
    virtual_time_ms: float
    steps: int
    faults_injected: int
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    traceback_text: Optional[str] = None
    repro_command: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class SimSweepResult:
    """Outcome of sweeping multiple seeds to uncover flaky concurrency/timing bugs."""

    total_runs: int
    passed_runs: int
    failed_runs: int
    first_failing_seed: Optional[int]
    results: Tuple[SimResult, ...] | List[SimResult]


class VirtualClock:
    """Deterministic virtual clock that advances instantaneously without real sleep."""

    def __init__(self, start_sec: float = 1_700_000_000.0) -> None:
        self._now_sec = float(start_sec)
        self._monotonic_sec = 0.0
        self.steps = 0
        self._lock = threading.Lock()

    def time(self) -> float:
        return self._now_sec + self._monotonic_sec

    def monotonic(self) -> float:
        return self._monotonic_sec

    def advance(self, seconds: float) -> float:
        delta = max(0.0, float(seconds))
        with self._lock:
            self._monotonic_sec += delta
            self.steps += 1
            return self._monotonic_sec


class SimChannel:
    """Deterministic fault-injecting async/sync message channel driven by RNG + VirtualClock."""

    def __init__(
        self,
        clock: VirtualClock,
        rng: random.Random,
        faults: Optional[FaultProfile] = None,
    ) -> None:
        self.clock = clock
        self.rng = rng
        self.faults = faults or FaultProfile()
        self.queue: List[Any] = []
        self.faults_injected = 0
        self._lock = threading.Lock()

    async def send(self, payload: Any) -> bool:
        """Send a payload; may deterministically drop, delay, or reorder based on seed."""
        if self.faults.drop_rate > 0.0 and self.rng.random() < self.faults.drop_rate:
            with self._lock:
                self.faults_injected += 1
            return False

        if self.faults.timeout_rate > 0.0 and self.rng.random() < self.faults.timeout_rate:
            with self._lock:
                self.faults_injected += 1
            self.clock.advance(self.faults.max_delay_ms / 1000.0)
            raise TimeoutError("ChronoSim injected deterministic channel timeout")

        delay_ms = self.rng.uniform(self.faults.min_delay_ms, self.faults.max_delay_ms)
        self.clock.advance(delay_ms / 1000.0)

        if (
            self.queue
            and self.faults.reorder_rate > 0.0
            and self.rng.random() < self.faults.reorder_rate
        ):
            with self._lock:
                self.faults_injected += 1
                idx = self.rng.randint(0, len(self.queue))
                self.queue.insert(idx, payload)
        else:
            with self._lock:
                self.queue.append(payload)
        await asyncio.sleep(0)
        return True

    async def recv(self) -> Any:
        """Pop the next queued payload, advancing virtual clock slightly."""
        await asyncio.sleep(0)
        if not self.queue:
            raise EOFError("SimChannel queue is empty")
        return self.queue.pop(0)


@contextlib.contextmanager
def _deterministic_env(
    clock: VirtualClock,
    rng: random.Random,
    max_steps: int,
) -> Iterator[None]:
    """Patch time/random/asyncio.sleep cleanly for the duration of one simulation."""
    orig_time = time.time
    orig_monotonic = time.monotonic
    orig_perf = time.perf_counter
    orig_sleep = time.sleep
    orig_async_sleep = asyncio.sleep
    orig_random_state = random.getstate()

    def _sim_sleep(secs: float) -> None:
        if clock.steps >= max_steps:
            raise RuntimeError(f"ChronoSim max_steps ({max_steps}) exceeded (possible infinite loop)")
        clock.advance(secs)

    async def _sim_async_sleep(delay: float, result: Any = None) -> Any:
        if clock.steps >= max_steps:
            raise RuntimeError(f"ChronoSim max_steps ({max_steps}) exceeded (possible infinite loop)")
        clock.advance(delay)
        await orig_async_sleep(0)
        return result

    random.setstate(rng.getstate())
    time.time = clock.time
    time.monotonic = clock.monotonic
    time.perf_counter = clock.monotonic
    time.sleep = _sim_sleep
    asyncio.sleep = _sim_async_sleep
    try:
        yield
    finally:
        time.time = orig_time
        time.monotonic = orig_monotonic
        time.perf_counter = orig_perf
        time.sleep = orig_sleep
        asyncio.sleep = orig_async_sleep
        random.setstate(orig_random_state)


class ChronoRunner:
    """Single-seed and multi-seed deterministic simulation runner."""

    def __init__(self, fault_profile: Optional[FaultProfile] = None) -> None:
        self.fault_profile = fault_profile or FaultProfile()

    def run_once(
        self,
        target: Callable[..., Any],
        *,
        seed: int = 0,
        target_label: str = "<callable>",
    ) -> SimResult:
        """Execute a sync or async callable under deterministic single-seed control."""
        clock = VirtualClock()
        rng = random.Random(seed)
        channel = SimChannel(clock, rng, self.fault_profile)

        # Pass channel/rng/clock only if target explicitly accepts them
        sig = inspect.signature(target)
        kwargs: Dict[str, Any] = {}
        for param_name, injected in (("clock", clock), ("rng", rng), ("channel", channel), ("seed", seed)):
            if param_name in sig.parameters:
                kwargs[param_name] = injected

        try:
            with _deterministic_env(clock, rng, self.fault_profile.max_steps):
                if inspect.iscoroutinefunction(target):
                    asyncio.run(target(**kwargs))
                else:
                    res = target(**kwargs)
                    if inspect.isawaitable(res):
                        asyncio.run(res)
            return SimResult(
                passed=True,
                seed=seed,
                virtual_time_ms=round(clock.monotonic() * 1000.0, 3),
                steps=clock.steps,
                faults_injected=channel.faults_injected,
            )
        except Exception as exc:
            return SimResult(
                passed=False,
                seed=seed,
                virtual_time_ms=round(clock.monotonic() * 1000.0, 3),
                steps=clock.steps,
                faults_injected=channel.faults_injected,
                error_type=type(exc).__name__,
                error_message=str(exc),
                traceback_text=traceback.format_exc(),
                repro_command=f"python cli.py sim {target_label} --seed {seed}",
            )

    def explore_seeds(
        self,
        target: Callable[..., Any],
        *,
        start_seed: int = 0,
        count: int = 20,
        stop_on_fail: bool = True,
        target_label: str = "<callable>",
    ) -> SimSweepResult:
        """Run deterministic simulations across a range of seeds."""
        results: List[SimResult] = []
        first_fail: Optional[int] = None
        for offset in range(max(1, count)):
            seed = start_seed + offset
            res = self.run_once(target, seed=seed, target_label=target_label)
            results.append(res)
            if not res.passed and first_fail is None:
                first_fail = seed
                if stop_on_fail:
                    break
        passed_runs = sum(1 for r in results if r.passed)
        return SimSweepResult(
            total_runs=len(results),
            passed_runs=passed_runs,
            failed_runs=len(results) - passed_runs,
            first_failing_seed=first_fail,
            results=results,
        )


def load_target_callable(file_path: str | Path, func_name: str = "main") -> Callable[..., Any]:
    """Dynamically load a callable from a Python file for `ait sim`."""
    path = Path(file_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Simulation target not found: {path}")
    spec = importlib.util.spec_from_file_location(f"ait_sim_{path.stem}", str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module spec from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    fn = getattr(module, func_name, None)
    if not callable(fn):
        raise AttributeError(f"Callable '{func_name}' not found in {path}")
    return fn


def run_simulation(
    target: Callable[..., Any],
    *,
    seed: int = 0,
    fault_profile: Optional[FaultProfile] = None,
) -> SimResult:
    """Convenience wrapper to run a single deterministic simulation."""
    return ChronoRunner(fault_profile).run_once(target, seed=seed)


def explore_seeds(
    target: Callable[..., Any],
    *,
    start_seed: int = 0,
    count: int = 20,
    fault_profile: Optional[FaultProfile] = None,
    stop_on_fail: bool = True,
) -> SimSweepResult:
    """Convenience wrapper to sweep multiple seeds."""
    return ChronoRunner(fault_profile).explore_seeds(
        target,
        start_seed=start_seed,
        count=count,
        stop_on_fail=stop_on_fail,
    )
