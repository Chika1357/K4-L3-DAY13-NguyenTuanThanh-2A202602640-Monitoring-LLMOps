from __future__ import annotations

from contextlib import contextmanager

from app import agent as agent_module


class ManagedPrompt:
    version = 3

    def compile(self, **variables: str) -> str:
        return (
            f"Feature={variables['feature']}\n"
            f"Docs={variables['docs']}\n"
            f"Question={variables['message']}"
        )


class RecordingLangfuseClient:
    def __init__(self) -> None:
        self.prompt = ManagedPrompt()
        self.span_updates: list[dict] = []

    def get_prompt(self, name: str, **kwargs):
        return self.prompt

    def update_current_span(self, **kwargs) -> None:
        self.span_updates.append(kwargs)


def test_agent_records_prompt_version_with_v4_observation_api(monkeypatch) -> None:
    monkeypatch.setenv("LANGFUSE_PROMPT_NAME", "day13-chat")
    monkeypatch.setenv("LANGFUSE_PROMPT_LABEL", "production")
    client = RecordingLangfuseClient()
    monkeypatch.setattr(agent_module, "get_langfuse_client", lambda: client)
    monkeypatch.setattr(agent_module, "tracing_enabled", lambda: True)

    propagated: list[dict] = []

    @contextmanager
    def record_attributes(**kwargs):
        propagated.append(kwargs)
        yield

    monkeypatch.setattr(agent_module, "propagate_attributes", record_attributes)

    agent = agent_module.LabAgent()
    agent_module.LabAgent.run.__wrapped__(
        agent,
        user_id="student-01",
        feature="qa",
        session_id="session-01",
        message="Explain traces",
        correlation_id="req-12345678",
    )

    span_update = client.span_updates[-1]
    assert span_update["metadata"] == {
        "doc_count": 1,
        "query_preview": "Explain traces",
        "prompt_name": "day13-chat",
        "prompt_label": "production",
        "prompt_version": "3",
        "prompt_source": "langfuse",
        "prompt_fetch_error": "",
    }
    assert span_update["version"] == "3"
    assert propagated[0]["metadata"]["correlation_id"] == "req-12345678"
    assert propagated[-1]["prompt"] is client.prompt


class RecordingObservation:
    def __init__(self, name: str, as_type: str, kwargs: dict) -> None:
        self.name = name
        self.as_type = as_type
        self.kwargs = kwargs
        self.updates: dict = {}

    def update(self, **kwargs):
        self.updates.update(kwargs)
        return self


def _run_with_recorded_observations(monkeypatch, message: str, observations=None):
    client = RecordingLangfuseClient()
    monkeypatch.setattr(agent_module, "get_langfuse_client", lambda: client)
    monkeypatch.setattr(agent_module, "tracing_enabled", lambda: True)
    observations = [] if observations is None else observations

    @contextmanager
    def record_observation(*, name: str, as_type: str, **kwargs):
        obs = RecordingObservation(name, as_type, kwargs)
        observations.append(obs)
        yield obs

    monkeypatch.setattr(agent_module, "start_observation", record_observation)
    agent = agent_module.LabAgent()
    result = agent_module.LabAgent.run.__wrapped__(
        agent,
        user_id="student-01",
        feature="qa",
        session_id="session-01",
        message=message,
        correlation_id="req-12345678",
    )
    return client, observations, result


def test_agent_creates_retrieval_and_generation_children(monkeypatch) -> None:
    client, observations, result = _run_with_recorded_observations(
        monkeypatch, "Explain monitoring, mail me at student@vinuni.edu.vn"
    )

    retrieval, prompt_span, generation = observations
    assert (retrieval.name, retrieval.as_type) == ("retrieval", "retriever")
    assert (prompt_span.name, prompt_span.as_type) == ("prompt-resolve", "span")
    assert prompt_span.updates["output"] == {"version": "3", "source": "langfuse"}
    assert retrieval.updates["output"]["doc_count"] == 1

    assert (generation.name, generation.as_type) == ("llm-generate", "generation")
    assert generation.kwargs["model"] == "claude-sonnet-4-5"
    assert generation.kwargs["prompt"] is client.prompt
    assert generation.kwargs["metadata"]["prompt_version"] == "3"
    usage = generation.updates["usage_details"]
    assert usage == {
        "input": result.tokens_in,
        "output": result.tokens_out,
        "total": result.tokens_in + result.tokens_out,
    }
    assert generation.updates["cost_details"]["total"] == result.cost_usd
    assert "completion_start_time" in generation.updates

    # No raw PII is captured in any observation input/output.
    captured = repr([o.kwargs for o in observations] + [o.updates for o in observations])
    assert "student@vinuni.edu.vn" not in captured


def test_retrieval_failure_marks_observation_as_error(monkeypatch) -> None:
    from app.incidents import STATE

    monkeypatch.setitem(STATE, "tool_fail", True)
    observations: list[RecordingObservation] = []
    try:
        _run_with_recorded_observations(monkeypatch, "Explain monitoring", observations)
    except RuntimeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("tool_fail should raise")

    (retrieval,) = observations  # generation never starts
    assert retrieval.updates["level"] == "ERROR"
    assert "Vector store timeout" in retrieval.updates["status_message"]
