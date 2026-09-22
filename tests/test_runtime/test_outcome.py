"""Tests for the finished-run summary every front-end reads."""

from __future__ import annotations

from deep_research.agents.events import agent_event
from deep_research.agents.researcher import sub_topic_completed_event
from deep_research.agents.steps import ReActRun
from deep_research.graph.events import report_published_event
from deep_research.graph.orchestrator import GraphRun
from deep_research.observability import TokenUsageMetric, ToolMetric
from deep_research.request_budget import (
    ProviderCategory,
    RequestBudgetSnapshot,
)
from deep_research.runtime.outcome import (
    ResearchOutcome,
    ToolCallSummary,
    build_outcome,
    evidence_path_from_state,
    quality_path_from_state,
    report_path_from_state,
    tool_call_summaries,
    total_token_usage,
)
from deep_research.utils.types import (
    LEGACY_QUALITY_CONTRACT_VERSION,
    QUALITY_CONTRACT_VERSION,
    QUALITY_STATUS_ACCEPTED,
    QUALITY_STATUS_PARTIAL,
    Critique,
    ReportQualitySnapshot,
    ResearchError,
    ResearchEvent,
    ResearchState,
    SubTopic,
)
from tests.graph_fakes import fake_report_review

QUESTION = "How mature is quantum error correction?"

REPORT_PATH = "output/report-session-1-0.md"
EVIDENCE_PATH = "output/report-session-1-0-evidence.md"
QUALITY_PATH = "output/report-session-1-0-quality.json"


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
    quality_path: str | None = QUALITY_PATH,
) -> object:
    """The terminal event the finalizer emits, carrying all three paths."""
    return report_published_event(
        quality_status=QUALITY_STATUS_ACCEPTED,
        report_path=report_path,
        evidence_path=evidence_path,
        quality_path=quality_path,
        document_writes=3,
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


def test_tool_call_summaries_carry_the_retries_the_tracker_recorded() -> None:
    """A retry is its own number, never pooled into the call it retried.

    The tracker's tool spans record how many transport retries a call made, so
    the summary reports them beside the call and failure counts: two calls that
    retried twice and once are two calls and three retries, and a tool that
    never retried reports zero rather than inheriting another tool's count.
    """
    metrics = [
        ToolMetric(
            session_id="session-1",
            tool_name="web_search",
            latency_ms=1.0,
            success=True,
            retry_count=2,
        ),
        ToolMetric(
            session_id="session-1",
            tool_name="web_search",
            latency_ms=1.0,
            success=False,
            retry_count=1,
            error_type="ProviderTimeoutError",
        ),
        ToolMetric(
            session_id="session-1",
            tool_name="read_url",
            latency_ms=1.0,
            success=True,
        ),
    ]

    summaries = {
        summary.tool_name: summary for summary in tool_call_summaries(metrics)
    }

    assert summaries["web_search"] == ToolCallSummary(
        tool_name="web_search", calls=2, failures=1, retries=3
    )
    assert summaries["read_url"].retries == 0


def sub_topic_event(duplicates: int, beyond_cap: int) -> object:
    """One sub-topic completion, exactly as the researcher records it."""
    return sub_topic_completed_event(
        SubTopic(
            coverage_id="topic-01",
            title="Alpha",
            rationale="First sub-topic.",
            search_queries=["alpha evidence"],
            success_criteria=["alpha answered"],
            priority=1,
        ),
        ReActRun(agent_name="researcher", stop_reason="finished"),
        index=1,
        findings=4,
        dropped_duplicate=duplicates,
        dropped_cap=beyond_cap,
        sources_retained=2,
        publishers_retained=2,
        source_urls_retained=2,
        findings_retained=4,
    )


def test_dropped_proposals_are_summed_from_the_researchers_own_records() -> None:
    """What the pass proposed and did not keep, from its own sub-topic records.

    A duplicate is a restatement folded into a finding already held, and a cap
    drop is a distinct finding past the per-sub-topic limit. Both are counted
    here as their own numbers rather than left invisible behind the findings
    that did enter state, and the two reasons stay distinct.
    """
    state = base_state(events=[sub_topic_event(2, 1), sub_topic_event(0, 4)])

    dropped = outcome_of(state).dropped_proposals

    assert dropped is not None
    assert dropped.duplicates == 2
    assert dropped.beyond_cap == 5
    assert dropped.total == 7


def test_dropped_proposals_are_absent_without_a_researchers_record() -> None:
    """No sub-topic completion is no answer, never a zero drop count."""
    assert outcome_of(base_state()).dropped_proposals is None


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
        quality=quality_snapshot(),
        critique=accepted_critique(),
        report_review=fake_report_review(),
    )

    assert outcome_of(accepted).quality_status == QUALITY_STATUS_ACCEPTED
    assert outcome_of(base_state()).quality_status == QUALITY_STATUS_PARTIAL


def test_accepted_is_true_only_for_an_accepted_terminal_status() -> None:
    """Acceptance needs a snapshot, the critic's satisfied route, and a review.

    Task 10 adds the third: the Critic's own acceptance and a clean gate are
    not a judgement of the report's substance, so a state that carries both and
    no scored review is `partial` — the case this test's last line now pins.
    """
    accepted = base_state(
        quality=quality_snapshot(),
        critique=accepted_critique(),
        report_review=fake_report_review(),
    )
    budget_spent = base_state(
        quality=quality_snapshot(),
        critique=accepted_critique(),
        report_review=fake_report_review(),
        iteration=1,
        max_iterations=1,
    )
    hard_failure = base_state(
        quality=quality_snapshot(hard_failures=["duplicate_claims"]),
        critique=accepted_critique(),
        report_review=fake_report_review(),
    )
    unreviewed = base_state(
        quality=quality_snapshot(), critique=accepted_critique()
    )

    assert outcome_of(accepted).accepted is True
    assert outcome_of(budget_spent).accepted is False
    assert outcome_of(hard_failure).accepted is False
    assert outcome_of(unreviewed).quality_status == QUALITY_STATUS_PARTIAL
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


def budget_snapshot(
    provider: ProviderCategory,
    *,
    attempts: int,
    ceiling: int | None = None,
    effective_limit: int | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> RequestBudgetSnapshot:
    return RequestBudgetSnapshot(
        provider=provider,
        attempts=attempts,
        ceiling=ceiling,
        effective_limit=effective_limit,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def completed_run() -> GraphRun:
    return GraphRun(
        session_id="session-1",
        state=base_state(),
        status="completed",
        trace_url=None,
    )


def test_request_budget_snapshots_default_to_an_empty_tuple() -> None:
    """A caller that never saw a budget still gets an immutable tuple."""
    outcome = build_outcome(completed_run(), metrics=[])

    assert outcome.request_budget_snapshots == ()
    assert isinstance(outcome.request_budget_snapshots, tuple)


def test_build_outcome_carries_the_request_budget_snapshots() -> None:
    snapshots = (
        budget_snapshot(
            "tavily", attempts=11, ceiling=11, effective_limit=11
        ),
    )

    outcome = build_outcome(
        completed_run(), metrics=[], request_budget_snapshots=snapshots
    )

    assert outcome.request_budget_snapshots == snapshots
    assert outcome.request_budget_snapshots[0].attempts == 11
    assert outcome.request_budget_snapshots[0].ceiling == 11


def test_request_budget_snapshots_preserve_the_declared_absence_of_a_ceiling() -> (
    None
):
    """An uncapped provider is recorded as ``None``, never as a zero limit."""
    outcome = build_outcome(
        completed_run(),
        metrics=[],
        request_budget_snapshots=(budget_snapshot("openai", attempts=2),),
    )

    assert outcome.request_budget_snapshots[0].ceiling is None
    assert outcome.request_budget_snapshots[0].effective_limit is None


# --- the quality record, the review, and the published set -------------------


def test_quality_path_reads_the_terminal_publication_event() -> None:
    state = base_state(events=[publication_event()])

    assert quality_path_from_state(state) == QUALITY_PATH
    assert outcome_of(state).quality_path == QUALITY_PATH


def test_a_failed_quality_write_advertises_no_paths_at_all() -> None:
    """The set is advertised whole or not at all.

    The node never emits a partial publication, and this is the shape it emits
    instead: every path ``None``, the write count truthful, and the failure
    named by an error. A front-end reading the event is pointed at nothing
    rather than at two thirds of a set.
    """
    state = base_state(
        events=[
            publication_event(
                report_path=None, evidence_path=None, quality_path=None
            ),
        ],
        errors=[
            ResearchError(
                error_type="graph_publication_failed",
                source="graph.finalize_report",
                message="A publication write did not complete.",
                details={
                    "artifact": "quality",
                    "tool": "write_document",
                    "failure_type": "ValidationError",
                },
            )
        ],
    )

    outcome = outcome_of(state)

    assert outcome.report_path is None
    assert outcome.evidence_path is None
    assert outcome.quality_path is None
    assert outcome.failed_publication_artifacts == ("quality",)
    assert outcome.failed_memory_writes == 0


def test_a_failed_memory_write_is_counted_and_withholds_no_path() -> None:
    """Memory is outside the published set; its failures are counted.

    ``nodes`` attempts a memory write only for an accepted report and only
    after the three documents, and the paths do not depend on it. Reading a
    failed claim write as an incomplete publication printed "Publication:
    incomplete … No artifact path is advertised" above all three advertised
    paths — and named ``memory`` once per failed claim.
    """
    state = base_state(
        events=[publication_event()],
        errors=[
            ResearchError(
                error_type="graph_publication_failed",
                source="graph.finalize_report",
                message="A publication write did not complete.",
                details={
                    "artifact": "memory",
                    "tool": "write_memory",
                    "failure_type": "OSError",
                },
            ),
            ResearchError(
                error_type="graph_publication_failed",
                source="graph.finalize_report",
                message="A publication write did not complete.",
                details={
                    "artifact": "memory",
                    "tool": "write_memory",
                    "failure_type": "OSError",
                },
            ),
        ],
    )

    outcome = outcome_of(state)

    assert outcome.failed_publication_artifacts == ()
    assert outcome.failed_memory_writes == 2
    assert outcome.report_path == REPORT_PATH
    assert outcome.evidence_path == EVIDENCE_PATH
    assert outcome.quality_path == QUALITY_PATH


def test_the_outcome_reports_the_session_span_the_events_cover() -> None:
    """Elapsed time is read from the record, never from a clock."""
    timed = base_state(
        events=[
            ResearchEvent(
                event_type="graph.session.started",
                source="graph",
                message="Research session started.",
                timestamp="2026-09-13T09:00:00+00:00",
            ),
            ResearchEvent(
                event_type="graph.node.started",
                source="graph.planner",
                message="Node planner started.",
                timestamp="2026-09-13T09:00:05+00:00",
            ),
            ResearchEvent(
                event_type="graph.session.completed",
                source="graph",
                message="Research session completed.",
                timestamp="2026-09-13T09:02:30+00:00",
            ),
        ]
    )

    assert outcome_of(timed).duration_seconds == 150.0
    # One event is not a span, and no events is no record at all.
    assert outcome_of(base_state(events=[timed.events[0]])).duration_seconds is (
        None
    )
    assert outcome_of(base_state()).duration_seconds is None


def test_the_outcome_surfaces_the_semantic_review_beside_the_critic() -> None:
    """The review's own status and mean, and never a zero for "no review"."""
    reviewed = base_state(
        quality=quality_snapshot(
            semantic_review_status="scored",
            semantic_review_score=0.86,
            semantic_review_fingerprint="abc123def456",
        ),
        report_review=fake_report_review(),
    )
    unreviewed = base_state(quality=quality_snapshot())

    assert outcome_of(reviewed).semantic_review_status == "scored"
    assert outcome_of(reviewed).semantic_review_score == 0.86
    assert outcome_of(unreviewed).semantic_review_status == ""
    assert outcome_of(unreviewed).semantic_review_score is None
    assert outcome_of(base_state()).semantic_review_status == ""


def test_the_outcome_carries_the_quality_contract_version() -> None:
    """Which contract wrote this session travels with the outcome."""
    modern = base_state(quality_contract_version=QUALITY_CONTRACT_VERSION)

    assert outcome_of(modern).quality_contract_version == (
        QUALITY_CONTRACT_VERSION
    )
    assert outcome_of(base_state()).quality_contract_version == (
        LEGACY_QUALITY_CONTRACT_VERSION
    )


def test_the_outcome_reports_target_progress_apart_from_topic_progress() -> None:
    """Two denominators, two readings, never one blended ratio."""
    state = base_state(
        quality=quality_snapshot(
            planned_topics=7,
            covered_topics=3,
            substantive_covered_topics=3,
            substantive_topic_ratio=3 / 7,
            planned_targets=12,
            required_targets=9,
            answered_targets=6,
            critical_targets=4,
            unanswered_critical_target_ids=["t-crit-1"],
            unaccounted_target_ids=["t-req-2"],
        )
    )

    coverage = outcome_of(state).coverage

    assert coverage is not None
    assert coverage.planned_topics == 7
    assert coverage.covered_topics == 3
    assert coverage.substantive_topic_ratio == 3 / 7
    assert coverage.planned_targets == 12
    assert coverage.required_targets == 9
    assert coverage.answered_targets == 6
    assert coverage.critical_targets == 4
    assert coverage.answered_critical_targets == 3
    assert coverage.unanswered_critical_target_ids == ("t-crit-1",)
    assert coverage.unaccounted_target_ids == ("t-req-2",)
    # No quality pass judged this run, so no coverage is claimed for it.
    assert outcome_of(base_state()).coverage is None


def test_the_outcome_reports_the_substantive_topic_count() -> None:
    """``CoverageProgress.covered_topics`` is the measured reading.

    The snapshot carries two numerators because historical artifacts carry the
    claimed one; the outcome's field promises the substantive count — topics
    whose every counted required target is answered — so it reads the field
    that means that.
    """
    state = base_state(
        quality=quality_snapshot(
            planned_topics=1,
            covered_topics=1,
            substantive_covered_topics=0,
            substantive_topic_ratio=0.0,
            planned_targets=1,
            required_targets=1,
            answered_targets=0,
            unaccounted_target_ids=[],
        )
    )

    coverage = outcome_of(state).coverage

    assert coverage is not None
    assert coverage.planned_topics == 1
    assert coverage.covered_topics == 0
    assert coverage.substantive_topic_ratio == 0.0


def test_the_outcome_counts_reads_works_and_citations_apart() -> None:
    """Without a composition there is no reader index to count citations from."""
    state = base_state(quality=quality_snapshot())

    assert outcome_of(state).evidence_counts is None
