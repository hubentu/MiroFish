"""Sync bridge for Graphiti async calls.

Neo4j's async driver (and Graphiti) bind Futures to the event loop that first
used them. ``asyncio.run()`` per call creates a *new* loop each time →
"Future attached to a different loop". Keep one long-lived loop in a daemon
thread for the process lifetime instead.
"""

from __future__ import annotations

import asyncio
import atexit
import threading
from collections.abc import Coroutine
from typing import TypeVar

T = TypeVar("T")

_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop, _thread
    with _lock:
        if _loop is not None and _loop.is_running():
            return _loop

        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def _runner() -> None:
            asyncio.set_event_loop(loop)
            ready.set()
            loop.run_forever()

        thread = threading.Thread(
            target=_runner, name="mirofish-graphiti-loop", daemon=True
        )
        thread.start()
        if not ready.wait(timeout=10):
            raise RuntimeError("Graphiti asyncio loop failed to start")
        _loop = loop
        _thread = thread
        return loop


def run_sync(coro: Coroutine[object, object, T]) -> T:
    """Run an async Graphiti call from MiroFish's sync Flask workers."""
    loop = _ensure_loop()
    return asyncio.run_coroutine_threadsafe(coro, loop).result()


def shutdown_async_bridge() -> None:
    """Stop the shared loop (tests / process exit)."""
    global _loop, _thread
    with _lock:
        loop, thread = _loop, _thread
        _loop = None
        _thread = None
    if loop is None:
        return
    loop.call_soon_threadsafe(loop.stop)
    if thread is not None:
        thread.join(timeout=5)


atexit.register(shutdown_async_bridge)
