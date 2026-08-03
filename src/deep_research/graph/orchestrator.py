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

from deep_research.graph.errors import GraphResumeError
from deep_research.graph.events import (
    session_completed_event,
    session_started_event,
)
from deep_research.graph.nodes import (
    ResearchAgent,
    agent_node,
    critic_node,
    refine_node,
    route_after_critic,
)
from deep_research.graph.state import (
    CRITIC_NODE,
    DEFAULT_MAX_ITERATIONS,
    FACT_CHECKER_NODE,
    NODE_NAMES,
    PLANNER_NODE,
    REFINE_NODE,
    RESEARCHER_NODE,
    ROUTE_END,
    ROUTE_REFINE,
    SOURCE_EVALUATOR_NODE,
    SYNTHESIZER_NODE,
    ResearchGraphState,
    dump_state,
    graph_recursion_limit,
    graph_route,
    graph_status,
    initial_graph_state,
    load_state,
)
from deep_research.observability import Tracker
from deep_research.utils.types import (
    MemorySnapshot,
    ResearchState,
    merge_research_state,
)

# The five agent nodes that run before the Critic, in order. The Critic and
# the refinement hop are named separately because they are wired by
# different calls: one conditional edge, one plain edge back. Derived from
# ``NODE_NAMES`` so the execution order lives in exactly one place.
AGENT_NODE_ORDER = NODE_NAMES[:5]


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


@dataclass(frozen=True)
class GraphRun:
    """Everything one research session produced."""

    session_id: str
    state: ResearchState
    status: str
    trace_url: str | None


def _session_outputs(state: ResearchState, *, status: str) -> dict[str, Any]:
    """The session metadata this run attaches to its LangSmith span.

    Counts, identifiers, and enumerated reasons only — never provider text
    and never a report body. ``route_decisions`` is the whole macro history
    of the run, which is what makes "graph route decisions are observable"
    true from the trace alone.
    """
    critique = state.critique
    return {
        "session_id": state.session_id,
        "status": status,
        "route_reason": graph_route(state)[1],
        "route_decisions": [
            event.metadata["reason"]
            for event in state.events
            if event.event_type == "graph.route.decided"
        ],
        "iteration": state.iteration,
        "max_iterations": state.max_iterations,
        "sub_topic_count": len(state.sub_topics),
        "finding_count": len(state.raw_findings),
        "source_count": len(state.evaluated_sources),
        "claim_count": len(state.verified_claims),
        "critic_score": None if critique is None else critique.score,
        "has_report": state.report is not None,
        "error_count": len(state.errors),
    }


async def _invoke(
    *,
    graph: Any,
    tracker: Tracker,
    channel: ResearchGraphState | None,
    session_id: str,
    question: str,
    max_iterations: int,
) -> GraphRun:
    """Run or resume the compiled graph inside one session span.

    ``channel`` is ``None`` when resuming: LangGraph reads the thread's
    checkpoint instead of an input. The session span is what gives every
    agent inside a node an active trace context — ``Tracker.agent_span``
    raises without one.
    """
    async with tracker.session_span(session_id, question) as span:
        result = await graph.ainvoke(
            channel, session_config(session_id, max_iterations=max_iterations)
        )
        final = load_state(result)
        status = graph_status(final)
        final = merge_research_state(
            final,
            {
                "events": [
                    session_completed_event(
                        status=status,
                        iteration=final.iteration,
                        error_count=len(final.errors),
                        has_report=final.report is not None,
                    )
                ]
            },
        )
        span.set_outputs(_session_outputs(final, status=status))
        trace_url = span.trace_url

    return GraphRun(
        session_id=session_id,
        state=final,
        status=status,
        trace_url=trace_url,
    )


async def run_research_graph(
    *,
    graph: Any,
    tracker: Tracker,
    session_id: str,
    question: str,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    memory_context: MemorySnapshot | None = None,
) -> GraphRun:
    """Run one research session from the question to a final status.

    ``session_started_event`` is written into the initial state *before* the
    graph runs, so it is checkpointed with everything else.
    ``session_completed_event`` is appended after, so it is not: the
    checkpoint is written by the graph, and a completion recorded by the
    runner belongs to the run rather than to the durable state.

    ``checkpointing`` is read off the compiled graph rather than taken from
    the caller: the graph either has a checkpointer or it does not, and the
    started event must record the truth either way.
    """
    checkpointing = getattr(graph, "checkpointer", None) is not None
    state = load_state(
        initial_graph_state(
            session_id=session_id,
            question=question,
            max_iterations=max_iterations,
            memory_context=memory_context,
        )
    )
    state = merge_research_state(
        state,
        {
            "events": [
                session_started_event(
                    session_id=session_id,
                    max_iterations=max_iterations,
                    checkpointing=checkpointing,
                )
            ]
        },
    )
    return await _invoke(
        graph=graph,
        tracker=tracker,
        channel=dump_state(state),
        session_id=session_id,
        question=state.original_question,
        max_iterations=max_iterations,
    )


async def resume_research_graph(
    *,
    graph: Any,
    tracker: Tracker,
    session_id: str,
    max_iterations: int | None = None,
) -> GraphRun:
    """Continue a checkpointed session from where it stopped.

    The question and the iteration budget are read back out of the
    checkpoint rather than re-supplied, so a resume cannot silently research
    something else under a session id that already means something, and it
    cannot die at a recursion bound smaller than the one the session started
    with. ``max_iterations`` is an explicit override for callers who want a
    different budget than the one the checkpoint records.
    """
    config = session_config(
        session_id, max_iterations=max_iterations or DEFAULT_MAX_ITERATIONS
    )
    try:
        snapshot = await graph.aget_state(config)
    except ValueError as error:
        raise GraphResumeError(
            "resuming a session requires a graph compiled with a checkpointer"
        ) from error

    values = snapshot.values
    if not isinstance(values, dict) or "state" not in values:
        raise GraphResumeError(
            f"no checkpoint was recorded for session {session_id}"
        )

    checkpointed = load_state(values)
    budget = checkpointed.max_iterations if max_iterations is None else max_iterations
    return await _invoke(
        graph=graph,
        tracker=tracker,
        channel=None,
        session_id=session_id,
        question=checkpointed.original_question,
        max_iterations=budget,
    )
