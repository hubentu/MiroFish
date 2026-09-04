import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app import create_app
from app.api import report as report_api
from app.config import Config
from app.models.project import ProjectManager
from app.services.report_agent import ReportManager
from app.services.memory.fake_backend import FakeKnowledgeGraphBackend
from app.services.report_transfer import pack_transfer_zip
from app.services.simulation_manager import SimulationManager
from app.services.simulation_runner import SimulationRunner


@pytest.fixture
def client(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    projects = uploads / "projects"
    simulations = uploads / "simulations"
    reports = uploads / "reports"
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(uploads))
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(projects))
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(reports))
    monkeypatch.setattr(SimulationManager, "SIMULATION_DATA_DIR", str(simulations))
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(simulations))

    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def _write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _plant_export_tree(tmp_path: Path):
    uploads = tmp_path / "uploads"
    project_dir = uploads / "projects" / "proj_export"
    simulation_dir = uploads / "simulations" / "sim_export"
    report_dir = uploads / "reports" / "report_export"

    _write_json(
        project_dir / "project.json",
        {
            "project_id": "proj_export",
            "name": "Export me",
            "status": "graph_completed",
            "graph_id": "mirofish_export",
            "simulation_requirement": "Predict export behavior.",
        },
    )
    _write_json(
        simulation_dir / "state.json",
        {
            "simulation_id": "sim_export",
            "project_id": "proj_export",
            "graph_id": "mirofish_export",
            "status": "completed",
        },
    )
    _write_json(
        report_dir / "meta.json",
        {
            "report_id": "report_export",
            "simulation_id": "sim_export",
            "graph_id": "mirofish_export",
            "simulation_requirement": "Predict export behavior.",
            "status": "completed",
        },
    )
    (report_dir / "full_report.md").write_text("# Export\n\nBody", encoding="utf-8")


def test_export_requires_report_id(client):
    response = client.post("/api/report/export", json={})

    assert response.status_code == 400
    assert response.json["success"] is False


def test_export_missing_report_returns_404(client):
    response = client.post(
        "/api/report/export", json={"report_id": "report_missing"}
    )

    assert response.status_code == 404
    assert response.json["success"] is False


def test_export_downloads_bundle_with_graph(client, tmp_path, monkeypatch):
    _plant_export_tree(tmp_path)
    graph_data = {
        "graph_id": "mirofish_export",
        "nodes": [{"uuid": "node-1", "name": "Node"}],
        "edges": [],
    }
    builder = MagicMock()
    builder.get_graph_data.return_value = graph_data
    monkeypatch.setattr(
        report_api,
        "GraphBuilderService",
        lambda: builder,
        raising=False,
    )

    response = client.post(
        "/api/report/export",
        json={"report_id": "report_export"},
    )

    assert response.status_code == 200
    builder.get_graph_data.assert_called_once_with("mirofish_export")
    assert response.mimetype == "application/zip"
    assert (
        "mirofish_report_export.mirofish.zip"
        in response.headers["Content-Disposition"]
    )
    with zipfile.ZipFile(io.BytesIO(response.data)) as bundle:
        assert "graph/graph.json" in bundle.namelist()
        assert json.loads(bundle.read("graph/graph.json")) == graph_data


def test_export_graph_load_exception_returns_500(client, tmp_path, monkeypatch):
    _plant_export_tree(tmp_path)
    builder = MagicMock()
    builder.get_graph_data.side_effect = RuntimeError("graph offline")
    monkeypatch.setattr(
        report_api, "GraphBuilderService", lambda: builder, raising=False
    )

    response = client.post(
        "/api/report/export", json={"report_id": "report_export"}
    )

    assert response.status_code == 500
    assert response.json["success"] is False


def test_export_rejects_empty_graph(client, tmp_path, monkeypatch):
    _plant_export_tree(tmp_path)
    builder = type(
        "EmptyGraphBuilder",
        (),
        {
            "get_graph_data": lambda self, graph_id: {
                "graph_id": graph_id,
                "nodes": [],
                "edges": [],
            }
        },
    )
    monkeypatch.setattr(report_api, "GraphBuilderService", builder, raising=False)

    response = client.post(
        "/api/report/export",
        json={"report_id": "report_export"},
    )

    assert response.status_code == 400
    assert "nodes" in response.json["error"]


def _build_transfer_bundle(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    project_dir = source / "project"
    simulation_dir = source / "simulation"
    report_dir = source / "report"
    _write_json(
        project_dir / "project.json",
        {
            "project_id": "proj_source",
            "name": "Imported project",
            "status": "created",
            "graph_id": "mirofish_source",
        },
    )
    _write_json(
        simulation_dir / "state.json",
        {
            "simulation_id": "sim_source",
            "project_id": "proj_source",
            "graph_id": "mirofish_source",
            "status": "created",
        },
    )
    _write_json(simulation_dir / "simulation_config.json", {"max_rounds": 1})
    _write_json(
        report_dir / "meta.json",
        {
            "report_id": "report_source",
            "simulation_id": "sim_source",
            "graph_id": "mirofish_source",
            "simulation_requirement": "Predict import behavior.",
            "status": "pending",
        },
    )
    (report_dir / "full_report.md").write_text("# Imported", encoding="utf-8")
    bundle = tmp_path / "transfer.mirofish.zip"
    pack_transfer_zip(
        project_dir=project_dir,
        simulation_dir=simulation_dir,
        report_dir=report_dir,
        graph_data={
            "graph_id": "mirofish_source",
            "nodes": [{"uuid": "node-1", "name": "Imported node"}],
            "edges": [],
        },
        dest_zip=bundle,
    )
    return bundle


def test_import_rejects_non_zip(client):
    response = client.post(
        "/api/report/import",
        data={"file": (io.BytesIO(b"not-a-zip"), "x.txt")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.json["success"] is False


def test_import_writes_uploads_and_hydrates(client, tmp_path, monkeypatch):
    bundle = _build_transfer_bundle(tmp_path)
    backend = FakeKnowledgeGraphBackend()
    monkeypatch.setattr(report_api, "get_memory_backend", lambda: backend, raising=False)

    with bundle.open("rb") as upload:
        response = client.post(
            "/api/report/import",
            data={"file": (upload, bundle.name)},
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    data = response.json["data"]
    uploads = tmp_path / "uploads"
    project = json.loads(
        (uploads / "projects" / data["project_id"] / "project.json").read_text()
    )
    state = json.loads(
        (uploads / "simulations" / data["simulation_id"] / "state.json").read_text()
    )
    report = json.loads(
        (uploads / "reports" / data["report_id"] / "meta.json").read_text()
    )
    assert project["status"] == "graph_completed"
    assert state["status"] == "completed"
    assert report["status"] == "completed"
    assert project["graph_id"] == state["graph_id"] == report["graph_id"]
    assert project["graph_id"] == data["graph_id"]
    assert SimulationManager().get_simulation(data["simulation_id"]).status.value == "completed"
    assert backend.list_nodes(data["graph_id"])[0].uuid == "node-1"
    assert (uploads / "exports" / f'{data["graph_id"]}.json').is_file()
    assert data["capabilities"] == {"report_agent": True, "live_world": False}
    assert report["capabilities"] == {"report_agent": True, "live_world": False}
    assert SimulationRunner.check_env_alive(data["simulation_id"]) is False
    assert not (
        uploads / "simulations" / data["simulation_id"] / "run_state.json"
    ).exists()


def test_import_rolls_back_uploads_when_hydrate_fails(
    client, tmp_path, monkeypatch
):
    bundle = _build_transfer_bundle(tmp_path)
    backend = MagicMock()
    backend.hydrate_graph_snapshot.side_effect = OSError("hydrate failed")
    monkeypatch.setattr(
        report_api, "get_memory_backend", lambda: backend, raising=False
    )

    with bundle.open("rb") as upload:
        response = client.post(
            "/api/report/import",
            data={"file": (upload, bundle.name)},
            content_type="multipart/form-data",
        )

    assert response.status_code == 500
    graph_id = backend.hydrate_graph_snapshot.call_args.args[0]
    backend.delete_graph.assert_called_once_with(graph_id)
    uploads = tmp_path / "uploads"
    for name in ("projects", "simulations", "reports", "exports"):
        assert not list((uploads / name).glob("*"))


def test_import_copy_failure_never_exposes_partial_uploads(
    client, tmp_path, monkeypatch
):
    bundle = _build_transfer_bundle(tmp_path)
    backend = MagicMock()
    real_copytree = report_api.shutil.copytree
    copies = 0

    def fail_second_copy(source, destination):
        nonlocal copies
        copies += 1
        if copies == 2:
            raise OSError("copy failed")
        return real_copytree(source, destination)

    monkeypatch.setattr(report_api.shutil, "copytree", fail_second_copy)
    monkeypatch.setattr(
        report_api, "get_memory_backend", lambda: backend, raising=False
    )

    with bundle.open("rb") as upload:
        response = client.post(
            "/api/report/import",
            data={"file": (upload, bundle.name)},
            content_type="multipart/form-data",
        )

    assert response.status_code == 400
    backend.hydrate_graph_snapshot.assert_not_called()
    uploads = tmp_path / "uploads"
    for name in ("projects", "simulations", "reports", "exports"):
        assert not list((uploads / name).glob("*"))
