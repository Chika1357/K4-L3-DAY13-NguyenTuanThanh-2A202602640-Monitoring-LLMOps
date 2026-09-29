from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx

from app import logging_config
from app.logging_config import scrub_event
from app.main import app
from app.middleware import resolve_correlation_id
from app.pii import hash_user_id

REQUEST_ID_FORMAT = re.compile(r"^req-[0-9a-f]{8}$")


def _post_chats(requests: list[tuple[dict, dict]]) -> list[httpx.Response]:
    async def send() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return [
                await client.post("/chat", json=body, headers=headers)
                for body, headers in requests
            ]

    return asyncio.run(send())


def _read_events(log_path: Path) -> list[dict]:
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


def _body(user: str, message: str = "Explain observability") -> dict:
    return {"user_id": user, "session_id": f"s-{user}", "feature": "qa", "message": message}


def test_generates_request_id_and_returns_headers(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(logging_config, "LOG_PATH", tmp_path / "logs.jsonl")

    (response,) = _post_chats([(_body("u01"), {})])

    request_id = response.headers["x-request-id"]
    assert REQUEST_ID_FORMAT.fullmatch(request_id)
    assert response.json()["correlation_id"] == request_id
    assert float(response.headers["x-response-time-ms"]) >= 0


def test_propagates_client_request_id(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(logging_config, "LOG_PATH", tmp_path / "logs.jsonl")

    (response,) = _post_chats([(_body("u01"), {"x-request-id": "req-abcdef12"})])

    assert response.headers["x-request-id"] == "req-abcdef12"
    assert response.json()["correlation_id"] == "req-abcdef12"


def test_rejects_unsafe_client_request_id() -> None:
    assert resolve_correlation_id("req-abcdef12") == "req-abcdef12"
    for bad in (None, "", "x" * 65, "evil\ninjected", "has space"):
        assert REQUEST_ID_FORMAT.fullmatch(resolve_correlation_id(bad))


def test_logs_are_enriched_and_context_does_not_leak(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)

    first, second = _post_chats(
        [
            (_body("u01"), {"x-request-id": "req-11111111"}),
            (_body("u02"), {}),
        ]
    )

    api_events = [e for e in _read_events(log_path) if e.get("service") == "api"]
    by_cid: dict[str, list[dict]] = {}
    for event in api_events:
        by_cid.setdefault(event["correlation_id"], []).append(event)

    assert set(by_cid) == {first.headers["x-request-id"], second.headers["x-request-id"]}
    for cid, user in ((first.headers["x-request-id"], "u01"), (second.headers["x-request-id"], "u02")):
        events = by_cid[cid]
        assert {e["event"] for e in events} == {"request_received", "response_sent"}
        for event in events:
            assert {"ts", "level", "event", "correlation_id", "env"} <= event.keys()
            assert event["user_id_hash"] == hash_user_id(user)
            assert event["session_id"] == f"s-{user}"
            assert event["feature"] == "qa"
            assert event["model"]


def test_pii_is_scrubbed_before_writing_file(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)
    # Kept short: message_preview is truncated to 80 chars after scrubbing.
    message = "s@vinuni.edu.vn 0987654321 001203004567 4111 1111 1111 1111"

    _post_chats([(_body("u01", message), {})])

    raw = log_path.read_text(encoding="utf-8")
    for secret in ("s@vinuni.edu.vn", "0987654321", "001203004567", "4111 1111 1111 1111"):
        assert secret not in raw
    for label in ("EMAIL", "PHONE_VN", "CCCD", "CREDIT_CARD"):
        assert f"[REDACTED_{label}]" in raw


def test_scrub_event_covers_nested_fields_and_keeps_system_ids() -> None:
    event = {
        "event": "request_failed",
        "correlation_id": "req-12345678",
        "user_id_hash": "123456789012",
        "exception": "ValueError: bad input from student@vinuni.edu.vn",
        "payload": {"detail": {"phones": ["0901234567"]}},
    }

    out = scrub_event(None, "error", event)

    assert out["user_id_hash"] == "123456789012"
    assert out["correlation_id"] == "req-12345678"
    assert "student@" not in out["exception"]
    assert out["payload"]["detail"]["phones"] == ["[REDACTED_PHONE_VN]"]
