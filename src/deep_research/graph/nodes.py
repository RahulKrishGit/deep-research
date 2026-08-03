"""LangGraph node wrappers over the agents that already work.

A node is thin on purpose: read the channel, invoke one agent, merge what
it returned with ``merge_research_state``, and bracket the whole thing with
graph-level events. Nothing here imports LangGraph — a node is an async
function from channel to channel, which is what makes every rule below
testable by calling it directly.

Failure handling is a halt discipline rather than an exception escaping
``ainvoke``. Three exception types become enumerated graph errors that mark
the run dead; every later node sees the mark, records ``graph.node.skipped``
and returns without invoking its agent; the router sends the run to ``END``
with status ``failed``. The state collected before the failure survives —
which is the whole point of not raising.

Anything other than those three exception types propagates. An unhandled
exception is a defect, not a research outcome, and converting it into a
recorded error would hide it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeAlias

from deep_research.agents.base import AgentRun
from deep_research.agents.errors import AgentConfigurationError, PlanningError
from deep_research.graph.errors import (
    GraphConfigurationError,
    agent_configuration_error,
    invalid_agent_state_error,
    invalid_route_error,
    planning_failed_error,
    provider_configuration_error,
)
from deep_research.graph.events import (
    node_completed_event,
    node_skipped_event,
    node_started_event,
    refinement_started_event,
    route_decided_event,
)
from deep_research.graph.state import (
    CRITIC_NODE,
    REFINE_NODE,
    ResearchGraphState,
    dump_state,
    graph_route,
    is_halted,
    load_state,
)
from deep_research.providers import ProviderConfigurationError
from deep_research.utils.types import (
    ResearchError,
    ResearchState,
    ResearchStateUpdate,
    advance_research_iteration,
    merge_research_state,
)

GraphNode: TypeAlias = Callable[
    [ResearchGraphState], Awaitable[ResearchGraphState]
]


class ResearchAgent(Protocol):
    """The one agent capability a graph node needs.

    Every concrete agent satisfies it. Keeping the protocol to a name and a
    ``run`` is what lets graph tests use two-line doubles with no provider,
    tracker, scratchpad, or toolset.
    """

    name: str

    async def run(self, state: ResearchState) -> AgentRun[Any]:
        """Run one agent pass over research state."""
        raise NotImplementedError


def _with(
    state: ResearchState,
    update: ResearchStateUpdate,
) -> ResearchGraphState:
    return dump_state(merge_research_state(state, update))


def _skipped(state: ResearchState, node: str) -> ResearchGraphState:
    return _with(
        state,
        {"events": [node_skipped_event(node, iteration=state.iteration)]},
    )


def _halt(
    state: ResearchState,
    error: ResearchError,
) -> ResearchGraphState:
    return _with(state, {"errors": [error]})


def agent_node(
    agent: ResearchAgent,
    *,
    node_name: str | None = None,
) -> GraphNode:
    """Wrap one agent as a LangGraph node.

    ``node_name`` defaults to the agent's own name so a LangSmith trace and
    the graph read the same; it is overridable so the same agent class can
    fill two slots if a later spec ever needs that.
    """
    name = (node_name or agent.name).strip()
    if not name:
        raise GraphConfigurationError("graph nodes need a non-blank name")

    async def node(channel: ResearchGraphState) -> ResearchGraphState:
        state = load_state(channel)
        if is_halted(state):
            return _skipped(state, name)

        started = merge_research_state(
            state,
            {"events": [node_started_event(name, iteration=state.iteration)]},
        )
        try:
            outcome = await agent.run(started)
        except PlanningError as error:
            return _halt(started, planning_failed_error(error, node=name))
        except AgentConfigurationError as error:
            return _halt(started, agent_configuration_error(error, node=name))
        except ProviderConfigurationError as error:
            return _halt(
                started, provider_configuration_error(error, node=name)
            )

        update = outcome.state_update
        try:
            merged = merge_research_state(started, update)
        except (TypeError, ValueError) as error:
            # merge_research_state already enforces the whole contract:
            # unknown fields, non-list appends, a direct iteration write, and
            # every Pydantic constraint. ValidationError subclasses ValueError.
            return _halt(started, invalid_agent_state_error(error, node=name))

        return _with(
            merged,
            {
                "events": [
                    node_completed_event(
                        name,
                        iteration=merged.iteration,
                        event_count=len(update.get("events", ())),
                        error_count=len(update.get("errors", ())),
                    )
                ]
            },
        )

    return node


def critic_node(
    agent: ResearchAgent,
    *,
    node_name: str = CRITIC_NODE,
) -> GraphNode:
    """Wrap the Critic and record the route its critique produced.

    The route event is recorded here rather than in the conditional edge
    because a LangGraph router reads state and cannot write it.
    ``graph_route`` is pure and the state does not change between this call
    and ``route_after_critic``'s, so the recorded decision and the taken
    edge agree by construction.
    """
    inner = agent_node(agent, node_name=node_name)

    async def node(channel: ResearchGraphState) -> ResearchGraphState:
        reviewed = await inner(channel)
        state = load_state(reviewed)
        destination, reason = graph_route(state)
        critique = state.critique
        return _with(
            state,
            {
                "events": [
                    route_decided_event(
                        destination=destination,
                        reason=reason,
                        iteration=state.iteration,
                        max_iterations=state.max_iterations,
                        should_continue=(
                            critique is not None and critique.should_continue
                        ),
                    )
                ]
            },
        )

    return node


async def refine_node(channel: ResearchGraphState) -> ResearchGraphState:
    """Open the next macro iteration before research runs again.

    This exists as its own node because a LangGraph conditional edge routes
    but cannot write, and the macro-iteration increment has to happen
    somewhere the graph can see and a test can call.
    """
    state = load_state(channel)
    if is_halted(state):
        return _skipped(state, REFINE_NODE)
    if state.iteration >= state.max_iterations:
        return _halt(
            state,
            invalid_route_error(
                node=REFINE_NODE,
                iteration=state.iteration,
                max_iterations=state.max_iterations,
            ),
        )

    advanced = advance_research_iteration(state)
    return _with(
        advanced,
        {
            "events": [
                refinement_started_event(
                    iteration=advanced.iteration,
                    max_iterations=advanced.max_iterations,
                )
            ]
        },
    )


def route_after_critic(channel: ResearchGraphState) -> str:
    """The conditional edge out of the Critic. Pure read of state."""
    return graph_route(load_state(channel))[0]
