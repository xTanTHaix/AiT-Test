"""Counterexample witness synthesis.

Each rule ships one minimal, self-contained snippet that demonstrates *why* the
pattern is dangerous. Snippets are syntax-validated before they leave this module
and are never executed by the engine; ``safe_to_execute`` tells the reader (and
the test harness) whether running one is harmless or intentionally hazardous.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, Mapping, Optional

from ..models import Witness

_IDENT_RE = re.compile(r"[^0-9A-Za-z_]")


@dataclass(frozen=True, slots=True)
class WitnessRequest:
    """Everything a template may use to specialise its snippet."""

    rule_id: str
    path: str
    lineno: int
    symbol: str = ""
    function: str = ""
    detail: Mapping[str, str] = field(default_factory=dict)


Builder = Callable[[WitnessRequest], Witness]


def _ident(text: str, fallback: str) -> str:
    """Reduce arbitrary source text to a safe Python identifier."""
    cleaned = _IDENT_RE.sub("_", text.strip()).strip("_")
    if not cleaned or cleaned[0].isdigit():
        return fallback
    return cleaned


def _apply(
    template: str, request: WitnessRequest, extra: Optional[Mapping[str, str]] = None
) -> str:
    """Substitute placeholders without invoking ``str.format`` on code bodies."""
    text = (
        template.replace("__WHERE__", f"{request.path}:{request.lineno}")
        .replace("__SYMBOL__", request.symbol or "<unknown>")
        .replace("__FUNCTION__", request.function or "<unknown>")
    )
    for key, value in (extra or {}).items():
        text = text.replace(f"__{key.upper()}__", value)
    return text


def _z01(request: WitnessRequest) -> Witness:
    """Pipe-buffer deadlock demonstration (intentionally hangs)."""
    code = _apply(
        """import subprocess
import sys

# Cause traced to __WHERE__
proc = subprocess.Popen(
    [sys.executable, "-c", "import sys; sys.stdout.write('A' * 70000)"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
proc.wait()  # Parent waits for exit; the child waits for the pipe to drain.
""",
        request,
    )
    return Witness(
        summary="Spawns a child that writes 70 KB into a pipe, then waits without draining it.",
        code=code,
        runnable=True,
        safe_to_execute=False,
    )


def _z02(request: WitnessRequest) -> Witness:
    """Zombie child demonstration."""
    code = _apply(
        """import subprocess
import sys

# Cause traced to __WHERE__
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.2)"])
print("parent finished without reaping child", child.pid)
""",
        request,
    )
    return Witness(
        summary="Starts a child process and exits without wait/poll/communicate/join.",
        code=code,
        runnable=True,
        safe_to_execute=True,
    )


def _z03(request: WitnessRequest) -> Witness:
    """Module-scope spawn demonstration (recursive on Windows spawn mode)."""
    code = _apply(
        """import multiprocessing

# Cause traced to __WHERE__
def work() -> None:
    return None

# Running this at module scope re-imports the module inside every child on
# Windows spawn mode, which spawns again: an unbounded process tree.
process = multiprocessing.get_context("spawn").Process(target=work)
process.start()
process.join()
""",
        request,
    )
    return Witness(
        summary="Spawns a process at import time instead of under an if __name__ guard.",
        code=code,
        runnable=True,
        safe_to_execute=False,
    )


def _r01(request: WitnessRequest) -> Witness:
    """Lost-update demonstration for an unlocked read-modify-write."""
    class_name = _ident(request.detail.get("class", "Counter"), "Counter")
    lock_name = _ident(request.detail.get("lock", "lock"), "lock").lstrip("_") or "lock"
    attr_name = _ident(request.detail.get("attr", "total"), "total").lstrip("_") or "total"
    code = _apply(
        """import threading

# Cause traced to __WHERE__
class __CLASS__:
    def __init__(self) -> None:
        self.__LOCK__ = threading.Lock()
        self.__ATTR__ = 0

    def add(self, delta: int) -> None:
        self.__ATTR__ += delta  # read-modify-write outside the lock

counter = __CLASS__()
workers = [threading.Thread(target=counter.add, args=(1,)) for _ in range(128)]
for item in workers:
    item.start()
for item in workers:
    item.join()
print("expected 128, observed", counter.__ATTR__)
""",
        request,
        {"class": class_name, "lock": lock_name, "attr": attr_name},
    )
    return Witness(
        summary="Two threads perform the same unlocked += and one update is lost.",
        code=code,
        runnable=True,
        safe_to_execute=True,
    )


def _r02(request: WitnessRequest) -> Witness:
    """Double-checked locking demonstration."""
    code = _apply(
        """import threading

# Cause traced to __WHERE__
class Registry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready = None

    def get(self):
        if self._ready is None:          # first check happens outside the lock
            with self._lock:
                if self._ready is None:  # second check repeats the same test
                    self._ready = "built"
        return self._ready
""",
        request,
    )
    return Witness(
        summary="State is tested before the lock and retested inside it without atomics.",
        code=code,
        runnable=True,
        safe_to_execute=True,
    )


def _a01(request: WitnessRequest) -> Witness:
    """Event-loop stall demonstration."""
    call = request.detail.get("call", "")
    if not call:
        call = "time.sleep(0.2)"
    elif not call.endswith((")", "...")):
        call = f"{call}(...)"
    code = _apply(
        """import asyncio
import time

# Cause traced to __WHERE__
async def handler() -> None:
    __CALL__  # synchronous call: every task on this loop is frozen meanwhile
    print("handler finished")

async def main() -> None:
    await asyncio.gather(handler(), handler())

asyncio.run(main())
""",
        request,
        {"call": call},
    )
    return Witness(
        summary="A blocking call inside a coroutine freezes every task on the event loop.",
        code=code,
        runnable=True,
        safe_to_execute=True,
    )


def _a02(request: WitnessRequest) -> Witness:
    """Task garbage-collection demonstration."""
    code = _apply(
        """import asyncio

# Cause traced to __WHERE__
async def worker() -> None:
    await asyncio.sleep(0.01)

async def main() -> None:
    asyncio.create_task(worker())  # no strong reference survives this statement
    await asyncio.sleep(0.05)

asyncio.run(main())
""",
        request,
    )
    return Witness(
        summary="A task with no strong reference can be collected while still pending.",
        code=code,
        runnable=True,
        safe_to_execute=True,
    )


def _a03(request: WitnessRequest) -> Witness:
    """Missing-await demonstration."""
    code = _apply(
        """import asyncio

# Cause traced to __WHERE__ (__SYMBOL__)
async def fetch() -> str:
    await asyncio.sleep(0.01)
    return "payload"

def main() -> None:
    pending = fetch()          # coroutine object created, never awaited
    print(type(pending).__name__, "was never executed")
    pending.close()

main()
""",
        request,
    )
    return Witness(
        summary="Calling a coroutine without await returns an unexecuted coroutine object.",
        code=code,
        runnable=True,
        safe_to_execute=True,
    )


def _m01(request: WitnessRequest) -> Witness:
    """In-place parameter mutation demonstration."""
    function_name = _ident(request.symbol.split(".")[-1], "mutate")
    code = _apply(
        """# Cause traced to __WHERE__ (__FUNCTION__)
def __FUNC__(items: list) -> None:
    items.append("injected")

payload: list = []
__FUNC__(payload)
print("caller list mutated:", payload)
""",
        request,
        {"func": function_name},
    )
    return Witness(
        summary="The callee mutates the caller's list through the shared reference.",
        code=code,
        runnable=True,
        safe_to_execute=True,
    )


def _m02(request: WitnessRequest) -> Witness:
    """Mutable default argument demonstration."""
    code = _apply(
        """# Cause traced to __WHERE__ (__FUNCTION__)
def append_tag(tag: str, bucket: list = []) -> list:
    bucket.append(tag)
    return bucket

print(append_tag("a"))   # ['a']
print(append_tag("b"))   # ['a', 'b'] - the default list survives between calls
""",
        request,
    )
    return Witness(
        summary="The default list is created once and shared by every call.",
        code=code,
        runnable=True,
        safe_to_execute=True,
    )


def _n01(request: WitnessRequest) -> Witness:
    """Unshielded None dereference demonstration."""
    code = _apply(
        """# Cause traced to __WHERE__ (__SYMBOL__)
class Worker:
    def send(self, payload: str) -> None:
        print("sent", payload)

def get_worker(key: str) -> "Worker | None":
    return None

worker = get_worker("alpha")
worker.send("payload")  # AttributeError: 'NoneType' object has no attribute 'send'
""",
        request,
    )
    return Witness(
        summary="A producer that can return None is dereferenced without a guard.",
        code=code,
        runnable=True,
        safe_to_execute=True,
    )


def _h01(request: WitnessRequest) -> Witness:
    """Bare-except suppression demonstration."""
    code = _apply(
        """# Cause traced to __WHERE__
def parse(value: str) -> int:
    try:
        return int(value)
    except:                  # swallows every failure, including KeyboardInterrupt
        return 0

print(parse("not-a-number"))  # 0 - the real cause is gone
""",
        request,
    )
    return Witness(
        summary="A bare except converts every failure into a silent default value.",
        code=code,
        runnable=True,
        safe_to_execute=True,
    )


def _h02(request: WitnessRequest) -> Witness:
    """Deep-nesting demonstration."""
    code = _apply(
        """# Cause traced to __WHERE__ (__FUNCTION__)
def classify(rows, flag):
    for row in rows:
        for cell in row:
            if flag:
                if cell:
                    if isinstance(cell, int):
                        if cell % 2 == 0:
                            return "even"
    return "other"

print(classify([[0, 1, 2]], True))
""",
        request,
    )
    return Witness(
        summary="Six nested control levels make every branch untestable in isolation.",
        code=code,
        runnable=True,
        safe_to_execute=True,
    )


_BUILDERS: Dict[str, Builder] = {
    "AIT-Z01": _z01,
    "AIT-Z02": _z02,
    "AIT-Z03": _z03,
    "AIT-R01": _r01,
    "AIT-R02": _r02,
    "AIT-A01": _a01,
    "AIT-A02": _a02,
    "AIT-A03": _a03,
    "AIT-M01": _m01,
    "AIT-M02": _m02,
    "AIT-N01": _n01,
    "AIT-H01": _h01,
    "AIT-H02": _h02,
}


def build_witness(request: WitnessRequest) -> Optional[Witness]:
    """Return a syntax-validated witness, or ``None`` when unsupported.

    A template that produces unparsable code is treated as a bug in the template
    and suppressed rather than shown: a broken counterexample is worse than none.
    """
    builder = _BUILDERS.get(request.rule_id)
    if builder is None:
        return None
    try:
        witness = builder(request)
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    try:
        ast.parse(witness.code)
    except SyntaxError:
        return None
    return witness


def supported_rules() -> tuple[str, ...]:
    """Return the rule ids that ship a witness template."""
    return tuple(sorted(_BUILDERS))
