"""The graph channel, the halt discipline, routing, and final status.

Pure: nothing here imports LangGraph, opens a span, or touches I/O, so the
two rules that matter most — "the extra-pass ceiling beats any model
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

from collections.abc import Sequence
from typing import TypedDict

from pydantic import JsonValue

from deep_research.agents.reader_notes import has_steering_kind, is_research_note
from deep_research.agents.report_reviewer import semantic_review_passes
from deep_research.utils.types import (
    MAX_NOTES_PER_RUN,
    NOTE_COVERAGE_PREFIX,
    QUALITY_CONTRACT_VERSION,
    QUALITY_STATUS_ACCEPTED,
    QUALITY_STATUS_PARTIAL,
    MemorySnapshot,
    ReaderAnswer,
    ReaderNote,
    ResearchState,
    active_reader_notes,
)

GRAPH_SOURCE = "graph"

PLANNER_NODE = "planner"
RESEARCHER_NODE = "researcher"
SOURCE_EVALUATOR_NODE = "source_evaluator"
EVIDENCE_VERIFIER_NODE = "evidence_verifier"
REPORT_WRITER_NODE = "report_writer"
REPORT_REVIEWER_NODE = "report_reviewer"
NOTE_PASS_NODE = "note_pass"
EXTRA_PASS_NODE = "extra_pass"
REDRAFT_NODE = "writer_redraft"
FINALIZE_NODE = "finalize_report"

# Execution order, with the terminal review, the three one-hop continuations,
# and the terminal publication step last. Node names deliberately equal agent
# names so a LangSmith trace reads the same as this tuple; ``report_reviewer``
# is the graph's own reviewer rather than one of the five agents,
# ``note_pass`` is the hop that opens the reader's notes' targeted research
# pass (live-briefs spec §4.6), ``extra_pass`` is the hop that carries the
# iteration increment,
# ``writer_redraft`` is the hop that hands the reviewer's defects back to the
# writer, and ``finalize_report`` is the one node with no model call at all.
NODE_NAMES = (
    PLANNER_NODE,
    RESEARCHER_NODE,
    SOURCE_EVALUATOR_NODE,
    EVIDENCE_VERIFIER_NODE,
    REPORT_WRITER_NODE,
    REPORT_REVIEWER_NODE,
    NOTE_PASS_NODE,
    EXTRA_PASS_NODE,
    REDRAFT_NODE,
    FINALIZE_NODE,
)

# How many writer re-runs a scored review with a material defect may buy. One:
# the defect list is addressed to the writer, and a reviewer that keeps naming
# defects cannot be allowed to loop the writer — the run publishes as not
# accepted after the single re-run.
MAX_WRITER_REDRAFTS = 1

# The supersteps one writer re-run takes: the redraft hop, the writer, the
# reviewer. A reader note buys at most one such re-run and one targeted pass
# (live-briefs spec §4.6), which is what the recursion limit allows for.
NOTE_REDRAFT_STEPS = 3

ROUTE_NOTE_PASS = "note_pass"
ROUTE_EXTRA_PASS = "extra_pass"
ROUTE_REDRAFT = "redraft"
ROUTE_FINALIZE = "finalize"
ROUTE_END = "end"

# Enumerated, project-generated routing reasons. Never provider text: these
# reach ResearchEvent.metadata and the session span's outputs.
GRAPH_ROUTES = {
    "report_accepted": (
        "The Report Reviewer accepted the report and no gate failed; any "
        "required target with no verified finding is listed under Not found."
    ),
    "report_not_accepted": (
        "The report was scored but not accepted (a gate failed, a material "
        "defect, or a mean below 0.80), and no required target is missing."
    ),
    "review_unavailable": (
        "The Report Reviewer did not score the report (provider failure or an "
        "invalid reply); it is published as partial."
    ),
    "extra_pass_requested": (
        "Required targets have no verified finding, or the terminal review's "
        "own coverage defect names one as still missing, and an extra pass "
        "remains; the researcher runs for those targets only."
    ),
    "extra_passes_exhausted": (
        "Required targets are still missing a verified finding, or the "
        "terminal review's own coverage defect still names one, no extra "
        "pass remains, and the report was not accepted; a target with no "
        "verified finding is listed under Not found, and one the review's "
        "own defect names keeps that defect in the record."
    ),
    "redraft_requested": (
        "The Report Reviewer scored the report and named a material defect, "
        "and the one writer re-run a defect list buys has not been spent; the "
        "writer drafts again with those defects fed back."
    ),
    "note_pass_requested": (
        "A reader note owes its one targeted research pass: a research note "
        "with no researched topic of its own, whatever the review said, or a "
        "steering note the review found no evidence for; the researcher runs "
        "for the notes that owe one, outside the extra-pass budget."
    ),
    "note_redraft_requested": (
        "The review found a reader note's steering ignored although its "
        "findings bear on it, or a note with a steering kind arrived after the "
        "review input was built, and that note has not had its one redraft; "
        "the writer drafts again with the reader's notes."
    ),
    "halted": "The run stopped on a non-recoverable error.",
}

GRAPH_STATUSES = ("completed", "max_iterations", "incomplete", "failed")

_STATUS_BY_ROUTE_REASON = {
    "report_accepted": "completed",
    # A report the reviewer judged and did not accept is a verdict about the
    # answer, and it publishes as ``incomplete`` with ``partial`` quality —
    # never as ``failed``, which belongs to a run that could not finish.
    "report_not_accepted": "incomplete",
    # No judgement of the report exists at all. That is not a defect in the
    # report and not a failed run: the artifacts publish, honestly partial.
    "review_unavailable": "incomplete",
    "extra_pass_requested": "incomplete",
    # PD-23: the ceiling was spent and the required targets are still missing,
    # so the run ends on its budget rather than on an acceptance. ``iteration``
    # is this status's own vocabulary and keeps its name in the CLI and the
    # API (PD-11).
    "extra_passes_exhausted": "max_iterations",
    # The run is continuing, not ending: the writer has been asked for one
    # more draft, so this is the same "not accepted yet" reading an extra pass
    # carries.
    "redraft_requested": "incomplete",
    # Both note routes continue the run (live-briefs spec §4.6), so they read
    # as the loops above do; neither is ever a run's last decision.
    "note_pass_requested": "incomplete",
    "note_redraft_requested": "incomplete",
    "halted": "failed",
}

# The error types that stop a run. Deliberately *not* "any error with
# recoverable=False": agents already record non-recoverable provider failures
# (a failed Context Check batch, for one) that a research pass is expected to
# survive, and halting on those would end a run over one blip.
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

DEFAULT_MAX_EXTRA_PASSES = 1

# Head-room over the supersteps a full budget needs, for the START edge and
# LangGraph's own bookkeeping steps.
_RECURSION_MARGIN = 10


class ResearchGraphState(TypedDict):
    """The whole LangGraph channel: one JSON-safe ``ResearchState`` dump."""

    state: dict[str, JsonValue]


def dump_state(state: ResearchState) -> ResearchGraphState:
    """Render one research state as the channel a node returns.

    Acquisition queues, candidate records, read registries, and pending
    passage IDs are deliberately part of this single JSON snapshot: the whole
    state is dumped, so a serializer cannot silently drop the records that make
    a resumed run auditable, and ``load_state`` gets them all back.
    """
    return {"state": state.model_dump(mode="json")}


def load_state(channel: ResearchGraphState) -> ResearchState:
    """Validate the channel back into a research state.

    Validation is not ceremony: it is what makes a checkpoint written by an
    older build fail loudly here rather than silently half-populate a node. A
    payload missing a field this build defaults is read with that default —
    the honest empty registry rather than a synthesized one — and a payload
    carrying a field this build does not define is refused by the contract
    model's ``extra='forbid'``.
    """
    return ResearchState.model_validate(channel["state"])


def initial_graph_state(
    *,
    session_id: str,
    question: str,
    max_extra_passes: int = DEFAULT_MAX_EXTRA_PASSES,
    memory_context: MemorySnapshot | None = None,
    reader_answers: Sequence[ReaderAnswer] = (),
) -> ResearchGraphState:
    """Build the channel one research session starts from.

    ``memory_context`` is supplied by the caller. The graph performs no
    recall of its own: that touches ChromaDB and an embedding provider,
    which orchestration has no business owning. ``reader_answers`` are the
    reader's answers to the one-time check (live-briefs spec §4.4), empty
    when nothing was asked.

    A new run stamps the current evidence contract. The read and evidence
    registries start empty on purpose: this session has read nothing yet, and
    a snapshot loaded without the stamp keeps the legacy version rather than
    asserting provenance it cannot prove.
    """
    return dump_state(
        ResearchState(
            session_id=session_id,
            original_question=question,
            max_extra_passes=max_extra_passes,
            memory_context=memory_context or MemorySnapshot(),
            reader_answers=list(reader_answers),
            quality_contract_version=QUALITY_CONTRACT_VERSION,
        )
    )


def is_halted(state: ResearchState) -> bool:
    """True when an enumerated graph failure has ended this run."""
    return any(
        error.error_type in HALTING_ERROR_TYPES for error in state.errors
    )


def extra_pass_target_ids(state: ResearchState) -> list[str]:
    """The obligations the next extra pass exists for (D10, spec §6.5).

    ``missing_required_target_ids`` is the code-stamped gate: a required
    target with no verified finding at all. A reviewer's own ``coverage``
    defect can name a required target that gate already counts as answered,
    when the answer it found does not actually settle the question — that
    target owes the pass too, *unless* a scoped re-review has since marked
    that same defect resolved (T5 addendum item 4): ``defect.material``
    already excludes it from ``ReportReview.material_defects`` and
    ``semantic_review_passes``, and this reads the same flag so a target the
    redraft already answered cannot still buy another pass just because the
    merged record keeps the old, now-resolved defect for its own history.
    The two lists are combined, in order, with duplicates dropped, so
    ``graph_route``'s decision and the pass's own job list can never
    diverge: whichever one is asked "is anything missing?" or "for what?",
    both read the same targets.

    A reader note's own targets (``note-…``, live-briefs spec §4.6) are never
    part of it: a note buys its one targeted pass through the note route,
    outside the extra-pass budget (D11), so a note target still missing after
    its pass neither buys an extra pass nor ends the run as exhausted.
    """
    review = state.report_review
    if review is None:
        return []
    required = set(state.quality.required_target_ids) if state.quality else set()
    coverage_target_ids = [
        target_id
        for defect in review.defects
        if defect.kind == "coverage" and defect.material
        for target_id in defect.target_ids
        if target_id in required
    ]
    return [
        target_id
        for target_id in dict.fromkeys(
            [*review.missing_required_target_ids, *coverage_target_ids]
        )
        if not target_id.startswith(NOTE_COVERAGE_PREFIX)
    ]


def note_dispositions(state: ResearchState) -> dict[str, str]:
    """The latest review's verdict on each reader note it judged, by note id."""
    review = state.report_review
    if review is None:
        return {}
    return {entry.note_id: entry.status for entry in review.note_dispositions}


def researched_note_topic_ids(state: ResearchState) -> set[str]:
    """The reader-note topics (``note-…``) that have done their research (spec §5.1).

    A topic is researched when its latest ``researcher.sub_topic.completed``
    event ended with any stop reason but ``provider_error``, or when a
    verified finding answers one of its targets (``state.quality``; no quality
    snapshot counts as none answered). A topic with no completed event — one
    ``stop`` left unstarted — is not researched. So a note whose thread failed,
    or never started, still owes its one note pass, unless a finding already
    answered it (D31; review I5; review 2, I-1).
    """
    latest: dict[str, str] = {}
    for event in state.events:
        if event.event_type != "researcher.sub_topic.completed":
            continue
        coverage_id = event.metadata.get("coverage_id")
        stop_reason = event.metadata.get("stop_reason")
        if isinstance(coverage_id, str) and isinstance(stop_reason, str):
            latest[coverage_id] = stop_reason
    answered = (
        set(state.quality.answered_target_ids) if state.quality is not None else set()
    )
    return {
        topic.coverage_id
        for topic in state.sub_topics
        if topic.coverage_id.startswith(NOTE_COVERAGE_PREFIX)
        and (
            latest.get(topic.coverage_id, "provider_error") != "provider_error"
            or any(target.target_id in answered for target in topic.evidence_targets)
        )
    }


def notes_due_a_pass(state: ResearchState) -> list[ReaderNote]:
    """Active notes owed their one targeted pass, not yet passed (D11; spec §5.4, D5).

    A research note — its kinds include ``new_angle`` — owes it while it has
    no researched topic of its own, whatever the review said or whether one
    was made; a steering note owes it when the review found no evidence for
    it. A mixed note's steering half judged ``no_evidence`` buys no pass: its
    topic already researched the note.
    """
    verdicts = note_dispositions(state)
    researched = researched_note_topic_ids(state)
    return [
        note
        for note in active_reader_notes(state.reader_notes)
        if not note.passed
        and (
            (
                is_research_note(note)
                and f"{NOTE_COVERAGE_PREFIX}{note.note_id}" not in researched
            )
            or (
                not is_research_note(note)
                and verdicts.get(note.note_id) == "no_evidence"
            )
        )
    ]


def notes_due_a_redraft(state: ResearchState) -> list[ReaderNote]:
    """Active notes owed their one redraft (live-briefs spec §4.6; spec §5.4, D20).

    Only a note with a steering kind buys one — a steering note, or a mixed
    note's steering half: one the report ignores although its findings bear
    on it, or one no review input carried because it arrived after the input
    was built. A note whose only kind is ``new_angle`` never does.
    """
    verdicts = note_dispositions(state)
    return [
        note
        for note in active_reader_notes(state.reader_notes)
        if has_steering_kind(note)
        and not note.redrafted
        and (
            verdicts.get(note.note_id) == "ignored_with_evidence"
            or not note.reviewed
        )
    ]


def graph_route(state: ResearchState) -> tuple[str, str]:
    """Where the graph goes after the Report Reviewer, and why (spec §6.3-§6.5).

    Pure, so the conditional edge, the recorded route event, the final status,
    and the terminal quality status all read the same decision.

    There are four destinations. ``ROUTE_EXTRA_PASS`` buys the one extra
    research pass the targets still missing a verified finding justify, and it
    is bought only for those targets. ``ROUTE_REDRAFT`` buys the one writer
    re-run a scored review's *material* defects justify — no research, one
    draft, with the defects fed back. ``ROUTE_FINALIZE`` publishes and stops.
    ``ROUTE_END`` skips publication entirely, and is reached only by a halted
    run: a failed run publishes nothing rather than a stale earlier pass's
    artifact.

    The order is the order of certainty. A halt outranks everything: the run
    could not finish, so no verdict about the report is owed. The extra pass
    is read next, because "these obligations have nowhere to come from" is a
    defect with somewhere to go while the budget holds — and a reviewer's own
    ``coverage`` defect naming a required target is that same defect even when
    the code-stamped gate already calls the target answered, so it buys the
    pass too, before any redraft — and the redraft is read after both, because
    a re-draft cannot answer a target no verified finding answers. Then the
    review: a report no reviewer scored is published as partial rather than
    judged; a scored report a reviewer explicitly refused buys its one
    re-draft before the run gives up, since that is the only lever left once
    research cannot help; and a scored report that cleared the gates and the
    reviewer is accepted (PD-23) — even when the pass bought for a missing
    target found nothing, since §6.4 accepts a report whose remaining
    obligations are *listed* under Not found. Only a scored report that was
    not accepted and still owes a target that the ceiling can no longer buy
    for ends as ``extra_passes_exhausted`` (status ``max_iterations``).

    The reader's notes are read right after a halt (live-briefs spec §4.6,
    D11; notes-progress-report spec §5.4): first a note owed its one targeted
    pass buys it (``ROUTE_NOTE_PASS``) — a research note with no researched
    topic, whatever the review's status, or a steering note the review found
    no evidence for — then a note with a steering kind the report ignores, or
    one no review has read yet, buys its one redraft (``ROUTE_REDRAFT`` with
    the reason ``note_redraft_requested``, which spends no writer re-run of
    the review's own). Each note is flagged when its route is taken, so
    neither check can loop; with no note due, every rule below reads as it
    always did.
    """
    if is_halted(state):
        return ROUTE_END, "halted"
    if notes_due_a_pass(state):
        return ROUTE_NOTE_PASS, "note_pass_requested"
    if notes_due_a_redraft(state):
        return ROUTE_REDRAFT, "note_redraft_requested"
    review = state.report_review
    missing = review is not None and bool(extra_pass_target_ids(state))
    if missing and state.iteration < state.max_extra_passes:
        return ROUTE_EXTRA_PASS, "extra_pass_requested"
    if review is None or review.status != "scored":
        return ROUTE_FINALIZE, "review_unavailable"
    if state.writer_redrafts < MAX_WRITER_REDRAFTS and review.material_defects:
        # A material defect blocks acceptance whatever else the review said, so
        # this route would otherwise be ``report_not_accepted`` — publishing a
        # report the run's own reviewer named as materially wrong while one
        # draft with the defect list fed back was still unspent.
        return ROUTE_REDRAFT, "redraft_requested"
    if (
        state.quality is not None
        and not state.quality.hard_failures
        and semantic_review_passes(review)
    ):
        return ROUTE_FINALIZE, "report_accepted"
    if missing:
        return ROUTE_FINALIZE, "extra_passes_exhausted"
    return ROUTE_FINALIZE, "report_not_accepted"


def graph_status(state: ResearchState) -> str:
    """Name how this run ended, from the same decision the router used."""
    return _STATUS_BY_ROUTE_REASON[graph_route(state)[1]]


def graph_quality_status(state: ResearchState) -> str:
    """The terminal quality status the finalizer stamps on both artifacts.

    Read from the same pure decision the router used, so the status a reader
    sees and the edge the graph took cannot disagree. ``accepted`` is
    ``report_accepted`` and nothing else: only a report the deterministic
    gates found no hard failure in *and* the Report Reviewer scored at or above
    the acceptance threshold is one, and a report no quality pass judged is
    ``partial`` — nothing unjudged may be called accepted, and the finalizer
    saves no finding to memory for a partial run.
    """
    return (
        QUALITY_STATUS_ACCEPTED
        if graph_route(state)[1] == "report_accepted"
        else QUALITY_STATUS_PARTIAL
    )


def graph_recursion_limit(max_extra_passes: int) -> int:
    """Bound LangGraph's supersteps from the graph's real shape.

    Always passed explicitly. LangGraph 1.2 defaults this generously, but
    earlier releases defaulted to 25 — under what the first pass plus one extra
    pass over eight nodes need — and an explicit value documents the shape.
    Every reader note may buy one targeted pass and one redraft (live-briefs
    spec §4.6, D11a), so the notes' own worst case is added on top: the limit
    is never what stops a run the notes lengthened.
    """
    if max_extra_passes < 0:
        raise ValueError("max_extra_passes must not be negative")
    return (
        (max_extra_passes + 1) * len(NODE_NAMES)
        + MAX_NOTES_PER_RUN * (len(NODE_NAMES) + NOTE_REDRAFT_STEPS)
        + _RECURSION_MARGIN
    )
