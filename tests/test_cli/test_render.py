"""Tests for what the CLI prints."""

from __future__ import annotations

import io
from collections.abc import Sequence

from deep_research.agents.events import agent_event
from deep_research.agents.evidence_verifier import evidence_verified_event
from deep_research.agents.report_writer import WrittenReport, report_written_event
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
    session_completed_event,
)
from deep_research.graph.orchestrator import GraphRun
from deep_research.observability import RunTelemetryCollector, TokenUsage
from deep_research.request_budget import (
    ProviderCategory,
    RequestBudget,
    RequestBudgetSnapshot,
)
from deep_research.runtime.outcome import ResearchOutcome, ToolCallSummary
from deep_research.runtime.outcome import build_outcome as real_build_outcome
from deep_research.utils.types import (
    REVIEW_DIMENSIONS,
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
    ReviewDefect,
    RunTelemetry,
    ScoredSource,
    SubTopic,
)
from tests.evidence_fakes import figure, make_finding, make_read

QUESTION = "How mature is quantum error correction?"

REPORT_PATH = "output/report-session-1-0.md"
EVIDENCE_PATH = "output/report-session-1-0-evidence.md"
QUALITY_PATH = "output/report-session-1-0-quality.json"

ANSWERED_TARGET_IDS = ("topic-01-target-01", "topic-01-target-02")
MISSING_TARGET_ID = "topic-02-target-01"
OWNER = "U.S. Energy Information Administration"


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
    quality: ReportQualitySnapshot | None = None,
    report_review: ReportReview | None = None,
    **overrides: object,
) -> ResearchState:
    """One composed, judged pass: the numbers the summary must print.

    Carries a scored review by default, because a pass with no review is never
    accepted — a test that wants the unreviewed reading passes
    ``report_review=None`` explicitly and says so.

    The default review is stamped the way the reviewer node stamps it (PD-5):
    with the snapshot's own missing-target reading. A fixture whose gates
    measured a missing target while its review named none would describe a
    record the graph never produces, and it would be accepted by a route the
    real run would not take.
    """
    snapshot = quality_snapshot() if quality is None else quality
    review = report_review
    if review is None:
        review = scored_review(
            missing_required_target_ids=list(
                snapshot.missing_required_target_ids
            )
        )
    return ResearchState.model_validate(
        {
            "session_id": "session-1",
            "original_question": QUESTION,
            "composition": judged_composition(),
            "report_review": review,
            "quality": snapshot,
            **overrides,
        }
    )


def quality_snapshot(**overrides: object) -> ReportQualitySnapshot:
    """The pass's snapshot: the readings the summary prints, and their ids.

    Only the readings this pipeline computes are named. The retired readings
    the type still carries are left at their defaults, so this fixture never
    depends on one.
    """
    payload: dict[str, object] = {
        "required_target_ids": [*ANSWERED_TARGET_IDS, MISSING_TARGET_ID],
        "answered_target_ids": list(ANSWERED_TARGET_IDS),
        "missing_required_target_ids": [MISSING_TARGET_ID],
        "verified_findings": 2,
        "dropped_findings": 1,
        "cited_findings": 2,
        "unjudged_sentences": [],
    }
    payload.update(overrides)
    return ReportQualitySnapshot.model_validate(payload)


def scored_review(**overrides: object) -> ReportReview:
    """A scored terminal review, which is what the CLI's Review row reads."""
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


def kept_findings() -> list[Finding]:
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


def judged_composition(**overrides: object) -> ReportComposition:
    """The pass's composition: the kept findings, and what it could not find."""
    payload: dict[str, object] = {
        "question": QUESTION,
        "session_id": "session-1",
        "findings": kept_findings(),
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
            graph_event(
                "graph.session.started",
                "Research session started.",
                session_id="session-1",
                max_extra_passes=2,
                checkpointing=False,
            ),
            node_started_event("planner", iteration=0),
            ResearchEvent(
                event_type="observability.span.started",
                source="observability",
                message="agent.planner started.",
            ),
            node_started_event("researcher", iteration=0),
            node_started_event("evidence_verifier", iteration=0),
            node_started_event("report_writer", iteration=0),
            graph_event(
                "graph.extra_pass.started",
                "Extra research pass 1 started.",
                iteration=1,
                max_extra_passes=2,
                targets=["topic-02-target-01"],
            ),
            session_completed_event(
                status="completed", iteration=1, error_count=0, has_report=True
            ),
        ],
    )


def index_of(lines: list[str], prefix: str) -> int:
    return next(
        index for index, line in enumerate(lines) if line.startswith(prefix)
    )


def graph_event(
    event_type: str, message: str, *, iteration: int = 0, **metadata: object
) -> ResearchEvent:
    """One graph record, built by its enumerated type rather than by producer.

    The graph's own constructors move with its nodes — the per-pass record is
    ``graph.extra_pass.started`` now, and the session and route records carry
    the extra-pass ceiling instead of the retired macro budget — while this
    renderer's contract is the type string and the message. Building the record
    here keeps the CLI's own surface tested without pinning a sibling module's
    keyword list.
    """
    return ResearchEvent(
        event_type=event_type,
        source="graph",
        message=message,
        metadata={"iteration": iteration, **metadata},
    )


# --- live progress ---------------------------------------------------------


def test_progress_returns_the_line_for_one_plain_event() -> None:
    line = render_progress(node_started_event("planner", iteration=0), verbose=False)

    assert line == "  [0] Node planner started."


def test_plain_progress_streams_every_allowed_event_type() -> None:
    assert render_progress(
        graph_event(
            "graph.session.started",
            "Research session started.",
            session_id="session-1",
            max_extra_passes=2,
            checkpointing=False,
        ),
        verbose=False,
    )
    assert render_progress(
        graph_event(
            "graph.extra_pass.started",
            "Extra research pass 1 started.",
            iteration=1,
            max_extra_passes=2,
            targets=["topic-02-target-01"],
        ),
        verbose=False,
    ) == "  [1] Extra research pass 1 started."
    assert render_progress(
        graph_event(
            "graph.route.decided",
            "Route decided: extra_pass_requested.",
            iteration=1,
            destination="extra_pass",
            reason="extra_pass_requested",
            max_extra_passes=2,
            missing_required_target_ids=["topic-02-target-01"],
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


def test_plain_progress_streams_the_pipeline_s_own_nodes() -> None:
    """Every node of the shipped graph streams, named as the graph names it."""
    for node in (
        "source_evaluator",
        "evidence_verifier",
        "report_writer",
        "report_reviewer",
    ):
        assert render_progress(
            node_started_event(node, iteration=0), verbose=False
        ) == f"  [0] Node {node} started."


def test_verbose_progress_streams_the_verifier_and_the_writer() -> None:
    """The two new agents' completion records, from their own producers."""
    verification = evidence_verified_event(kept_findings())
    written = report_written_event(
        WrittenReport(
            markdown="# Research report\n",
            evidence_markdown="# Evidence ledger\n",
            composition=judged_composition(),
            statement_count=3,
            citation_count=2,
            refused_count=0,
        )
    )

    assert render_progress(verification, verbose=False) is None
    assert render_progress(verification, verbose=True) == (
        "  [0] Verified 2 findings."
    )
    assert render_progress(written, verbose=False) is None
    assert render_progress(written, verbose=True) == (
        "  [0] Wrote 3 statement(s) citing 2 source(s); "
        "0 drafted point(s) refused."
    )


def test_verbose_progress_streams_the_enumerated_provider_failure() -> None:
    failure = agent_event(
        agent_name="report_reviewer",
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


def _topic(coverage_id: str, title: str) -> SubTopic:
    return SubTopic(
        coverage_id=coverage_id,
        title=title,
        rationale="It decides the comparison.",
        search_queries=["grid storage cost 2026"],
        success_criteria=["A read source answers it."],
        priority=1,
    )


def test_a_memory_write_failure_does_not_withhold_the_artifact_paths() -> None:
    """Memory is a separate write; its failure is not an incomplete set.

    Three document writes succeeded and two finding writes to memory failed.
    The summary called the publication incomplete, said no artifact path was
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
    assert "Memory: 2 finding writes to memory failed" in joined


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


def test_a_stopped_pass_is_not_printed_as_a_never_researched_topic() -> None:
    """``error_reading`` gives a skip's enumerated reason its own sentence.

    ``sub_topic_skipped_error`` writes one message for every reason, and that
    message says the sub-topic was never researched. A sub-topic the pass's own
    cap deferred is not that, so the verbose line -- the only place the
    sentence is printed -- must read each reason's own words rather than the
    producer's.
    """
    deferred = _topic("topic-04", "Interconnection queues")
    stopped = _topic("topic-06", "Retirement schedules")
    state = ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        sub_topics=[deferred, stopped],
        errors=[
            sub_topic_skipped_error(deferred, reason="cap"),
            sub_topic_skipped_error(
                stopped, reason="provider_failure_stopped_processing"
            ),
        ],
    )

    lines = render_warnings(build_outcome(state=state), verbose=True)

    assert lines == [
        "Warnings: 2 errors (0 recovered, 2 non-fatal, 0 fatal)",
        "  agent.researcher: 2 errors (coverage topic-04, topic-06)",
        "    researcher_sub_topic_skipped (non-fatal; reason cap): topic-04 "
        '"Interconnection queues"',
        "    researcher_sub_topic_skipped (non-fatal; reason "
        'provider_failure_stopped_processing): topic-06 "Retirement schedules"',
        "    warning: [researcher_sub_topic_skipped] This planned sub-topic "
        "was deferred: the pass reached its sub-topic limit before its turn "
        "came up.",
        "    warning: [researcher_sub_topic_skipped] A planned sub-topic was "
        "never researched; a provider failure stopped the pass before it "
        "could run.",
    ]


# --- the summary -----------------------------------------------------------


def test_the_summary_leads_with_the_quality_block() -> None:
    outcome = build_outcome(
        state=quality_state(), evidence_path=EVIDENCE_PATH
    )

    lines = render_summary(outcome, verbose=False)
    joined = "\n".join(lines)

    assert "Quality: partial (review scored 0.90)" in joined
    assert (
        "Findings: 2 checked (0 with corrected context, 0 unchecked context), "
        "0 quoted (snippet on the page only), 1 dropped; 2 cited" in joined
    )
    assert (
        "Integrity: 0 duplicate fact rows; 0 uncited statements; "
        "0 unjudged sentences; 0 forecasts without release" in joined
    )
    assert "Required targets: 2/3 answered" in joined
    assert f"Report: {REPORT_PATH}" in joined
    assert f"Evidence ledger: {EVIDENCE_PATH}" in joined


def test_the_summary_prints_identity_and_status_before_the_quality_block() -> None:
    outcome = build_outcome(
        state=quality_state(), evidence_path=EVIDENCE_PATH
    )

    lines = render_summary(outcome, verbose=False)

    assert index_of(lines, "Session ID:") < index_of(lines, "Status:")
    assert index_of(lines, "Status:") < index_of(lines, "Quality:")
    assert index_of(lines, "Quality:") < index_of(lines, "Required targets:")
    assert index_of(lines, "Required targets:") < index_of(lines, "Not found:")
    assert index_of(lines, "Not found:") < index_of(lines, "Sources:")
    assert index_of(lines, "Sources:") < index_of(lines, "Review:")
    assert index_of(lines, "Review:") < index_of(lines, "Findings:")
    assert index_of(lines, "Findings:") < index_of(lines, "Integrity:")
    assert index_of(lines, "Integrity:") < index_of(lines, "Report:")
    assert index_of(lines, "Report:") < index_of(lines, "Evidence ledger:")


def test_the_summary_prints_findings_review_and_integrity_lines() -> None:
    lines = render_summary(build_outcome(state=quality_state()), verbose=False)

    assert any(l.startswith("Findings: 2 checked") for l in lines)
    assert any(
        l.startswith(
            "Integrity: 0 duplicate fact rows; 0 uncited statements; "
            "0 unjudged sentences; 0 forecasts without release"
        )
        for l in lines
    )
    assert not any(
        "critic" in l.casefold() or "claims:" in l.casefold() for l in lines
    )


def test_the_findings_line_counts_the_verifier_s_own_readings_apart() -> None:
    """The four readings are kept apart, and the total is every kept finding.

    ``verified_findings`` and ``corrected_findings`` are disjoint sets — one
    counts findings kept exactly as written, the other those whose context was
    corrected — so the total the line prints as "checked" is their sum, and the
    corrected count is a reading *inside* it. A dropped finding is in neither,
    and a cited one is not a checked one. The numbers below are all different,
    so a line that printed one reading under another's name would not read
    back.
    """
    outcome = build_outcome(
        state=quality_state(
            quality=quality_snapshot(
                verified_findings=3,
                corrected_findings=1,
                context_unchecked_findings=4,
                dropped_findings=2,
                cited_findings=2,
            )
        )
    )

    joined = "\n".join(render_summary(outcome, verbose=False))

    assert (
        "Findings: 4 checked (1 with corrected context, 4 unchecked context), "
        "0 quoted (snippet on the page only), 2 dropped; 2 cited" in joined
    )


def test_the_findings_line_counts_quoted_findings_apart() -> None:
    """D21: a quoted finding is neither checked nor dropped -- its own count
    must appear beside the other four so the line's total still accounts for
    every finding the pass judged, and a cited finding never outnumbers a
    checked one."""
    outcome = build_outcome(
        state=quality_state(
            quality=quality_snapshot(
                verified_findings=17,
                quoted_findings=19,
                dropped_findings=2,
                cited_findings=25,
            )
        )
    )

    joined = "\n".join(render_summary(outcome, verbose=False))

    assert (
        "Findings: 17 checked (0 with corrected context, 0 unchecked context), "
        "19 quoted (snippet on the page only), 2 dropped; 25 cited" in joined
    )



def test_the_integrity_line_counts_the_unjudged_sentences_it_lists() -> None:
    """The count and the list come from one field, so they cannot disagree."""
    outcome = build_outcome(
        state=quality_state(
            quality=quality_snapshot(
                duplicate_fact_rows=1,
                uncited_settled_points=2,
                unjudged_sentences=["S003", "S007"],
                forecasts_without_release=3,
            )
        )
    )

    joined = "\n".join(render_summary(outcome, verbose=False))

    assert (
        "Integrity: 1 duplicate fact rows; 2 uncited statements; "
        "2 unjudged sentences; 3 forecasts without release" in joined
    )


def run_telemetry_snapshot(
    *,
    seconds: float = 223.4,
    output_tokens: int = 61_200,
    rate_limits: int = 3,
    rate_limit_recovered: int = 2,
) -> RunTelemetry:
    """One run's §7.3 figures, through the collector's own two seams.

    A real budget drives the peak (one model call reserved by the researcher),
    and ``record_call`` supplies the slow, near-cap reply of the plan's own
    example line: 61,200 of 65,536 output tokens is 93% of the writer's cap.
    """
    collector = RunTelemetryCollector()
    budget = RequestBudget()
    budget.set_observer(collector.observe_budget)
    collector.note_call_starting("researcher")
    budget.reserve("deepseek")
    collector.record_call(
        agent="researcher",
        operation="structured_output",
        seconds=4.0,
        output_tokens=1_000,
        configured_cap=65_536,
        truncated=False,
    )
    collector.record_call(
        agent="report_writer",
        operation="structured_output",
        seconds=seconds,
        output_tokens=output_tokens,
        configured_cap=65_536,
        truncated=False,
    )
    for _ in range(rate_limits):
        collector.note_rate_limit()
    collector.note_rate_limit_recovered(rate_limit_recovered)
    return collector.snapshot()


TELEMETRY_LINE = (
    "Telemetry: peak 1 provider calls in flight (researcher); 3 rate limits "
    "(2 recovered); slowest call report_writer 223.4 s; report_writer output "
    "61,200 of 65,536 tokens (93% of its cap); 0 truncated; "
    "loop lag max 0.0 s; 0 blocks \u2265 5 s"
)


def test_the_summary_prints_one_telemetry_line_after_the_integrity_line() -> None:
    """One line, in the §7.3 order and wording, and one only.

    It sits with the other evidence readings -- after the Integrity line and
    before the artifact paths -- because it is read the same way: a fact about
    the run, not a judgement of the report.
    """
    lines = render_summary(
        build_outcome(state=quality_state(run_telemetry=run_telemetry_snapshot())),
        verbose=False,
    )

    assert lines.count(TELEMETRY_LINE) == 1
    assert index_of(lines, "Integrity:") < lines.index(TELEMETRY_LINE)
    assert lines.index(TELEMETRY_LINE) < index_of(lines, "Report:")


def test_the_summary_prints_the_advice_the_telemetry_triggers() -> None:
    """Both triggers fire: three rate limits, and a reply at 93% of its cap.

    The strings are the ones the telemetry's own renderer produces -- the knob
    of the agent at the peak, and the config key that bounded the fullest
    operation, never a bare "raise the cap".
    """
    lines = render_summary(
        build_outcome(state=quality_state(run_telemetry=run_telemetry_snapshot())),
        verbose=False,
    )

    assert (
        "rate limits hit 3 times; consider lowering agents.sub_topic_concurrency"
        in lines
    )
    assert (
        "output within 93% of the report_writer cap (llm.max_tokens); "
        "consider raising it" in lines
    )


def test_a_quiet_run_prints_its_telemetry_without_advice() -> None:
    """No rate limits and a reply well inside its cap: the line still prints,
    and nothing is recommended — advice a run did not earn is noise."""
    quiet = run_telemetry_snapshot(
        seconds=12.0,
        output_tokens=1_000,
        rate_limits=0,
        rate_limit_recovered=0,
    )
    lines = render_summary(
        build_outcome(state=quality_state(run_telemetry=quiet)), verbose=False
    )

    assert sum(line.startswith("Telemetry:") for line in lines) == 1
    assert [line for line in lines if line.startswith("rate limits hit")] == []
    assert [line for line in lines if line.startswith("output within")] == []


def test_a_run_that_recorded_no_telemetry_prints_no_telemetry_line() -> None:
    """A harness or a run with no collector measured nothing: no line, and no
    row of zeroes standing in for a measurement nobody took."""
    lines = render_summary(build_outcome(state=quality_state()), verbose=False)

    assert [line for line in lines if line.startswith("Telemetry:")] == []


def test_the_verdict_carries_the_review_status_and_mean() -> None:
    """What replaced the retired reviewer's score fragment.

    The verdict is the terminal quality status the gates judged, and the
    review's own status and mean are the judgement it rests on. A run with no
    judgement at all prints its verdict alone: there is no score to print.
    """
    reviewed = "\n".join(
        render_summary(build_outcome(state=quality_state()), verbose=False)
    )
    unscored = "\n".join(
        render_summary(
            build_outcome(
                state=quality_state(
                    quality=quality_snapshot(),
                    report_review=ReportReview(status="incomplete"),
                )
            ),
            verbose=False,
        )
    )
    no_review = "\n".join(
        render_summary(
            build_outcome(
                state=quality_state().model_copy(
                    update={"report_review": None}
                )
            ),
            verbose=False,
        )
    )

    assert "Quality: partial (review scored 0.90)" in reviewed
    assert "Quality: partial (review incomplete)" in unscored
    assert "Quality: partial\n" in no_review
    assert "/10" not in reviewed


def test_the_review_row_names_the_packet_its_score_was_made_over() -> None:
    """The mean is printed once, beside the verdict it earned.

    This row carries the judgement's identity: the status, and the fingerprint
    of the packet it was made over, so two runs' judgements can be told apart.
    A review with no score says that, rather than printing a zero mean.
    """
    reviewed = "\n".join(
        render_summary(build_outcome(state=quality_state()), verbose=False)
    )
    unreviewed = "\n".join(
        render_summary(
            build_outcome(
                state=quality_state(
                    quality=quality_snapshot(
                        semantic_review_status="incomplete",
                        semantic_review_score=None,
                    ),
                    report_review=ReportReview(status="incomplete"),
                )
            ),
            verbose=False,
        )
    )

    assert "Review: scored (fingerprint packet-1)" in reviewed
    assert "Quality reasons:" not in reviewed
    assert "Review: incomplete (no score was recorded)" in unreviewed
    assert "Quality reasons: semantic review incomplete" in unreviewed
    assert "Review: scored 0.00" not in unreviewed


def test_an_accepted_run_says_accepted() -> None:
    """PD-23: passes spent, gates clear, reviewer accepts -> ``accepted``.

    The missing target is not hidden by the acceptance: it is listed under
    Not found, which is what §6.4 accepts, and the console states both facts.
    """
    outcome = build_outcome(
        state=quality_state(iteration=1, max_extra_passes=1)
    )

    joined = "\n".join(render_summary(outcome, verbose=False))

    assert "Quality: accepted (review scored 0.90)" in joined
    assert "Quality reasons:" not in joined
    assert "Not found: topic-02-target-01" in joined
    assert "Required targets: 2/3 answered" in joined


def test_a_pass_with_an_extra_pass_left_is_not_accepted() -> None:
    """The same report, with the pass still to spend, is not accepted yet."""
    outcome = build_outcome(state=quality_state())

    joined = "\n".join(render_summary(outcome, verbose=False))

    assert "Quality: partial (review scored 0.90)" in joined
    assert "Quality: accepted" not in joined


def test_the_quality_line_stands_alone_without_a_snapshot() -> None:
    """No quality pass judged this run: no counts are invented for it."""
    lines = render_summary(build_outcome(state=progress_state()), verbose=False)
    joined = "\n".join(lines)

    assert "Quality: partial" in joined
    assert "Required targets:" not in joined
    assert "Sources:" not in joined
    assert "Findings:" not in joined
    assert "Integrity:" not in joined


def test_a_missing_required_target_is_listed_as_not_found() -> None:
    """The report's own account of what it could not answer is printed."""
    lines = render_summary(build_outcome(state=quality_state()), verbose=False)

    assert "Not found: topic-02-target-01" in lines


def test_not_found_is_omitted_when_the_report_lists_nothing() -> None:
    outcome = build_outcome(
        state=quality_state(
            composition=judged_composition(not_found=[]),
        )
    )

    joined = "\n".join(render_summary(outcome, verbose=False))

    assert "Required targets: 2/3 answered" in joined
    assert "Not found:" not in joined


def test_a_missing_evidence_ledger_file_says_so() -> None:
    outcome = build_outcome(state=quality_state(), evidence_path=None)

    joined = "\n".join(render_summary(outcome, verbose=False))

    assert "Evidence ledger: not written to disk" in joined


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


def test_an_exhausted_extra_pass_budget_says_what_it_means() -> None:
    """PD-23: the status now means extra passes spent with targets missing."""
    lines = render_summary(build_outcome(status="max_iterations"), verbose=False)

    joined = "\n".join(lines)
    assert "Status: max_iterations" in joined
    assert "extra passes exhausted" in joined
    assert "required targets still missing" in joined
    assert "the report not accepted" in joined


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
# cited, and the verifier's own readings at three different numbers.

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


def composed_state(**overrides: object) -> ResearchState:
    """A composed pass whose counts are all different numbers.

    Three reads over two works, three assessed sources of which one is cited,
    two kept findings and one dropped, and two of three required targets
    answered with the third listed under Not found.
    """
    composition = ReportComposition(
        question=QUESTION,
        session_id="session-1",
        sources=[
            _scored_source(READ_URL, publisher_id="network.example"),
            _scored_source(MIRROR_URL, publisher_id="mirror.example"),
            _scored_source(OTHER_URL),
        ],
        findings=kept_findings(),
        not_found=judged_composition().not_found,
        summary=[
            ReportPoint(
                text="Independent corroboration was established.",
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
            semantic_review_status="scored",
            semantic_review_score=0.86,
            semantic_review_fingerprint="abc123def456",
        ),
        # Stamped the way the reviewer node stamps it (PD-5): a review that
        # named no missing target while the gates measured one describes a
        # record the graph never produces, and it routes to acceptance.
        report_review=scored_review(
            missing_required_target_ids=[MISSING_TARGET_ID]
        ),
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


def test_the_summary_counts_the_kept_findings_apart_from_the_dropped_ones() -> None:
    """Two kept findings are not two verified findings and not two cited ones.

    Each reading is printed under its own name, so a check count can never be
    read as a citation count and a dropped finding never disappears into the
    number of the ones that survived.
    """
    joined = "\n".join(render_summary(composed_outcome(), verbose=False))

    assert (
        "Findings: 2 checked (0 with corrected context, 0 unchecked context), "
        "0 quoted (snippet on the page only), 1 dropped; 2 cited" in joined
    )
    assert "claims" not in joined.casefold()
    assert "critic" not in joined.casefold()


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
        "works 2, publishers 2, findings 2" in joined
    )


def test_the_coverage_line_reports_the_required_targets_and_the_not_found_list() -> (
    None
):
    """The count is the gate's reading; the Not found row is the report's own.

    A required target no finding answers is missing from the answer count
    whatever else happens. When the report lists it under Not found the run has
    accounted for it, and when nothing lists it the Unresolved row is where it
    shows up — one number for both readings would either hide an unaccounted
    obligation or report an accounted one as a defect.
    """
    listed = "\n".join(render_summary(composed_outcome(), verbose=False))
    unlisted = "\n".join(
        render_summary(
            composed_outcome(
                composition=judged_composition(not_found=[]),
            ),
            verbose=False,
        )
    )

    assert "Required targets: 2/3 answered" in listed
    assert "Not found: topic-02-target-01" in listed
    assert "3/3" not in listed
    assert "Required targets: 2/3 answered" in unlisted
    assert "Not found:" not in unlisted


def test_the_coverage_line_never_counts_an_optional_answer_as_required() -> None:
    """§6.4: the count is *required* targets answered, so it cannot exceed them.

    The plan's optional targets are answered by the same verified findings, and
    ``answered_target_ids`` holds every target a finding answers. Counting that
    list printed ``Required targets: 3/2 answered`` — and, when the answered
    targets were the optional ones, a satisfied ``2/2 answered`` directly above
    ``Unresolved: 1 missing required target``. The 6.4 audit reads this line out
    of ``cli.log`` as the run's own answer to "every part of the question
    answered", so a numerator of answers is a false statement about the run.
    """
    joined = "\n".join(
        render_summary(
            composed_outcome(
                state_overrides={
                    "quality": quality_snapshot(
                        required_target_ids=[
                            ANSWERED_TARGET_IDS[0],
                            MISSING_TARGET_ID,
                        ],
                        answered_target_ids=[
                            "topic-07-target-01",
                            "topic-07-target-02",
                            ANSWERED_TARGET_IDS[0],
                        ],
                        missing_required_target_ids=[MISSING_TARGET_ID],
                    )
                }
            ),
            verbose=False,
        )
    )

    assert "Required targets: 1/2 answered" in joined
    assert "Required targets: 3/2" not in joined
    assert "Required targets: 2/2" not in joined
    # The count and the row that names a still-owed target are two readings of
    # one run, so they can never contradict each other.
    assert "Unresolved: 1 missing required target (topic-02-target-01)" in joined


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
                    "report_review": ReportReview(status="incomplete"),
                }
            ),
            verbose=False,
        )
    )

    assert "Quality: partial (review scored 0.86)" in reviewed
    assert "Review: scored (fingerprint abc123def456)" in reviewed
    assert "Quality reasons:" not in reviewed
    assert "Review: incomplete (no score was recorded)" in unreviewed
    assert "Quality reasons: semantic review incomplete" in unreviewed
    assert "Review: scored 0.00" not in unreviewed


def test_the_summary_names_open_defects_with_their_scope_and_the_owed_targets() -> (
    None
):
    """A partial verdict names what is open, not that "limitations remain"."""
    state = composed_state(
        state_overrides={
            "report_review": scored_review(
                defects=[
                    ReviewDefect(
                        defect_id="review-01",
                        kind="coverage",
                        severity="major",
                        target_ids=[MISSING_TARGET_ID],
                        problem="The 2025 plan is not answered.",
                    ),
                    ReviewDefect(
                        defect_id="review-02",
                        kind="freshness",
                        severity="minor",
                        target_ids=["topic-01-target-01"],
                        problem="One passage is older than the others.",
                    ),
                ]
            )
        }
    )

    joined = "\n".join(render_summary(build_outcome(state=state), verbose=False))

    # Only the material defect is open; the minor one is recorded, not open.
    # The missing target is named by the gate's own reading, not the review's.
    assert (
        "Unresolved: 1 defect (coverage topic-02-target-01) (semantic review); "
        "1 missing required target (topic-02-target-01)" in joined
    )
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
