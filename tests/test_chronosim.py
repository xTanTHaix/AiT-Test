"""Tests for ChronoSim deterministic simulation testing and clean v1.1.0 CLI output."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests._support import FIXTURES, REPO_ROOT

from ait.sim import FaultProfile, explore_seeds, run_simulation

CLI = REPO_ROOT / "cli.py"


class ChronoSimTests(unittest.TestCase):
    def test_virtual_clock_runs_long_async_sleeps_instantly(self) -> None:
        async def sleepy_task(clock) -> None:
            t0 = clock.monotonic()
            await asyncio.sleep(30.0)
            await asyncio.sleep(15.0)
            self.assertAlmostEqual(clock.monotonic() - t0, 45.0, places=3)

        real_t0 = time.perf_counter()
        res = run_simulation(sleepy_task, seed=7)
        real_elapsed = time.perf_counter() - real_t0

        self.assertTrue(res.passed, res.traceback_text)
        self.assertAlmostEqual(res.virtual_time_ms, 45_000.0, places=1)
        self.assertLess(real_elapsed, 1.0)

    def test_single_seed_deterministically_reproduces_flaky_fault(self) -> None:
        async def flaky_sender(channel) -> None:
            for i in range(10):
                ok = await channel.send(f"msg-{i}")
                if not ok:
                    raise RuntimeError(f"packet dropped at {i}")

        profile = FaultProfile(drop_rate=0.25)
        sweep = explore_seeds(flaky_sender, start_seed=0, count=25, fault_profile=profile)
        self.assertIsNotNone(sweep.first_failing_seed)
        bad_seed = sweep.first_failing_seed
        assert bad_seed is not None

        replay_1 = run_simulation(flaky_sender, seed=bad_seed, fault_profile=profile)
        replay_2 = run_simulation(flaky_sender, seed=bad_seed, fault_profile=profile)
        self.assertFalse(replay_1.passed)
        self.assertEqual(replay_1.error_message, replay_2.error_message)
        self.assertEqual(replay_1.virtual_time_ms, replay_2.virtual_time_ms)

    def test_max_steps_watchdog_catches_infinite_sleep_loop(self) -> None:
        async def infinite_loop() -> None:
            while True:
                await asyncio.sleep(0.1)

        res = run_simulation(infinite_loop, seed=1, fault_profile=FaultProfile(max_steps=20))
        self.assertFalse(res.passed)
        self.assertEqual(res.error_type, "RuntimeError")
        self.assertIn("max_steps", res.error_message or "")

    def test_cli_sim_subcommand_json_and_repro(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sim_target.py"
            target.write_text(
                "import asyncio\n"
                "async def main(channel) -> None:\n"
                "    for i in range(8):\n"
                "        if not await channel.send(i):\n"
                "            raise RuntimeError(f'drop at {i}')\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "sim",
                    str(target),
                    "--seeds",
                    "20",
                    "--drop-rate",
                    "0.3",
                    "--json",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            self.assertIsNotNone(data["first_failing_seed"])

    def test_clean_default_cli_output_hides_cryptic_markers(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(CLI), str(FIXTURES / "bad_async.py"), "--no-color", "--no-cache"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("[DEFECT 2 · WARNING 1]", proc.stdout)
        self.assertNotIn("FWF..........", proc.stdout)
        self.assertNotIn("Minimal Repro Witness", proc.stdout)


if __name__ == "__main__":
    unittest.main()
