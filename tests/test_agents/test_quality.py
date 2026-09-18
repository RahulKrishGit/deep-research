"""Tests for deterministic report quality metrics and hard gates."""

from __future__ import annotations

from deep_research.agents import (
    ReportQualitySnapshot as AgentReportQualitySnapshot,
)
from deep_research.agents.identity import claim_fingerprint, finding_fingerprint
from deep_research.agents.quality import compute_report_quality
from deep_research.agents.report import ReportComposition, ReportPoint, ReportSection
from deep_research.utils import ReportQualitySnapshot as UtilsReportQualitySnapshot
from deep_research.utils.types import (
    Claim,
    Finding,
    ResearchState,
    ScoredSource,
    SubTopic,
)

EXTRACTED_AT = "2026-08-01T12:00:00+00:00"


def _topic(index: int) -> SubTopic:
    return SubTopic(
        coverage_id=f"topic-{index:02d}",
        title=f"Topic {index}",
        rationale="This topic matters to the answer.",
        search_queries=[f"topic {index} evidence"],
        success_criteria=["A checked claim answers the topic."],
        priority=index,
    )


def _source(url: str, *, status: str = "scored") -> ScoredSource:
    if status != "scored":
        return ScoredSource(
            url=url,
            title=url,
            rationale="The source was not scored in this pass.",
            evaluation_status=status,
        )
    return ScoredSource(
        url=url,
        title=url,
        authority_score=0.8,
        recency_score=0.8,
        relevance_score=0.8,
        overall_score=0.8,
        rationale="The source was scored.",
    )


def _finding(content: str, *, topic: str = "Topic 1") -> Finding:
    return Finding(
        content=content,
        source_url="https://example.test/a",
        source_title="Example source",
        extracted_at=EXTRACTED_AT,
        confidence=0.8,
        related_sub_topic=topic,
    )


def _claim(
    text: str,
    *,
    url: str = "https://example.test/a",
    verdict: str = "verified",
    coverage_id: str = "topic-01",
    finding: Finding | None = None,
) -> Claim:
    return Claim(
        claim_id=claim_fingerprint(text),
        text=text,
        source_urls=[url],
        verdict=verdict,
        evidence_status=(
            "verified_pair"
            if verdict == "verified"
            else "source_supported"
            if verdict == "insufficient_evidence"
            else None
        ),
        confidence=0.8,
        evidence=["An independent source supports this claim."],
        contradictions=[],
        verification_evidence=[],
        consumed_finding_fingerprints=(
            [finding_fingerprint(finding)] if finding is not None else []
        ),
        consumed_coverage_ids=[coverage_id],
    )


def _point(text: str, claim: Claim, *, urls: list[str] | None = None) -> ReportPoint:
    return ReportPoint(
        text=text,
        claim_ids=[claim.claim_id],
        source_urls=urls if urls is not None else list(claim.source_urls),
    )


def _state_and_composition() -> tuple[ResearchState, ReportComposition]:
    topics = [_topic(index) for index in range(1, 6)]
    first_finding = _finding("The first result was measured.")
    second_finding = _finding("The second result was measured.")
    first_claim = _claim(
        "The first result was measured.",
        finding=first_finding,
    )
    second_claim = _claim(
        "The second result was contradicted.",
        url="https://example.test/b",
        verdict="contradicted",
        coverage_id="topic-02",
        finding=second_finding,
    )
    state = ResearchState(
        session_id="session-1",
        original_question="What happened?",
        sub_topics=topics,
        raw_findings=[first_finding, first_finding, second_finding],
        evaluated_sources=[
            _source("https://example.test/a"),
            _source("https://example.test/a"),
            _source("https://example.test/b", status="unscored_cap"),
        ],
        verified_claims=[first_claim, first_claim, second_claim],
        report="# Reader report",
        report_evidence="# Evidence ledger",
    )
    composition = ReportComposition(
        question=state.original_question,
        session_id=state.session_id,
        as_of=EXTRACTED_AT,
        scope="Topics 1 through 5.",
        sub_topics=topics,
        claims=[first_claim, second_claim],
        sources=[
            _source("https://example.test/a"),
            _source("https://example.test/b", status="unscored_cap"),
        ],
        findings=[first_finding, second_finding],
        summary=[_point("The first result was measured.", first_claim)],
        sections=[
            ReportSection(
                title="Evidence",
                points=[_point("The second result was contradicted.", second_claim)],
            )
        ],
        uncertainty_notes=["topic-05 remains unresolved."],
    )
    return state, composition


def _complete_state_and_composition(
    covered: int,
) -> tuple[ResearchState, ReportComposition]:
    topics = [_topic(index) for index in range(1, 6)]
    source = _source("https://example.test/a")
    findings: list[Finding] = []
    claims: list[Claim] = []
    points: list[ReportPoint] = []
    for index in range(1, covered + 1):
        finding = _finding(
            f"Topic {index} was settled.", topic=f"Topic {index}"
        )
        claim = _claim(
            finding.content,
            coverage_id=f"topic-{index:02d}",
            finding=finding,
        )
        findings.append(finding)
        claims.append(claim)
        points.append(_point(finding.content, claim))
    state = ResearchState(
        session_id="session-complete",
        original_question="What happened?",
        sub_topics=topics,
        raw_findings=findings,
        evaluated_sources=[source],
        verified_claims=claims,
        report="# Reader report",
        report_evidence="# Evidence ledger",
    )
    composition = ReportComposition(
        question=state.original_question,
        session_id=state.session_id,
        as_of=EXTRACTED_AT,
        scope="Topics 1 through 5.",
        sub_topics=topics,
        claims=claims,
        sources=[source],
        findings=findings,
        summary=points,
    )
    return state, composition


def test_quality_snapshot_counts_unique_records_and_claimed_points() -> None:
    state, composition = _state_and_composition()

    snapshot = compute_report_quality(state, composition)

    assert snapshot.planned_topics == 5
    assert snapshot.covered_topics == 2
    assert snapshot.coverage_ratio == 0.4
    assert snapshot.unresolved_topic_ids == ["topic-05"]
    assert snapshot.unique_findings == 2
    assert snapshot.unique_sources == 2
    assert snapshot.cited_sources == 2
    assert snapshot.scored_cited_source_ratio == 0.5
    assert snapshot.verified_claims == 1
    assert snapshot.contradicted_claims == 1
    assert snapshot.duplicate_claims == 1
    assert snapshot.duplicate_source_rows == 1
    assert snapshot.uncited_settled_points == 0
    assert snapshot.hard_failures == [
        "duplicate_claims",
        "duplicate_source_rows",
        "unscored_cited_sources",
        "contradicted_settled_claims",
        "broad_plan_coverage_below_0.80",
    ]


def test_quality_snapshot_flags_missing_reader_and_evidence_artifacts() -> None:
    state, composition = _state_and_composition()
    state = state.model_copy(update={"report": None, "report_evidence": None})

    snapshot = compute_report_quality(state, composition)

    assert "missing_reader_report" in snapshot.hard_failures
    assert "missing_evidence_ledger" in snapshot.hard_failures


def test_report_quality_snapshot_is_exported_from_typed_layers() -> None:
    assert AgentReportQualitySnapshot is UtilsReportQualitySnapshot


def test_broad_plan_coverage_threshold_is_exactly_eighty_percent() -> None:
    complete_state, complete_composition = _complete_state_and_composition(4)
    complete = compute_report_quality(complete_state, complete_composition)

    partial_state, partial_composition = _complete_state_and_composition(3)
    partial = compute_report_quality(partial_state, partial_composition)

    assert complete.coverage_ratio == 0.8
    assert "broad_plan_coverage_below_0.80" not in complete.hard_failures
    assert partial.coverage_ratio == 0.6
    assert "broad_plan_coverage_below_0.80" in partial.hard_failures


def test_quality_snapshot_flags_unresolved_markers_and_uncited_points() -> None:
    state, composition = _complete_state_and_composition(5)
    claim = composition.claims[0]
    composition = composition.model_copy(
        update={
            "summary": [
                ReportPoint(
                    text="A settled point without a citation.",
                    claim_ids=[claim.claim_id],
                    source_urls=[],
                ),
                ReportPoint(
                    text="A point with an unresolved citation marker.",
                    claim_ids=[claim.claim_id],
                    source_urls=["[?]"],
                ),
            ]
        }
    )

    snapshot = compute_report_quality(state, composition)

    assert snapshot.uncited_settled_points == 1
    assert "unresolved_citations" in snapshot.hard_failures
    assert "uncited_settled_points" in snapshot.hard_failures


def test_quality_snapshot_requires_scope_and_as_of_declarations() -> None:
    state, composition = _complete_state_and_composition(5)
    composition = composition.model_copy(update={"scope": " ", "as_of": ""})

    snapshot = compute_report_quality(state, composition)

    assert "missing_scope" in snapshot.hard_failures
    assert "missing_as_of" in snapshot.hard_failures
