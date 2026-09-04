# Task 2 Report: Hydrate graph snapshot into Neo4j

## Status

Complete. Commit: `bc16e9f` (`feat(memory): hydrate graph snapshots into Graphiti/Neo4j`).

## Implementation

- Added `KnowledgeGraphBackend.hydrate_graph_snapshot(graph_id, snapshot)`.
- Added fake-backend hydration using the existing `GraphNode` and `GraphEdge` DTOs.
- Added deterministic Graphiti/Neo4j hydration with direct Cypher:
  - creates/registers the graph when absent;
  - writes all `Entity` nodes before `RELATES_TO` edges;
  - preserves exported node and edge UUIDs;
  - forces imported nodes and edges to use the destination `graph_id` as `group_id`;
  - restores labels, summaries, attributes, temporal fields, and episode references.
- Added the required Zep Cloud `NotImplementedError`.
- Added the fake round-trip test and an opt-in live Neo4j integration test.

## TDD Evidence

### RED

Command:

```text
cd backend && python -m pytest tests/test_graph_hydrate.py::test_fake_hydrate_round_trip -v
```

Result: `1 failed`.

Expected failure:

```text
AttributeError: 'FakeKnowledgeGraphBackend' object has no attribute 'hydrate_graph_snapshot'
```

### GREEN

Command:

```text
cd backend && python -m pytest tests/test_graph_hydrate.py -v
```

Result: `1 passed, 1 skipped`. The integration test skips unless
`MIROFISH_NEO4J_INTEGRATION=1`.

Regression command:

```text
cd backend && python -m pytest tests/test_graph_hydrate.py tests/test_graphiti_backend.py tests/test_memory_factory.py -v
```

Result: `11 passed, 1 skipped`.

`git diff --check` passed, and edited files had no IDE linter diagnostics.

## Concerns

- Live Neo4j was not exercised because `MIROFISH_NEO4J_INTEGRATION` was not enabled.
- Hydrated snapshots do not contain Graphiti embeddings; Graphiti's full-text indexes
  make imported names/facts searchable, while vector relevance becomes available only
  if embeddings are generated separately.
- Pre-existing uncommitted Graphiti client changes were deliberately excluded from this
  task's commit.

## Important Review Fixes (2026-09-04)

- Node hydration now merges on `(uuid, group_id)` and rejects UUIDs already owned by
  another graph. UUID remapping was deliberately avoided because references outside
  the imported snapshot could not be rewritten safely.
- Edge endpoints are validated against the complete snapshot before any graph or node
  write. Invalid imports raise `ValueError` listing every bad edge UUID, leaving no
  partially hydrated destination graph.
- Fake-backend coverage now explicitly verifies edge UUID preservation and atomic
  rejection of missing endpoints; Graphiti coverage verifies cross-graph collisions.

Test command:

```text
cd backend && python -m pytest tests/test_graph_hydrate.py tests/test_memory_protocol_fake.py tests/test_graphiti_backend.py -v
```

Result: `11 passed, 1 skipped` (live Neo4j integration remains opt-in).

`git diff --check` passed, and edited files had no IDE linter diagnostics.

# Task 2 Report: Protocol, DTOs, Fake backend

## Status

**Complete.** All brief steps executed (TDD, implement, test pass, commit).

## Commits

```
feat(memory): add KnowledgeGraphBackend protocol and fake
```

Files committed:
- `backend/app/services/memory/types.py`
- `backend/app/services/memory/protocol.py`
- `backend/app/services/memory/fake_backend.py`
- `backend/app/services/memory/__init__.py`
- `backend/tests/test_memory_protocol_fake.py`

Unrelated workspace changes were **not** staged.

## Test Summary

| Step | Command | Result |
|------|---------|--------|
| Pre-implement | `pytest tests/test_memory_protocol_fake.py -v` | FAIL — `ModuleNotFoundError: No module named 'app.services.memory'` |
| Post-implement | same | **PASS** — 1 passed in ~0.55s |

Test coverage: create graph → set ontology → ingest episode → list nodes → search by substring → delete graph roundtrip.

## Implementation Notes

- **DTOs** (`types.py`): `GraphNode`, `GraphEdge`, `SearchHits`, `EpisodeItem`, `IngestResult` match brief signatures exactly.
- **Protocol** (`protocol.py`): `KnowledgeGraphBackend` typing.Protocol with all 10 methods.
- **Fake** (`fake_backend.py`): dict-of-graphs keyed by `graph_id`; exposes `fake._graphs[gid]["ontology"]` for later tasks.
- **`add_episodes`**: seeds one node (first capitalized token, default `"Entity"`) and one self-loop edge with `fact = content`.
- **`search`**: case-insensitive substring over edge facts and node name/summary; `scope` ignored (ponytail comment).
- **`__init__.py`**: re-exports protocol, DTOs, and `FakeKnowledgeGraphBackend`.

## Self-Review

| Check | Outcome |
|-------|---------|
| Brief interfaces match | Yes |
| TDD order (fail → pass) | Yes |
| No zep/graphiti SDK imports | Yes |
| Graph keyed by `graph_id` string | Yes |
| Only Task 2 files committed | Yes |
| Linter clean | Yes |

## Concerns / Follow-ups

1. **`search` scope** — Fake ignores `scope`; real Graphiti/Zep backends must honor `"edges"` / other values in Task 3+.
2. **`get_node` is global** — scans all graphs by UUID; sufficient for fake/CI; real backend may need graph-scoped lookup later.
3. **Import side-effect** — `from app.services.memory.*` loads `app.services.__init__` (pulls zep deps). CI with `.venv`/uv is fine; bare conda env without zep-cloud fails at collection. Pre-existing, not introduced by Task 2.
4. **Synthetic graph shape** — self-loop edge is minimal; contract tests only need substring search, not realistic topology.

## Next Task

Task 3 can implement `GraphitiKnowledgeGraphBackend` against the same protocol using `graph_id` as Graphiti `group_id`.
