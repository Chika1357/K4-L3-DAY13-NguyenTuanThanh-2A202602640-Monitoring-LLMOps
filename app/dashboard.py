"""Runtime dashboard built from data/logs.jsonl following config/dashboard.yaml.

The contract (panel ids, titles, units, thresholds, time range, refresh) is
read from the YAML so the page and the validator never drift apart.
"""
from __future__ import annotations

import html
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import yaml

from .metrics import percentile

CONFIG_PATH = Path("config/dashboard.yaml")


def load_contract(path: Path = CONFIG_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["dashboard"]


def load_records(log_path: Path) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []
    records = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            record["_ts"] = datetime.fromisoformat(record["ts"].replace("Z", "+00:00"))
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        records.append(record)
    return records


def _passes(value: float | None, threshold: dict[str, Any]) -> bool | None:
    if value is None:
        return None
    if threshold["operator"] == "lte":
        return value <= threshold["value"]
    return value >= threshold["value"]


def _cumulative(values: list[float | None]) -> list[float]:
    total, out = 0.0, []
    for value in values:
        total += value or 0
        out.append(round(total, 6))
    return out


def _pct(part: int, whole: int) -> float | None:
    return round(part / whole * 100, 2) if whole else None


def compute_dashboard(
    records: list[dict[str, Any]],
    contract: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    minutes = contract["time_range_minutes"]
    end = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    start = end - timedelta(minutes=minutes)
    buckets = [start + timedelta(minutes=i) for i in range(minutes)]
    window = [r for r in records if start <= r["_ts"] < end]

    def by_event(*events: str) -> list[dict[str, Any]]:
        return [r for r in window if r.get("event") in events]

    def per_minute(rows: list[dict[str, Any]], fn) -> list[float | None]:
        grouped: dict[datetime, list[dict[str, Any]]] = {b: [] for b in buckets}
        for row in rows:
            grouped[row["_ts"].replace(second=0, microsecond=0)].append(row)
        return [fn(grouped[b]) for b in buckets]

    sent = by_event("response_sent")
    received = by_event("request_received")
    failed = by_event("request_failed")
    tool_rows = [r for r in window if r.get("tool_success") is not None]

    def lat(rows, p, field="latency_ms"):
        values = [r[field] for r in rows if r.get(field) is not None]
        return percentile(values, p) if values else None

    first_ts = min((r["_ts"] for r in received), default=None)
    active_minutes = max(1.0, (now - first_ts).total_seconds() / 60) if first_ts else None

    values: dict[str, dict[str, Any]] = {
        "latency": {
            "p50": lat(sent, 50),
            "p95": lat(sent, 95),
            "p99": lat(sent, 99),
            "ttft_p95": lat(sent, 95, "ttft_ms"),
        },
        "traffic": {
            "count": len(received),
            "rate_per_minute": round(len(received) / active_minutes, 2) if active_minutes else None,
        },
        "errors": {
            "error_rate_pct": _pct(len(failed), len(received)),
            "count_by_value": dict(Counter(r.get("error_type") or "unknown" for r in failed)),
            "tool_success_rate_pct": _pct(sum(1 for r in tool_rows if r["tool_success"]), len(tool_rows)),
        },
        "cost": {
            "total": round(sum(r.get("cost_usd") or 0 for r in sent), 6) if sent else None,
        },
        "tokens": {
            "sum_by_field": {
                "tokens_in": sum(r.get("tokens_in") or 0 for r in sent),
                "tokens_out": sum(r.get("tokens_out") or 0 for r in sent),
            } if sent else {},
        },
        "quality": {
            "mean": round(mean(r["quality_score"] for r in sent), 3) if sent else None,
        },
    }

    series: dict[str, dict[str, list[float | None]]] = {
        "latency": {
            "P50": per_minute(sent, lambda rows: lat(rows, 50)),
            "P95": per_minute(sent, lambda rows: lat(rows, 95)),
            "P99": per_minute(sent, lambda rows: lat(rows, 99)),
            "TTFT P95": per_minute(sent, lambda rows: lat(rows, 95, "ttft_ms")),
        },
        "traffic": {"requests/min": per_minute(received, len)},
        "errors": {
            "error rate %": per_minute(
                received + failed,
                lambda rows: _pct(
                    sum(r["event"] == "request_failed" for r in rows),
                    sum(r["event"] == "request_received" for r in rows),
                ),
            ),
            "retrieval success %": per_minute(
                tool_rows,
                lambda rows: _pct(sum(1 for r in rows if r["tool_success"]), len(rows)),
            ),
        },
        "cost": {
            "USD/min": per_minute(sent, lambda rows: round(sum(r.get("cost_usd") or 0 for r in rows), 6) if rows else None),
            "cumulative USD": _cumulative(per_minute(sent, lambda rows: sum(r.get("cost_usd") or 0 for r in rows))),
        },
        # Token/cost thresholds apply to the window total, so plot running totals.
        "tokens": {
            "tokens_in (cumulative)": _cumulative(per_minute(sent, lambda rows: sum(r.get("tokens_in") or 0 for r in rows))),
            "tokens_out (cumulative)": _cumulative(per_minute(sent, lambda rows: sum(r.get("tokens_out") or 0 for r in rows))),
        },
        "quality": {
            "mean quality": per_minute(sent, lambda rows: round(mean(r["quality_score"] for r in rows), 3) if rows else None),
        },
    }

    panels = []
    for panel in contract["panels"]:
        threshold = panel["threshold"]
        value = values[panel["id"]].get(threshold["aggregation"])
        if isinstance(value, dict):  # sum_by_field: every field must pass
            checks = [_passes(v, threshold) for v in value.values()]
            ok = None if not value else all(checks)
        else:
            ok = _passes(value, threshold)
        panels.append(
            {
                "id": panel["id"],
                "title": panel["title"],
                "unit": panel["unit"],
                "threshold": threshold,
                "threshold_ok": ok,
                "values": values[panel["id"]],
                "series": series[panel["id"]],
            }
        )

    return {
        "title": contract["title"],
        "time_range_minutes": minutes,
        "refresh_seconds": contract["refresh_seconds"],
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "generated_at": now.isoformat(),
        "record_count": len(window),
        "buckets": [b.isoformat() for b in buckets],
        "panels": panels,
    }


def render_html(data: dict[str, Any]) -> str:
    payload = json.dumps(data).replace("</", "<\\/")
    return _TEMPLATE.replace("__DATA__", payload).replace(
        "__REFRESH__", str(data["refresh_seconds"])
    ).replace("__TITLE__", html.escape(data["title"]))


_TEMPLATE = """<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="__REFRESH__">
<title>__TITLE__</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --text:#1d2330; --muted:#667085; --line:#e4e7ec; --ok:#12805c; --bad:#c4320a; --thr:#c4320a; }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--text); }
  header { padding:16px 20px; border-bottom:1px solid var(--line); background:var(--card); display:flex; flex-wrap:wrap; gap:8px 24px; align-items:baseline; }
  h1 { font-size:18px; margin:0; }
  .meta { color:var(--muted); font-size:13px; }
  main { display:grid; grid-template-columns:repeat(auto-fit,minmax(420px,1fr)); gap:16px; padding:16px 20px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; min-width:0; }
  .card h2 { font-size:15px; margin:0 0 2px; display:flex; justify-content:space-between; gap:8px; }
  .unit { color:var(--muted); font-size:12px; }
  .badge { font-size:12px; padding:1px 8px; border-radius:99px; font-weight:600; white-space:nowrap; }
  .ok { background:#e7f6ef; color:var(--ok); } .bad { background:#fdecea; color:var(--bad); } .na { background:#eef0f3; color:var(--muted); }
  .stats { display:flex; flex-wrap:wrap; gap:4px 18px; margin:8px 0; }
  .stat b { font-size:18px; font-variant-numeric:tabular-nums; } .stat span { color:var(--muted); font-size:12px; margin-left:4px; }
  .chart { position:relative; height:200px; }
  @media (max-width:480px) { main { grid-template-columns:1fr; padding:16px; } }
</style>
</head>
<body>
<header>
  <h1 id="title"></h1>
  <span class="meta" id="range"></span>
  <span class="meta" id="gen"></span>
</header>
<main id="grid"></main>
<script>
const D = __DATA__;
const fmtTime = iso => new Date(iso).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
const fmt = v => v === null || v === undefined ? '–' : (typeof v === 'number' ? (Math.abs(v) < 0.01 && v !== 0 ? v.toFixed(6) : +v.toFixed(3)) : v);
document.getElementById('title').textContent = D.title;
document.getElementById('range').textContent =
  `Time range: last ${D.time_range_minutes} min (${fmtTime(D.window_start)} – ${fmtTime(D.window_end)}) · refresh ${D.refresh_seconds}s · ${D.record_count} log records`;
document.getElementById('gen').textContent = `Source: data/logs.jsonl · updated ${new Date(D.generated_at).toLocaleString()}`;
const labels = D.buckets.map(fmtTime);
const colors = ['#2f6fdf', '#e8871e', '#8e44ad', '#12805c'];
const opName = {lte: '≤', gte: '≥'};
for (const p of D.panels) {
  const card = document.createElement('section');
  card.className = 'card';
  const t = p.threshold;
  const badge = p.threshold_ok === null ? ['na', 'no data'] : p.threshold_ok ? ['ok', 'OK'] : ['bad', 'BREACH'];
  const stats = Object.entries(p.values).map(([k, v]) =>
    typeof v === 'object' && v !== null
      ? Object.entries(v).map(([k2, v2]) => `<div class="stat"><b>${fmt(v2)}</b><span>${k2}</span></div>`).join('') || `<div class="stat"><b>–</b><span>${k}</span></div>`
      : `<div class="stat"><b>${fmt(v)}</b><span>${k}</span></div>`).join('');
  card.innerHTML = `<h2><span>${p.title} <span class="unit">(${p.unit})</span></span>
    <span class="badge ${badge[0]}">${badge[1]}</span></h2>
    <div class="unit">Threshold: ${t.aggregation} ${opName[t.operator]} ${t.value} ${p.unit}</div>
    <div class="stats">${stats}</div><div class="chart"><canvas></canvas></div>`;
  document.getElementById('grid').appendChild(card);
  const datasets = Object.entries(p.series).map(([name, data], i) => ({
    label: name, data, borderColor: colors[i % colors.length], backgroundColor: colors[i % colors.length],
    // Minutes without requests stay as gaps; joining them would draw a fake trend.
    spanGaps: false, pointRadius: 3, borderWidth: 2, tension: 0.2,
  }));
  if (p.id === 'traffic') { datasets[0].type = 'bar'; }
  datasets.push({ label: `threshold ${opName[t.operator]} ${t.value}`, data: labels.map(() => t.value),
    borderColor: '#c4320a', borderDash: [6, 4], pointRadius: 0, borderWidth: 1.5 });
  new Chart(card.querySelector('canvas'), {
    type: 'line',
    data: { labels, datasets },
    options: { responsive: true, maintainAspectRatio: false, animation: false,
      scales: { x: { ticks: { maxTicksLimit: 12 }, title: { display: true, text: 'time (1-minute buckets)' } },
                y: { beginAtZero: true, title: { display: true, text: p.unit } } },
      plugins: { legend: { labels: { boxWidth: 12 } } } },
  });
}
</script>
</body>
</html>
"""
