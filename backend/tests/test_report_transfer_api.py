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
