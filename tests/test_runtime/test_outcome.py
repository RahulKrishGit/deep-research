"""Tests for the finished-run summary every front-end reads."""

from __future__ import annotations

import re
from collections.abc import Sequence

from deep_research.agents.events import agent_event
from deep_research.agents.evidence import (
    resolve_read_works,
    resolve_retained_work_keys,
)
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report import render_written_report, written_citations
from deep_research.agents.researcher import sub_topic_completed_event
from deep_research.agents.steps import ReActRun
from deep_research.graph.events import report_published_event
from deep_research.graph.orchestrator import GraphRun
from deep_research.graph.state import graph_status
from deep_research.observability import TokenUsageMetric, ToolMetric
from deep_research.request_budget import (
    ProviderCategory,
    RequestBudgetSnapshot,
)
from deep_research.runtime.outcome import (
    CoverageProgress,
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
    REVIEW_DIMENSIONS,
    FactRow,
    FigureContext,
    FigureResult,
    Finding,
    FindingVerification,
    NotFoundTarget,
    ReadRecord,
    ReportComposition,
    ReportPoint,
    ReportQualitySnapshot,
    ReportReview,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ScoredSource,
    SubTopic,
)
from tests.evidence_fakes import figure, make_finding, make_read

QUESTION = "How mature is quantum error correction?"

REPORT_PATH = "output/report-session-1-0.md"
EVIDENCE_PATH = "output/report-session-1-0-evidence.md"
QUALITY_PATH = "output/report-session-1-0-quality.json"

# One answered target per sub-topic, and a third the pass could not answer.
ANSWERED_TARGET_IDS = ("topic-01-target-01", "topic-01-target-02")
MISSING_TARGET_ID = "topic-02-target-01"
REQUIRED_TARGET_IDS = (*ANSWERED_TARGET_IDS, MISSING_TARGET_ID)

OWNER = "U.S. Energy Information Administration"


def base_state(**overrides: object) -> ResearchState:
    payload: dict[str, object] = {
        "session_id": "session-1",
        "original_question": QUESTION,
    }
    payload.update(overrides)
    return ResearchState.model_validate(payload)


def per_pass_agent_event(path: str | None) -> object:
    """A per-pass agent record that names a path in its own metadata.

    The writer and the verifier announce what they produced, and neither
    record is the terminal publication: only the finalizer publishes, so the
    tests build this shape to prove the outcome never reads a path out of an
    agent's own report of its work.
    """
    return agent_event(
        agent_name="report_writer",
        event_type="report_writer.report.written",
        message="Wrote 3 statement(s) citing 2 source(s); 0 drafted point(s) refused.",
        metadata={"statements": 3, "citations": 2, "output_path": path},
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
    """The step-4 reading of one judged pass: ids and verified-finding counts.

    Only the readings this pipeline computes are named: the required targets'
    ids, the Evidence Verifier's own finding counts, and the two integrity
    invariants the summary prints. The retired readings the type still carries
    are left at their defaults, so a fixture cannot agree with itself by
    accident.
    """
    payload: dict[str, object] = {
        "required_target_ids": list(REQUIRED_TARGET_IDS),
        "answered_target_ids": list(ANSWERED_TARGET_IDS),
        "missing_required_target_ids": [MISSING_TARGET_ID],
        "verified_findings": 2,
        "dropped_findings": 1,
        "cited_findings": 2,
        "duplicate_fact_rows": 0,
        "uncited_settled_points": 0,
        "forecasts_without_release": 0,
    }
    payload.update(overrides)
    return ReportQualitySnapshot.model_validate(payload)


def kept_finding(
    snippet: str,
    *,
    value: str,
    unit: str,
    period: str,
    kind: str,
    target_ids: Sequence[str],
) -> Finding:
    """One finding the Evidence Verifier kept, figure and context included."""
    read = make_read()
    wanted = figure(value, unit, period, kind)
    return make_finding(
        read,
        snippet,
        figures=[wanted],
        target_ids=list(target_ids),
        verification=FindingVerification(
            status="verified",
            figure_results=[
                FigureResult(
                    figure=wanted,
                    matched=True,
                    context=FigureContext(
                        period=period,
                        attribution="own",
                        organisation=OWNER,
                        kind=kind,
                    ),
                    evidence_words=snippet,
                )
            ],
        ),
    )


def verified_findings() -> list[Finding]:
    """The two findings the pass kept, each answering one required target."""
    return [
        kept_finding(
            "Generators added 10.4 gigawatts (GW) of new battery storage "
            "capacity in 2024",
            value="10.4",
            unit="GW",
            period="2024",
            kind="actual",
            target_ids=["topic-01-target-01"],
        ),
        kept_finding(
            "capacity growth from battery storage could set a record as "
            "operators report plans to add 19.6 GW",
            value="19.6",
            unit="GW",
            period="2025",
            kind="forecast",
            target_ids=["topic-01-target-02"],
        ),
    ]


def dropped_finding() -> Finding:
    """One finding the verifier dropped, and why."""
    read = make_read()
    return make_finding(
        read,
        "Battery storage is the fastest-growing source on the grid.",
        verification=FindingVerification(
            status="dropped", dropped_reason="snippet_not_on_page"
        ),
    )


def judged_composition(**overrides: object) -> ReportComposition:
    """The pass's composition: the kept findings, and what it could not find."""
    payload: dict[str, object] = {
        "question": QUESTION,
        "session_id": "session-1",
        "findings": verified_findings(),
        "not_found": [
            NotFoundTarget(
                target_id=MISSING_TARGET_ID,
                question="How much battery storage is planned for 2025?",
                queries=["battery storage 2025 plans"],
                searched=True,
            )
        ],
    }
    payload.update(overrides)
    return ReportComposition.model_validate(payload)


def read_records(findings: Sequence[Finding]) -> dict[str, ReadRecord]:
    """The reads those findings were extracted from, keyed by their own id."""
    reads = {read.read_id: read for read in (make_read(),)}
    for finding in findings:
        reads.setdefault(finding.read_id, make_read())
    return reads


def verified_state(**overrides: object) -> ResearchState:
    """One judged pass over verified findings: the numbers a summary prints.

    Three findings — the two the verifier kept and the one it dropped — over a
    composition that answers two of the three required targets and lists the
    third under Not found. The pass spent no extra pass yet, so the run it
    belongs to is not accepted whatever it is judged against.
    """
    findings = [*verified_findings(), dropped_finding()]
    payload: dict[str, object] = {
        "verified_findings": findings,
        "read_records": read_records(findings),
        "composition": judged_composition(),
        "quality": quality_snapshot(),
    }
    payload.update(overrides)
    return base_state(**payload)


def counted_state(**overrides: object) -> ResearchState:
    """The same pass with every verified-finding reading a distinct number."""
    snapshot = quality_snapshot(
        verified_findings=3,
        corrected_findings=1,
        dropped_findings=2,
        context_unchecked_findings=4,
        cited_findings=2,
    )
    return verified_state(quality=snapshot, **overrides)


def scored_review(**overrides: object) -> ReportReview:
    """A scored terminal review, which is what an accepted run carries.

    Built here rather than imported from the shared graph fakes: the review
    record is the API and CLI's own reading surface, and this file pins what
    the outcome does with it.
    """
    payload: dict[str, object] = {
        "status": "scored",
        "dimensions": {name: 0.9 for name in REVIEW_DIMENSIONS},
        "reviewed_statement_ids": ["S001"],
        "per_statement_dispositions": {"S001": "supported"},
        "input_fingerprint": "packet-1",
        "composition_fingerprint": "composition-1",
    }
    payload.update(overrides)
    return ReportReview.model_validate(payload)


def outcome_of(state: ResearchState, *, status: str = "completed") -> ResearchOutcome:
    return build_outcome(
        GraphRun(
            session_id="session-1",
            state=state,
            status=status,
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

    A pass that wrote a file earlier must not be advertised once the terminal
    write failed.
    """
    state = base_state(
        events=[
            per_pass_agent_event(REPORT_PATH),
            publication_event(report_path=None),
        ]
    )

    assert report_path_from_state(state) is None


def test_a_lone_per_pass_agent_record_yields_no_report_path() -> None:
    """The deleted compatibility fallback must not come back.

    ``test_a_failed_terminal_report_write_never_falls_back`` pairs a per-pass
    agent record with a terminal ``publication_event(report_path=None)``,
    which returns ``None`` whether or not a fallback exists — the terminal
    record is read first either way. A state carrying *only* the agent's own
    record is the shape that distinguishes the two: with no publication record
    at all, that record's metadata must not be consulted.
    """
    state = base_state(events=[per_pass_agent_event(REPORT_PATH)])

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
            per_pass_agent_event(EVIDENCE_PATH),
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
                per_pass_agent_event(EVIDENCE_PATH),
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
    """No quality pass judged this run, so no verdict claims it was accepted."""
    assert outcome_of(base_state()).quality_status == QUALITY_STATUS_PARTIAL
    assert outcome_of(base_state()).accepted is False


def test_a_gate_failure_is_never_accepted() -> None:
    """A hard failure is a defect the report carries, whoever reviewed it."""
    failing = verified_state(
        quality=quality_snapshot(hard_failures=["unjudged_sentences"]),
        report_review=scored_review(),
    )

    assert outcome_of(failing).quality_status == QUALITY_STATUS_PARTIAL
    assert outcome_of(failing).accepted is False


def test_a_pass_with_an_extra_pass_left_is_not_accepted() -> None:
    """PD-23: the route is ``extra_pass``, so the report is not accepted yet.

    The fixture is the shape the reviewer node leaves: the deterministic pass
    measured one missing required target and the record names it (PD-5), with
    an extra pass still to spend. Acceptance is the router's own decision
    (``graph_quality_status``), which the outcome reads rather than re-derives.
    """
    judged = verified_state(
        report_review=scored_review(
            missing_required_target_ids=[MISSING_TARGET_ID]
        )
    )

    assert outcome_of(judged).accepted is False
    assert outcome_of(judged).quality_status == QUALITY_STATUS_PARTIAL


def test_a_spent_extra_pass_with_the_target_under_not_found_is_accepted() -> None:
    """PD-23: passes spent, gates clear, reviewer accepts -> completed.

    The missing target stays missing and stays disclosed — it is listed under
    Not found, which §6.4 accepts — so the run finishes ``completed`` with an
    accepted report rather than ``max_iterations``. The budget running out is a
    fact about the machine, not a defect in the report.

    The status asserted is ``graph_status``'s own reading of the judged state,
    not the status a fixture was handed: the outcome carries the run's status
    from its ``GraphRun``, so asking it here would assert the constructor.
    """
    judged = verified_state(
        iteration=1,
        max_extra_passes=1,
        report_review=scored_review(
            missing_required_target_ids=[MISSING_TARGET_ID]
        ),
    )

    outcome = outcome_of(judged)

    assert graph_status(judged) == "completed"
    assert outcome.quality_status == QUALITY_STATUS_ACCEPTED
    assert outcome.accepted is True
    assert outcome.failed is False
    assert outcome.coverage is not None
    assert outcome.coverage.missing_required_target_ids == (MISSING_TARGET_ID,)
    assert outcome.coverage.not_found_target_ids == (MISSING_TARGET_ID,)


def test_a_scored_review_that_did_not_pass_is_not_accepted() -> None:
    """A reviewer that judged the report and did not accept it is a verdict."""
    rejected = scored_review(
        dimensions={
            name: 0.5 for name in REVIEW_DIMENSIONS
        },
        missing_required_target_ids=[MISSING_TARGET_ID],
    )

    outcome = outcome_of(
        verified_state(iteration=1, max_extra_passes=1, report_review=rejected)
    )

    assert outcome.accepted is False
    assert outcome.quality_status == QUALITY_STATUS_PARTIAL


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
    failed memory write as an incomplete publication printed "Publication:
    incomplete … No artifact path is advertised" above all three advertised
    paths — and named ``memory`` once per failed write.
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


def test_the_outcome_surfaces_the_semantic_review_without_the_old_reviewer() -> None:
    """The review's own status and mean, and never a zero for "no review"."""
    reviewed = base_state(
        quality=quality_snapshot(
            semantic_review_status="scored",
            semantic_review_score=0.86,
            semantic_review_fingerprint="abc123def456",
        ),
        report_review=scored_review(),
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


# --- the verified findings, the targets and the not-found list ---------------


def test_coverage_and_evidence_counts_come_from_verified_findings() -> None:
    outcome = outcome_of(verified_state())

    assert (
        outcome.coverage.required_targets,
        outcome.coverage.answered_targets,
    ) == (3, 2)
    assert outcome.coverage.missing_required_target_ids == (
        "topic-02-target-01",
    )
    assert (
        outcome.evidence_counts.verified_findings,
        outcome.evidence_counts.dropped_findings,
    ) == (2, 1)


def test_the_target_counts_are_the_ids_the_gates_judged() -> None:
    """The counts are the length of the id lists, not the retired scalars.

    The quality pass of this pipeline records the required and answered target
    *ids*; the scalars beside them belong to the retired reading and stay at
    their defaults. Reading the scalars would publish "0/0 required targets
    answered" above a Not found list with an entry in it, so the counts are
    taken from the id lists — the same lists the gates and the extra-pass
    router read.
    """
    coverage = outcome_of(verified_state()).coverage

    assert coverage is not None
    assert (coverage.required_targets, coverage.answered_targets) == (3, 2)
    assert coverage.required_targets != 0
    assert coverage.answered_targets != 0


def test_the_not_found_target_ids_come_from_the_composition() -> None:
    """What the report could not answer is read from the report's own list."""
    listed = outcome_of(verified_state()).coverage
    unlisted = outcome_of(
        verified_state(composition=judged_composition(not_found=[]))
    ).coverage

    assert listed is not None
    assert listed.not_found_target_ids == ("topic-02-target-01",)
    assert unlisted is not None
    assert unlisted.not_found_target_ids == ()
    # The missing target stays missing either way: Not found explains it, and
    # does not answer it.
    assert unlisted.missing_required_target_ids == ("topic-02-target-01",)


def test_no_quality_pass_judged_is_no_coverage_at_all() -> None:
    """A run nothing measured prints no counts rather than zeroes."""
    assert outcome_of(base_state()).coverage is None


def test_the_evidence_counts_carry_the_verified_finding_readings() -> None:
    counts = outcome_of(counted_state()).evidence_counts

    assert counts is not None
    assert (
        counts.verified_findings,
        counts.corrected_findings,
        counts.dropped_findings,
        counts.context_unchecked_findings,
        counts.cited_findings,
    ) == (3, 1, 2, 4, 2)


def test_the_evidence_counts_carry_the_reads_and_the_citations() -> None:
    """Read calls, works, sources and findings stay four different numbers."""
    counts = outcome_of(verified_state()).evidence_counts

    assert counts is not None
    assert counts.read_records == 1
    assert counts.network_reads == 1
    assert counts.cache_reads == 0
    assert counts.unique_works == 0
    assert counts.findings == 2
    assert counts.cited_assessed_sources == 0


def test_the_evidence_counts_are_absent_without_a_judged_composition() -> None:
    """Two ways to have no counts, and both answer "not measured".

    Without a composition there is no reader index to count citations from;
    without a quality snapshot there is no verified-finding reading either.
    Either absence is reported as ``None`` rather than as a row of zeroes.
    """
    unjudged = verified_state(quality=None)
    uncomposed = verified_state(composition=None)

    assert outcome_of(unjudged).evidence_counts is None
    assert outcome_of(uncomposed).evidence_counts is None


def test_coverage_progress_is_a_frozen_reading_of_ids() -> None:
    """The dataclass publishes tuples, so a caller cannot edit the reading."""
    coverage = outcome_of(verified_state()).coverage

    assert coverage == CoverageProgress(
        required_targets=3,
        answered_targets=2,
        missing_required_target_ids=(MISSING_TARGET_ID,),
        not_found_target_ids=(MISSING_TARGET_ID,),
    )


# --- work identity and citation counts (Task 4.10d) ---------------------------
#
# Three work-identity records the claim-era quality record published, restored
# against the live counts. ``distinct_retention_counts`` (``agents/report.py``)
# is the helper ``EvidenceCounts`` reads, and ``resolve_retained_work_keys`` is
# the one keying its ``unique_works`` is the cardinality of — so a count that
# resolved identity a second way, or a map that dropped a URL, fails here.

GRID_URL = "https://grid.example.test/outlook-2024"
MIRROR_URL = "https://repository.example.test/grid-outlook-2024"
MIRROR_TWO_URL = "https://mirror.example.test/grid-outlook-2024"
QUEUE_URL = "https://queue.example.test/interconnection-2024"
KEY_FACTS_URL = "https://keyfacts.example.test/capacity"
UNCITED_URL = "https://uncited.example.test/background"

# The identity one assessment resolved for both copies of the outlook. The two
# reads share no byte, so only this persisted key joins them.
GRID_WORK_ID = "doi:10.1234/grid.2025"
KEY_FACTS_WORK_ID = "doi:10.1234/keyfacts.2024"

GRID_TEXT = "Grid Storage Outlook 2024: 10 GW in 2024."
MIRROR_TEXT = "Grid Storage Outlook 2024, re-typeset: 10 GW in 2024."
QUEUE_TEXT = "Interconnection Queue 2024: 800 MW."
KEY_FACTS_TEXT = "Battery storage additions reached 10.4 GW in 2024."


def assessed_source(
    url: str,
    *,
    title: str,
    work_id: str | None = None,
    transport: str = "unknown",
    status: str = "scored",
) -> ScoredSource:
    """One assessed source row: its full score set, and the work it resolved to."""
    scores = (
        {
            "authority_score": 0.8,
            "recency_score": 0.8,
            "relevance_score": 0.8,
            "overall_score": 0.8,
        }
        if status == "scored"
        else {}
    )
    return ScoredSource(
        url=url,
        title=title,
        rationale="Assessed from its own read.",
        work_id=work_id,
        transport_relation=transport,
        evaluation_status=status,
        **scores,
    )


def sourced_state(
    sources: Sequence[ScoredSource],
    reads: Sequence[ReadRecord],
    **composition_fields: object,
) -> ResearchState:
    """A judged pass over exactly these reads, assessing exactly these sources."""
    payload: dict[str, object] = {
        "read_records": {read.read_id: read for read in reads},
        "composition": judged_composition(
            sources=list(sources), **composition_fields
        ),
    }
    return verified_state(**payload)


def work_keys(state: ResearchState) -> dict[str, str]:
    """The keying the works count is the cardinality of, per retained URL."""
    composition = state.composition
    assert composition is not None
    return resolve_retained_work_keys(
        [source.url for source in composition.sources],
        state.read_records.values(),
        sources=composition.sources,
    )


def test_the_works_count_is_the_identity_the_work_map_publishes() -> None:
    """The count and the map cannot disagree about a work.

    An assessed source's ``work_id`` is resolved once per snapshot, with the
    anchors the Source Evaluator validated, and that is what the count reads.
    Counting the works a second way — from the reads alone, with no anchors —
    resolves *less*: the DOI that joined a copy to its original was validated
    as an anchor on the source, and a re-typeset mirror never shared the
    original's bytes, so the two reads resolve to two works. The run would
    then name one work and count two.
    """
    original = make_read(GRID_TEXT, url=GRID_URL, title="Grid Storage Outlook 2024")
    mirror = make_read(
        MIRROR_TEXT,
        url=MIRROR_URL,
        title="Grid Storage Outlook 2024 (repository copy)",
    )
    # The reads alone cannot see the join, which is why the count may not be
    # re-derived from them: two reads, two keys.
    assert len(set(resolve_read_works([original, mirror]).values())) == 2

    state = sourced_state(
        [
            assessed_source(
                GRID_URL, title="Grid Storage Outlook 2024", work_id=GRID_WORK_ID
            ),
            assessed_source(
                MIRROR_URL,
                title="Grid Storage Outlook 2024 (repository copy)",
                work_id=GRID_WORK_ID,
                transport="mirror",
            ),
        ],
        [original, mirror],
    )
    keys = work_keys(state)
    counts = outcome_of(state).evidence_counts

    assert set(keys.values()) == {GRID_WORK_ID}
    assert counts is not None
    assert counts.unique_works == len(set(keys.values()))
    # Two retained source URLs, one work.
    assert counts.source_urls == 2
    assert counts.unique_works == 1


def test_the_work_map_accounts_for_every_retained_source_url() -> None:
    """The map is the count's own keying, so the two cannot disagree.

    Every retained URL has exactly one key — the persisted identity an
    assessment established, or the key the URL's own read supports — so
    ``unique_works`` is that map's distinct values by construction. Publishing
    only the established identities, and reading a count off the reads, is how
    an identity-less read once became a work the map never named.
    """
    original = make_read(GRID_TEXT, url=GRID_URL, title="Grid Storage Outlook 2024")
    unlabelled = make_read(
        MIRROR_TEXT,
        url=MIRROR_URL,
        title="Grid Storage Outlook 2024 (repository copy)",
    )
    state = sourced_state(
        [
            assessed_source(
                GRID_URL, title="Grid Storage Outlook 2024", work_id=GRID_WORK_ID
            ),
            assessed_source(
                MIRROR_URL,
                title="Grid Storage Outlook 2024 (repository copy)",
            ),
        ],
        [original, unlabelled],
    )
    keys = work_keys(state)
    counts = outcome_of(state).evidence_counts

    assert set(keys) == {GRID_URL, MIRROR_URL}
    assert counts is not None
    assert counts.unique_works == len(set(keys.values()))
    # Two different documents and no shared alias, one of them assessed
    # without an identity: one resolved work, one unresolved entry.
    assert counts.unique_works == 2


def test_a_source_no_assessment_covers_is_its_own_work_entry() -> None:
    """An unresolved work stays its own entry; it is never folded into a peer.

    The source the per-run cap left unscored has no persisted identity, so its
    URL keeps the key its own read supports rather than the key of whatever
    source sits beside it — and it stays accounted for: a source the run read
    is a work it retains whether or not anyone assessed it.
    """
    reads = [
        make_read(GRID_TEXT, url=GRID_URL, title="Grid Storage Outlook 2024"),
        make_read(
            MIRROR_TEXT,
            url=MIRROR_URL,
            title="Grid Storage Outlook 2024 (repository copy)",
        ),
        make_read(QUEUE_TEXT, url=QUEUE_URL, title="Interconnection Queue 2024"),
    ]
    state = sourced_state(
        [
            assessed_source(
                GRID_URL, title="Grid Storage Outlook 2024", work_id=GRID_WORK_ID
            ),
            assessed_source(
                MIRROR_URL,
                title="Grid Storage Outlook 2024 (repository copy)",
                work_id=GRID_WORK_ID,
                transport="mirror",
            ),
            assessed_source(
                QUEUE_URL,
                title="Interconnection Queue 2024",
                status="unscored_cap",
            ),
        ],
        reads,
    )
    keys = work_keys(state)
    counts = outcome_of(state).evidence_counts

    # The map accounts for every retained URL, the unscored one included, so
    # the count is exactly its distinct values.
    assert set(keys) == {GRID_URL, MIRROR_URL, QUEUE_URL}
    assert counts is not None
    assert counts.unique_works == len(set(keys.values()))
    # One joined work, plus the unassessed source's own unresolved entry.
    assert counts.unique_works == 2
    assert counts.assessed_sources == 3


def test_the_cited_count_is_what_the_published_report_cites() -> None:
    """P2-1: ``cited_assessed_sources`` is the report's own "N sources cited".

    The count intersects the assessed URLs with exactly the pages the written
    report cites: everything its points, its sections and its Key facts rows
    reference, with mirror copies never merged. A source cited only through a
    Key facts row therefore counts as cited, three URLs of one work are three
    cited sources, and an assessed source the report never cites is not
    counted at all.
    """
    key_read = make_read(
        KEY_FACTS_TEXT, url=KEY_FACTS_URL, title="Battery capacity additions"
    )
    key_finding = make_finding(
        key_read, KEY_FACTS_TEXT, figures=[figure("10.4", "GW", "2024", "actual")]
    )
    state = sourced_state(
        [
            assessed_source(
                GRID_URL, title="Grid Storage Outlook 2024", work_id=GRID_WORK_ID
            ),
            assessed_source(
                MIRROR_URL,
                title="Grid Storage Outlook 2024 (repository copy)",
                work_id=GRID_WORK_ID,
                transport="mirror",
            ),
            assessed_source(
                MIRROR_TWO_URL,
                title="Grid Storage Outlook 2024 (second repository copy)",
                work_id=GRID_WORK_ID,
                transport="mirror",
            ),
            assessed_source(
                KEY_FACTS_URL,
                title="Battery capacity additions",
                work_id=KEY_FACTS_WORK_ID,
            ),
            assessed_source(UNCITED_URL, title="Background"),
        ],
        [make_read(GRID_TEXT, url=GRID_URL, title="Grid Storage Outlook 2024"), key_read],
        findings=[key_finding],
        fact_rows=[
            FactRow(
                row_id="K001",
                organisation="Example Laboratory",
                attribution="own",
                measure="battery storage power capacity added",
                period="2024",
                value="10.4 GW",
                kind="actual",
                finding_id=finding_fingerprint(key_finding),
            )
        ],
        summary=[
            ReportPoint(
                text="Generators added 10 GW of battery storage capacity in 2024.",
                source_urls=[GRID_URL, MIRROR_URL, MIRROR_TWO_URL],
            )
        ],
    )
    composition = state.composition
    counts = outcome_of(state).evidence_counts

    assert composition is not None
    index = written_citations(composition)
    assert [citation.url for citation in index] == [
        GRID_URL,
        MIRROR_URL,
        MIRROR_TWO_URL,
        KEY_FACTS_URL,
    ]
    assert counts is not None
    assert counts.cited_assessed_sources == len(index)
    assert counts.cited_assessed_sources == 4

    # The report's own header prints that same number.
    cited_line = re.search(r"\b(\d+) sources cited\b", render_written_report(composition))
    assert cited_line is not None
    assert int(cited_line.group(1)) == counts.cited_assessed_sources

    # It is a citation count, not a works count and not the assessed total:
    # the three mirror copies are one work, and the uncited source is counted
    # only as assessed.
    assert counts.unique_works == 3
    assert counts.assessed_sources == 5
