# Graphiti Default Memory Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Graphiti + Neo4j the default MiroFish knowledge-graph/memory backend while keeping Zep Cloud behind `MEMORY_BACKEND=zep`, without breaking existing HTTP/`graph_id` contracts.

**Architecture:** Introduce a `KnowledgeGraphBackend` protocol. `GraphitiBackend` (default) talks to Neo4j via `graphiti-core` using MiroFish `graph_id` as Graphiti `group_id`. `ZepCloudBackend` wraps today’s `zep-cloud` SDK. Existing services (`graph_builder`, `zep_entity_reader`, `zep_tools`, `zep_graph_memory_updater`) call the protocol through a factory.

**Tech Stack:** Python 3.11–3.12, Flask, `graphiti-core`, Neo4j 5.26+ (Docker), existing `zep-cloud==3.25.0` (legacy path), pytest.

## Global Constraints

- Default `MEMORY_BACKEND=graphiti`; legacy `MEMORY_BACKEND=zep`.
- Neo4j via Docker Compose; Graphiti runs in-process in the MiroFish backend.
- Preserve MiroFish API contracts (`graph_id`, Report Agent tool shapes, simulation memory hooks).
- No migration of existing Zep Cloud graphs into Neo4j.
- Do not rename all `zep_*` modules in this change (facades stay named; they become backend-agnostic).
- No in-app backend switcher; operators choose via env.
- CI must not require live Neo4j or Zep Cloud keys (mock/`FakeKnowledgeGraphBackend` for unit tests).
- Pin `group_id` as Graphiti namespace **within one Neo4j database** (Neo4j Community). Do not create one Neo4j database per MiroFish graph.

---

## File structure

| File | Responsibility |
|------|----------------|
| `backend/app/services/memory/__init__.py` | Public exports: protocol, DTOs, factory |
| `backend/app/services/memory/types.py` | Shared DTOs (`GraphNode`, `GraphEdge`, `SearchHits`, `EpisodeItem`, `BatchProgress`) |
| `backend/app/services/memory/protocol.py` | `KnowledgeGraphBackend` Protocol |
| `backend/app/services/memory/async_bridge.py` | Sync wrapper for Graphiti async calls |
| `backend/app/services/memory/ontology_mapper.py` | MiroFish ontology dict → Graphiti Pydantic entity types |
| `backend/app/services/memory/graphiti_backend.py` | Graphiti + Neo4j implementation |
| `backend/app/services/memory/zep_cloud_backend.py` | Zep Cloud implementation (extracted from current SDK calls) |
| `backend/app/services/memory/factory.py` | `get_memory_backend()` + cache clear for tests |
| `backend/app/services/memory/fake_backend.py` | In-memory fake for unit tests |
| `backend/app/config.py` | `MEMORY_BACKEND`, Neo4j, optional embedding overrides; conditional validate |
| `backend/requirements.txt` / `backend/pyproject.toml` | Add `graphiti-core`, keep `zep-cloud` |
| `docker-compose.yml` / `docker-compose.neo4j.yml` | Neo4j service |
| `.env.example`, `README.md`, `README-ZH.md` | Document Graphiti default + Zep flag |
| Existing service facades | Inject backend instead of calling `get_zep_client()` directly |

---

### Task 1: Config, dependencies, Neo4j Compose

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/requirements.txt`
- Modify: `backend/pyproject.toml`
- Modify: `.env.example`
- Create: `docker-compose.neo4j.yml` (Neo4j-only Compose for local source installs; keep existing `docker-compose.yml` and extend it to include Neo4j)
- Modify: `docker-compose.yml`
- Test: `backend/tests/test_memory_config.py`

**Interfaces:**
- Consumes: none
- Produces: `Config.MEMORY_BACKEND`, `Config.NEO4J_URI`, `Config.NEO4J_USER`, `Config.NEO4J_PASSWORD`, optional `Config.GRAPHITI_EMBEDDING_*`; `Config.validate()` returns errors only for the active backend

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_memory_config.py
import os
import importlib

import pytest


def _reload_config(monkeypatch, **env):
    for key in (
        "MEMORY_BACKEND",
        "ZEP_API_KEY",
        "ZEP_API_URL",
        "NEO4J_URI",
        "NEO4J_USER",
        "NEO4J_PASSWORD",
        "LLM_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import app.config as config_mod
    importlib.reload(config_mod)
    return config_mod.Config


def test_graphiti_default_requires_neo4j_not_zep(monkeypatch):
    Config = _reload_config(
        monkeypatch,
        MEMORY_BACKEND="graphiti",
        LLM_API_KEY="llm",
        NEO4J_URI="bolt://localhost:7687",
        NEO4J_USER="neo4j",
        NEO4J_PASSWORD="secret",
    )
    errors = Config.validate()
    assert "ZEP_API_KEY 未配置" not in errors
    assert not any("NEO4J" in e for e in errors)


def test_zep_backend_requires_zep_key(monkeypatch):
    Config = _reload_config(
        monkeypatch,
        MEMORY_BACKEND="zep",
        LLM_API_KEY="llm",
    )
    errors = Config.validate()
    assert "ZEP_API_KEY 未配置" in errors


def test_graphiti_missing_neo4j_password(monkeypatch):
    Config = _reload_config(
        monkeypatch,
        MEMORY_BACKEND="graphiti",
        LLM_API_KEY="llm",
        NEO4J_URI="bolt://localhost:7687",
        NEO4J_USER="neo4j",
    )
    errors = Config.validate()
    assert any("NEO4J_PASSWORD" in e for e in errors)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_memory_config.py -v`  
Expected: FAIL (MEMORY_BACKEND / Neo4j validation not implemented)

- [ ] **Step 3: Implement config + deps + Compose**

In `config.py`, add:

```python
MEMORY_BACKEND = os.environ.get("MEMORY_BACKEND", "graphiti").strip().lower()
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD")
GRAPHITI_EMBEDDING_MODEL = os.environ.get("GRAPHITI_EMBEDDING_MODEL")
GRAPHITI_EMBEDDING_DIM = os.environ.get("GRAPHITI_EMBEDDING_DIM")
GRAPHITI_EMBEDDING_BASE_URL = os.environ.get("GRAPHITI_EMBEDDING_BASE_URL")
GRAPHITI_EMBEDDING_API_KEY = os.environ.get("GRAPHITI_EMBEDDING_API_KEY")
```

Update `validate()`:

```python
if not cls.LLM_API_KEY:
    errors.append("LLM_API_KEY 未配置")
backend = (cls.MEMORY_BACKEND or "graphiti").lower()
if backend not in {"graphiti", "zep"}:
    errors.append("MEMORY_BACKEND 必须是 graphiti 或 zep")
elif backend == "zep":
    if not cls.ZEP_API_KEY:
        errors.append("ZEP_API_KEY 未配置")
    if os.environ.get("ZEP_API_URL"):
        errors.append("ZEP_API_URL 不受支持；MiroFish 仅连接 Zep Cloud")
elif backend == "graphiti":
    if not cls.NEO4J_URI:
        errors.append("NEO4J_URI 未配置")
    if not cls.NEO4J_USER:
        errors.append("NEO4J_USER 未配置")
    if not cls.NEO4J_PASSWORD:
        errors.append("NEO4J_PASSWORD 未配置")
```

Add to `requirements.txt` and `pyproject.toml` dependencies:

```
graphiti-core>=0.11.0
```

Keep `zep-cloud==3.25.0`.

Add Neo4j service to `docker-compose.yml` and create `docker-compose.neo4j.yml`:

```yaml
services:
  neo4j:
    image: neo4j:5.26-community
    ports:
      - "7474:7474"
      - "7687:7687"
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD:-mirofish_neo4j}
      NEO4J_PLUGINS: '["apoc"]'
    volumes:
      - neo4j_data:/data
    healthcheck:
      test: ["CMD", "cypher-shell", "-u", "neo4j", "-p", "${NEO4J_PASSWORD:-mirofish_neo4j}", "RETURN 1"]
      interval: 10s
      timeout: 5s
      retries: 10

volumes:
  neo4j_data:
```

Update `.env.example` per the design spec (Graphiti default + optional Zep).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_memory_config.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/requirements.txt backend/pyproject.toml \
  backend/tests/test_memory_config.py .env.example docker-compose.yml docker-compose.neo4j.yml
git commit -m "feat(memory): add MEMORY_BACKEND config and Neo4j Compose"
```

---

### Task 2: Protocol, DTOs, Fake backend

**Files:**
- Create: `backend/app/services/memory/types.py`
- Create: `backend/app/services/memory/protocol.py`
- Create: `backend/app/services/memory/fake_backend.py`
- Create: `backend/app/services/memory/__init__.py`
- Test: `backend/tests/test_memory_protocol_fake.py`

**Interfaces:**
- Consumes: none
- Produces:

```python
@dataclass
class GraphNode:
    uuid: str
    name: str
    labels: list[str]
    summary: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    group_id: str = ""

@dataclass
class GraphEdge:
    uuid: str
    name: str
    fact: str
    source_node_uuid: str
    target_node_uuid: str
    created_at: str | None = None
    valid_at: str | None = None
    invalid_at: str | None = None
    expired_at: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    group_id: str = ""

@dataclass
class SearchHits:
    facts: list[str]
    edges: list[dict[str, Any]]
    nodes: list[dict[str, Any]]
    query: str
    total_count: int

@dataclass
class EpisodeItem:
    content: str
    name: str | None = None
    reference_time: datetime | None = None
    source_description: str = "mirofish"

@dataclass
class IngestResult:
    episode_uuids: list[str]
    item_count: int

class KnowledgeGraphBackend(Protocol):
    def create_graph(self, graph_id: str, name: str) -> str: ...
    def delete_graph(self, graph_id: str) -> None: ...
    def graph_exists(self, graph_id: str) -> bool: ...
    def set_ontology(self, graph_id: str, ontology: dict[str, Any]) -> None: ...
    def add_episodes(
        self,
        graph_id: str,
        items: list[EpisodeItem],
        *,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> IngestResult: ...
    def list_nodes(self, graph_id: str) -> list[GraphNode]: ...
    def list_edges(self, graph_id: str) -> list[GraphEdge]: ...
    def get_node(self, node_uuid: str) -> GraphNode | None: ...
    def get_node_edges(self, graph_id: str, node_uuid: str) -> list[GraphEdge]: ...
    def search(
        self,
        graph_id: str,
        query: str,
        *,
        limit: int = 10,
        scope: str = "edges",
    ) -> SearchHits: ...
```

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_memory_protocol_fake.py
from datetime import datetime, timezone

from app.services.memory.fake_backend import FakeKnowledgeGraphBackend
from app.services.memory.types import EpisodeItem


def test_fake_create_ingest_search_roundtrip():
    backend = FakeKnowledgeGraphBackend()
    gid = backend.create_graph("mirofish_test1", "Test")
    assert backend.graph_exists(gid)

    backend.set_ontology(
        gid,
        {
            "entity_types": [{"name": "Person", "description": "A person", "attributes": []}],
            "edge_types": [],
        },
    )
    result = backend.add_episodes(
        gid,
        [
            EpisodeItem(
                content="Alice works at MiroFish.",
                name="chunk-0",
                reference_time=datetime.now(timezone.utc),
            )
        ],
    )
    assert result.item_count == 1
    assert backend.list_nodes(gid)  # fake seeds a node from content
    hits = backend.search(gid, "Alice", limit=5)
    assert hits.total_count >= 1
    assert any("Alice" in f for f in hits.facts)

    backend.delete_graph(gid)
    assert not backend.graph_exists(gid)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_memory_protocol_fake.py -v`  
Expected: FAIL (module not found)

- [ ] **Step 3: Implement types, protocol, fake**

Implement `FakeKnowledgeGraphBackend` as a dict-of-graphs store:
- `add_episodes` creates one synthetic `GraphNode` named from the first capitalized token (or `"Entity"`) and one edge fact = episode content (good enough for contract tests).
- `search` does case-insensitive substring match over facts/node names.
- `set_ontology` stores ontology on the graph record (no schema enforcement in fake).
- `get_node` / `get_node_edges` / `list_*` read from that store.

Export from `__init__.py`: `KnowledgeGraphBackend`, DTOs, `FakeKnowledgeGraphBackend`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_memory_protocol_fake.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/memory backend/tests/test_memory_protocol_fake.py
git commit -m "feat(memory): add KnowledgeGraphBackend protocol and fake"
```

---

### Task 3: Async bridge + factory skeleton

**Files:**
- Create: `backend/app/services/memory/async_bridge.py`
- Create: `backend/app/services/memory/factory.py`
- Test: `backend/tests/test_memory_factory.py`

**Interfaces:**
- Consumes: `Config.MEMORY_BACKEND`, protocol
- Produces: `run_sync(coro)` ; `get_memory_backend() -> KnowledgeGraphBackend` ; `clear_memory_backend_cache()`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_memory_factory.py
import asyncio
from app.services.memory.async_bridge import run_sync
from app.services.memory.factory import get_memory_backend, clear_memory_backend_cache
from app.services.memory.fake_backend import FakeKnowledgeGraphBackend


def test_run_sync_returns_coroutine_result():
    async def _add(a, b):
        await asyncio.sleep(0)
        return a + b

    assert run_sync(_add(2, 3)) == 5


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_memory_factory.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement bridge + factory**

```python
# async_bridge.py
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
```

```python
# factory.py
from functools import lru_cache
from typing import Optional
from ...config import Config
from .protocol import KnowledgeGraphBackend

@lru_cache(maxsize=1)
def _cached_backend(backend_name: str) -> KnowledgeGraphBackend:
    if backend_name == "zep":
        from .zep_cloud_backend import ZepCloudBackend
        return ZepCloudBackend()
    from .graphiti_backend import GraphitiBackend
    return GraphitiBackend()

def get_memory_backend(*, override: Optional[KnowledgeGraphBackend] = None) -> KnowledgeGraphBackend:
    if override is not None:
        return override
    name = (Config.MEMORY_BACKEND or "graphiti").strip().lower()
    return _cached_backend(name)

def clear_memory_backend_cache() -> None:
    _cached_backend.cache_clear()
```

For Task 3 only: stub `GraphitiBackend` / `ZepCloudBackend` as empty classes raising `NotImplementedError` on use, OR make factory tests use `override=` only and defer importing real backends until Task 4/5. Prefer: factory imports lazily inside `_cached_backend` so Task 3 tests only use `override=`.

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_memory_factory.py tests/test_memory_protocol_fake.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/memory/async_bridge.py backend/app/services/memory/factory.py \
  backend/tests/test_memory_factory.py
git commit -m "feat(memory): add async bridge and backend factory"
```

---

### Task 4: GraphitiBackend — lifecycle, ontology, ingest, search

**Files:**
- Create: `backend/app/services/memory/ontology_mapper.py`
- Create: `backend/app/services/memory/graphiti_backend.py`
- Test: `backend/tests/test_graphiti_backend.py`

**Interfaces:**
- Consumes: `run_sync`, `Config` Neo4j + LLM settings, protocol DTOs
- Produces: working `GraphitiBackend` implementing the full protocol

- [ ] **Step 1: Write failing tests with mocked Graphiti client**

```python
# backend/tests/test_graphiti_backend.py
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.memory.graphiti_backend import GraphitiBackend
from app.services.memory.types import EpisodeItem


def test_create_graph_registers_group_and_builds_indices():
    backend = GraphitiBackend(client=MagicMock())
    backend._ensure_indices = MagicMock()
    gid = backend.create_graph("mirofish_abc", "Demo")
    assert gid == "mirofish_abc"
    assert backend.graph_exists(gid)


def test_add_episodes_calls_add_episode_with_group_id():
    client = MagicMock()
    client.add_episode = AsyncMock(return_value=MagicMock(episode=MagicMock(uuid="ep-1")))
    backend = GraphitiBackend(client=client)
    backend.create_graph("mirofish_abc", "Demo")

    result = backend.add_episodes(
        "mirofish_abc",
        [EpisodeItem(content="Bob likes tea.", name="c0", reference_time=datetime.now(timezone.utc))],
    )
    assert result.item_count == 1
    assert client.add_episode.await_count == 1
    kwargs = client.add_episode.await_args.kwargs
    assert kwargs["group_id"] == "mirofish_abc"
    assert "Bob likes tea." in kwargs["episode_body"]


def test_search_maps_edges_to_search_hits():
    edge = MagicMock()
    edge.uuid = "e1"
    edge.name = "LIKES"
    edge.fact = "Bob likes tea"
    edge.source_node_uuid = "n1"
    edge.target_node_uuid = "n2"
    client = MagicMock()
    client.search = AsyncMock(return_value=[edge])
    backend = GraphitiBackend(client=client)
    backend.create_graph("mirofish_abc", "Demo")
    hits = backend.search("mirofish_abc", "tea", limit=5)
    assert hits.facts == ["Bob likes tea"]
    assert hits.edges[0]["uuid"] == "e1"
```

Adjust mocks to match the installed `graphiti-core` return types (some versions return `SearchResults` with `.edges` / `.nodes`; normalize both in implementation).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_graphiti_backend.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement ontology mapper + GraphitiBackend**

`ontology_mapper.py`: convert MiroFish ontology entity_types into `dict[str, type[BaseModel]]` using dynamic Pydantic models (same attribute sanitization as `graph_builder.set_ontology` via `normalize_ontology_attributes` / reserved names). Store mapped types on the backend keyed by `graph_id` and pass `entity_types=` into `add_episode`.

`graphiti_backend.py` responsibilities:
1. Construct `Graphiti(uri, user, password, llm_client=..., embedder=...)` using OpenAI-compatible clients from `LLM_*` (and embedding overrides when set). Allow `client=` injection for tests.
2. Maintain process-local set/dict of known `graph_id`s (create_graph registers; delete_graph clears namespace data via Graphiti/Neo4j group clear helper).
3. `graph_exists`: true if registered **or** any node/edge found for that `group_id` (Cypher / Graphiti retrieve).
4. `add_episodes`: for each item, `run_sync(client.add_episode(..., group_id=graph_id, source=EpisodeType.text, entity_types=...))`; invoke `progress_callback(done, total)`.
5. `list_nodes` / `list_edges`: query Neo4j filtered by `group_id` (driver session) and map to `GraphNode`/`GraphEdge`. Prefer Graphiti helpers if available; otherwise small Cypher:
   - nodes: `MATCH (n:Entity {group_id: $gid}) RETURN n`
   - edges: `MATCH (a)-[r:RELATES_TO]->(b) WHERE r.group_id = $gid RETURN r,a,b` (confirm actual relationship/label names against installed graphiti-core schema; adjust Cypher to match).
6. `search`: `run_sync(client.search(query=..., group_ids=[graph_id], num_results=limit))` (or `group_id=` per installed signature); map to `SearchHits`.
7. `delete_graph`: clear all nodes/edges for `group_id`, unregister.

Document in module docstring: MiroFish `graph_id` == Graphiti `group_id` namespace in the shared Neo4j DB.

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_graphiti_backend.py tests/test_memory_protocol_fake.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/memory/ontology_mapper.py \
  backend/app/services/memory/graphiti_backend.py \
  backend/tests/test_graphiti_backend.py
git commit -m "feat(memory): implement GraphitiBackend against Neo4j"
```

---

### Task 5: ZepCloudBackend — wrap existing Zep SDK usage

**Files:**
- Create: `backend/app/services/memory/zep_cloud_backend.py`
- Test: `backend/tests/test_zep_cloud_backend.py`

**Interfaces:**
- Consumes: `get_zep_client`, `call_zep_read_with_retry`, `fetch_all_nodes` / `fetch_all_edges`, protocol
- Produces: `ZepCloudBackend` implementing the same protocol

- [ ] **Step 1: Write failing test with mocked Zep client**

```python
# backend/tests/test_zep_cloud_backend.py
from unittest.mock import MagicMock, patch

from app.services.memory.zep_cloud_backend import ZepCloudBackend


def test_create_graph_calls_zep_graph_create():
    client = MagicMock()
    with patch("app.services.memory.zep_cloud_backend.get_zep_client", return_value=client):
        backend = ZepCloudBackend(api_key="test-key")
        backend.create_graph("mirofish_z1", "Zep Graph")
    client.graph.create.assert_called_once()
    kwargs = client.graph.create.call_args.kwargs
    assert kwargs["graph_id"] == "mirofish_z1"


def test_search_normalizes_edge_facts():
    client = MagicMock()
    edge = MagicMock(fact="A related to B", uuid_="e1", name="REL", source_node_uuid="a", target_node_uuid="b")
    client.graph.search.return_value = MagicMock(edges=[edge], nodes=[])
    with patch("app.services.memory.zep_cloud_backend.get_zep_client", return_value=client):
        backend = ZepCloudBackend(api_key="test-key")
        hits = backend.search("mirofish_z1", "related", limit=5)
    assert "A related to B" in hits.facts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_zep_cloud_backend.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement ZepCloudBackend**

Move the concrete Zep calls from:
- `GraphBuilderService.create_graph` / `set_ontology` / batch ingest / `delete_graph`
- paging list nodes/edges
- `graph.search`

into protocol methods. For Zep ingest, keep using the existing Batch API helpers (can extract methods from `graph_builder` into the backend, or have `add_episodes` call a slimmed batch path). Prefer extracting batch ingest into `ZepCloudBackend.add_episodes` so `graph_builder` becomes backend-agnostic.

Preserve Cloud-only URL rejection via existing `get_zep_client`.

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_zep_cloud_backend.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/memory/zep_cloud_backend.py backend/tests/test_zep_cloud_backend.py
git commit -m "feat(memory): implement ZepCloudBackend behind protocol"
```

---

### Task 6: Wire `GraphBuilderService` to the protocol

**Files:**
- Modify: `backend/app/services/graph_builder.py`
- Test: `backend/tests/test_graph_builder_backend_wiring.py`

**Interfaces:**
- Consumes: `get_memory_backend()`, `EpisodeItem`, `IngestResult`
- Produces: `GraphBuilderService` that builds graphs without importing `zep_cloud` directly (except via backend)

- [ ] **Step 1: Write failing wiring test**

```python
# backend/tests/test_graph_builder_backend_wiring.py
from app.services.graph_builder import GraphBuilderService
from app.services.memory.fake_backend import FakeKnowledgeGraphBackend


def test_create_graph_uses_injected_backend():
    fake = FakeKnowledgeGraphBackend()
    svc = GraphBuilderService(backend=fake)
    gid = svc.create_graph("wired", graph_id="mirofish_wire1")
    assert gid == "mirofish_wire1"
    assert fake.graph_exists(gid)


def test_set_ontology_delegates():
    fake = FakeKnowledgeGraphBackend()
    svc = GraphBuilderService(backend=fake)
    svc.create_graph("wired", graph_id="mirofish_wire1")
    ontology = {"entity_types": [{"name": "Org", "description": "org", "attributes": []}], "edge_types": []}
    svc.set_ontology("mirofish_wire1", ontology)
    assert fake._graphs["mirofish_wire1"]["ontology"]["entity_types"][0]["name"] == "Org"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_graph_builder_backend_wiring.py -v`  
Expected: FAIL (`backend=` not accepted / still requires ZEP_API_KEY)

- [ ] **Step 3: Refactor GraphBuilderService**

```python
def __init__(self, api_key: str | None = None, backend: KnowledgeGraphBackend | None = None):
    self.backend = backend or get_memory_backend()
    # keep api_key only for legacy callers; do not require ZEP key when graphiti
    self.task_manager = TaskManager()
```

Change:
- `create_graph` → `self.backend.create_graph`
- `set_ontology` → `self.backend.set_ontology`
- `add_text_batches` → convert chunks to `EpisodeItem`s and `self.backend.add_episodes` (Zep backend may still use Batch API internally; Graphiti uses sequential episodes). Keep `BatchSubmission` shape for progress/state: for Graphiti, `batch_id` can be a local UUID and `episode_uuids` from `IngestResult`.
- `delete_graph` / `get_graph_data` → protocol list/delete

Remove direct `from zep_cloud import ...` from this file once delegated.

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_graph_builder_backend_wiring.py tests/test_memory_config.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/graph_builder.py backend/tests/test_graph_builder_backend_wiring.py
git commit -m "refactor(graph): wire GraphBuilderService to memory backend"
```

---

### Task 7: Wire entity reader + tools + memory updater

**Files:**
- Modify: `backend/app/services/zep_entity_reader.py`
- Modify: `backend/app/services/zep_tools.py` (`search_graph`, `get_all_nodes`, `get_all_edges`, `get_node_detail`, `get_node_edges`)
- Modify: `backend/app/services/zep_graph_memory_updater.py`
- Modify: `backend/app/services/oasis_profile_generator.py` (uses `zep_client.graph.search`)
- Test: `backend/tests/test_services_backend_wiring.py`

**Interfaces:**
- Consumes: protocol search/list/get/add_episodes
- Produces: facades that work with `FakeKnowledgeGraphBackend` without Zep

- [ ] **Step 1: Write failing wiring tests**

```python
# backend/tests/test_services_backend_wiring.py
from app.services.memory.fake_backend import FakeKnowledgeGraphBackend
from app.services.memory.types import EpisodeItem
from app.services.zep_entity_reader import ZepEntityReader
from app.services.zep_tools import ZepToolsService
from app.services.zep_graph_memory_updater import ZepGraphMemoryUpdater


def test_entity_reader_lists_fake_nodes():
    fake = FakeKnowledgeGraphBackend()
    fake.create_graph("g1", "g")
    fake.add_episodes("g1", [EpisodeItem(content="Carol founded Acme.")])
    reader = ZepEntityReader(backend=fake)
    nodes = reader.get_all_nodes("g1")  # or existing public method name
    assert len(nodes) >= 1


def test_tools_search_uses_backend():
    fake = FakeKnowledgeGraphBackend()
    fake.create_graph("g1", "g")
    fake.add_episodes("g1", [EpisodeItem(content="Carol founded Acme.")])
    tools = ZepToolsService(backend=fake, llm_client=None)
    result = tools.search_graph("g1", "Carol", limit=5)
    assert result.total_count >= 1
```

Adapt method names to the real public APIs on those classes (`filter_entities`, etc.).

- [ ] **Step 2: Run tests expecting fail**

Run: `cd backend && python -m pytest tests/test_services_backend_wiring.py -v`  
Expected: FAIL

- [ ] **Step 3: Inject backend into each facade**

Pattern for each:

```python
def __init__(..., backend: KnowledgeGraphBackend | None = None):
    self.backend = backend or get_memory_backend()
```

Replace `self.client.graph.*` with `self.backend.*`.  
Keep LLM-heavy InsightForge / interview logic in `zep_tools.py`; only swap the retrieval primitives.

For `ZepGraphMemoryUpdater`, replace episode add + wait with `backend.add_episodes`. If Graphiti `add_episode` is synchronous w.r.t. extraction completion, skip Zep-style episode polling; if not, poll node/edge growth or Graphiti episode get if available.

For `oasis_profile_generator.py`, replace direct `zep_client.graph.search` with `self.backend.search(...).facts` (or inject tools/backend).

- [ ] **Step 4: Run wiring + prior memory tests**

Run: `cd backend && python -m pytest tests/test_services_backend_wiring.py tests/test_graph_builder_backend_wiring.py tests/test_memory_protocol_fake.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/zep_entity_reader.py backend/app/services/zep_tools.py \
  backend/app/services/zep_graph_memory_updater.py backend/app/services/oasis_profile_generator.py \
  backend/tests/test_services_backend_wiring.py
git commit -m "refactor(memory): wire entity reader, tools, updater to backend"
```

---

### Task 8: Keep legacy Zep tests green; update docs

**Files:**
- Modify: existing `backend/tests/test_zep_*.py` only if constructors now require backend or no longer read `ZEP_API_KEY` at import
- Modify: `README.md`, `README-ZH.md`
- Modify: frontend copy strings that hard-code “Zep” in user-visible setup hints (search `zep` / `Zep` in `frontend/src` locale files and Step components); replace default-path copy with “knowledge graph (Graphiti/Neo4j)” without adding a switcher
- Test: full unit suite

- [ ] **Step 1: Run full backend unit tests**

Run: `cd backend && python -m pytest -v`  
Expected: fix any constructor/patch path breakages from Tasks 6–7. Do **not** delete Zep contract tests; update patches to `app.services.memory.zep_cloud_backend.get_zep_client` where needed.

- [ ] **Step 2: Update README quick start**

Replace Zep-required env section with:

```env
MEMORY_BACKEND=graphiti
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password
# Optional legacy:
# MEMORY_BACKEND=zep
# ZEP_API_KEY=...
```

Add: start Neo4j with `docker compose -f docker-compose.neo4j.yml up -d` before `npm run dev`.

- [ ] **Step 3: Re-run tests**

Run: `cd backend && python -m pytest -v`  
Expected: PASS (skipping any tests explicitly marked for live Cloud)

- [ ] **Step 4: Commit**

```bash
git add README.md README-ZH.md frontend/src backend/tests
git commit -m "docs: Graphiti+Neo4j default memory backend setup"
```

---

### Task 9: Manual smoke checklist (no CI)

**Files:** none required (operator checklist)

- [ ] **Step 1: Start Neo4j**

```bash
docker compose -f docker-compose.neo4j.yml up -d
```

- [ ] **Step 2: Configure `.env`**

`MEMORY_BACKEND=graphiti`, Neo4j password matching Compose, existing `LLM_*`. No `ZEP_API_KEY` required.

- [ ] **Step 3: Run smoke**

1. `npm run dev`
2. Upload a tiny seed doc → generate ontology → build graph → confirm nodes in UI
3. Prepare simulation → run 1–2 rounds with memory updater enabled
4. Open Report Agent tool that searches the graph → confirm non-empty facts

- [ ] **Step 4: Optional Zep regression**

Set `MEMORY_BACKEND=zep` + `ZEP_API_KEY`, rebuild a **new** project (do not reuse Graphiti `graph_id`), confirm Cloud path still works.

- [ ] **Step 5: Commit nothing unless smoke found fixes; if fixes needed, commit those separately**

---

## Self-review (plan vs spec)

| Spec requirement | Task |
|------------------|------|
| Graphiti + Neo4j default | 1, 4, 8 |
| Zep behind `MEMORY_BACKEND` | 1, 5, factory |
| Stable `graph_id` / tool contracts | 2, 6, 7 |
| Docker Neo4j | 1 |
| Config validation per backend | 1 |
| Adapter protocol | 2–5 |
| Wire builder/reader/tools/updater | 6–7 |
| Docs / copy | 8 |
| No Zep CE / no graph migration | Global constraints + Task 9 note |
| Tests without live Neo4j/Cloud | 2–7 mocks/fake |
| Manual smoke | 9 |

No TBD placeholders. Protocol method names are consistent across tasks (`create_graph`, `add_episodes`, `search`, etc.).
