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
