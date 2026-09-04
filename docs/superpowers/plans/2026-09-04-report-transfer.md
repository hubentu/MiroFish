# Report Transfer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one MiroFish instance export a completed run as a single `.mirofish.zip` (report + simulation + graph), and let another Compose-based instance import it, hydrate Neo4j, resume OASIS for live interviews, and use all Step 5 Interactive Tools.

**Architecture:** Pure pack/unpack helper writes/reads the zip and remaps IDs into existing `uploads/` layouts. Graph travels inside the zip as `graph/graph.json` and is hydrated into local Neo4j via a new `hydrate_graph_snapshot` on `KnowledgeGraphBackend`. Live chat requires a new **resume-for-interview** path that starts OASIS from existing DBs and skips simulation rounds, entering wait-for-commands. UI: Export on Step 5; Import on Home history; Start world after import.

**Tech Stack:** Python 3.11+, Flask, Graphiti + Neo4j (Compose), Vue 3 frontend, pytest, existing OASIS parallel runner / IPC.

## Global Constraints

- Transfer is **one file only**: `.mirofish.zip` must include `graph/graph.json`.
- Viewer default: `MEMORY_BACKEND=graphiti` with Compose **local Neo4j** always started.
- Import mints new `project_id` / `simulation_id` / `report_id` / `graph_id` and remaps refs.
- Live individual chat + survey require OASIS `check_env_alive()` after **Start world**; do not invent a second interview stack.
- Reuse `uploads/{projects,simulations,reports}/` layouts; no parallel store.
- Unit tests must not require live Neo4j/OASIS (use `FakeKnowledgeGraphBackend` / mocks).
- YAGNI: no CLI in v1, no multi-project bulk export, no static-site chat path.
- Spec: `docs/superpowers/specs/2026-09-04-report-transfer-design.md`

---

## File structure

| File | Responsibility |
|------|----------------|
| `backend/app/services/report_transfer.py` | Manifest, pack zip, unpack/validate, ID remap, capabilities detection |
| `backend/app/services/memory/protocol.py` | Add `hydrate_graph_snapshot` |
| `backend/app/services/memory/fake_backend.py` | In-memory hydrate for tests |
| `backend/app/services/memory/graphiti_backend.py` | Neo4j hydrate from snapshot |
| `backend/app/services/memory/zep_cloud_backend.py` | Raise `NotImplementedError` (transfer requires Graphiti default) |
| `backend/app/api/report.py` | `POST /export`, `POST /import` |
| `backend/app/services/simulation_runner.py` | `resume_for_interview(simulation_id)` |
| `backend/scripts/run_parallel_simulation.py` | `--interview-only` skip rounds → wait-for-commands |
| `backend/app/api/simulation.py` | `POST /resume-world` (or extend start) |
| `frontend/src/api/report.js` | `exportReport`, `importReport` |
| `frontend/src/api/simulation.js` | `resumeWorld` |
| `frontend/src/components/Step5Interaction.vue` | Export button |
| `frontend/src/components/HistoryDatabase.vue` | Import control + navigate |
| `frontend/src/views/InteractionView.vue` | Start world affordance after import |
| `locales/en.json`, `locales/zh.json` | Copy for export/import/start world |
| `README.md` / `README-ZH.md` | Viewer: compose up → import zip → start world |
| `backend/tests/test_report_transfer.py` | Pack/unpack/remap/validation |
| `backend/tests/test_graph_hydrate.py` | Fake + Graphiti hydrate contract |
| `backend/tests/test_report_transfer_api.py` | Export/import Flask routes with fakes |
| `backend/tests/test_resume_world.py` | Resume wiring (mocked process/IPC) |

---

### Task 1: Zip pack / unpack / ID remap

**Files:**
- Create: `backend/app/services/report_transfer.py`
- Create: `backend/tests/test_report_transfer.py`

**Interfaces:**
- Consumes: stdlib `zipfile`, `json`, `uuid`, `pathlib`; uploads root from config/env
- Produces:
  - `FORMAT_VERSION = 1`
  - `build_manifest(...) -> dict`
  - `pack_transfer_zip(*, project_dir, simulation_dir, report_dir, graph_data: dict, dest_zip: Path) -> Path`
  - `unpack_and_validate(zip_path: Path, work_dir: Path) -> dict` (returns paths + manifest; raises `ValueError`)
  - `remap_ids(manifest, project_data, sim_state, report_meta, new_ids: dict) -> tuple[dict, dict, dict]`
  - `detect_live_world_capability(simulation_dir: Path) -> bool`
  - `mint_ids() -> dict` with keys `project_id`, `simulation_id`, `report_id`, `graph_id`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_report_transfer.py
import json
from pathlib import Path

import pytest

from app.services.report_transfer import (
    FORMAT_VERSION,
    detect_live_world_capability,
    mint_ids,
    pack_transfer_zip,
    remap_ids,
    unpack_and_validate,
)


def _write_tree(root: Path):
    proj = root / "project"
    sim = root / "simulation"
    report = root / "report"
    proj.mkdir()
    sim.mkdir()
    report.mkdir()
    (proj / "project.json").write_text(
        json.dumps({
            "project_id": "proj_old",
            "graph_id": "mirofish_old",
            "simulation_requirement": "predict X",
            "status": "graph_completed",
        }),
        encoding="utf-8",
    )
    (sim / "state.json").write_text(
        json.dumps({
            "simulation_id": "sim_old",
            "project_id": "proj_old",
            "graph_id": "mirofish_old",
            "status": "completed",
        }),
        encoding="utf-8",
    )
    (sim / "simulation_config.json").write_text("{}", encoding="utf-8")
    (sim / "reddit_profiles.json").write_text("[]", encoding="utf-8")
    (sim / "reddit_simulation.db").write_bytes(b"SQLite")
    (report / "meta.json").write_text(
        json.dumps({
            "report_id": "report_old",
            "simulation_id": "sim_old",
            "graph_id": "mirofish_old",
            "status": "completed",
            "simulation_requirement": "predict X",
        }),
        encoding="utf-8",
    )
    (report / "outline.json").write_text("{}", encoding="utf-8")
    (report / "full_report.md").write_text("# Title\n\nbody", encoding="utf-8")
    graph = {
        "graph_id": "mirofish_old",
        "nodes": [{"uuid": "n1", "name": "A", "labels": ["Entity"], "summary": ""}],
        "edges": [],
    }
    return proj, sim, report, graph


def test_round_trip_requires_graph(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    proj, sim, report, graph = _write_tree(src)
    zpath = tmp_path / "out.mirofish.zip"
    pack_transfer_zip(
        project_dir=proj,
        simulation_dir=sim,
        report_dir=report,
        graph_data=graph,
        dest_zip=zpath,
    )
    work = tmp_path / "work"
    work.mkdir()
    result = unpack_and_validate(zpath, work)
    assert result["manifest"]["format_version"] == FORMAT_VERSION
    assert (work / "graph" / "graph.json").exists()
    assert result["manifest"]["capabilities"]["live_world"] is True


def test_reject_missing_graph(tmp_path: Path):
    zpath = tmp_path / "bad.zip"
    import zipfile
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format_version": FORMAT_VERSION}))
        zf.writestr("report/full_report.md", "# x")
        zf.writestr("report/meta.json", "{}")
    with pytest.raises(ValueError, match="graph"):
        unpack_and_validate(zpath, tmp_path / "w")


def test_remap_ids_rewrites_refs():
    new_ids = mint_ids()
    project = {"project_id": "proj_old", "graph_id": "g_old"}
    state = {"simulation_id": "sim_old", "project_id": "proj_old", "graph_id": "g_old"}
    meta = {"report_id": "report_old", "simulation_id": "sim_old", "graph_id": "g_old"}
    p2, s2, m2 = remap_ids({}, project, state, meta, new_ids)
    assert p2["project_id"] == new_ids["project_id"]
    assert s2["simulation_id"] == new_ids["simulation_id"]
    assert m2["report_id"] == new_ids["report_id"]
    assert p2["graph_id"] == new_ids["graph_id"]
    assert s2["graph_id"] == new_ids["graph_id"]
    assert m2["graph_id"] == new_ids["graph_id"]


def test_detect_live_world_needs_db_and_profiles(tmp_path: Path):
    sim = tmp_path / "sim"
    sim.mkdir()
    (sim / "simulation_config.json").write_text("{}", encoding="utf-8")
    assert detect_live_world_capability(sim) is False
    (sim / "reddit_profiles.json").write_text("[]", encoding="utf-8")
    (sim / "reddit_simulation.db").write_bytes(b"x")
    assert detect_live_world_capability(sim) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_report_transfer.py -v`  
Expected: FAIL with `ModuleNotFoundError` or import error for `report_transfer`

- [ ] **Step 3: Write minimal implementation**

Implement `backend/app/services/report_transfer.py`:

- Zip layout exactly as spec (`manifest.json`, `project/`, `simulation/`, `report/`, `graph/graph.json`).
- Copy allowlisted simulation files if present: `state.json`, `simulation_config.json`, `env_status.json`, `reddit_profiles.json`, `twitter_profiles.csv`, `reddit_simulation.db`, `twitter_simulation.db`, `run_state.json`.
- Copy report files: `meta.json`, `outline.json`, `full_report.md`, `section_*.md`.
- `unpack_and_validate`: require `format_version == FORMAT_VERSION`, `report/full_report.md`, `report/meta.json`, `graph/graph.json` with a JSON object that includes a `nodes` key (empty `nodes` → `ValueError`).
- `mint_ids`: `proj_` / `sim_` / `report_` + 12 hex; `graph_id` = `mirofish_` + 16 hex (match existing style).
- `detect_live_world_capability`: True if `simulation_config.json` exists AND at least one of (reddit profiles+db) or (twitter profiles+db).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_report_transfer.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/report_transfer.py backend/tests/test_report_transfer.py
git commit -m "feat(transfer): add .mirofish.zip pack/unpack and ID remap"
```

---

### Task 2: Hydrate graph snapshot into Neo4j (protocol + fake + Graphiti)

**Files:**
- Modify: `backend/app/services/memory/protocol.py`
- Modify: `backend/app/services/memory/fake_backend.py`
- Modify: `backend/app/services/memory/graphiti_backend.py`
- Modify: `backend/app/services/memory/zep_cloud_backend.py`
- Create: `backend/tests/test_graph_hydrate.py`

**Interfaces:**
- Consumes: `GraphNode` / `GraphEdge` DTOs; snapshot dict shaped like `GraphBuilderService.get_graph_data`
- Produces: `KnowledgeGraphBackend.hydrate_graph_snapshot(self, graph_id: str, snapshot: dict) -> None`  
  Creates graph if missing; writes all nodes then edges under `graph_id` as Graphiti `group_id`; subsequent `list_nodes` / `search` see the data.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_graph_hydrate.py
from app.services.memory.fake_backend import FakeKnowledgeGraphBackend


def test_fake_hydrate_round_trip():
    backend = FakeKnowledgeGraphBackend()
    snapshot = {
        "nodes": [
            {"uuid": "n1", "name": "Alice", "labels": ["Person"], "summary": "researcher", "attributes": {}},
            {"uuid": "n2", "name": "Bob", "labels": ["Person"], "summary": "", "attributes": {}},
        ],
        "edges": [
            {
                "uuid": "e1",
                "name": "KNOWS",
                "fact": "Alice knows Bob",
                "source_node_uuid": "n1",
                "target_node_uuid": "n2",
                "attributes": {},
                "episodes": [],
            }
        ],
    }
    backend.hydrate_graph_snapshot("mirofish_new", snapshot)
    nodes = backend.list_nodes("mirofish_new")
    edges = backend.list_edges("mirofish_new")
    assert {n.uuid for n in nodes} == {"n1", "n2"}
    assert len(edges) == 1
    hits = backend.search("mirofish_new", "Alice", limit=5)
    assert hits.total_count >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_graph_hydrate.py::test_fake_hydrate_round_trip -v`  
Expected: FAIL (`hydrate_graph_snapshot` missing)

- [ ] **Step 3: Implement Fake + protocol stub**

Add method to protocol. Implement on `FakeKnowledgeGraphBackend` by filling `_graphs[graph_id]["nodes"|"edges"]` using existing `GraphNode`/`GraphEdge` construction patterns in that file. On `ZepCloudBackend`, implement as:

```python
def hydrate_graph_snapshot(self, graph_id: str, snapshot: dict) -> None:
    raise NotImplementedError("Report transfer hydrate requires MEMORY_BACKEND=graphiti")
```

- [ ] **Step 4: Implement GraphitiBackend.hydrate_graph_snapshot**

Spike against existing Neo4j labels Graphiti uses in this repo (read `graphiti_backend.py` list_nodes Cypher). Minimal approach:

1. `create_graph(graph_id, name=graph_id)` if not exists.
2. For each node: MERGE by uuid + set `group_id`, name, summary, labels/attributes.
3. For each edge: MERGE relationship between source/target with fact/name/`group_id`.

Preserve exported UUIDs so edge endpoints resolve. Prefer direct Cypher via the backend’s Neo4j driver/async bridge over re-LLM episode ingest (faster, deterministic).

Add a second test that **skips** without Neo4j:

```python
import os
import pytest

@pytest.mark.skipif(os.getenv("MIROFISH_NEO4J_INTEGRATION") != "1", reason="needs live Neo4j")
def test_graphiti_hydrate_integration():
    from app.services.memory.graphiti_backend import GraphitiBackend
    # hydrate small snapshot, list_nodes, delete_graph cleanup
```

- [ ] **Step 5: Run unit tests**

Run: `cd backend && python -m pytest tests/test_graph_hydrate.py -v`  
Expected: fake test PASS; integration skipped unless env set

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/memory/protocol.py backend/app/services/memory/fake_backend.py \
  backend/app/services/memory/graphiti_backend.py backend/app/services/memory/zep_cloud_backend.py \
  backend/tests/test_graph_hydrate.py
git commit -m "feat(memory): hydrate graph snapshots into Graphiti/Neo4j"
```

---

### Task 3: Export API

**Files:**
- Modify: `backend/app/api/report.py`
- Create: `backend/tests/test_report_transfer_api.py`

**Interfaces:**
- Consumes: `ReportManager`, simulation/project dirs on disk, `GraphBuilderService.get_graph_data`, `pack_transfer_zip`
- Produces: `POST /api/report/export` JSON `{ "report_id": "..." }` → `application/zip` download named `mirofish_<report_id>.mirofish.zip`

- [ ] **Step 1: Write failing API test**

```python
# backend/tests/test_report_transfer_api.py
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_export_requires_report_id(client):
    res = client.post("/api/report/export", json={})
    assert res.status_code == 400
```

Use the same Flask test client / upload-folder fixture pattern as existing backend API tests (e.g. `test_ontology_api_errors.py`). Extend with a filesystem fixture: plant project/sim/report under tmp uploads, mock `get_graph_data` to return one node, call export, open zip, assert `graph/graph.json` present.

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && python -m pytest tests/test_report_transfer_api.py::test_export_requires_report_id -v`  
Expected: FAIL (404 route or missing fixture)

- [ ] **Step 3: Implement route**

In `report.py`:

```python
@report_bp.route('/export', methods=['POST'])
def export_report_package():
    data = request.get_json() or {}
    report_id = data.get('report_id')
    if not report_id:
        return jsonify({"success": False, "error": "report_id required"}), 400
    report = ReportManager.get_report(report_id)
    # resolve simulation_id, project_id, graph_id from meta
    # load graph via GraphBuilderService().get_graph_data(graph_id)
    # if no nodes: 400
    # pack_transfer_zip to temp file
    # return send_file(..., mimetype='application/zip',
    #                  download_name=f'mirofish_{report_id}.mirofish.zip')
```

Set `capabilities.live_world` via `detect_live_world_capability`. Do not fail export solely because live_world is false.

- [ ] **Step 4: Tests pass**

Run: `cd backend && python -m pytest tests/test_report_transfer_api.py -k export -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/report.py backend/tests/test_report_transfer_api.py
git commit -m "feat(api): export report package as .mirofish.zip"
```

---

### Task 4: Import API (disk + Neo4j hydrate)

**Files:**
- Modify: `backend/app/api/report.py`
- Modify: `backend/app/services/report_transfer.py` (add `install_unpacked_package(...)` if useful)
- Modify: `backend/tests/test_report_transfer_api.py`

**Interfaces:**
- Consumes: `unpack_and_validate`, `mint_ids`, `remap_ids`, `get_memory_backend().hydrate_graph_snapshot`
- Produces: `POST /api/report/import` multipart field `file` → JSON  
  `{ "success": true, "data": { "project_id", "simulation_id", "report_id", "graph_id", "capabilities" } }`

- [ ] **Step 1: Write failing import tests**

```python
def test_import_rejects_non_zip(client):
    data = {"file": (io.BytesIO(b"not-a-zip"), "x.txt")}
    res = client.post(
        "/api/report/import",
        data=data,
        content_type="multipart/form-data",
    )
    assert res.status_code == 400


def test_import_writes_uploads_and_hydrates(client, tmp_path, monkeypatch):
    # build a valid zip with pack_transfer_zip
    # patch get_memory_backend to FakeKnowledgeGraphBackend singleton
    # post import
    # assert uploads/projects/proj_*/project.json exists
    # assert fake.list_nodes(new_graph_id)
    # assert response JSON ids match disk
    ...
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && python -m pytest tests/test_report_transfer_api.py -k import -v`  
Expected: FAIL (missing route)

- [ ] **Step 3: Implement import**

Algorithm:

1. Save upload to temp zip.
2. `unpack_and_validate` into temp dir.
3. `mint_ids()`; load JSON files; `remap_ids`; optional `imported_from` field on project/state.
4. Copy remapped trees into `uploads/projects/<new>/`, `uploads/simulations/<new>/`, `uploads/reports/<new>/`.
5. Also save `graph/graph.json` under `uploads/exports/<new_graph_id>.json`.
6. `backend.hydrate_graph_snapshot(new_graph_id, snapshot)`.
7. On hydrate failure: delete the new upload dirs (best-effort) and return 500 — do not leave a “ready” project without graph.
8. Return ids + capabilities (recompute `live_world` from installed sim dir).

Mark project/sim/report status so history lists them (e.g. project `graph_completed`, sim `completed`, report `completed`). Ensure `SimulationManager.get_simulation` can load remapped `state.json` — match that loader’s schema before coding.

- [ ] **Step 4: Tests pass**

Run: `cd backend && python -m pytest tests/test_report_transfer_api.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/report.py backend/app/services/report_transfer.py \
  backend/tests/test_report_transfer_api.py
git commit -m "feat(api): import .mirofish.zip and hydrate Neo4j graph"
```

---

### Task 5: Resume world for live interviews

**Files:**
- Modify: `backend/scripts/run_parallel_simulation.py`
- Modify: `backend/app/services/simulation_runner.py`
- Modify: `backend/app/api/simulation.py`
- Create: `backend/tests/test_resume_world.py`

**Interfaces:**
- Consumes: existing sim dir with config + DBs; IPC client
- Produces:
  - CLI flag `--interview-only` on parallel runner: load platforms from existing DBs, **skip round loops**, enter `wait_for_commands`
  - `SimulationRunner.resume_for_interview(simulation_id: str) -> SimulationRunState`
  - `POST /api/simulation/resume-world` body `{ "simulation_id" }` → starts resume; 400 if missing config/DBs

- [ ] **Step 1: Spike runner behavior (read-only)**

Confirm how `run_reddit_simulation` / twitter open existing `*_simulation.db`. Document in a short comment on the resume method: interview-only assumes DBs already populated by export.

- [ ] **Step 2: Write failing unit test for runner wiring**

```python
# backend/tests/test_resume_world.py
import pytest
from unittest.mock import patch


def test_resume_for_interview_requires_config(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.simulation_runner.SimulationRunner.RUN_STATE_DIR",
        str(tmp_path),
    )
    from app.services.simulation_runner import SimulationRunner
    with pytest.raises(ValueError, match="配置|config|不存在"):
        SimulationRunner.resume_for_interview("sim_missing")
```

- [ ] **Step 3: Implement `--interview-only` in `run_parallel_simulation.py`**

When set:

- Still initialize environments from config + existing DB paths (same as normal start).
- Skip the `run_*_simulation` round execution (effective rounds = 0).
- Proceed to existing `wait_for_commands` loop unchanged.

Accept the flag from `SimulationRunner` when `resume_for_interview` builds the subprocess (mirror how `start_simulation` adds `--max-rounds`).

- [ ] **Step 4: Implement `resume_for_interview`**

Clone the process-spawn path from `start_simulation`, but:

- Require live-world capability checks (config + profiles + DBs).
- Pass `--interview-only`.
- Do **not** enable graph memory updater by default.
- Refuse if env already alive / run state active (same guards as start).

- [ ] **Step 5: API route**

```python
@simulation_bp.route('/resume-world', methods=['POST'])
def resume_world():
    simulation_id = (request.get_json() or {}).get('simulation_id')
    if not simulation_id:
        return jsonify({"success": False, "error": "simulation_id required"}), 400
    state = SimulationRunner.resume_for_interview(simulation_id)
    return jsonify({"success": True, "data": state.to_dict() if hasattr(state, "to_dict") else vars(state)})
```

- [ ] **Step 6: Tests**

Run: `cd backend && python -m pytest tests/test_resume_world.py -v`  
Expected: PASS (process spawn mocked where needed)

- [ ] **Step 7: Manual smoke (when implementing)**

Export → import on clean uploads → `resume-world` → `check_env_alive` true → one `interview/batch` call.

- [ ] **Step 8: Commit**

```bash
git add backend/scripts/run_parallel_simulation.py backend/app/services/simulation_runner.py \
  backend/app/api/simulation.py backend/tests/test_resume_world.py
git commit -m "feat(simulation): resume imported world for live interviews"
```

---

### Task 6: Frontend export / import / start world

**Files:**
- Modify: `frontend/src/api/report.js`
- Modify: `frontend/src/api/simulation.js`
- Modify: `frontend/src/components/Step5Interaction.vue`
- Modify: `frontend/src/components/HistoryDatabase.vue`
- Modify: `frontend/src/views/InteractionView.vue`
- Modify: `locales/en.json`, `locales/zh.json`

**Interfaces:**
- Consumes: `/api/report/export`, `/api/report/import`, `/api/simulation/resume-world`, existing interview APIs
- Produces: UI flows matching spec

- [ ] **Step 1: API helpers**

```javascript
// report.js
export const exportReportPackage = (reportId) => {
  return service.post('/api/report/export', { report_id: reportId }, { responseType: 'blob' })
}

export const importReportPackage = (file) => {
  const form = new FormData()
  form.append('file', file)
  return service.post('/api/report/import', form, {
    headers: { 'Content-Type': 'multipart/form-data' }
  })
}

// simulation.js
export const resumeWorld = (simulationId) => {
  return service.post('/api/simulation/resume-world', { simulation_id: simulationId })
}
```

Handle blob download for export (object URL + `<a download>`). If axios interceptors break blobs, use raw `fetch` with the same base URL.

- [ ] **Step 2: Step 5 Export button**

When report is complete, show **Export** near the action bar. On click → download `mirofish_<id>.mirofish.zip`.

- [ ] **Step 3: Home / History Import**

Add **Import report** on `HistoryDatabase` header: hidden file input `accept=".zip,.mirofish.zip"`. On success → `router.push({ name: 'Interaction', params: { reportId: data.report_id } })` and pass `simulation_id` / `capabilities` via query or existing store pattern.

- [ ] **Step 4: Start world on Interaction view**

- If env not alive and live_world capable: show **Start world** → `resumeWorld(simulationId)`.
- Disable individual chat + survey until env alive; Report Agent always enabled.
- If `live_world: false`, show i18n that only Report Agent is available.

- [ ] **Step 5: i18n keys**

Add export / import / start world / live_world unavailable strings to `en.json` and `zh.json`.

- [ ] **Step 6: Manual UI check**

Export from Step 5 → import from Home → Start world → exercise all three Interactive Tools.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/report.js frontend/src/api/simulation.js \
  frontend/src/components/Step5Interaction.vue frontend/src/components/HistoryDatabase.vue \
  frontend/src/views/InteractionView.vue locales/en.json locales/zh.json
git commit -m "feat(ui): export/import report package and start world"
```

---

### Task 7: Compose defaults + docs

**Files:**
- Modify: `docker-compose.yml` (verify neo4j + depends_on + uploads; fix only if gaps)
- Modify: `.env.example`
- Modify: `README.md`, `README-ZH.md`

**Interfaces:**
- Produces: documented viewer path — `.env` → `docker compose up` → Import one zip → Start world → Step 5

- [ ] **Step 1: Verify Compose**

Confirm default `docker compose up` starts Neo4j and MiroFish with healthcheck dependency and `./backend/uploads` mount. If Neo4j is only in `docker-compose.neo4j.yml`, merge so the default file starts Neo4j.

- [ ] **Step 2: Docs section**

```markdown
## Transfer a report to another instance

1. Source instance Step 5 → **Export** (one `.mirofish.zip`, graph included).
2. Viewer: configure `.env` (LLM + `NEO4J_PASSWORD`), then `docker compose up -d`.
3. Home → **Import report** → select the zip.
4. Step 5 → **Start world** (required for individual chat / survey).
5. Report Agent works after import; live agent tools work after the world is running.
```

Mirror in `README-ZH.md`.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml .env.example README.md README-ZH.md
git commit -m "docs: report transfer via Compose and single zip"
```

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| Single `.mirofish.zip` with graph inside | 1, 3 |
| Import remaps IDs into uploads | 1, 4 |
| Hydrate into local Neo4j | 2, 4 |
| Compose starts local Neo4j by default | 7 |
| Report Agent after import | 2, 4, 6 |
| Live individual chat + survey | 5, 6 |
| Export on Step 5 | 6 |
| Import on Home/history | 6 |
| Start world / env alive | 5, 6 |
| Docs for viewer path | 7 |
| No standalone static chat | (non-goal, omitted) |

## Consistency notes

- Graph snapshot shape = `GraphBuilderService.get_graph_data` output.
- New graph ids use `mirofish_` prefix.
- `resume_for_interview` must not re-run full rounds; `--interview-only` is the contract.
- Zep hydrate intentionally unimplemented; transfer assumes Graphiti default.
