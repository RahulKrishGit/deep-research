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
    AcquisitionState,
    Claim,
    EvidenceDisposition,
    EvidenceTarget,
    EvidenceUnit,
    Finding,
    ReportStatement,
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


def _targeted_topic(index: int) -> SubTopic:
    """One planned topic with the required obligation it actually owes.

    Task 10 reads broad coverage from answered obligations rather than from
    claims that recorded consuming a topic id, so a fixture that wants to model
    "this topic is covered" has to say what the topic required.
    """
    return SubTopic(
        coverage_id=f"topic-{index:02d}",
        title=f"Topic {index}",
        rationale="This topic matters to the answer.",
        search_queries=[f"topic {index} evidence"],
        success_criteria=["A checked claim answers the topic."],
        priority=index,
        evidence_targets=[
            EvidenceTarget(
                target_id=f"t{index}",
                coverage_id=f"topic-{index:02d}",
                question=f"What does topic {index} require?",
                required_dimensions=["finding"],
                required=True,
                critical=False,
                support_policy="independent_pair",
            )
        ],
    )


def _complete_state_and_composition(
    covered: int,
) -> tuple[ResearchState, ReportComposition]:
    topics = [_targeted_topic(index) for index in range(1, 6)]
    source = _source("https://example.test/a")
    findings: list[Finding] = []
    claims: list[Claim] = []
    units: dict[str, EvidenceUnit] = {}
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
        evidence_id = f"e{index}"
        findings.append(finding)
        claims.append(claim)
        units[evidence_id] = EvidenceUnit(
            evidence_id=evidence_id,
            read_id=f"read-{index}",
            source_url="https://example.test/a",
            source_title="Example source",
            locator="chunk-0",
            excerpt=finding.content,
            target_ids=[f"t{index}"],
            origin="researcher",
        )
        points.append(
            ReportPoint(
                text=finding.content,
                claim_ids=[claim.claim_id],
                source_urls=["https://example.test/a"],
                statement=ReportStatement(
                    statement_id=f"S{index:03d}",
                    text=finding.content,
                    claim_cluster_ids=[claim.claim_id],
                    evidence_ids=[evidence_id],
                    target_ids=[f"t{index}"],
                    answered_dimensions=["finding"],
                ),
            )
        )
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
        evidence_units=units,
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


def test_the_broad_plan_gate_reads_substantive_coverage_not_the_claimed_ratio() -> None:
    """Task 10: 80% of *answered* topics, over the plan's own denominator.

    Four of five topics answered is 0.80 on both readings here, so the gate
    passes; one more topic left unanswered drops it below and fails. The
    denominator stays the plan's five topics in both cases — nothing shrinks it.
    """
    four_state, four_composition = _complete_state_and_composition(4)
    four = compute_report_quality(four_state, four_composition)
    assert four.substantive_topic_ratio == 0.8
    assert four.planned_topics == 5
    assert four.required_targets == 5
    assert four.answered_targets == 4
    assert "broad_plan_coverage_below_0.80" not in four.hard_failures
    # The one unanswered obligation has no recorded reason, which is a separate
    # failure: Section 2.3 requires every remaining target to be accounted for.
    assert four.unaccounted_target_ids == ["t5"]
    assert "unaccounted_required_targets" in four.hard_failures

    three_state, three_composition = _complete_state_and_composition(3)
    three = compute_report_quality(three_state, three_composition)
    assert three.substantive_topic_ratio == 0.6
    assert three.planned_topics == 5
    assert "broad_plan_coverage_below_0.80" in three.hard_failures


# --- Bug 2 regressions: a deferred disposition is not a terminal judgement
# while the same target's work still sits queued. ---------------------------


def _deferred_state_and_composition() -> tuple[ResearchState, ReportComposition]:
    """Two targets: t1 answered, t2 unanswered and open to a deferral test."""
    t1 = EvidenceTarget(
        target_id="t1",
        coverage_id="topic-01",
        question="What does topic 1 require?",
        required_dimensions=["finding"],
        required=True,
        critical=False,
        support_policy="independent_pair",
    )
    t2 = EvidenceTarget(
        target_id="t2",
        coverage_id="topic-02",
        question="What does topic 2 require?",
        required_dimensions=["finding"],
        required=True,
        critical=False,
        support_policy="independent_pair",
    )
    topics = [
        SubTopic(
            coverage_id="topic-01",
            title="Topic 1",
            rationale="This topic matters to the answer.",
            search_queries=["topic 1 evidence"],
            success_criteria=["A checked claim answers the topic."],
            priority=1,
            evidence_targets=[t1],
        ),
        SubTopic(
            coverage_id="topic-02",
            title="Topic 2",
            rationale="This topic matters to the answer.",
            search_queries=["topic 2 evidence"],
            success_criteria=["A checked claim answers the topic."],
            priority=2,
            evidence_targets=[t2],
        ),
    ]
    claim_one = Claim(
        claim_id=claim_fingerprint("Topic 1 was settled."),
        text="Topic 1 was settled.",
        source_urls=["https://example.test/a"],
        verdict="verified",
        evidence_status="verified_pair",
        confidence=0.9,
        evidence=["An independent review states the same figure."],
        contradictions=[],
        verification_evidence=[],
        target_ids=["t1"],
    )
    point = ReportPoint(
        text="Topic 1 was settled.",
        claim_ids=[claim_one.claim_id],
        source_urls=["https://example.test/a"],
        statement=ReportStatement(
            statement_id="S001",
            text="Topic 1 was settled.",
            claim_cluster_ids=[claim_one.claim_id],
            target_ids=["t1"],
            answered_dimensions=["finding"],
        ),
    )
    state = ResearchState(
        session_id="session-deferred",
        original_question="What happened?",
        sub_topics=topics,
        verified_claims=[claim_one],
        report="# Reader report",
        report_evidence="# Evidence ledger",
    )
    composition = ReportComposition(
        question=state.original_question,
        session_id=state.session_id,
        sub_topics=topics,
        claims=[claim_one],
        summary=[point],
    )
    return state, composition


def test_a_deferred_disposition_does_not_account_while_its_work_is_queued() -> (
    None
):
    """Section 2.6: a queued deferral is not a terminal disposition.

    ``_record_deferred_passages`` writes a ``deferred_capacity`` disposition
    at the same moment it queues the omitted passage for a later bounded
    extraction pass — the disposition records queued work, not a terminal
    judgement. Accounting for the target while that work still sits in the
    queue hides an obligation that has not actually been decided.
    """
    state, composition = _deferred_state_and_composition()
    disposition = EvidenceDisposition(
        item_id="read-1/p-3",
        stage="read-selection",
        reason="deferred_capacity",
        target_ids=["t2"],
    )
    queued_state = state.model_copy(
        update={
            "evidence_dispositions": [disposition],
            "acquisition_state_by_target": {
                "t2": AcquisitionState(
                    target_id="t2",
                    pending_passage_ids=["read-1/p-3"],
                    pending_extraction_ids=["read-1"],
                )
            },
        }
    )

    quality = compute_report_quality(queued_state, composition)

    assert quality.unaccounted_target_ids == ["t2"]
    assert "unaccounted_required_targets" in quality.hard_failures

    # Drain control: once the queue empties, the same disposition accounts
    # for the target again — this proves the guard tracks the queue, not the
    # reason string.
    drained_state = queued_state.model_copy(
        update={
            "acquisition_state_by_target": {
                "t2": AcquisitionState(
                    target_id="t2",
                    pending_passage_ids=[],
                    pending_extraction_ids=[],
                )
            }
        }
    )
    drained_quality = compute_report_quality(drained_state, composition)
    assert drained_quality.unaccounted_target_ids == []


def test_an_unattempted_target_with_a_recorded_reason_is_still_accounted() -> (
    None
):
    """A disposition with a reason but no acquisition record stays accounted.

    Pins the predicate's ``None`` branch: a target that never got an
    ``AcquisitionState`` entry at all — the shape most real dispositions
    carry — must not be treated as having outstanding work.
    """
    state, composition = _deferred_state_and_composition()
    disposition = EvidenceDisposition(
        item_id="candidate-1",
        stage="evidence_admission",
        reason="every candidate for this obligation was denied",
        target_ids=["t2"],
    )
    unattempted_state = state.model_copy(
        update={"evidence_dispositions": [disposition]}
    )

    quality = compute_report_quality(unattempted_state, composition)

    assert quality.unaccounted_target_ids == []


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
