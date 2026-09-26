"""Task 4.2, spec §6.2-§6.3: the terminal report review, one call per review.

The reviewer reads one packet — the reader report, every reader statement with
its code-built label, the cited findings' snippets and labels, the key facts,
Not found, and the deterministic gate results — and returns the seven
dimensions, one disposition per statement, and its typed defects. There are no
claims, no verdict badges, no evidence batches, and no Critic here: the critic
and the fact checker left with step 4 (D6, PD-16, PD-21).

Every named case is its own test function, because a review that "looks right"
is exactly what the formula this review replaced already provided. The review
under test is offline: scripted provider replies, no network.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import httpx
import yaml
from openai import APITimeoutError

import pytest

from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report import (
    ReportComposition,
    ReportPoint,
    ReportSection,
    render_written_report,
)
from deep_research.agents.report_reviewer import (
    _fact_row_line,
    _table_lines,
    REPORT_REVIEWER_ROLE,
    REPORT_REVIEW_PROMPT_VERSION,
    REPORT_REVIEW_SYSTEM_PROMPT,
    REVIEW_DIMENSIONS,
    REVIEW_RUBRIC_VERSION,
    SCOPED_REPORT_REVIEW_INSTRUCTION,
    SCOPED_REPORT_REVIEW_SYSTEM_PROMPT,
    SEMANTIC_REVIEW_MEAN,
    PreviousDefectResolutionDraft,
    PreviousDefectView,
    ReportReviewer,
    ReviewDefectDraft,
    ReviewDimensionScores,
    StatementDispositionDraft,
    ScopedReportReviewDraft,
    ScopedReportReviewInput,
    build_report_review_input,
    build_scoped_report_review_input,
    composition_semantic_fingerprint,
    remap_review_for_redraft,
    report_review_input_fingerprint,
    review_messages,
    review_report,
    review_scoped_report,
    scoped_report_review_input_fingerprint,
    scoped_review_messages,
    semantic_review_passes,
)
from deep_research.observability import (
    LangSmithRuntimeConfig,
    TokenUsage,
    Tracker,
)
from deep_research.providers import (
    ProviderOutputLimitError,
    ProviderResponseError,
    ProviderResponseTelemetry,
)
from deep_research.providers.deepseek_provider import DeepSeekSchemaChatProvider
from deep_research.utils.config import LLMConfig
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    UNREVIEWED_STATEMENT_DISPOSITION,
    FigureContext,
    FigureResult,
    FindingVerification,
    ItemMark,
    NotFoundTarget,
    ReportPart,
    ReportQualitySnapshot,
    ReportReview,
    ReportStatement,
    ReportTable,
    ResearchState,
    ReviewDefect,
    SubTopic,
    TableCell,
    TableEntry,
    UnreachablePage,
    FactRow,
)
from tests.agent_fakes import ScriptedCompleter
from tests.evidence_fakes import (
    EIA_PAGE,
    EIA_TITLE,
    EIA_URL,
    figure,
    make_finding,
    make_read,
    make_target,
)

SESSION_ID = "session-review"
QUESTION = "How much battery storage capacity was added in 2024?"
EIA = "U.S. Energy Information Administration"
EIA_SNIPPET = (
    "Generators added 10.4 gigawatts (GW) of new battery storage capacity in 2024"
)
TARGET_ID = "topic-01-target-01"
FORECAST_TARGET_ID = "topic-02-target-01"

DIMENSION_NAMES = (
    "completeness",
    "prioritization",
    "evidence_quality",
    "attribution",
    "uncertainty",
    "readability",
    "actionability",
)

# The three sentences one written report composes: S001 in the summary, S002
# and S003 in the one findings section, in the writer's own numbering order.
WRITTEN_SENTENCES = {
    "S001": (
        "Generators added 10.4 gigawatts of battery storage capacity in the "
        "United States in 2024."
    ),
    "S002": (
        "Battery storage was the second-largest source of new generating "
        "capacity that year."
    ),
    "S003": (
        "Capacity growth from battery storage could set a record in 2025."
    ),
}


def _tracker() -> Tracker:
    return Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False, project="review-tests", api_key=None
        )
    )


def _output_limit_error() -> ProviderOutputLimitError:
    """One truncated reply, typed as the provider boundary raises it."""
    return ProviderOutputLimitError(
        ProviderResponseTelemetry(
            finish_reason_category="length",
            configured_max_tokens=4096,
            usage=TokenUsage(input_tokens=5, output_tokens=4096),
            request_attempt=1,
        )
    )


# --- the written report this review judges ----------------------------------


def _written_finding():
    """The EIA 2024 actual, verified with its figure's own context."""
    read = make_read(url=EIA_URL, title=EIA_TITLE)
    finding = make_finding(
        read,
        EIA_SNIPPET,
        figures=[figure("10.4", "GW", "2024", "actual")],
        target_ids=[TARGET_ID],
        release_date="2025-03-12",
    )
    result = FigureResult(
        figure=finding.figures[0],
        matched=True,
        evidence_words=EIA_SNIPPET,
        context=FigureContext(
            period="2024",
            scope=None,
            attribution="own",
            organisation=EIA,
            kind="actual",
        ),
    )
    return finding.model_copy(
        update={
            "verification": FindingVerification(
                status="verified", figure_results=[result]
            )
        }
    )


def _fact_row(finding_id: str) -> FactRow:
    return FactRow(
        row_id="K001",
        organisation=EIA,
        attribution="own",
        measure="battery storage power capacity added",
        period="2024",
        value="10.4 GW",
        kind="actual",
        scope=None,
        release="January 2025 Preliminary Monthly Electric Generator Inventory",
        finding_id=finding_id,
        target_ids=[TARGET_ID],
    )


def _topic() -> SubTopic:
    target = make_target(TARGET_ID)
    return SubTopic(
        coverage_id=target.coverage_id,
        title="Battery storage additions",
        rationale="It is the question asked.",
        search_queries=["battery storage capacity additions 2024"],
        success_criteria=["a capacity addition is quoted"],
        priority=1,
        evidence_targets=[target],
    )


def _quality(**overrides: object) -> ReportQualitySnapshot:
    """The snapshot a writer node leaves behind for this report.

    One verified finding from the one cited source answers the one required
    target, nothing was left unjudged and no hard check failed. The counters
    the reviewer's deterministic block reads are named explicitly; every other
    field's zero is the model's own default.
    """
    fields: dict[str, object] = {
        "cited_sources": 1,
        "verified_findings": 1,
        "cited_findings": 1,
        "required_target_ids": [TARGET_ID],
        "answered_target_ids": [TARGET_ID],
        "uncited_settled_points": 0,
        "unjudged_sentences": [],
        "hard_failures": [],
    }
    fields.update(overrides)
    return ReportQualitySnapshot(**fields)


def _written_composition(
    *,
    quality_status: str = "not yet quality-gated",
    not_found: bool = True,
) -> ReportComposition:
    """The composition ``compose_written_report`` produces for three sentences.

    Built here in the writer's own shape — the labels, key facts row, Not found
    entry and statement ids are the writer's — so the packet tests need no
    provider call. ``test_a_real_written_report_builds_the_same_packet`` runs
    the writer's real ``compose_written_report`` and checks this fixture
    against it.
    """
    finding = _written_finding()
    finding_id = finding_fingerprint(finding)
    statements = {
        statement_id: ReportStatement(
            statement_id=statement_id,
            text=text,
            finding_ids=[finding_id],
            target_ids=[TARGET_ID],
        )
        for statement_id, text in WRITTEN_SENTENCES.items()
    }

    def point(statement_id: str) -> ReportPoint:
        return ReportPoint(
            text=statements[statement_id].text,
            source_urls=[EIA_URL],
            statement=statements[statement_id],
        )

    return ReportComposition(
        question=QUESTION,
        session_id=SESSION_ID,
        iteration=0,
        max_extra_passes=1,
        as_of="2026-08-01",
        scope="United States",
        quality_status=quality_status,
        sub_topics=[_topic()],
        findings=[finding],
        fact_rows=[_fact_row(finding_id)],
        not_found=(
            [
                NotFoundTarget(
                    target_id=FORECAST_TARGET_ID,
                    question="What is the 2025 capacity addition forecast?",
                    queries=["battery storage forecast 2025"],
                    pages_read=[EIA_URL],
                    searched=True,
                )
            ]
            if not_found
            else []
        ),
        finding_labels={"F01": finding_id},
        summary=[point("S001")],
        sections=[
            ReportSection(title="Additions in 2024", points=[point("S002"), point("S003")])
        ],
    )


def state_with_written_report(**overrides: object) -> ResearchState:
    """The state a writer node leaves behind: report, composition, quality.

    The reader report is ``render_written_report``'s own output, so the packet's
    reader content and the composition it was rendered from cannot drift.
    """
    composition = overrides.pop("composition", None) or _written_composition()
    report = overrides.pop("report", None) or render_written_report(composition)
    payload: dict[str, object] = {
        "session_id": SESSION_ID,
        "original_question": QUESTION,
        "composition": composition,
        "report": report,
        "quality": _quality(),
        "sub_topics": list(composition.sub_topics),
        "initial_target_ids": [TARGET_ID],
    }
    payload.update(overrides)
    return ResearchState.model_validate(payload)


def packet():
    """The packet one review of the written report judges."""
    return build_report_review_input(state_with_written_report())


def _scores(value: float = 1.0) -> dict[str, float]:
    return {name: value for name in DIMENSION_NAMES}


def _scored_review(**overrides: object) -> ReportReview:
    payload: dict[str, object] = {
        "status": "scored",
        "dimensions": _scores(),
        "defects": [],
        "per_statement_dispositions": {"S001": "supported"},
        "reviewed_statement_ids": ["S001"],
        "input_fingerprint": "packet-1",
        "composition_fingerprint": "composition-1",
        "rubric_version": REVIEW_RUBRIC_VERSION,
        "rationale": "Recorded for the review tests.",
    }
    payload.update(overrides)
    return ReportReview.model_validate(payload)


def _unsupported_defect(statement_id: str = "S001") -> ReviewDefect:
    return ReviewDefect(
        defect_id="review-01",
        target_ids=[TARGET_ID],
        statement_ids=[statement_id],
        kind="missing_support",
        severity="critical",
        problem="This statement is not carried by the cited passage.",
    )


def _render(built) -> str:
    return "\n\n".join(message.content for message in review_messages(built))


def _defect_draft(
    *,
    statement_ids: tuple[str, ...] = ("S001",),
    target_ids: tuple[str, ...] = (TARGET_ID,),
    kind: str = "missing_support",
    severity: str = "major",
    problem: str = "The cited passage does not establish this sentence.",
) -> ReviewDefectDraft:
    return ReviewDefectDraft(
        kind=kind,
        severity=severity,
        statement_ids=list(statement_ids),
        target_ids=list(target_ids),
        problem=problem,
    )


def _draft(
    *,
    dimensions: dict[str, float] | None = None,
    dispositions: list[tuple[str, str]] | None = None,
    defects: list[ReviewDefectDraft] | None = None,
    rationale: str = "Reviewed the report against the findings behind it.",
):
    """One provider reply for the written report's three statements."""
    from deep_research.agents.report_reviewer import (
        ReportReviewDraft,
        ReviewDimensionScores,
        StatementDispositionDraft,
    )

    entries = (
        [(statement_id, "supported") for statement_id in WRITTEN_SENTENCES]
        if dispositions is None
        else dispositions
    )
    return ReportReviewDraft(
        dimensions=ReviewDimensionScores(**_scores() if dimensions is None else dimensions),
        statement_dispositions=[
            StatementDispositionDraft(statement_id=statement_id, disposition=disposition)
            for statement_id, disposition in entries
        ],
        defects=list(defects or []),
        rationale=rationale,
    )


# --- dimension scoring and the pass rule ------------------------------------


def test_a_scored_review_that_is_refused_is_never_a_pass() -> None:
    assert not semantic_review_passes(None)
    assert not semantic_review_passes(
        _scored_review().model_copy(
            update={
                "status": "incomplete",
                "dimensions": {},
                "rationale": "incomplete",
            }
        )
    )


@pytest.mark.asyncio
async def test_the_review_opens_an_agent_span_when_one_is_wired() -> None:
    """The reviewer is observable exactly where a tracker is wired to it."""
    from tests.agent_fakes import agent_scope

    tracker = _tracker()
    completer = ScriptedCompleter(outputs=[_draft()])

    async with agent_scope(tracker, agent_name=REPORT_REVIEWER_ROLE):
        review = await review_report(completer, packet(), tracker=tracker)

    assert review.status == "scored"


def test_review_dimensions_are_exactly_the_seven_named_dimensions() -> None:
    assert REVIEW_DIMENSIONS == frozenset(DIMENSION_NAMES)
    assert len(REVIEW_DIMENSIONS) == 7
    assert SEMANTIC_REVIEW_MEAN == 0.80


def test_review_cannot_average_away_a_major_false_claim() -> None:
    review = ReportReview(
        status="scored",
        dimensions={key: 1.0 for key in REVIEW_DIMENSIONS},
        defects=[
            ReviewDefect(
                defect_id="review-01",
                target_ids=[TARGET_ID],
                statement_ids=["S001"],
                kind="missing_support",
                severity="critical",
                problem="The main number is not in the source.",
            )
        ],
        per_statement_dispositions={"S001": "supported"},
        reviewed_statement_ids=["S001"],
        input_fingerprint="packet1",
        rubric_version=REVIEW_RUBRIC_VERSION,
        rationale="A central unsupported assertion.",
    )
    assert not semantic_review_passes(review)


def test_a_perfect_mean_still_fails_when_a_dimension_is_missing() -> None:
    assert semantic_review_passes(_scored_review())

    six = {name: 1.0 for name in DIMENSION_NAMES if name != "uncertainty"}
    assert not semantic_review_passes(
        _scored_review().model_copy(update={"dimensions": six})
    )


def test_a_non_finite_or_out_of_range_score_never_passes() -> None:
    perfect = _scored_review()
    for value in (math.nan, math.inf, -0.1, 1.1):
        assert not semantic_review_passes(
            perfect.model_copy(
                update={"dimensions": {**_scores(), "attribution": value}}
            )
        )


def test_the_mean_is_taken_over_the_declared_dimension_set() -> None:
    below = {name: 0.79 for name in DIMENSION_NAMES}
    assert not semantic_review_passes(_scored_review(dimensions=below))
    at_threshold = {name: SEMANTIC_REVIEW_MEAN for name in DIMENSION_NAMES}
    assert semantic_review_passes(_scored_review(dimensions=at_threshold))


def test_an_unscored_review_never_passes_whatever_its_dimensions_say() -> None:
    perfect = _scored_review()
    for status in ("incomplete", "provider_failed"):
        assert not semantic_review_passes(
            perfect.model_copy(update={"status": status})
        )


def test_an_unsettled_statement_without_a_material_defect_is_refused() -> None:
    """The type boundary: "not established" cannot be recorded as a clean score."""
    with pytest.raises(ValueError):
        _scored_review(
            per_statement_dispositions={
                "S001": UNREVIEWED_STATEMENT_DISPOSITION
            },
            reviewed_statement_ids=[],
        )


def test_a_scored_review_cannot_declare_an_unreviewed_statement() -> None:
    with pytest.raises(ValueError):
        _scored_review(
            unreviewed_statement_ids=["S002"],
            defects=[_unsupported_defect("S002")],
            per_statement_dispositions={"S002": "unsupported"},
        )


def test_a_scored_review_must_disposition_every_statement_it_reviewed() -> None:
    """Reading a statement is not judging it, at the type boundary too."""
    with pytest.raises(ValueError):
        _scored_review(
            reviewed_statement_ids=["S001", "S002"],
            per_statement_dispositions={"S001": "supported"},
        )


def test_a_scored_review_needs_the_seven_dimensions_and_a_fingerprint() -> None:
    with pytest.raises(ValueError):
        _scored_review(dimensions={"completeness": 1.0})
    with pytest.raises(ValueError):
        _scored_review(input_fingerprint="")


def test_a_review_that_is_not_scored_carries_no_scores() -> None:
    with pytest.raises(ValueError):
        _scored_review(status="incomplete")
    assert _scored_review().model_copy(update={"status": "incomplete"}).dimensions


# --- the packet: the whole report, no claims, no batches --------------------


def test_the_packet_holds_statements_findings_and_facts_but_no_claims() -> None:
    built = build_report_review_input(
        state_with_written_report(), state_with_written_report().composition
    )
    assert built.expected_statement_ids == ["S001", "S002", "S003"]
    assert "Generators added 10.4 gigawatts" in built.reader_content or built.findings
    assert not hasattr(built, "claims")


def test_the_packet_carries_every_statement_and_no_claim_or_batch_field() -> None:
    built = packet()
    assert [statement.statement_id for statement in built.statements] == [
        "S001",
        "S002",
        "S003",
    ]
    assert built.statement("S002") is not None
    fields = set(type(built).model_fields)
    assert not [name for name in fields if name.startswith("claim")]
    for forbidden in (
        "verdicts",
        "evidence_batches",
        "targets",
        "sources",
        "ranked_rows",
    ):
        assert forbidden not in fields


def test_a_statement_carries_its_code_built_label_and_its_findings_labels() -> None:
    """The labels are the reader's own: never re-derived, only carried.

    S001 states the figure, so it ends with the key facts row's label, and every
    statement carries the label of each finding it cites.
    """
    built = packet()
    summary = built.statement("S001")
    second = built.statement("S002")

    assert summary is not None and second is not None
    assert f"{EIA}'s own figure" in summary.label
    assert "actual" in summary.label
    assert "January 2025 Preliminary Monthly Electric Generator Inventory" in summary.label
    assert summary.finding_labels
    assert all(f"{EIA}'s own figure" in label for label in summary.finding_labels)
    # A sentence that states no figure gets no key facts label, and still knows
    # the labels of the findings it cites.
    assert second.label == ""
    assert second.finding_labels == summary.finding_labels
    # The label is packet-only material now (spec §3.1 rule 8 cuts it from the
    # printed report; it never reached ``reader_content``, only the request).
    assert summary.label not in built.reader_content
    assert summary.label in _render(built)


def test_the_finding_block_carries_the_snippet_host_and_figure_labels() -> None:
    built = packet()
    [finding] = built.findings

    assert finding.label == "F01"
    assert finding.source_title == EIA_TITLE
    assert finding.host == "eia.gov"
    assert finding.snippet == EIA_SNIPPET
    assert [label for label in finding.figure_labels] == [
        f"{EIA}'s own figure; actual; released 2025-03-12"
    ]
    assert EIA_SNIPPET in _render(built)


def _second_eia_finding() -> object:
    """A second EIA finding whose reader label is identical to the first's.

    Same publisher, same attribution, same kind and same release, so
    ``_finding_label`` renders the same string for both and only the registry
    label (``F01``/``F02``) can tell a statement which snippet it rests on.
    """
    finding = _written_finding().model_copy(
        update={
            "content": "A second EIA page states the same capacity addition.",
            "snippet": (
                "Generators added 9.9 gigawatts (GW) of new battery storage "
                "capacity in 2024"
            ),
        }
    )
    result = FigureResult(
        figure=finding.figures[0].model_copy(update={"value": "9.9"}),
        matched=True,
        evidence_words=finding.snippet,
        context=FigureContext(
            period="2024",
            scope=None,
            attribution="own",
            organisation=EIA,
            kind="actual",
        ),
    )
    return finding.model_copy(
        update={
            "verification": FindingVerification(
                status="verified", figure_results=[result]
            )
        }
    )


def _composition_citing(
    findings: list,
    statement_findings: dict[str, list[str]],
) -> ReportComposition:
    """A composition whose statements cite exactly the findings named for them."""
    composition = _written_composition()
    labels = {
        f"F{index:02d}": finding_fingerprint(finding)
        for index, finding in enumerate(findings, start=1)
    }
    statements = {
        statement_id: ReportStatement(
            statement_id=statement_id,
            text=WRITTEN_SENTENCES[statement_id],
            finding_ids=list(finding_ids),
            target_ids=[TARGET_ID],
        )
        for statement_id, finding_ids in statement_findings.items()
    }
    points = [
        ReportPoint(
            text=statement.text,
            source_urls=[EIA_URL],
            statement=statement,
        )
        for statement in statements.values()
    ]
    return composition.model_copy(
        update={
            "findings": list(findings),
            "finding_labels": labels,
            "summary": points[:1],
            "sections": [ReportSection(title="Additions in 2024", points=points[1:])],
        }
    )


def test_a_statement_names_the_registry_labels_of_the_findings_it_cites() -> None:
    """Two findings can share a reader label; only F-labels tell them apart.

    ``_finding_label`` is built from organisation, attribution, kind and
    release, so two findings from one publisher with the same kind and release
    render the *same* string. A statement that carries only that string cannot
    be matched to a snippet, and the reviewer would judge it against a guess.
    """
    first = _written_finding()
    second = _second_eia_finding()
    composition = _composition_citing(
        [first, second],
        {
            "S001": [finding_fingerprint(first)],
            "S002": [finding_fingerprint(second)],
        },
    )
    built = build_report_review_input(state_with_written_report(composition=composition))

    # The two findings' descriptive labels are identical...
    assert (
        built.statement("S001").finding_labels
        == built.statement("S002").finding_labels
    )
    # ...so each statement must name its own registry label.
    assert built.statement("S001").finding_refs == ["F01"]
    assert built.statement("S002").finding_refs == ["F02"]
    assert {finding.label for finding in built.findings} == {"F01", "F02"}

    rendered = _render(built)
    assert "### F01" in rendered and "### F02" in rendered
    s001 = rendered.split("- S002")[0].split("- S001")[1]
    s002 = rendered.split("- S002")[1]
    assert "cites: F01" in s001 and "cites: F02" not in s001
    assert "cites: F02" in s002 and "cites: F01" not in s002


def test_two_revision_editions_with_one_fingerprint_keep_their_own_snippets() -> None:
    """A shared fingerprint must not collapse two editions onto one snippet.

    Two revision editions of one page -- an unchanged URL, sub-topic and
    content, with only the structured figure or its release differing -- share a
    ``finding_fingerprint`` and are both registered in ``finding_labels``. A
    fingerprint-keyed lookup hands the later edition's snippet to both labels,
    so a statement citing the first is judged against the wrong evidence.
    """
    first = _written_finding()
    second = _written_finding().model_copy(
        update={
            "snippet": "EARLIER EDITION: Generators added 9.9 gigawatts in 2024",
            "release_date": "2024-06-01",
        }
    )
    shared = finding_fingerprint(first)
    assert finding_fingerprint(second) == shared
    composition = _composition_citing([first, second], {"S001": [shared], "S002": [shared]})
    built = build_report_review_input(state_with_written_report(composition=composition))

    shown = {finding.label: finding for finding in built.findings}
    assert shown["F01"].snippet == first.snippet
    assert shown["F02"].snippet == second.snippet
    assert shown["F01"].figure_labels != shown["F02"].figure_labels
    assert "released 2025-03-12" in shown["F01"].figure_labels[0]
    assert "released 2024-06-01" in shown["F02"].figure_labels[0]


def test_the_key_facts_and_not_found_lines_reach_the_request() -> None:
    built = packet()

    assert built.fact_rows and "10.4 GW" in built.fact_rows[0]
    assert "battery storage power capacity added" in built.fact_rows[0]
    assert built.not_found == ["What is the 2025 capacity addition forecast?"]
    rendered = _render(built)
    assert "10.4 GW" in rendered
    assert "What is the 2025 capacity addition forecast?" in rendered


def test_the_deterministic_block_reports_unjudged_sentences_not_untraced_figures() -> None:
    """PD-10, D8: ``unjudged_sentences`` replaced ``untraced_figures``."""
    state = state_with_written_report(
        quality=_quality(
            hard_failures=["missing_reader_report"],
            unjudged_sentences=["S003"],
            duplicate_fact_rows=0,
            unresolved_citations=1,
            uncited_settled_points=2,
        )
    )
    built = build_report_review_input(state)

    assert built.deterministic.hard_checks == ["missing_reader_report"]
    assert built.deterministic.unjudged_sentences == ["S003"]
    assert built.deterministic.unresolved_citations == 1
    assert built.deterministic.uncited_settled_points == 2
    assert not hasattr(built.deterministic, "untraced_figures")
    rendered = _render(built)
    assert "S003" in rendered
    assert "untraced figures" not in rendered.casefold()


def test_the_report_is_never_prefix_clipped() -> None:
    closing = "The final row contradicts the opening claim."
    composition = _written_composition()
    report = f"{'Filler sentence. ' * 1_200}\n{closing}"
    assert len(report) > 16_000

    built = build_report_review_input(
        state_with_written_report(composition=composition, report=report)
    )

    assert built.reader_content == report
    assert built.reader_content.rstrip().endswith(closing)
    assert closing in _render(built)


def test_a_late_contradiction_beyond_16000_characters_is_reviewed() -> None:
    late = "Break-even was later disputed."
    report = f"{'Filler sentence. ' * 1_200}\n{late}"

    assert late in _render(
        build_report_review_input(state_with_written_report(report=report))
    )


def test_the_review_request_cannot_see_another_reviewers_score_or_the_threshold() -> None:
    """The packet carries no coaching: no score, no bar, no prior judgement."""
    rendered = _render(packet()).casefold()

    # Word-bounded: "critical target" is a plan concept and "critical" is a
    # defect severity, not coaching — the check is for the *other* reviewer.
    assert re.search(r"\bcritic\b", rendered) is None
    for forbidden in (
        "fact checker",
        "acceptance threshold",
        "suggested verdict",
        "should_continue",
        "prior run",
        "mean score",
    ):
        assert forbidden not in rendered


def test_the_review_packet_carries_no_score_field_at_all() -> None:
    fields = set(type(packet()).model_fields)
    for forbidden in ("critic_score", "score", "threshold", "verdict", "quality"):
        assert forbidden not in fields


def test_the_request_names_the_exact_fingerprint_it_reviews() -> None:
    built = packet()
    assert built.fingerprint in _render(built)


def test_the_manifest_marks_which_target_ids_are_required() -> None:
    """P3b (RevRouteR3): REVIEW_DEFECT_RULES tells the reviewer a coverage
    defect on a *required* target is always material, but nothing in the
    packet said which target ids those are -- the manifest must show it.
    """
    rendered = _render(packet())

    assert f"{TARGET_ID} [required]" in rendered


@pytest.mark.asyncio
async def test_a_real_written_report_builds_the_same_packet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fixture is the writer's shape: proved against the writer itself.

    ``compose_written_report`` numbers its candidates S001… in bottom-line-
    then-sections render order (spec §6.7), and a real composition must
    produce the same three statement ids, the same label and the same key
    facts line the hand-built fixture does.
    """
    from deep_research.agents.report_writer import ReportWriterTask, compose_written_report
    from deep_research.utils.types import BottomLineDraft, SectionDraft, WriterPointDraft as _WriterPointDraft

    class _Verdict:
        def __init__(self, label: str) -> None:
            self.label = label
            self.verdict = "consistent"
            self.corrected_text = ""
            self.reason = "the finding states it"

    async def consistent(
        provider, items, *, question, fingerprint=None,
        batch_size=None, concurrency=None, gate=None,
    ):
        # The bounds and the shared gate are part of the call the real
        # checker accepts (PD-12; spec §6.5's shared semaphore).
        del provider, question, fingerprint, batch_size, concurrency, gate
        return {item.label: _Verdict(item.label) for item in items}, []

    monkeypatch.setattr(
        "deep_research.agents.evidence_verifier.check_statements", consistent
    )
    finding = _written_finding()
    task = ReportWriterTask(
        session_id=SESSION_ID,
        instruction=QUESTION,
        question=QUESTION,
        iteration=0,
        max_extra_passes=1,
        as_of="2026-08-01",
        scope="United States",
        generated_on="2026-08-01",
        sub_topics=[_topic()],
        targets=[make_target(TARGET_ID)],
        findings=[finding],
        sources=[],
        registry=[("F01", finding)],
        facts=[_fact_row(finding_fingerprint(finding))],
        not_found=[],
        answered={TARGET_ID: [finding_fingerprint(finding)]},
    )
    completer = ScriptedCompleter(
        outputs=[
            SectionDraft(
                title="Additions in 2024",
                points=[
                    _WriterPointDraft(text=WRITTEN_SENTENCES["S002"], finding_labels=["F01"]),
                    _WriterPointDraft(text=WRITTEN_SENTENCES["S003"], finding_labels=["F01"]),
                ],
            ),
            BottomLineDraft(
                sentences=[
                    _WriterPointDraft(text=WRITTEN_SENTENCES["S001"], finding_labels=["F01"])
                ]
            ),
        ]
    )

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    built = build_report_review_input(
        state_with_written_report(
            composition=composition, report=render_written_report(composition)
        )
    )

    assert built.expected_statement_ids == ["S001", "S002", "S003"]
    assert built.statements[0].label == packet().statements[0].label
    assert built.fact_rows == packet().fact_rows
    assert built.findings[0].snippet == packet().findings[0].snippet


# --- fingerprint semantics --------------------------------------------------


def test_a_cosmetic_status_badge_does_not_invalidate_the_fingerprint() -> None:
    before = report_review_input_fingerprint(packet())

    badged = build_report_review_input(
        state_with_written_report(
            composition=_written_composition(quality_status="accepted")
        )
    )
    assert before == report_review_input_fingerprint(badged)


def test_a_content_change_invalidates_the_fingerprint() -> None:
    before = report_review_input_fingerprint(packet())

    composition = _written_composition()
    changed = composition.model_copy(
        update={
            "sections": [
                ReportSection(
                    title="Additions in 2024",
                    points=[
                        ReportPoint(
                            text="Battery storage additions were never confirmed.",
                            source_urls=[EIA_URL],
                            statement=ReportStatement(
                                statement_id="S002",
                                text="Battery storage additions were never confirmed.",
                                finding_ids=list(
                                    composition.sections[0].points[0].statement.finding_ids
                                ),
                                target_ids=[TARGET_ID],
                            ),
                        ),
                        composition.sections[0].points[1],
                    ],
                )
            ]
        }
    )
    after = build_report_review_input(
        state_with_written_report(composition=changed)
    )

    assert before != report_review_input_fingerprint(after)


def test_a_statement_change_invalidates_the_fingerprint() -> None:
    """The statements are judged, so a change to one is a change of material."""
    composition = _written_composition()
    before = report_review_input_fingerprint(packet())

    retitled = composition.model_copy(
        update={
            "summary": [
                composition.summary[0].model_copy(
                    update={
                        "statement": composition.summary[0].statement.model_copy(
                            update={"text": "Battery storage additions were 10.4 GW."}
                        )
                    }
                )
            ]
        }
    )

    assert before != report_review_input_fingerprint(
        build_report_review_input(
            state_with_written_report(composition=retitled)
        )
    )


def test_a_not_found_change_invalidates_the_fingerprint() -> None:
    """A target the run could not answer is part of what the reviewer reads."""
    before = report_review_input_fingerprint(packet())

    assert before != report_review_input_fingerprint(
        build_report_review_input(
            state_with_written_report(
                composition=_written_composition(not_found=False)
            )
        )
    )


def test_a_reference_change_invalidates_the_fingerprint() -> None:
    """The cited finding's snippet is the evidence the reviewer reads."""
    before = report_review_input_fingerprint(packet())

    finding = _written_finding().model_copy(
        update={"snippet": EIA_PAGE.replace("66%", "67%")}
    )
    composition = _written_composition().model_copy(update={"findings": [finding]})
    after = build_report_review_input(
        state_with_written_report(composition=composition)
    )

    assert before != report_review_input_fingerprint(after)


def test_the_composition_fingerprint_ignores_the_presentation_badge() -> None:
    """A stamp the finalizer writes is not a content change."""
    composition = _written_composition()
    before = composition_semantic_fingerprint(composition)

    assert before
    assert (
        composition_semantic_fingerprint(
            composition.model_copy(update={"quality_status": "accepted"})
        )
        == before
    )


def test_the_composition_fingerprint_moves_with_content_and_references() -> None:
    composition = _written_composition()
    before = composition_semantic_fingerprint(composition)

    reworded = composition.model_copy(
        update={
            "sections": [
                ReportSection(
                    title="Additions in 2024",
                    points=[
                        composition.sections[0].points[0].model_copy(
                            update={
                                "statement": composition.sections[0]
                                .points[0]
                                .statement.model_copy(
                                    update={"text": "Something else entirely."}
                                )
                            }
                        ),
                        composition.sections[0].points[1],
                    ],
                )
            ]
        }
    )
    assert composition_semantic_fingerprint(reworded) != before

    re_registered = composition.model_copy(
        update={
            "findings": [
                _written_finding().model_copy(update={"content": "Other text."})
            ]
        }
    )
    assert composition_semantic_fingerprint(re_registered) != before

    re_facted = composition.model_copy(
        update={
            "fact_rows": [
                composition.fact_rows[0].model_copy(update={"value": "10.3 GW"})
            ]
        }
    )
    assert composition_semantic_fingerprint(re_facted) != before

    re_not_found = composition.model_copy(update={"not_found": []})
    assert composition_semantic_fingerprint(re_not_found) != before

    # The plan is not part of what the reviewer judges: it never sees it, and
    # the same report judged by the same packet is the same judgement.
    re_planned = composition.model_copy(update={"sub_topics": []})
    assert composition_semantic_fingerprint(re_planned) == before


def test_replacing_the_composition_invalidates_a_mismatched_review() -> None:
    """The state rule: a judgement belongs to the report it judged."""
    from deep_research.utils.types import merge_research_state

    composition = _written_composition()
    stored = _scored_review(
        composition_fingerprint=composition_semantic_fingerprint(composition)
    )
    state = state_with_written_report(composition=composition, report_review=stored)

    # The same content, re-stamped: the review survives.
    restamped = composition.model_copy(update={"quality_status": "accepted"})
    kept = merge_research_state(state, {"composition": restamped})
    assert kept.report_review is not None
    assert kept.report_review.input_fingerprint == "packet-1"

    # Different content: the review is gone, and gone is never "passed".
    changed = composition.model_copy(
        update={
            "fact_rows": [
                composition.fact_rows[0].model_copy(update={"value": "10.3 GW"})
            ]
        }
    )
    dropped = merge_research_state(state, {"composition": changed})
    assert dropped.report_review is None


def test_the_composition_fingerprint_covers_section_points() -> None:
    """A themed findings bullet is reader-visible content, like the summary."""
    composition = _written_composition()
    bullet = ReportStatement(
        statement_id="S004",
        text="Break-even was reached in the survey's own wording.",
        finding_ids=list(composition.summary[0].statement.finding_ids),
        target_ids=[TARGET_ID],
    )
    section = ReportSection(
        title="Error correction",
        points=[
            ReportPoint(
                text=bullet.text,
                source_urls=[EIA_URL],
                statement=bullet,
            )
        ],
    )
    with_sections = composition.model_copy(
        update={"sections": [*composition.sections, section]}
    )
    before = composition_semantic_fingerprint(with_sections)
    assert before

    reworded = with_sections.model_copy(
        update={
            "sections": [
                *composition.sections,
                ReportSection(
                    title="Error correction",
                    points=[
                        ReportPoint(
                            text="Break-even was never reached.",
                            source_urls=[EIA_URL],
                            statement=bullet.model_copy(
                                update={"text": "Break-even was never reached."}
                            ),
                        )
                    ],
                ),
            ]
        }
    )
    assert composition_semantic_fingerprint(reworded) != before


# --- one call per review ----------------------------------------------------


@pytest.fixture
def reviewer_with_reply() -> Callable[..., tuple[ReportReviewer, ScriptedCompleter]]:
    """A reviewer whose one provider call answers with a scripted reply."""

    def build(
        *,
        all_supported: bool = True,
        unsupported: tuple[str, ...] = (),
        skip: tuple[str, ...] = (),
        contradicting: tuple[str, ...] = (),
        defects: tuple[ReviewDefectDraft, ...] = (),
    ) -> tuple[ReportReviewer, ScriptedCompleter]:
        dispositions = [
            (
                statement_id,
                "unsupported"
                if statement_id in unsupported or not all_supported
                else "supported",
            )
            for statement_id in WRITTEN_SENTENCES
            if statement_id not in skip
        ]
        reply = _draft(
            dispositions=dispositions,
            defects=[
                *(
                    [
                        _defect_draft(
                            statement_ids=tuple(contradicting),
                            kind="presentation",
                            problem=(
                                "The sentence's prose contradicts its own "
                                "code-built label."
                            ),
                        )
                    ]
                    if contradicting
                    else []
                ),
                *defects,
            ],
        )
        completer = ScriptedCompleter(outputs=[reply])
        return ReportReviewer(provider=completer), completer

    return build


@pytest.fixture
def reviewer_truncating() -> ReportReviewer:
    """A reviewer whose request is truncated twice: the re-ask, then a failure."""
    return ReportReviewer(
        provider=ScriptedCompleter(
            outputs=[_output_limit_error(), _output_limit_error()]
        ),
        config=AgentRuntimeConfig(report_review_max_tokens=4096),
    )


@pytest.mark.asyncio
async def test_one_call_judges_every_statement(reviewer_with_reply) -> None:
    reviewer, completer = reviewer_with_reply(all_supported=True)
    review = await reviewer.review(packet(), previous=None)
    assert review.status == "scored" and [n for n, _, _ in completer.calls] == ["ReportReviewDraft"]
    assert set(review.per_statement_dispositions.values()) == {"supported"}


@pytest.mark.asyncio
async def test_an_unsupported_statement_is_a_material_defect(reviewer_with_reply) -> None:
    reviewer, _ = reviewer_with_reply(unsupported=["S002"])
    review = await reviewer.review(packet(), previous=None)
    assert "S002" in review.derived_defect_statement_ids and not semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_missing_disposition_leaves_the_review_incomplete(reviewer_with_reply) -> None:
    reviewer, _ = reviewer_with_reply(skip=["S003"])
    assert (await reviewer.review(packet(), previous=None)).status == "incomplete"


@pytest.mark.asyncio
async def test_a_defect_for_prose_against_its_label_is_kept(reviewer_with_reply) -> None:
    reviewer, _ = reviewer_with_reply(contradicting=["S001"])
    review = await reviewer.review(packet(), previous=None)
    assert [d.statement_ids for d in review.defects if d.material] == [["S001"]]


@pytest.mark.asyncio
async def test_a_truncated_reply_is_asked_once_more_then_a_failure_is_recorded(reviewer_truncating) -> None:
    review = await reviewer_truncating.review(packet(), previous=None)
    assert review.status == "provider_failed" and len(reviewer_truncating.provider.calls) == 2


@pytest.mark.asyncio
async def test_a_complete_review_is_scored() -> None:
    built = packet()
    completer = ScriptedCompleter(outputs=[_draft()])

    review = await review_report(completer, built)

    assert review.status == "scored"
    assert set(review.dimensions) == REVIEW_DIMENSIONS
    assert review.input_fingerprint == built.fingerprint
    assert review.rubric_version == REVIEW_RUBRIC_VERSION
    assert review.reviewed_statement_ids == ["S001", "S002", "S003"]
    assert review.unreviewed_statement_ids == []
    assert review.per_statement_dispositions == {
        statement_id: "supported" for statement_id in WRITTEN_SENTENCES
    }
    assert len(completer.calls) == 1
    assert semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_reply_that_records_no_statement_disposition_is_incomplete() -> None:
    """Claiming nothing read is not the review: the judgement has to be recorded.

    A reply with a perfect mean and no dispositions has skipped the
    per-statement review this task exists to perform. Missing statement
    dispositions are incomplete, not a default pass.
    """
    built = packet()
    completer = ScriptedCompleter(outputs=[_draft(dispositions=[])])

    review = await review_report(completer, built)

    assert len(completer.calls) == 1
    assert review.status == "incomplete"
    assert review.dimensions == {}
    assert review.unreviewed_statement_ids == ["S001", "S002", "S003"]
    assert review.per_statement_dispositions == {
        statement_id: UNREVIEWED_STATEMENT_DISPOSITION
        for statement_id in WRITTEN_SENTENCES
    }
    assert not semantic_review_passes(review)
    assert "S001" in review.rationale
    assert "S003" in review.rationale


@pytest.mark.asyncio
async def test_a_missing_statement_review_is_incomplete_not_a_default_pass() -> None:
    built = packet()
    completer = ScriptedCompleter(outputs=[_draft(dispositions=[("S001", "supported")])])

    review = await review_report(completer, built)

    assert review.status == "incomplete"
    assert review.reviewed_statement_ids == ["S001"]
    assert review.unreviewed_statement_ids == ["S002", "S003"]
    assert not semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_truncated_review_call_is_re_asked_once_at_a_high_effort() -> None:
    """A truncated request is re-asked once, at the effort that leaves budget.

    The retry is recorded because it was a second paid call: without the record
    nothing in the artifacts tells this review apart from one that took a
    single request.
    """
    completer = ScriptedCompleter(outputs=[_output_limit_error(), _draft()])
    reviewer = ReportReviewer(
        provider=completer,
        config=AgentRuntimeConfig(report_review_max_tokens=4096),
    )

    review = await reviewer.review(packet())

    assert review.status == "scored"
    assert completer.budgets == [4096, 4096]
    assert completer.efforts == [None, "high"]
    assert [error.error_type for error in reviewer.review_records] == [
        "report_review_output_limit_retry"
    ]
    retry = reviewer.review_records[0]
    assert retry.recoverable is True
    assert retry.source == f"agent.{REPORT_REVIEWER_ROLE}"
    assert retry.details["outcome"] == "answered"
    assert retry.details["max_tokens"] == 4096


@pytest.mark.asyncio
async def test_a_review_retry_that_hits_an_outage_is_recorded_as_failed() -> None:
    """A retry that fails at the provider is recorded, and the path is unchanged."""
    completer = ScriptedCompleter(
        outputs=[
            _output_limit_error(),
            ProviderResponseError(
                "provider unavailable",
                retryable=True,
                failure_category="http",
                http_status_code=503,
                failure_origin="sdk",
            ),
        ]
    )
    reviewer = ReportReviewer(
        provider=completer,
        config=AgentRuntimeConfig(report_review_max_tokens=4096),
    )

    review = await reviewer.review(packet())

    assert completer.efforts == [None, "high"]
    assert review.status == "provider_failed"
    assert review.dimensions == {}
    assert [error.error_type for error in reviewer.review_records] == [
        "report_review_output_limit_retry"
    ]
    assert reviewer.review_records[0].details["outcome"] == "failed"
    assert reviewer.review_records[0].recoverable is True


@pytest.mark.asyncio
async def test_a_second_truncation_keeps_the_non_fatal_unjudged_path() -> None:
    """Two truncations leave the report unjudged, and never fail the run."""
    completer = ScriptedCompleter(
        outputs=[_output_limit_error(), _output_limit_error()]
    )
    reviewer = ReportReviewer(
        provider=completer,
        config=AgentRuntimeConfig(report_review_max_tokens=4096),
    )

    review = await reviewer.review(packet())

    assert review.status == "provider_failed"
    assert review.dimensions == {}
    assert not semantic_review_passes(review)
    assert completer.efforts == [None, "high"]
    retry = reviewer.review_records[0]
    assert retry.error_type == "report_review_output_limit_retry"
    assert retry.details["outcome"] == "truncated"
    assert retry.recoverable is True


@pytest.mark.asyncio
async def test_a_provider_failure_is_recorded_provider_failed_never_scored() -> None:
    from deep_research.providers import ProviderError

    completer = ScriptedCompleter(outputs=[ProviderError("the provider is down")])

    review = await review_report(completer, packet())

    assert review.status == "provider_failed"
    assert review.dimensions == {}
    assert not semantic_review_passes(review)
    assert review.rationale.strip()
    assert len(completer.calls) == 1


@pytest.mark.asyncio
async def test_a_schema_failure_is_incomplete_not_a_default_pass() -> None:
    completer = ScriptedCompleter(outputs=[{"not": "a draft"}])

    review = await review_report(completer, packet())

    assert review.status == "incomplete"
    assert not semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_fingerprint_mismatch_is_incomplete_not_a_default_pass() -> None:
    built = packet()
    completer = ScriptedCompleter(outputs=[_draft()])

    review = await review_report(
        completer,
        built,
        reviewed_fingerprint="a-different-packet",
    )

    assert review.status == "incomplete"
    assert review.input_fingerprint == built.fingerprint
    assert not semantic_review_passes(review)
    assert completer.calls == []


@pytest.mark.asyncio
async def test_failed_review_records_validation_fields_without_provider_values() -> None:
    from deep_research.providers import (
        StructuredOutputError,
        StructuredValidationDiagnostic,
    )

    diagnostics = (
        StructuredValidationDiagnostic(
            attempt=1,
            field_paths=("dimensions.attribution",),
            category="missing",
        ),
        StructuredValidationDiagnostic(
            attempt=2,
            field_paths=("defects.0.problem",),
            category="string_bounds",
        ),
    )
    completer = ScriptedCompleter(
        outputs=[
            StructuredOutputError(
                "provider body contains SECRET_REVIEW_VALUE",
                diagnostics=diagnostics,
            )
        ]
    )

    review = await review_report(completer, packet())

    assert review.status == "incomplete"
    assert review.dimensions == {}
    assert "attempt=1 category=missing field_paths=dimensions.attribution" in review.rationale
    assert "attempt=2 category=string_bounds field_paths=defects.0.problem" in review.rationale
    assert "SECRET_REVIEW_VALUE" not in review.rationale


@pytest.mark.asyncio
async def test_an_invalid_retry_after_truncation_keeps_its_validation_fields() -> None:
    """The re-ask path must not strip the diagnostics off the schema failure."""
    from deep_research.providers import (
        StructuredOutputError,
        StructuredValidationDiagnostic,
    )

    completer = ScriptedCompleter(
        outputs=[
            _output_limit_error(),
            StructuredOutputError(
                "safe schema failure",
                diagnostics=(
                    StructuredValidationDiagnostic(
                        attempt=2,
                        field_paths=("defects",),
                        category="other_schema",
                        error_types=("too_long",),
                    ),
                ),
            ),
        ]
    )
    reviewer = ReportReviewer(
        provider=completer,
        config=AgentRuntimeConfig(report_review_max_tokens=4096),
    )

    review = await reviewer.review(packet())

    assert review.status == "incomplete"
    assert (
        "attempt=2 category=other_schema field_paths=defects error_types=too_long"
        in review.rationale
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("seconds_past_deadline", "expected_status"),
    [(-60, "scored"), (60, "provider_failed")],
)
async def test_report_judge_generation_respects_its_own_request_deadline(
    seconds_past_deadline: int, expected_status: str
) -> None:
    """A long complete review must be scored; a later transport timeout cannot be.

    The deadline is the shipped reviewer's own: a generation that finishes
    inside it is scored, and one that runs past it is a provider failure.
    """
    raw = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    config = LLMConfig.model_validate(raw["llm"]).model_copy(
        update={"retry_count": 0}
    )
    deadline = config.resolve_for("report_reviewer").timeout
    generation_seconds = deadline + seconds_past_deadline

    class TimedResponses:
        async def create(self, **kwargs):
            if generation_seconds > kwargs.get("timeout", config.timeout):
                raise APITimeoutError(
                    request=httpx.Request("POST", "https://api.deepseek.com/responses")
                )
            return SimpleNamespace(
                status="completed",
                incomplete_details=None,
                output_text=_draft().model_dump_json(),
                model="deepseek-v4-flash",
                usage=SimpleNamespace(
                    input_tokens=100, output_tokens=1000, total_tokens=1100
                ),
            )

    tracker = _tracker()
    provider = DeepSeekSchemaChatProvider(
        config, tracker, client=SimpleNamespace(responses=TimedResponses())
    )
    async with tracker.session_span(SESSION_ID, QUESTION):
        review = await ReportReviewer(provider=provider).review(packet())

    assert review.status == expected_status
    if expected_status == "scored":
        assert semantic_review_passes(review)
    else:
        assert review.dimensions == {}
        assert "ProviderTimeoutError" in review.rationale
        assert not semantic_review_passes(review)


# --- the reply's defects ----------------------------------------------------


@pytest.mark.asyncio
async def test_a_complete_review_keeps_all_distinct_material_defects() -> None:
    """One defect per statement must not lose the thirteenth finding."""
    statement_ids = [f"S{number:03d}" for number in range(1, 14)]
    sentences = {
        statement_id: f"Unsupported assertion number {index}."
        for index, statement_id in enumerate(statement_ids, start=1)
    }
    composition = _many_statement_composition(sentences)
    built = build_report_review_input(state_with_written_report(composition=composition))
    reply = _draft(
        dimensions=_scores(0.2),
        dispositions=[(statement_id, "unsupported") for statement_id in statement_ids],
        defects=[
            _defect_draft(
                statement_ids=(statement_id,),
                problem=f"No cited passage establishes assertion {index}.",
            )
            for index, statement_id in enumerate(statement_ids, start=1)
        ],
    )
    completer = ScriptedCompleter(outputs=[reply])

    review = await review_report(completer, built)

    assert review.status == "scored"
    assert [defect.statement_ids for defect in review.material_defects] == [
        [statement_id] for statement_id in statement_ids
    ]
    assert not semantic_review_passes(review)


def _many_statement_composition(sentences: dict[str, str]) -> ReportComposition:
    """A composition with one summary point per sentence, in id order."""
    composition = _written_composition()
    statements = {
        statement_id: ReportStatement(
            statement_id=statement_id,
            text=text,
            finding_ids=list(composition.summary[0].statement.finding_ids),
            target_ids=[TARGET_ID],
        )
        for statement_id, text in sentences.items()
    }
    return composition.model_copy(
        update={
            "summary": [
                ReportPoint(
                    text=text,
                    source_urls=[EIA_URL],
                    statement=statements[statement_id],
                )
                for statement_id, text in sentences.items()
            ],
            "sections": [],
        }
    )


@pytest.mark.asyncio
async def test_a_reply_over_the_stated_defect_bound_is_refused_not_cut() -> None:
    """The request states the defect bound it enforces, and beyond it refuses."""
    built = packet()
    stated = re.search(r"at most (\d+) defects", _render(built))
    assert stated is not None
    bound = int(stated.group(1))
    reply = _draft(
        defects=[
            _defect_draft(
                statement_ids=("S001",),
                problem=f"Distinct unsupported reading number {index}.",
            )
            for index in range(bound + 1)
        ]
    )

    review = await review_report(ScriptedCompleter(outputs=[reply]), built)

    assert bound >= len(built.expected_statement_ids)
    assert review.status == "incomplete"
    assert review.defects == []
    assert not semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_defect_with_an_unknown_id_keeps_its_problem_and_loses_the_id() -> None:
    """A scope id this packet does not carry is dropped; the problem stays.

    The reviewer found something real; what it got wrong was the address. Losing
    the address must not lose the finding, and a phantom id must never enter the
    record as a resolvable scope.
    """
    built = packet()
    completer = ScriptedCompleter(
        outputs=[
            _draft(
                defects=[
                    _defect_draft(
                        statement_ids=("S999",),
                        target_ids=("t-does-not-exist",),
                        kind="coverage",
                        severity="critical",
                        problem="A topic nobody planned is missing.",
                    )
                ]
            )
        ]
    )

    review = await review_report(completer, built)

    assert review.status == "scored"
    assert [defect.statement_ids for defect in review.defects] == [[]]
    assert review.defects[0].target_ids == []
    assert review.defects[0].problem == "A topic nobody planned is missing."
    assert not semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_defect_with_an_unknown_kind_is_dropped_and_the_drop_is_recorded() -> None:
    """The defect vocabulary is closed: a new kind is not a new category."""
    built = packet()
    completer = ScriptedCompleter(
        outputs=[
            _draft(
                defects=[
                    _defect_draft(kind="vibes", problem="This feels wrong."),
                    _defect_draft(
                        statement_ids=("S002",),
                        severity="minor",
                        kind="presentation",
                        problem="This sentence reads awkwardly.",
                    ),
                ]
            )
        ]
    )

    review = await review_report(completer, built)

    assert review.status == "scored"
    assert [defect.problem for defect in review.defects] == [
        "This sentence reads awkwardly."
    ]
    assert "vibes" in review.rationale
    assert semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_dropped_defect_note_names_only_the_field_that_failed() -> None:
    """The note is the only record of the drop, so it must not misstate it.

    A valid kind with an unknown severity was recorded as "the kind is not a
    defect kind", which names a field that was in fact correct and hides which
    one to fix.
    """
    built = packet()
    completer = ScriptedCompleter(
        outputs=[
            _draft(
                defects=[
                    _defect_draft(kind="coverage", severity="urgent"),
                    _defect_draft(kind="vibes", severity="major"),
                ]
            )
        ]
    )

    review = await review_report(completer, built)

    assert review.status == "scored"
    assert review.defects == []
    assert "'urgent' is not a defect severity" in review.rationale
    assert "'coverage' is not a defect kind" not in review.rationale
    assert "'vibes' is not a defect kind" in review.rationale
    assert "'major' is not a defect severity" not in review.rationale


@pytest.mark.asyncio
async def test_a_coverage_defect_on_a_required_target_is_always_material() -> None:
    """A coverage defect on a required target is material whatever severity
    the model gave it (D11): the model called an identical missing-half-answer
    problem major in one review and minor in another, and routing cannot be
    left to that inconsistency.
    """
    built = packet()
    completer = ScriptedCompleter(
        outputs=[
            _draft(
                defects=[
                    _defect_draft(
                        statement_ids=("S001",),
                        target_ids=(TARGET_ID,),
                        kind="coverage",
                        severity="minor",
                        problem="The question's part has no named answer.",
                    )
                ]
            )
        ]
    )

    review = await review_report(completer, built)

    assert review.status == "scored"
    [defect] = review.defects
    assert defect.severity == "major"
    assert defect.material
    assert not semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_disposition_outside_the_packet_is_refused() -> None:
    """A judgement about a record this packet does not carry is not a judgement."""
    built = packet()
    completer = ScriptedCompleter(
        outputs=[
            _draft(
                dispositions=[
                    *[(sid, "supported") for sid in WRITTEN_SENTENCES],
                    ("S999", "supported"),
                ]
            )
        ]
    )

    review = await review_report(completer, built)

    assert review.status == "incomplete"
    assert review.per_statement_dispositions == {}
    assert "S999" in review.rationale
    assert not semantic_review_passes(review)


# --- dispositions and the defects they derive -------------------------------


@pytest.mark.asyncio
async def test_an_unsettled_disposition_always_blocks_acceptance() -> None:
    """A reply that says "unsupported" cannot also claim a clean review."""
    built = packet()
    completer = ScriptedCompleter(
        outputs=[
            _draft(
                dispositions=[
                    ("S001", "unsupported"),
                    ("S002", "supported"),
                    ("S003", "supported"),
                ]
            )
        ]
    )

    review = await review_report(completer, built)

    assert review.status == "scored"
    assert review.per_statement_dispositions["S001"] == "unsupported"
    assert review.derived_defect_statement_ids == ["S001"]
    assert review.material_defects
    assert not semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_minor_defect_cannot_suppress_the_derived_material_defect() -> None:
    """A contract-valid reply is recorded, never left to crash the run.

    ``minor`` is not material, so a reply that dispositions a statement
    ``unsupported`` and returns only a *minor* defect naming it must still get
    the material defect the record contract requires derived for it, rather
    than a ``ValidationError`` out of ``review_report``.
    """
    built = packet()
    completer = ScriptedCompleter(
        outputs=[
            _draft(
                dispositions=[
                    ("S001", "unsupported"),
                    ("S002", "supported"),
                    ("S003", "supported"),
                ],
                defects=[
                    _defect_draft(
                        statement_ids=("S001",),
                        kind="presentation",
                        severity="minor",
                        problem="The sentence reads awkwardly.",
                    )
                ],
            )
        ]
    )

    review = await review_report(completer, built)

    assert review.status == "scored"
    assert review.derived_defect_statement_ids == ["S001"]
    (derived,) = [
        defect for defect in review.defects if defect.defect_id.startswith("review-")
        and defect.material
    ]
    assert derived.material
    assert derived.statement_ids == ["S001"]
    assert derived.target_ids == [TARGET_ID]
    assert derived.kind == "missing_support"
    assert not semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_later_supported_cannot_overwrite_an_earlier_unsupported() -> None:
    """Two readings of one statement resolve to the less settled one.

    A reply that says both "this sentence is not in its source" and "this
    sentence is supported" has not agreed with itself, and recording the
    agreement is the one reading that must never happen.
    """
    built = packet()
    completer = ScriptedCompleter(
        outputs=[
            _draft(
                dispositions=[
                    ("S001", "unsupported"),
                    ("S001", "supported"),
                    ("S002", "supported"),
                    ("S003", "supported"),
                ]
            )
        ]
    )

    review = await review_report(completer, built)

    assert review.per_statement_dispositions["S001"] == "unsupported"
    assert review.derived_defect_statement_ids == ["S001"]
    assert not semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_later_unsupported_reading_displaces_an_earlier_supported() -> None:
    """The same rule in the other order: the doubt is never the one dropped."""
    built = packet()
    completer = ScriptedCompleter(
        outputs=[
            _draft(
                dispositions=[
                    ("S001", "supported"),
                    ("S001", "unsupported"),
                    ("S002", "supported"),
                    ("S003", "supported"),
                ]
            )
        ]
    )

    review = await review_report(completer, built)

    assert review.per_statement_dispositions["S001"] == "unsupported"
    assert review.derived_defect_statement_ids == ["S001"]
    assert not semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_judgement_that_cannot_be_recorded_is_incomplete_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The backstop: the merge is the last place a contract violation can land.

    Assembling the record is the one step that can still fail on the record
    contract; a ``ValidationError`` there must be recorded as ``incomplete`` —
    no dimensions, no defects, the reason in the rationale — rather than
    propagating to a caller that has no exit code for it.
    """
    from deep_research.agents import report_reviewer as report_reviewer_module

    built = packet()
    completer = ScriptedCompleter(outputs=[_draft()])
    merge = report_reviewer_module._merge_review  # noqa: SLF001

    def refuse_to_record_a_judgement(*args: object, **kwargs: object) -> ReportReview:
        if kwargs.get("status") != "incomplete":
            return ReportReview.model_validate({"status": "scored"})
        return merge(*args, **kwargs)

    monkeypatch.setattr(
        report_reviewer_module, "_merge_review", refuse_to_record_a_judgement
    )

    review = await review_report(completer, built)

    assert review.status == "incomplete"
    assert review.dimensions == {}
    assert review.defects == []
    assert "ValidationError" in review.rationale
    assert not semantic_review_passes(review)


# --- the reviewer itself: fingerprints, reuse, records ----------------------


@pytest.mark.asyncio
async def test_a_reviewer_records_its_own_call_fingerprint() -> None:
    completer = ScriptedCompleter(outputs=[_draft()])
    reviewer = ReportReviewer(provider=completer)

    review = await reviewer.review(packet())

    assert reviewer.name == REPORT_REVIEWER_ROLE
    assert reviewer.allowed_tools == ()
    assert review.status == "scored"
    assert set(reviewer.call_fingerprints) == {"ReportReviewDraft"}
    assert completer.calls[0][1] == REPORT_REVIEWER_ROLE
    assert completer.calls[0][0] == "ReportReviewDraft"


@pytest.mark.asyncio
async def test_a_reused_review_costs_no_provider_call() -> None:
    built = packet()
    stored = await review_report(ScriptedCompleter(outputs=[_draft()]), built)
    completer = ScriptedCompleter(outputs=[])
    reused = await ReportReviewer(provider=completer).review(built, previous=stored)

    assert reused is stored
    assert completer.calls == []


@pytest.mark.asyncio
async def test_a_stored_review_of_other_content_is_not_reused() -> None:
    built = packet()
    stored = _scored_review(input_fingerprint="a-different-packet")
    completer = ScriptedCompleter(outputs=[_draft()])

    review = await ReportReviewer(provider=completer).review(built, previous=stored)

    assert review is not stored
    assert review.status == "scored"
    assert review.input_fingerprint == built.fingerprint
    assert len(completer.calls) == 1


@pytest.mark.asyncio
async def test_an_incomplete_stored_review_is_never_reused() -> None:
    built = packet()
    stored = _scored_review().model_copy(update={"status": "incomplete"})
    completer = ScriptedCompleter(outputs=[_draft()])

    review = await ReportReviewer(provider=completer).review(built, previous=stored)

    assert review is not stored
    assert review.status == "scored"


@pytest.mark.asyncio
async def test_a_reused_review_records_no_retry() -> None:
    """Records describe the review just made, never the one it reused."""
    built = packet()
    completer = ScriptedCompleter(outputs=[_output_limit_error(), _draft()])
    reviewer = ReportReviewer(
        provider=completer,
        config=AgentRuntimeConfig(report_review_max_tokens=4096),
    )

    first = await reviewer.review(built)
    second = await reviewer.review(built, previous=first)

    assert second is first
    assert len(completer.calls) == 2
    assert reviewer.review_records == ()


def test_a_fact_row_line_names_its_subject() -> None:
    """D11: the reviewer reads which thing each key fact is about, as the reader does."""
    row = _fact_row("finding-1")
    assert "| subject Model B |" in _fact_row_line(row.model_copy(update={"subject": "Model B"}))
    assert "| subject not stated |" in _fact_row_line(row)


def test_the_prompt_never_claims_a_sentence_without_a_label_states_no_figure() -> None:
    """Final review I-1: the premise the reviewer reads must be true of every sentence.

    The label builder labels only the units ``figures.quantities_in`` parses
    (power, energy, percent), so a sentence stating a price, a count or a rating
    ends with no label while stating a figure — the row is in the Key facts
    table with no label beside the sentence. The prompt used to read "A sentence
    that ends with no label states no figure", which told the model to read such
    a sentence as figure-free and skip the provenance check the label exists for.
    The prompt states the premise truly instead, and sends the sentence to the
    cited findings' own figure labels, which the packet prints for every unit.
    """
    assert "A sentence that ends with no label states no figure:" not in REPORT_REVIEW_SYSTEM_PROMPT
    assert "a unit this report does not label" in REPORT_REVIEW_SYSTEM_PROMPT
    assert "or one no cited finding carries" in REPORT_REVIEW_SYSTEM_PROMPT
    assert "against their figure labels" in REPORT_REVIEW_SYSTEM_PROMPT


# --- spec §11.1: the renamed packet sections, the table, finding status -----


def test_the_prompt_version_is_report_review_5() -> None:
    assert REPORT_REVIEW_PROMPT_VERSION == "report-review-5"


def test_the_prompt_no_longer_says_the_code_built_label_it_ends_with() -> None:
    assert "the code-built label it ends with" not in REPORT_REVIEW_SYSTEM_PROMPT


def test_the_packet_renames_key_facts_to_verified_figures() -> None:
    rendered = _render(packet())
    assert "# Verified figures" in rendered
    assert "# Key facts" not in rendered


def test_the_packet_renames_not_found_to_what_the_report_could_not_confirm() -> None:
    rendered = _render(packet())
    assert "# What the report could not confirm" in rendered
    assert "# Not found" not in rendered
    assert "must list these and must not present them as answered" in rendered


def _table_composition() -> ReportComposition:
    """A composition with a one-row findings table, for the packet's own
    ``# Table`` block (spec §11.1)."""
    table = ReportTable(
        shape="findings",
        columns=["What was measured", "Result", "Who reported it (and when)", "Source"],
        rows=[
            [
                TableCell(text="battery storage capacity added", statement_ids=["S001"], row_ids=["K001"]),
                TableCell(text="10.4 GW", statement_ids=["S001"], row_ids=["K001"]),
                TableCell(text=EIA, statement_ids=["S001"], row_ids=["K001"]),
                TableCell(
                    text="",
                    entries=[TableEntry(text="", source_url=EIA_URL)],
                    statement_ids=["S001"],
                    row_ids=["K001"],
                ),
            ]
        ],
        caption="No figure in this table is a forecast.",
    )
    return _written_composition().model_copy(update={"table": table})


def test_the_packet_carries_a_table_block_with_backing_ids() -> None:
    built = build_report_review_input(state_with_written_report(composition=_table_composition()))
    rendered = _render(built)
    assert "# Table" in rendered
    assert "[statements: S001]" in rendered
    assert "backing fact rows: K001" in rendered
    assert "10.4 GW" in rendered


def test_a_report_with_no_table_reads_as_none_qualified() -> None:
    built = packet()
    assert built.table_lines == []
    assert "(no table qualified for this report)" in _render(built)


def test_the_finding_block_carries_a_status_line() -> None:
    built = packet()
    assert built.findings[0].status == "verified"
    assert "status: verified" in _render(built)


def test_a_verified_corrected_finding_reads_verified_with_corrections() -> None:
    finding = _written_finding()
    corrected = finding.model_copy(
        update={
            "verification": finding.verification.model_copy(
                update={"status": "verified_corrected"}
            )
        }
    )
    composition = _written_composition().model_copy(update={"findings": [corrected]})
    built = build_report_review_input(state_with_written_report(composition=composition))
    assert built.findings[0].status == "verified with corrections"
    assert "status: verified with corrections" in _render(built)


def test_a_quoted_finding_reads_quoted_not_checked_for_context() -> None:
    finding = _written_finding()
    quoted = finding.model_copy(
        update={
            "verification": finding.verification.model_copy(
                update={"status": "quoted", "figure_results": []}
            )
        }
    )
    composition = _written_composition().model_copy(update={"findings": [quoted]})
    built = build_report_review_input(state_with_written_report(composition=composition))
    assert built.findings[0].status == "quoted, not checked for context"


def test_the_composition_fingerprint_moves_with_the_table() -> None:
    before = composition_semantic_fingerprint(_written_composition())
    after = composition_semantic_fingerprint(_table_composition())
    assert before != after


def test_the_composition_fingerprint_moves_with_parts() -> None:
    before = composition_semantic_fingerprint(_written_composition())
    with_parts = _written_composition().model_copy(
        update={
            "parts": [
                ReportPart(
                    coverage_id="topic-01",
                    sub_topic_title="Battery storage additions",
                    finding_ids=["F01"],
                    status="written",
                )
            ]
        }
    )
    assert before != composition_semantic_fingerprint(with_parts)


def test_the_composition_fingerprint_moves_with_unreachable_pages() -> None:
    before = composition_semantic_fingerprint(_written_composition())
    with_unreachable = _written_composition().model_copy(
        update={"unreachable": [UnreachablePage(url="https://example.com/denied", title="Denied")]}
    )
    assert before != composition_semantic_fingerprint(with_unreachable)


# --- T5 addendum: scoped re-review after a redraft --------------------------


PART_A = "topic-01"
PART_B = "topic-02"


def _redraft_fixture(
    *, extra_defect: bool = False
) -> tuple[ReportComposition, ReportComposition, ReportReview]:
    """The composition a first full review judged, the redrafted composition
    that followed it, and that first review -- built by hand so Part A's
    statement id can drift between the two (as real renumbering would) while
    its words stay identical, and Part B's text and id both change.
    """
    finding = _written_finding()
    finding_id = finding_fingerprint(finding)

    def statement(statement_id: str, text: str) -> ReportStatement:
        return ReportStatement(
            statement_id=statement_id, text=text, finding_ids=[finding_id], target_ids=[TARGET_ID]
        )

    def point(stmt: ReportStatement) -> ReportPoint:
        return ReportPoint(text=stmt.text, source_urls=[EIA_URL], statement=stmt)

    old_bottom = statement("S001", "Old bottom line, before the redraft.")
    old_a = statement("S002", "Part A's point, which the redraft will not touch.")
    old_b = statement("S003", "Part B's point, which the redraft will replace.")
    old = ReportComposition(
        question=QUESTION, session_id=SESSION_ID, iteration=0, max_extra_passes=1,
        as_of="2026-08-01", scope="United States",
        findings=[finding], fact_rows=[_fact_row(finding_id)],
        finding_labels={"F01": finding_id},
        summary=[point(old_bottom)],
        sections=[
            ReportSection(title="Part A", coverage_id=PART_A, points=[point(old_a)]),
            ReportSection(title="Part B", coverage_id=PART_B, points=[point(old_b)]),
        ],
    )
    new_bottom = statement("S010", "New bottom line, after the redraft.")
    new_a = statement("S011", "Part A's point, which the redraft will not touch.")
    new_b = statement("S012", "Part B's point, now backed by the finding.")
    new = ReportComposition(
        question=QUESTION, session_id=SESSION_ID, iteration=0, max_extra_passes=1,
        as_of="2026-08-01", scope="United States",
        findings=[finding], fact_rows=[_fact_row(finding_id)],
        finding_labels={"F01": finding_id},
        summary=[point(new_bottom)],
        sections=[
            ReportSection(title="Part A", coverage_id=PART_A, points=[point(new_a)]),
            ReportSection(title="Part B", coverage_id=PART_B, points=[point(new_b)]),
        ],
        parts=[
            ReportPart(coverage_id=PART_A, sub_topic_title="Part A", finding_ids=[finding_id], status="carried_over"),
            ReportPart(coverage_id=PART_B, sub_topic_title="Part B", finding_ids=[finding_id], status="written"),
        ],
    )
    defects = [
        ReviewDefect(
            defect_id="review-01", kind="missing_support", severity="major",
            statement_ids=["S003"], target_ids=[TARGET_ID],
            problem="Part B's claim isn't backed by a cited finding.",
        )
    ]
    if extra_defect:
        defects.append(
            ReviewDefect(
                defect_id="review-02", kind="presentation", severity="minor",
                statement_ids=["S002"], target_ids=[],
                problem="Part A repeats a qualifier the bottom line already gave.",
            )
        )
    previous_review = ReportReview(
        status="scored",
        dimensions=_scores(0.85),
        defects=defects,
        per_statement_dispositions={
            "S001": "supported", "S002": "supported", "S003": "unsupported",
        },
        reviewed_statement_ids=["S001", "S002", "S003"],
        input_fingerprint="old-packet-fp",
        composition_fingerprint=composition_semantic_fingerprint(old),
        rubric_version=REVIEW_RUBRIC_VERSION,
        rationale="The first full review.",
    )
    return old, new, previous_review


def _redraft_state(new: ReportComposition) -> ResearchState:
    return state_with_written_report(composition=new, report=render_written_report(new))


def test_a_scoped_packet_marks_changed_and_unchanged_and_carries_previous_defects() -> None:
    old, new, previous_review = _redraft_fixture()
    remapped = remap_review_for_redraft(
        previous_review, previous_composition=old, composition=new
    )
    assert remapped is not None
    scoped = build_scoped_report_review_input(_redraft_state(new), previous_review=remapped)
    assert scoped is not None
    assert set(scoped.changed_statement_ids) == {"S010", "S012"}
    assert scoped.unchanged_statement_ids == ["S011"]
    assert [defect.defect_id for defect in scoped.previous_defects] == ["review-01"]
    assert scoped.previous_defects[0].problem == "Part B's claim isn't backed by a cited finding."
    rendered = "\n\n".join(m.content for m in scoped_review_messages(scoped))
    assert "S010" in rendered and "S012" in rendered
    assert "S011" in rendered
    assert "review-01" in rendered


def test_an_unchanged_part_whose_text_differs_forces_a_full_review() -> None:
    """T5 addendum: a part changed without a redraft request is not scoped."""
    old, new, previous_review = _redraft_fixture()
    tampered_section = new.sections[0].model_copy(
        update={
            "points": [
                new.sections[0].points[0].model_copy(
                    update={"text": "Part A's point, secretly rewritten."}
                )
            ]
        }
    )
    tampered = new.model_copy(update={"sections": [tampered_section, new.sections[1]]})

    assert remap_review_for_redraft(
        previous_review, previous_composition=old, composition=tampered
    ) is None


def test_a_composition_with_nothing_carried_over_is_not_scoped() -> None:
    old, new, previous_review = _redraft_fixture()
    all_written = new.model_copy(
        update={
            "parts": [
                part.model_copy(update={"status": "written"}) for part in new.parts
            ]
        }
    )
    assert remap_review_for_redraft(
        previous_review, previous_composition=old, composition=all_written
    ) is None


@pytest.mark.asyncio
async def test_a_resolved_defect_is_recorded_as_resolved_and_an_unresolved_one_as_unresolved() -> None:
    old, new, previous_review = _redraft_fixture(extra_defect=True)
    remapped = remap_review_for_redraft(
        previous_review, previous_composition=old, composition=new
    )
    assert remapped is not None
    scoped = build_scoped_report_review_input(_redraft_state(new), previous_review=remapped)
    assert scoped is not None
    assert {d.defect_id for d in scoped.previous_defects} == {"review-01", "review-02"}

    reply = ScopedReportReviewDraft(
        dimensions=ReviewDimensionScores(**_scores()),
        statement_dispositions=[
            StatementDispositionDraft(statement_id="S010", disposition="supported"),
            StatementDispositionDraft(statement_id="S012", disposition="supported"),
        ],
        previous_defect_resolutions=[
            PreviousDefectResolutionDraft(defect_id="review-01", resolved=False),
            PreviousDefectResolutionDraft(defect_id="review-02", resolved=True, note="Fixed."),
        ],
        new_defects=[],
        rationale="Re-checked the report as it now stands.",
    )
    reviewer = ReportReviewer(provider=ScriptedCompleter(outputs=[reply]))

    review = await reviewer.review_scoped(scoped)

    by_id = {defect.defect_id: defect for defect in review.defects}
    assert by_id["review-01"].resolution == "unresolved"
    assert by_id["review-02"].resolution == "resolved"
    # A resolved defect is recorded, not silently dropped, but no longer
    # blocks acceptance whatever its original severity (T5 addendum item 4).
    assert not by_id["review-02"].material
    assert by_id["review-01"].material
    assert "Resolved by the redraft: review-02" in review.rationale
    assert "Still unresolved: review-01" in review.rationale


@pytest.mark.asyncio
async def test_a_disposition_returned_for_an_unchanged_id_does_not_overwrite_the_carried_one() -> (
    None
):
    """Fable prompt review: an unchanged id is "not being asked again" -- the
    scoped prompt's own promise -- so a reply that judges one anyway (a model
    that answered beyond its scope) must not move that id's carried
    disposition; the carried reading stands."""
    old, new, previous_review = _redraft_fixture()
    remapped = remap_review_for_redraft(
        previous_review, previous_composition=old, composition=new
    )
    assert remapped is not None
    scoped = build_scoped_report_review_input(_redraft_state(new), previous_review=remapped)
    assert scoped is not None
    assert scoped.carried_dispositions["S011"] == "supported"

    reply = ScopedReportReviewDraft(
        dimensions=ReviewDimensionScores(**_scores()),
        statement_dispositions=[
            StatementDispositionDraft(statement_id="S010", disposition="supported"),
            StatementDispositionDraft(statement_id="S012", disposition="supported"),
            StatementDispositionDraft(statement_id="S011", disposition="unsupported"),
        ],
        previous_defect_resolutions=[
            PreviousDefectResolutionDraft(defect_id="review-01", resolved=True, note="Fixed."),
        ],
        new_defects=[],
        rationale="Re-checked the report as it now stands.",
    )
    reviewer = ReportReviewer(provider=ScriptedCompleter(outputs=[reply]))

    review = await reviewer.review_scoped(scoped)

    assert review.per_statement_dispositions["S011"] == "supported"


@pytest.mark.asyncio
async def test_a_new_defect_in_a_changed_part_is_accepted() -> None:
    old, new, previous_review = _redraft_fixture()
    remapped = remap_review_for_redraft(
        previous_review, previous_composition=old, composition=new
    )
    assert remapped is not None
    scoped = build_scoped_report_review_input(_redraft_state(new), previous_review=remapped)
    assert scoped is not None

    reply = ScopedReportReviewDraft(
        dimensions=ReviewDimensionScores(**_scores()),
        statement_dispositions=[
            StatementDispositionDraft(statement_id="S010", disposition="supported"),
            StatementDispositionDraft(statement_id="S012", disposition="unsupported"),
        ],
        previous_defect_resolutions=[
            PreviousDefectResolutionDraft(defect_id="review-01", resolved=True),
        ],
        new_defects=[
            ReviewDefectDraft(
                kind="missing_support", severity="major", statement_ids=["S012"],
                target_ids=[], problem="The redrafted point still overstates the finding.",
            )
        ],
        rationale="Found a new problem in the redrafted part.",
    )
    reviewer = ReportReviewer(provider=ScriptedCompleter(outputs=[reply]))

    review = await reviewer.review_scoped(scoped)

    problems = [defect.problem for defect in review.defects]
    assert "The redrafted point still overstates the finding." in problems


@pytest.mark.asyncio
async def test_a_new_defect_claimed_on_an_unchanged_part_is_refused() -> None:
    old, new, previous_review = _redraft_fixture()
    remapped = remap_review_for_redraft(
        previous_review, previous_composition=old, composition=new
    )
    assert remapped is not None
    scoped = build_scoped_report_review_input(_redraft_state(new), previous_review=remapped)
    assert scoped is not None

    reply = ScopedReportReviewDraft(
        dimensions=ReviewDimensionScores(**_scores()),
        statement_dispositions=[
            StatementDispositionDraft(statement_id="S010", disposition="supported"),
            StatementDispositionDraft(statement_id="S012", disposition="supported"),
        ],
        previous_defect_resolutions=[
            PreviousDefectResolutionDraft(defect_id="review-01", resolved=True),
        ],
        new_defects=[
            ReviewDefectDraft(
                kind="presentation", severity="minor", statement_ids=["S011"],
                target_ids=[], problem="Part A alone reads a little repetitive.",
            )
        ],
        rationale="Nothing else changed.",
    )
    reviewer = ReportReviewer(provider=ScriptedCompleter(outputs=[reply]))

    review = await reviewer.review_scoped(scoped)

    problems = [defect.problem for defect in review.defects]
    assert "Part A alone reads a little repetitive." not in problems
    assert "may not raise a new defect naming only unchanged statements" in review.rationale


@pytest.mark.asyncio
async def test_a_new_defect_citing_a_changed_and_unchanged_contradiction_is_accepted() -> None:
    """T5 addendum: naming a changed id together with the unchanged one it
    contradicts still names a changed id, so this one rule covers both."""
    old, new, previous_review = _redraft_fixture()
    remapped = remap_review_for_redraft(
        previous_review, previous_composition=old, composition=new
    )
    assert remapped is not None
    scoped = build_scoped_report_review_input(_redraft_state(new), previous_review=remapped)
    assert scoped is not None

    reply = ScopedReportReviewDraft(
        dimensions=ReviewDimensionScores(**_scores()),
        statement_dispositions=[
            StatementDispositionDraft(statement_id="S010", disposition="supported"),
            StatementDispositionDraft(statement_id="S012", disposition="unsupported"),
        ],
        previous_defect_resolutions=[
            PreviousDefectResolutionDraft(defect_id="review-01", resolved=True),
        ],
        new_defects=[
            ReviewDefectDraft(
                kind="contradiction", severity="major", statement_ids=["S012", "S011"],
                target_ids=[], problem="The redrafted Part B now contradicts Part A.",
            )
        ],
        rationale="Found a cross-part contradiction.",
    )
    reviewer = ReportReviewer(provider=ScriptedCompleter(outputs=[reply]))

    review = await reviewer.review_scoped(scoped)

    problems = [defect.problem for defect in review.defects]
    assert "The redrafted Part B now contradicts Part A." in problems


@pytest.mark.asyncio
async def test_the_merged_scoped_review_feeds_the_acceptance_gate_like_a_full_review() -> None:
    old, new, previous_review = _redraft_fixture()
    remapped = remap_review_for_redraft(
        previous_review, previous_composition=old, composition=new
    )
    assert remapped is not None
    scoped = build_scoped_report_review_input(_redraft_state(new), previous_review=remapped)
    assert scoped is not None

    reply = ScopedReportReviewDraft(
        dimensions=ReviewDimensionScores(**_scores()),
        statement_dispositions=[
            StatementDispositionDraft(statement_id="S010", disposition="supported"),
            StatementDispositionDraft(statement_id="S012", disposition="supported"),
        ],
        previous_defect_resolutions=[
            PreviousDefectResolutionDraft(defect_id="review-01", resolved=True),
        ],
        new_defects=[],
        rationale="Clean redraft.",
    )
    reviewer = ReportReviewer(provider=ScriptedCompleter(outputs=[reply]))

    review = await reviewer.review_scoped(scoped)

    assert review.status == "scored"
    assert semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_scoped_review_call_fingerprints_under_its_own_schema_name() -> None:
    old, new, previous_review = _redraft_fixture()
    remapped = remap_review_for_redraft(
        previous_review, previous_composition=old, composition=new
    )
    assert remapped is not None
    scoped = build_scoped_report_review_input(_redraft_state(new), previous_review=remapped)
    assert scoped is not None
    reply = ScopedReportReviewDraft(
        dimensions=ReviewDimensionScores(**_scores()),
        statement_dispositions=[
            StatementDispositionDraft(statement_id="S010", disposition="supported"),
            StatementDispositionDraft(statement_id="S012", disposition="supported"),
        ],
        previous_defect_resolutions=[
            PreviousDefectResolutionDraft(defect_id="review-01", resolved=True),
        ],
        new_defects=[],
        rationale="Clean redraft.",
    )
    reviewer = ReportReviewer(provider=ScriptedCompleter(outputs=[reply]))

    await reviewer.review_scoped(scoped)

    assert "ScopedReportReviewDraft" in reviewer.call_fingerprints
    full_fingerprint = reviewer.fingerprint_call("ReportReviewDraft")
    assert reviewer.call_fingerprints["ScopedReportReviewDraft"] != full_fingerprint


def test_the_scoped_packet_fingerprint_differs_from_the_full_packets() -> None:
    old, new, previous_review = _redraft_fixture()
    remapped = remap_review_for_redraft(
        previous_review, previous_composition=old, composition=new
    )
    assert remapped is not None
    scoped = build_scoped_report_review_input(_redraft_state(new), previous_review=remapped)
    assert scoped is not None
    assert scoped.fingerprint == scoped_report_review_input_fingerprint(scoped)
    assert scoped.fingerprint != scoped.base.fingerprint


def test_the_scoped_prompt_asks_for_each_previous_defects_own_resolution() -> None:
    assert "say whether the redraft resolved it" in SCOPED_REPORT_REVIEW_SYSTEM_PROMPT
    assert "previous_defect_resolutions" in SCOPED_REPORT_REVIEW_INSTRUCTION


# --- reviewer round (RevFormatT5): P0/P1 fixes -------------------------------


@pytest.mark.asyncio
async def test_a_target_only_coverage_defect_in_a_scoped_reply_stays_major() -> None:
    """P0 repro: a new defect naming no statement at all (a report-level or
    target-scoped coverage gap) must not be dropped, and D11's floor must
    still force it material in the scoped path exactly as a full review
    would -- a findings-table mis-credit works the same way, since its own
    defect carries only fact-row ids and no statement id either.
    """
    old, new, previous_review = _redraft_fixture()
    remapped = remap_review_for_redraft(
        previous_review, previous_composition=old, composition=new
    )
    assert remapped is not None
    scoped = build_scoped_report_review_input(_redraft_state(new), previous_review=remapped)
    assert scoped is not None
    assert TARGET_ID in scoped.base.required_target_ids

    reply = ScopedReportReviewDraft(
        dimensions=ReviewDimensionScores(**_scores()),
        statement_dispositions=[
            StatementDispositionDraft(statement_id="S010", disposition="supported"),
            StatementDispositionDraft(statement_id="S012", disposition="supported"),
        ],
        previous_defect_resolutions=[
            PreviousDefectResolutionDraft(defect_id="review-01", resolved=True),
        ],
        new_defects=[
            ReviewDefectDraft(
                kind="coverage", severity="minor", statement_ids=[], target_ids=[TARGET_ID],
                problem="The redraft still leaves this required target unanswered.",
            )
        ],
        rationale="Coverage is still missing.",
    )
    reviewer = ReportReviewer(provider=ScriptedCompleter(outputs=[reply]))

    review = await reviewer.review_scoped(scoped)

    coverage_defects = [d for d in review.defects if d.kind == "coverage"]
    assert coverage_defects, "the target-only coverage defect must not be dropped"
    assert coverage_defects[0].severity == "major"
    assert not semantic_review_passes(review)


@pytest.mark.asyncio
async def test_remap_drops_a_defect_whose_old_id_now_names_a_different_carried_statement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1 repro, with the real writer's own renumbering: a redrafted part
    shrinks by one point, which shifts a carried-over part's statement ids.
    A previous defect against the redrafted part's own (now gone) statement
    must never be carried onto the carried-over part's shifted statement
    just because the old id string happens to collide with the new one.
    """
    from deep_research.agents.report_writer import ReportWriterTask, compose_written_report
    from deep_research.utils.types import BottomLineDraft, SectionDraft, SubTopic
    from deep_research.utils.types import WriterPointDraft as _WriterPointDraft

    class _Verdict:
        def __init__(self, label: str) -> None:
            self.label = label
            self.verdict = "consistent"
            self.corrected_text = ""
            self.reason = "the finding states it"

    async def consistent(
        provider, items, *, question, fingerprint=None,
        batch_size=None, concurrency=None, gate=None,
    ):
        del provider, question, fingerprint, batch_size, concurrency, gate
        return {item.label: _Verdict(item.label) for item in items}, []

    monkeypatch.setattr(
        "deep_research.agents.evidence_verifier.check_statements", consistent
    )

    target_a = make_target("topic-01-target-01")
    target_b = make_target("topic-02-target-01")
    topic_a = SubTopic(
        coverage_id="topic-01", title="Part A", rationale="It matters.",
        search_queries=["a"], success_criteria=["a"], priority=1, evidence_targets=[target_a],
    )
    topic_b = SubTopic(
        coverage_id="topic-02", title="Part B", rationale="It matters.",
        search_queries=["b"], success_criteria=["b"], priority=2, evidence_targets=[target_b],
    )
    read_a = make_read(url="https://a.test/1", title="Page A")
    finding_a = make_finding(
        read_a, "Part A's finding.", figures=[figure("10.4", "GW", "2024", "actual")],
        target_ids=["topic-01-target-01"],
    )
    finding_a = finding_a.model_copy(update={
        "verification": FindingVerification(
            status="verified",
            figure_results=[FigureResult(
                figure=finding_a.figures[0], matched=True, evidence_words="10.4 GW",
                context=FigureContext(period="2024", scope=None, attribution="own",
                                       organisation="Org A", kind="actual"),
            )],
        )
    })
    read_b = make_read(url="https://a.test/2", title="Page B")
    finding_b = make_finding(
        read_b, "Part B's finding.", figures=[figure("5.0", "GW", "2025", "actual")],
        target_ids=["topic-02-target-01"],
    )
    finding_b = finding_b.model_copy(update={
        "verification": FindingVerification(
            status="verified",
            figure_results=[FigureResult(
                figure=finding_b.figures[0], matched=True, evidence_words="5.0 GW",
                context=FigureContext(period="2025", scope=None, attribution="own",
                                       organisation="Org B", kind="actual"),
            )],
        )
    })

    def _task(*, defects, previous):
        return ReportWriterTask(
            session_id=SESSION_ID, instruction=QUESTION, question=QUESTION,
            iteration=0, max_extra_passes=1, as_of="2026-08-01", scope="United States",
            generated_on="2026-08-01",
            sub_topics=[topic_a, topic_b], targets=[target_a, target_b],
            findings=[finding_a, finding_b], sources=[],
            registry=[("F01", finding_a), ("F02", finding_b)],
            facts=[], not_found=[],
            answered={
                "topic-01-target-01": [finding_fingerprint(finding_a)],
                "topic-02-target-01": [finding_fingerprint(finding_b)],
            },
            defects=defects, previous=previous,
        )

    first_completer = ScriptedCompleter(
        outputs=[
            SectionDraft(
                title="Part A",
                points=[
                    _WriterPointDraft(text="Part A's first point.", finding_labels=["F01"]),
                    _WriterPointDraft(text="Part A's second point, which is wrong.", finding_labels=["F01"]),
                ],
            ),
            SectionDraft(
                title="Part B",
                points=[_WriterPointDraft(text="Part B's point.", finding_labels=["F02"])],
            ),
            BottomLineDraft(
                sentences=[_WriterPointDraft(text="Bottom line before the redraft.", finding_labels=["F01"])]
            ),
        ]
    )
    old = await compose_written_report(
        _task(defects=[], previous=None), provider=first_completer, section_concurrency=1
    )

    wrong_statement_id = old.sections[0].points[1].statement.statement_id
    old_first_id = old.sections[0].points[0].statement.statement_id
    old_b_id = old.sections[1].points[0].statement.statement_id
    defect = ReviewDefect(
        defect_id="review-01", kind="missing_support", severity="major",
        statement_ids=[wrong_statement_id], target_ids=["topic-01-target-01"],
        problem="Part A's second point is not backed by the finding.",
    )

    second_completer = ScriptedCompleter(
        outputs=[
            SectionDraft(
                title="Part A",
                points=[_WriterPointDraft(text="Part A's corrected point.", finding_labels=["F01"])],
            ),
            BottomLineDraft(
                sentences=[_WriterPointDraft(text="Bottom line after the redraft.", finding_labels=["F01"])]
            ),
        ]
    )
    new = await compose_written_report(
        _task(defects=[defect], previous=old), provider=second_completer, section_concurrency=1
    )

    statuses = {part.coverage_id: part.status for part in new.parts}
    assert statuses["topic-01"] == "written"
    assert statuses["topic-02"] == "carried_over"
    carried_section = next(section for section in new.sections if section.coverage_id == "topic-02")
    carried_id = carried_section.points[0].statement.statement_id
    # The renumbering shifted part B's carried statement onto the defect's
    # stale old id -- the exact collision the fix must not fall for.
    assert carried_id == wrong_statement_id

    previous_review = ReportReview(
        status="scored", dimensions=_scores(0.85), defects=[defect],
        per_statement_dispositions={
            old_first_id: "supported",
            wrong_statement_id: "unsupported",
            old_b_id: "supported",
        },
        reviewed_statement_ids=[old_first_id, wrong_statement_id, old_b_id],
        input_fingerprint="old-fp", composition_fingerprint=composition_semantic_fingerprint(old),
        rubric_version=REVIEW_RUBRIC_VERSION, rationale="The first full review.",
    )

    remapped = remap_review_for_redraft(previous_review, previous_composition=old, composition=new)

    assert remapped is not None
    assert set(remapped.per_statement_dispositions) == {carried_id}
    remapped_defect = next(d for d in remapped.defects if d.defect_id == "review-01")
    assert remapped_defect.statement_ids == []
    assert remapped_defect.coverage_ids == ["topic-01"]


def test_the_table_block_shows_real_option_cell_text_and_publishers() -> None:
    """P1 repro: the packet's own '# Table' block must show the reader's own
    cell content -- the writer's marks over checked statements -- not an em
    dash for every options-table cell, and must pin each cell's own backing
    statement ids so a mis-credit can be found.
    """
    from deep_research.agents.report_table import options_table
    from tests.test_agents.test_report_table import _fable_composition

    composition = _fable_composition()
    table = options_table(composition)
    composition = composition.model_copy(update={"table": table})

    lines = _table_lines(composition)
    rendered = "\n".join(lines)

    assert "the best wireless headphones" in rendered
    assert "sound score of 4.8 out of 5" in rendered
    assert "SoundGuys" in rendered
    assert "[statements:" in rendered
    xm6_line = lines[0]
    # The cells the fixture actually marks must not read as empty, even
    # though "—" also appears as the credit separator inside populated ones.
    assert "Sound quality: —" not in xm6_line
    assert "Microphone: —" not in xm6_line


def test_the_scoped_developer_message_carries_the_full_reviews_judging_rules() -> None:
    """P1: changed statements must be judged by exactly the full review's
    standard, so the scoped developer message must carry its rules."""
    assert "against their figure labels" in SCOPED_REPORT_REVIEW_SYSTEM_PROMPT
    assert "according to X" in SCOPED_REPORT_REVIEW_SYSTEM_PROMPT
    assert (
        "a fact that is not in the findings you were shown is not "
        "established by anything you know" in SCOPED_REPORT_REVIEW_SYSTEM_PROMPT.casefold()
    )


def test_the_scoped_messages_reuse_the_full_reviews_section_intros() -> None:
    old, new, previous_review = _redraft_fixture()
    remapped = remap_review_for_redraft(
        previous_review, previous_composition=old, composition=new
    )
    assert remapped is not None
    scoped = build_scoped_report_review_input(_redraft_state(new), previous_review=remapped)
    assert scoped is not None
    rendered = "\n\n".join(m.content for m in scoped_review_messages(scoped))

    assert "must list these and must not present them as answered" in rendered
    assert (
        "The report's own verified figures, each with the label a sentence "
        "stating it would carry" in rendered
    )
    assert (
        "The question-shaped table code assembled from the statements' "
        "option marks" in rendered
    )
