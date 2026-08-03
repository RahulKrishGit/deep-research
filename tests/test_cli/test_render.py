"""Tests for what the CLI prints."""

from __future__ import annotations

from deep_research.cli import render_progress, render_summary, render_warnings
from deep_research.graph.events import (
    node_started_event,
    refinement_started_event,
    session_completed_event,
    session_started_event,
)
from deep_research.observability import TokenUsage
from deep_research.runtime.outcome import ResearchOutcome, ToolCallSummary
from deep_research.utils.types import (
    ResearchError,
    ResearchEvent,
    ResearchState,
)

QUESTION = "How mature is quantum error correction?"


def build_outcome(**overrides) -> ResearchOutcome:
    state = overrides.pop("state", None) or ResearchState(
        session_id="session-1", original_question=QUESTION
    )
    defaults = {
        "session_id": "session-1",
        "question": QUESTION,
        "status": "completed",
        "state": state,
        "trace_url": None,
        "report_path": "report-session-1-0.md",
        "token_usage": TokenUsage(),
        "tool_calls": (),
    }
    defaults.update(overrides)
    return ResearchOutcome(**defaults)


def progress_state() -> ResearchState:
    return ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        events=[
            session_started_event(
                session_id="session-1", max_iterations=2, checkpointing=False
            ),
            node_started_event("planner", iteration=0),
            ResearchEvent(
                event_type="observability.span.started",
                source="observability",
                message="agent.planner started.",
            ),
            node_started_event("researcher", iteration=0),
            refinement_started_event(iteration=1, max_iterations=2),
            session_completed_event(
                status="completed", iteration=1, error_count=0, has_report=True
            ),
        ],
    )


def test_progress_shows_each_agent_without_the_span_noise() -> None:
    lines = render_progress(build_outcome(state=progress_state()), verbose=False)

    joined = "\n".join(lines)
    assert "Node planner started." in joined
    assert "Node researcher started." in joined
    assert "Refinement pass 1 started." in joined
    assert "observability" not in joined


def test_verbose_progress_keeps_every_event_but_the_spans() -> None:
    state = progress_state()

    lines = render_progress(build_outcome(state=state), verbose=True)

    assert len(lines) == len(
        [
            event
            for event in state.events
            if not event.event_type.startswith("observability.span.")
        ]
    ) + 1  # the heading


def test_warnings_render_one_line_per_recorded_error() -> None:
    state = ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        errors=[
            ResearchError(
                error_type="web_search_failed",
                source="tools.web_search",
                message="The search provider timed out.",
            )
        ],
    )

    lines = render_warnings(build_outcome(state=state))

    assert lines == [
        "warning: [web_search_failed] The search provider timed out."
    ]


def test_no_errors_means_no_warnings() -> None:
    assert render_warnings(build_outcome()) == []


def test_the_summary_names_the_session_status_and_report() -> None:
    lines = render_summary(build_outcome(), verbose=False)

    joined = "\n".join(lines)
    assert "Session ID: session-1" in joined
    assert "Status: completed" in joined
    assert "Report: report-session-1-0.md" in joined
    assert "Trace" not in joined


def test_the_summary_prints_the_trace_url_when_there_is_one() -> None:
    lines = render_summary(
        build_outcome(trace_url="https://smith.example/run/1"), verbose=False
    )

    assert "Trace: https://smith.example/run/1" in "\n".join(lines)


def test_the_summary_is_explicit_when_no_report_reached_disk() -> None:
    lines = render_summary(build_outcome(report_path=None), verbose=False)

    assert "Report: not written to disk" in "\n".join(lines)


def test_a_limited_run_says_so_without_calling_itself_a_failure() -> None:
    lines = render_summary(build_outcome(status="max_iterations"), verbose=False)

    joined = "\n".join(lines)
    assert "Status: max_iterations" in joined
    assert "refinement budget" in joined


def test_verbose_summary_reports_tool_calls_and_tokens() -> None:
    outcome = build_outcome(
        token_usage=TokenUsage(input_tokens=900, output_tokens=100),
        tool_calls=(
            ToolCallSummary(tool_name="web_search", calls=4, failures=1),
            ToolCallSummary(tool_name="query_memory", calls=2, failures=0),
        ),
    )

    joined = "\n".join(render_summary(outcome, verbose=True))

    assert "web_search: 4 calls (1 failed)" in joined
    assert "query_memory: 2 calls" in joined
    assert "Tokens: 1000 total (900 in / 100 out)" in joined


def test_verbose_summary_says_tokens_are_unavailable_when_none_were_seen() -> None:
    joined = "\n".join(render_summary(build_outcome(), verbose=True))

    assert "Tokens: not available" in joined
