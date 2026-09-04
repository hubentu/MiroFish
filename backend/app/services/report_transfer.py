"""Pack/unpack .mirofish.zip report transfer bundles."""

from __future__ import annotations

import json
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FORMAT_VERSION = 1

_SIM_FILES = (
    "state.json",
    "simulation_config.json",
    "env_status.json",
    "reddit_profiles.json",
    "twitter_profiles.csv",
    "reddit_simulation.db",
    "twitter_simulation.db",
    "run_state.json",
)
_REPORT_FILES = ("meta.json", "outline.json", "full_report.md")


def mint_ids() -> dict[str, str]:
    return {
        "project_id": f"proj_{uuid.uuid4().hex[:12]}",
        "simulation_id": f"sim_{uuid.uuid4().hex[:12]}",
        "report_id": f"report_{uuid.uuid4().hex[:12]}",
        "graph_id": f"mirofish_{uuid.uuid4().hex[:16]}",
    }


def detect_live_world_capability(simulation_dir: Path) -> bool:
    if not (simulation_dir / "simulation_config.json").is_file():
        return False
    reddit = (
        (simulation_dir / "reddit_profiles.json").is_file()
        and (simulation_dir / "reddit_simulation.db").is_file()
    )
    twitter = (
        (simulation_dir / "twitter_profiles.csv").is_file()
        and (simulation_dir / "twitter_simulation.db").is_file()
    )
    return reddit or twitter


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_manifest(
    *,
    project_data: dict[str, Any],
    sim_state: dict[str, Any],
    report_meta: dict[str, Any],
    graph_data: dict[str, Any],
    live_world: bool,
    simulation_dir: Path,
) -> dict[str, Any]:
    platforms: list[str] = []
    if (simulation_dir / "reddit_simulation.db").is_file():
        platforms.append("reddit")
    if (simulation_dir / "twitter_simulation.db").is_file():
        platforms.append("twitter")
    title = project_data.get("name") or report_meta.get("title") or ""
    return {
        "format_version": FORMAT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "title": title,
        "simulation_requirement": project_data.get("simulation_requirement")
        or report_meta.get("simulation_requirement")
        or "",
        "source": {
            "project_id": project_data.get("project_id", ""),
            "simulation_id": sim_state.get("simulation_id", ""),
            "report_id": report_meta.get("report_id", ""),
            "graph_id": graph_data.get("graph_id")
            or project_data.get("graph_id")
            or sim_state.get("graph_id")
            or report_meta.get("graph_id")
            or "",
        },
        "platforms": platforms,
        "capabilities": {
            "report_agent": True,
            "live_world": live_world,
        },
    }


def pack_transfer_zip(
    *,
    project_dir: Path,
    simulation_dir: Path,
    report_dir: Path,
    graph_data: dict[str, Any],
    dest_zip: Path,
) -> Path:
    project_data = _read_json(project_dir / "project.json")
    sim_state = _read_json(simulation_dir / "state.json")
    report_meta = _read_json(report_dir / "meta.json")
    live_world = detect_live_world_capability(simulation_dir)
    manifest = build_manifest(
        project_data=project_data,
        sim_state=sim_state,
        report_meta=report_meta,
        graph_data=graph_data,
        live_world=live_world,
        simulation_dir=simulation_dir,
    )

    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

        zf.write(project_dir / "project.json", "project/project.json")

        for name in _SIM_FILES:
            src = simulation_dir / name
            if src.is_file():
                zf.write(src, f"simulation/{name}")

        for name in _REPORT_FILES:
            src = report_dir / name
            if src.is_file():
                zf.write(src, f"report/{name}")
        for section in sorted(report_dir.glob("section_*.md")):
            zf.write(section, f"report/{section.name}")

        zf.writestr(
            "graph/graph.json",
            json.dumps(graph_data, ensure_ascii=False, indent=2),
        )

    return dest_zip


def _safe_extract(zf: zipfile.ZipFile, dest_dir: Path) -> None:
    dest_root = dest_dir.resolve()
    for info in zf.infolist():
        member = Path(info.filename)
        if member.is_absolute() or ".." in member.parts:
            raise ValueError(f"unsafe zip member path: {info.filename}")
        target = (dest_root / member).resolve()
        if not target.is_relative_to(dest_root):
            raise ValueError(f"unsafe zip member path: {info.filename}")
        zf.extract(info, dest_root)


def _validate_graph(work_dir: Path) -> None:
    graph_path = work_dir / "graph" / "graph.json"
    if not graph_path.is_file():
        raise ValueError("missing required graph/graph.json")
    try:
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("invalid graph/graph.json") from exc
    if not isinstance(graph, dict) or "nodes" not in graph:
        raise ValueError("graph/graph.json must include nodes")
    if not graph["nodes"]:
        raise ValueError("graph/graph.json nodes must not be empty")


def unpack_and_validate(zip_path: Path, work_dir: Path) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        _safe_extract(zf, work_dir)

    manifest_path = work_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("missing manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != FORMAT_VERSION:
        raise ValueError(f"unsupported format_version: {manifest.get('format_version')}")

    for rel in ("report/full_report.md", "report/meta.json"):
        if not (work_dir / rel).is_file():
            raise ValueError(f"missing required {rel}")

    _validate_graph(work_dir)

    return {
        "manifest": manifest,
        "project_dir": work_dir / "project",
        "simulation_dir": work_dir / "simulation",
        "report_dir": work_dir / "report",
        "graph_path": work_dir / "graph" / "graph.json",
    }


def remap_ids(
    manifest: dict[str, Any],
    project_data: dict[str, Any],
    sim_state: dict[str, Any],
    report_meta: dict[str, Any],
    new_ids: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    del manifest  # reserved for future cross-ref remapping
    project = dict(project_data)
    state = dict(sim_state)
    meta = dict(report_meta)
    project["project_id"] = new_ids["project_id"]
    project["graph_id"] = new_ids["graph_id"]
    state["simulation_id"] = new_ids["simulation_id"]
    state["project_id"] = new_ids["project_id"]
    state["graph_id"] = new_ids["graph_id"]
    meta["report_id"] = new_ids["report_id"]
    meta["simulation_id"] = new_ids["simulation_id"]
    meta["graph_id"] = new_ids["graph_id"]
    return project, state, meta
