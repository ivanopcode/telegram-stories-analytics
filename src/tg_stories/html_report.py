"""Standalone HTML report renderer."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .analysis import human_duration, user_label


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def pct(value: float | None) -> str:
    return f"{(value or 0) * 100:.1f}%"


def bar_chart(rows: list[dict[str, Any]], *, title: str, value_key: str, label_key: str = "label", suffix: str = "") -> str:
    if not rows:
        return f"<section class='panel'><h2>{esc(title)}</h2><p class='muted'>No data.</p></section>"
    max_value = max(float(row.get(value_key) or 0) for row in rows) or 1.0
    body = []
    for row in rows:
        value = float(row.get(value_key) or 0)
        width = max(1.0, value / max_value * 100.0)
        label = row.get(label_key)
        body.append(
            "<div class='bar-row'>"
            f"<div class='bar-label'>{esc(label)}</div>"
            "<div class='bar-track'>"
            f"<div class='bar-fill' style='width:{width:.2f}%'></div>"
            "</div>"
            f"<div class='bar-value'>{esc(format_number(value))}{esc(suffix)}</div>"
            "</div>"
        )
    return f"<section class='panel'><h2>{esc(title)}</h2><div class='bars'>{''.join(body)}</div></section>"


def format_number(value: float) -> str:
    if value >= 100 or value.is_integer():
        return str(int(round(value)))
    return f"{value:.1f}"


def user_rows(rows: list[dict[str, Any]], columns: list[tuple[str, str]], limit: int = 20) -> str:
    headers = "".join(f"<th>{esc(title)}</th>" for title, _ in columns)
    body = []
    for row in rows[:limit]:
        cells = []
        for _, key in columns:
            if key == "user":
                cells.append(f"<td>{esc(user_label(row.get('user')))}</td>")
            elif key == "view_rate":
                cells.append(f"<td>{pct(row.get('view_rate'))}</td>")
            elif key == "reaction_rate_per_viewed_story":
                cells.append(f"<td>{pct(row.get('reaction_rate_per_viewed_story'))}</td>")
            elif key.endswith("_seconds"):
                cells.append(f"<td>{esc(human_duration(row.get(key)))}</td>")
            else:
                cells.append(f"<td>{esc(row.get(key))}</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")
    return f"<table><thead><tr>{headers}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def render_html(analysis: dict[str, Any], *, source_file: str | None = None) -> str:
    summary = analysis["summary"]
    regularity_rows = analysis["regularity"]["all_time_top"]
    reaction_rows = analysis["reactions"]["top_by_absolute_count"]
    fast_rows = analysis["speed"]["top_fast_regular_viewers"]
    segments = analysis.get("segments", {})

    regularity_chart_rows = [
        {"label": user_label(row.get("user")), "value": (row.get("view_rate") or 0) * 100}
        for row in regularity_rows[:20]
    ]
    reaction_chart_rows = [
        {"label": user_label(row.get("user")), "value": row.get("reaction_count") or 0}
        for row in reaction_rows[:20]
    ]
    fast_chart_rows = [
        {"label": user_label(row.get("user")), "value": row.get("median_view_latency_seconds") or 0}
        for row in fast_rows[:20]
    ]
    segment_rows = [
        {"label": key.replace("_", " "), "value": value.get("view_events") or 0}
        for key, value in sorted(segments.items())
    ]

    cards = [
        ("Stories", summary.get("story_count")),
        ("Unique viewers", summary.get("unique_viewers")),
        ("View events", summary.get("view_events")),
        ("Reaction events", summary.get("reaction_events")),
        ("Median view latency", human_duration(summary.get("median_view_latency_seconds"))),
        ("Within 24h", pct(summary.get("views_within_24h_rate"))),
    ]
    cards_html = "".join(f"<div class='metric'><span>{esc(label)}</span><strong>{esc(value)}</strong></div>" for label, value in cards)

    recent_sections = []
    for key, window in analysis["regularity"].get("recent_windows", {}).items():
        rows = [
            {"label": user_label(row.get("user")), "value": (row.get("view_rate") or 0) * 100}
            for row in window.get("top_by_regularity", [])[:15]
        ]
        recent_sections.append(
            "<section class='panel'>"
            f"<h2>{esc(key.replace('_', ' ').title())}</h2>"
            f"<p class='muted'>{esc(window.get('story_count'))} stories, {esc(window.get('unique_viewers'))} unique viewers, "
            f"{esc(window.get('regular_threshold_count'))} regulars above threshold.</p>"
            f"{bar_inner(rows, suffix='%')}"
            "</section>"
        )

    payload = {
        "generated_at": analysis.get("generated_at"),
        "source_file": source_file,
    }
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Telegram Stories Analytics</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f7f2;
      --ink: #17202a;
      --muted: #667085;
      --panel: #ffffff;
      --line: #d9dee6;
      --accent: #0f7b6c;
      --accent-2: #a53f2b;
      --accent-3: #355c9a;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--ink); background: var(--bg); }}
    header {{ padding: 28px 32px 18px; border-bottom: 1px solid var(--line); background: #fff; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; letter-spacing: 0; }}
    h2 {{ margin: 0 0 14px; font-size: 18px; letter-spacing: 0; }}
    h3 {{ margin: 18px 0 10px; font-size: 15px; }}
    main {{ max-width: 1240px; margin: 0 auto; padding: 24px; }}
    .muted {{ color: var(--muted); margin: 0; }}
    .grid {{ display: grid; grid-template-columns: repeat(12, 1fr); gap: 16px; }}
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-top: 18px; }}
    .metric {{ border: 1px solid var(--line); background: var(--panel); border-radius: 8px; padding: 14px; }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; }}
    .metric strong {{ display: block; margin-top: 5px; font-size: 22px; }}
    .panel {{ grid-column: span 6; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; min-width: 0; }}
    .panel.wide {{ grid-column: span 12; }}
    .bars {{ display: grid; gap: 8px; }}
    .bar-row {{ display: grid; grid-template-columns: minmax(160px, 1.2fr) minmax(120px, 3fr) 64px; align-items: center; gap: 10px; }}
    .bar-label {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .bar-track {{ height: 14px; border-radius: 999px; background: #edf1f4; overflow: hidden; }}
    .bar-fill {{ height: 100%; background: linear-gradient(90deg, var(--accent), var(--accent-3)); border-radius: inherit; }}
    .bar-value {{ text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 8px 6px; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; }}
    code {{ background: #eef1f3; padding: 2px 5px; border-radius: 4px; }}
    @media (max-width: 900px) {{
      header {{ padding: 22px 18px 14px; }}
      main {{ padding: 16px; }}
      .panel {{ grid-column: span 12; }}
      .bar-row {{ grid-template-columns: 1fr; gap: 4px; }}
      .bar-value {{ text-align: left; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Telegram Stories Analytics</h1>
    <p class="muted">Generated {esc(analysis.get('generated_at'))}. Source: <code>{esc(source_file)}</code></p>
    <script type="application/json" id="report-meta">{esc(json.dumps(payload, ensure_ascii=False))}</script>
    <div class="metrics">{cards_html}</div>
  </header>
  <main class="grid">
    {bar_chart(regularity_chart_rows, title='All-Time Regularity', value_key='value', suffix='%')}
    {bar_chart(reaction_chart_rows, title='Top Reactions', value_key='value')}
    {bar_chart(fast_chart_rows, title='Fast Regular Viewers, Median Seconds', value_key='value', suffix='s')}
    {bar_chart(segment_rows, title='Segments by View Events', value_key='value')}
    {''.join(recent_sections)}
    <section class="panel wide">
      <h2>All-Time Regularity Table</h2>
      {user_rows(regularity_rows, [('Viewer', 'user'), ('Viewed', 'viewed_story_count'), ('Total', 'total_story_count'), ('Rate', 'view_rate'), ('Reactions', 'reaction_count'), ('Longest Streak', 'longest_streak')])}
    </section>
    <section class="panel wide">
      <h2>Reaction Rate</h2>
      {user_rows(analysis['reactions']['top_by_rate_min_views'], [('Viewer', 'user'), ('Viewed', 'viewed_story_count'), ('Reactions', 'reaction_count'), ('Reaction Rate', 'reaction_rate_per_viewed_story')])}
    </section>
    <section class="panel wide">
      <h2>Churn</h2>
      <h3>Lapsed Viewers</h3>
      {user_rows(analysis['churn']['lapsed_viewers'], [('Viewer', 'user'), ('Prior Views', 'prior_viewed_story_count'), ('Last View', 'last_view_date'), ('All-Time Rate', 'all_time_view_rate')])}
      <h3>New Recent Viewers</h3>
      {user_rows(analysis['churn']['new_recent_viewers'], [('Viewer', 'user'), ('Views', 'viewed_story_count'), ('First Seen', 'first_seen_story_date'), ('Reactions', 'reaction_count')])}
    </section>
  </main>
</body>
</html>"""


def bar_inner(rows: list[dict[str, Any]], suffix: str = "") -> str:
    if not rows:
        return "<p class='muted'>No data.</p>"
    max_value = max(float(row.get("value") or 0) for row in rows) or 1.0
    body = []
    for row in rows:
        value = float(row.get("value") or 0)
        width = max(1.0, value / max_value * 100.0)
        body.append(
            "<div class='bar-row'>"
            f"<div class='bar-label'>{esc(row.get('label'))}</div>"
            "<div class='bar-track'>"
            f"<div class='bar-fill' style='width:{width:.2f}%'></div>"
            "</div>"
            f"<div class='bar-value'>{esc(format_number(value))}{esc(suffix)}</div>"
            "</div>"
        )
    return f"<div class='bars'>{''.join(body)}</div>"


def write_html(path: Path, analysis: dict[str, Any], *, source_file: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(analysis, source_file=source_file), encoding="utf-8")
