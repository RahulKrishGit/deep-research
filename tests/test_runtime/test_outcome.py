"""Tests for the finished-run summary every front-end reads."""

from __future__ import annotations

from deep_research.agents.events import agent_event
from deep_research.graph.orchestrator import GraphRun
from deep_research.observability import TokenUsageMetric, ToolMetric
from deep_research.runtime.outcome import (
    ResearchOutcome,
    ToolCallSummary,
    build_outcome,
    report_path_from_state,
    tool_call_summaries,
    total_token_usage,
)
from deep_research.utils.types import ResearchError, ResearchState

QUESTION = "How mature is quantum error correction?"


def base_state(**overrides: object) -> ResearchState:
    payload: dict[str, object] = {
        "session_id": "session-1",
        "original_question": QUESTION,
    }
    payload.update(overrides)
    return ResearchState.model_validate(payload)


def synthesis_event(path: str | None) -> object:
    return agent_event(
        agent_name="synthesizer",
        event_type="synthesizer.synthesis.completed",
        message="Report synthesis complete.",
        metadata={"output_path": path, "section_count": 3},
    )


def test_report_path_reads_the_last_synthesis_event() -> None:
    state = base_state(
        events=[
            synthesis_event("report-session-1-0.md"),
            synthesis_event("report-session-1-1.md"),
        ]
    )

    assert report_path_from_state(state) == "report-session-1-1.md"


def test_report_path_is_none_when_no_report_was_written() -> None:
    assert report_path_from_state(base_state()) is None
    assert report_path_from_state(base_state(events=[synthesis_event(None)])) is None


def test_tool_call_summaries_group_by_tool_and_count_failures() -> None:
    metrics = [
        ToolMetric(
            session_id="session-1",
            tool_name="web_search",
            latency_ms=1.0,
            success=True,
        ),
        ToolMetric(
            session_id="session-1",
            tool_name="web_search",
            latency_ms=1.0,
            success=False,
            error_type="ProviderTimeoutError",
        ),
        ToolMetric(
            session_id="session-1",
            tool_name="query_memory",
            latency_ms=1.0,
            success=True,
        ),
    ]

    assert tool_call_summaries(metrics) == [
        ToolCallSummary(tool_name="query_memory", calls=1, failures=0),
        ToolCallSummary(tool_name="web_search", calls=2, failures=1),
    ]


def test_total_token_usage_sums_every_llm_span() -> None:
    metrics = [
        TokenUsageMetric(
            session_id="session-1",
            model="gpt-4o",
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            latency_ms=1.0,
            success=True,
        ),
        TokenUsageMetric(
            session_id="session-1",
            model="gpt-4o",
            input_tokens=5,
            output_tokens=1,
            total_tokens=6,
            latency_ms=1.0,
            success=True,
        ),
    ]

    usage = total_token_usage(metrics)

    assert usage.input_tokens == 105
    assert usage.output_tokens == 21
    assert usage.total_tokens == 126


def test_total_token_usage_is_zero_when_nothing_reported() -> None:
    assert total_token_usage([]).total_tokens == 0


def test_build_outcome_carries_everything_a_front_end_needs() -> None:
    state = base_state(
        report="# Research report",
        events=[synthesis_event("report-session-1-0.md")],
        errors=[
            ResearchError(
                error_type="web_search_failed",
                source="tools.web_search",
                message="The search provider timed out.",
            )
        ],
    )
    run = GraphRun(
        session_id="session-1",
        state=state,
        status="completed",
        trace_url="https://smith.example/run/1",
    )

    outcome = build_outcome(run, metrics=[])

    assert isinstance(outcome, ResearchOutcome)
    assert outcome.session_id == "session-1"
    assert outcome.question == QUESTION
    assert outcome.status == "completed"
    assert outcome.trace_url == "https://smith.example/run/1"
    assert outcome.report_path == "report-session-1-0.md"
    assert outcome.report == "# Research report"
    assert len(outcome.errors) == 1
    assert outcome.failed is False


def test_a_failed_run_is_reported_as_failed() -> None:
    run = GraphRun(
        session_id="session-1",
        state=base_state(),
        status="failed",
        trace_url=None,
    )

    assert build_outcome(run, metrics=[]).failed is True
