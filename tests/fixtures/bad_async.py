"""Intentional defects for AIT-A01 / AIT-A02 / AIT-A03 (never imported by tests)."""

import asyncio
import time


async def blocking_handler() -> str:
    """AIT-A01: time.sleep freezes the whole event loop."""
    time.sleep(0.2)
    return "done"


async def worker() -> None:
    """Leaf coroutine used by the orphan-task demo."""
    await asyncio.sleep(0.01)


async def orphan_task_demo() -> None:
    """AIT-A02: the task handle is discarded immediately."""
    asyncio.create_task(worker())
    await asyncio.sleep(0.05)


async def fetch() -> str:
    """Coroutine that must be awaited."""
    await asyncio.sleep(0.01)
    return "payload"


def missing_await_demo():
    """AIT-A03: calling a coroutine without await returns an unused object."""
    pending = fetch()
    return pending
