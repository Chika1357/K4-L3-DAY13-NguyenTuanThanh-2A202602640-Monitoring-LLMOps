from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any

try:
    from langfuse import get_client, observe, propagate_attributes

    LANGFUSE_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - chỉ dùng khi chưa cài requirements
    LANGFUSE_SDK_AVAILABLE = False

    def observe(*args: Any, **kwargs: Any):
        def decorator(func):
            return func

        return decorator

    class _DummyClient:
        def update_current_span(self, **kwargs: Any) -> None:
            return None

        def update_current_generation(self, **kwargs: Any) -> None:
            return None

    def get_client():
        return _DummyClient()

    @contextmanager
    def propagate_attributes(**kwargs: Any):
        yield


class _NoopObservation:
    def update(self, **kwargs: Any) -> "_NoopObservation":
        return self


def get_langfuse_client():
    return get_client()


@contextmanager
def start_observation(*, name: str, as_type: str, **kwargs: Any):
    """Open a child observation nested under the currently active one.

    Uses the SDK client directly (a no-op tracer when keys are missing) and
    yields an object whose ``update()`` sets usage, cost, output, level, ...
    """
    if not LANGFUSE_SDK_AVAILABLE:
        yield _NoopObservation()
        return
    with get_client().start_as_current_observation(name=name, as_type=as_type, **kwargs) as obs:
        yield obs


def tracing_enabled() -> bool:
    return LANGFUSE_SDK_AVAILABLE and bool(
        os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")
    )
