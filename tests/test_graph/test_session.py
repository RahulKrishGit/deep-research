"""Tests for the session runner: trace metadata, checkpointing, resume."""

from __future__ import annotations

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
from deep_research.graph.state import DEFAULT_MAX_ITERATIONS
from deep_research.observability import AgentMetric, Tracker
from deep_research.utils.config import GraphConfig
from deep_research.utils.types import MemorySnapshot, ResearchState
from tests.graph_fakes import FakeAgent, fake_critique, fake_research_agents

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
async def test_a_run_attaches_session_metadata_and_routes_to_the_trace(
    tracker: Tracker,
) -> None:
    agents = fake_research_agents(
        critic=FakeAgent(
            "critic",
            [
                {"critique": fake_critique(should_continue=True, score=4)},
                {"critique": fake_critique(should_continue=False, score=9)},
            ],
        )
    )

    await run_research_graph(
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
        checkpointing=True,
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
async def test_a_checkpointed_run_records_that_checkpointing_was_on(
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
        checkpointing=True,
    )

    assert run.state.events[0].metadata["checkpointing"] is True


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

