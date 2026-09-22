"""Tests for what the CLI prints."""

from __future__ import annotations

import io

from deep_research.agents.events import agent_event
from deep_research.agents.researcher import (
    sub_topic_completed_event,
    sub_topic_skipped_error,
)
from deep_research.agents.steps import ReActRun
from deep_research.cli import (
    ProgressStream,
    render_progress,
    render_summary,
    render_warnings,
)
from deep_research.graph.errors import (
    publication_write_error,
    report_review_unavailable_error,
)
from deep_research.graph.events import (
    node_completed_event,
    node_started_event,
    refinement_started_event,
    route_decided_event,
    session_completed_event,
    session_started_event,
)
from deep_research.graph.orchestrator import GraphRun
from deep_research.observability import TokenUsage
from deep_research.request_budget import (
    ProviderCategory,
    RequestBudgetSnapshot,
)
from deep_research.runtime.outcome import ResearchOutcome, ToolCallSummary
from deep_research.runtime.outcome import build_outcome as real_build_outcome
from deep_research.utils.types import (
    Claim,
    Critique,
    CritiqueGap,
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
from tests.graph_fakes import fake_report_review

QUESTION = "How mature is quantum error correction?"

REPORT_PATH = "output/report-session-1-0.md"
EVIDENCE_PATH = "output/report-session-1-0-evidence.md"
QUALITY_PATH = "output/report-session-1-0-quality.json"


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
        "report_path": REPORT_PATH,
        "token_usage": TokenUsage(),
        "tool_calls": (),
    }
    defaults.update(overrides)
    return ResearchOutcome(**defaults)


def quality_state(
    *,
    critique: Critique | None = None,
    quality: ReportQualitySnapshot | None = None,
    with_critique: bool = True,
    report_review: ReportReview | None = None,
) -> ResearchState:
    """One composed, judged pass: the numbers the summary must print.

    Carries a scored semantic review by default, because since Task 10 a pass
    with no review is never accepted — a test that wants the unreviewed reading
    passes ``report_review=None`` explicitly and says so.
    """
    return ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        report_review=(
            fake_report_review() if report_review is None else report_review
        ),
        quality=quality
        if quality is not None
        else quality_snapshot(),
        critique=(
            critique if critique is not None else gap_critique()
        )
        if with_critique
        else None,
    )


def quality_snapshot(**overrides: object) -> ReportQualitySnapshot:
    payload: dict[str, object] = {
        "coverage_ratio": 3 / 7,
        "planned_topics": 7,
        "covered_topics": 3,
        "unresolved_topic_ids": [
            "topic-03",
            "topic-05",
            "topic-06",
            "topic-07",
        ],
        "unique_findings": 10,
        "unique_sources": 14,
        "cited_sources": 12,
        "scored_cited_source_ratio": 10 / 12,
        "verified_claims": 14,
        "contradicted_claims": 1,
        "duplicate_claims": 0,
        "duplicate_source_rows": 0,
        "uncited_settled_points": 0,
    }
    payload.update(overrides)
    return ReportQualitySnapshot.model_validate(payload)


def gap_critique() -> Critique:
    """A model review that asked for more work, so the run is partial."""
    return Critique(
        score=6,
        gaps=[],
        unsupported_claims=[],
        recommended_queries=["qec cost 2025"],
        should_continue=True,
        rationale="Recorded for renderer tests.",
    )


def accepting_critique() -> Critique:
    return Critique(
        score=6,
        gaps=[],
        unsupported_claims=[],
        recommended_queries=[],
        should_continue=False,
        rationale="Recorded for renderer tests.",
    )


def error_state() -> ResearchState:
    return ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        errors=[
            ResearchError(
                error_type="researcher_sub_topic_skipped",
                source="agent.researcher",
                message=(
                    "A planned sub-topic was never researched; the report "
                    "will be incomplete for it."
                ),
                details={"coverage_id": "topic-03", "reason": "cap"},
            ),
            ResearchError(
                error_type="researcher_extraction_provider_error",
                source="agent.researcher",
                message="The model provider failed during research.",
                details={"coverage_id": "topic-05"},
            ),
            ResearchError(
                error_type="web_search_failed",
                source="tools.web_search",
                message="The search provider timed out.",
                details={"tool": "web_search"},
            ),
        ],
    )


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


def index_of(lines: list[str], prefix: str) -> int:
    return next(
        index for index, line in enumerate(lines) if line.startswith(prefix)
    )


# --- live progress ---------------------------------------------------------


def test_progress_returns_the_line_for_one_plain_event() -> None:
    line = render_progress(node_started_event("planner", iteration=0), verbose=False)

    assert line == "  [0] Node planner started."


def test_plain_progress_streams_every_allowed_event_type() -> None:
    assert render_progress(
        session_started_event(
            session_id="session-1", max_iterations=2, checkpointing=False
        ),
        verbose=False,
    )
    assert render_progress(
        refinement_started_event(iteration=1, max_iterations=2), verbose=False
    )
    assert render_progress(
        route_decided_event(
            destination="refine",
            reason="refinement_requested",
            iteration=1,
            max_iterations=2,
            should_continue=True,
        ),
        verbose=False,
    )


def test_progress_skips_span_lifecycle_events_in_both_modes() -> None:
    span = ResearchEvent(
        event_type="observability.span.started",
        source="observability",
        message="agent.planner started.",
    )

    assert render_progress(span, verbose=False) is None
    assert render_progress(span, verbose=True) is None


def test_plain_progress_skips_an_agent_completion() -> None:
    completed = node_completed_event(
        "planner", iteration=0, event_count=2, error_count=0
    )

    assert render_progress(completed, verbose=False) is None
    assert (
        render_progress(completed, verbose=True)
        == "  [0] Node planner completed."
    )


def test_verbose_progress_streams_an_agents_own_completion() -> None:
    completed = agent_event(
        agent_name="researcher",
        event_type="researcher.research.completed",
        message="Research pass complete.",
        metadata={"iteration": 1, "finding_count": 4},
    )

    assert render_progress(completed, verbose=False) is None
    assert render_progress(completed, verbose=True) == (
        "  [1] Research pass complete."
    )


def test_verbose_progress_streams_the_enumerated_provider_failure() -> None:
    failure = agent_event(
        agent_name="critic",
        event_type="agent.provider_failure",
        message="The model provider failed during the review.",
        metadata={"iteration": 0},
    )

    assert render_progress(failure, verbose=False) is None
    assert render_progress(failure, verbose=True) == (
        "  [0] The model provider failed during the review."
    )


def test_verbose_progress_streams_nothing_else() -> None:
    """Verbose adds completions and typed errors, never every agent record."""
    started = agent_event(
        agent_name="researcher",
        event_type="researcher.sub_topic.started",
        message="Sub-topic research started.",
        metadata={"iteration": 0},
    )
    tool_call = agent_event(
        agent_name="researcher",
        event_type="researcher.tool_call",
        message="The researcher called web_search.",
        metadata={"iteration": 0},
    )

    assert render_progress(started, verbose=True) is None
    assert render_progress(tool_call, verbose=True) is None


# --- the debug event log ---------------------------------------------------


def test_debug_events_streams_every_recorded_event_type() -> None:
    """The debug log is the complete event record, not a filtered subset.

    A record neither the plain stream nor ``--verbose`` shows — a mid-agent
    progress event — is exactly what the debug surface exists to reveal, and
    it is printed with the enumerated type and source that identify it.
    """
    tool_call = agent_event(
        agent_name="researcher",
        event_type="researcher.tool_call",
        message="The researcher called web_search.",
        metadata={"iteration": 2},
    )

    assert render_progress(tool_call, verbose=False, debug=False) is None
    assert render_progress(tool_call, verbose=True, debug=False) is None
    assert render_progress(tool_call, verbose=False, debug=True) == (
        "  [2] researcher.tool_call (agent.researcher): "
        "The researcher called web_search."
    )


def test_debug_events_streams_nothing_twice() -> None:
    """One record, one line: debug never reprints what verbose already did.

    Both switches on is the composed surface — the record appears once, in the
    debug form that identifies it, rather than once per switch.
    """
    completed = node_completed_event(
        "planner", iteration=1, event_count=2, error_count=0
    )
    stream = io.StringIO()
    handler = ProgressStream(stream, verbose=True, debug=True)

    handler(completed)

    lines = [line for line in stream.getvalue().splitlines() if line]
    assert lines == [
        "  [1] graph.node.completed (graph.planner): Node planner completed."
    ]
    assert render_progress(completed, verbose=False, debug=True) == lines[0]


def test_debug_events_keeps_the_span_lifecycle_out() -> None:
    """Two records per span would bury the log; they stay excluded."""
    span = ResearchEvent(
        event_type="observability.span.started",
        source="observability",
        message="agent.planner started.",
    )

    assert render_progress(span, verbose=False, debug=True) is None
    assert render_progress(span, verbose=True, debug=True) is None


def test_debug_events_never_print_event_metadata() -> None:
    """Bounded fields only: no metadata value, URL, or query can reach stdout."""
    event = agent_event(
        agent_name="researcher",
        event_type="researcher.tool_call",
        message="The researcher called web_search.",
        metadata={
            "iteration": 0,
            "url": "https://example.invalid/secret-page",
            "query": "confidential search query",
        },
    )

    line = render_progress(event, verbose=False, debug=True)

    assert line is not None
    assert "https://" not in line
    assert "secret-page" not in line
    assert "confidential search query" not in line


# --- grouped warnings ------------------------------------------------------


def test_warnings_group_by_the_source_that_recorded_them() -> None:
    lines = render_warnings(build_outcome(state=error_state()))

    assert lines == [
        "Warnings: 3 errors (0 recovered, 3 non-fatal, 0 fatal)",
        "  agent.researcher: 2 errors (coverage topic-03, topic-05)",
        "    researcher_sub_topic_skipped (non-fatal; reason cap): topic-03",
        "    researcher_extraction_provider_error (non-fatal): topic-05",
        "  tools.web_search: 1 error",
        "    web_search_failed (non-fatal)",
    ]


def test_warnings_omit_the_coverage_fragment_when_none_is_named() -> None:
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

    assert render_warnings(build_outcome(state=state)) == [
        "Warnings: 1 error (0 recovered, 1 non-fatal, 0 fatal)",
        "  tools.web_search: 1 error",
        "    web_search_failed (non-fatal)",
    ]


def test_a_recoverable_failure_is_not_printed_as_recovered() -> None:
    """``recoverable`` means the run continued, not that anything resolved it.

    Both records below come from the production constructors for failures a
    run survives without any resolution: a terminal semantic review whose
    provider failed, and the terminal quality write that withheld the whole
    advertised artifact set. Nothing recovered either one, and calling them
    recovered would publish a judgement no producer made. ``recoverable`` is
    the producer saying the run carried on, which is what is printed.
    """
    state = ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        errors=[
            report_review_unavailable_error(
                node="review_report",
                review_status="provider_failed",
                reason="provider_error",
            ),
            publication_write_error(
                node="finalize_report",
                artifact="quality",
                tool="write_document",
                failure_type="OSError",
            ),
        ],
    )

    lines = render_warnings(build_outcome(state=state))
    joined = "\n".join(lines)

    assert lines[0] == "Warnings: 2 errors (0 recovered, 2 non-fatal, 0 fatal)"
    assert "(recovered" not in joined
    assert (
        "graph_report_review_unavailable (non-fatal; reason provider_error)"
        in joined
    )
    assert "graph_publication_failed (non-fatal; failure_type OSError)" in joined


def test_only_a_recorded_resolution_prints_as_recovered() -> None:
    """A resolution marker is the one thing that earns the word ``recovered``.

    No producer stamps one today, and a run that recorded none prints none:
    the count follows the same rule as the phrase, so a header can never
    report a recovery the records do not carry.
    """
    resolved = ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        errors=[
            ResearchError(
                error_type="web_scraper_failed",
                source="tools.web_scraper",
                message="The scraper fell back to a cached body.",
                recoverable=True,
                details={"resolved_by": "validated_cache_read"},
            )
        ],
    )
    fatal = resolved.model_copy(
        update={
            "errors": [
                resolved.errors[0].model_copy(
                    update={"recoverable": False, "details": {}}
                )
            ]
        }
    )

    assert render_warnings(build_outcome(state=resolved)) == [
        "Warnings: 1 error (1 recovered, 0 non-fatal, 0 fatal)",
        "  tools.web_scraper: 1 error",
        "    web_scraper_failed (recovered)",
    ]
    assert render_warnings(build_outcome(state=fatal))[0] == (
        "Warnings: 1 error (0 recovered, 0 non-fatal, 1 fatal)"
    )


def test_an_unresolved_access_problem_names_the_question_it_left_open() -> None:
    """The id alone is not the missing question; the plan's title is."""
    state = ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        sub_topics=[
            SubTopic(
                coverage_id="topic-03",
                title="Siting, permitting, and fire safety rules",
                rationale="It changes the decision surface.",
                search_queries=["NFPA 855 storage siting 2026"],
                success_criteria=["A read source answers it."],
                priority=1,
            )
        ],
        errors=[
            ResearchError(
                error_type="researcher_extraction_provider_error",
                source="agent.researcher",
                message="The model provider failed during research.",
                recoverable=False,
                details={"coverage_id": "topic-03", "reason": "provider_timeout"},
            )
        ],
    )

    lines = render_warnings(build_outcome(state=state))
    joined = "\n".join(lines)

    assert lines[0] == "Warnings: 1 error (0 recovered, 0 non-fatal, 1 fatal)"
    assert (
        'researcher_extraction_provider_error (fatal; reason '
        'provider_timeout): topic-03 "Siting, permitting, and fire safety rules"'
        in joined
    )


def test_repeated_identical_errors_collapse_to_one_aggregated_line() -> None:
    """Five occurrences of one failure are one line with a count, not five."""
    state = ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        errors=[
            ResearchError(
                error_type="web_search_failed",
                source="tools.web_search",
                message="The search provider timed out.",
                details={"reason": "provider_timeout"},
            )
            for _ in range(5)
        ],
    )

    lines = render_warnings(build_outcome(state=state))

    assert lines == [
        "Warnings: 5 errors (0 recovered, 5 non-fatal, 0 fatal)",
        "  tools.web_search: 5 errors",
        "    web_search_failed (x5; non-fatal; reason provider_timeout)",
    ]


def test_no_errors_means_no_warnings() -> None:
    assert render_warnings(build_outcome()) == []


def test_a_topic_that_met_its_obligations_is_not_a_never_researched_warning() -> (
    None
):
    """Prior completion, deferral and a stopped pass are three readings.

    ``researcher_sub_topic_skipped`` carries one written message for all four
    of its reasons, and that message says "never researched". A topic whose
    required targets are already answered by reader statements was researched
    — on an earlier pass — and counting it as an error under its coverage id
    reports a coverage loss the run does not have. The deferred and the
    never-attempted skips stay warnings, each with its own reading.
    """
    state = ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        sub_topics=[
            _topic("topic-01", "Grid-scale storage costs"),
            _topic("topic-04", "Interconnection queues"),
            _topic("topic-06", "Retirement schedules"),
        ],
        errors=[
            _skip_error("topic-01", "required_targets_completed"),
            _skip_error("topic-04", "cap"),
            _skip_error("topic-06", "provider_failure_stopped_processing"),
        ],
    )

    assert render_warnings(build_outcome(state=state)) == [
        "Warnings: 2 errors (0 recovered, 2 non-fatal, 0 fatal)",
        "  agent.researcher: 2 errors (coverage topic-04, topic-06)",
        '    researcher_sub_topic_skipped (non-fatal; reason cap): topic-04 '
        '"Interconnection queues"',
        "    researcher_sub_topic_skipped "
        "(non-fatal; reason provider_failure_stopped_processing): topic-06 "
        '"Retirement schedules"',
        "Skipped: 1 sub-topic that owed nothing this pass: topic-01 "
        '"Grid-scale storage costs"',
    ]

    assert render_warnings(build_outcome(state=state), verbose=True) == [
        "Warnings: 2 errors (0 recovered, 2 non-fatal, 0 fatal)",
        "  agent.researcher: 2 errors (coverage topic-04, topic-06)",
        '    researcher_sub_topic_skipped (non-fatal; reason cap): topic-04 '
        '"Interconnection queues"',
        "    researcher_sub_topic_skipped "
        "(non-fatal; reason provider_failure_stopped_processing): topic-06 "
        '"Retirement schedules"',
        "    warning: [researcher_sub_topic_skipped] This planned sub-topic "
        "was deferred: the pass reached its sub-topic limit before its turn "
        "came up.",
        "    warning: [researcher_sub_topic_skipped] A planned sub-topic was "
        "never researched; a provider failure stopped the pass before it "
        "could run.",
        "Skipped: 1 sub-topic that owed nothing this pass: topic-01 "
        '"Grid-scale storage costs"',
        "    note: [researcher_sub_topic_skipped] This planned sub-topic "
        "already met its required targets on an earlier pass, so no new "
        "research was owed for it.",
    ]


def _topic(coverage_id: str, title: str) -> SubTopic:
    return SubTopic(
        coverage_id=coverage_id,
        title=title,
        rationale="It decides the comparison.",
        search_queries=["grid storage cost 2026"],
        success_criteria=["A read source answers it."],
        priority=1,
    )


def _skip_error(coverage_id: str, reason: str) -> ResearchError:
    """One ``researcher_sub_topic_skipped`` record, from the real producer."""
    return sub_topic_skipped_error(
        _topic(coverage_id, "A planned sub-topic"), reason=reason
    )


def test_a_memory_write_failure_does_not_withhold_the_artifact_paths() -> None:
    """Memory is a separate write; its failure is not an incomplete set.

    Three document writes succeeded and two claim writes to memory failed. The
    summary called the publication incomplete, said no artifact path was
    advertised, and then printed all three paths — and the failure list read
    "memory, memory". Memory is not part of the set whose completeness gates
    the paths (``nodes`` attempts it only for an accepted report, after the
    three documents), so the paths stand and the memory failures are counted
    under their own name.
    """
    state = ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        errors=[
            publication_write_error(
                node="finalize_report",
                artifact="memory",
                tool="write_memory",
                failure_type="OSError",
            ),
            publication_write_error(
                node="finalize_report",
                artifact="memory",
                tool="write_memory",
                failure_type="OSError",
            ),
        ],
    )

    lines = render_summary(
        build_outcome(
            state=state,
            evidence_path=EVIDENCE_PATH,
            quality_path=QUALITY_PATH,
        ),
        verbose=False,
    )
    joined = "\n".join(lines)

    assert "Publication: incomplete" not in joined
    assert "not advertised" not in joined
    assert f"Report: {REPORT_PATH}" in joined
    assert f"Evidence ledger: {EVIDENCE_PATH}" in joined
    assert f"Quality record: {QUALITY_PATH}" in joined
    assert "Memory: 2 claim writes to memory failed" in joined


def test_a_failed_document_write_withholds_every_artifact_path() -> None:
    """The set is published whole or advertised not at all.

    A document failure is the case the withholding sentence is for: the
    sibling writes may have left files on disk, and the summary must not
    advertise any of the three.
    """
    state = ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        errors=[
            publication_write_error(
                node="finalize_report",
                artifact="quality",
                tool="write_document",
                failure_type="OSError",
            )
        ],
    )

    lines = render_summary(
        build_outcome(
            state=state,
            # A failed document write nulls all three paths at the seam
            # (``nodes``), which is the shape the summary renders from.
            report_path=None,
            evidence_path=None,
            quality_path=None,
        ),
        verbose=False,
    )
    joined = "\n".join(lines)

    assert "Publication: incomplete; these writes failed: quality." in joined
    assert f"Report: {REPORT_PATH}" not in joined
    assert f"Evidence ledger: {EVIDENCE_PATH}" not in joined
    assert f"Quality record: {QUALITY_PATH}" not in joined
    assert joined.count("not advertised") == 3


def test_verbose_warnings_add_the_typed_messages() -> None:
    lines = render_warnings(build_outcome(state=error_state()), verbose=True)

    assert lines == [
        "Warnings: 3 errors (0 recovered, 3 non-fatal, 0 fatal)",
        "  agent.researcher: 2 errors (coverage topic-03, topic-05)",
        "    researcher_sub_topic_skipped (non-fatal; reason cap): topic-03",
        "    researcher_extraction_provider_error (non-fatal): topic-05",
        "    warning: [researcher_sub_topic_skipped] This planned sub-topic "
        "was deferred: the pass reached its sub-topic limit before its turn "
        "came up.",
        "    warning: [researcher_extraction_provider_error] The model "
        "provider failed during research.",
        "  tools.web_search: 1 error",
        "    web_search_failed (non-fatal)",
        "    warning: [web_search_failed] The search provider timed out.",
    ]


# --- the summary -----------------------------------------------------------


def test_the_summary_leads_with_the_quality_block() -> None:
    outcome = build_outcome(
        state=quality_state(), evidence_path=EVIDENCE_PATH
    )

    lines = render_summary(outcome, verbose=False)
    joined = "\n".join(lines)

    assert (
        "Quality: partial (critic 6/10; 3/7 topics covered, 43%)" in joined
    )
    assert (
        "Evidence: 12 cited sources; 10 scored; 14 verified, 1 contradicted"
        in joined
    )
    assert (
        "Integrity: 0 duplicate claims; 0 duplicate source rows; "
        "0 uncited settled points" in joined
    )
    assert "Open coverage: topic-03, topic-05, topic-06, topic-07" in joined
    assert f"Report: {REPORT_PATH}" in joined
    assert f"Evidence ledger: {EVIDENCE_PATH}" in joined


def test_the_summary_prints_identity_and_status_before_the_quality_block() -> None:
    outcome = build_outcome(
        state=quality_state(), evidence_path=EVIDENCE_PATH
    )

    lines = render_summary(outcome, verbose=False)

    assert index_of(lines, "Session ID:") < index_of(lines, "Status:")
    assert index_of(lines, "Status:") < index_of(lines, "Quality:")
    assert index_of(lines, "Quality:") < index_of(lines, "Evidence:")
    assert index_of(lines, "Evidence:") < index_of(lines, "Integrity:")
    assert index_of(lines, "Integrity:") < index_of(lines, "Open coverage:")
    assert index_of(lines, "Open coverage:") < index_of(lines, "Report:")
    assert index_of(lines, "Report:") < index_of(lines, "Evidence ledger:")


def test_the_critic_fragment_is_omitted_without_a_model_review() -> None:
    """Step 3: the critic fragment needs a model review to exist."""
    outcome = build_outcome(
        state=quality_state(
            with_critique=False,
            quality=quality_snapshot(
                coverage_ratio=1.0,
                planned_topics=2,
                covered_topics=2,
                unresolved_topic_ids=[],
            ),
        )
    )

    joined = "\n".join(render_summary(outcome, verbose=False))

    assert "Quality: partial (2/2 topics covered, 100%)" in joined
    assert "critic 6/10" not in joined
    assert "/10" not in joined


def test_an_accepted_run_says_accepted() -> None:
    outcome = build_outcome(
        state=quality_state(critique=accepting_critique())
    )

    joined = "\n".join(render_summary(outcome, verbose=False))

    assert "Quality: accepted (critic 6/10; 3/7 topics covered, 43%)" in joined


def test_the_quality_line_stands_alone_without_a_snapshot() -> None:
    """No quality pass judged this run: no counts are invented for it."""
    lines = render_summary(build_outcome(state=progress_state()), verbose=False)
    joined = "\n".join(lines)

    assert "Quality: partial" in joined
    assert "Evidence:" not in joined
    assert "Integrity:" not in joined
    assert "Open coverage:" not in joined


def test_open_coverage_is_omitted_when_nothing_is_open() -> None:
    outcome = build_outcome(
        state=quality_state(
            quality=quality_snapshot(
                coverage_ratio=1.0, covered_topics=7, unresolved_topic_ids=[]
            )
        )
    )

    joined = "\n".join(render_summary(outcome, verbose=False))

    assert "Open coverage:" not in joined


def test_a_missing_evidence_ledger_file_says_so() -> None:
    outcome = build_outcome(state=quality_state(), evidence_path=None)

    joined = "\n".join(render_summary(outcome, verbose=False))

    assert "Evidence ledger: not written to disk" in joined


def test_the_scored_count_comes_back_exactly() -> None:
    """``scored_cited_source_ratio`` is ``scored / cited`` by construction."""
    exact = build_outcome(
        state=quality_state(
            quality=quality_snapshot(
                cited_sources=3, scored_cited_source_ratio=2 / 3
            )
        )
    )
    none_cited = build_outcome(
        state=quality_state(
            quality=quality_snapshot(
                cited_sources=0, scored_cited_source_ratio=0.0
            )
        )
    )

    assert "3 cited sources; 2 scored;" in "\n".join(
        render_summary(exact, verbose=False)
    )
    assert "0 cited sources; 0 scored;" in "\n".join(
        render_summary(none_cited, verbose=False)
    )


def test_the_summary_names_the_session_status_and_report() -> None:
    lines = render_summary(build_outcome(), verbose=False)

    joined = "\n".join(lines)
    assert "Session ID: session-1" in joined
    assert "Status: completed" in joined
    assert f"Report: {REPORT_PATH}" in joined
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


def sub_topic_event(duplicates: int, beyond_cap: int) -> ResearchEvent:
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


def test_verbose_tool_totals_label_retries_beside_calls_and_failures() -> None:
    outcome = build_outcome(
        tool_calls=(
            ToolCallSummary(
                tool_name="web_search", calls=4, failures=1, retries=2
            ),
            ToolCallSummary(tool_name="read_url", calls=2, failures=0),
            ToolCallSummary(
                tool_name="document_reader", calls=1, failures=0, retries=1
            ),
        )
    )

    joined = "\n".join(render_summary(outcome, verbose=True))

    assert "web_search: 4 calls (1 failed, 2 retries)" in joined
    assert "document_reader: 1 calls (1 retry)" in joined
    # A tool that never retried carries no retry label at all: the retry count
    # is its own number rather than a suffix every line wears.
    assert "read_url: 2 calls" in joined
    assert "read_url: 2 calls (" not in joined


def test_verbose_totals_label_the_proposals_the_researcher_dropped() -> None:
    state = ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        events=[sub_topic_event(2, 1), sub_topic_event(0, 4)],
    )

    joined = "\n".join(render_summary(build_outcome(state=state), verbose=True))

    assert (
        "Dropped proposals: 7 "
        "(2 duplicate findings, 5 past the per-sub-topic cap)" in joined
    )


def test_verbose_totals_invent_no_drop_count_the_records_do_not_carry() -> None:
    """Absent and zero are different readings of the same line.

    No researcher sub-topic completion is no record at all, so the line is
    absent rather than a zero the run never measured; a recorded pass that
    dropped nothing says ``none``, which is a measurement.
    """
    without_records = "\n".join(render_summary(build_outcome(), verbose=True))
    assert "Dropped proposals" not in without_records

    state = ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        events=[sub_topic_event(0, 0)],
    )
    recorded_zero = "\n".join(
        render_summary(build_outcome(state=state), verbose=True)
    )
    assert "Dropped proposals: none" in recorded_zero


def test_verbose_summary_says_tokens_are_unavailable_when_none_were_seen() -> None:
    joined = "\n".join(render_summary(build_outcome(), verbose=True))

    assert "Tokens: not available" in joined


def test_verbose_summary_is_explicit_when_no_tool_calls_were_recorded() -> None:
    joined = "\n".join(render_summary(build_outcome(), verbose=True))

    assert "Tool calls: none recorded" in joined


# --- the request budget ----------------------------------------------------


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


def budgeted_outcome(**overrides: object) -> ResearchOutcome:
    """An outcome carrying the three provider categories a run really records."""
    snapshots = overrides.pop(
        "request_budget_snapshots",
        (
            budget_snapshot(
                "deepseek",
                attempts=3,
                ceiling=10,
                effective_limit=10,
                input_tokens=900,
                output_tokens=100,
            ),
            budget_snapshot("openai", attempts=2),
            budget_snapshot(
                "tavily", attempts=11, ceiling=11, effective_limit=11
            ),
        ),
    )
    return build_outcome(request_budget_snapshots=snapshots, **overrides)


def test_the_request_budget_section_names_all_three_providers() -> None:
    joined = "\n".join(render_summary(budgeted_outcome(), verbose=True))

    assert "Request budget:" in joined
    assert "deepseek: 3 attempts reserved" in joined
    assert "openai: 2 attempts reserved" in joined
    assert "tavily: 11 attempts reserved" in joined


def test_a_request_budget_ceiling_renders_its_effective_limit() -> None:
    joined = "\n".join(render_summary(budgeted_outcome(), verbose=True))

    assert "(ceiling 10, effective limit 10)" in joined
    assert "(ceiling 11, effective limit 11)" in joined


def test_an_absent_request_budget_ceiling_is_never_rendered_as_zero() -> None:
    joined = "\n".join(render_summary(budgeted_outcome(), verbose=True))
    line = next(
        row for row in joined.splitlines() if row.strip().startswith("openai:")
    )

    assert "ceiling not set" in line
    assert "0" not in line
    assert "effective limit" not in line


def test_the_request_budget_tokens_are_post_response_counts_not_cost() -> None:
    joined = "\n".join(render_summary(budgeted_outcome(), verbose=True))

    assert "reported post-response" in joined
    assert "not a cost" in joined
    assert "1000 total (900 in / 100 out)" in joined


def test_the_request_budget_section_is_verbose_only() -> None:
    joined = "\n".join(render_summary(budgeted_outcome(), verbose=False))

    assert "Request budget" not in joined
    assert "post-response" not in joined
    assert "deepseek" not in joined
    assert "openai" not in joined
    assert "tavily" not in joined


def test_verbose_summary_does_not_print_request_budget_tokens_twice() -> None:
    """The snapshots carry the tokens once the budget reported them."""
    outcome = budgeted_outcome(
        token_usage=TokenUsage(input_tokens=900, output_tokens=100)
    )

    joined = "\n".join(render_summary(outcome, verbose=True))

    assert joined.count("900 in") == 1
    assert "Tokens: 1000 total (900 in / 100 out)" not in joined


def test_a_legacy_outcome_without_request_budget_snapshots_keeps_the_token_line() -> (
    None
):
    """An injected outcome from before the budget existed still reports."""
    outcome = build_outcome(
        token_usage=TokenUsage(input_tokens=900, output_tokens=100)
    )

    joined = "\n".join(render_summary(outcome, verbose=True))

    assert "Tokens: 1000 total (900 in / 100 out)" in joined


def test_a_scraper_failure_and_request_budget_render_no_urls_or_queries() -> None:
    """Bounded fields only: no error text, no URL, no query content."""
    state = ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        errors=[
            ResearchError(
                error_type="scraper_failure",
                source="tools.scraper",
                message="The scraper could not read one source.",
                details={"url": "https://example.invalid/secret-page"},
            )
        ],
    )
    outcome = budgeted_outcome(state=state)

    joined = "\n".join(
        render_summary(outcome, verbose=True)
        + render_warnings(outcome, verbose=True)
    )

    assert "Request budget:" in joined
    assert "https://" not in joined
    assert "example.invalid" not in joined
    assert "secret-page" not in joined
    assert QUESTION not in joined


# --- the compact outcome ------------------------------------------------------
#
# One composed pass, with a read registry the counts can be read from. The
# fixture is shaped so that no two quantities on the summary happen to be
# equal: three reads over two works, three assessed sources of which one is
# cited, and three checked claims at three different corroboration badges.

READ_URL = "https://network.example/report"
MIRROR_URL = "https://mirror.example/report"
OTHER_URL = "https://other.example/analysis"
CONTENT_ONE = "a" * 64
CONTENT_TWO = "b" * 64


def _read(
    read_id: str,
    *,
    url: str,
    content_sha256: str,
    acquisition_kind: str,
) -> ReadRecord:
    return ReadRecord(
        read_id=read_id,
        requested_url=url,
        resolved_url=url,
        title=f"Source at {url}",
        reader="web_scraper",
        retrieved_at="2026-09-13T09:00:00+00:00",
        content_sha256=content_sha256,
        extraction_complete=True,
        passages={"p. 1": "A measured statement."},
        acquisition_kind=acquisition_kind,  # type: ignore[arg-type]
        origin_session_id="session-1",
    )


def _scored_source(
    url: str, *, publisher_id: str | None = None, work_id: str | None = None
) -> ScoredSource:
    return ScoredSource(
        url=url,
        title=f"Source at {url}",
        authority_score=0.8,
        recency_score=0.8,
        relevance_score=0.8,
        overall_score=0.8,
        rationale="Read primary material with a stated date.",
        publisher_id=publisher_id,
        work_id=work_id,
    )


def _badged_claim(text: str, *, verdict: str, badge: str | None) -> Claim:
    return Claim(
        claim_id=f"claim-{abs(hash(text)) % 100000:05d}",
        text=text,
        source_urls=[READ_URL],
        verdict=verdict,  # type: ignore[arg-type]
        evidence_status=badge,  # type: ignore[arg-type]
        confidence=0.8,
        evidence=["A measured statement."],
        contradictions=[],
        verification_evidence=[],
    )


def composed_state(**overrides: object) -> ResearchState:
    """A composed pass whose four counts are four different numbers."""
    corroborated = _badged_claim(
        "Independent corroboration was established.", verdict="verified",
        badge="verified_pair",
    )
    attributed = _badged_claim(
        "Only primary-source attribution was established.",
        verdict="insufficient_evidence",
        badge="source_supported",
    )
    unclassified = _badged_claim(
        "No corroboration classification was recorded.",
        verdict="unverified",
        badge=None,
    )
    composition = ReportComposition(
        question=QUESTION,
        session_id="session-1",
        claims=[corroborated, attributed, unclassified],
        sources=[
            _scored_source(READ_URL, publisher_id="network.example"),
            _scored_source(MIRROR_URL, publisher_id="mirror.example"),
            _scored_source(OTHER_URL),
        ],
        findings=[],
        summary=[
            ReportPoint(
                text=corroborated.text,
                claim_ids=[corroborated.claim_id],
                source_urls=[READ_URL],
            )
        ],
        sections=[],
        sub_topics=[
            SubTopic(
                coverage_id="topic-01",
                title="Grid connection and interconnection",
                rationale="It changes the decision surface.",
                search_queries=["FERC queue 2026"],
                success_criteria=["A read source answers it."],
                priority=1,
            )
        ],
    )
    state = ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        composition=composition,
        report="# Research report\n\nA measured statement.[1]\n",
        report_evidence="# Evidence ledger\n",
        read_records={
            read.read_id: read
            for read in (
                _read("read-1", url=READ_URL, content_sha256=CONTENT_ONE,
                      acquisition_kind="network"),
                _read("read-2", url=MIRROR_URL, content_sha256=CONTENT_ONE,
                      acquisition_kind="cache"),
                _read("read-3", url=OTHER_URL, content_sha256=CONTENT_TWO,
                      acquisition_kind="network"),
            )
        },
        events=[
            ResearchEvent(
                event_type="graph.session.started",
                source="graph",
                message="Research session started.",
                timestamp="2026-09-13T09:00:00+00:00",
            ),
            ResearchEvent(
                event_type="graph.session.completed",
                source="graph",
                message="Research session completed.",
                timestamp="2026-09-13T09:02:30+00:00",
            ),
        ],
        quality=quality_snapshot(
            planned_topics=3,
            covered_topics=2,
            coverage_ratio=2 / 3,
            substantive_covered_topics=2,
            substantive_topic_ratio=2 / 3,
            planned_targets=4,
            required_targets=3,
            answered_targets=2,
            critical_targets=2,
            unanswered_critical_target_ids=["t-2"],
            semantic_review_status="scored",
            semantic_review_score=0.86,
            semantic_review_fingerprint="abc123def456",
        ),
        report_review=fake_report_review(),
    )
    payload = overrides.pop("state_overrides", {})
    return state.model_copy(update={**payload, **overrides})


def composed_outcome(**overrides: object) -> ResearchOutcome:
    """The outcome a finished run produces, with every derived field real."""
    state = composed_state(**overrides)
    return real_build_outcome(
        GraphRun(
            session_id=state.session_id,
            state=state,
            status="completed",
            trace_url=None,
        ),
        metrics=[],
    )


def test_the_summary_counts_checked_claims_apart_from_corroborated_ones() -> None:
    """Three checked claims are not three verified ones.

    Each canonical claim is counted under the badge it actually recorded, and
    the four counts add up to the number that was checked — so a check count
    can never be printed as a corroboration count.
    """
    joined = "\n".join(render_summary(composed_outcome(), verbose=False))

    assert (
        "Claims: 3 checked; 1 independently corroborated, "
        "1 primary-source attributed, 0 contested, 1 not established" in joined
    )
    assert "3 verified" not in joined


def test_the_summary_reports_assessed_cited_reads_works_and_publishers_apart() -> (
    None
):
    """Four quantities, four numbers, no alias standing in for another.

    Three reads (two network, one validated cache reuse) resolve to two works,
    because the network read and the cache read are the same document; three
    assessed sources are cited once; and the publisher count is the established
    identities alone.
    """
    joined = "\n".join(render_summary(composed_outcome(), verbose=False))

    assert (
        "Sources: 3 assessed, 1 cited; reads 3 (network 2, cache reuse 1), "
        "works 2, publishers 2, findings 0" in joined
    )


def test_the_coverage_line_reports_the_substantive_topic_count() -> None:
    """The line says "substantive", so it prints the substantive numerator.

    ``covered_topics`` on the snapshot is the claimed reading — topics some
    claim recorded consuming — and the two disagree exactly when a topic was
    claimed but never answered. Printing the claimed value next to a
    substantive ratio published a topic as covered that the ratio itself
    scored at zero.
    """
    joined = "\n".join(
        render_summary(
            composed_outcome(
                quality=quality_snapshot(
                    planned_topics=1,
                    covered_topics=1,
                    coverage_ratio=1.0,
                    substantive_covered_topics=0,
                    substantive_topic_ratio=0.0,
                    planned_targets=1,
                    required_targets=1,
                    answered_targets=0,
                )
            ),
            verbose=False,
        )
    )

    assert (
        "Coverage: 0/1 topics covered (substantive, 0%); "
        "0/1 required targets answered" in joined
    )


def test_the_summary_keeps_topic_and_target_progress_apart() -> None:
    joined = "\n".join(render_summary(composed_outcome(), verbose=False))

    assert (
        "Coverage: 2/3 topics covered (substantive, 67%); "
        "2/3 required targets answered; 1/2 critical targets answered" in joined
    )


def test_the_summary_names_the_semantic_review_and_never_invents_a_score() -> None:
    reviewed = "\n".join(render_summary(composed_outcome(), verbose=False))
    unreviewed = "\n".join(
        render_summary(
            composed_outcome(
                state_overrides={
                    "quality": quality_snapshot(
                        semantic_review_status="incomplete",
                        semantic_review_score=None,
                    ),
                    "report_review": None,
                }
            ),
            verbose=False,
        )
    )

    assert "Review: scored 0.86 (fingerprint abc123def456)" in reviewed
    assert "Quality reasons:" not in reviewed
    assert "Review: incomplete (no score was recorded)" in unreviewed
    assert "Quality reasons: semantic review incomplete" in unreviewed
    assert "Review: scored 0.00" not in unreviewed


def test_the_summary_names_open_defects_with_their_scope() -> None:
    """A partial verdict names what is open, not that "limitations remain"."""
    state = composed_state(
        state_overrides={
            "critique": Critique(
                score=6,
                gaps=[
                    CritiqueGap(
                        gap_id="gap-01",
                        coverage_id="topic-01",
                        target_ids=["t-1"],
                        kind="coverage",
                        severity="major",
                        repair_action="acquire",
                        problem="The interconnection queue is not answered.",
                    ),
                    CritiqueGap(
                        gap_id="gap-02",
                        target_ids=["t-2"],
                        kind="freshness",
                        severity="minor",
                        repair_action="acquire",
                        problem="One passage is older than the others.",
                    ),
                ],
                unsupported_claims=[],
                recommended_queries=[],
                should_continue=True,
                rationale="One material defect remains.",
            )
        }
    )

    joined = "\n".join(render_summary(build_outcome(state=state), verbose=False))

    # Only the material defect is open; the minor one is recorded, not open.
    assert "Unresolved: 1 defect (coverage topic-01) (critic)" in joined
    assert "freshness" not in joined


def test_the_summary_reports_the_recorded_elapsed_span() -> None:
    joined = "\n".join(render_summary(composed_outcome(), verbose=False))

    assert "Elapsed: 2m 30s" in joined


def test_the_summary_reports_every_artifact_path_of_the_published_set() -> None:
    outcome = composed_outcome(
        state_overrides={
            "report_path": REPORT_PATH,
            "evidence_path": EVIDENCE_PATH,
            "quality_path": "output/report-session-1-0-quality.json",
        }
    )

    joined = "\n".join(render_summary(outcome, verbose=False))

    assert f"Report: {REPORT_PATH}" in joined
    assert f"Evidence ledger: {EVIDENCE_PATH}" in joined
    assert "Quality record: output/report-session-1-0-quality.json" in joined
    assert "Publication: incomplete" not in joined


def test_an_incomplete_publication_advertises_no_path_and_says_which_write_failed(
) -> None:
    """The whole set or nothing, and the failure record says which one.

    Two of the three files may well exist on disk; none of them is advertised,
    because a front-end holding two paths cannot tell which artifact is missing.
    """
    outcome = composed_outcome(
        state_overrides={
            "errors": [
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
            ]
        }
    )

    joined = "\n".join(render_summary(outcome, verbose=False))

    assert "Publication: incomplete; these writes failed: quality." in joined
    # The line says the path is withheld, not that no file exists: a sibling
    # write may well have succeeded and left one on disk.
    assert (
        "Report: not advertised; the artifact set is published whole or not "
        "at all." in joined
    )
    assert (
        "Evidence ledger: not advertised; the artifact set is published whole "
        "or not at all." in joined
    )
    assert (
        "Quality record: not advertised; the artifact set is published whole "
        "or not at all." in joined
    )
    assert REPORT_PATH not in joined
    assert EVIDENCE_PATH not in joined
