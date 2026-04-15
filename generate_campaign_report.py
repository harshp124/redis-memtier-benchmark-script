#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any


def split_columns(line: str) -> list[str]:
    return [part.strip() for part in re.split(r"\s{2,}", line.strip()) if part.strip()]


def normalize_header(name: str) -> str:
    normalized = name.lower().replace("%", "pct")
    normalized = re.sub(r"[^a-z0-9.]+", "_", normalized).strip("_")
    normalized = normalized.replace(".", "_")
    normalized = re.sub(r"_+", "_", normalized)
    return normalized


def maybe_number(value: str) -> float | None:
    if value in {"---", "N/A", "nan", "-nan"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_stats_table(raw_text: str) -> list[dict[str, Any]]:
    lines = raw_text.splitlines()
    candidates: list[list[dict[str, Any]]] = []

    for index, line in enumerate(lines):
        if not line.strip().startswith("Type") or "Ops/sec" not in line:
            continue

        headers = split_columns(line)
        normalized_headers = [normalize_header(header) for header in headers]
        rows: list[dict[str, Any]] = []

        for next_line in lines[index + 1 :]:
            stripped = next_line.strip()
            if not stripped:
                break
            if set(stripped) in ({"-"}, {"="}):
                continue
            if stripped.startswith("[RUN #"):
                continue

            columns = split_columns(next_line)
            if len(columns) != len(headers):
                if rows:
                    break
                continue

            row: dict[str, Any] = {}
            for header, normalized, value in zip(headers, normalized_headers, columns):
                if header == "Type":
                    row["type"] = value
                else:
                    row[normalized] = maybe_number(value)
            rows.append(row)

        if rows and any(row.get("type") == "Totals" for row in rows):
            candidates.append(rows)

    if not candidates:
        raise ValueError("Unable to locate a memtier statistics table in raw output.")

    return candidates[-1]


def parse_distribution(raw_text: str) -> dict[str, list[dict[str, float]]]:
    lines = raw_text.splitlines()
    start_index = next((i for i, line in enumerate(lines) if "Request Latency Distribution" in line), -1)
    if start_index == -1:
        return {}

    series: dict[str, list[dict[str, float]]] = {}
    for line in lines[start_index + 1 :]:
        stripped = line.strip()
        if not stripped or stripped == "---" or stripped.startswith("Type") or re.fullmatch(r"[-=]+", stripped):
            continue
        match = re.match(r"^([A-Z]+)\s+([\d.]+)\s+([\d.]+)$", stripped)
        if not match:
            continue
        command, latency, percent = match.groups()
        series.setdefault(command, []).append({"latency": float(latency), "percent": float(percent)})
    return series


def classify_run(stem: str) -> tuple[str, str]:
    parts = stem.split("_")
    workload = "other"
    mode = "other"
    for candidate in ("readheavy", "mixed", "writeheavy", "load", "warmup"):
        if candidate in parts:
            workload = candidate
            break
    if "saturation" in parts:
        mode = "saturation"
    elif "ratelimit" in parts:
        mode = "ratelimit"
    elif "load" in parts:
        mode = "load"
    elif "warmup" in parts:
        mode = "warmup"
    return workload, mode


def percentile_series(total_row: dict[str, Any]) -> list[dict[str, float | str]]:
    result = []
    for key, value in total_row.items():
        if value is None or not key.endswith("_latency") or not key.startswith("p"):
            continue
        label = key[:-8].replace("_", ".").upper()
        try:
            percentile = float(label[1:])
        except ValueError:
            continue
        result.append({"label": label, "percentile": percentile, "value": float(value)})
    return sorted(result, key=lambda item: float(item["percentile"]))


def related_files(run_dir: Path, stem: str) -> list[str]:
    patterns = [
        f"{stem}.raw.txt",
        f"{stem}_FULL_RUN_*.txt",
        f"{stem}_GET_command_run_*.txt",
        f"{stem}_SET_command_run_*.txt",
        f"{stem}_FULL_RUN_*.hgrm",
        f"{stem}_GET_command_run_*.hgrm",
        f"{stem}_SET_command_run_*.hgrm",
    ]
    found: list[str] = []
    for pattern in patterns:
        for path in sorted(run_dir.glob(pattern)):
            if path.name not in found:
                found.append(path.name)
    return found


def parse_run_file(path: Path) -> dict[str, Any] | None:
    raw_text = path.read_text(encoding="utf-8", errors="replace")
    try:
        rows = parse_stats_table(raw_text)
    except ValueError:
        return None

    totals = next((row for row in rows if row["type"] == "Totals"), rows[-1])
    stem = path.name.removesuffix(".raw.txt")
    workload, mode = classify_run(stem)
    return {
        "name": stem,
        "file": path.name,
        "workload": workload,
        "mode": mode,
        "rows": rows,
        "totals": totals,
        "distribution": parse_distribution(raw_text),
        "percentiles": percentile_series(totals),
        "related_files": related_files(path.parent, stem),
    }


def format_num(value: float | None, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "N/A"
    return f"{value:,.{digits}f}{suffix}"


def svg_bar_chart(items: list[dict[str, Any]], title: str, color_a: str = "#0f766e", color_b: str = "#2563eb") -> str:
    usable = [item for item in items if item.get("value") is not None]
    if not usable:
        return "<p>No data available.</p>"

    width = max(720, 110 * len(usable))
    height = 290
    left = 52
    bottom = 58
    top = 20
    chart_width = width - left - 20
    chart_height = height - top - bottom
    max_value = max(float(item["value"]) for item in usable) or 1.0
    bar_width = chart_width / len(usable)

    bars = []
    for idx, item in enumerate(usable):
      value = float(item["value"])
      x = left + idx * bar_width + 8
      width_current = max(bar_width - 16, 24)
      bar_height = (value / max_value) * chart_height
      y = top + chart_height - bar_height
      bars.append(
          f'<rect x="{x:.2f}" y="{y:.2f}" width="{width_current:.2f}" height="{bar_height:.2f}" rx="4" fill="{color_a if idx % 2 == 0 else color_b}"></rect>'
      )
      bars.append(
          f'<text x="{x + width_current / 2:.2f}" y="{height - 18}" text-anchor="middle" font-size="12" fill="#5b6773">{html.escape(str(item["label"]))}</text>'
      )
      bars.append(
          f'<text x="{x + width_current / 2:.2f}" y="{max(12, y - 6):.2f}" text-anchor="middle" font-size="11" fill="#17212b">{value:.2f}</text>'
      )

    return f"""
      <div class="chart-title">{html.escape(title)}</div>
      <svg viewBox="0 0 {width} {height}" width="100%" height="auto">
        <line x1="{left}" y1="{top}" x2="{left}" y2="{height - bottom}" stroke="#94a3b8"></line>
        <line x1="{left}" y1="{height - bottom}" x2="{width - 12}" y2="{height - bottom}" stroke="#94a3b8"></line>
        {''.join(bars)}
      </svg>
    """


def svg_line_chart(series_map: dict[str, list[dict[str, float]]], title: str) -> str:
    names = [name for name, items in series_map.items() if items]
    if not names:
        return "<p>No distribution data available.</p>"

    width = 760
    height = 320
    left = 56
    right = 20
    top = 18
    bottom = 44
    usable_width = width - left - right
    usable_height = height - top - bottom
    all_points = [point for name in names for point in series_map[name]]
    max_latency = max(point["latency"] for point in all_points) or 1.0
    colors = {"GET": "#2563eb", "SET": "#ea580c", "WAIT": "#64748b", "TOTAL": "#0f766e"}

    grid = []
    for tick in (0, 25, 50, 75, 100):
        x = left + (tick / 100) * usable_width
        grid.append(f'<line x1="{x}" y1="{top}" x2="{x}" y2="{height - bottom}" stroke="rgba(148,163,184,0.18)"></line>')
        grid.append(f'<text x="{x}" y="{height - 16}" text-anchor="middle" font-size="12" fill="#5b6773">{tick}%</text>')

    lines = []
    legends = []
    for idx, name in enumerate(names):
        color = colors.get(name, "#0f766e")
        points = []
        for point in series_map[name]:
            x = left + (point["percent"] / 100) * usable_width
            y = top + usable_height - (point["latency"] / max_latency) * usable_height
            points.append(f"{x:.2f},{y:.2f}")
        lines.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="3" stroke-linecap="round"></polyline>')
        legends.append(
            f'<g transform="translate({left + idx * 90}, {height - 4})"><rect width="16" height="4" rx="2" fill="{color}"></rect><text x="22" y="6" font-size="12" fill="#5b6773">{html.escape(name)}</text></g>'
        )

    return f"""
      <div class="chart-title">{html.escape(title)}</div>
      <svg viewBox="0 0 {width} {height}" width="100%" height="auto">
        <line x1="{left}" y1="{top}" x2="{left}" y2="{height - bottom}" stroke="#94a3b8"></line>
        <line x1="{left}" y1="{height - bottom}" x2="{width - right}" y2="{height - bottom}" stroke="#94a3b8"></line>
        {''.join(grid)}
        {''.join(lines)}
        {''.join(legends)}
      </svg>
    """


def render_metrics(run: dict[str, Any]) -> str:
    totals = run["totals"]
    metrics = [
        ("Throughput", format_num(totals.get("ops_sec"), 2, " ops/sec"), "Totals row"),
        ("Avg Latency", format_num(totals.get("avg_latency"), 3, " ms"), "Totals row"),
        ("P95", format_num(totals.get("p95_latency"), 3, " ms"), "Tail latency"),
        ("P99", format_num(totals.get("p99_latency"), 3, " ms"), "Tail latency"),
        ("Hits/sec", format_num(totals.get("hits_sec"), 2), "Read hits"),
        ("Misses/sec", format_num(totals.get("misses_sec"), 2), "Read misses"),
        ("KB/sec", format_num(totals.get("kb_sec"), 2), "Transfer rate"),
        ("Mode", html.escape(run["mode"]), html.escape(run["workload"])),
    ]
    return "".join(
        f'<div class="metric"><div class="metric-label">{label}</div><div class="metric-value">{value}</div><div class="metric-sub">{sub}</div></div>'
        for label, value, sub in metrics
    )


def render_table(rows: list[dict[str, Any]]) -> str:
    headers = ["type"] + [key for key in rows[0].keys() if key != "type"]
    header_html = "".join(f"<th>{html.escape(header.replace('_', ' '))}</th>" for header in headers)
    body_html = []
    for row in rows:
        cells = []
        for header in headers:
            value = row.get(header)
            if isinstance(value, float):
                cells.append(f"<td>{value:.3f}</td>")
            elif value is None:
                cells.append("<td>---</td>")
            else:
                cells.append(f"<td>{html.escape(str(value))}</td>")
        body_html.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{header_html}</tr></thead><tbody>{''.join(body_html)}</tbody></table>"


def render_summary_table(runs: list[dict[str, Any]]) -> str:
    rows = []
    for run in runs:
        totals = run["totals"]
        rows.append(
            "<tr>"
            f"<td>{html.escape(run['name'])}</td>"
            f"<td>{html.escape(run['workload'])}</td>"
            f"<td>{html.escape(run['mode'])}</td>"
            f"<td>{format_num(totals.get('ops_sec'), 2)}</td>"
            f"<td>{format_num(totals.get('avg_latency'), 3)}</td>"
            f"<td>{format_num(totals.get('p95_latency'), 3)}</td>"
            f"<td>{format_num(totals.get('p99_latency'), 3)}</td>"
            "</tr>"
        )
    return f"""
    <table>
      <thead>
        <tr>
          <th>Run</th>
          <th>Workload</th>
          <th>Mode</th>
          <th>Ops/sec</th>
          <th>Avg Latency</th>
          <th>P95</th>
          <th>P99</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
    """


def render_html(run_dir: Path, runs: list[dict[str, Any]]) -> str:
    if not runs:
        raise ValueError("No parsable raw memtier files were found.")

    best_throughput = max((run["totals"].get("ops_sec") or 0) for run in runs)
    p99_values = [run["totals"].get("p99_latency") for run in runs if run["totals"].get("p99_latency") is not None]
    lowest_p99 = min(p99_values) if p99_values else None

    summary_cards = [
        ("Runs Parsed", str(len(runs))),
        ("Best Throughput", format_num(best_throughput, 2, " ops/sec")),
        ("Lowest P99", format_num(lowest_p99, 3, " ms")),
        ("Folder", html.escape(run_dir.name)),
    ]

    summary_charts = [
        svg_bar_chart(
            [{"label": run["name"], "value": run["totals"].get("ops_sec")} for run in runs],
            "Throughput by run",
        ),
        svg_bar_chart(
            [{"label": run["name"], "value": run["totals"].get("p99_latency")} for run in runs],
            "P99 latency by run",
            color_a="#2563eb",
            color_b="#0f766e",
        ),
        svg_bar_chart(
            [{"label": run["name"], "value": run["totals"].get("avg_latency")} for run in runs],
            "Average latency by run",
            color_a="#ea580c",
            color_b="#2563eb",
        ),
    ]

    tabs = ['<button class="tab-button active" data-tab="summary">Summary</button>']
    panels = [f"""
      <section class="tab-panel active" id="tab-summary">
        <div class="panel">
          <h1>Memtier Campaign Report</h1>
          <p>This report was generated from all parsable <code>*.raw.txt</code> files in this output folder.</p>
          <div class="cards">
            {''.join(f'<div class="card"><div class="card-label">{label}</div><div class="card-value">{value}</div></div>' for label, value in summary_cards)}
          </div>
        </div>
        <div class="panel chart-stack">
          {''.join(f'<div class="chart-box">{chart}</div>' for chart in summary_charts)}
        </div>
        <div class="panel">
          <h2>Run Summary</h2>
          {render_summary_table(runs)}
        </div>
      </section>
    """]

    for idx, run in enumerate(runs, start=1):
        tab_id = f"run-{idx}"
        tabs.append(f'<button class="tab-button" data-tab="{tab_id}">{html.escape(run["name"])}</button>')
        related_links = "".join(f'<li><a href="{html.escape(file)}">{html.escape(file)}</a></li>' for file in run["related_files"])
        distribution = run["distribution"]
        panels.append(f"""
          <section class="tab-panel" id="tab-{tab_id}">
            <div class="panel">
              <h2>{html.escape(run["name"])}</h2>
              <p>Workload: <strong>{html.escape(run["workload"])}</strong> | Mode: <strong>{html.escape(run["mode"])}</strong></p>
              <div class="metrics">
                {render_metrics(run)}
              </div>
            </div>
            <div class="panel chart-grid">
              <div class="chart-box">
                {svg_bar_chart(run["percentiles"], "Latency percentiles")}
              </div>
              <div class="chart-box">
                {svg_line_chart(distribution, "Request latency distribution")}
              </div>
            </div>
            <div class="panel">
              <h3>Related Files</h3>
              <ul class="file-list">{related_links}</ul>
            </div>
            <div class="panel">
              <h3>Parsed Table</h3>
              {render_table(run["rows"])}
            </div>
          </section>
        """)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Memtier Campaign Report</title>
  <style>
    :root {{
      --bg: #f5f7fa;
      --panel: #ffffff;
      --ink: #18212b;
      --muted: #5b6773;
      --line: #d8e0e8;
      --accent: #0f766e;
      --shadow: 0 8px 24px rgba(15, 23, 42, 0.06);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--ink);
    }}
    .page {{
      max-width: 1360px;
      margin: 0 auto;
      padding: 24px;
    }}
    .tabs {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 18px;
    }}
    .tab-button {{
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      border-radius: 999px;
      padding: 10px 16px;
      cursor: pointer;
      font-weight: 600;
    }}
    .tab-button.active {{
      background: var(--accent);
      color: #fff;
      border-color: var(--accent);
    }}
    .tab-panel {{ display: none; }}
    .tab-panel.active {{ display: block; }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px;
      margin-bottom: 18px;
      box-shadow: var(--shadow);
    }}
    .cards, .metrics {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }}
    .card, .metric {{
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px;
      background: #fff;
    }}
    .card-label, .metric-label {{
      color: var(--muted);
      text-transform: uppercase;
      font-size: 12px;
      letter-spacing: .04em;
      margin-bottom: 8px;
    }}
    .card-value, .metric-value {{
      font-size: 24px;
      font-weight: 700;
      line-height: 1.1;
    }}
    .metric-sub {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 13px;
    }}
    .chart-stack, .chart-grid {{
      display: grid;
      gap: 16px;
    }}
    .chart-grid {{
      grid-template-columns: 1fr 1fr;
    }}
    .chart-box {{
      overflow-x: auto;
    }}
    .chart-title {{
      font-weight: 700;
      margin-bottom: 8px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      text-align: left;
      padding: 10px;
      border-bottom: 1px solid var(--line);
    }}
    th {{
      font-size: 12px;
      text-transform: uppercase;
      color: var(--muted);
    }}
    a {{ color: var(--accent); text-decoration: none; }}
    p {{ color: var(--muted); line-height: 1.5; }}
    .file-list {{
      margin: 0;
      padding-left: 18px;
    }}
    @media (max-width: 980px) {{
      .cards, .metrics, .chart-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <div class="tabs">
      {''.join(tabs)}
    </div>
    {''.join(panels)}
  </div>
  <script>
    document.querySelectorAll('.tab-button').forEach((button) => {{
      button.addEventListener('click', () => {{
        document.querySelectorAll('.tab-button').forEach((item) => item.classList.remove('active'));
        document.querySelectorAll('.tab-panel').forEach((panel) => panel.classList.remove('active'));
        button.classList.add('active');
        document.getElementById(`tab-${{button.dataset.tab}}`).classList.add('active');
      }});
    }});
  </script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a tabbed HTML dashboard for a memtier benchmark campaign.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--title", default="Memtier Campaign Report")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    raw_files = sorted(run_dir.glob("*.raw.txt"))
    runs = []
    for raw_file in raw_files:
        parsed = parse_run_file(raw_file)
        if parsed is not None:
            runs.append(parsed)

    summary = {
        "title": args.title,
        "run_dir": str(run_dir),
        "runs": runs,
    }

    (run_dir / "campaign_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (run_dir / "index.html").write_text(render_html(run_dir, runs), encoding="utf-8")


if __name__ == "__main__":
    main()
