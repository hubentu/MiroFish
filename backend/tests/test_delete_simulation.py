"""Tests for DELETE /api/simulation/<id> history cleanup."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from app import create_app
from app.models.project import ProjectManager
from app.services.report_agent import ReportManager
from app.services.simulation_manager import SimulationManager


def _plant_sim(tmp_path: Path, sim_id: str, report_id: str) -> None:
    sim_dir = tmp_path / "simulations" / sim_id
    sim_dir.mkdir(parents=True)
    (sim_dir / "state.json").write_text(
        json.dumps({
            "simulation_id": sim_id,
            "project_id": "proj_x",
            "graph_id": "mirofish_x",
            "status": "completed",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
            "config_generated": True,
            "profiles_generated": True,
            "entity_types": [],
            "twitter_enabled": False,
            "reddit_enabled": True,
            "error": None,
            "current_round": 0,
            "total_rounds": 0,
        }),
        encoding="utf-8",
    )
    report_dir = tmp_path / "reports" / report_id
    report_dir.mkdir(parents=True)
    (report_dir / "meta.json").write_text(
        json.dumps({
            "report_id": report_id,
            "simulation_id": sim_id,
            "graph_id": "mirofish_x",
            "simulation_requirement": "x",
            "status": "completed",
            "outline": None,
            "markdown_content": "# hi",
            "created_at": "2026-01-01T00:00:00",
            "completed_at": "2026-01-01T00:00:00",
            "error": None,
        }),
        encoding="utf-8",
    )
    (report_dir / "full_report.md").write_text("# hi", encoding="utf-8")


def test_delete_simulation_removes_sim_and_reports(tmp_path, monkeypatch):
    _plant_sim(tmp_path, "sim_delme", "report_delme")
    monkeypatch.setattr(SimulationManager, "SIMULATION_DATA_DIR", str(tmp_path / "simulations"))
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path / "projects"))

    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    with patch(
        "app.services.simulation_runner.SimulationRunner.check_env_alive",
        return_value=False,
    ):
        res = client.delete("/api/simulation/sim_delme")

    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True
    assert body["data"]["simulation_id"] == "sim_delme"
    assert "report_delme" in body["data"]["deleted_reports"]
    assert not (tmp_path / "simulations" / "sim_delme").exists()
    assert not (tmp_path / "reports" / "report_delme").exists()


def test_delete_simulation_missing_404(tmp_path, monkeypatch):
    monkeypatch.setattr(SimulationManager, "SIMULATION_DATA_DIR", str(tmp_path / "simulations"))
    (tmp_path / "simulations").mkdir(parents=True)
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()
    res = client.delete("/api/simulation/sim_missing")
    assert res.status_code == 404
