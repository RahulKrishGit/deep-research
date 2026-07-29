"""Tests for LangSmith runtime settings and nested trace context."""

from collections.abc import Mapping

import pytest
from pydantic import ValidationError

from deep_research.observability.context import (
    LangSmithRuntimeConfig,
    TraceContext,
    bind_trace_context,
    build_trace_metadata,
    current_trace_context,
    load_langsmith_runtime_config,
)
from deep_research.utils.config import LangSmithConfig


def test_disabled_runtime_config_does_not_require_secrets() -> None:
    runtime = load_langsmith_runtime_config(
        LangSmithConfig(tracing_enabled=False, project=""), environ={}
    )
    assert runtime.tracing_enabled is False
    assert runtime.project == ""
    assert runtime.api_key is None


def test_enabled_runtime_config_loads_api_key_without_serializing_it() -> None:
    runtime = load_langsmith_runtime_config(
        LangSmithConfig(tracing_enabled=True, project="deep-research-tests"),
        environ={"LANGSMITH_API_KEY": "secret-key"},
    )
    assert runtime.api_key is not None
    assert runtime.api_key.get_secret_value() == "secret-key"
    assert "secret-key" not in runtime.model_dump_json()


@pytest.mark.parametrize(
    ("config", "environ", "missing_name"),
    [
        (
            LangSmithConfig(tracing_enabled=True, project=""),
            {"LANGSMITH_API_KEY": "secret-key"},
            "LANGSMITH_PROJECT",
        ),
        (
            LangSmithConfig(tracing_enabled=True, project="deep-research-tests"),
            {},
            "LANGSMITH_API_KEY",
        ),
    ],
)
def test_enabled_runtime_config_requires_project_and_api_key(
    config: LangSmithConfig, environ: Mapping[str, str], missing_name: str
) -> None:
    with pytest.raises(ValueError, match=missing_name):
        load_langsmith_runtime_config(config, environ=environ)


def test_nested_trace_context_restores_parent() -> None:
    session = TraceContext(session_id="session-1")
    agent = session.model_copy(update={"agent_name": "planner"})
    assert current_trace_context() is None
    with bind_trace_context(session):
        assert current_trace_context() == session
        with bind_trace_context(agent):
            assert current_trace_context() == agent
        assert current_trace_context() == session
    assert current_trace_context() is None


def test_build_trace_metadata_filters_none_and_protects_context_keys() -> None:
    context = TraceContext(session_id="session-1", agent_name="planner", iteration=2)
    metadata = build_trace_metadata(
        context, extra={"span_kind": "react", "session_id": "wrong-session"}
    )
    assert metadata == {
        "span_kind": "react",
        "session_id": "session-1",
        "agent_name": "planner",
        "iteration": 2,
    }


@pytest.mark.parametrize(
    "values",
    [
        {"tracing_enabled": True, "project": "", "api_key": "secret-key"},
        {
            "tracing_enabled": True,
            "project": "deep-research-tests",
            "api_key": None,
        },
        {
            "tracing_enabled": True,
            "project": "deep-research-tests",
            "api_key": "   ",
        },
    ],
)
def test_direct_enabled_runtime_config_requires_project_and_api_key(
    values: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        LangSmithRuntimeConfig(**values)
