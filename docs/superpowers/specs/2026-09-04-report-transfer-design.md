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
- Deploy path: document and rely on existing `docker compose` so both instances run the same stack (`uploads/` volume + Neo4j).
- Import UX: Home / history **Import report**; Export UX: Step 5 when report is complete.

## Non-goals

- Re-running ontology / graph build / multi-round simulation on the receiving instance.
- Standalone static website that chats without MiroFish.
- Cross-version migration beyond a single `format_version` (unsupported versions fail clearly).
- Sharing Neo4j volumes between machines as the primary transfer mechanism (zip is primary; Compose is deploy).

## Decision summary

| Choice | Selection |
|--------|-----------|
| Transfer unit | Versioned `.mirofish.zip` (one project’s results) |
| Deploy | Docker Compose (existing) for identical viewer instances |
| Report Agent graph | Bundled `graph.json` snapshot; tools use snapshot when present |
| Live agent chat / survey | Include full simulation dir; **resume OASIS** into wait-for-commands after import |
| IDs on import | Mint new `project_id` / `simulation_id` / `report_id`; remap refs |
| Interviews without env | Out of scope — env must be started so `check_env_alive()` succeeds |

## Architecture

```
Instance A (completed run)          Package              Instance B (viewer)
─────────────────────────          ─────────            ────────────────────
uploads/project|sim|report   →   .mirofish.zip   →   uploads/ (new IDs)
graph via API snapshot       →   graph/graph.json →  snapshot (+ optional Neo4j load)
                                                      │
                                                      ├─ Step 5 Report Agent (snapshot tools)
                                                      └─ Start world → OASIS wait-for-commands
                                                           → interview/batch → live chat + survey
```

Both instances run the same MiroFish server. Live chat does not talk only to Flask: UI → `/api/simulation/interview*` → IPC → OASIS worker for that `simulation_id`. Import restores files; **Start world** brings the worker up.

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
  graph.json               # nodes/edges snapshot from graph data API
```

`manifest.json` fields (minimum):

- `format_version` (e.g. `1`)
- `exported_at`
- `title`, `simulation_requirement`
- `source`: original `project_id`, `simulation_id`, `report_id`, `graph_id`
- `platforms`: which sim DBs/profiles are included
- `capabilities`: `{ "report_agent": true, "live_world": true|false }`  
  (`live_world` false if DBs/profiles missing — import still allowed for report-only chat)

## Components

### Backend

- `POST /api/report/export` — body `{ "report_id" }` → build zip → download.  
  Resolve simulation + project from report meta; fetch graph if needed; cache under `uploads/exports/`.
- `POST /api/report/import` — multipart zip → validate → mint IDs → atomic write into `uploads/{projects,simulations,reports}/` + store `graph.json` beside sim or under `uploads/exports/` keyed by new graph id → return `{ project_id, simulation_id, report_id, graph_id, capabilities }`.
- Helper module (e.g. `report_transfer.py`): pack, unpack, ID remap, validation. Keep routes thin.
- **Resume world:** endpoint or reuse/extend existing simulation start so an imported sim with configs + DBs enters OASIS **wait-for-commands** without re-running rounds. Must make `SimulationIPCClient.check_env_alive()` true.
- Report Agent / graph tools: if a local `graph.json` exists for the simulation’s `graph_id`, use it for search/entity/stats tools; otherwise keep current live memory-backend behavior.

### Frontend

- Step 5: **Export** when report is completed → download `.mirofish.zip`.
- Home / `HistoryDatabase`: **Import report** → file picker → import API → navigate to interaction/Step 5.
- After import: **Start world** (auto-attempt or explicit button) until env alive; then enable individual chat + survey tabs. If `capabilities.live_world` is false, show clear message that only Report Agent is available.

### Compose

- Existing `docker-compose.yml` (mirofish + neo4j, `./backend/uploads` mount) is the supported viewer deploy.
- Docs: viewer = configure `.env` (LLM keys) → `docker compose up` → Import zip → Start world → Step 5.

## Data flow

1. Instance A finishes report → user clicks Export → zip download.  
2. Instance B Import → disk layout looks like a normal completed project with remapped IDs.  
3. User opens Step 5 → Report Agent works immediately (report + graph snapshot + B’s LLM).  
4. Start world → OASIS resumes from imported DBs/profiles → individual chat + survey use existing `interview` / `interview/batch` APIs.

## Error handling

- Export: missing report, empty markdown, graph fetch failure, incomplete sim when claiming `live_world` → clear 4xx/5xx with message. Prefer exporting with `live_world: false` over failing if only DBs are missing (still useful for Report Agent).
- Import: bad zip, missing `manifest.json` / required report files / unsupported `format_version` → 400. Unpack to temp, validate, then move (no partial `uploads/` corruption).
- Start world: missing configs/DBs or OASIS start failure → surface error; Report Agent remains usable.
- Chat/survey while env down → existing “environment not running” errors; UI should prompt Start world.

## Testing

- Unit: pack/unpack round-trip, ID remap, manifest validation, reject bad `format_version`.
- Unit: Report Agent tools resolve answers from a fixture `graph.json` without live Neo4j/Zep.
- Integration (as feasible): import zip → export meta present → chat report endpoint succeeds with mocked LLM; resume world sets env alive (or mock IPC) so interview path is wired.
- Manual: two Compose (or local) instances — export from A, import on B, Start world, exercise all three Interactive Tools.

## Implementation notes

- Reuse existing disk layouts under `backend/uploads/` rather than inventing a parallel store.
- Existing `scripts/export_html.py` stays as optional static viewer; not the transfer path.
- YAGNI: no multi-project bulk export; no CLI required for v1 (Compose + UI is enough); CLI can wrap the same helper later.

## Open implementation detail (resolve in plan)

Exact “resume without re-running rounds” hook in `SimulationRunner` / parallel runner — prefer extending current start/wait-for-commands path over a parallel interview stack. Plan should spike this early; if resume-from-DB is missing, add a minimal resume entrypoint as part of this feature.
