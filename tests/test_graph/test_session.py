"""Tests for the session runner: trace metadata, checkpointing, resume."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from deep_research.agents.base import AgentRun
from deep_research.agents.errors import AgentConfigurationError
from deep_research.graph.errors import GraphResumeError
from deep_research.graph.orchestrator import (
    build_checkpointer,
    compile_research_graph,
    resume_research_graph,
    run_research_graph,
)
from deep_research.graph.state import (
    DEFAULT_MAX_ITERATIONS,
    PLANNER_NODE,
    ResearchGraphState,
    dump_state,
)
from deep_research.observability import AgentMetric, Tracker
from deep_research.observability.context import LangSmithRuntimeConfig
from deep_research.utils.config import GraphConfig
from deep_research.utils.types import MemorySnapshot, ResearchState
from tests.graph_fakes import (
    FakeAgent,
    fake_critique,
    fake_finding,
    fake_research_agents,
    fake_research_state,
)
from tests.test_observability_tracker import RecordingTraceFactory

QUESTION = "How mature is quantum error correction?"


class SpanningFakeAgent(FakeAgent):
    """A fake that opens the agent span a real agent would.

    Proves the session span's trace context reaches a LangGraph node — the
    contextvar has to survive whatever task LangGraph runs the node in, and
    ``Tracker.agent_span`` raises without an active session span.
    """

    def __init__(self, name: str, tracker: Tracker) -> None:
        super().__init__(name)
        self._tracker = tracker

    async def run(self, state: ResearchState) -> AgentRun[Any]:
        async with self._tracker.agent_span(self.name):
            return await super().run(state)


class EmptyValuesGraph:
    """A compiled-graph stand-in whose values stream is always empty.

    LangGraph can legally yield no snapshots when a resumed thread has no
    pending nodes, so the empty-stream boundary has to be pinned by tests:
    a terminal checkpoint is the only empty stream a resume may fall back
    to. ``aget_state`` serves the scripted snapshot each test supplies.
    """

    def __init__(self, *, snapshot: Any | None = None) -> None:
        self._snapshot = snapshot

    async def astream(
        self,
        channel: ResearchGraphState | None,
        config: dict[str, Any],
        *,
        stream_mode: str | None = None,
    ) -> AsyncIterator[ResearchGraphState]:
        """Yield nothing, ever: a stream with zero snapshots."""
        return
        yield  # pragma: no cover

    async def ainvoke(
        self,
        channel: ResearchGraphState | None,
        config: dict[str, Any],
    ) -> ResearchGraphState:
        raise AssertionError("the empty-stream graph must never be invoked")

    async def aget_state(self, config: dict[str, Any]) -> Any:
        if self._snapshot is None:
            raise ValueError("a resumable graph needs a checkpointer")
        return self._snapshot


def _event_types(state: ResearchState) -> list[str]:
    return [event.event_type for event in state.events]


@pytest.mark.asyncio
async def test_a_run_returns_the_final_state_and_its_status(
    tracker: Tracker,
) -> None:
    graph = compile_research_graph(fake_research_agents())

    run = await run_research_graph(
        graph=graph,
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
        max_iterations=2,
    )

    assert run.session_id == "session-1"
    assert run.status == "completed"
    assert run.state.report == "# Research report: pass 1"
    assert run.state.original_question == QUESTION
    assert run.state.max_iterations == 2


@pytest.mark.asyncio
async def test_a_run_brackets_itself_with_session_events(
    tracker: Tracker,
) -> None:
    graph = compile_research_graph(fake_research_agents())

    run = await run_research_graph(
        graph=graph,
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
    )
    types = _event_types(run.state)

    assert types[0] == "graph.session.started"
    assert types[-1] == "graph.session.completed"
    assert run.state.events[-1].metadata["status"] == "completed"
    assert run.state.events[-1].metadata["has_report"] is True


@pytest.mark.asyncio
async def test_a_run_carries_the_callers_memory_context_to_the_planner(
    tracker: Tracker,
) -> None:
    agents = fake_research_agents()

    await run_research_graph(
        graph=compile_research_graph(agents),
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
        memory_context=MemorySnapshot(suggested_strategies=["start broad"]),
    )

    assert agents.planner.calls[0].memory_context.suggested_strategies == [
        "start broad"
    ]


@pytest.mark.asyncio
async def test_a_run_attaches_session_metadata_and_routes_to_the_trace() -> None:
    trace_factory = RecordingTraceFactory()
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=True,
            project="deep-research-tests",
            api_key="secret-key",
        ),
        client_factory=lambda **kwargs: object(),
        trace_factory=trace_factory,
    )
    agents = fake_research_agents(
        critic=FakeAgent(
            "critic",
            [
                {"critique": fake_critique(should_continue=True, score=4)},
                {"critique": fake_critique(should_continue=False, score=9)},
            ],
        )
    )

    run = await run_research_graph(
        graph=compile_research_graph(agents),
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
    )

    completed = [
        event
        for event in tracker.events
        if event.event_type == "observability.span.completed"
        and event.metadata["span_name"] == "research.session"
    ]
    assert len(completed) == 1
    assert completed[0].metadata["session_id"] == "session-1"
    assert completed[0].metadata["success"] is True

    # The whole trace surface the spec asks for lands on the session run:
    # identity, final status, the route decisions, and every count. A wrong
    # key or a dropped field fails here rather than shipping silently.
    session_run = trace_factory.managers[0].run
    assert run.trace_url == session_run.trace_url
    assert session_run.end_calls[-1]["outputs"] == {
        "session_id": "session-1",
        "status": "completed",
        "route_reason": "critique_satisfied",
        "route_decisions": ["refinement_requested", "critique_satisfied"],
        "iteration": 1,
        "max_iterations": DEFAULT_MAX_ITERATIONS,
        "sub_topic_count": 1,
        "finding_count": 2,
        "source_count": 2,
        "claim_count": 2,
        "critic_score": 9,
        "has_report": True,
        "error_count": 0,
    }


@pytest.mark.asyncio
async def test_every_node_gets_its_own_agent_span(tracker: Tracker) -> None:
    agents = fake_research_agents(
        planner=SpanningFakeAgent("planner", tracker),
        researcher=SpanningFakeAgent("researcher", tracker),
        source_evaluator=SpanningFakeAgent("source_evaluator", tracker),
        fact_checker=SpanningFakeAgent("fact_checker", tracker),
        synthesizer=SpanningFakeAgent("synthesizer", tracker),
        critic=SpanningFakeAgent("critic", tracker),
    )

    await run_research_graph(
        graph=compile_research_graph(agents),
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
    )

    spanned = {
        metric.agent_name
        for metric in tracker.metrics
        if isinstance(metric, AgentMetric)
    }
    assert spanned == {
        "planner",
        "researcher",
        "source_evaluator",
        "fact_checker",
        "synthesizer",
        "critic",
    }


@pytest.mark.asyncio
async def test_a_checkpointed_session_can_be_resumed_by_its_session_id(
    tracker: Tracker,
) -> None:
    agents = fake_research_agents()
    graph = compile_research_graph(
        agents, checkpointer=build_checkpointer(enabled=True)
    )
    first = await run_research_graph(
        graph=graph,
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
    )
    call_counts = [len(agents.planner.calls), len(agents.critic.calls)]

    resumed = await resume_research_graph(
        graph=graph, tracker=tracker, session_id="session-1"
    )

    assert resumed.state.report == first.state.report
    assert resumed.state.original_question == QUESTION
    assert resumed.status == "completed"
    # A finished session replays its checkpoint; no agent runs again.
    assert [len(agents.planner.calls), len(agents.critic.calls)] == call_counts


@pytest.mark.asyncio
async def test_a_resume_uses_the_checkpointed_iteration_budget(
    tracker: Tracker,
) -> None:
    agents = fake_research_agents(
        researcher=FakeAgent(
            "researcher",
            [RuntimeError("crash"), {"raw_findings": [fake_finding()]}],
        ),
        critic=FakeAgent(
            "critic", [{"critique": fake_critique(should_continue=True, score=3)}]
        ),
    )
    graph = compile_research_graph(
        agents, checkpointer=build_checkpointer(enabled=True)
    )

    # A mid-run crash leaves the thread checkpointed but unfinished. The
    # budget the session started with must survive in the checkpoint, or a
    # resume dies at a recursion bound derived from the runner's own
    # default (3) instead of the session's real budget (6).
    with pytest.raises(RuntimeError, match="crash"):
        await run_research_graph(
            graph=graph,
            tracker=tracker,
            session_id="session-1",
            question=QUESTION,
            max_iterations=6,
        )

    resumed = await resume_research_graph(
        graph=graph, tracker=tracker, session_id="session-1"
    )

    assert resumed.state.max_iterations == 6
    assert resumed.state.iteration == 6
    assert resumed.status == "max_iterations"
    # Passes 0-6 of a budget-6 run, with the crashed pass re-run on resume:
    # eight researcher calls in all.
    assert len(agents.researcher.calls) == 8


@pytest.mark.asyncio
async def test_the_started_event_reads_checkpointing_off_the_compiled_graph(
    tracker: Tracker,
) -> None:
    graph = compile_research_graph(
        fake_research_agents(), checkpointer=build_checkpointer(enabled=True)
    )

    run = await run_research_graph(
        graph=graph,
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
    )

    assert run.state.events[0].metadata["checkpointing"] is True


@pytest.mark.asyncio
async def test_a_graph_without_a_checkpointer_never_claims_resumability(
    tracker: Tracker,
) -> None:
    run = await run_research_graph(
        graph=compile_research_graph(fake_research_agents()),
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
    )

    assert run.state.events[0].metadata["checkpointing"] is False


@pytest.mark.asyncio
async def test_resuming_an_unknown_session_is_refused(tracker: Tracker) -> None:
    graph = compile_research_graph(
        fake_research_agents(), checkpointer=build_checkpointer(enabled=True)
    )

    with pytest.raises(GraphResumeError, match="no checkpoint"):
        await resume_research_graph(
            graph=graph, tracker=tracker, session_id="never-run"
        )


@pytest.mark.asyncio
async def test_resuming_without_a_checkpointer_is_refused(
    tracker: Tracker,
) -> None:
    graph = compile_research_graph(fake_research_agents())

    with pytest.raises(GraphResumeError, match="checkpointer"):
        await resume_research_graph(
            graph=graph, tracker=tracker, session_id="session-1"
        )


@pytest.mark.asyncio
async def test_a_completed_checkpoint_resumes_with_a_handler_without_rerunning_nodes(
    tracker: Tracker,
) -> None:
    agents = fake_research_agents()
    graph = compile_research_graph(
        agents, checkpointer=build_checkpointer(enabled=True)
    )
    first = await run_research_graph(
        graph=graph,
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
    )
    call_counts = [len(agents.planner.calls), len(agents.researcher.calls)]

    received = []
    resumed = await resume_research_graph(
        graph=graph,
        tracker=tracker,
        session_id="session-1",
        event_handler=received.append,
    )

    assert resumed.status == "completed"
    assert resumed.state.report == first.state.report
    # A finished session replays its checkpoint; no agent runs again.
    assert [len(agents.planner.calls), len(agents.researcher.calls)] == call_counts
    # Every checkpointed event is delivered once, in state order, and the
    # runner's single completion event closes the stream.
    assert received == resumed.state.events
    assert received[-1].event_type == "graph.session.completed"
    assert (
        sum(
            event.event_type == "graph.session.completed"
            for event in received
        )
        == 1
    )


@pytest.mark.asyncio
async def test_a_zero_snapshot_terminal_resume_publishes_the_checkpoint(
    tracker: Tracker,
) -> None:
    """The acceptance case: an empty values stream on a terminal checkpoint.

    A resumed thread with no pending nodes can yield zero snapshots. The
    checkpoint itself is then the run's only result: it must be returned
    and its events published exactly once, in state order, with one
    completion event last — never a RuntimeError and never a node rerun.
    """
    agents = fake_research_agents()
    graph = compile_research_graph(
        agents, checkpointer=build_checkpointer(enabled=True)
    )
    first = await run_research_graph(
        graph=graph,
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
    )
    call_counts = [len(agents.planner.calls), len(agents.researcher.calls)]
    # The durable checkpoint holds everything except the runner-appended
    # completion event, and has no pending nodes.
    checkpointed = first.state.model_copy(
        update={"events": first.state.events[:-1]}
    )
    empty = EmptyValuesGraph(
        snapshot=SimpleNamespace(values=dump_state(checkpointed), next=())
    )

    received = []
    resumed = await resume_research_graph(
        graph=empty,
        tracker=tracker,
        session_id="session-1",
        event_handler=received.append,
    )

    assert [len(agents.planner.calls), len(agents.researcher.calls)] == call_counts
    assert resumed.status == "completed"
    assert resumed.state.report == first.state.report
    assert received == resumed.state.events
    assert received[-1].event_type == "graph.session.completed"
    assert (
        sum(
            event.event_type == "graph.session.completed"
            for event in received
        )
        == 1
    )


@pytest.mark.asyncio
async def test_an_empty_values_stream_on_a_fresh_run_is_still_an_error(
    tracker: Tracker,
) -> None:
    with pytest.raises(
        RuntimeError, match="research graph produced no state"
    ):
        await run_research_graph(
            graph=EmptyValuesGraph(),
            tracker=tracker,
            session_id="session-1",
            question=QUESTION,
            event_handler=lambda event: None,
        )


@pytest.mark.asyncio
async def test_an_empty_values_stream_on_a_nonterminal_resume_is_still_an_error(
    tracker: Tracker,
) -> None:
    checkpointed = SimpleNamespace(
        values=dump_state(fake_research_state(max_iterations=2)),
        next=(PLANNER_NODE,),
    )

    with pytest.raises(
        RuntimeError, match="research graph produced no state"
    ):
        await resume_research_graph(
            graph=EmptyValuesGraph(snapshot=checkpointed),
            tracker=tracker,
            session_id="session-1",
            event_handler=lambda event: None,
        )


@pytest.mark.asyncio
async def test_an_unfinished_resume_streams_events_in_order_without_duplicates(
    tracker: Tracker,
) -> None:
    agents = fake_research_agents(
        researcher=FakeAgent(
            "researcher",
            [RuntimeError("crash"), {"raw_findings": [fake_finding()]}],
        ),
        critic=FakeAgent(
            "critic", [{"critique": fake_critique(should_continue=True, score=3)}]
        ),
    )
    graph = compile_research_graph(
        agents, checkpointer=build_checkpointer(enabled=True)
    )
    with pytest.raises(RuntimeError, match="crash"):
        await run_research_graph(
            graph=graph,
            tracker=tracker,
            session_id="session-1",
            question=QUESTION,
            max_iterations=6,
        )

    received = []
    resumed = await resume_research_graph(
        graph=graph,
        tracker=tracker,
        session_id="session-1",
        event_handler=received.append,
    )

    assert resumed.status == "max_iterations"
    # The crashed pass re-runs on resume, exactly as without a handler.
    assert len(agents.researcher.calls) == 8
    assert received == resumed.state.events
    assert received[-1].event_type == "graph.session.completed"


@pytest.mark.asyncio
async def test_a_failed_run_still_returns_its_state(tracker: Tracker) -> None:
    agents = fake_research_agents(
        planner=FakeAgent("planner", [AgentConfigurationError("bad wiring")])
    )

    run = await run_research_graph(
        graph=compile_research_graph(agents),
        tracker=tracker,
        session_id="session-1",
        question=QUESTION,
    )

    assert run.status == "failed"
    assert run.state.original_question == QUESTION
    assert run.state.events[-1].metadata["status"] == "failed"


def test_the_graph_config_default_matches_the_graph_module_default() -> None:
    assert GraphConfig().max_iterations == DEFAULT_MAX_ITERATIONS

