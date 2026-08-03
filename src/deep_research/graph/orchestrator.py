"""The research graph: assembly, compilation, and the session runner.

The only module in this package that imports LangGraph. Everything the
graph *decides* — routing, halting, the macro-iteration bound — lives in
``state.py`` and ``nodes.py``, framework-free, so this module is pure
wiring and the rules are testable without compiling anything.

Graph shape:

    START -> planner -> researcher -> source_evaluator -> fact_checker
          -> synthesizer -> critic -> {refine -> researcher | END}

``refine`` is the hop that carries the macro-iteration increment. It exists
because a LangGraph conditional edge chooses a destination but cannot write
state, and the increment has to happen somewhere both the graph and a test
can see.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from deep_research.graph.nodes import (
    ResearchAgent,
    agent_node,
    critic_node,
    refine_node,
    route_after_critic,
)
from deep_research.graph.state import (
    CRITIC_NODE,
    FACT_CHECKER_NODE,
    PLANNER_NODE,
    REFINE_NODE,
    RESEARCHER_NODE,
    ROUTE_END,
    ROUTE_REFINE,
    SOURCE_EVALUATOR_NODE,
    SYNTHESIZER_NODE,
    ResearchGraphState,
    graph_recursion_limit,
)

# The five agent nodes that run before the Critic, in order. The Critic and
# the refinement hop are named separately because they are wired by
# different calls: one conditional edge, one plain edge back.
AGENT_NODE_ORDER = (
    PLANNER_NODE,
    RESEARCHER_NODE,
    SOURCE_EVALUATOR_NODE,
    FACT_CHECKER_NODE,
    SYNTHESIZER_NODE,
)


@dataclass(frozen=True)
class ResearchAgents:
    """The six agents one research graph runs.

    A dataclass rather than a mapping so ``build_research_graph`` has a
    typed six-field signature: forgetting the Fact Checker is a
    ``TypeError`` at construction, not a ``KeyError`` deep inside assembly.
    """

    planner: ResearchAgent
    researcher: ResearchAgent
    source_evaluator: ResearchAgent
    fact_checker: ResearchAgent
    synthesizer: ResearchAgent
    critic: ResearchAgent


def build_research_graph(agents: ResearchAgents) -> StateGraph:
    """Assemble the uncompiled research graph."""
    builder = StateGraph(ResearchGraphState)
    builder.add_node(PLANNER_NODE, agent_node(agents.planner, node_name=PLANNER_NODE))
    builder.add_node(
        RESEARCHER_NODE, agent_node(agents.researcher, node_name=RESEARCHER_NODE)
    )
    builder.add_node(
        SOURCE_EVALUATOR_NODE,
        agent_node(agents.source_evaluator, node_name=SOURCE_EVALUATOR_NODE),
    )
    builder.add_node(
        FACT_CHECKER_NODE,
        agent_node(agents.fact_checker, node_name=FACT_CHECKER_NODE),
    )
    builder.add_node(
        SYNTHESIZER_NODE,
        agent_node(agents.synthesizer, node_name=SYNTHESIZER_NODE),
    )
    builder.add_node(CRITIC_NODE, critic_node(agents.critic, node_name=CRITIC_NODE))
    builder.add_node(REFINE_NODE, refine_node)

    builder.add_edge(START, PLANNER_NODE)
    for source, destination in zip(
        AGENT_NODE_ORDER, (*AGENT_NODE_ORDER[1:], CRITIC_NODE), strict=True
    ):
        builder.add_edge(source, destination)
    builder.add_conditional_edges(
        CRITIC_NODE,
        route_after_critic,
        {ROUTE_REFINE: REFINE_NODE, ROUTE_END: END},
    )
    builder.add_edge(REFINE_NODE, RESEARCHER_NODE)
    return builder


def compile_research_graph(
    agents: ResearchAgents,
    *,
    checkpointer: Any | None = None,
) -> Any:
    """Compile the research graph, optionally with a checkpointer.

    The checkpointer is injected rather than chosen here so a durable saver
    can replace the in-memory one without touching a node.
    """
    return build_research_graph(agents).compile(checkpointer=checkpointer)


def build_checkpointer(*, enabled: bool) -> Any | None:
    """Return the checkpointer this build supports, or ``None``.

    ``InMemorySaver`` survives a resume inside one process, which is what
    spec 11 asks for and what its tests exercise. Durable checkpointing is
    a hardening concern and drops in through ``compile_research_graph``.
    """
    return InMemorySaver() if enabled else None


def session_config(session_id: str, *, max_iterations: int) -> dict[str, Any]:
    """The LangGraph run config for one research session.

    ``thread_id`` is the session id, which is what makes resume-by-session
    work. ``recursion_limit`` is always set explicitly: it is derived from
    the graph's real shape rather than left to a framework default that has
    changed between releases.
    """
    return {
        "configurable": {"thread_id": session_id},
        "recursion_limit": graph_recursion_limit(max_iterations),
    }
