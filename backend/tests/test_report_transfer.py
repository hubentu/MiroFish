import json
import zipfile
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
    (sim / "env_status.json").write_text('{"status":"alive"}', encoding="utf-8")
    (sim / "run_state.json").write_text(
        '{"runner_status":"running"}', encoding="utf-8"
    )
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
    assert not (work / "simulation" / "env_status.json").exists()
    assert not (work / "simulation" / "run_state.json").exists()
    with zipfile.ZipFile(zpath) as bundle:
        assert "simulation/env_status.json" not in bundle.namelist()
        assert "simulation/run_state.json" not in bundle.namelist()


def test_unpack_removes_legacy_live_state(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    proj, sim, report, graph = _write_tree(src)
    zpath = tmp_path / "legacy.zip"
    pack_transfer_zip(
        project_dir=proj,
        simulation_dir=sim,
        report_dir=report,
        graph_data=graph,
        dest_zip=zpath,
    )
    with zipfile.ZipFile(zpath, "a") as bundle:
        bundle.writestr("simulation/env_status.json", '{"status":"alive"}')
        bundle.writestr("simulation/run_state.json", '{"runner_status":"running"}')

    result = unpack_and_validate(zpath, tmp_path / "work")

    assert not (result["simulation_dir"] / "env_status.json").exists()
    assert not (result["simulation_dir"] / "run_state.json").exists()


def test_reject_zip_slip_member(tmp_path: Path):
    zpath = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(zpath, "w") as bundle:
        bundle.writestr("../escaped.txt", "nope")

    with pytest.raises(ValueError, match="unsafe zip member"):
        unpack_and_validate(zpath, tmp_path / "work")
    assert not (tmp_path / "escaped.txt").exists()


def test_reject_empty_graph_nodes(tmp_path: Path):
    zpath = tmp_path / "bad.zip"
    import zipfile

    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format_version": FORMAT_VERSION}))
        zf.writestr("report/full_report.md", "# x")
        zf.writestr("report/meta.json", "{}")
        zf.writestr("graph/graph.json", json.dumps({"nodes": [], "edges": []}))
    with pytest.raises(ValueError, match="nodes"):
        unpack_and_validate(zpath, tmp_path / "w")


def test_reject_unsupported_format_version(tmp_path: Path):
    zpath = tmp_path / "bad.zip"
    import zipfile

    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format_version": 999}))
        zf.writestr("report/full_report.md", "# x")
        zf.writestr("report/meta.json", "{}")
        zf.writestr(
            "graph/graph.json",
            json.dumps({"nodes": [{"uuid": "n1"}], "edges": []}),
        )
    with pytest.raises(ValueError, match="format_version"):
        unpack_and_validate(zpath, tmp_path / "w")


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
