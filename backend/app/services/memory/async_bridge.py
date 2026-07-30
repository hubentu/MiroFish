import asyncio
from collections.abc import Coroutine
from typing import TypeVar

T = TypeVar("T")


def run_sync(coro: Coroutine[object, object, T]) -> T:
    """Run an async Graphiti call from MiroFish's sync Flask workers.

    ponytail: uses asyncio.run per call — fine for request/worker threads;
    if a loop is already running in-thread, fall back to a dedicated thread
    with a new loop (ceiling: high concurrency → upgrade to shared loop).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
