"""Tests for deterministic report quality metrics and hard gates."""

from __future__ import annotations

import json

from deep_research.agents import (
    ReportQualitySnapshot as AgentReportQualitySnapshot,
)
from deep_research.agents.identity import claim_fingerprint, finding_fingerprint
from deep_research.agents.quality import (
    compute_report_quality,
    compute_substantive_coverage,
)
from deep_research.agents.report import (
    ReportComposition,
    ReportPoint,
    ReportSection,
    render_quality_json,
)
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


def _deferred_state_and_composition(
    *, critical: bool = False
) -> tuple[ResearchState, ReportComposition]:
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
        critical=critical,
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
        item_id="t2",
        stage="acquisition",
        reason="deferred_capacity",
        target_ids=["t2"],
    )
    queued_state = state.model_copy(
        update={
            "evidence_dispositions": [disposition],
            "acquisition_state_by_target": {
                "topic-02": AcquisitionState(
                    target_id="topic-02",
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
                "topic-02": AcquisitionState(
                    target_id="topic-02",
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
    """A target-level record with no acquisition state stays accounted.

    Pins the predicate's ``None`` branch: a target that never got an
    ``AcquisitionState`` entry at all must not be treated as having
    outstanding work. The record has to be about the obligation itself — the
    item id is the target — because a per-passage omission is not a judgement
    about whether the obligation can be met.
    """
    state, composition = _deferred_state_and_composition()
    disposition = EvidenceDisposition(
        item_id="t2",
        stage="acquisition",
        reason="every candidate for this obligation was denied",
        target_ids=["t2"],
    )
    unattempted_state = state.model_copy(
        update={"evidence_dispositions": [disposition]}
    )

    quality = compute_report_quality(unattempted_state, composition)

    assert quality.unaccounted_target_ids == []


# --- Change 6: terminal-pass accounting. At the terminal pass there is no
# later pass, so queued work is not outstanding, and a required non-critical
# target whose acquisition loop ran at least once is accounted as "pursued,
# unmet" rather than left to block acceptance on a target nobody could ever
# finish acquiring in a one-iteration run. -----------------------------------


def test_terminal_pass_treats_queued_work_as_not_outstanding() -> None:
    """A queue that will never be drained is not "outstanding" at the ceiling.

    The same fixture ``test_a_deferred_disposition_does_not_account_while_
    its_work_is_queued`` pins for a mid-run pass: at a mid-run pass the queue
    still holds the target back, but at ``terminal=True`` there is no later
    pass to drain it, so the target-itemed disposition the fixture already
    carries accounts for the target.
    """
    state, composition = _deferred_state_and_composition()
    disposition = EvidenceDisposition(
        item_id="t2",
        stage="acquisition",
        reason="deferred_capacity",
        target_ids=["t2"],
    )
    queued_state = state.model_copy(
        update={
            "evidence_dispositions": [disposition],
            "acquisition_state_by_target": {
                "topic-02": AcquisitionState(
                    target_id="topic-02",
                    pending_passage_ids=["read-1/p-3"],
                    pending_extraction_ids=["read-1"],
                )
            },
        }
    )

    mid_run = compute_report_quality(queued_state, composition)
    assert mid_run.unaccounted_target_ids == ["t2"]
    assert "unaccounted_required_targets" in mid_run.hard_failures

    terminal = compute_report_quality(queued_state, composition, terminal=True)
    assert terminal.unaccounted_target_ids == []
    assert "unaccounted_required_targets" not in terminal.hard_failures


def test_terminal_pass_accounts_a_pursued_unmet_required_target() -> None:
    """A required, non-critical target whose loop ran at least once.

    At a mid-run pass this target stays unaccounted: it carries neither a
    denied URL nor two empty searches, the live path's own bar. At the
    terminal pass there is no later pass to spend a second search on, so one
    search or one read is what "this obligation was pursued" can mean, and
    the target is accounted as pursued, unmet.
    """
    state, composition = _deferred_state_and_composition()
    pursued_state = state.model_copy(
        update={
            "acquisition_state_by_target": {
                "topic-02": AcquisitionState(
                    target_id="topic-02",
                    attempted_urls=["https://example.test/pursued"],
                    empty_searches=1,
                )
            }
        }
    )

    mid_run = compute_report_quality(pursued_state, composition)
    assert mid_run.unaccounted_target_ids == ["t2"]
    assert "unaccounted_required_targets" in mid_run.hard_failures

    terminal = compute_report_quality(pursued_state, composition, terminal=True)
    assert terminal.unaccounted_target_ids == []
    assert "unaccounted_required_targets" not in terminal.hard_failures


def test_a_critical_target_is_never_accounted_by_the_terminal_pursued_rule() -> (
    None
):
    """An unanswered critical target is a hard gate whatever the trail says.

    The terminal "pursued, unmet" rule is stated for a required *non-critical*
    target only; a critical target with the exact same acquisition trail
    stays unaccounted and still fails ``unanswered_critical_targets`` — the
    one gate the terminal pass never relaxes.
    """
    state, composition = _deferred_state_and_composition(critical=True)
    pursued_state = state.model_copy(
        update={
            "acquisition_state_by_target": {
                "topic-02": AcquisitionState(
                    target_id="topic-02",
                    attempted_urls=["https://example.test/pursued"],
                    empty_searches=1,
                )
            }
        }
    )

    terminal = compute_report_quality(pursued_state, composition, terminal=True)

    assert terminal.unanswered_critical_target_ids == ["t2"]
    assert "unanswered_critical_targets" in terminal.hard_failures


def test_a_never_attempted_target_stays_unaccounted_at_the_terminal_pass() -> (
    None
):
    """A target with no acquisition state at all was never pursued.

    The terminal pass lowers the bar for a target whose loop ran; it does not
    invent a bar-clearing attempt for one that was never opened.
    """
    state, composition = _deferred_state_and_composition()

    terminal = compute_report_quality(state, composition, terminal=True)

    assert terminal.unaccounted_target_ids == ["t2"]
    assert "unaccounted_required_targets" in terminal.hard_failures


def test_compute_report_quality_threads_terminal_into_substantive_coverage() -> (
    None
):
    """``terminal`` is the caller's own flag, threaded straight through."""
    state, composition = _deferred_state_and_composition()
    pursued_state = state.model_copy(
        update={
            "acquisition_state_by_target": {
                "topic-02": AcquisitionState(
                    target_id="topic-02",
                    attempted_urls=["https://example.test/pursued"],
                    empty_searches=1,
                )
            }
        }
    )

    mid_run = compute_substantive_coverage(pursued_state, composition)
    assert mid_run.accounted_target_ids == []

    terminal = compute_substantive_coverage(
        pursued_state, composition, terminal=True
    )
    assert terminal.accounted_target_ids == ["t2"]


def test_terminal_pursued_unmet_accounting_is_recorded_as_data() -> None:
    """P1-a: the terminal rule is recorded as data, not only a gate predicate.

    ``apply_terminal_pursued_unmet_accounting`` writes one target-itemed
    ``EvidenceDisposition`` (``reason="pursued_unmet"``) and one ``context``
    ``ReportStatement`` classified "not acquired" by the reader's own lexicon,
    so every consumer of the audit trail agrees once the update is merged:
    the quality snapshot's ``_accounts_for_target`` path (not only the
    terminal-only gate branch), the review packet's per-target view, and the
    rendered reader report's own "Not acquired" group.
    """
    from deep_research.agents.quality import (
        apply_terminal_pursued_unmet_accounting,
    )
    from deep_research.agents.report import render_reader_report
    from deep_research.agents.report_review import build_report_review_input
    from deep_research.utils.types import merge_research_state

    state, composition = _deferred_state_and_composition()
    pursued_state = state.model_copy(
        update={
            "iteration": 3,
            "max_iterations": 3,
            "composition": composition,
            "acquisition_state_by_target": {
                "topic-02": AcquisitionState(
                    target_id="topic-02",
                    attempted_urls=["https://example.test/pursued"],
                    empty_searches=1,
                )
            },
        }
    )

    update = apply_terminal_pursued_unmet_accounting(pursued_state)

    assert "evidence_dispositions" in update
    (disposition,) = update["evidence_dispositions"]
    assert disposition.item_id == "t2"
    assert disposition.stage == "acquisition"
    assert disposition.reason == "pursued_unmet"
    assert disposition.target_ids == ["t2"]

    assert "composition" in update
    (new_statement,) = [
        statement
        for statement in update["composition"].uncertainty_statements
        if statement.target_ids == ["t2"]
    ]
    assert new_statement.mode == "context"
    assert "not acquired" in (new_statement.basis or "").casefold()

    merged = merge_research_state(pursued_state, update)

    # The quality snapshot: accounted through the standard disposition path,
    # not only through the terminal-only gate branch — this holds even
    # without passing ``terminal=True`` again, because the reason is now real
    # data on the audit trail.
    quality = compute_report_quality(merged, merged.composition)
    assert quality.unaccounted_target_ids == []
    assert "unaccounted_required_targets" not in quality.hard_failures

    # The review packet: the same target reads as accounted there too.
    packet = build_report_review_input(merged, merged.composition)
    (target_view,) = [
        target for target in packet.targets if target.target_id == "t2"
    ]
    assert target_view.accounted is True

    # The rendered reader report: the target's own question is named under
    # "Not acquired", not silently dropped.
    rendered = render_reader_report(merged.composition)
    assert "### Not acquired" in rendered
    assert "What does topic 2 require?" in rendered


def test_terminal_pursued_unmet_accounting_is_a_no_op_off_the_terminal_pass() -> (
    None
):
    """Mid-run, nothing is materialized: there may still be a later pass."""
    from deep_research.agents.quality import (
        apply_terminal_pursued_unmet_accounting,
    )

    state, composition = _deferred_state_and_composition()
    mid_run_state = state.model_copy(
        update={
            "iteration": 1,
            "max_iterations": 3,
            "composition": composition,
            "acquisition_state_by_target": {
                "topic-02": AcquisitionState(
                    target_id="topic-02",
                    attempted_urls=["https://example.test/pursued"],
                    empty_searches=1,
                )
            },
        }
    )

    assert apply_terminal_pursued_unmet_accounting(mid_run_state) == {}


# --- Change 6: covered_topics counts a topic as covered when every counted
# target is answered, accounted (the terminal rule above), or optional. -----


def _two_target_topic(
    index: int, *, second_required: bool, second_critical: bool = False
) -> SubTopic:
    """One topic with two targets: ``t{index}a`` answerable, ``t{index}b`` as given."""
    return SubTopic(
        coverage_id=f"topic-{index:02d}",
        title=f"Topic {index}",
        rationale="This topic matters to the answer.",
        search_queries=[f"topic {index} evidence"],
        success_criteria=["A checked claim answers the topic."],
        priority=index,
        evidence_targets=[
            EvidenceTarget(
                target_id=f"t{index}a",
                coverage_id=f"topic-{index:02d}",
                question=f"What does topic {index}a require?",
                required_dimensions=["finding"],
                required=True,
                critical=False,
                support_policy="independent_pair",
            ),
            EvidenceTarget(
                target_id=f"t{index}b",
                coverage_id=f"topic-{index:02d}",
                question=f"What does topic {index}b require?",
                required_dimensions=["finding"],
                required=second_required,
                critical=second_critical,
                support_policy="independent_pair",
            ),
        ],
    )


def _one_answered_target_state_and_composition(
    topic: SubTopic,
    *,
    session_id: str,
    acquisition_state_by_target: dict[str, AcquisitionState] | None = None,
) -> tuple[ResearchState, ReportComposition]:
    claim = _claim("Topic 1a was settled.", coverage_id=topic.coverage_id)
    point = ReportPoint(
        text="Topic 1a was settled.",
        claim_ids=[claim.claim_id],
        source_urls=["https://example.test/a"],
        statement=ReportStatement(
            statement_id="S001",
            text="Topic 1a was settled.",
            claim_cluster_ids=[claim.claim_id],
            target_ids=[topic.evidence_targets[0].target_id],
            answered_dimensions=["finding"],
        ),
    )
    state = ResearchState(
        session_id=session_id,
        original_question="What happened?",
        sub_topics=[topic],
        verified_claims=[claim],
        report="# Reader report",
        report_evidence="# Evidence ledger",
        acquisition_state_by_target=acquisition_state_by_target or {},
    )
    composition = ReportComposition(
        question=state.original_question,
        session_id=state.session_id,
        sub_topics=[topic],
        claims=[claim],
        summary=[point],
    )
    return state, composition


def test_covered_topics_counts_an_optional_unanswered_target_as_covered() -> None:
    """A topic is covered when every counted target is answered or optional."""
    topic = _two_target_topic(1, second_required=False)
    state, composition = _one_answered_target_state_and_composition(
        topic, session_id="session-optional"
    )

    quality = compute_report_quality(state, composition)

    assert quality.substantive_covered_topics == 1
    assert quality.substantive_topic_ratio == 1.0


def test_covered_topics_counts_a_terminal_accounted_target_topic_as_covered() -> (
    None
):
    """A topic whose only gap is a terminally accounted target is covered."""
    topic = _two_target_topic(1, second_required=True)
    state, composition = _one_answered_target_state_and_composition(
        topic,
        session_id="session-terminal-covered",
        acquisition_state_by_target={
            "topic-01": AcquisitionState(
                target_id="topic-01",
                attempted_urls=["https://example.test/pursued"],
                empty_searches=1,
            )
        },
    )

    mid_run = compute_report_quality(state, composition)
    assert mid_run.substantive_covered_topics == 0

    terminal = compute_report_quality(state, composition, terminal=True)
    assert terminal.substantive_covered_topics == 1


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


def test_a_snapshot_predating_the_substantive_field_publishes_its_count() -> None:
    """The quality record reads the same fallback as the outcome.

    A snapshot with no substantive numerator publishes the claimed count under
    both names rather than a zero beside a nonzero ratio.
    """
    state, composition = _complete_state_and_composition(4)
    snapshot = compute_report_quality(state, composition)
    legacy = snapshot.model_copy(update={"substantive_covered_topics": None})
    judged = state.model_copy(update={"quality": legacy})

    record = json.loads(render_quality_json(judged, composition, None))
    counts = record["counts"]

    assert counts["covered_topics"] == legacy.covered_topics
    assert counts["claimed_covered_topics"] == legacy.covered_topics
    assert counts["substantive_topic_ratio"] == legacy.substantive_topic_ratio


def test_quality_snapshot_requires_scope_and_as_of_declarations() -> None:
    state, composition = _complete_state_and_composition(5)
    composition = composition.model_copy(update={"scope": " ", "as_of": ""})

    snapshot = compute_report_quality(state, composition)

    assert "missing_scope" in snapshot.hard_failures
    assert "missing_as_of" in snapshot.hard_failures


# --- Section 2.3 accounting: a passage-level omission is not a judgement
# about the obligation itself. ---------------------------------------------


def test_a_passage_level_disposition_does_not_account_for_a_target() -> None:
    """Only a terminal record about the obligation accounts for it.

    The producers write per-passage dispositions as a matter of course —
    ``claim_pool_dispositions`` stamps the unit's target ids on an
    ``out_of_scope`` row for every registry unit outside one packet, and the
    extraction step writes ``irrelevant`` for every selected passage that
    yielded no finding. Those say one passage was not used. Counting them as
    the target's account turned the §2.3 gate off for nearly every target that
    had a read: an unanswered required obligation then blocked nothing.
    """
    state, composition = _deferred_state_and_composition()
    assert compute_report_quality(state, composition).unaccounted_target_ids == [
        "t2"
    ]

    passage = EvidenceDisposition(
        item_id="e9",
        stage="adjudication-packet",
        reason="out_of_scope",
        target_ids=["t2"],
    )
    passage_state = state.model_copy(update={"evidence_dispositions": [passage]})
    quality = compute_report_quality(passage_state, composition)

    assert quality.unaccounted_target_ids == ["t2"]
    assert "unaccounted_required_targets" in quality.hard_failures

    # A record about the target itself is the §2.3 account.
    terminal = EvidenceDisposition(
        item_id="t2",
        stage="acquisition",
        reason="no candidate source was found for this obligation",
        target_ids=["t2"],
    )
    terminal_state = passage_state.model_copy(
        update={"evidence_dispositions": [terminal]}
    )
    assert (
        compute_report_quality(terminal_state, composition).unaccounted_target_ids
        == []
    )

    # And so is the acquisition trail the run writes itself: a denied read,
    # keyed the way the Researcher keys it — by the sub-topic's coverage id,
    # never by the target id the obligation carries.
    denied_state = passage_state.model_copy(
        update={
            "acquisition_state_by_target": {
                "topic-02": AcquisitionState(
                    target_id="topic-02",
                    remaining_calls=0,
                    empty_searches=2,
                    denied_urls=["https://example.test/denied"],
                )
            }
        }
    )
    assert (
        compute_report_quality(denied_state, composition).unaccounted_target_ids
        == []
    )


def test_the_snapshot_keeps_the_claimed_and_substantive_topic_counts_apart() -> (
    None
):
    """Two readings, two fields: a topic a claim consumed is not an answer.

    ``covered_topics`` is the claimed count kept for historical artifacts.
    The substantive numerator is the one the outcome and the CLI report, so
    the snapshot has to carry it rather than let a caller read the claimed
    field next to ``substantive_topic_ratio``.
    """
    state, composition = _complete_state_and_composition(4)
    claimed_only = _claim("Topic 5 was asserted.", coverage_id="topic-05")
    claimed_point = ReportPoint(
        text="Topic 5 was asserted.",
        claim_ids=[claimed_only.claim_id],
        source_urls=["https://example.test/a"],
        statement=ReportStatement(
            statement_id="S900",
            text="Topic 5 was asserted.",
            mode="attributed",
            claim_cluster_ids=[claimed_only.claim_id],
            target_ids=[],
            answered_dimensions=["finding"],
        ),
    )
    composition = composition.model_copy(
        update={
            "claims": [*composition.claims, claimed_only],
            "summary": [*composition.summary, claimed_point],
        }
    )
    state = state.model_copy(
        update={"verified_claims": [*state.verified_claims, claimed_only]}
    )

    snapshot = compute_report_quality(state, composition)

    assert snapshot.covered_topics == 5
    assert snapshot.coverage_ratio == 1.0
    assert snapshot.substantive_covered_topics == 4
    assert snapshot.substantive_topic_ratio == 0.8
    # The published record reads like the snapshot: the substantive count is
    # what `covered_topics` means there, and the claimed one keeps a name that
    # says what it is.
    judged = state.model_copy(update={"quality": snapshot})
    record = json.loads(render_quality_json(judged, composition, None))
    counts = record["counts"]
    assert counts["covered_topics"] == 4
    assert counts["claimed_covered_topics"] == 5
