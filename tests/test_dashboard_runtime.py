from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from app.dashboard import compute_dashboard, load_contract, render_html
from app.main import app

NOW = datetime(2026, 9, 29, 9, 30, 30, tzinfo=timezone.utc)


def _rec(event: str, minutes_ago: float, **fields) -> dict:
    return {"event": event, "_ts": NOW - timedelta(minutes=minutes_ago), **fields}


def _sent(minutes_ago: float, latency: int, **overrides) -> dict:
    fields = dict(
        latency_ms=latency, ttft_ms=50, tokens_in=40, tokens_out=100,
        cost_usd=0.0016, quality_score=0.8, tool_success=True,
    )
    fields.update(overrides)
    return _rec("response_sent", minutes_ago, **fields)


def _panel(data: dict, panel_id: str) -> dict:
    return next(p for p in data["panels"] if p["id"] == panel_id)


def test_dashboard_aggregates_follow_contract() -> None:
    records = [
        _rec("request_received", 5), _sent(5, 400),
        _rec("request_received", 4), _sent(4, 600, quality_score=0.6),
        _rec("request_received", 3),
        _rec("request_failed", 3, error_type="RuntimeError", tool_success=False),
        _rec("request_received", 2), _sent(2, 5000),
        # Outside the 60-minute window: must be ignored.
        _rec("request_received", 90), _sent(90, 99999),
    ]

    data = compute_dashboard(records, load_contract(), now=NOW)

    assert [p["id"] for p in data["panels"]] == ["latency", "traffic", "errors", "cost", "tokens", "quality"]
    assert data["time_range_minutes"] == 60 and len(data["buckets"]) == 60

    latency = _panel(data, "latency")
    assert latency["values"]["p50"] == 600
    assert latency["values"]["p99"] == 5000
    assert latency["values"]["ttft_p95"] == 50
    assert latency["threshold_ok"] is False  # p95 = 5000 > 3000

    errors = _panel(data, "errors")["values"]
    assert errors["error_rate_pct"] == 25.0
    assert errors["count_by_value"] == {"RuntimeError": 1}
    assert errors["tool_success_rate_pct"] == 75.0

    assert _panel(data, "traffic")["values"]["count"] == 4
    assert _panel(data, "cost")["values"]["total"] == 0.0048
    assert _panel(data, "tokens")["values"]["sum_by_field"] == {"tokens_in": 120, "tokens_out": 300}
    quality = _panel(data, "quality")
    assert quality["values"]["mean"] == round((0.8 + 0.6 + 0.8) / 3, 3)
    assert quality["threshold_ok"] is False  # 0.733 < 0.75


def test_empty_window_reports_no_data() -> None:
    data = compute_dashboard([], load_contract(), now=NOW)
    assert all(p["threshold_ok"] is None for p in data["panels"])


def test_render_html_embeds_data_safely() -> None:
    data = compute_dashboard([], load_contract(), now=NOW)
    data["title"] = "T"
    html = render_html(data)
    assert '<meta http-equiv="refresh" content="30">' in html
    assert "</script><script>" not in render_html({**data, "title": "</script><script>"}).split("const D =")[1]


def test_dashboard_endpoints_respond(monkeypatch, tmp_path) -> None:
    from app import logging_config

    monkeypatch.setattr(logging_config, "LOG_PATH", tmp_path / "logs.jsonl")

    async def fetch():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/dashboard/data"), await client.get("/dashboard")

    data_response, page_response = asyncio.run(fetch())
    assert data_response.status_code == 200
    assert len(data_response.json()["panels"]) == 6
    assert page_response.status_code == 200
    assert "text/html" in page_response.headers["content-type"]
