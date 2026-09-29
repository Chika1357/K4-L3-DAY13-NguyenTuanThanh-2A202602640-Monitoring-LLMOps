from __future__ import annotations

import re
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from structlog.contextvars import bind_contextvars, clear_contextvars

from .pii import scrub_text

# Accept client-provided IDs only if they are short and log-safe; anything else
# (empty, too long, newlines/control chars) is replaced to prevent log injection.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def new_correlation_id() -> str:
    return f"req-{uuid.uuid4().hex[:8]}"


def resolve_correlation_id(header_value: str | None) -> str:
    candidate = (header_value or "").strip()
    # correlation_id is exempt from log scrubbing, so a client ID that looks like
    # PII (e.g. "0987654321") must be replaced here instead of logged verbatim.
    if _SAFE_REQUEST_ID.fullmatch(candidate) and scrub_text(candidate) == candidate:
        return candidate
    return new_correlation_id()


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Drop any context left over from a previous request on this worker.
        clear_contextvars()

        correlation_id = resolve_correlation_id(request.headers.get("x-request-id"))
        bind_contextvars(correlation_id=correlation_id)

        request.state.correlation_id = correlation_id

        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000

        response.headers["x-request-id"] = correlation_id
        response.headers["x-response-time-ms"] = f"{elapsed_ms:.2f}"

        return response
