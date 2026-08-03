"""Characterization tests for the LangGraph surface this project builds on.

These test no project code. They pin the four framework behaviors the
orchestrator's design rests on — a single-key dict channel replaced by each
node, a conditional edge driven by a ``path_map``, resume by ``thread_id``,
and what state lookup does for an unknown thread or a graph with no
checkpointer — so a LangGraph upgrade that moves any of them fails here, in
one small file, instead of somewhere inside the research graph.
"""

from __future__ import annotations

from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph


class _Channel(TypedDict):
    payload: dict[str, int]


async def _increment(channel: _Channel) -> _Channel:
    payload = dict(channel["payload"])
    payload["count"] = payload.get("count", 0) + 1
    return {"payload": payload}


async def _double(channel: _Channel) -> _Channel:
    payload = dict(channel["payload"])
    payload["count"] = payload["count"] * 2
    return {"payload": payload}


def _route(channel: _Channel) -> str:
    return "again" if channel["payload"]["count"] < 4 else "stop"


def _loop_builder() -> StateGraph:
    builder = StateGraph(_Channel)
    builder.add_node("increment", _increment)
    builder.add_node("double", _double)
    builder.add_edge(START, "increment")
    builder.add_edge("increment", "double")
    builder.add_conditional_edges(
        "double", _route, {"again": "increment", "stop": END}
    )
    return builder


def _chain_builder() -> StateGraph:
    builder = StateGraph(_Channel)
    builder.add_node("increment", _increment)
    builder.add_node("double", _double)
    builder.add_edge(START, "increment")
    builder.add_edge("increment", "double")
    builder.add_edge("double", END)
    return builder


def _config(thread_id: str) -> dict[str, object]:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 20}


@pytest.mark.asyncio
async def test_a_conditional_edge_loops_until_its_path_map_says_stop() -> None:
    graph = _loop_builder().compile()

    result = await graph.ainvoke({"payload": {"count": 0}}, {"recursion_limit": 20})

    # 0 -> 1 -> 2 (loop, 2 < 4) -> 3 -> 6 (stop, 6 >= 4)
    assert result["payload"]["count"] == 6


@pytest.mark.asyncio
async def test_an_interrupted_run_resumes_from_its_thread_id() -> None:
    graph = _chain_builder().compile(
        checkpointer=InMemorySaver(), interrupt_before=["double"]
    )
    config = _config("session-1")

    paused = await graph.ainvoke({"payload": {"count": 0}}, config)
    snapshot = await graph.aget_state(config)
    resumed = await graph.ainvoke(None, config)

    assert paused["payload"]["count"] == 1
    assert snapshot.next == ("double",)
    assert resumed["payload"]["count"] == 2


@pytest.mark.asyncio
async def test_resuming_a_finished_thread_returns_its_final_values() -> None:
    graph = _chain_builder().compile(checkpointer=InMemorySaver())
    config = _config("session-1")

    await graph.ainvoke({"payload": {"count": 0}}, config)
    resumed = await graph.ainvoke(None, config)

    assert resumed["payload"]["count"] == 2


@pytest.mark.asyncio
async def test_an_unknown_thread_has_no_checkpointed_values() -> None:
    graph = _chain_builder().compile(checkpointer=InMemorySaver())

    snapshot = await graph.aget_state(_config("never-run"))

    assert snapshot.values == {}


@pytest.mark.asyncio
async def test_state_lookup_without_a_checkpointer_raises() -> None:
    graph = _chain_builder().compile()

    with pytest.raises(ValueError):
        await graph.aget_state(_config("session-1"))
