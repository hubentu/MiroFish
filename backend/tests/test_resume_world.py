import asyncio
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

from app.api import simulation as simulation_api
from app.services.simulation_runner import RunnerStatus, SimulationRunner


@pytest.fixture(autouse=True)
def clear_runner_state():
    def clear():
        for handle in SimulationRunner._stdout_files.values():
            if handle is not None:
                handle.close()
        SimulationRunner._run_states.clear()
        SimulationRunner._processes.clear()
        SimulationRunner._action_queues.clear()
        SimulationRunner._monitor_threads.clear()
        SimulationRunner._stdout_files.clear()
        SimulationRunner._stderr_files.clear()
        SimulationRunner._graph_memory_enabled.clear()

    clear()
    yield
    clear()


def test_resume_for_interview_refuses_when_ipc_env_alive(tmp_path, monkeypatch):
    simulation_id = "sim_imported"
    sim_dir = tmp_path / simulation_id
    sim_dir.mkdir()
    (sim_dir / "simulation_config.json").write_text(
        json.dumps({"time_config": {}}), encoding="utf-8"
    )
    (sim_dir / "reddit_profiles.json").write_text("[]", encoding="utf-8")
    (sim_dir / "reddit_simulation.db").write_bytes(b"existing-db")

    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))

    spawned = {}

    def popen(cmd, **kwargs):
        spawned["cmd"] = cmd
        raise AssertionError("must not spawn when IPC env is alive")

    monkeypatch.setattr("app.services.simulation_runner.subprocess.Popen", popen)

    with patch.object(SimulationRunner, "check_env_alive", return_value=True):
        with pytest.raises(ValueError, match="已在运行"):
            SimulationRunner.resume_for_interview(simulation_id)

    assert "cmd" not in spawned


def test_resume_for_interview_requires_config(tmp_path, monkeypatch):
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))

    with pytest.raises(ValueError, match="配置|config|不存在"):
        SimulationRunner.resume_for_interview("sim_missing")


def test_resume_for_interview_spawns_interview_only_for_available_platform(
    tmp_path, monkeypatch
):
    simulation_id = "sim_imported"
    sim_dir = tmp_path / simulation_id
    sim_dir.mkdir()
    (sim_dir / "simulation_config.json").write_text(
        json.dumps({"time_config": {}}), encoding="utf-8"
    )
    (sim_dir / "reddit_profiles.json").write_text("[]", encoding="utf-8")
    (sim_dir / "reddit_simulation.db").write_bytes(b"existing-db")

    script = tmp_path / "scripts" / "run_parallel_simulation.py"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(SimulationRunner, "SCRIPTS_DIR", str(script.parent))
    monkeypatch.setattr(
        SimulationRunner,
        "_sync_simulation_status",
        classmethod(lambda cls, *args, **kwargs: None),
    )
    monkeypatch.setattr(
        SimulationRunner,
        "_monitor_simulation",
        classmethod(lambda cls, *args, **kwargs: None),
    )

    spawned = {}

    class Process:
        pid = 123

        def poll(self):
            return None

    class Thread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    def popen(cmd, **kwargs):
        spawned["cmd"] = cmd
        return Process()

    monkeypatch.setattr("app.services.simulation_runner.subprocess.Popen", popen)
    monkeypatch.setattr("app.services.simulation_runner.threading.Thread", Thread)

    state = SimulationRunner.resume_for_interview(simulation_id)

    assert state.runner_status == RunnerStatus.RUNNING
    assert "--interview-only" in spawned["cmd"]
    assert "--reddit-only" in spawned["cmd"]
    assert "--twitter-only" not in spawned["cmd"]
    assert SimulationRunner._graph_memory_enabled[simulation_id] is False


def test_interview_only_opens_existing_db_without_running_rounds(
    tmp_path, monkeypatch
):
    script_path = (
        Path(__file__).parents[1] / "scripts" / "run_parallel_simulation.py"
    )
    spec = importlib.util.spec_from_file_location("parallel_runner_test", script_path)
    runner = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(runner)

    db_path = tmp_path / "twitter_simulation.db"
    db_path.write_bytes(b"existing-world")
    (tmp_path / "twitter_profiles.csv").write_text("user_id\n", encoding="utf-8")

    class Graph:
        def get_agents(self):
            return []

    class Env:
        def __init__(self):
            self.reset_called = False
            self.step_calls = 0

        async def reset(self):
            self.reset_called = True

        async def step(self, actions):
            self.step_calls += 1

    env = Env()
    monkeypatch.setattr(runner, "create_model", lambda *args, **kwargs: object())

    async def graph(*args, **kwargs):
        return Graph()

    monkeypatch.setattr(runner, "generate_twitter_agent_graph", graph)
    monkeypatch.setattr(runner.oasis, "make", lambda **kwargs: env)

    result = asyncio.run(
        runner.run_twitter_simulation(
            {"event_config": {"initial_posts": [{"content": "must not run"}]}},
            str(tmp_path),
            interview_only=True,
        )
    )

    assert db_path.read_bytes() == b"existing-world"
    assert result.env is env
    assert env.reset_called is True
    assert env.step_calls == 0


def test_resume_world_api_validates_and_returns_state(monkeypatch):
    app = Flask(__name__)
    with app.test_request_context("/resume-world", method="POST", json={}):
        response, status = simulation_api.resume_world()
        assert status == 400
        assert response.get_json()["success"] is False

    state = type("State", (), {"to_dict": lambda self: {"runner_status": "running"}})()
    with patch.object(SimulationRunner, "resume_for_interview", return_value=state):
        with app.test_request_context(
            "/resume-world", method="POST", json={"simulation_id": "sim_imported"}
        ):
            response = simulation_api.resume_world()
            assert response.get_json() == {
                "success": True,
                "data": {"runner_status": "running"},
            }
