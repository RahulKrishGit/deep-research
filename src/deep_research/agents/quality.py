"""The written report's deterministic gate set (spec §6.4, PD-10).

Nine gates over one pass's report and the verified findings behind it:
unresolved citations, uncited settled points, duplicate fact rows, a missing
as-of or scope, sentences the Statement Check never judged, required targets
neither answered nor listed as Not found, and the two missing artifacts.
Every reading is typed state or the typed composition -- nothing here
inspects Markdown.

The claim-era accounting below it (``compute_substantive_coverage``,
``pursued_unmet_target_ids``, ``apply_terminal_pursued_unmet_accounting``)
stays until Task 4.10 deletes it with the rest of the claim pipeline
(PD-21): the graph's synthesizer node still calls it.
"""

from __future__ import annotations

from collections.abc import Sequence

from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report import ReportComposition
from deep_research.agents.verified_facts import (
    answered_target_ids,
    same_organisation,
    same_period,
)
from deep_research.utils.types import (
    EvidenceDisposition,
    EvidenceTarget,
    ReportQualitySnapshot,
    ReportReview,
    ReportStatement,
    ResearchState,
    SubstantiveCoverage,
    counted_evidence_targets,
    target_is_answered,
)

# Re-exported for the graph and the package surface (PD-21): the broad-plan
# gate these bounded is gone (PD-10), and Task 4.10 removes both names.
BROAD_PLAN_MIN_TOPICS = 5
BROAD_PLAN_COVERAGE_THRESHOLD = 0.80


def _target_by_id(
    state: ResearchState, target_id: str
) -> EvidenceTarget | None:
    """The plan's own evidence target for ``target_id``, if the plan names it.

    Shared by every reader that needs the target object rather than just its
    id: the acquisition-state lookup keys by the sub-topic's coverage id, and
    the terminal accounting rule needs the target's own ``critical`` flag.
    """
    for topic in state.sub_topics:
        for target in counted_evidence_targets(topic.evidence_targets):
            if target.target_id == target_id:
                return target
    return None


def _acquisition_state_for(
    state: ResearchState, target_id: str
) -> AcquisitionState | None:
    """The acquisition state of the target a given obligation id names.

    The Researcher is the only writer of ``acquisition_state_by_target``, and
    it keys each entry by the sub-topic's ``coverage_id``: one acquisition
    loop runs per sub-topic, and a target's own id is namespaced *inside*
    that coverage id (``topic-01-target-01``). A reader looking the queue up
    by target id therefore found nothing in any production run, which read as
    "this obligation has no queue" — the opposite of what the entry said.

    A target id the plan does not name resolves to no state at all, which is
    the honest answer for it: the accounting has no queue to judge and no
    attempt to credit.
    """
    target = _target_by_id(state, target_id)
    if target is None:
        return None
    return state.acquisition_state_by_target.get(target.coverage_id)


def _has_outstanding_work(
    acquisition: AcquisitionState | None, *, terminal: bool = False
) -> bool:
    """True when this target's acquisition still holds queued work.

    Keyed on state, not on which reason wrote a disposition:
    ``EvidenceDisposition.reason`` is an intentionally unconstrained ``str``
    (a future reason token must stay readable without touching this guard),
    so the one reason that coexists with genuinely pending work
    (``deferred_capacity``) cannot be told apart from a terminal one by
    string alone. A target with no recorded acquisition state at all has no
    queue to be outstanding, which is not the same claim as "nothing is
    queued" — see the second branch's own docstring in
    ``_accounted_target_ids``.

    At the terminal pass (``terminal=True``) queued work is never
    outstanding: there is no later pass to drain a candidate URL or a pending
    passage, so a queue that will never run again cannot hold a target's
    account back.
    """
    if acquisition is None or terminal:
        return False
    return bool(
        acquisition.candidate_urls
        or acquisition.pending_passage_ids
        or acquisition.pending_extraction_ids
    )


def _acquisition_loop_ran(acquisition: AcquisitionState) -> bool:
    """True once this target's acquisition made at least one search or read.

    The terminal-pass "pursued, unmet" rule needs a lower bar than the live
    path's "denied, or two empty searches" (``_accounted_target_ids``): there
    is no later pass to spend a second search on, so one search or one read
    attempt is what "this obligation was pursued" can mean here.
    ``consecutive_searches`` resets to zero the moment a read follows it, so a
    target that searched once and then read is caught by ``attempted_urls``/
    ``read_urls`` instead — together the four fields are the whole trail a
    loop that ran at all leaves behind.
    """
    return bool(
        acquisition.attempted_urls
        or acquisition.read_urls
        or acquisition.consecutive_searches
        or acquisition.empty_searches
    )


def _accounts_for_target(disposition: EvidenceDisposition, target_id: str) -> bool:
    """Whether one disposition is a recorded judgement about this obligation.

    The record has to be about the target itself: its item is the target, not
    a passage, a read, or a candidate URL. The producers write per-passage
    dispositions as a matter of course — ``claim_pool_dispositions`` stamps
    the unit's target ids on an ``out_of_scope`` row for every registry unit
    outside one packet, and the extraction step writes ``irrelevant`` for every
    selected passage that yielded no finding — and those say one passage was
    not used, never that the obligation cannot be met. Counting them turned
    the §2.3 accounting gate off for nearly every target that had a read.

    No producer writes a target-itemed record today, so on real input this
    predicate is never true and the accounting comes from the acquisition
    trail (``_accounted_target_ids``). It states the rule such a record would
    have to meet rather than describing a path the run takes.
    """
    return disposition.item_id == target_id and bool(disposition.reason.strip())


def pursued_unmet_target_ids(
    state: ResearchState, unanswered: Sequence[str]
) -> list[str]:
    """The required, non-critical targets among ``unanswered`` worth recording
    as "pursued, unmet" at the terminal pass, in ``unanswered`` order.

    A target qualifies when its acquisition holds no work that would be
    outstanding at the terminal pass (``_has_outstanding_work(...,
    terminal=True)``), it does not already clear the live-path bar (a denied
    URL, or two empty searches — that target is accounted for some other
    way), it is not critical (``unanswered_critical_targets`` stays a hard
    gate the terminal pass never relaxes), and its acquisition loop ran at
    least one search or read (``_acquisition_loop_ran``).

    This is the pure decision ``_accounted_target_ids`` uses for its own
    terminal-only branch, exported so the graph's synthesizer node can also
    call it directly: the caller is expected to *record* each id here as a
    real, target-itemed ``EvidenceDisposition`` (see
    ``apply_terminal_pursued_unmet_accounting``) rather than let this
    function's return value be the only place the decision is visible — a
    gate predicate a caller must re-derive is not an audit trail.
    """
    ids: list[str] = []
    for target_id in unanswered:
        acquisition = _acquisition_state_for(state, target_id)
        if acquisition is None:
            continue
        if _has_outstanding_work(acquisition, terminal=True):
            continue
        if acquisition.denied_urls or acquisition.empty_searches >= 2:
            continue
        target = _target_by_id(state, target_id)
        if (
            target is not None
            and not target.critical
            and _acquisition_loop_ran(acquisition)
        ):
            ids.append(target_id)
    return ids


def _accounted_target_ids(
    state: ResearchState,
    unanswered: Sequence[str],
    *,
    terminal: bool = False,
) -> set[str]:
    """The unanswered targets this run has a recorded reason for.

    Three local records count, and all are evidence a reader can check
    rather than a claim about the model's intent:

    * an ``EvidenceDisposition`` whose *item* is the target — the shape a
      terminal judgement about the obligation takes. Real producers write one
      target-itemed record today: at the terminal pass, the graph's
      synthesizer node writes a ``pursued_unmet`` disposition for every id
      ``pursued_unmet_target_ids`` names (``apply_terminal_pursued_unmet_
      accounting``), before this pass's report is rendered. A per-passage
      omission, however explicit its reason, still does not count
      (``_accounts_for_target``);
    * an acquisition state for the target that shows a spent search (a denied
      URL, or two empty searches) with nothing queued behind it. **This is the
      live path**: it is where a run actually records that an obligation was
      pursued and could not be met;
    * at the terminal pass only (``terminal=True``), the same "pursued,
      unmet" set ``pursued_unmet_target_ids`` computes, read directly here as
      well: this is the gate predicate's own fallback, so a caller that asks
      this question before (or instead of) the graph node has recorded the
      matching disposition still gets the correct answer. There is no later
      pass to spend a second search or clear a queue on, so "pursued, unmet"
      is the honest reading of a target the run tried and did not finish,
      rather than treating it as never attempted.

    None of the three counts while the target's acquisition still holds
    queued work at a non-terminal pass (``_has_outstanding_work``): a
    deferral is a decision to do the work later, which is the opposite of
    terminal — ``_record_deferred_passages`` writes a ``deferred_capacity``
    disposition in the same breath it queues the omitted passage, so the
    disposition alone cannot be read as a finished judgement while that queue
    is still open and another pass remains to drain it. At the terminal pass
    a queue can never drain, so it never holds a target back
    (``_has_outstanding_work(..., terminal=True)`` is always ``False``).

    Nothing else does. In particular a target nobody has attempted yet is
    *not* accounted for, at any pass: calling an unstarted obligation
    "unavailable" is the same error as calling it "answered", in the
    opposite direction.

    Every record is read through the target's own coverage id
    (``_acquisition_state_for``), which is the key the Researcher writes the
    queue under.
    """

    accounted: set[str] = set()
    for disposition in state.evidence_dispositions:
        accounted.update(
            target_id
            for target_id in disposition.target_ids
            if target_id in unanswered
            and _accounts_for_target(disposition, target_id)
            and not _has_outstanding_work(
                _acquisition_state_for(state, target_id), terminal=terminal
            )
        )
    for target_id in unanswered:
        acquisition = _acquisition_state_for(state, target_id)
        if acquisition is None:
            continue
        if _has_outstanding_work(acquisition, terminal=terminal):
            continue
        if acquisition.denied_urls or acquisition.empty_searches >= 2:
            accounted.add(target_id)
    if terminal:
        accounted.update(pursued_unmet_target_ids(state, unanswered))
    return accounted


def compute_substantive_coverage(
    state: ResearchState,
    composition: ReportComposition | None,
    *,
    terminal: bool = False,
) -> SubstantiveCoverage:
    """Section 2.3 coverage, from the plan's obligations and the answers.

    The denominator is the frozen inventory: for each planned sub-topic, the
    counted evidence targets it declared — the targets the plan stamped, never
    a subset some later pass happened to work on. The numerator is
    ``target_is_answered``, which reads the reader statements this composition
    actually renders: a target is answered only when a substantive statement
    names it, fills every required dimension, and rests on evidence carrying
    the target's support policy. A claim that merely recorded consuming a
    topic id does not enter this count at all.

    ``covered_topics`` counts a topic when every counted target is answered,
    accounted for (the terminal-pass rule in ``_accounted_target_ids``), or
    optional (``not target.required``): a topic a plan owes nothing on, or
    whose only open obligation is a pursued-and-unmet or genuinely optional
    target, is not the gap a broad-plan gate exists to catch. A topic with no
    counted target at all is not covered — the honest reading of a plan that
    owes nothing, and the one that keeps a legacy plan from scoring 100%.

    ``terminal`` is the caller's own fact about this pass (``iteration >=
    max_extra_passes``), threaded straight into ``_accounted_target_ids``: it
    changes what counts as accounted, which in turn changes which topics
    ``covered_topics`` reads as covered.

    The judgement is made against the composition this function was handed,
    not against whatever the state happens to carry: ``target_is_answered``
    reads reader statements from the state, and a caller that scored one
    composition while the state held another would otherwise get an answer
    about the wrong report.

    ``composition`` may be ``None`` for a session whose Markdown predates the
    composition contract. That is the "no reader statement was composed"
    reading, which is what ``target_is_answered`` already returns for a state
    with no composition: every obligation stands unanswered at the plan's own
    denominator.
    """

    view = (
        state
        if state.composition is composition
        else state.model_copy(update={"composition": composition})
    )

    planned_targets = 0
    required_targets = 0
    answered_targets = 0
    critical_targets = 0
    answered_critical_targets = 0
    unanswered_required: list[str] = []
    unanswered_critical: list[str] = []
    answered_by_id: dict[str, bool] = {}

    for topic in state.sub_topics:
        counted = counted_evidence_targets(topic.evidence_targets)
        planned_targets += len(counted)
        for target in counted:
            answered = target_is_answered(view, target)
            answered_by_id[target.target_id] = answered
            if target.required:
                required_targets += 1
                if answered:
                    answered_targets += 1
                else:
                    unanswered_required.append(target.target_id)
            if target.critical:
                critical_targets += 1
                if answered:
                    answered_critical_targets += 1
                else:
                    unanswered_critical.append(target.target_id)

    accounted = _accounted_target_ids(state, unanswered_required, terminal=terminal)

    covered_topics = 0
    for topic in state.sub_topics:
        counted = counted_evidence_targets(topic.evidence_targets)
        if counted and all(
            answered_by_id[target.target_id]
            or not target.required
            or target.target_id in accounted
            for target in counted
        ):
            covered_topics += 1

    planned_topics = len(state.sub_topics)
    return SubstantiveCoverage(
        planned_topics=planned_topics,
        covered_topics=covered_topics,
        topic_ratio=(
            covered_topics / planned_topics if planned_topics else 0.0
        ),
        planned_targets=planned_targets,
        required_targets=required_targets,
        answered_targets=answered_targets,
        critical_targets=critical_targets,
        answered_critical_targets=answered_critical_targets,
        unanswered_required_target_ids=unanswered_required,
        unanswered_critical_target_ids=unanswered_critical,
        accounted_target_ids=[
            target_id
            for target_id in unanswered_required
            if target_id in accounted
        ],
        unaccounted_target_ids=[
            target_id
            for target_id in unanswered_required
            if target_id not in accounted
        ],
        initial_target_ids=list(state.initial_target_ids),
        expanded_target_ids=list(state.expanded_target_ids),
    )


def apply_terminal_pursued_unmet_accounting(
    state: ResearchState,
) -> dict[str, object]:
    """Record the terminal pass's "pursued, unmet" targets as real data.

    Called once, from the graph's synthesizer node, after the composition
    exists and before it is rendered to Markdown. For every id
    ``pursued_unmet_target_ids`` names, this returns an update that adds:

    * one target-itemed ``EvidenceDisposition`` (``reason="pursued_unmet"``),
      so ``_accounts_for_target`` and every quality-snapshot consumer read a
      real record instead of only the gate predicate seeing the decision;
    * one ``context`` ``ReportStatement``, appended to the composition's
      ``uncertainty_statements`` (and ``uncertainty_notes``) with a
      ``basis`` the reader's own lexicon classifies "Not acquired"
      (``report._uncertainty_group``) — so the rendered reader report and
      the evidence ledger, both pure functions of the composition, name the
      target instead of silently omitting it.

    The return value is a plain ``ResearchStateUpdate``-shaped dict, never a
    full ``ResearchState``: ``evidence_dispositions`` is a mergeable list
    field (``merge_evidence_dispositions`` unions the new records into
    whatever the state already carries), so the caller applies this update
    the same way every other node applies its own — through
    ``merge_research_state`` — rather than this function re-deriving the
    union itself.

    An empty dict off the terminal pass, without a composition, or when
    nothing qualifies: nothing is recorded, so a caller may call this
    unconditionally every pass without guarding it itself.
    """
    composition = state.composition
    if state.iteration < state.max_extra_passes or composition is None:
        return {}
    coverage = compute_substantive_coverage(state, composition)
    pursued = pursued_unmet_target_ids(
        state, coverage.unanswered_required_target_ids
    )
    if not pursued:
        return {}

    existing_item_ids = {
        disposition.item_id for disposition in state.evidence_dispositions
    }
    new_dispositions = [
        EvidenceDisposition(
            item_id=target_id,
            stage="acquisition",
            reason="pursued_unmet",
            target_ids=[target_id],
        )
        for target_id in pursued
        if target_id not in existing_item_ids
    ]

    target_by_id = {
        target.target_id: target
        for topic in state.sub_topics
        for target in counted_evidence_targets(topic.evidence_targets)
    }
    existing_texts = set(composition.uncertainty_notes)
    new_statements: list[ReportStatement] = []
    for target_id in pursued:
        target = target_by_id.get(target_id)
        question = target.question if target is not None else target_id
        text = (
            f"Not acquired: {question} was pursued (at least one search or "
            "read) but was not resolved within this run's budget."
        )
        if text in existing_texts:
            continue
        new_statements.append(
            ReportStatement(
                statement_id=f"terminal-{target_id}",
                text=text,
                mode="context",
                target_ids=[target_id],
                basis=(
                    "not acquired: the acquisition loop ran and did not "
                    "resolve within this run's budget"
                ),
            )
        )

    update: dict[str, object] = {}
    if new_dispositions:
        update["evidence_dispositions"] = new_dispositions
    if new_statements:
        update["composition"] = composition.model_copy(
            update={
                "uncertainty_statements": [
                    *composition.uncertainty_statements,
                    *new_statements,
                ],
                "uncertainty_notes": [
                    *composition.uncertainty_notes,
                    *(statement.text for statement in new_statements),
                ],
            }
        )
    return update


def compute_report_quality(
    state: ResearchState,
    composition: ReportComposition,
) -> ReportQualitySnapshot:
    """The written report's own gate set (spec §6.4, PD-10).

    ``state`` supplies the plan's targets, the verified findings and the two
    published artifacts; ``composition`` supplies the sentences that reached
    the reader and the rows the Key Facts table prints. No report prose is
    parsed, and nothing here is a judgement of the writing: the Statement
    Check and the Report Reviewer judge sentences and evidence, and this
    function only reads what they recorded.

    A required target is missing when no verified finding answers it
    (``verified_facts.answered_target_ids``), and accounted for when the
    reader report lists it under Not found: §2.3 requires every remaining
    obligation to be disclosed, not to be met.
    """
    targets = [t for topic in state.sub_topics for t in topic.evidence_targets]
    required = [t.target_id for t in targets if t.required]
    answered = answered_target_ids(state.verified_findings, targets)
    missing = [t for t in required if t not in answered]
    listed = {row.target_id for row in composition.not_found}
    unaccounted = [t for t in missing if t not in listed]
    by_id = {finding_fingerprint(f): f for f in composition.findings}
    points = [*composition.summary, *(p for s in composition.sections for p in s.points)]
    uncited = sum(1 for p in points if p.statement is None or not p.statement.finding_ids)
    unresolved = sum(1 for p in points if p.statement is not None and (
        any(i not in by_id for i in p.statement.finding_ids) or not p.source_urls))
    # §6.4, D8: every kept sentence was judged by the Statement Check, or its
    # batch failure is recorded. A kept sentence with neither is the gate.
    failure_recorded = any(
        error.error_type in {"evidence_verifier_statement_check_failed", "report_writer_statement_check_failed"}
        for error in composition.errors
    )
    judged = {"consistent", "corrected"}
    unjudged = [
        p.statement.statement_id for p in points
        if p.statement is not None
        and (verdict := composition.statement_verdicts.get(p.statement.statement_id)) not in judged
        and not (verdict == "unchecked" and failure_recorded)
    ]
    rows = composition.fact_rows
    # Invariant (F11): fact_rows() already merges same-fact rows, so this guards
    # hand-built compositions and future producers; no test fixture is spent on it.
    duplicates = sum(1 for n, a in enumerate(rows) for b in rows[n + 1:]
                     if a.kind == b.kind and a.value == b.value and same_period(a.period, b.period)
                     and same_organisation(a.organisation, b.organisation))
    statuses = [f.verification for f in state.verified_findings if f.verification is not None]
    failures = [name for name, failed in (
        ("unresolved_citations", unresolved > 0), ("uncited_settled_points", uncited > 0),
        ("duplicate_fact_rows", duplicates > 0), ("missing_as_of", not composition.as_of),
        ("missing_scope", not composition.scope), ("unjudged_sentences", bool(unjudged)),
        ("unaccounted_required_targets", bool(unaccounted)),
        ("missing_reader_report", not state.report), ("missing_evidence_ledger", not state.report_evidence),
    ) if failed]
    return ReportQualitySnapshot(
        # The claim-era readings this function used to measure -- claimed
        # coverage, claim counts, source scoring -- have no gate and no
        # producer left (PD-10). Their fields stay required on the type until
        # Task 4.10 deletes them, so they are written as the empty value of
        # their own scale: an unmeasured zero, never a stale one.
        coverage_ratio=0.0,
        planned_topics=0,
        covered_topics=0,
        unique_findings=0,
        unique_sources=0,
        scored_cited_source_ratio=0.0,
        verified_claims=0,
        contradicted_claims=0,
        duplicate_claims=0,
        duplicate_source_rows=0,
        required_target_ids=required, answered_target_ids=sorted(answered),
        missing_required_target_ids=missing, unaccounted_target_ids=unaccounted,
        verified_findings=sum(1 for v in statuses if v.status == "verified"),
        corrected_findings=sum(1 for v in statuses if v.status == "verified_corrected"),
        dropped_findings=sum(1 for v in statuses if v.status == "dropped"),
        context_unchecked_findings=sum(1 for v in statuses if v.context_unchecked),
        dropped_figures=sum(1 for v in statuses for r in v.figure_results if not r.kept),
        cited_findings=len({i for p in points if p.statement for i in p.statement.finding_ids}),
        cited_sources=len({u for p in points for u in p.source_urls}),
        duplicate_fact_rows=duplicates, uncited_settled_points=uncited,
        unresolved_citations=unresolved, unjudged_sentences=unjudged,
        refused_sentences=len(composition.rejected_points), hard_failures=failures,
        forecasts_without_release=sum(1 for row in rows if row.kind == "forecast" and not row.release),
    )


def review_status_fields(
    review: ReportReview | None,
) -> dict[str, object]:
    """The semantic-review fields a quality snapshot records beside its own.

    Kept here rather than in the review module so the snapshot's vocabulary is
    written in one place. A ``None`` review is recorded as an empty status and
    no score — never as a zero, which would read as a judgement that the report
    scored nothing rather than that no judgement was made.
    """

    if review is None:
        return {
            "semantic_review_status": "",
            "semantic_review_score": None,
            "semantic_review_fingerprint": "",
        }
    return {
        "semantic_review_status": review.status,
        "semantic_review_score": review.mean_score,
        "semantic_review_fingerprint": review.input_fingerprint,
    }


__all__ = [
    "BROAD_PLAN_COVERAGE_THRESHOLD",
    "BROAD_PLAN_MIN_TOPICS",
    "apply_terminal_pursued_unmet_accounting",
    "compute_report_quality",
    "compute_substantive_coverage",
    "pursued_unmet_target_ids",
    "review_status_fields",
]
