#!/usr/bin/env python3
"""Export MiroFish graph + report into a single offline-viewable HTML file.

Usage:
  python scripts/export_html.py --report-id report_7d14aa8c30e4
  python scripts/export_html.py --report-id report_7d14aa8c30e4 --from-disk
  python scripts/export_html.py --report-id report_xxx --graph-id mirofish_yyy -o out.html
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPLOADS = ROOT / "backend" / "uploads"
DEFAULT_BASE = "http://127.0.0.1:5001"


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if isinstance(payload, dict) and "data" in payload and "success" in payload:
        if not payload.get("success"):
            raise RuntimeError(payload.get("error") or f"API failed: {url}")
        return payload["data"]
    return payload


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_report(report_id: str, base: str, from_disk: bool) -> tuple[dict, str]:
    disk_dir = UPLOADS / "reports" / report_id
    if from_disk or not _api_up(base):
        meta_path = disk_dir / "meta.json"
        md_path = disk_dir / "full_report.md"
        if not meta_path.exists():
            raise FileNotFoundError(f"Report not found on disk: {disk_dir}")
        meta = json.loads(_read_text(meta_path))
        md = _read_text(md_path) if md_path.exists() else (meta.get("markdown_content") or "")
        return meta, md

    report = _get_json(f"{base}/api/report/{report_id}")
    md = report.get("markdown_content") or ""
    if not md and (disk_dir / "full_report.md").exists():
        md = _read_text(disk_dir / "full_report.md")
    return report, md


def load_graph(graph_id: str, base: str, from_disk: bool) -> dict:
    # Graph JSON is not persisted locally; API is required unless a cache file is passed.
    if from_disk:
        cache = UPLOADS / "exports" / f"{graph_id}.json"
        if cache.exists():
            return json.loads(_read_text(cache))
        # Fall through to API — disk mode still needs live graph fetch once.
    return _get_json(f"{base}/api/graph/data/{graph_id}")


def _api_up(base: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base}/api/simulation/history?limit=1", timeout=3):
            return True
    except Exception:
        return False


def md_to_html(md: str) -> str:
    """Tiny markdown subset → HTML. Good enough for MiroFish reports."""
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    in_ul = False
    in_ol = False
    in_blockquote = False

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    def close_bq():
        nonlocal in_blockquote
        if in_blockquote:
            out.append("</blockquote>")
            in_blockquote = False

    def inline(text: str) -> str:
        text = html.escape(text)
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
        text = re.sub(r"\[(.+?)\]\((https?://[^)]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)
        return text

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped == "---":
            close_lists()
            close_bq()
            out.append("<hr>")
            i += 1
            continue

        if stripped.startswith(">"):
            close_lists()
            if not in_blockquote:
                out.append("<blockquote>")
                in_blockquote = True
            out.append(f"<p>{inline(stripped.lstrip('> ').strip())}</p>")
            i += 1
            continue
        else:
            close_bq()

        heading = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if heading:
            close_lists()
            level = len(heading.group(1))
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            i += 1
            continue

        if re.match(r"^[-*]\s+", stripped):
            close_bq()
            if in_ol:
                out.append("</ol>")
                in_ol = False
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{inline(re.sub(r'^[-*]\\s+', '', stripped))}</li>")
            i += 1
            continue

        if re.match(r"^\d+\.\s+", stripped):
            close_bq()
            if in_ul:
                out.append("</ul>")
                in_ul = False
            if not in_ol:
                out.append("<ol>")
                in_ol = True
            out.append(f"<li>{inline(re.sub(r'^\\d+\\.\\s+', '', stripped))}</li>")
            i += 1
            continue

        close_lists()
        if not stripped:
            i += 1
            continue

        # Merge consecutive paragraph lines.
        para = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if not nxt or nxt.startswith("#") or nxt.startswith(">") or nxt == "---" or re.match(r"^[-*]\s+", nxt) or re.match(r"^\d+\.\s+", nxt):
                break
            para.append(nxt)
            i += 1
        out.append(f"<p>{inline(' '.join(para))}</p>")

    close_lists()
    close_bq()
    return "\n".join(out)


def slim_graph(graph: dict) -> dict:
    """Keep fields the HTML viewer needs; drop bulky episode arrays."""
    nodes = []
    for n in graph.get("nodes") or []:
        nodes.append({
            "id": n.get("uuid") or n.get("id") or n.get("name"),
            "name": n.get("name") or "?",
            "summary": n.get("summary") or "",
            "labels": n.get("labels") or [],
            "attributes": n.get("attributes") or {},
        })
    edges = []
    for e in graph.get("edges") or []:
        edges.append({
            "id": e.get("uuid") or e.get("id"),
            "source": e.get("source_node_uuid") or e.get("source"),
            "target": e.get("target_node_uuid") or e.get("target"),
            "source_name": e.get("source_node_name") or "",
            "target_name": e.get("target_node_name") or "",
            "type": e.get("name") or e.get("fact_type") or e.get("attributes", {}).get("edge_type") or "RELATED",
            "fact": e.get("fact") or e.get("attributes", {}).get("fact") or "",
        })
    return {
        "graph_id": graph.get("graph_id"),
        "node_count": graph.get("node_count") or len(nodes),
        "edge_count": graph.get("edge_count") or len(edges),
        "nodes": nodes,
        "edges": edges,
    }


def build_html(report: dict, markdown: str, graph: dict) -> str:
    title = (
        (report.get("outline") or {}).get("title")
        or report.get("report_id")
        or "MiroFish Export"
    )
    report_html = md_to_html(markdown)
    graph_json = json.dumps(slim_graph(graph), ensure_ascii=False)
    meta = {
        "report_id": report.get("report_id"),
        "simulation_id": report.get("simulation_id"),
        "graph_id": report.get("graph_id") or graph.get("graph_id"),
        "requirement": report.get("simulation_requirement") or "",
        "created_at": report.get("created_at"),
        "completed_at": report.get("completed_at"),
        "status": report.get("status"),
    }
    meta_json = json.dumps(meta, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{html.escape(title)}</title>
<style>
  :root {{
    --bg: #f6f4ef;
    --ink: #1c1b19;
    --muted: #5c574f;
    --line: #d9d2c5;
    --panel: #fffdf8;
    --accent: #0f6b5c;
    --accent-soft: #d8efe9;
    --warn: #8a4b12;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: "IBM Plex Sans", "Noto Sans SC", "Segoe UI", sans-serif;
    color: var(--ink);
    background:
      radial-gradient(1200px 500px at 10% -10%, #e7f3ef 0%, transparent 55%),
      radial-gradient(900px 400px at 100% 0%, #f3ebe0 0%, transparent 50%),
      var(--bg);
  }}
  header {{
    position: sticky; top: 0; z-index: 5;
    display: flex; gap: 16px; align-items: center; justify-content: space-between;
    padding: 14px 22px;
    background: rgba(255,253,248,0.92);
    border-bottom: 1px solid var(--line);
    backdrop-filter: blur(8px);
  }}
  .brand {{
    font-family: "IBM Plex Mono", "JetBrains Mono", monospace;
    font-weight: 700; letter-spacing: 0.04em; font-size: 14px;
  }}
  .tabs {{ display: flex; gap: 8px; }}
  .tab {{
    border: 1px solid var(--line); background: transparent; color: var(--ink);
    padding: 8px 14px; border-radius: 999px; cursor: pointer; font: inherit;
  }}
  .tab.active {{ background: var(--accent); border-color: var(--accent); color: white; }}
  .meta {{
    color: var(--muted); font-size: 12px; font-family: "IBM Plex Mono", monospace;
  }}
  main {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
  .panel {{ display: none; }}
  .panel.active {{ display: block; }}
  .card {{
    background: var(--panel); border: 1px solid var(--line); border-radius: 18px;
    padding: 28px 32px; box-shadow: 0 10px 40px rgba(28,27,25,0.04);
  }}
  .report h1 {{ font-size: 2rem; line-height: 1.25; margin: 0 0 12px; }}
  .report h2 {{ font-size: 1.35rem; margin: 2rem 0 0.8rem; }}
  .report h3 {{ font-size: 1.1rem; margin: 1.4rem 0 0.6rem; }}
  .report p {{ line-height: 1.75; color: #2a2722; }}
  .report blockquote {{
    margin: 1rem 0; padding: 10px 16px; border-left: 3px solid var(--accent);
    background: var(--accent-soft); color: #204842;
  }}
  .report hr {{ border: 0; border-top: 1px solid var(--line); margin: 1.5rem 0; }}
  .report code {{
    font-family: "IBM Plex Mono", monospace; font-size: 0.9em;
    background: #f0ebe3; padding: 0.1em 0.35em; border-radius: 4px;
  }}
  .graph-tools {{
    display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; align-items: center;
  }}
  .graph-tools input, .graph-tools select, .graph-tools button {{
    font: inherit; padding: 8px 12px; border-radius: 10px; border: 1px solid var(--line);
    background: white;
  }}
  .graph-tools button {{ cursor: pointer; background: var(--accent); color: white; border-color: var(--accent); }}
  #graph-canvas {{
    width: 100%; height: min(72vh, 760px); border: 1px solid var(--line);
    border-radius: 14px; background: #fbfaf7; display: block; touch-action: none;
  }}
  .graph-layout {{
    display: grid; grid-template-columns: 1fr 320px; gap: 16px;
  }}
  @media (max-width: 900px) {{
    .graph-layout {{ grid-template-columns: 1fr; }}
  }}
  #node-detail {{
    border: 1px solid var(--line); border-radius: 14px; padding: 16px; background: white;
    min-height: 220px; overflow: auto;
  }}
  #node-detail h3 {{ margin: 0 0 8px; font-size: 1.05rem; }}
  #node-detail .muted {{ color: var(--muted); font-size: 12px; }}
  #node-detail .facts {{ margin-top: 12px; }}
  #node-detail .fact {{
    border-top: 1px solid var(--line); padding: 8px 0; font-size: 13px; line-height: 1.5;
  }}
  .stats {{ color: var(--muted); font-size: 13px; }}
  footer {{
    max-width: 1200px; margin: 0 auto; padding: 8px 24px 32px;
    color: var(--muted); font-size: 12px;
  }}
</style>
</head>
<body>
<header>
  <div>
    <div class="brand">MIROFISH EXPORT</div>
    <div class="meta" id="meta-line"></div>
  </div>
  <div class="tabs">
    <button class="tab active" data-tab="report">Report</button>
    <button class="tab" data-tab="graph">Graph</button>
  </div>
</header>
<main>
  <section id="panel-report" class="panel active">
    <article class="card report">{report_html}</article>
  </section>
  <section id="panel-graph" class="panel">
    <div class="card">
      <div class="graph-tools">
        <input id="search" type="search" placeholder="Search nodes / facts…" style="min-width:220px;flex:1" />
        <select id="edge-filter"><option value="">All edge types</option></select>
        <button type="button" id="zoom-out" title="Zoom out">−</button>
        <button type="button" id="zoom-in" title="Zoom in">+</button>
        <button type="button" id="reset-view">Fit all</button>
        <span class="stats" id="graph-stats"></span>
      </div>
      <div class="graph-layout">
        <canvas id="graph-canvas"></canvas>
        <aside id="node-detail">
          <h3>Select a node</h3>
          <div class="muted">Click a node to inspect summary and connected facts.</div>
        </aside>
      </div>
    </div>
  </section>
</main>
<footer>Generated by scripts/export_html.py · open this file in any browser · no server required</footer>
<script>
const META = {meta_json};
const GRAPH = {graph_json};

document.getElementById('meta-line').textContent =
  [META.report_id, META.graph_id, META.status].filter(Boolean).join(' · ');

const MIN_SCALE = 0.02;
const MAX_SCALE = 6;

document.querySelectorAll('.tab').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('panel-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'graph') {{
      resize();
      fitToView();
    }}
  }});
}});

const spread = Math.max(180, Math.sqrt(GRAPH.nodes.length) * 28);
const nodes = GRAPH.nodes.map((n, i) => ({{
  ...n,
  x: Math.cos(i * 2.399963) * spread * (0.35 + (i % 7) / 10),
  y: Math.sin(i * 2.399963) * spread * (0.35 + (i % 5) / 10),
  vx: 0, vy: 0,
}}));
const nodeById = Object.fromEntries(nodes.map(n => [n.id, n]));
const edges = GRAPH.edges.filter(e => nodeById[e.source] && nodeById[e.target]);
const edgeTypes = [...new Set(edges.map(e => e.type))].sort();
const edgeFilter = document.getElementById('edge-filter');
edgeTypes.forEach(t => {{
  const opt = document.createElement('option');
  opt.value = t; opt.textContent = t;
  edgeFilter.appendChild(opt);
}});
document.getElementById('graph-stats').textContent =
  `${{GRAPH.node_count}} nodes · ${{GRAPH.edge_count}} edges`;

const canvas = document.getElementById('graph-canvas');
const ctx = canvas.getContext('2d');
let W = 0, H = 0, dpr = 1;
let scale = 1, ox = 0, oy = 0;
let dragNode = null, panning = false, lastX = 0, lastY = 0;
let selected = null;
let query = '';
let typeFilter = '';
let autoFitPending = true;

function resize() {{
  const rect = canvas.getBoundingClientRect();
  dpr = window.devicePixelRatio || 1;
  W = rect.width; H = rect.height;
  canvas.width = Math.floor(W * dpr);
  canvas.height = Math.floor(H * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}}
window.addEventListener('resize', () => {{ resize(); fitToView(); }});

function matches(n) {{
  if (!query) return true;
  const q = query.toLowerCase();
  return (n.name || '').toLowerCase().includes(q)
    || (n.summary || '').toLowerCase().includes(q);
}}

function visibleEdges() {{
  return edges.filter(e => {{
    if (typeFilter && e.type !== typeFilter) return false;
    const a = nodeById[e.source], b = nodeById[e.target];
    return matches(a) && matches(b);
  }});
}}

function visibleNodes() {{
  const visE = visibleEdges();
  if (!query && !typeFilter) return nodes;
  const active = new Set();
  visE.forEach(e => {{ active.add(e.source); active.add(e.target); }});
  return nodes.filter(n => active.has(n.id) || matches(n));
}}

function tick() {{
  const visE = visibleEdges();
  const N = visibleNodes();
  const nCount = Math.max(N.length, 1);
  // Keep the layout compact enough that fit/zoom-out can show everything.
  const repulse = Math.max(400, 180000 / nCount);
  const linkLen = Math.max(40, 140 - Math.sqrt(nCount));
  const gravity = 0.004;

  for (let i = 0; i < N.length; i++) {{
    for (let j = i + 1; j < N.length; j++) {{
      const a = N[i], b = N[j];
      let dx = a.x - b.x, dy = a.y - b.y;
      let dist2 = dx*dx + dy*dy || 0.01;
      let f = repulse / dist2;
      let fx = dx * f, fy = dy * f;
      a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
    }}
  }}
  visE.forEach(e => {{
    const a = nodeById[e.source], b = nodeById[e.target];
    let dx = b.x - a.x, dy = b.y - a.y;
    let dist = Math.hypot(dx, dy) || 0.01;
    let f = (dist - linkLen) * 0.015;
    a.vx += dx / dist * f; a.vy += dy / dist * f;
    b.vx -= dx / dist * f; b.vy -= dy / dist * f;
  }});
  N.forEach(n => {{
    n.vx += -n.x * gravity; n.vy += -n.y * gravity;
    n.vx *= 0.82; n.vy *= 0.82;
    if (n !== dragNode) {{ n.x += n.vx; n.y += n.vy; }}
  }});
}}

function boundsOf(list) {{
  if (!list.length) return {{ minX: -100, maxX: 100, minY: -100, maxY: 100 }};
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  list.forEach(n => {{
    if (n.x < minX) minX = n.x;
    if (n.x > maxX) maxX = n.x;
    if (n.y < minY) minY = n.y;
    if (n.y > maxY) maxY = n.y;
  }});
  if (!Number.isFinite(minX)) return {{ minX: -100, maxX: 100, minY: -100, maxY: 100 }};
  // Avoid zero-size boxes.
  if (maxX - minX < 40) {{ minX -= 20; maxX += 20; }}
  if (maxY - minY < 40) {{ minY -= 20; maxY += 20; }}
  return {{ minX, maxX, minY, maxY }};
}}

function fitToView(padding = 40) {{
  if (!W || !H) resize();
  if (!W || !H) return;
  const b = boundsOf(visibleNodes());
  const bw = b.maxX - b.minX;
  const bh = b.maxY - b.minY;
  const sx = (W - padding * 2) / bw;
  const sy = (H - padding * 2) / bh;
  scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, Math.min(sx, sy)));
  const cx = (b.minX + b.maxX) / 2;
  const cy = (b.minY + b.maxY) / 2;
  ox = W / 2 - cx * scale;
  oy = H / 2 - cy * scale;
  autoFitPending = false;
}}

function zoomAt(mx, my, factor) {{
  const beforeX = (mx - ox) / scale, beforeY = (my - oy) / scale;
  scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale * factor));
  ox = mx - beforeX * scale;
  oy = my - beforeY * scale;
}}

function worldFromEvent(ev) {{
  const rect = canvas.getBoundingClientRect();
  const x = (ev.clientX - rect.left - ox) / scale;
  const y = (ev.clientY - rect.top - oy) / scale;
  return {{x, y}};
}}

function hitNode(x, y) {{
  let best = null, bestD = 14 / scale;
  nodes.forEach(n => {{
    if (query && !matches(n)) return;
    const d = Math.hypot(n.x - x, n.y - y);
    if (d < bestD) {{ best = n; bestD = d; }}
  }});
  return best;
}}

function showDetail(n) {{
  selected = n;
  const facts = edges.filter(e => e.source === n.id || e.target === n.id).slice(0, 40);
  const labels = (n.labels || []).join(', ') || 'Entity';
  document.getElementById('node-detail').innerHTML = `
    <h3>${{escapeHtml(n.name)}}</h3>
    <div class="muted">${{escapeHtml(labels)}} · ${{escapeHtml(n.id)}}</div>
    <p style="font-size:14px;line-height:1.55;margin:10px 0 0">${{escapeHtml(n.summary || 'No summary')}}</p>
    <div class="facts">
      ${{facts.map(f => `<div class="fact"><strong>${{escapeHtml(f.type)}}</strong><br>${{escapeHtml(f.fact || (f.source_name + ' → ' + f.target_name))}}</div>`).join('') || '<div class="muted">No connected facts</div>'}}
    </div>`;
}}

function escapeHtml(s) {{
  return String(s ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
}}

function draw() {{
  ctx.clearRect(0, 0, W, H);
  ctx.save();
  ctx.translate(ox, oy);
  ctx.scale(scale, scale);

  const visE = visibleEdges();
  ctx.lineWidth = 1 / Math.max(scale, 0.05);
  visE.forEach(e => {{
    const a = nodeById[e.source], b = nodeById[e.target];
    const hot = selected && (selected.id === a.id || selected.id === b.id);
    ctx.strokeStyle = hot ? '#0f6b5c' : 'rgba(92,87,79,0.22)';
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
  }});

  nodes.forEach(n => {{
    if (query && !matches(n) && !(selected && selected.id === n.id)) return;
    const r = (selected && selected.id === n.id ? 7 : 5) / Math.sqrt(Math.max(scale, 0.08));
    ctx.beginPath();
    ctx.fillStyle = selected && selected.id === n.id ? '#0f6b5c' : '#2a2722';
    ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
    ctx.fill();
    if (scale > 0.45 || (selected && selected.id === n.id) || (query && matches(n))) {{
      ctx.fillStyle = '#5c574f';
      ctx.font = `${{11 / Math.sqrt(Math.max(scale, 0.2))}}px "IBM Plex Sans", sans-serif`;
      ctx.fillText(n.name.slice(0, 28), n.x + 8 / Math.max(scale, 0.2), n.y + 3 / Math.max(scale, 0.2));
    }}
  }});
  ctx.restore();
}}

let warmTicks = 0;
function loop() {{
  // Warm up layout, then auto-fit once so the full graph is in view.
  const steps = warmTicks < 80 ? 8 : 2;
  for (let i = 0; i < steps; i++) tick();
  warmTicks++;
  if (autoFitPending && warmTicks >= 80) fitToView();
  draw();
  requestAnimationFrame(loop);
}}

canvas.addEventListener('pointerdown', ev => {{
  const w = worldFromEvent(ev);
  const n = hitNode(w.x, w.y);
  if (n) {{
    dragNode = n; showDetail(n);
  }} else {{
    panning = true; lastX = ev.clientX; lastY = ev.clientY;
  }}
  canvas.setPointerCapture(ev.pointerId);
}});
canvas.addEventListener('pointermove', ev => {{
  if (dragNode) {{
    const w = worldFromEvent(ev);
    dragNode.x = w.x; dragNode.y = w.y; dragNode.vx = 0; dragNode.vy = 0;
  }} else if (panning) {{
    ox += ev.clientX - lastX; oy += ev.clientY - lastY;
    lastX = ev.clientX; lastY = ev.clientY;
  }}
}});
canvas.addEventListener('pointerup', () => {{ dragNode = null; panning = false; }});
canvas.addEventListener('wheel', ev => {{
  ev.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
  zoomAt(mx, my, ev.deltaY < 0 ? 1.12 : 1 / 1.12);
}}, {{ passive: false }});

document.getElementById('search').addEventListener('input', e => {{
  query = e.target.value.trim();
  fitToView();
}});
edgeFilter.addEventListener('change', e => {{
  typeFilter = e.target.value;
  fitToView();
}});
document.getElementById('reset-view').addEventListener('click', () => {{
  query = ''; typeFilter = '';
  document.getElementById('search').value = ''; edgeFilter.value = '';
  fitToView();
}});
document.getElementById('zoom-in').addEventListener('click', () => zoomAt(W / 2, H / 2, 1.25));
document.getElementById('zoom-out').addEventListener('click', () => zoomAt(W / 2, H / 2, 1 / 1.25));

resize();
// Pre-settle offline so first paint already has a usable layout.
for (let i = 0; i < 120; i++) tick();
fitToView();
loop();

// ponytail: one assert-style check that export payload is sane
if (!GRAPH.nodes.length) console.warn('export check: graph has 0 nodes');
if (!META.report_id) console.warn('export check: missing report_id');
</script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Export MiroFish graph + report to HTML")
    p.add_argument("--report-id", required=True, help="e.g. report_7d14aa8c30e4")
    p.add_argument("--graph-id", default=None, help="defaults from report meta")
    p.add_argument("--base-url", default=DEFAULT_BASE)
    p.add_argument("--from-disk", action="store_true", help="prefer local uploads/ files")
    p.add_argument("-o", "--output", default=None, help="output HTML path")
    args = p.parse_args(argv)

    report, markdown = load_report(args.report_id, args.base_url.rstrip("/"), args.from_disk)
    graph_id = args.graph_id or report.get("graph_id")
    if not graph_id:
        raise SystemExit("No graph_id on report; pass --graph-id")
    if not markdown.strip():
        raise SystemExit("Report markdown is empty")

    graph = load_graph(graph_id, args.base_url.rstrip("/"), args.from_disk)
    # Cache graph JSON for later --from-disk runs
    cache_dir = UPLOADS / "exports"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{graph_id}.json"
    cache_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")

    out = Path(args.output) if args.output else Path(f"mirofish_{args.report_id}.html")
    out.write_text(build_html(report, markdown, graph), encoding="utf-8")
    print(f"Wrote {out.resolve()}")
    print(f"  report={args.report_id}  graph={graph_id}  nodes={graph.get('node_count')}  edges={graph.get('edge_count')}")
    print(f"  graph cache: {cache_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.URLError as e:
        print(f"API unreachable ({e}). Is the backend up, or use --from-disk after one successful fetch?", file=sys.stderr)
        raise SystemExit(2)
