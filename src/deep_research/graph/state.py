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

from typing import TypedDict

from pydantic import JsonValue

from deep_research.utils.types import MemorySnapshot, ResearchState

GRAPH_SOURCE = "graph"

PLANNER_NODE = "planner"
RESEARCHER_NODE = "researcher"
SOURCE_EVALUATOR_NODE = "source_evaluator"
FACT_CHECKER_NODE = "fact_checker"
SYNTHESIZER_NODE = "synthesizer"
CRITIC_NODE = "critic"
REFINE_NODE = "refine"

# Execution order, with the refinement hop last. Node names deliberately
# equal agent names so a LangSmith trace reads the same as this tuple.
NODE_NAMES = (
    PLANNER_NODE,
    RESEARCHER_NODE,
    SOURCE_EVALUATOR_NODE,
    FACT_CHECKER_NODE,
    SYNTHESIZER_NODE,
    CRITIC_NODE,
    REFINE_NODE,
)

ROUTE_REFINE = "refine"
ROUTE_END = "end"

# Enumerated, project-generated routing reasons. Never provider text: these
# reach ResearchEvent.metadata and the session span's outputs.
GRAPH_ROUTES = {
    "refinement_requested": (
        "The critic asked for another research pass and budget remains."
    ),
    "critique_satisfied": "The critic accepted the report.",
    "max_iterations_reached": (
        "The refinement budget is exhausted; this is the final report."
    ),
    "missing_critique": (
        "No critique was recorded, so no refinement can be justified."
    ),
    "halted": "The run stopped on a non-recoverable error.",
}

GRAPH_STATUSES = ("completed", "max_iterations", "incomplete", "failed")

_STATUS_BY_ROUTE_REASON = {
    "critique_satisfied": "completed",
    "max_iterations_reached": "max_iterations",
    "missing_critique": "incomplete",
    "refinement_requested": "incomplete",
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
    """Render one research state as the channel a node returns."""
    return {"state": state.model_dump(mode="json")}


def load_state(channel: ResearchGraphState) -> ResearchState:
    """Validate the channel back into a research state.

    Validation is not ceremony: it is what makes a checkpoint written by an
    older build fail loudly here rather than silently half-populate a node.
    """
    return ResearchState.model_validate(channel["state"])


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
    """
    return dump_state(
        ResearchState(
            session_id=session_id,
            original_question=question,
            max_iterations=max_iterations,
            memory_context=memory_context or MemorySnapshot(),
        )
    )


def is_halted(state: ResearchState) -> bool:
    """True when an enumerated graph failure has ended this run."""
    return any(
        error.error_type in HALTING_ERROR_TYPES for error in state.errors
    )


def graph_route(state: ResearchState) -> tuple[str, str]:
    """Decide where the graph goes after the Critic, and why.

    Pure, so the conditional edge, the recorded route event, and the final
    status all read the same decision. ``Critique.should_continue`` is the
    critic's recommendation; the iteration bound is the graph's law and is
    checked here regardless of what the model said.
    """
    if is_halted(state):
        return ROUTE_END, "halted"
    critique = state.critique
    if critique is None:
        return ROUTE_END, "missing_critique"
    if not critique.should_continue:
        return ROUTE_END, "critique_satisfied"
    if state.iteration >= state.max_iterations:
        return ROUTE_END, "max_iterations_reached"
    return ROUTE_REFINE, "refinement_requested"


def graph_status(state: ResearchState) -> str:
    """Name how this run ended, from the same decision the router used."""
    return _STATUS_BY_ROUTE_REASON[graph_route(state)[1]]


def graph_recursion_limit(max_iterations: int) -> int:
    """Bound LangGraph's supersteps from the graph's real shape.

    Always passed explicitly. LangGraph 1.2 defaults this generously, but
    earlier releases defaulted to 25 — under what four macro passes over
    seven nodes need — and an explicit value documents the shape.
    """
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")
    return (max_iterations + 1) * len(NODE_NAMES) + _RECURSION_MARGIN
