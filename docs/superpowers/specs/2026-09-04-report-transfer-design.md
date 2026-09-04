# Report Transfer Between MiroFish Instances

**Date:** 2026-09-04  
**Status:** Approved for planning  

## Problem

A completed prediction (report + simulation world) lives on one MiroFish instance. Another person running the same MiroFish stack should be able to open that result, read the report, chat with the Report Agent, and use live Interactive Tools (chat with individuals, world survey) without re-running the full pipeline.

Today there is only a static offline HTML exporter (`scripts/export_html.py`) with no import path and no live agent runtime on the receiving side.

## Goals

- Export one completed project’s **simulation results** as a portable `.mirofish.zip`.
- Import that zip on another MiroFish instance (same app via Docker Compose or local `npm run dev`).
- After import, Step 5 Interactive Tools work:
  - Chat with Report Agent
  - Chat with any individual agent
  - Send survey / questionnaire into the world
- Deploy path: `docker compose` starts **MiroFish + local Neo4j** by default (Graphiti/Neo4j memory backend). Viewer instances use the same stack.
- Transfer is **one file only**: `.mirofish.zip` includes report, simulation artifacts, **and** the graph. No separate Neo4j dump or second artifact.
- Import UX: Home / history **Import report**; Export UX: Step 5 when report is complete.

## Non-goals

- Re-running ontology / graph build / multi-round simulation on the receiving instance.
- Standalone static website that chats without MiroFish.
- Cross-version migration beyond a single `format_version` (unsupported versions fail clearly).
- Shipping a separate Neo4j volume backup or multi-file transfer kit (graph travels inside the zip).
- Making Zep Cloud the default for viewer instances.

## Decision summary

| Choice | Selection |
|--------|-----------|
| Transfer unit | Single `.mirofish.zip` (report + sim + graph inside) |
| Deploy | Docker Compose starts **local Neo4j** + MiroFish; Graphiti+Neo4j default |
| Report Agent graph | Graph embedded in zip; **import loads it into local Neo4j** under the new `graph_id` |
| Live agent chat / survey | Include full simulation dir; **resume OASIS** into wait-for-commands after import |
| IDs on import | Mint new `project_id` / `simulation_id` / `report_id` / `graph_id`; remap refs |
| Interviews without env | Out of scope — env must be started so `check_env_alive()` succeeds |

## Architecture

```
Instance A (completed run)          ONE file             Instance B (viewer Compose)
─────────────────────────          ─────────            ──────────────────────────
uploads/project|sim|report   →                       →  uploads/ (new IDs)
graph (from live Neo4j/API)  →   .mirofish.zip      →  load graph/graph.json → local Neo4j
                                 (graph inside)         Graphiti tools use new graph_id
                                                      │
                                                      ├─ Step 5 Report Agent (Neo4j-backed tools)
                                                      └─ Start world → OASIS wait-for-commands
                                                           → interview/batch → live chat + survey
```

Both instances run the same MiroFish server with **local Neo4j** (Compose). Transfer is a single zip; the receiving Neo4j is empty until import hydrates it from `graph/graph.json`. Live chat still needs OASIS: UI → `/api/simulation/interview*` → IPC → worker for that `simulation_id`. Import restores files; **Start world** brings the worker up.

## Package format

```
manifest.json
project/
  project.json
simulation/
  state.json
  simulation_config.json
  env_status.json          # if present
  reddit_profiles.json     # if present
  twitter_profiles.csv     # if present
  reddit_simulation.db     # if present (required for live resume when that platform was used)
  twitter_simulation.db    # if present
  run_state.json           # if present
  # other sim artifacts as available; omit huge regenerable caches if unused
report/
  meta.json
  outline.json
  full_report.md
  section_*.md             # if present
graph/
  graph.json               # REQUIRED — full nodes/edges for Neo4j hydrate on import
```

The zip is the only handoff artifact. Graph must be inside it so instance B does not need instance A’s Neo4j volume.

`manifest.json` fields (minimum):

- `format_version` (e.g. `1`)
- `exported_at`
- `title`, `simulation_requirement`
- `source`: original `project_id`, `simulation_id`, `report_id`, `graph_id`
- `platforms`: which sim DBs/profiles are included
- `capabilities`: `{ "report_agent": true, "live_world": true|false }`  
  (`live_world` false if DBs/profiles missing — import still allowed for Report Agent after Neo4j hydrate)

## Components

### Backend

- `POST /api/report/export` — body `{ "report_id" }` → build **one** `.mirofish.zip` (including graph) → download.  
  Resolve simulation + project from report meta; export graph from the default Graphiti/Neo4j backend (or graph data API); fail export if graph cannot be included.
- `POST /api/report/import` — multipart zip → validate (require `graph/graph.json`) → mint IDs → atomic write into `uploads/{projects,simulations,reports}/` → **hydrate graph into local Neo4j** under the new `graph_id` → return `{ project_id, simulation_id, report_id, graph_id, capabilities }`.
- Helper module (e.g. `report_transfer.py`): pack, unpack, ID remap, validation, Neo4j hydrate. Keep routes thin.
- **Resume world:** endpoint or reuse/extend existing simulation start so an imported sim with configs + DBs enters OASIS **wait-for-commands** without re-running rounds. Must make `SimulationIPCClient.check_env_alive()` true.
- Report Agent / graph tools: after import, use the **default Neo4j-backed** memory backend with the new `graph_id` (no special snapshot-only tool path required once hydrate succeeds). Keep a zip-local `graph.json` copy on disk for re-hydrate/debug if needed.

### Frontend

- Step 5: **Export** when report is completed → download `.mirofish.zip`.
- Home / `HistoryDatabase`: **Import report** → file picker → import API → navigate to interaction/Step 5.
- After import: **Start world** (auto-attempt or explicit button) until env alive; then enable individual chat + survey tabs. If `capabilities.live_world` is false, show clear message that only Report Agent is available.

### Compose

- `docker-compose.yml` must start **local Neo4j** and MiroFish by default (`depends_on` healthy Neo4j; `./backend/uploads` mount). Viewer default memory backend: Graphiti + that Neo4j.
- Docs: viewer = configure `.env` (LLM keys + Neo4j password) → `docker compose up` → Import **one** zip → Start world → Step 5.

## Data flow

1. Instance A finishes report → Export → single `.mirofish.zip` (graph inside).  
2. Instance B Import → write uploads + hydrate graph into **local Neo4j** with new IDs.  
3. User opens Step 5 → Report Agent works (report + Neo4j graph + B’s LLM).  
4. Start world → OASIS resumes from imported DBs/profiles → individual chat + survey use existing `interview` / `interview/batch` APIs.

## Error handling

- Export: missing report, empty markdown, **missing/unloadable graph**, incomplete sim when claiming `live_world` → clear 4xx/5xx. Prefer `live_world: false` over failing if only DBs are missing (Report Agent still needs a successful graph include).
- Import: bad zip, missing `manifest.json` / report files / **`graph/graph.json`**, unsupported `format_version`, Neo4j hydrate failure → 400/5xx with message. Unpack to temp, validate, then move + hydrate (no partial `uploads/` corruption; failed hydrate should not leave a “ready” imported project without graph).
- Start world: missing configs/DBs or OASIS start failure → surface error; Report Agent remains usable if Neo4j hydrate succeeded.
- Chat/survey while env down → existing “environment not running” errors; UI should prompt Start world.

## Testing

- Unit: pack/unpack round-trip (graph required in zip), ID remap, manifest validation, reject bad `format_version` / missing graph.
- Unit/integration: import hydrate writes nodes/edges into Neo4j under new `graph_id` (testcontainer or mocked backend).
- Integration (as feasible): import zip → Report Agent chat with mocked LLM against hydrated graph; resume world sets env alive (or mock IPC) so interview path is wired.
- Manual: two Compose instances (each with local Neo4j) — export one zip from A, import on B, Start world, exercise all three Interactive Tools.

## Implementation notes

- Reuse existing disk layouts under `backend/uploads/` rather than inventing a parallel store.
- Compose already includes a `neo4j` service; ensure docs and defaults assume it is always up for transfer/viewer use.
- Existing `scripts/export_html.py` stays as optional static viewer; not the transfer path.
- YAGNI: no multi-project bulk export; no CLI required for v1 (Compose + UI is enough); CLI can wrap the same helper later.

## Open implementation detail (resolve in plan)

1. Exact “resume without re-running rounds” hook in `SimulationRunner` / parallel runner — prefer extending current start/wait-for-commands path. Spike early; add a minimal resume entrypoint if missing.
2. Exact Graphiti/Neo4j **hydrate from `graph.json`** API (bulk node/edge write under new `group_id`/`graph_id`) — plan should spike against current `KnowledgeGraphBackend` / GraphitiBackend; add a dedicated import helper if the protocol lacks bulk ingest.
