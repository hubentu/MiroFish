import json
from pathlib import Path
from unittest.mock import MagicMock

from app.services.simulation_runner import SimulationRunner


def test_check_env_alive_false_when_process_gone_clears_stale_status(tmp_path, monkeypatch):
    sim_id = "sim_stale"
    sim_dir = tmp_path / sim_id
    sim_dir.mkdir()
    status_file = sim_dir / "env_status.json"
    status_file.write_text(
        json.dumps({"status": "alive", "timestamp": "2026-01-01T00:00:00"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    SimulationRunner._processes.pop(sim_id, None)

    assert SimulationRunner.check_env_alive(sim_id) is False
    assert json.loads(status_file.read_text(encoding="utf-8"))["status"] == "stopped"


def test_check_env_alive_true_when_process_and_status_alive(tmp_path, monkeypatch):
    sim_id = "sim_live"
    sim_dir = tmp_path / sim_id
    sim_dir.mkdir()
    (sim_dir / "env_status.json").write_text(
        json.dumps({"status": "alive"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    process = MagicMock()
    process.poll.return_value = None
    SimulationRunner._processes[sim_id] = process
    try:
        assert SimulationRunner.check_env_alive(sim_id) is True
    finally:
        SimulationRunner._processes.pop(sim_id, None)
