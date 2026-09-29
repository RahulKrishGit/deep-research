"""Live publication (live-briefs spec E2, AC1): events reach the handler as they
happen, exactly once."""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

import pytest

from deep_research.agents.base import AgentRun
from deep_research.agents.errors import PlanningError
from deep_research.agents.events import agent_event
from deep_research.agents.steps import ReActRun
from deep_research.graph.live import _LIVE_SINK, bind_live_sink, publish_live
from deep_research.graph.orchestrator import (
    compile_research_graph,
    run_research_graph,
    session_config,
)
from deep_research.graph.state import initial_graph_state
from deep_research.observability import Tracker
from deep_research.utils.types import ResearchEvent, ResearchState
from tests.graph_fakes import fake_research_agents, fake_sub_topic, fake_target

QUESTION = "How mature is quantum error correction?"


def _event(event_type: str) -> ResearchEvent:
    return agent_event(agent_name="planner", event_type=event_type, message="Probe event.")


class LivePlanner:
    """A planner double that publishes live from its node's task and from a child task.

    ``seen_during_run`` is what the handler had received when ``run`` finished — the
    proof that live events arrived while the node was still running, not with its
    snapshot. ``sink_seen`` records whether the node's task could see a bound sink.
    """

    name = "planner"

    def __init__(self, received: list[ResearchEvent], *, fail: bool = False) -> None:
        self.received = received
        self.fail = fail
        self.published: dict[str, ResearchEvent] = {}
        self.seen_during_run: list[ResearchEvent] = []
        self.sink_seen: bool | None = None

    async def run(self, state: ResearchState) -> AgentRun[Any]:
        self.sink_seen = _LIVE_SINK.get() is not None
        direct = _event("planner.planning.started")
        publish_live(direct)
        self.published["direct"] = direct

        async def child() -> None:
            event = _event("test.child_task")
            publish_live(event)
            self.published["child"] = event

        await asyncio.gather(child())
        self.seen_during_run = list(self.received)
        if self.fail:
            raise PlanningError("scripted planning failure")
        quiet = _event("test.snapshot_only")
        self.published["quiet"] = quiet
        return AgentRun(
            agent_name=self.name,
            result=None,
            react=ReActRun(agent_name=self.name, stop_reason="finished"),
            errors=[],
            state_update={
                "sub_topics": [fake_sub_topic(targets=[fake_target()])],
                "events": [direct, self.published["child"], quiet],
            },
        )


@pytest.mark.asyncio
async def test_a_sink_bound_around_the_graph_stream_reaches_node_tasks() -> None:
    """Risk R1, tested first: LangGraph runs a node in a task that inherits the
    ContextVar bound around ``astream``, and so does every task the node starts."""
    delivered: list[ResearchEvent] = []
    planner = LivePlanner(delivered)
    graph = compile_research_graph(fake_research_agents(planner=planner))

    with bind_live_sink(delivered.append):
        async for _ in graph.astream(
            initial_graph_state(session_id="session-1", question=QUESTION),
            session_config("session-1", max_extra_passes=1),
            stream_mode="values",
        ):
            pass

    assert planner.sink_seen is True
    ids = {event.event_id for event in delivered}
    assert planner.published["direct"].event_id in ids
    assert planner.published["child"].event_id in ids


def test_publish_live_without_a_bound_sink_is_a_no_op() -> None:
    publish_live(_event("planner.planning.started"))  # nothing bound: no error


def test_bind_live_sink_restores_the_previous_sink() -> None:
    outer: list[ResearchEvent] = []
    inner: list[ResearchEvent] = []
    first, second, third = _event("a.b"), _event("a.c"), _event("a.d")
    with bind_live_sink(outer.append):
        with bind_live_sink(inner.append):
            publish_live(first)
        publish_live(second)
    publish_live(third)

    assert inner == [first]
    assert outer == [second]


@pytest.mark.asyncio
async def test_live_sink_reaches_nodes(tracker: Tracker) -> None:
    """The spec's named test (E2): a node's live events — its own, a child task's and
    its graph.node.started — reach the handler while the node runs, before the node's
    graph.node.completed."""
    received: list[ResearchEvent] = []
    planner = LivePlanner(received)
    graph = compile_research_graph(fake_research_agents(planner=planner))

    run = await run_research_graph(
        graph=graph, tracker=tracker, session_id="session-1", question=QUESTION,
        event_handler=received.append,
    )

    seen = {event.event_id for event in planner.seen_during_run}
    assert planner.published["direct"].event_id in seen
    assert planner.published["child"].event_id in seen
    assert planner.published["quiet"].event_id not in seen
    started = next(
        e for e in received
        if e.event_type == "graph.node.started" and e.metadata["node"] == "planner"
    )
    assert started.event_id in seen
    types = [event.event_type for event in received]
    done = next(
        i for i, e in enumerate(received)
        if e.event_type == "graph.node.completed" and e.metadata["node"] == "planner"
    )
    assert max(
        types.index("planner.planning.started"),
        types.index("test.child_task"),
        types.index("test.snapshot_only"),
    ) < done
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_every_event_is_delivered_once_when_live_and_snapshot_paths_mix(
    tracker: Tracker,
) -> None:
    received: list[ResearchEvent] = []
    planner = LivePlanner(received)
    graph = compile_research_graph(fake_research_agents(planner=planner))

    run = await run_research_graph(
        graph=graph, tracker=tracker, session_id="session-1", question=QUESTION,
        event_handler=received.append,
    )

    counts = Counter(event.event_id for event in received)
    assert set(counts.values()) == {1}
    assert set(counts) == {event.event_id for event in run.state.events}
    live = {event.event_id for event in planner.seen_during_run} | {
        e.event_id for e in received if e.event_type == "graph.node.started"
    }
    # Everything not published live keeps state order.
    assert [e.event_id for e in received if e.event_id not in live] == [
        e.event_id for e in run.state.events if e.event_id not in live
    ]
    assert received[0].event_type == "graph.session.started"
    assert received[-1].event_type == "graph.session.completed"


@pytest.mark.asyncio
async def test_a_node_that_halts_after_publishing_live_has_delivered_those_events_once(
    tracker: Tracker,
) -> None:
    """Delivered live, then the node halted: the handler has the events once, and the
    halted state — which keeps nothing the failed agent returned — does not."""
    received: list[ResearchEvent] = []
    graph = compile_research_graph(
        fake_research_agents(planner=LivePlanner(received, fail=True))
    )

    run = await run_research_graph(
        graph=graph, tracker=tracker, session_id="session-1", question=QUESTION,
        event_handler=received.append,
    )

    assert run.status == "failed"
    assert [e.event_type for e in received].count("planner.planning.started") == 1
    assert "planner.planning.started" not in [e.event_type for e in run.state.events]
    assert {e.event_id for e in run.state.events} <= {e.event_id for e in received}
    assert len({e.event_id for e in received}) == len(received)


@pytest.mark.asyncio
async def test_two_runs_at_once_keep_their_own_sinks(tracker: Tracker) -> None:
    first: list[ResearchEvent] = []
    second: list[ResearchEvent] = []

    async def one(session_id: str, sink: list[ResearchEvent]) -> None:
        graph = compile_research_graph(fake_research_agents(planner=LivePlanner(sink)))
        await run_research_graph(
            graph=graph, tracker=tracker, session_id=session_id, question=QUESTION,
            event_handler=sink.append,
        )

    await asyncio.gather(one("session-a", first), one("session-b", second))

    for sink, session_id in ((first, "session-a"), (second, "session-b")):
        assert sink[0].metadata["session_id"] == session_id
        assert [e.event_type for e in sink].count("planner.planning.started") == 1
        assert [e.event_type for e in sink].count("test.child_task") == 1


@pytest.mark.asyncio
async def test_a_run_without_a_handler_binds_no_sink(tracker: Tracker) -> None:
    planner = LivePlanner([])
    graph = compile_research_graph(fake_research_agents(planner=planner))

    run = await run_research_graph(
        graph=graph, tracker=tracker, session_id="session-1", question=QUESTION
    )

    assert run.status == "completed"
    assert planner.sink_seen is False
