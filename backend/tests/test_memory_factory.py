import asyncio
from app.services.memory.async_bridge import run_sync
from app.services.memory.factory import get_memory_backend, clear_memory_backend_cache
from app.services.memory.fake_backend import FakeKnowledgeGraphBackend


def test_run_sync_returns_coroutine_result():
    async def _add(a, b):
        await asyncio.sleep(0)
        return a + b

    assert run_sync(_add(2, 3)) == 5


def test_run_sync_reuses_same_loop_across_calls():
    """Neo4j/Graphiti need one stable loop — not a fresh asyncio.run() each time."""
    loops: list[asyncio.AbstractEventLoop] = []

    async def capture():
        loops.append(asyncio.get_running_loop())
        await asyncio.sleep(0)
        return True

    assert run_sync(capture()) is True
    assert run_sync(capture()) is True
    assert len(loops) == 2
    assert loops[0] is loops[1]


def test_factory_can_inject_override(monkeypatch):
    clear_memory_backend_cache()
    fake = FakeKnowledgeGraphBackend()
    monkeypatch.setenv("MEMORY_BACKEND", "graphiti")
    # factory should accept explicit override for tests
    from app.services.memory import factory as factory_mod
    import importlib
    importlib.reload(factory_mod)
    backend = factory_mod.get_memory_backend(override=fake)
    assert backend is fake
