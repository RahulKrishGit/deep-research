"""The research graph: assembly, compilation, and the session runner.

The only module in this package that imports LangGraph. Everything the
graph *decides* — routing, halting, the extra-pass ceiling — lives in
``state.py`` and ``nodes.py``, framework-free, so this module is pure
wiring and the rules are testable without compiling anything.

Graph shape:

    START -> planner -> researcher -> source_evaluator -> evidence_verifier
          -> report_writer -> report_reviewer
          -> {extra_pass -> researcher | finalize_report -> END}

``report_reviewer`` is the terminal review: it judges the report the Report
Writer just composed, and it runs before the route because the missing
required targets it stamps are what decide whether one more research pass is
owed. ``extra_pass`` is the hop that carries the iteration increment, and it
loops back to the researcher alone: an extra pass runs only for the targets
that were missing, so the plan — and every topic that already answered its
own obligation — is deliberately not re-derived. It exists as its own node
because a LangGraph conditional edge chooses a destination but cannot write
state, and the increment has to happen somewhere both the graph and a test
can see. ``finalize_report`` is the run's only writer: it publishes the three
composed artifacts once, at the terminal node, and is the reason the graph
has three destinations after the review rather than two.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeAlias

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from deep_research.graph.errors import GraphResumeError
from deep_research.graph.events import (
    session_completed_event,
    session_started_event,
)
from deep_research.graph.nodes import (
    ReportPublisher,
    ReportReviewerLike,
    ResearchAgent,
    agent_node,
    extra_pass_node,
    finalize_report_node,
    report_reviewer_node,
    report_writer_node,
    route_after_review,
)
from deep_research.graph.state import (
    DEFAULT_MAX_EXTRA_PASSES,
    EVIDENCE_VERIFIER_NODE,
    EXTRA_PASS_NODE,
    FINALIZE_NODE,
    NODE_NAMES,
    PLANNER_NODE,
    REPORT_REVIEWER_NODE,
    REPORT_WRITER_NODE,
    RESEARCHER_NODE,
    ROUTE_END,
    ROUTE_EXTRA_PASS,
    ROUTE_FINALIZE,
    SOURCE_EVALUATOR_NODE,
    ResearchGraphState,
    dump_state,
    graph_recursion_limit,
    graph_route,
    graph_status,
    initial_graph_state,
    load_state,
)
from deep_research.observability import RunTelemetryCollector, Tracker
from deep_research.utils.types import (
    MemorySnapshot,
    ResearchEvent,
    ResearchState,
    merge_research_state,
)

# The five agent nodes that run before the terminal review, in order. The
# reviewer, the extra-pass hop, and the terminal finalizer are named
# separately because they are wired by different calls: one conditional edge,
# one plain edge back to the researcher, and one plain edge to END. Derived
# from ``NODE_NAMES`` so the execution order lives in exactly one place.
AGENT_NODE_ORDER = NODE_NAMES[:5]


@dataclass(frozen=True)
class ResearchAgents:
    """The five agents one research graph runs, plus its reviewer and writer.

    A dataclass rather than a mapping so ``build_research_graph`` has a
    typed signature: forgetting the Evidence Verifier is a ``TypeError`` at
    construction, not a ``KeyError`` deep inside assembly.

    ``publisher`` is the graph's terminal writer. Left ``None``, the Report
    Writer is asked instead (see ``terminal_publisher``); a graph whose writer
    cannot write records that nothing was published rather than dropping the
    artifacts silently.

    ``report_reviewer`` is the terminal reviewer, and it is the one
    collaborator whose absence is *recorded* rather than substituted: a graph
    without one still runs and still publishes, and the review node writes an
    explicit ``incomplete`` judgement with a recoverable error. There is no
    fallback that would let an unreviewed report be accepted, because a silent
    fallback to a gate-only acceptance is the defect this wiring exists to
    remove.
    """

    planner: ResearchAgent
    researcher: ResearchAgent
    source_evaluator: ResearchAgent
    evidence_verifier: ResearchAgent
    report_writer: ResearchAgent
    publisher: ReportPublisher | None = None
    report_reviewer: ReportReviewerLike | None = None


def terminal_publisher(agents: ResearchAgents) -> ReportPublisher | None:
    """The one writer of the terminal artifacts, or ``None`` when unwired.

    The explicit slot wins. Otherwise the Report Writer is asked: that agent
    declares the ``write_document`` and ``save_to_memory`` tools and provides
    the publishing methods, so a production graph publishes without a second
    wiring step. A double that implements only ``run`` is not a publisher, and
    the finalizer then records that nothing was written.
    """
    if agents.publisher is not None:
        return agents.publisher
    writer = agents.report_writer
    if isinstance(writer, ReportPublisher):
        return writer
    return None


def build_research_graph(
    agents: ResearchAgents,
    *,
    run_telemetry: RunTelemetryCollector | None = None,
) -> StateGraph:
    """Assemble the uncompiled research graph.

    ``run_telemetry`` is the run's §7.3 collector, handed to the terminal
    finalizer — the one node that renders the quality record — so the figures
    published beside the report are the whole run's rather than one pass's.
    ``None`` builds the graph exactly as it was built before there was one.
    """
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
        EVIDENCE_VERIFIER_NODE,
        agent_node(
            agents.evidence_verifier, node_name=EVIDENCE_VERIFIER_NODE
        ),
    )
    builder.add_node(
        REPORT_WRITER_NODE, report_writer_node(agents.report_writer)
    )
    builder.add_node(
        REPORT_REVIEWER_NODE, report_reviewer_node(agents.report_reviewer)
    )
    builder.add_node(EXTRA_PASS_NODE, extra_pass_node)
    builder.add_node(
        FINALIZE_NODE,
        finalize_report_node(
            terminal_publisher(agents), run_telemetry=run_telemetry
        ),
    )

    builder.add_edge(START, PLANNER_NODE)
    for source, destination in zip(
        AGENT_NODE_ORDER,
        (*AGENT_NODE_ORDER[1:], REPORT_REVIEWER_NODE),
        strict=True,
    ):
        builder.add_edge(source, destination)
    # The review is written before the route is read, because the missing
    # required targets it stamps are what the route acts on.
    builder.add_conditional_edges(
        REPORT_REVIEWER_NODE,
        route_after_review,
        {
            ROUTE_EXTRA_PASS: EXTRA_PASS_NODE,
            ROUTE_FINALIZE: FINALIZE_NODE,
            ROUTE_END: END,
        },
    )
    # The extra-pass hop loops back to the researcher alone: the pass it opens
    # exists for the targets that were missing, and the topics that already
    # answered their own obligations are not part of it.
    builder.add_edge(EXTRA_PASS_NODE, RESEARCHER_NODE)
    builder.add_edge(FINALIZE_NODE, END)
    return builder


def compile_research_graph(
    agents: ResearchAgents,
    *,
    checkpointer: Any | None = None,
    run_telemetry: RunTelemetryCollector | None = None,
) -> Any:
    """Compile the research graph, optionally with a checkpointer.

    The checkpointer is injected rather than chosen here so a durable saver
    can replace the in-memory one without touching a node. ``run_telemetry``
    is threaded to the terminal finalizer for the same reason the budget is
    threaded to the providers: one collector per run, and the run's figures
    are reported by the node that publishes what they describe.
    """
    return build_research_graph(agents, run_telemetry=run_telemetry).compile(
        checkpointer=checkpointer
    )


def build_checkpointer(*, enabled: bool) -> Any | None:
    """Return the checkpointer this build supports, or ``None``.

    ``InMemorySaver`` survives a resume inside one process, which is what
    spec 11 asks for and what its tests exercise. Durable checkpointing is
    a hardening concern and drops in through ``compile_research_graph``.
    """
    return InMemorySaver() if enabled else None


def session_config(session_id: str, *, max_extra_passes: int) -> dict[str, Any]:
    """The LangGraph run config for one research session.

    ``thread_id`` is the session id, which is what makes resume-by-session
    work. ``recursion_limit`` is always set explicitly: it is derived from
    the graph's real shape rather than left to a framework default that has
    changed between releases.
    """
    return {
        "configurable": {"thread_id": session_id},
        "recursion_limit": graph_recursion_limit(max_extra_passes),
    }


# The local mirror of ``main.ProgressHandler``. Defined here rather than
# imported so the orchestrator never imports ``main`` (the entry point owns
# ``main``, not the other way around), and ``main`` re-exports its own alias
# of the same shape for front-end callers.
ProgressHandler: TypeAlias = Callable[[ResearchEvent], None]


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
        "max_extra_passes": state.max_extra_passes,
        "extra_pass_target_count": len(state.extra_pass_target_ids),
        "sub_topic_count": len(state.sub_topics),
        "finding_count": len(state.raw_findings),
        "verified_finding_count": len(state.verified_findings),
        "source_count": len(state.evaluated_sources),
        "has_report": state.report is not None,
        "error_count": len(state.errors),
    }


async def _stream_graph_result(
    *,
    graph: Any,
    channel: ResearchGraphState | None,
    config: dict[str, Any],
    event_handler: ProgressHandler,
    terminal_checkpoint: ResearchState | None = None,
) -> ResearchGraphState:
    """Run the graph in values mode, publishing only newly appended events.

    Each ``stream_mode="values"`` snapshot is the cumulative channel, so the
    slice after the last published index is exactly the events this superstep
    appended — nothing is published twice, and order within a snapshot is the
    order the graph recorded it. On a resume the first snapshot carries the
    checkpointed events, so a fresh handler still sees the whole session.

    A terminal checkpoint has no pending nodes, so its resumed stream can be
    empty and there is no first snapshot to carry the checkpointed events.
    When the caller knows the resume hit such a checkpoint
    (``terminal_checkpoint`` is not ``None``), the checkpoint itself is the
    run's only result: its events are published once, in state order, and the
    channel is returned unchanged. Every other empty stream is a defect and
    keeps failing with the exact ``RuntimeError``.
    """
    latest: ResearchGraphState | None = None
    published = 0

    if channel is not None:
        initial = load_state(channel)
        for event in initial.events:
            event_handler(event)
        published = len(initial.events)

    async for snapshot in graph.astream(
        channel,
        config,
        stream_mode="values",
    ):
        latest = snapshot
        state = load_state(snapshot)
        for event in state.events[published:]:
            event_handler(event)
        published = len(state.events)

    if latest is None:
        if terminal_checkpoint is None:
            raise RuntimeError("research graph produced no state")
        for event in terminal_checkpoint.events:
            event_handler(event)
        return dump_state(terminal_checkpoint)
    return latest


async def _invoke(
    *,
    graph: Any,
    tracker: Tracker,
    channel: ResearchGraphState | None,
    session_id: str,
    question: str,
    max_extra_passes: int,
    event_handler: ProgressHandler | None = None,
    terminal_checkpoint: ResearchState | None = None,
) -> GraphRun:
    """Run or resume the compiled graph inside one session span.

    ``channel`` is ``None`` when resuming: LangGraph reads the thread's
    checkpoint instead of an input. The session span is what gives every
    agent inside a node an active trace context — ``Tracker.agent_span``
    raises without one. With a handler, execution streams so each cumulative
    state event reaches it live; without one, the plain ``ainvoke`` path is
    unchanged. ``terminal_checkpoint`` is the resume-only fallback for a
    terminal checkpoint whose stream is legitimately empty (see
    ``_stream_graph_result``).
    """
    async with tracker.session_span(session_id, question) as span:
        config = session_config(session_id, max_extra_passes=max_extra_passes)
        if event_handler is None:
            result = await graph.ainvoke(channel, config)
        else:
            result = await _stream_graph_result(
                graph=graph,
                channel=channel,
                config=config,
                event_handler=event_handler,
                terminal_checkpoint=terminal_checkpoint,
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
        if event_handler is not None:
            event_handler(final.events[-1])
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
    max_extra_passes: int = DEFAULT_MAX_EXTRA_PASSES,
    memory_context: MemorySnapshot | None = None,
    event_handler: ProgressHandler | None = None,
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

    When ``event_handler`` is supplied, the run streams and every cumulative
    state event is delivered once, in state order, with the terminal
    ``graph.session.completed`` last. Without one, execution is the plain
    ``ainvoke`` path and nothing else changes.
    """
    checkpointing = getattr(graph, "checkpointer", None) is not None
    state = load_state(
        initial_graph_state(
            session_id=session_id,
            question=question,
            max_extra_passes=max_extra_passes,
            memory_context=memory_context,
        )
    )
    state = merge_research_state(
        state,
        {
            "events": [
                session_started_event(
                    session_id=session_id,
                    max_extra_passes=max_extra_passes,
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
        max_extra_passes=max_extra_passes,
        event_handler=event_handler,
    )


async def resume_research_graph(
    *,
    graph: Any,
    tracker: Tracker,
    session_id: str,
    max_extra_passes: int | None = None,
    event_handler: ProgressHandler | None = None,
) -> GraphRun:
    """Continue a checkpointed session from where it stopped.

    The question and the extra-pass budget are read back out of the
    checkpoint rather than re-supplied, so a resume cannot silently research
    something else under a session id that already means something, and it
    cannot die at a recursion bound smaller than the one the session started
    with. ``max_extra_passes`` is an explicit override for callers who want a
    different budget than the one the checkpoint records.

    With ``event_handler`` the resumed supersteps stream the same way a
    fresh run does, except that a terminal checkpoint — no pending nodes —
    can yield an empty stream, and then the checkpoint itself is the run's
    only result. Without one, execution is the plain ``ainvoke`` path.
    """
    config = session_config(
        session_id,
        max_extra_passes=max_extra_passes
        if max_extra_passes is not None
        else DEFAULT_MAX_EXTRA_PASSES,
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
    budget = (
        checkpointed.max_extra_passes
        if max_extra_passes is None
        else max_extra_passes
    )
    # An empty ``next`` means the checkpoint is terminal: the graph has no
    # pending nodes, so a resumed stream is legitimately empty and the
    # checkpoint is the fallback result. Any other checkpoint must produce
    # snapshots or fail exactly as before.
    terminal_checkpoint = checkpointed if not snapshot.next else None
    return await _invoke(
        graph=graph,
        tracker=tracker,
        channel=None,
        session_id=session_id,
        question=checkpointed.original_question,
        max_extra_passes=budget,
        event_handler=event_handler,
        terminal_checkpoint=terminal_checkpoint,
    )
