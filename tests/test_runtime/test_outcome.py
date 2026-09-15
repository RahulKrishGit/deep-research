"""Tests for the finished-run summary every front-end reads."""

from __future__ import annotations

from deep_research.agents.events import agent_event
from deep_research.graph.events import report_published_event
from deep_research.graph.orchestrator import GraphRun
from deep_research.observability import TokenUsageMetric, ToolMetric
from deep_research.runtime.outcome import (
    ResearchOutcome,
    ToolCallSummary,
    build_outcome,
    evidence_path_from_state,
    report_path_from_state,
    tool_call_summaries,
    total_token_usage,
)
from deep_research.utils.types import (
    QUALITY_STATUS_ACCEPTED,
    QUALITY_STATUS_PARTIAL,
    Critique,
    ReportQualitySnapshot,
    ResearchError,
    ResearchState,
)

QUESTION = "How mature is quantum error correction?"

REPORT_PATH = "output/report-session-1-0.md"
EVIDENCE_PATH = "output/report-session-1-0-evidence.md"


def base_state(**overrides: object) -> ResearchState:
    payload: dict[str, object] = {
        "session_id": "session-1",
        "original_question": QUESTION,
    }
    payload.update(overrides)
    return ResearchState.model_validate(payload)


def legacy_synthesis_event(path: str | None) -> object:
    """The per-pass event a refinement used to publish a report under.

    Task 6 removed in-synthesis publication, so this record no longer names
    the session's final artifact. The tests keep building it to prove the
    outcome never falls back to an earlier pass's file.
    """
    return agent_event(
        agent_name="synthesizer",
        event_type="synthesizer.synthesis.completed",
        message="Report synthesis complete.",
        metadata={"output_path": path, "section_count": 3},
    )


def publication_event(
    report_path: str | None = REPORT_PATH,
    evidence_path: str | None = EVIDENCE_PATH,
) -> object:
    """The terminal event the finalizer emits, carrying both artifact paths."""
    return report_published_event(
        quality_status=QUALITY_STATUS_ACCEPTED,
        report_path=report_path,
        evidence_path=evidence_path,
        document_writes=2,
        memory_writes=1,
        error_count=0,
    )


def quality_snapshot(**overrides: object) -> ReportQualitySnapshot:
    payload: dict[str, object] = {
        "coverage_ratio": 1.0,
        "planned_topics": 7,
        "covered_topics": 7,
        "unique_findings": 3,
        "unique_sources": 12,
        "cited_sources": 12,
        "scored_cited_source_ratio": 1.0,
        "verified_claims": 14,
        "contradicted_claims": 1,
        "duplicate_claims": 0,
        "duplicate_source_rows": 0,
        "uncited_settled_points": 0,
    }
    payload.update(overrides)
    return ReportQualitySnapshot.model_validate(payload)


def accepted_critique() -> Critique:
    return Critique(
        score=6,
        gaps=[],
        unsupported_claims=[],
        recommended_queries=[],
        should_continue=False,
        rationale="Recorded for outcome tests.",
    )


def outcome_of(state: ResearchState) -> ResearchOutcome:
    return build_outcome(
        GraphRun(
            session_id="session-1",
            state=state,
            status="completed",
            trace_url=None,
        ),
        metrics=[],
    )


def test_report_path_reads_the_terminal_publication_event() -> None:
    state = base_state(events=[publication_event()])

    assert report_path_from_state(state) == REPORT_PATH


def test_report_path_is_none_when_no_report_was_published() -> None:
    assert report_path_from_state(base_state()) is None


def test_a_failed_terminal_report_write_never_falls_back() -> None:
    """The terminal event is the only record of the session's final report.

    A refinement pass that wrote a file earlier must not be advertised once
    the terminal write failed.
    """
    state = base_state(
        events=[
            legacy_synthesis_event(REPORT_PATH),
            publication_event(report_path=None),
        ]
    )

    assert report_path_from_state(state) is None


def test_a_lone_legacy_synthesis_event_yields_no_report_path() -> None:
    """The deleted compatibility fallback must not come back.

    ``test_a_failed_terminal_report_write_never_falls_back`` pairs a legacy
    synthesis event with a terminal ``publication_event(report_path=None)``,
    which returns ``None`` whether or not a fallback exists — the terminal
    record is read first either way. A state carrying *only* the legacy event
    is the shape that distinguishes the two: with no publication record at
    all, the legacy metadata must not be consulted.
    """
    state = base_state(events=[legacy_synthesis_event(REPORT_PATH)])

    assert report_path_from_state(state) is None


def test_report_path_falls_back_to_the_state_stamp() -> None:
    """A state the finalizer stamped is authoritative on its own."""
    state = base_state(report_path=REPORT_PATH)

    assert report_path_from_state(state) == REPORT_PATH


def test_evidence_path_reads_the_terminal_publication_event() -> None:
    state = base_state(events=[publication_event()])

    assert evidence_path_from_state(state) == EVIDENCE_PATH


def test_evidence_path_is_none_when_no_ledger_was_published() -> None:
    assert evidence_path_from_state(base_state()) is None


def test_a_failed_terminal_evidence_write_never_falls_back() -> None:
    """The two terminal writes fail independently, and neither leaks a path."""
    state = base_state(
        events=[
            legacy_synthesis_event(EVIDENCE_PATH),
            publication_event(evidence_path=None),
        ]
    )

    assert evidence_path_from_state(state) is None
    assert report_path_from_state(state) == REPORT_PATH


def test_evidence_path_falls_back_to_the_state_stamp() -> None:
    state = base_state(evidence_path=EVIDENCE_PATH)

    assert evidence_path_from_state(state) == EVIDENCE_PATH


def test_the_newest_publication_record_wins() -> None:
    state = base_state(
        events=[
            publication_event(
                report_path=REPORT_PATH, evidence_path=EVIDENCE_PATH
            ),
            publication_event(report_path="output/report-session-1-1.md"),
        ]
    )

    assert report_path_from_state(state) == "output/report-session-1-1.md"
    assert outcome_of(state).evidence_path == EVIDENCE_PATH


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


def test_tool_call_summaries_can_be_limited_to_one_session() -> None:
    metrics = [
        ToolMetric(
            session_id="session-1",
            tool_name="web_search",
            latency_ms=1.0,
            success=True,
        ),
        ToolMetric(
            session_id="session-2",
            tool_name="web_search",
            latency_ms=1.0,
            success=True,
        ),
        ToolMetric(
            session_id="session-1",
            tool_name="query_memory",
            latency_ms=1.0,
            success=True,
        ),
    ]

    assert tool_call_summaries(metrics, session_id="session-1") == [
        ToolCallSummary(tool_name="query_memory", calls=1, failures=0),
        ToolCallSummary(tool_name="web_search", calls=1, failures=0),
    ]


def test_total_token_usage_can_be_limited_to_one_session() -> None:
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
            session_id="session-2",
            model="gpt-4o",
            input_tokens=5,
            output_tokens=1,
            total_tokens=6,
            latency_ms=1.0,
            success=True,
        ),
    ]

    usage = total_token_usage(metrics, session_id="session-1")

    assert usage.input_tokens == 100
    assert usage.output_tokens == 20


def test_build_outcome_carries_everything_a_front_end_needs() -> None:
    state = base_state(
        report="# Research report",
        events=[publication_event()],
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
    assert outcome.report_path == REPORT_PATH
    assert outcome.evidence_path == EVIDENCE_PATH
    assert outcome.report == "# Research report"
    assert len(outcome.errors) == 1
    assert outcome.failed is False


def test_build_outcome_carries_both_terminal_artifact_paths() -> None:
    """Both paths come from the terminal publication, never from report prose."""
    run = GraphRun(
        session_id="session-1",
        state=base_state(events=[publication_event()]),
        status="completed",
        trace_url=None,
    )

    outcome = build_outcome(run, metrics=[])

    assert outcome.report_path == REPORT_PATH
    assert outcome.evidence_path == EVIDENCE_PATH


def test_a_failed_terminal_evidence_write_yields_no_evidence_path() -> None:
    """Step 1: a failed ledger write yields ``None``, not an earlier path."""
    run = GraphRun(
        session_id="session-1",
        state=base_state(
            events=[
                legacy_synthesis_event(EVIDENCE_PATH),
                publication_event(evidence_path=None),
            ]
        ),
        status="completed",
        trace_url=None,
    )

    outcome = build_outcome(run, metrics=[])

    assert outcome.evidence_path is None


def test_quality_is_the_typed_snapshot_from_state() -> None:
    snapshot = quality_snapshot()
    run = GraphRun(
        session_id="session-1",
        state=base_state(quality=snapshot),
        status="completed",
        trace_url=None,
    )

    assert build_outcome(run, metrics=[]).quality is snapshot


def test_quality_status_names_the_terminal_verdict() -> None:
    accepted = base_state(
        quality=quality_snapshot(), critique=accepted_critique()
    )

    assert outcome_of(accepted).quality_status == QUALITY_STATUS_ACCEPTED
    assert outcome_of(base_state()).quality_status == QUALITY_STATUS_PARTIAL


def test_accepted_is_true_only_for_an_accepted_terminal_status() -> None:
    """Acceptance needs both a snapshot and the critic's satisfied route."""
    accepted = base_state(
        quality=quality_snapshot(), critique=accepted_critique()
    )
    budget_spent = base_state(
        quality=quality_snapshot(),
        critique=accepted_critique(),
        iteration=1,
        max_iterations=1,
    )
    hard_failure = base_state(
        quality=quality_snapshot(hard_failures=["duplicate_claims"]),
        critique=accepted_critique(),
    )

    assert outcome_of(accepted).accepted is True
    assert outcome_of(budget_spent).accepted is False
    assert outcome_of(hard_failure).accepted is False
    assert outcome_of(base_state()).accepted is False


def test_build_outcome_ignores_metrics_from_other_sessions() -> None:
    """A resumed run shares its tracker with the run that made the checkpoint."""
    run = GraphRun(
        session_id="session-1",
        state=base_state(),
        status="completed",
        trace_url=None,
    )
    metrics = [
        ToolMetric(
            session_id="session-2",
            tool_name="web_search",
            latency_ms=1.0,
            success=True,
        ),
        TokenUsageMetric(
            session_id="session-2",
            model="gpt-4o",
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            latency_ms=1.0,
            success=True,
        ),
    ]

    outcome = build_outcome(run, metrics=metrics)

    assert outcome.tool_calls == ()
    assert outcome.token_usage.total_tokens == 0


def test_a_failed_run_is_reported_as_failed() -> None:
    run = GraphRun(
        session_id="session-1",
        state=base_state(),
        status="failed",
        trace_url=None,
    )

    assert build_outcome(run, metrics=[]).failed is True
