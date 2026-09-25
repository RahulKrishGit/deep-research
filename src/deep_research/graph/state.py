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

from typing import TypedDict

from pydantic import JsonValue

from deep_research.agents.report_reviewer import semantic_review_passes
from deep_research.utils.types import (
    QUALITY_CONTRACT_VERSION,
    QUALITY_STATUS_ACCEPTED,
    QUALITY_STATUS_PARTIAL,
    MemorySnapshot,
    ResearchState,
)

GRAPH_SOURCE = "graph"

PLANNER_NODE = "planner"
RESEARCHER_NODE = "researcher"
SOURCE_EVALUATOR_NODE = "source_evaluator"
EVIDENCE_VERIFIER_NODE = "evidence_verifier"
REPORT_WRITER_NODE = "report_writer"
REPORT_REVIEWER_NODE = "report_reviewer"
EXTRA_PASS_NODE = "extra_pass"
FINALIZE_NODE = "finalize_report"

# Execution order, with the terminal review, the one-hop extra pass, and the
# terminal publication step last. Node names deliberately equal agent names so
# a LangSmith trace reads the same as this tuple; ``report_reviewer`` is the
# graph's own reviewer rather than one of the five agents, ``extra_pass`` is
# the hop that carries the iteration increment, and ``finalize_report`` is the
# one node with no model call at all.
NODE_NAMES = (
    PLANNER_NODE,
    RESEARCHER_NODE,
    SOURCE_EVALUATOR_NODE,
    EVIDENCE_VERIFIER_NODE,
    REPORT_WRITER_NODE,
    REPORT_REVIEWER_NODE,
    EXTRA_PASS_NODE,
    FINALIZE_NODE,
)

ROUTE_EXTRA_PASS = "extra_pass"
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
        "Required targets have no verified finding and an extra pass remains; "
        "the researcher runs for those targets only."
    ),
    "extra_passes_exhausted": (
        "Required targets still have no verified finding, no extra pass "
        "remains, and the report was not accepted; the targets are listed "
        "under Not found."
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
    max_extra_passes: int = DEFAULT_MAX_EXTRA_PASSES,
    memory_context: MemorySnapshot | None = None,
) -> ResearchGraphState:
    """Build the channel one research session starts from.

    ``memory_context`` is supplied by the caller. The graph performs no
    recall of its own: that touches ChromaDB and an embedding provider,
    which orchestration has no business owning.

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
            quality_contract_version=QUALITY_CONTRACT_VERSION,
        )
    )


def is_halted(state: ResearchState) -> bool:
    """True when an enumerated graph failure has ended this run."""
    return any(
        error.error_type in HALTING_ERROR_TYPES for error in state.errors
    )


def graph_route(state: ResearchState) -> tuple[str, str]:
    """Where the graph goes after the Report Reviewer, and why (spec §6.3-§6.5).

    Pure, so the conditional edge, the recorded route event, the final status,
    and the terminal quality status all read the same decision.

    There are three destinations. ``ROUTE_EXTRA_PASS`` buys the one extra
    research pass the targets still missing a verified finding justify, and it
    is bought only for those targets. ``ROUTE_FINALIZE`` publishes and stops.
    ``ROUTE_END`` skips publication entirely, and is reached only by a halted
    run: a failed run publishes nothing rather than a stale earlier pass's
    artifact.

    The order is the order of certainty. A halt outranks everything: the run
    could not finish, so no verdict about the report is owed. The extra pass
    is read next, because "these obligations have nowhere to come from" is a
    defect with somewhere to go while the budget holds. Then the review: a
    report no reviewer scored is published as partial rather than judged, and a
    scored report that cleared the gates and the reviewer is accepted
    (PD-23) — even when the pass bought for a missing target found nothing,
    since §6.4 accepts a report whose remaining obligations are *listed* under
    Not found. Only a scored report that was not accepted and still owes a
    target that the ceiling can no longer buy for ends as
    ``extra_passes_exhausted`` (status ``max_iterations``).
    """
    if is_halted(state):
        return ROUTE_END, "halted"
    review = state.report_review
    missing = review is not None and bool(review.missing_required_target_ids)
    if missing and state.iteration < state.max_extra_passes:
        return ROUTE_EXTRA_PASS, "extra_pass_requested"
    if review is None or review.status != "scored":
        return ROUTE_FINALIZE, "review_unavailable"
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
    """
    if max_extra_passes < 0:
        raise ValueError("max_extra_passes must not be negative")
    return (max_extra_passes + 1) * len(NODE_NAMES) + _RECURSION_MARGIN
