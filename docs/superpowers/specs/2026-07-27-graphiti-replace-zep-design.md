# Replace Zep Cloud with Graphiti (Neo4j) as Default Memory Backend

**Date:** 2026-07-27  
**Status:** Approved for planning  
**Branch:** `feature/graphiti-replace-zep`

## Problem

MiroFish depends on Zep Cloud (`zep-cloud==3.25.0`, `ZEP_API_KEY`) for knowledge-graph construction, entity reads, Report Agent search tools, and live simulation memory updates. Self-hosting “local Zep” is not viable: Zep Community Edition is deprecated; Zep’s supported open-source path is Graphiti + a graph database.

## Goals

- Default memory/knowledge-graph backend: **Graphiti** (`graphiti-core`) + **Neo4j** via Docker Compose.
- Keep **Zep Cloud** available behind a feature flag for rollback.
- Preserve existing MiroFish HTTP/API contracts (`graph_id`, Report Agent tools, simulation memory hooks) so frontend and high-level services stay stable.
- Do not migrate existing Zep Cloud graphs into Neo4j.

## Non-goals

- Hosting deprecated Zep Community Edition.
- Renaming all `zep_*` modules/files (deferred follow-up).
- Multi-worker distributed locking beyond the current process-local graph lifecycle locks.
- Cross-backend project portability (switching backends mid-project is unsupported).

## Decision summary

| Choice | Selection |
|--------|-----------|
| Approach | Shared `KnowledgeGraphBackend` adapter + dual implementations |
| Default backend | Graphiti in-process Python SDK |
| Graph DB | Neo4j (Docker Compose) |
| Legacy backend | Zep Cloud via `MEMORY_BACKEND=zep` |
| API compatibility | Stable MiroFish contracts; swap under the adapter |
| Local Zep CE | Rejected (deprecated) |

## Architecture

```
Frontend / Report Agent / Simulation
              │
              ▼
     existing services
  (graph_builder, tools,
   entity_reader, memory_updater)
              │
              ▼
   KnowledgeGraphBackend (protocol)
         ┌────┴────┐
         ▼         ▼
  GraphitiBackend   ZepCloudBackend
  (graphiti-core)   (zep-cloud SDK)
         │
         ▼
   Neo4j (Docker)
```

- Zep Cloud `graph_id` maps 1:1 to Graphiti `group_id`. MiroFish continues to store and expose `graph_id` in the project model.
- Factory `get_memory_backend()` selects implementation from `MEMORY_BACKEND`.

## Components

### `KnowledgeGraphBackend` protocol

Minimum operations (cover current Zep usage):

| Operation | Purpose |
|-----------|---------|
| `create_graph(graph_id, name)` | Create isolated graph / group |
| `delete_graph(graph_id)` | Tear down graph |
| `get_graph(graph_id)` | Existence / metadata check |
| `set_ontology(graph_id, ontology)` | Custom entity/edge types |
| `add_episodes(graph_id, items, ...)` | Ingest text as episodes |
| `wait_for_episodes(...)` / progress hooks | Preserve TaskManager progress UX |
| `list_nodes(graph_id)` / `list_edges(graph_id)` | Entity reader + statistics |
| `get_node(uuid)` / `get_node_edges(...)` | Detail views |
| `search(graph_id, query, limit, ...)` | Report tools + persona generation |

Search and read results normalize to existing MiroFish shapes: `{facts, edges, nodes}` and node/edge dataclasses used by `zep_tools` / `zep_entity_reader`.

### `GraphitiBackend`

- Uses `graphiti-core` against Neo4j (`NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`).
- Configures LLM + embedder via OpenAI-compatible clients, reusing MiroFish `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL_NAME` where possible.
- Optional override: `GRAPHITI_EMBEDDING_MODEL`, `GRAPHITI_EMBEDDING_DIM`, and embedding base URL/key if distinct from chat LLM.
- Bridges Graphiti’s async API to MiroFish’s sync service layer with a small sync runner (no asyncio rewrite of the whole backend in this change).
- Ontology: map MiroFish ontology dict → Graphiti Pydantic entity type models (and edge constraints where supported).
- Ingest: replace Zep Batch API with batched/sequential `add_episode` calls while still reporting progress to TaskManager.

### `ZepCloudBackend`

- Move current direct `zep-cloud` client usage behind the same protocol.
- Preserve existing retry, timeout, paging, and Cloud-only URL policy when `MEMORY_BACKEND=zep`.

### Service facades

- `graph_builder`, `zep_entity_reader`, `zep_tools`, `zep_graph_memory_updater` call the protocol, not a concrete SDK.
- Keep current file/class names for this change to limit churn; they become backend-agnostic facades.
- Process-local lifecycle locking (`zep_lifecycle`) remains keyed by `graph_id` and applies to both backends.

### Docker / ops

- Add a `neo4j` service to Compose (ports `7474` / `7687`, persistent volume, APOC as required by Graphiti docs).
- Document starting Neo4j for source-based `npm run dev` workflows.
- App container (if used) depends on Neo4j when `MEMORY_BACKEND=graphiti`.

## Configuration

```env
# graphiti (default) | zep
MEMORY_BACKEND=graphiti

# Required when MEMORY_BACKEND=graphiti
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password

# Optional Graphiti embedding overrides
# GRAPHITI_EMBEDDING_MODEL=...
# GRAPHITI_EMBEDDING_DIM=...
# GRAPHITI_EMBEDDING_BASE_URL=...
# GRAPHITI_EMBEDDING_API_KEY=...

# Required when MEMORY_BACKEND=zep
ZEP_API_KEY=your_zep_api_key_here
```

`Config.validate()`:

- `graphiti` → require Neo4j credentials (and LLM key as today).
- `zep` → require `ZEP_API_KEY`; keep rejecting unsupported `ZEP_API_URL`.
- Do not require Zep key when running Graphiti, and vice versa.

Docs and `.env.example` present Graphiti + Neo4j as the default setup. Frontend copy that mentions Zep (e.g. setup hints) should say “knowledge graph / Graphiti” for the default path; no in-app backend switcher in this change—operators pick the backend via env.

## Data flow

1. **Graph build:** seed text → chunks → `add_episodes` on selected backend → ontology applied → nodes/edges readable via `graph_id`.
2. **Env setup:** entity reader lists/filters nodes by ontology labels through the protocol.
3. **Simulation:** agent activities → episode text → `add_episodes` (same graph).
4. **Report Agent:** InsightForge / Panorama / QuickSearch → protocol `search` + node/edge reads → unchanged tool result shapes.

## Error handling

- Neo4j/Graphiti unavailable → clear API errors (aligned with current missing-config failures).
- Transient Neo4j/LLM/network errors on ingest/search → retry with existing backoff patterns where safe.
- Permanent ingest failures → fail the build/update task with a readable message.
- Empty search → keep existing tool-level fallbacks.
- Backend switch does not migrate data; document that projects are bound to the backend that created their graph.

## Testing

- Unit tests for the protocol using a fake in-memory backend (create → ingest → search shape).
- Graphiti backend tests with mocked `graphiti-core` (no live Neo4j in CI).
- Existing Zep tests remain; runnable when `MEMORY_BACKEND=zep` / with mocks; CI must not require Cloud keys.
- Manual smoke: Neo4j up → small graph build → search → one simulation memory update round.

## Rollout

1. Land adapter + Graphiti backend + Neo4j Compose on feature branch.
2. Default `MEMORY_BACKEND=graphiti` in `.env.example`.
3. Keep `MEMORY_BACKEND=zep` as escape hatch until Graphiti path is trusted.
4. Follow-up PR: remove Zep backend and rename `zep_*` facades.

## Success criteria

- With Neo4j running and `MEMORY_BACKEND=graphiti`, a full MiroFish flow (build graph → prepare sim → run rounds with memory updates → report tools) works without `ZEP_API_KEY`.
- With `MEMORY_BACKEND=zep` and a valid Cloud key, existing Zep behavior still works.
- Frontend continues to use `graph_id` without breaking contract changes.
)
