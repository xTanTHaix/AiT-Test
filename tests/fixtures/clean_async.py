"""Counterexamples that must produce zero findings (correct async usage)."""

import asyncio


async def handler() -> str:
    """Awaitable sleep keeps the loop responsive."""
    await asyncio.sleep(0.01)
    return "done"


async def fetch() -> str:
    """Coroutine that is always awaited by its caller."""
    await asyncio.sleep(0.01)
    return "payload"


async def main() -> None:
    """Keep a strong reference to the task and await it."""
    task = asyncio.create_task(handler())
    payload = await fetch()
    print(payload, await task)
