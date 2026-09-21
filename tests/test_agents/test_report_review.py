"""Task 10: the terminal, source-bound semantic report review.

Every named adversarial case from the task brief and the controller's
carried-forward rulings is its own test function, because a review that
"looks right" is exactly what the structural proxy already provided. The
review under test is offline: scripted provider replies, no network.
"""

from __future__ import annotations

import math
import re

import pytest

from deep_research.agents.critic import CritiqueGapDraft
from deep_research.agents.quality import compute_report_quality
from deep_research.agents.report import ReportComposition, ReportPoint
from deep_research.agents.report_review import (
    REPORT_JUDGE_ROLE,
    REVIEW_DIMENSIONS,
    REVIEW_RUBRIC_VERSION,
    SEMANTIC_REVIEW_MEAN,
    ReportReviewer,
    build_report_review_input,
    report_review_input_fingerprint,
    review_messages,
    review_report,
    semantic_review_passes,
)
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.utils.types import (
    UNREVIEWED_STATEMENT_DISPOSITION,
    Claim,
    CritiqueGap,
    EvidenceDisposition,
    EvidenceTarget,
    EvidenceUnit,
    ReportQualitySnapshot,
    ReportReview,
    ReportStatement,
    ResearchState,
    SubTopic,
)
from tests.agent_fakes import ScriptedCompleter

SESSION_ID = "session-review"
QUESTION = "How mature is quantum error correction?"
_URL = "https://example.test/qec"
_URL_B = "https://example.test/other"

DIMENSION_NAMES = (
    "completeness",
    "prioritization",
    "evidence_quality",
    "attribution",
    "uncertainty",
    "readability",
    "actionability",
)


def _tracker() -> Tracker:
    return Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False, project="review-tests", api_key=None
        )
    )


def _unit(
    evidence_id: str,
    *,
    excerpt: str = "Logical error rates fell below break-even.",
    url: str = _URL,
    target_ids: tuple[str, ...] = ("t1",),
) -> EvidenceUnit:
    return EvidenceUnit(
        evidence_id=evidence_id,
        read_id=f"read-{evidence_id}",
        source_url=url,
        source_title="QEC 2025",
        locator="chunk-0",
        excerpt=excerpt,
        target_ids=list(target_ids),
        origin="researcher",
    )


def _claim(
    text: str,
    *,
    url: str = _URL,
    verdict: str = "verified",
    evidence_status: str | None = "verified_pair",
    cluster_id: str = "cluster-1",
    target_ids: tuple[str, ...] = ("t1",),
) -> Claim:
    from deep_research.agents.identity import claim_fingerprint

    return Claim(
        claim_id=claim_fingerprint(text),
        text=text,
        source_urls=[url],
        verdict=verdict,
        confidence=0.8,
        evidence=["An independent review states the same figure."],
        contradictions=[],
        verification_evidence=[],
        evidence_status=evidence_status,
        cluster_id=cluster_id,
        target_ids=list(target_ids),
    )


def _statement(
    statement_id: str,
    text: str,
    *,
    mode: str = "settled",
    clusters: tuple[str, ...] = ("cluster-1",),
    evidence: tuple[str, ...] = ("e1",),
    targets: tuple[str, ...] = ("t1",),
    dimensions: tuple[str, ...] = ("finding",),
    basis: str | None = None,
) -> ReportStatement:
    return ReportStatement(
        statement_id=statement_id,
        text=text,
        mode=mode,
        claim_cluster_ids=list(clusters),
        evidence_ids=list(evidence),
        target_ids=list(targets),
        answered_dimensions=list(dimensions),
        basis=basis,
    )


def _topic(
    *,
    coverage_id: str = "topic-01",
    target_id: str = "t1",
    critical: bool = False,
    required_dimensions: tuple[str, ...] = ("finding",),
    support_policy: str = "independent_pair",
) -> SubTopic:
    return SubTopic(
        coverage_id=coverage_id,
        title="Error correction",
        rationale="It is the bottleneck.",
        search_queries=["qec 2025"],
        success_criteria=["a logical error rate is quoted"],
        priority=1,
        evidence_targets=[
            EvidenceTarget(
                target_id=target_id,
                coverage_id=coverage_id,
                question="What is the logical error rate?",
                required_dimensions=list(required_dimensions),
                required=True,
                critical=critical,
                support_policy=support_policy,
            )
        ],
    )


def _composition(
    *,
    statements: tuple[ReportStatement, ...] | None = None,
    units: dict[str, EvidenceUnit] | None = None,
    claims: tuple[Claim, ...] | None = None,
    sub_topics: tuple[SubTopic, ...] | None = None,
    answer_kind: str = "factual",
    quality_status: str = "not yet quality-gated",
) -> ReportComposition:
    """A small, correctly cited composition with one settled statement."""
    claims = claims if claims is not None else (_claim("Break-even was reached."),)
    units = units if units is not None else {"e1": _unit("e1")}
    statements = (
        statements
        if statements is not None
        else (_statement("S001", "Break-even was reached."),)
    )
    return ReportComposition(
        question=QUESTION,
        session_id=SESSION_ID,
        iteration=0,
        max_iterations=3,
        as_of="2026-08-01",
        scope="Global",
        quality_status=quality_status,
        sub_topics=list(sub_topics if sub_topics is not None else (_topic(),)),
        claims=list(claims),
        sources=[],
        findings=[],
        evidence_units=units,
        summary=[
            ReportPoint(
                text=statement.text,
                claim_ids=[claim.claim_id for claim in claims],
                source_urls=[_URL],
                statement=statement,
            )
            for statement in statements
        ],
        answer_kind=answer_kind,
    )


def _state(
    *,
    composition: ReportComposition | None = None,
    report: str | None = None,
    **overrides: object,
) -> ResearchState:
    payload: dict[str, object] = {
        "session_id": SESSION_ID,
        "original_question": QUESTION,
        "composition": composition,
        "report": report,
        "initial_target_ids": ["t1"],
    }
    # Production keeps the frozen plan on the state and copies it into the
    # composition; the fixtures do the same, so coverage is read from the plan
    # rather than from whatever the report happened to render.
    if composition is not None:
        payload.setdefault("sub_topics", list(composition.sub_topics))
    payload.update(overrides)
    return ResearchState.model_validate(payload)


def _packet(state: ResearchState):
    return build_report_review_input(state)


def _reviewer(completer: ScriptedCompleter, tracker: Tracker | None = None):
    return ReportReviewer(provider=completer, tracker=tracker)


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


def _unsupported_defect(statement_id: str = "S001") -> CritiqueGap:
    return CritiqueGap(
        gap_id="gap-01",
        target_ids=["t1"],
        statement_ids=[statement_id],
        kind="missing_support",
        severity="critical",
        repair_action="adjudicate",
        problem="This statement is not carried by the cited passage.",
    )


def _render(packet) -> str:
    return "\n\n".join(message.content for message in review_messages(packet))


# --- the verbatim contract from the plan ------------------------------------


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

    state = _state(composition=_composition(), report="Break-even was reached.")
    tracker = _tracker()
    completer = ScriptedCompleter(outputs=[_draft_payload()])

    async with agent_scope(tracker, agent_name=REPORT_JUDGE_ROLE):
        review = await review_report(
            completer, _packet(state), tracker=tracker
        )

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
            CritiqueGap(
                gap_id="g1",
                target_ids=["t1"],
                claim_cluster_ids=["c1"],
                statement_ids=["s1"],
                kind="missing_support",
                severity="critical",
                repair_action="adjudicate",
                problem="The main number is not in the source.",
                recommended_queries=[],
            )
        ],
        per_statement_dispositions={"s1": "unsupported"},
        reviewed_statement_ids=["s1"],
        input_fingerprint="packet1",
        rubric_version=2,
        rationale="A central unsupported assertion.",
    )
    assert not semantic_review_passes(review)


def test_a_perfect_mean_still_fails_when_a_dimension_is_missing() -> None:
    assert semantic_review_passes(_scored_review())

    six = {
        name: 1.0 for name in DIMENSION_NAMES if name != "uncertainty"
    }
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


# --- the deterministic proxy this task replaces -----------------------------


def test_directive_language_alone_cannot_buy_a_pass() -> None:
    """A polished non-answer: "should", ranked bullets, no substantive answer.

    The deterministic gates must be unchanged by the word "should" — removing
    that keyword cannot alter deterministic acceptance — so the semantic review
    is the only gate that can fail an answer-shaped text.
    """
    composition = _composition()
    with_should = ReportComposition.model_validate(
        {
            **composition.model_dump(mode="python"),
            "summary": [
                {
                    **composition.summary[0].model_dump(mode="python"),
                    "text": "Operators should adopt this approach.",
                }
            ],
        }
    )
    state_a = _state(composition=composition, report="Break-even was reached.")
    state_b = _state(composition=with_should, report="Operators should act.")

    quality_a = compute_report_quality(state_a, composition)
    quality_b = compute_report_quality(state_b, with_should)

    assert quality_a.hard_failures == quality_b.hard_failures
    assert quality_a.covered_topics == quality_b.covered_topics
    assert quality_a.substantive_topic_ratio == quality_b.substantive_topic_ratio

    no_answer = _scored_review(
        dimensions={
            **{name: 0.95 for name in DIMENSION_NAMES},
            "completeness": 0.2,
        },
        defects=[
            CritiqueGap(
                gap_id="gap-01",
                coverage_id="topic-01",
                target_ids=["t1"],
                statement_ids=["S001"],
                kind="coverage",
                severity="critical",
                repair_action="acquire",
                problem="The report never states the logical error rate.",
                recommended_queries=["qec logical error rate 2025"],
            )
        ],
        per_statement_dispositions={"S001": "unsupported"},
    )
    assert not semantic_review_passes(no_answer)


# --- substantive coverage ---------------------------------------------------


def test_a_cutoff_date_claim_does_not_cover_a_technical_constraint() -> None:
    """A publication/cutoff date is not evidence for a technical obligation."""
    composition = _composition(
        statements=(
            _statement(
                "S001",
                "This survey covers publications up to 2025.",
                dimensions=("date",),
            ),
        ),
        sub_topics=(_topic(required_dimensions=("constraint",)),),
    )
    state = _state(composition=composition, report="This survey covers 2025.")
    quality = compute_report_quality(state, composition)

    assert quality.required_targets == 1
    assert quality.answered_targets == 0
    assert quality.substantive_topic_ratio == 0.0
    assert "broad_plan_coverage_below_0.80" not in quality.hard_failures

    target = _packet(state).targets[0]
    assert target.answered is False
    assert target.required_dimensions == ["constraint"]


def test_an_unsupported_topic_association_does_not_count() -> None:
    """A claim that consumed a topic id is not an answered obligation."""
    composition = _composition(
        claims=(
            _claim(
                "Break-even was reached.",
                evidence_status="source_supported",
                verdict="insufficient_evidence",
            ),
        ),
        sub_topics=(_topic(support_policy="independent_pair"),),
    )
    state = _state(composition=composition, report="Break-even was reached.")
    quality = compute_report_quality(state, composition)

    # The statement answers the dimension, but the evidence behind it is
    # primary-source attribution, which is not the independent pair the target
    # declared. The target stays unanswered.
    assert quality.answered_targets == 0
    assert quality.substantive_topic_ratio == 0.0


def test_an_unanswered_critical_target_blocks_acceptance() -> None:
    composition = _composition(
        statements=(
            _statement("S001", "Something else entirely.", targets=("t1",)),
        ),
        sub_topics=(
            _topic(critical=True, required_dimensions=("finding", "constraint")),
        ),
    )
    state = _state(composition=composition, report="Something else entirely.")
    quality = compute_report_quality(state, composition)

    assert quality.unanswered_critical_target_ids == ["t1"]
    assert "unanswered_critical_targets" in quality.hard_failures


def test_an_unaccounted_target_is_reported_separately_from_the_ratio() -> None:
    """The original denominator is kept, and target/topic metrics are apart."""
    topics = tuple(
        _topic(coverage_id=f"topic-{index:02d}", target_id=f"t{index}")
        for index in range(1, 4)
    )
    composition = _composition(
        statements=(
            _statement("S001", "Only the first is answered.", targets=("t1",)),
        ),
        sub_topics=topics,
    )
    state = _state(
        composition=composition,
        report="Only the first is answered.",
        initial_target_ids=["t1", "t2", "t3"],
    )
    quality = compute_report_quality(state, composition)

    assert quality.planned_topics == 3
    assert quality.required_targets == 3
    assert quality.answered_targets == 1
    assert quality.substantive_topic_ratio == pytest.approx(1 / 3)
    assert quality.unaccounted_target_ids == ["t2", "t3"]


def test_a_reason_in_the_evidence_audit_trail_accounts_for_a_target() -> None:
    """Section 2.6: insufficiency reasons are the local evidence for coverage."""
    composition = _composition(
        statements=(
            _statement("S001", "Only the first is answered.", targets=("t1",)),
        ),
        sub_topics=(
            _topic(coverage_id="topic-01", target_id="t1"),
            _topic(coverage_id="topic-02", target_id="t2"),
        ),
    )
    state = _state(
        composition=composition,
        report="Only the first is answered.",
        initial_target_ids=["t1", "t2"],
        evidence_dispositions=[
            EvidenceDisposition(
                item_id="candidate-1",
                stage="evidence_admission",
                reason="every candidate for this obligation was denied",
                target_ids=["t2"],
            )
        ],
    )
    quality = compute_report_quality(state, composition)

    assert quality.answered_targets == 1
    assert quality.unaccounted_target_ids == []


def test_the_expanded_inventory_is_a_union_never_a_smaller_denominator() -> None:
    from deep_research.utils.types import merge_research_state

    composition = _composition()
    state = _state(composition=composition, report="Break-even was reached.")
    merged = merge_research_state(
        state, {"initial_target_ids": ["t1", "t2"], "expanded_target_ids": ["t3"]}
    )
    assert merged.initial_target_ids == ["t1", "t2"]
    assert merged.expanded_target_ids == ["t3"]


# --- the packet: full report, no clipping, complete manifest ----------------


def test_the_report_is_never_prefix_clipped() -> None:
    closing = "The final row contradicts the opening claim."
    composition = _composition()
    report = f"{'Filler sentence. ' * 1_200}\n{closing}"
    assert len(report) > 16_000

    state = _state(composition=composition, report=report)
    packet = _packet(state)

    assert packet.reader_content == report
    assert packet.reader_content.rstrip().endswith(closing)
    assert closing in _render(packet)


def test_a_late_contradiction_beyond_16000_characters_is_reviewed() -> None:
    composition = _composition()
    late = "Break-even was later disputed."
    report = f"{'Filler sentence. ' * 1_200}\n{late}"
    state = _state(composition=composition, report=report)

    assert late in _render(_packet(state))


def test_evidence_beyond_the_packet_budget_is_batched_with_a_manifest() -> None:
    units = {
        f"e{index}": _unit(f"e{index}", excerpt="X" * 400)
        for index in range(1, 40)
    }
    statements = tuple(
        _statement(f"S{index:03d}", f"Fact {index}.", evidence=(f"e{index}",))
        for index in range(1, 40)
    )
    composition = _composition(statements=statements, units=units)
    state = _state(composition=composition, report="A long report.")
    packet = _packet(state)

    batched = [
        item.evidence_id
        for batch in packet.evidence_batches
        for item in batch.items
    ]
    assert len(packet.evidence_batches) > 1
    assert sorted(batched) == sorted(units)
    assert len(batched) == len(set(batched))
    assert packet.omitted_evidence_ids == []
    assert packet.expected_batch_ids == [
        batch.batch_id for batch in packet.evidence_batches
    ]
    assert packet.total_batches == len(packet.evidence_batches)


def test_the_packet_carries_every_statement_and_target() -> None:
    state = _state(composition=_composition(), report="Break-even was reached.")
    packet = _packet(state)

    assert [statement.statement_id for statement in packet.statements] == ["S001"]
    assert [target.target_id for target in packet.targets] == ["t1"]
    assert packet.expected_statement_ids == ["S001"]


def test_the_review_request_cannot_see_the_critic_score_or_the_threshold() -> None:
    from deep_research.agents.critic import Critique

    composition = _composition()
    low = _state(
        composition=composition,
        report="Break-even was reached.",
        critique=Critique(
            score=2,
            gaps=[],
            unsupported_claims=[],
            recommended_queries=[],
            should_continue=True,
            rationale="Weak.",
        ),
    )
    high = _state(
        composition=composition,
        report="Break-even was reached.",
        critique=Critique(
            score=9,
            gaps=[],
            unsupported_claims=[],
            recommended_queries=[],
            should_continue=False,
            rationale="Strong.",
        ),
    )
    assert review_messages(_packet(low)) == review_messages(_packet(high))

    text = _render(_packet(high)).casefold()
    # Word-bounded: "critical target" is a plan concept, not coaching, and the
    # check is for the *other* reviewer's score rather than for a substring.
    assert re.search(r"\bcritic\b", text) is None
    for forbidden in (
        "acceptance threshold",
        "suggested verdict",
        "should_continue",
        "prior run",
    ):
        assert forbidden not in text


def test_the_review_packet_carries_no_score_field_at_all() -> None:
    fields = set(
        type(_packet(_state(composition=_composition()))).model_fields
    )
    for forbidden in ("critic_score", "score", "threshold", "verdict", "quality"):
        assert forbidden not in fields


def test_the_request_names_the_exact_fingerprint_it_reviews() -> None:
    packet = _packet(_state(composition=_composition(), report="A report."))
    assert packet.fingerprint in _render(packet)


# --- fingerprint semantics --------------------------------------------------


def test_a_cosmetic_status_badge_does_not_invalidate_the_fingerprint() -> None:
    composition = _composition()
    state = _state(composition=composition, report="Break-even was reached.")
    before = report_review_input_fingerprint(_packet(state))

    badged = composition.model_copy(update={"quality_status": "accepted"})
    after = report_review_input_fingerprint(
        _packet(_state(composition=badged, report="Break-even was reached."))
    )
    assert before == after


def test_a_content_change_invalidates_the_fingerprint() -> None:
    composition = _composition()
    state = _state(composition=composition, report="Break-even was reached.")
    before = report_review_input_fingerprint(_packet(state))

    changed = composition.model_copy(
        update={
            "summary": [
                ReportPoint(
                    text="Break-even was never reached.",
                    claim_ids=list(composition.summary[0].claim_ids),
                    source_urls=[_URL],
                    statement=composition.summary[0].statement,
                )
            ]
        }
    )
    after = report_review_input_fingerprint(
        _packet(_state(composition=changed, report="Break-even was never reached."))
    )
    assert before != after


def test_a_target_change_invalidates_the_fingerprint() -> None:
    composition = _composition()
    state = _state(composition=composition, report="Break-even was reached.")
    before = report_review_input_fingerprint(_packet(state))

    fewer = composition.model_copy(update={"sub_topics": []})
    assert before != report_review_input_fingerprint(
        _packet(_state(composition=fewer, report="Break-even was reached."))
    )


def test_a_reference_change_invalidates_the_fingerprint() -> None:
    composition = _composition()
    state = _state(composition=composition, report="Break-even was reached.")
    before = report_review_input_fingerprint(_packet(state))

    re_excerpted = composition.model_copy(
        update={
            "evidence_units": {
                "e1": _unit("e1", excerpt="A different passage.")
            }
        }
    )
    assert before != report_review_input_fingerprint(
        _packet(_state(composition=re_excerpted, report="Break-even was reached."))
    )


# --- review_report: coverage, batching, failures ----------------------------


def _draft_payload(
    *,
    dimensions: dict[str, float] | None = None,
    defects: list[CritiqueGapDraft] | None = None,
    dispositions: dict[str, str] | None = None,
    reviewed_statements: list[str] | None = None,
    reviewed_evidence: list[str] | None = None,
    rationale: str = "Reviewed the report and its evidence.",
):
    from deep_research.agents.report_review import (
        ReportReviewDraft,
        ReviewDimensionScores,
        StatementDispositionDraft,
    )

    scores = _scores() if dimensions is None else dimensions
    return ReportReviewDraft(
        dimensions=ReviewDimensionScores(**scores),
        statement_dispositions=[
            StatementDispositionDraft(
                statement_id=statement_id, disposition=disposition
            )
            for statement_id, disposition in (
                {"S001": "supported"} if dispositions is None else dispositions
            ).items()
        ],
        defects=list(defects or []),
        reviewed_statement_ids=(
            ["S001"] if reviewed_statements is None else reviewed_statements
        ),
        reviewed_evidence_ids=(
            ["e1"] if reviewed_evidence is None else reviewed_evidence
        ),
        rationale=rationale,
    )


@pytest.mark.asyncio
async def test_a_complete_review_is_scored() -> None:
    state = _state(composition=_composition(), report="Break-even was reached.")
    packet = _packet(state)
    completer = ScriptedCompleter(outputs=[_draft_payload()])

    review = await review_report(completer, packet)

    assert review.status == "scored"
    assert set(review.dimensions) == REVIEW_DIMENSIONS
    assert review.input_fingerprint == packet.fingerprint
    assert review.rubric_version == REVIEW_RUBRIC_VERSION
    assert review.reviewed_statement_ids == ["S001"]
    assert review.unreviewed_statement_ids == []
    assert review.per_statement_dispositions == {"S001": "supported"}
    assert review.reviewed_batch_ids == packet.expected_batch_ids
    assert len(completer.calls) == 1
    assert semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_missing_statement_review_is_incomplete_not_a_default_pass() -> None:
    composition = _composition(
        statements=(
            _statement("S001", "Break-even was reached."),
            _statement("S002", "A second claim.", evidence=("e2",)),
        ),
        units={"e1": _unit("e1"), "e2": _unit("e2")},
    )
    state = _state(composition=composition, report="Two statements.")
    completer = ScriptedCompleter(
        outputs=[
            _draft_payload(
                reviewed_statements=["S001"],
                reviewed_evidence=["e1", "e2"],
            )
        ]
    )

    review = await review_report(completer, _packet(state))

    assert review.status == "incomplete"
    assert review.unreviewed_statement_ids == ["S002"]
    assert not semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_reply_that_records_no_statement_disposition_is_incomplete() -> None:
    """Claiming the reading is not the review: the judgement has to be recorded.

    ``reviewed_statement_ids`` is the reply's own account of what it read; a
    disposition is the judgement it reached about that statement. A reply that
    lists every statement id and records a disposition for none of them has
    skipped the per-statement support review the Task 7 brief demanded and this
    task exists to perform — and with a perfect mean and no defects it would
    otherwise be recorded ``scored`` and accepted, publishing a judgement that
    was never made. Missing statement review is incomplete, not a default pass.
    """
    composition = _composition(
        statements=(
            _statement("S001", "Break-even was reached."),
            _statement("S002", "A second claim.", evidence=("e2",)),
        ),
        units={"e1": _unit("e1"), "e2": _unit("e2")},
    )
    state = _state(composition=composition, report="Two statements.")
    packet = _packet(state)
    completer = ScriptedCompleter(
        outputs=[
            _draft_payload(
                dispositions={},
                reviewed_statements=["S001", "S002"],
                reviewed_evidence=["e1", "e2"],
            )
        ]
    )

    review = await review_report(completer, packet)

    assert len(completer.calls) == 1
    assert review.status == "incomplete"
    assert review.dimensions == {}
    assert review.per_statement_dispositions == {}
    assert not semantic_review_passes(review)
    assert "S001" in review.rationale
    assert "S002" in review.rationale


@pytest.mark.asyncio
async def test_a_complete_review_of_several_statements_is_still_scored() -> None:
    """The inverse failure, pinned: a complete review must not be refused.

    The rule this adds is "one disposition per statement the packet carries",
    so a review that records one for every statement it read is scored and
    accepted exactly as before — the requirement cannot be satisfied by
    refusing every multi-statement report.
    """
    composition = _composition(
        statements=(
            _statement("S001", "Break-even was reached."),
            _statement("S002", "A second claim.", evidence=("e2",)),
        ),
        units={"e1": _unit("e1"), "e2": _unit("e2")},
    )
    state = _state(composition=composition, report="Two statements.")
    packet = _packet(state)
    completer = ScriptedCompleter(
        outputs=[
            _draft_payload(
                dispositions={"S001": "supported", "S002": "attributed"},
                reviewed_statements=["S001", "S002"],
                reviewed_evidence=["e1", "e2"],
            )
        ]
    )

    review = await review_report(completer, packet)

    assert review.status == "scored"
    assert review.per_statement_dispositions == {
        "S001": "supported",
        "S002": "attributed",
    }
    assert review.reviewed_statement_ids == ["S001", "S002"]
    assert semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_missing_evidence_batch_is_incomplete_not_a_default_pass() -> None:
    units = {"e1": _unit("e1"), "e2": _unit("e2")}
    statements = (
        _statement("S001", "One.", evidence=("e1",)),
        _statement("S002", "Two.", evidence=("e2",)),
    )
    composition = _composition(statements=statements, units=units)
    state = _state(composition=composition, report="Two statements.")
    # The cross-section reply covers only ``e1``, so ``batch-01`` is re-asked —
    # and its own reply also leaves ``e2`` unreviewed. A batch that never came
    # back with its evidence is what "missing evidence batch" means.
    from deep_research.agents.report_review import ReviewBatchDraft

    completer = ScriptedCompleter(
        outputs=[
            _draft_payload(
                reviewed_statements=["S001", "S002"],
                reviewed_evidence=["e1"],
            ),
            ReviewBatchDraft(
                batch_id="batch-01",
                statement_dispositions=[],
                defects=[],
                reviewed_statement_ids=[],
                reviewed_evidence_ids=["e1"],
                problem="",
            ),
        ]
    )

    review = await review_report(completer, _packet(state))

    assert review.status == "incomplete"
    assert "e2" in review.omitted_evidence_ids
    assert review.reviewed_batch_ids == []
    assert not semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_provider_failure_is_recorded_provider_failed_never_scored() -> None:
    from deep_research.providers import ProviderError

    state = _state(composition=_composition(), report="Break-even was reached.")
    completer = ScriptedCompleter(outputs=[ProviderError("the provider is down")])

    review = await review_report(completer, _packet(state))

    assert review.status == "provider_failed"
    assert review.dimensions == {}
    assert not semantic_review_passes(review)
    assert review.rationale.strip()


@pytest.mark.asyncio
async def test_a_schema_failure_is_incomplete_not_a_default_pass() -> None:
    state = _state(composition=_composition(), report="Break-even was reached.")
    completer = ScriptedCompleter(outputs=[{"not": "a draft"}])

    review = await review_report(completer, _packet(state))

    assert review.status == "incomplete"
    assert not semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_fingerprint_mismatch_is_incomplete_not_a_default_pass() -> None:
    state = _state(composition=_composition(), report="Break-even was reached.")
    packet = _packet(state)
    completer = ScriptedCompleter(outputs=[_draft_payload()])

    review = await review_report(
        completer,
        packet,
        reviewed_fingerprint="a-different-packet",
    )

    assert review.status == "incomplete"
    assert review.input_fingerprint == packet.fingerprint
    assert not semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_defect_outside_the_packet_is_refused() -> None:
    state = _state(composition=_composition(), report="Break-even was reached.")
    completer = ScriptedCompleter(
        outputs=[
            _draft_payload(
                defects=[
                    CritiqueGapDraft(
                        target_ids=["t-does-not-exist"],
                        kind="coverage",
                        severity="critical",
                        repair_action="acquire",
                        problem="A topic nobody planned is missing.",
                    )
                ]
            )
        ]
    )

    review = await review_report(completer, _packet(state))

    assert review.status == "incomplete"
    assert review.defects == []
    assert not semantic_review_passes(review)


@pytest.mark.asyncio
async def test_an_unsettled_disposition_always_blocks_acceptance() -> None:
    """A reply that says "unsupported" cannot also claim a clean review."""
    state = _state(composition=_composition(), report="Break-even was reached.")
    completer = ScriptedCompleter(
        outputs=[_draft_payload(dispositions={"S001": "unsupported"}, defects=[])]
    )

    review = await review_report(completer, _packet(state))

    assert review.status == "scored"
    assert review.per_statement_dispositions == {"S001": "unsupported"}
    assert review.derived_defect_statement_ids == ["S001"]
    assert review.material_defects
    assert not semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_minor_defect_cannot_suppress_the_derived_material_defect() -> None:
    """C-1: a contract-valid reply is recorded, never left to crash the run.

    ``minor`` is not material, so a reply that dispositions a statement
    ``unsupported`` and returns only a *minor* defect naming it has not
    satisfied the record contract's rule that an unsettled statement be named
    by a material defect. The skip test in ``_derived_defects`` asked whether
    *any* defect named the statement, derived nothing, and ``_merge_review``
    raised a ``ValidationError`` straight out of ``review_report`` — no review,
    no status, and neither exit 4 nor exit 3. The judgement did happen; it must
    be recorded, with the material defect it needs derived for it.
    """
    state = _state(composition=_composition(), report="Break-even was reached.")
    completer = ScriptedCompleter(
        outputs=[
            _draft_payload(
                defects=[
                    CritiqueGapDraft(
                        target_ids=["t1"],
                        statement_ids=["S001"],
                        kind="presentation",
                        severity="minor",
                        repair_action="synthesize",
                        problem="The sentence reads awkwardly.",
                    )
                ],
                dispositions={"S001": "unsupported"},
            )
        ]
    )

    review = await review_report(completer, _packet(state))

    assert review.status == "scored"
    assert review.per_statement_dispositions == {"S001": "unsupported"}
    assert review.derived_defect_statement_ids == ["S001"]
    (derived,) = [
        gap for gap in review.defects if gap.gap_id.startswith("review-")
    ]
    assert derived.material
    assert derived.statement_ids == ["S001"]
    assert not semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_judgement_that_cannot_be_recorded_is_incomplete_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The backstop: the merge is the last place a contract violation can land.

    Fixing the materiality test makes C-1's reply shape unreachable, so the
    guard is tested where it lives. Assembling the record is the one step that
    can still fail on the record contract; a ``ValidationError`` there must be
    recorded as ``incomplete`` — no dimensions, no defects, the reason in the
    rationale — rather than propagating to a caller that has no exit code for
    it. An unrecorded judgement is not an acceptance.
    """
    from deep_research.agents import report_review as report_review_module

    state = _state(composition=_composition(), report="Break-even was reached.")
    packet = _packet(state)
    completer = ScriptedCompleter(outputs=[_draft_payload()])
    merge = report_review_module._merge_review  # noqa: SLF001

    def refuse_to_record_a_judgement(*args: object, **kwargs: object) -> ReportReview:
        if kwargs.get("status") != "incomplete":
            # A genuine pydantic failure from the real record contract, not a
            # hand-made exception: this is the shape C-1 produced.
            return ReportReview.model_validate({"status": "scored"})
        return merge(*args, **kwargs)

    monkeypatch.setattr(
        report_review_module, "_merge_review", refuse_to_record_a_judgement
    )

    review = await review_report(completer, packet)

    assert review.status == "incomplete"
    assert review.dimensions == {}
    assert review.defects == []
    assert "ValidationError" in review.rationale
    assert not semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_returned_to_fact_checker_disposition_blocks() -> None:
    """Item 5: prose that introduces an unattested mechanism goes back."""
    state = _state(composition=_composition(), report="Break-even was reached.")
    completer = ScriptedCompleter(
        outputs=[_draft_payload(dispositions={"S001": "returned_to_fact_checker"})]
    )

    review = await review_report(completer, _packet(state))

    assert review.per_statement_dispositions["S001"] == "returned_to_fact_checker"
    assert review.unsettled_statement_ids == ["S001"]
    assert review.material_defects
    assert not semantic_review_passes(review)


def test_an_unsettled_statement_without_a_material_defect_is_refused() -> None:
    """The type boundary: "unsupported" cannot be recorded as a clean score."""
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
    """The other half of the coverage rule, at the type boundary.

    ``reviewed_statement_ids`` says what the review read; a disposition says
    what it concluded about it. The contract already refuses a scored review
    that declares an unread statement, and it must equally refuse one that read
    a statement and recorded no judgement of it — otherwise the per-statement
    review can be omitted while the record still reads ``scored``.
    """
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


@pytest.mark.asyncio
async def test_oversized_evidence_is_reviewed_batch_by_batch() -> None:
    from deep_research.agents.report_review import ReviewBatchDraft

    units = {
        f"e{index}": _unit(f"e{index}", excerpt="X" * 400)
        for index in range(1, 12)
    }
    statements = tuple(
        _statement(f"S{index:03d}", f"Fact {index}.", evidence=(f"e{index}",))
        for index in range(1, 12)
    )
    composition = _composition(statements=statements, units=units)
    state = _state(composition=composition, report="A long report.")
    packet = _packet(state)
    assert len(packet.evidence_batches) > 1

    outputs: list[object] = [
        _draft_payload(
            reviewed_statements=[
                statement.statement_id for statement in statements
            ],
            # The cross-section reply reads every statement and only the first
            # batch's evidence, so the remaining batches have to be asked for by
            # their own requests — which is the behaviour this test is named
            # for. A reply that claimed every evidence id up front would satisfy
            # the coverage assertion below without one batch request ever being
            # made, and deleting the follow-up loop entirely would leave it
            # passing.
            reviewed_evidence=packet.evidence_batches[0].evidence_ids,
            dispositions={
                statement.statement_id: "supported" for statement in statements
            },
        )
    ]
    for batch in packet.evidence_batches[1:]:
        outputs.append(
            ReviewBatchDraft(
                batch_id=batch.batch_id,
                statement_dispositions=[],
                defects=[],
                reviewed_statement_ids=[],
                reviewed_evidence_ids=[item.evidence_id for item in batch.items],
                problem="",
            )
        )
    completer = ScriptedCompleter(outputs=outputs)

    review = await review_report(completer, packet)

    assert review.status == "scored"
    assert review.reviewed_batch_ids == packet.expected_batch_ids
    assert len(completer.calls) == len(packet.evidence_batches)


@pytest.mark.asyncio
async def test_a_later_supported_cannot_overwrite_an_earlier_unsupported() -> None:
    """The disagreement rule: an unsettled reading is not voted away.

    A cross-section reply and a follow-up batch reply judge the same statements
    from different material, and they can disagree. The merge keeps the *less*
    settled reading, because "this sentence is not carried by its evidence" may
    not be overwritten by a second reply's "supported" — recording two readings
    of one sentence as agreement is the one outcome that turns a finding into
    an acceptance. The rule only ever runs on a batched review, which is
    exactly where nothing pinned it: replacing its condition with an
    unconditional overwrite left the whole suite green.
    """
    from deep_research.agents.report_review import ReviewBatchDraft

    units = {
        f"e{index}": _unit(f"e{index}", excerpt="Y" * 3000)
        for index in range(1, 4)
    }
    statements = tuple(
        _statement(f"S{index:03d}", f"Fact {index}.", evidence=(f"e{index}",))
        for index in range(1, 4)
    )
    composition = _composition(statements=statements, units=units)
    state = _state(composition=composition, report="A long report.")
    packet = _packet(state)
    assert len(packet.evidence_batches) > 1

    outputs: list[object] = [
        _draft_payload(
            defects=[
                CritiqueGapDraft(
                    target_ids=["t1"],
                    statement_ids=["S001"],
                    kind="missing_support",
                    severity="major",
                    repair_action="adjudicate",
                    problem="S001 is not carried by its passage.",
                )
            ],
            dispositions={
                "S001": "unsupported",
                "S002": "supported",
                "S003": "supported",
            },
            reviewed_statements=["S001", "S002", "S003"],
            reviewed_evidence=packet.evidence_batches[0].evidence_ids,
        )
    ]
    for index, batch in enumerate(packet.evidence_batches[1:]):
        outputs.append(
            ReviewBatchDraft(
                batch_id=batch.batch_id,
                # The first follow-up contradicts the cross-section reply about
                # S001; the rest judge nothing new.
                statement_dispositions=(
                    [{"statement_id": "S001", "disposition": "supported"}]
                    if index == 0
                    else []
                ),
                defects=[],
                reviewed_statement_ids=[],
                reviewed_evidence_ids=[item.evidence_id for item in batch.items],
                problem="",
            )
        )
    completer = ScriptedCompleter(outputs=outputs)

    review = await review_report(completer, packet)

    assert review.status == "scored"
    assert review.per_statement_dispositions["S001"] == "unsupported"
    assert not semantic_review_passes(review)
    assert review.unsettled_statement_ids == ["S001"]


@pytest.mark.asyncio
async def test_a_later_unsupported_reading_displaces_an_earlier_supported() -> None:
    """The other direction, which is the one that loses a finding.

    The rule is order-independent on purpose: the *less* settled reading wins
    wherever it arrives. The test above covers a later "supported" arriving
    after an "unsupported"; this one covers a later "unsupported" arriving
    after a "supported", which is the direction where a regression is
    dangerous — a follow-up reply saying "this sentence is not in the source"
    would be discarded in favour of the earlier "supported", and the review
    would pass with the suite green. Pinning only the first direction was the
    re-review's N-1 finding: replacing the condition with a first-write-wins
    test left every other test passing.
    """
    from deep_research.agents.report_review import ReviewBatchDraft

    units = {
        f"e{index}": _unit(f"e{index}", excerpt="Z" * 3000)
        for index in range(1, 4)
    }
    statements = tuple(
        _statement(f"S{index:03d}", f"Fact {index}.", evidence=(f"e{index}",))
        for index in range(1, 4)
    )
    composition = _composition(statements=statements, units=units)
    state = _state(composition=composition, report="A long report.")
    packet = _packet(state)
    assert len(packet.evidence_batches) > 1

    outputs: list[object] = [
        _draft_payload(
            dispositions={
                "S001": "supported",
                "S002": "supported",
                "S003": "supported",
            },
            reviewed_statements=["S001", "S002", "S003"],
            reviewed_evidence=packet.evidence_batches[0].evidence_ids,
        )
    ]
    for index, batch in enumerate(packet.evidence_batches[1:]):
        outputs.append(
            ReviewBatchDraft(
                batch_id=batch.batch_id,
                # The first follow-up withdraws the cross-section reply's
                # reading of S001, and returns no defect of its own — so the
                # material defect that blocks acceptance has to be derived from
                # the disposition itself.
                statement_dispositions=(
                    [{"statement_id": "S001", "disposition": "unsupported"}]
                    if index == 0
                    else []
                ),
                defects=[],
                reviewed_statement_ids=[],
                reviewed_evidence_ids=[item.evidence_id for item in batch.items],
                problem="",
            )
        )
    completer = ScriptedCompleter(outputs=outputs)

    review = await review_report(completer, packet)

    assert review.status == "scored"
    assert review.per_statement_dispositions["S001"] == "unsupported"
    assert review.derived_defect_statement_ids == ["S001"]
    assert not semantic_review_passes(review)
    assert review.unsettled_statement_ids == ["S001"]


@pytest.mark.asyncio
async def test_a_reviewer_records_its_own_call_fingerprint() -> None:
    state = _state(composition=_composition(), report="Break-even was reached.")
    completer = ScriptedCompleter(outputs=[_draft_payload()])
    reviewer = _reviewer(completer)

    review = await reviewer.review(_packet(state))

    assert reviewer.name == REPORT_JUDGE_ROLE
    assert reviewer.allowed_tools == ()
    assert review.status == "scored"
    assert set(reviewer.call_fingerprints) == {"ReportReviewDraft"}
    assert completer.calls[0][1] == REPORT_JUDGE_ROLE
    assert completer.calls[0][0] == "ReportReviewDraft"


@pytest.mark.asyncio
async def test_a_reused_review_costs_no_provider_call() -> None:
    state = _state(composition=_composition(), report="Break-even was reached.")
    packet = _packet(state)
    stored = await review_report(
        ScriptedCompleter(outputs=[_draft_payload()]),
        packet,
    )
    completer = ScriptedCompleter(outputs=[])
    reused = await _reviewer(completer).review(packet, previous=stored)

    assert reused is stored
    assert completer.calls == []


@pytest.mark.asyncio
async def test_a_stored_review_of_other_content_is_not_reused() -> None:
    state = _state(composition=_composition(), report="Break-even was reached.")
    packet = _packet(state)
    stored = _scored_review(input_fingerprint="a-different-packet")
    completer = ScriptedCompleter(outputs=[_draft_payload()])

    review = await _reviewer(completer).review(packet, previous=stored)

    assert review is not stored
    assert review.status == "scored"
    assert review.input_fingerprint == packet.fingerprint
    assert len(completer.calls) == 1


@pytest.mark.asyncio
async def test_an_incomplete_stored_review_is_never_reused() -> None:
    state = _state(composition=_composition(), report="Break-even was reached.")
    packet = _packet(state)
    stored = _scored_review().model_copy(update={"status": "incomplete"})
    completer = ScriptedCompleter(outputs=[_draft_payload()])

    review = await _reviewer(completer).review(packet, previous=stored)

    assert review is not stored
    assert review.status == "scored"


# --- the semantic dimensions the brief names --------------------------------


@pytest.mark.asyncio
async def test_a_fabricated_citation_is_a_material_defect() -> None:
    state = _state(composition=_composition(), report="Break-even was reached.")
    completer = ScriptedCompleter(
        outputs=[
            _draft_payload(
                defects=[
                    CritiqueGapDraft(
                        target_ids=["t1"],
                        statement_ids=["S001"],
                        kind="missing_support",
                        severity="critical",
                        repair_action="adjudicate",
                        problem="The cited passage does not contain this number.",
                    )
                ],
                dispositions={"S001": "unsupported"},
            )
        ]
    )
    review = await review_report(completer, _packet(state))

    assert review.status == "scored"
    assert not semantic_review_passes(review)
    assert [gap.kind for gap in review.material_defects] == ["missing_support"]


@pytest.mark.asyncio
async def test_an_unqualified_single_source_conclusion_is_a_defect() -> None:
    state = _state(composition=_composition(), report="Every vendor has solved this.")
    completer = ScriptedCompleter(
        outputs=[
            _draft_payload(
                dimensions={**_scores(), "attribution": 0.5},
                defects=[
                    CritiqueGapDraft(
                        target_ids=["t1"],
                        statement_ids=["S001"],
                        kind="source_quality",
                        severity="major",
                        repair_action="assess_source",
                        problem=(
                            "A single vendor's own statement is presented as a "
                            "universal conclusion."
                        ),
                    )
                ],
                dispositions={"S001": "attributed"},
            )
        ]
    )
    review = await review_report(completer, _packet(state))

    assert not semantic_review_passes(review)


@pytest.mark.asyncio
async def test_supported_attribution_passes_without_a_second_work() -> None:
    """Section 2.1: an attributed primary fact is not failed for a lone work."""
    attributed = (
        _claim(
            "The agency measured 3.2%.",
            verdict="insufficient_evidence",
            evidence_status="source_supported",
        ),
    )
    strict = _composition(
        claims=attributed,
        statements=(
            _statement("S001", "The agency measured 3.2%.", dimensions=("finding",)),
        ),
    )
    strict_state = _state(composition=strict, report="The agency measured 3.2%.")
    # The target declared ``independent_pair``, and attribution is not a pair.
    assert compute_report_quality(strict_state, strict).answered_targets == 0

    attributed_composition = _composition(
        claims=attributed,
        statements=(
            _statement("S001", "The agency measured 3.2%.", dimensions=("finding",)),
        ),
        sub_topics=(_topic(support_policy="primary_attribution"),),
    )
    state = _state(
        composition=attributed_composition, report="The agency measured 3.2%."
    )
    assert compute_report_quality(state, attributed_composition).answered_targets == 1

    completer = ScriptedCompleter(
        outputs=[
            _draft_payload(
                dimensions={**_scores(), "attribution": 1.0},
                dispositions={"S001": "attributed"},
            )
        ]
    )
    review = await review_report(completer, _packet(state))
    assert semantic_review_passes(review)


@pytest.mark.asyncio
async def test_an_incorrect_comparison_denominator_is_a_defect() -> None:
    composition = _composition(answer_kind="comparison")
    state = _state(composition=composition, report="A is twice B.")
    completer = ScriptedCompleter(
        outputs=[
            _draft_payload(
                dimensions={**_scores(), "evidence_quality": 0.6},
                defects=[
                    CritiqueGapDraft(
                        target_ids=["t1"],
                        statement_ids=["S001"],
                        kind="missing_support",
                        severity="major",
                        repair_action="adjudicate",
                        problem=(
                            "The comparison divides a per-capita figure by a "
                            "national total, so the ratio is meaningless."
                        ),
                    )
                ],
            )
        ]
    )
    review = await review_report(completer, _packet(state))
    assert not semantic_review_passes(review)


@pytest.mark.asyncio
async def test_an_invented_limitation_is_a_defect() -> None:
    state = _state(
        composition=_composition(),
        report="No data was available for any region.",
    )
    completer = ScriptedCompleter(
        outputs=[
            _draft_payload(
                dimensions={**_scores(), "uncertainty": 0.4},
                defects=[
                    CritiqueGapDraft(
                        target_ids=["t1"],
                        statement_ids=["S001"],
                        kind="coverage",
                        severity="major",
                        repair_action="synthesize",
                        problem=(
                            "The report invents a data gap the evidence does "
                            "not record."
                        ),
                    )
                ],
            )
        ]
    )
    review = await review_report(completer, _packet(state))
    assert not semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_late_contradiction_is_still_reviewed_and_refused() -> None:
    late = "Break-even was later disputed."
    report = f"{'Filler sentence. ' * 1_200}\n{late}"
    state = _state(composition=_composition(), report=report)
    packet = _packet(state)
    assert late in _render(packet)

    completer = ScriptedCompleter(
        outputs=[
            _draft_payload(
                dimensions={**_scores(), "uncertainty": 0.3},
                defects=[
                    CritiqueGapDraft(
                        target_ids=["t1"],
                        statement_ids=["S001"],
                        kind="contradiction",
                        severity="major",
                        repair_action="adjudicate",
                        problem=(
                            "The closing statement contradicts the opening "
                            "claim and both are presented as settled."
                        ),
                    )
                ],
            )
        ]
    )
    review = await review_report(completer, packet)
    assert not semantic_review_passes(review)


@pytest.mark.asyncio
async def test_a_sentence_initial_unattested_place_name_is_reportable() -> None:
    """Ruling 5's accepted residual: no offline rule can catch this one.

    The review is the only mechanism that can, so the request must tell the
    reviewer to check every name and place in a statement against the evidence
    behind it with *no* sentence-position exemption, and the defect it reports
    must block acceptance.

    The rule is asserted as a rule, not as two words: the task review deleted
    this whole sentence-position paragraph from the prompt and every test still
    passed, because "sentence" and "place" also occur in the unrelated
    ``returned_to_fact_checker`` guidance. The assertions below fail when the
    capability is removed, which is what makes the residual above verifiable
    rather than merely stated.
    """
    composition = _composition(
        statements=(
            _statement("S001", "California added capacity.", dimensions=("finding",)),
        ),
    )
    state = _state(composition=composition, report="California added capacity.")
    packet = _packet(state)
    request = _render(packet)
    assert "California added capacity." in request
    lowered = request.casefold()
    assert "sentence" in lowered
    assert "place" in lowered
    assert "including a name or place that opens a sentence" in request
    assert "sentence position is not evidence" in request

    completer = ScriptedCompleter(
        outputs=[
            _draft_payload(
                dimensions={**_scores(), "attribution": 0.4},
                defects=[
                    CritiqueGapDraft(
                        target_ids=["t1"],
                        statement_ids=["S001"],
                        kind="missing_support",
                        severity="major",
                        repair_action="adjudicate",
                        problem=(
                            "The place name in this statement appears in no "
                            "cited passage."
                        ),
                    )
                ],
                dispositions={"S001": "returned_to_fact_checker"},
            )
        ]
    )
    review = await review_report(completer, packet)

    assert not semantic_review_passes(review)
    assert review.defects[0].statement_ids == ["S001"]


def test_the_request_asks_for_an_evidenced_comparison_basis() -> None:
    """Ruling 6: a ranking needs a real basis, not abundance or confidence.

    Asserted over the ranking section — the part of the request that governs
    the rows — rather than over the whole request. The review gutted this
    section's rule paragraph and the test still passed, because the same three
    words also occur in the system prompt's own wording; a check that a
    sentence is present somewhere is not a check that the section carries it.
    """
    composition = _composition(answer_kind="constraints")
    state = _state(composition=composition, report="A ranked list.")
    request = _render(_packet(state))
    ranking = request.split("# Ranking and comparison basis")[1].split("# Evidence")[0]
    lowered = ranking.casefold()

    assert "comparison basis" in lowered
    assert "importance" in lowered
    assert "abundance" in lowered
    assert "no defensible universal order" in lowered


@pytest.mark.asyncio
async def test_a_ranking_without_a_comparison_basis_defects_prioritization() -> None:
    composition = _composition(answer_kind="constraints")
    state = _state(composition=composition, report="A ranked list.")
    completer = ScriptedCompleter(
        outputs=[
            _draft_payload(
                dimensions={**_scores(), "prioritization": 0.3},
                defects=[
                    CritiqueGapDraft(
                        coverage_id="topic-01",
                        target_ids=["t1"],
                        kind="missing_support",
                        severity="major",
                        repair_action="acquire",
                        problem=(
                            "The ranking order is asserted without any "
                            "evidenced comparison between the options."
                        ),
                        recommended_queries=["qec constraint comparison 2025"],
                    )
                ],
            )
        ]
    )
    review = await review_report(completer, _packet(state))

    assert review.status == "scored"
    assert not semantic_review_passes(review)
    (defect,) = review.material_defects
    assert defect.repair_action == "acquire"
    assert defect.target_ids == ["t1"]


def test_a_ranked_report_shows_the_reviewer_its_rows_and_their_evidence() -> None:
    """Ruling 6's gate is only a gate if the ranked rows reach the reviewer.

    A constraint row's statement records the dimensions the *recorded atom*
    carries, and the words a constraints plan uses — "constraint", "ranking",
    "priority" — match no atom signal, so a legitimately attested row carries no
    answered dimension at all. A ranking section that selects rows by that field
    therefore selects nothing on a real constraints report: it prints the basis
    rule, then says no ranked row was recorded, and the prioritization judgement
    has no order and no comparison to be made from. The rows come from the
    composition's own ranked order, which is what the reader table prints.
    """
    from deep_research.agents.report import ReportComposition
    from deep_research.utils.types import ReportConstraint

    # Keyed by evidence id, valued with the stance it was selected under: the
    # reverse orientation names no id in the registry and reads as no evidence.
    claim = _claim("Interconnection queues dominate.", target_ids=("t1",)).model_copy(
        update={"evidence_selection": {"e1": "supports"}}
    )
    composition = ReportComposition(
        question=QUESTION,
        session_id=SESSION_ID,
        iteration=0,
        max_iterations=3,
        as_of="2026-08-01",
        scope="Global",
        sub_topics=[_topic(required_dimensions=("constraint", "comparison"))],
        claims=[claim],
        evidence_units={
            "e1": _unit(
                "e1", excerpt="Queue delays dominate interconnection in Britain."
            )
        },
        answer_kind="constraints",
        constraints=[
            ReportConstraint(
                text="Interconnection queues dominate.",
                claim_ids=[claim.claim_id],
                source_urls=[_URL],
                deployment_mechanism="Capacity markets pay for availability.",
                geography="Great Britain.",
            )
        ],
    )
    row_statement = composition.constraints[0].statement
    assert row_statement is not None
    # The derivation's own record, not one this test wrote: the recorded atom
    # carries none of the dimensions the plan asked for.
    assert row_statement.answered_dimensions == []
    assert row_statement.evidence_ids == ["e1"]

    state = _state(composition=composition, report="A ranked table.")
    request = _render(_packet(state))
    ranking = request.split("# Ranking and comparison basis")[1].split("# Evidence")[0]

    assert "Interconnection queues dominate." in ranking
    assert "Capacity markets pay for availability." in ranking
    assert "Queue delays dominate interconnection in Britain." in ranking


def test_the_composition_fingerprint_ignores_the_presentation_badge() -> None:
    """A stamp the finalizer writes is not a content change."""
    from deep_research.agents.report_review import (
        composition_semantic_fingerprint,
    )

    composition = _composition()
    before = composition_semantic_fingerprint(composition)

    assert before
    assert (
        composition_semantic_fingerprint(
            composition.model_copy(update={"quality_status": "accepted"})
        )
        == before
    )


def test_the_composition_fingerprint_moves_with_content_and_references() -> None:
    from deep_research.agents.report_review import (
        composition_semantic_fingerprint,
    )

    composition = _composition()
    before = composition_semantic_fingerprint(composition)

    reworded = composition.model_copy(
        update={
            "summary": [
                ReportPoint(
                    text="Break-even was never reached.",
                    claim_ids=list(composition.summary[0].claim_ids),
                    source_urls=[_URL],
                    statement=composition.summary[0].statement,
                )
            ]
        }
    )
    assert composition_semantic_fingerprint(reworded) != before

    re_excerpted = composition.model_copy(
        update={"evidence_units": {"e1": _unit("e1", excerpt="Other text.")}}
    )
    assert composition_semantic_fingerprint(re_excerpted) != before

    re_targeted = composition.model_copy(update={"sub_topics": []})
    assert composition_semantic_fingerprint(re_targeted) != before


def test_replacing_the_composition_invalidates_a_mismatched_review() -> None:
    """The state rule: a judgement belongs to the report it judged."""
    from deep_research.agents.report_review import (
        composition_semantic_fingerprint,
    )
    from deep_research.utils.types import merge_research_state

    composition = _composition()
    stored = _scored_review(
        composition_fingerprint=composition_semantic_fingerprint(composition)
    )
    state = _state(
        composition=composition,
        report="Break-even was reached.",
        report_review=stored,
    )

    # The same content, re-stamped: the review survives.
    restamped = composition.model_copy(update={"quality_status": "accepted"})
    kept = merge_research_state(state, {"composition": restamped})
    assert kept.report_review is not None
    assert kept.report_review.input_fingerprint == "packet-1"

    # Different content: the review is gone, and gone is never "passed".
    changed = composition.model_copy(update={"sub_topics": []})
    dropped = merge_research_state(state, {"composition": changed})
    assert dropped.report_review is None


def test_the_composition_fingerprint_covers_cells_as_statements() -> None:
    """Table cells are reader-visible content and part of the identity.

    A constraint row renders a deployment-mechanism cell and a geography cell,
    and an answer row renders its own cells. They are ``ReportStatement``
    records rather than points, so a projection that treated them as points
    raised ``AttributeError: 'ReportStatement' object has no attribute
    'claim_ids'`` — which is what the CLI acceptance tests hit before this test
    existed, on a composition that had constraints and answer rows.
    """
    from deep_research.agents.report_review import (
        composition_semantic_fingerprint,
    )
    from deep_research.utils.types import ReportAnswerRow, ReportConstraint

    composition = _composition(answer_kind="constraints")
    mechanism = _statement("C001", "A capacity market pays for availability.")
    geography = _statement("C002", "The scheme covers Great Britain.")
    row = ReportConstraint(
        text="Interconnection queue delays dominate.",
        claim_ids=list(composition.summary[0].claim_ids),
        source_urls=[_URL],
        statement=composition.summary[0].statement,
        deployment_mechanism=mechanism.text,
        geography=geography.text,
        mechanism_statement=mechanism,
        geography_statement=geography,
    )
    with_rows = composition.model_copy(
        update={
            "constraints": [row],
            "answer_rows": [ReportAnswerRow(cells=[geography, mechanism])],
        }
    )

    before = composition_semantic_fingerprint(with_rows)
    assert before

    # A cell's text is content: changing it changes the identity.
    reworded_cell = with_rows.model_copy(
        update={
            "constraints": [
                row.model_copy(
                    update={
                        "geography_statement": geography.model_copy(
                            update={"text": "The scheme covers Ireland."}
                        )
                    }
                )
            ]
        }
    )
    assert composition_semantic_fingerprint(reworded_cell) != before

    # ...and so is the row's own point content.
    reworded_row = with_rows.model_copy(
        update={
            "constraints": [
                row.model_copy(update={"text": "Something else dominates."})
            ]
        }
    )
    assert composition_semantic_fingerprint(reworded_row) != before


def test_the_composition_fingerprint_covers_section_points() -> None:
    """A themed findings bullet is reader-visible content, like the summary.

    ``composition.sections`` renders as the report's themed bullet groups, so
    its points are content a reader receives. The projection that added table
    cells as statements dropped these points on the way past: a section bullet
    is a ``ReportPoint``, not a cell statement, so it must stay in the points
    projection rather than disappearing from the identity altogether.
    """
    from deep_research.agents.report_review import (
        composition_semantic_fingerprint,
    )
    from deep_research.utils.types import ReportSection

    composition = _composition()
    section = ReportSection(
        title="Error correction",
        points=[
            ReportPoint(
                text=composition.summary[0].text,
                claim_ids=list(composition.summary[0].claim_ids),
                source_urls=[_URL],
                statement=composition.summary[0].statement,
            )
        ],
    )
    with_sections = composition.model_copy(update={"sections": [section]})
    before = composition_semantic_fingerprint(with_sections)
    assert before

    reworded = with_sections.model_copy(
        update={
            "sections": [
                ReportSection(
                    title="Error correction",
                    points=[
                        ReportPoint(
                            text="Break-even was never reached.",
                            claim_ids=list(composition.summary[0].claim_ids),
                            source_urls=[_URL],
                            statement=composition.summary[0].statement,
                        )
                    ],
                )
            ]
        }
    )
    assert composition_semantic_fingerprint(reworded) != before


def test_a_quality_snapshot_keeps_the_review_apart_from_its_diagnostics() -> None:
    snapshot = ReportQualitySnapshot(
        coverage_ratio=1.0,
        planned_topics=1,
        covered_topics=1,
        unique_findings=1,
        unique_sources=1,
        cited_sources=1,
        scored_cited_source_ratio=1.0,
        verified_claims=1,
        contradicted_claims=0,
        duplicate_claims=0,
        duplicate_source_rows=0,
        uncited_settled_points=0,
        hard_failures=[],
        semantic_review_status="incomplete",
        semantic_review_score=None,
    )
    assert snapshot.hard_failures == []
    assert snapshot.semantic_review_status == "incomplete"
    assert snapshot.semantic_review_score is None
    assert _URL_B  # a second address, so no fixture accidentally reuses one
