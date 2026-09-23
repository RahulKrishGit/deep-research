"""The graph channel, the halt discipline, routing, and final status.

Pure: nothing here imports LangGraph, opens a span, or touches I/O, so the
two rules that matter most — "the iteration bound beats any model
judgement" and "a non-recoverable failure ends the run with its state
intact" — are testable without compiling a graph or standing up an agent.

The channel carries the whole ``ResearchState`` as one JSON-safe mapping
under the key ``state``. Merging happens inside each node through the
project's own ``merge_research_state`` rather than through per-field
LangGraph reducers: the append/replace rules and the "``iteration`` moves
only through ``advance_research_iteration``" guard already live there, and
a second copy expressed as channel annotations could drift from the first.

The dump is plain JSON, not a Pydantic object. LangGraph's checkpoint
serializer does handle Pydantic v2 models, but it revives them by importing
the class and warns that unregistered types will be blocked in a future
release. Primitives sidestep that entirely and keep checkpoints readable.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import TypedDict

from pydantic import JsonValue

from deep_research.agents.report_review import semantic_review_passes
from deep_research.utils.types import (
    GAP_MATERIAL_SEVERITIES,
    QUALITY_CONTRACT_VERSION,
    QUALITY_STATUS_ACCEPTED,
    QUALITY_STATUS_PARTIAL,
    AcquisitionState,
    Critique,
    CritiqueGap,
    MemorySnapshot,
    RepairStopReason,
    ReportReview,
    ResearchProgress,
    ResearchState,
    counted_evidence_targets,
    progress_improved,
    sub_topic_owes_evidence,
    target_is_answered,
    unanswered_required_targets,
)

GRAPH_SOURCE = "graph"

PLANNER_NODE = "planner"
RESEARCHER_NODE = "researcher"
SOURCE_EVALUATOR_NODE = "source_evaluator"
FACT_CHECKER_NODE = "fact_checker"
SYNTHESIZER_NODE = "synthesizer"
CRITIC_NODE = "critic"
REPORT_REVIEW_NODE = "report_review"
REFINE_NODE = "refine"
FINALIZE_NODE = "finalize_report"

# Execution order, with the terminal semantic review, the refinement hop, and
# the terminal publication step last. Node names deliberately equal agent names
# so a LangSmith trace reads the same as this tuple; ``report_review`` is the
# graph's own reviewer rather than one of the six agents, and
# ``finalize_report`` is the one node with no model call at all.
NODE_NAMES = (
    PLANNER_NODE,
    RESEARCHER_NODE,
    SOURCE_EVALUATOR_NODE,
    FACT_CHECKER_NODE,
    SYNTHESIZER_NODE,
    CRITIC_NODE,
    REPORT_REVIEW_NODE,
    REFINE_NODE,
    FINALIZE_NODE,
)

ROUTE_REFINE = "refine"
ROUTE_FINALIZE = "finalize"
ROUTE_END = "end"

# Enumerated, project-generated routing reasons. Never provider text: these
# reach ResearchEvent.metadata and the session span's outputs.
GRAPH_ROUTES = {
    "refinement_requested": (
        "The critic asked for another research pass and budget remains."
    ),
    "quality_gate_failed": (
        "The deterministic quality gates found a hard failure and budget "
        "remains, so the report was sent back for one more research pass."
    ),
    "critique_satisfied": "The critic accepted the report.",
    "critique_failed": (
        "The critic's review never validated, so this report was never judged."
    ),
    "max_iterations_reached": (
        "The refinement budget is exhausted; this is the final report."
    ),
    "no_progress": (
        "The last repair changed nothing substantive, so another pass would "
        "repeat it; this is the final report."
    ),
    "pending_capacity": (
        "Evidence this run already deferred is still unprocessed and the "
        "repair budget cannot reach it; the deferral is recorded, not lost."
    ),
    "evidence_unavailable": (
        "Every route to the evidence a repair still owes was tried and none "
        "supplied it; the obligation stays open and is reported as open."
    ),
    "provider_failure": (
        "A repair pass ended on a model-provider failure rather than a "
        "result, so the repair did not happen."
    ),
    "missing_critique": (
        "No critique was recorded, so no refinement can be justified."
    ),
    "semantic_review_gap": (
        "The terminal semantic review did not accept the report: it found a "
        "material defect, or its mean over the seven dimensions is below the "
        "acceptance threshold."
    ),
    "halted": "The run stopped on a non-recoverable error.",
}

GRAPH_STATUSES = ("completed", "max_iterations", "incomplete", "failed")

_STATUS_BY_ROUTE_REASON = {
    "critique_satisfied": "completed",
    "critique_failed": "failed",
    "max_iterations_reached": "max_iterations",
    "missing_critique": "incomplete",
    "refinement_requested": "incomplete",
    "quality_gate_failed": "incomplete",
    # A semantic review that judged the report and did not accept it is a
    # verdict about the answer, and a report the gates would publish as
    # "completed" is not one. The run publishes it honestly as incomplete with
    # `partial` quality — never as `failed`, which belongs to a run that could
    # not finish, and never as `completed`, which would claim the gates cleared
    # a report the reviewer refused.
    "semantic_review_gap": "incomplete",
    # A repair stop is a statement about the machine, never about the report:
    # the report was reviewed, and the loop stopped because repeating it would
    # change nothing, because deferred evidence could not fit, because the
    # evidence does not exist, or because the provider failed. None of those is
    # a quality verdict, and none may read as ``failed`` — that status belongs
    # to ``critique_failed``, where no review ever happened at all.
    "no_progress": "incomplete",
    "pending_capacity": "incomplete",
    "evidence_unavailable": "incomplete",
    "provider_failure": "incomplete",
    "halted": "failed",
}

# The error types that stop a run. Deliberately *not* "any error with
# recoverable=False": agents already record non-recoverable provider
# failures (critic_review_provider_error, for one) that a research pass is
# expected to survive, and halting on those would end a run over one blip.
HALTING_ERROR_TYPES = frozenset(
    {
        "graph_agent_configuration_error",
        "graph_planning_failed",
        "graph_provider_configuration_error",
        "graph_invalid_agent_state",
        "graph_invalid_route",
        "graph_request_attempt_limit_exceeded",
    }
)

DEFAULT_MAX_ITERATIONS = 3

# Head-room over the supersteps a full budget needs, for the START edge and
# LangGraph's own bookkeeping steps.
_RECURSION_MARGIN = 10


class ResearchGraphState(TypedDict):
    """The whole LangGraph channel: one JSON-safe ``ResearchState`` dump."""

    state: dict[str, JsonValue]


def dump_state(state: ResearchState) -> ResearchGraphState:
    """Render one research state as the channel a node returns.

    Acquisition queues, candidate records, read registries, and pending
    passage IDs are deliberately part of this single JSON snapshot. Keeping
    the check here prevents a future channel serializer from silently
    dropping the state that makes a resumed run auditable.
    """
    serialized = state.model_dump(mode="json")
    if "acquisition_state_by_target" not in serialized:
        raise ValueError("research state must persist acquisition state")
    return {"state": serialized}


def load_state(channel: ResearchGraphState) -> ResearchState:
    """Validate the channel back into a research state.

    Validation is not ceremony: it is what makes a checkpoint written by an
    older build fail loudly here rather than silently half-populate a node.
    """
    payload = channel["state"]
    # Older checkpoints predate the acquisition registry; loading them keeps
    # the honest empty default rather than synthesizing evidence or rejecting
    # an otherwise valid legacy snapshot.
    if "acquisition_state_by_target" not in payload:
        payload = {**payload, "acquisition_state_by_target": {}}
    return ResearchState.model_validate(payload)


def initial_graph_state(
    *,
    session_id: str,
    question: str,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    memory_context: MemorySnapshot | None = None,
) -> ResearchGraphState:
    """Build the channel one research session starts from.

    ``memory_context`` is supplied by the caller. The graph performs no
    recall of its own: that touches ChromaDB and an embedding provider,
    which orchestration has no business owning.

    A new run stamps the current evidence contract. The read and evidence
    registries start empty on purpose: this session has read nothing yet, and
    a snapshot loaded without the stamp keeps the legacy version rather than
    claiming provenance it cannot prove.
    """
    return dump_state(
        ResearchState(
            session_id=session_id,
            original_question=question,
            max_iterations=max_iterations,
            memory_context=memory_context or MemorySnapshot(),
            quality_contract_version=QUALITY_CONTRACT_VERSION,
        )
    )


def is_halted(state: ResearchState) -> bool:
    """True when an enumerated graph failure has ended this run."""
    return any(
        error.error_type in HALTING_ERROR_TYPES for error in state.errors
    )


def graph_route(state: ResearchState) -> tuple[str, str]:
    """Decide where the graph goes after the Critic, and why.

    Pure, so the conditional edge, the recorded route event, the final status,
    and the terminal quality status all read the same decision.
    ``Critique.should_continue`` is the critic's recommendation and the
    deterministic quality gate is the graph's own verdict; the iteration bound
    is the graph's law and is checked here regardless of what either said.

    There are three destinations. ``ROUTE_REFINE`` buys another research pass.
    ``ROUTE_FINALIZE`` publishes and stops — the Critic's acceptance when the
    gates agree, or the best report a spent budget allows. ``ROUTE_END`` skips
    publication entirely, and is reached only by a halted run: a failed run
    publishes nothing rather than a stale earlier pass's artifact.

    A critique that was never *made* cannot satisfy anything. A failed review
    carries ``should_continue=False`` for the same reason a provider outage
    does — it says nothing about the report, so it buys no research cycle — and
    that is exactly why ``review_status`` is consulted here: reading only
    ``should_continue`` would publish an unreviewed report as accepted. The
    reason names the real cause and beats the iteration bound, because "the
    budget ran out" would describe a report that was in fact never judged.

    The terminal semantic review is consulted for one thing only, and
    deliberately: a **scored** review that did not accept the report consumes
    the refinement opportunity the Critic's silence would otherwise leave
    unspent, because "the report is not good enough" is a defect with somewhere
    to go. A review that was never made — missing, incomplete, or failed on the
    provider — does not buy a pass: it says nothing about the report, and
    another research cycle would not produce a judgement. It blocks acceptance
    through ``graph_quality_status`` instead, which is the honest place for
    "no verdict".
    """
    if is_halted(state):
        return ROUTE_END, "halted"
    critique = state.critique
    if critique is None:
        return ROUTE_FINALIZE, "missing_critique"
    if critique.review_status == "failed":
        return ROUTE_FINALIZE, "critique_failed"
    rejected = semantic_review_rejects(state)
    if state.iteration >= state.max_iterations:
        return (
            ROUTE_FINALIZE,
            "semantic_review_gap" if rejected else "max_iterations_reached",
        )
    stopped = state.repair_stop_reason
    if stopped is not None and stopped != "max_iterations" and _wants_another_pass(
        state, critique
    ):
        # Another pass is wanted — by the Critic, by the deterministic gate, or
        # by the semantic review — and the last repair loop already established
        # that buying one would repeat work. The budget is spent on the stall's
        # own terms rather than on another identical pass, and the reason names
        # which stall it was. Checked *after* the iteration bound so "the budget
        # ran out" keeps naming the ceiling, and after a failed review so a
        # review that never happened is never reported as a stalled repair.
        return ROUTE_FINALIZE, stopped
    if critique.should_continue:
        return ROUTE_REFINE, "refinement_requested"
    if state.quality is not None and state.quality.hard_failures:
        # A model score cannot override a deterministic hard failure: while
        # budget remains, the gate sends the report back for another pass.
        return ROUTE_REFINE, "quality_gate_failed"
    if rejected:
        return ROUTE_REFINE, "semantic_review_gap"
    return ROUTE_FINALIZE, "critique_satisfied"


def _wants_another_pass(state: ResearchState, critique: Critique) -> bool:
    """True when something other than the budget is asking for another pass."""
    if critique.should_continue:
        return True
    if state.quality is not None and bool(state.quality.hard_failures):
        return True
    return semantic_review_rejects(state)


def semantic_review_rejects(state: ResearchState) -> bool:
    """True when a *scored* semantic review judged the report and refused it.

    Only a scored review can reject: `incomplete` and `provider_failed` are
    the absence of a judgement, and treating an absent judgement as a
    rejection would let a provider outage buy research passes and then report
    the report as reviewed-and-refused. A missing review is therefore a
    non-acceptance that routes nowhere, which is what
    ``graph_quality_status`` exists to express.
    """
    review = state.report_review
    return review is not None and review.status == "scored" and not (
        semantic_review_passes(review)
    )


def repair_is_terminal(state: ResearchState) -> bool:
    """True when the recorded repair stop ends the loop instead of buying a pass.

    Read by the refinement hop's own edge, so the run stops *before* spending
    the pass a stall would only repeat — acting on it after the next Critic
    would mean the second unchanged pass had already been paid for.

    ``max_iterations`` is not terminal here: that reason says the loop is
    working and the budget is what stops it, and the ceiling has its own
    route reason once the final pass has run. A critique that is missing or
    that never validated is not terminal either — the stall is not what ended
    those runs, and reporting it as the cause would misname them.
    """
    stopped = state.repair_stop_reason
    if stopped is None or stopped == "max_iterations":
        return False
    critique = state.critique
    if critique is None or critique.review_status == "failed":
        return False
    return _wants_another_pass(state, critique)


def graph_status(state: ResearchState) -> str:
    """Name how this run ended, from the same decision the router used."""
    return _STATUS_BY_ROUTE_REASON[graph_route(state)[1]]


def graph_quality_status(state: ResearchState) -> str:
    """The terminal quality status the finalizer stamps on both artifacts.

    Read from the same pure decision the router used, so the status a reader
    sees and the edge the graph took cannot disagree. Only a report the gates
    cleared *and* the Critic accepted *and* the terminal semantic review scored
    at or above the threshold is ``accepted``: ``critique_satisfied`` is itself
    reachable only once the gates found no hard failure while budget remained,
    so a run that exhausted its budget with failures ends ``partial``. A run no
    quality pass ever judged is ``partial`` too — nothing unjudged may be
    called accepted, and the finalizer saves no claim to memory for a partial
    run.

    A failed review is the case this distinction exists for: its score is the
    floor and its gap list is empty, so every other signal it carries reads
    like a clean acceptance. ``critique_failed`` never reaches
    ``critique_satisfied``, so an unreviewed report is ``partial`` — visible as
    unpublished-to-memory and not-accepted rather than indistinguishable from a
    report the Critic actually cleared.

    Task 10 adds the second reviewer, and it is checked here rather than only
    in the route: a report whose semantic review is missing, incomplete, or
    provider-failed is *never* accepted, and that is true no matter which edge
    the graph took. Missing reviewer support must not silently revert strict
    acceptance to critic-only, and the only way to guarantee that is to make
    acceptance a property of the review rather than of the route.
    """
    if state.quality is None:
        return QUALITY_STATUS_PARTIAL
    if graph_route(state)[1] != "critique_satisfied":
        return QUALITY_STATUS_PARTIAL
    return (
        QUALITY_STATUS_ACCEPTED
        if semantic_review_passes(state.report_review)
        else QUALITY_STATUS_PARTIAL
    )


# --- Task 9: repair progress and repair stop reasons ------------------------


def _material_gap_ids(gaps: Sequence[CritiqueGap]) -> list[str]:
    """The stable identity of every open material defect, sorted.

    A ``gap_id`` is positional *within one review* (``gap-01``, ``gap-02``), so
    two reviews' ids cannot be compared: the same defect that was ``gap-02``
    last pass can be ``gap-01`` now. The identity used here is the action plus
    the scope, which is what makes "this defect is still open" answerable
    across passes — and it is the same identity routing folds jobs by, so a
    defect cannot be open for the router and closed for the progress check.
    """
    keys = {
        "|".join(
            (
                gap.repair_action,
                gap.coverage_id or "",
                ",".join(sorted(gap.target_ids)),
                ",".join(sorted(gap.statement_ids)),
                ",".join(sorted(gap.claim_cluster_ids)),
            )
        )
        for gap in gaps
        if gap.severity in GAP_MATERIAL_SEVERITIES
    }
    return sorted(keys)


def open_material_gap_ids(critique: Critique | None) -> list[str]:
    """Every open material defect the Critic named, by stable identity."""
    if critique is None or critique.review_status != "reviewed":
        return []
    return _material_gap_ids(critique.gaps)


def open_review_defect_ids(review: ReportReview | None) -> list[str]:
    """Every open material defect the semantic review named.

    Read from a *scored* review only. An incomplete or provider-failed review
    judged nothing, so its empty defect list is an absence of findings rather
    than a finding of none — and reporting its defects as "resolved" next pass
    would credit the run with closing defects nobody ever raised.
    """
    if review is None or review.status != "scored":
        return []
    return _material_gap_ids(review.defects)


def _selectable_acquisition_keys(state: ResearchState) -> set[str]:
    """The acquisition keys a refinement pass will actually work on.

    Two things make a topic's queue spendable work: the plan still owes
    evidence for it, so ``select_sub_topics`` hands it to the Researcher, or a
    typed ``acquire`` job names it, which the refinement hop routes to the
    Researcher as an errand in its own right. Everything else in
    ``acquisition_state_by_target`` belongs to a sub-topic this pass skips —
    an answered topic keeps whatever search candidates the last pass left
    queued, and nothing will ever drain them — so counting those leftovers as
    work owed is what kept a stalled run paying for passes it could not use.
    """
    keys = {
        sub_topic.coverage_id
        for sub_topic in state.sub_topics
        if sub_topic_owes_evidence(state, sub_topic)
    }
    keys.update(
        job.coverage_id
        for job in state.refinement_targets
        if job.action == "acquire" and job.coverage_id
    )
    return keys


def _unattempted_repair_keys(state: ResearchState) -> set[str]:
    """Routed repair errands the run never opened an acquisition for.

    A typed ``acquire`` job a *review* or the Critic asked for is an errand the
    hop has dispatched, so the stop decision must see it even when no
    acquisition state exists yet for that topic — the key is the record of an
    attempt, and its absence says the work has not started. Judged without it,
    a run whose every *attempted* lead was spent published with the defect it
    had just routed still open.

    The mechanical ``unanswered_target`` jobs are deliberately excluded, and
    that exclusion is the whole point of computing this from the routed list
    rather than from ``_selectable_acquisition_keys``: that obligation is the
    standing one ``no_progress`` is allowed to stop on, so counting an
    never-attempted unmet topic as pending work would make the no-progress
    rule unreachable — a run that changed nothing would keep buying passes.
    """
    return {
        job.coverage_id
        for job in state.refinement_targets
        if job.action == "acquire"
        and job.origin in ("critic_gap", "review_defect")
        and job.coverage_id
    }.difference(state.acquisition_state_by_target)


def pending_repair_work(state: ResearchState) -> list[str]:
    """Every piece of evidence work this run still holds unprocessed.

    Per target and in acquisition order: a passage batch still owed, an
    extraction that was handed over but not consumed, a queued candidate, and
    a deferred candidate record. Deferred is the one that matters most — a
    deferral is a decision to do the work later, and a run that reports
    ``no_progress`` while holding one has relabelled a capacity limit as a
    dead end.

    Only the targets a refinement pass will actually select are read
    (``_selectable_acquisition_keys``). A queue on a topic the pass skips is
    not work this run can spend: the Researcher never revisits that topic, so
    the leftover can never be drained, and counting it held the stop reason
    open — the run bought every remaining macro pass and ended at the
    iteration ceiling rather than reporting the dead end it had established.

    A routed repair errand whose target has *no* acquisition state is owed
    work too, and is reported as such (``_unattempted_repair_keys``). Counting
    only the keys the map happens to hold let the stop decision call a run
    finished while the acquire job the hop had just routed for such a topic was
    still open, which is the one shape the routed worklist exists to keep
    alive.
    """
    items: list[str] = []
    selectable = _selectable_acquisition_keys(state)
    for target_id, acquisition in state.acquisition_state_by_target.items():
        if target_id not in selectable:
            continue
        items.extend(
            f"{target_id}:passage:{item}"
            for item in acquisition.pending_passage_ids
        )
        items.extend(
            f"{target_id}:extraction:{item}"
            for item in acquisition.pending_extraction_ids
        )
        items.extend(
            f"{target_id}:candidate:{url}" for url in acquisition.candidate_urls
        )
        items.extend(
            f"{target_id}:deferred:{url}"
            for url, record in acquisition.candidate_records.items()
            if record.status in ("queued", "deferred")
        )
    items.extend(
        f"{key}:unattempted" for key in sorted(_unattempted_repair_keys(state))
    )
    return list(dict.fromkeys(items))


def composition_fingerprint(state: ResearchState) -> str:
    """A stable fingerprint of the *structure* of the report this pass produced.

    The substantive reader statements and the shape of each one — its mode, the
    targets and dimensions it answers, and the clusters and evidence behind it.
    Deliberately not the rendered prose: a real synthesizer re-words every
    pass, so hashing the text made every pass look like progress and left
    ``no_progress`` reachable only against byte-identical fakes. What is
    structural is what a repair changes — a duplicated paragraph is the same
    key twice (the keys are a list, never a set), while restating one fact in
    new words is not.
    """
    statements = state.composition.statements if state.composition else []
    keys = sorted(
        json.dumps(
            {
                "mode": statement.mode,
                "targets": sorted(statement.target_ids),
                "dimensions": sorted(statement.answered_dimensions),
                "clusters": sorted(statement.claim_cluster_ids),
                "evidence": sorted(statement.evidence_ids),
            },
            sort_keys=True,
        )
        for statement in statements
        if statement.substantive
    )
    encoded = json.dumps(keys)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def progress_snapshot(
    state: ResearchState,
    *,
    previous: ResearchProgress | None = None,
) -> ResearchProgress:
    """What one completed pass substantively changed, as of now.

    ``completed_target_ids`` is the strict Section 2.3 reading — a reader
    statement that satisfies the target's dimensions and policy — so an added
    plan obligation is not progress until its evidence exists. Assessed support
    is fingerprinted per *assessed source* and per checked claim: a source's
    identity (publisher, work, assessment revision, temporal status) and a
    claim's badge are what a repair changes, while a re-scored source is the
    same support at a new number and is deliberately not progress.
    ``resolved_gap_ids`` is read against the previous snapshot's open defects,
    because a defect that is gone from this review is what "resolved" means.
    """
    completed = [
        target.target_id
        for topic in state.sub_topics
        for target in counted_evidence_targets(topic.evidence_targets)
        if target_is_answered(state, target)
    ]
    unresolved = [
        *open_material_gap_ids(state.critique),
        *open_review_defect_ids(state.report_review),
    ]
    resolved = (
        []
        if previous is None
        else sorted(set(previous.unresolved_major_gap_ids).difference(unresolved))
    )
    return ResearchProgress(
        completed_target_ids=completed,
        assessed_support_fingerprints=[
            *(
                "source:"
                + "|".join(
                    (
                        source.url,
                        source.publisher_id or "",
                        source.work_id or "",
                        source.assessment_revision,
                        source.temporal.status,
                    )
                )
                for source in state.evaluated_sources
            ),
            *(
                "claim:"
                + "|".join(
                    (claim.claim_id, claim.evidence_status or claim.verdict)
                )
                for claim in state.verified_claims
            ),
        ],
        resolved_gap_ids=resolved,
        pending_work_ids=pending_repair_work(state),
        unresolved_major_gap_ids=unresolved,
        composition_fingerprint=composition_fingerprint(state),
        error_count=len(state.errors),
    )


def repair_capacity_spent(state: ResearchState) -> bool:
    """True when no further acquisition can be bought for the work still owed.

    The macro-iteration ceiling for the next pass, or every *selectable*
    target's own acquisition state reporting no remaining calls. A run that
    still holds deferred evidence and still has calls to spend has not run out
    of capacity — it has simply not spent it yet.

    The targets read are the ones a refinement pass will work on
    (``_selectable_acquisition_keys``), not every entry the run ever wrote: a
    topic the pass skips can hold no calls and no queue and neither fact says
    anything about what this run could still buy. A routed repair errand with
    no acquisition state at all has spent nothing
    (``_unattempted_repair_keys``), so a run is never reported as having spent
    capacity for work it never opened an acquisition with.

    The ceiling is read as "no pass can be opened", not "the next pass is the
    last": the refinement hop can open pass ``iteration + 1`` whenever
    ``iteration < max_iterations``, so declaring capacity spent at
    ``iteration + 1 >= max_iterations`` stopped a run one pass early while a
    deferred candidate and a pass were both still available.
    """
    if state.iteration >= state.max_iterations:
        return True
    selectable = _selectable_acquisition_keys(state)
    if not selectable or _unattempted_repair_keys(state):
        return False
    return all(
        state.acquisition_state_by_target[key].remaining_calls <= 0
        for key in selectable
    )


def _leads_exhausted(acquisition: AcquisitionState) -> bool:
    """True when one target's acquisition has nothing left to try."""
    if (
        acquisition.candidate_urls
        or acquisition.pending_passage_ids
        or acquisition.pending_extraction_ids
    ):
        return False
    return acquisition.empty_searches >= 2 or bool(acquisition.denied_urls)


def evidence_exhausted(state: ResearchState) -> bool:
    """True when every outstanding obligation is out of leads.

    Requires an acquisition attempt for every unanswered required target: a
    target nobody has tried to acquire for yet is not evidence anyone failed
    to find, and calling that "unavailable" would report a dead end where the
    truth is that the work has not started.

    The attempt is read under ``EvidenceTarget.coverage_id`` — the key the
    Researcher writes ``acquisition_state_by_target`` under, because one
    acquisition loop runs per sub-topic — and never under ``target_id``, which
    is namespaced *inside* that coverage id (``topic-01-target-01``). Looking
    it up by target id found no attempt in any production run, so this
    function was constantly False and a target whose every lead was spent was
    reported as a stall instead of as the dead end the run had established.
    """
    outstanding = unanswered_required_targets(state)
    if not outstanding:
        return False
    attempts = [
        state.acquisition_state_by_target[target.coverage_id]
        for target in outstanding
        if target.coverage_id in state.acquisition_state_by_target
    ]
    if len(attempts) != len(outstanding):
        return False
    return all(_leads_exhausted(attempt) for attempt in attempts)


def provider_failed(state: ResearchState, *, since: int = 0) -> bool:
    """True when a non-recoverable provider error ended the pass just finished.

    Read from the recorded errors rather than guessed from a missing result: a
    provider outage and a pass that simply found nothing are different facts,
    and only the first may be reported as ``provider_failure``.

    ``since`` is the error count the previous snapshot recorded, so only the
    errors of the pass being judged are read. Reading the whole list let one
    old outage outrank pending deferred work with capacity still available —
    the run stopped on a failure that had already been survived instead of
    processing the evidence it was holding.
    """
    return any(
        not error.recoverable and "provider" in error.error_type
        for error in state.errors[since:]
    )


def repair_stop_reason(
    state: ResearchState,
    *,
    before: ResearchProgress,
    after: ResearchProgress,
) -> RepairStopReason | None:
    """Why the repair loop stopped, or ``None`` while it should continue.

    Evaluated after a *whole* repair job has finished — the plan extension and
    the acquisition it triggered are one job, so "a target was added" is never
    measured as progress on its own. The order is the order of certainty:

    * progress means the loop is working; only the budget can stop it, and the
      reason then names the budget — ``max_iterations`` means the pass this hop
      is opening is the run's last, which is informational and never terminal,
      because ``graph_route`` reports the ceiling itself once that pass has run;
    * a provider outage *in the pass just finished* is a fact about the
      machine, and is reported as one rather than as a stall. An outage the run
      already survived is not re-read;
    * work this run deferred is *owed*, so it continues unless capacity is
      genuinely spent, and then says so — ``pending_capacity``, never
      ``no_progress``;
    * leads that were all tried and all failed are ``evidence_unavailable``,
      which is a real answer about the world rather than a stall;
    * anything else that changed nothing is ``no_progress``.
    """
    if progress_improved(before, after):
        return (
            "max_iterations"
            if state.iteration + 1 >= state.max_iterations
            else None
        )
    if provider_failed(state, since=before.error_count):
        return "provider_failure"
    if after.pending_work_ids:
        return "pending_capacity" if repair_capacity_spent(state) else None
    if evidence_exhausted(state):
        return "evidence_unavailable"
    return "no_progress"


def graph_recursion_limit(max_iterations: int) -> int:
    """Bound LangGraph's supersteps from the graph's real shape.

    Always passed explicitly. LangGraph 1.2 defaults this generously, but
    earlier releases defaulted to 25 — under what four macro passes over
    seven nodes need — and an explicit value documents the shape.
    """
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")
    return (max_iterations + 1) * len(NODE_NAMES) + _RECURSION_MARGIN
