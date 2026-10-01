"""Replay mode: the real graph runs offline inside the API's own loop."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deep_research.api.app import create_app
from deep_research.api.replay import ReplayCaseMiddleware, ReplayRunner, parse_hold
from deep_research.api.sessions import SessionStore
from deep_research.e2e_evaluation.replay import production_config_path
from deep_research.e2e_evaluation.replay_matrix import REPLAY_CASE_IDS, scenario_by_id
from deep_research.utils.types import ResearchEvent
from tests.test_api.fakes import ScriptedRunner
from tests.test_api.replay_support import EXTRA_PASS_CASE, REVIEW_UNAVAILABLE_CASE, guarded, replay_outcome
from tests.test_api.test_app import valid_preflight, wait_until_terminal


PROGRESS_TYPES = frozenset({
    "planner.progress",
    "source_evaluator.progress",
    "evidence_verifier.progress",
    "report_writer.progress",
})


def replay_app(root: Path, *, delay: float = 0.0):
    runner = ReplayRunner(default_case=EXTRA_PASS_CASE, delay=delay, root=root)
    app = create_app(runner=runner, config_path=str(production_config_path()), mode="replay")
    app.add_middleware(ReplayCaseMiddleware, default_case=EXTRA_PASS_CASE)
    return app


def frames(text: str) -> list[str]:
    """The ``event:`` names of an SSE body, in order."""
    return [line[7:] for frame in text.split("\n\n") for line in frame.splitlines() if line.startswith("event: ")]


def test_replay_runner_completes_the_default_case_with_the_network_denied(tmp_path: Path) -> None:
    with guarded() as attempts, TestClient(replay_app(tmp_path)) as client:
        posted = client.post("/research", json={"query": "anything the operator typed"})
        assert posted.status_code == 202
        session_id = posted.json()["session_id"]
        status = wait_until_terminal(client, session_id)
        assert status["status"] == "completed"
        assert status["iteration"] == 1
        assert status["query"] == scenario_by_id(EXTRA_PASS_CASE).question
        names = frames(client.get(f"/research/{session_id}/stream").text)
        assert names[0] == "graph.session.started" and names[-1] == "graph.session.completed"
        report = client.get(f"/research/{session_id}/report")
        assert report.status_code == 200 and report.headers["content-type"].startswith("text/markdown")
    assert attempts == []


def test_replay_case_header_picks_the_outcome_only_in_replay_mode(tmp_path: Path) -> None:
    with guarded(), TestClient(replay_app(tmp_path)) as client:
        chosen = client.post("/research", json={"query": "q"}, headers={"X-Replay-Case": REVIEW_UNAVAILABLE_CASE})
        assert chosen.json()["query"] == scenario_by_id(REVIEW_UNAVAILABLE_CASE).question
        status = wait_until_terminal(client, chosen.json()["session_id"])
        assert status["status"] == "incomplete"
        assert status["semantic_review_status"] == "provider_failed"
        unknown = client.post("/research", json={"query": "typed text"}, headers={"X-Replay-Case": "no-such-case"})
        assert unknown.status_code == 202
        assert unknown.json()["query"] == "typed text"
        failed = wait_until_terminal(client, unknown.json()["session_id"])
        assert failed["status"] == "failed"
        assert failed["errors"][0]["error_type"] == "api.research.configuration_error"
        assert failed["errors"][0]["details"] == {"reason": "config_invalid"}
    live = create_app(runner=ScriptedRunner(), preflight=valid_preflight)
    with TestClient(live) as client:
        posted = client.post("/research", json={"query": "typed text"}, headers={"X-Replay-Case": REVIEW_UNAVAILABLE_CASE})
        assert posted.json()["query"] == "typed text"


def test_two_replay_sessions_at_once_both_finish(tmp_path: Path) -> None:
    with guarded(), TestClient(replay_app(tmp_path)) as client:
        ids = [client.post("/research", json={"query": "q"}).json()["session_id"] for _ in range(2)]
        for session_id in ids:
            assert wait_until_terminal(client, session_id)["status"] == "completed"
            assert (tmp_path / session_id).is_dir()


def _start(store: SessionStore, session_id: str, question: str) -> None:
    store.start(
        session_id=session_id,
        query=question,
        max_extra_passes=None,
        output_format="markdown",
        config_overrides={},
        config_path=str(production_config_path()),
    )


@pytest.mark.asyncio
async def test_pacer_releases_events_over_time_and_the_tail_before_the_fold(tmp_path: Path) -> None:
    delay = 0.05
    with guarded():
        scenario = scenario_by_id(EXTRA_PASS_CASE)
        store = SessionStore(runner=ReplayRunner(default_case=EXTRA_PASS_CASE, delay=delay, root=tmp_path))
        _start(store, "s1", scenario.question)
        session = store.require("s1")
        stamps: list[float] = []
        statuses: list[str] = []
        names: list[str] = []
        async for event in store.iter_events("s1"):
            stamps.append(time.perf_counter())
            statuses.append(session.status)
            names.append(event.event_type)
        assert session.task is not None
        await session.task
    assert statuses[0] == "running"
    assert names[0] == "graph.session.started" and names[-1] == "graph.session.completed"
    assert statuses[-1] == "running", "the tail is published before the outcome is folded"
    assert session.status == "completed"
    n = len(stamps)
    assert n >= 40
    assert stamps[-1] - stamps[0] >= 0.8 * (n - 1) * delay


@pytest.mark.asyncio
async def test_close_cancels_the_pacer_and_publishes_nothing_more(tmp_path: Path) -> None:
    with guarded():
        scenario = scenario_by_id(EXTRA_PASS_CASE)
        store = SessionStore(runner=ReplayRunner(default_case=EXTRA_PASS_CASE, delay=0.2, root=tmp_path))
        _start(store, "s1", scenario.question)
        session = store.require("s1")
        await asyncio.sleep(1.5)  # the graph is long done; the pacer is mid-way
        published = len(session.events)
        assert 0 < published < 72
        started = time.perf_counter()
        await store.close()
        assert time.perf_counter() - started < 1.0
        await asyncio.sleep(0.5)
    assert len(session.events) == published
    assert session.status == "running" and session.finished_at is not None


@pytest.mark.asyncio
async def test_the_replay_runner_delivers_every_event_once_inside_its_own_node(tmp_path: Path) -> None:
    """live-briefs spec E4 and AC1 on the real graph: the paced queue receives live
    events through the same handler; every event arrives once; each agent's events
    arrive between its node's graph.node.started and graph.node.completed; and each
    researcher.tool_call arrives after its topic's started event and before its
    completed event, although the topics run concurrently."""
    received: list[ResearchEvent] = []
    with guarded():
        scenario = scenario_by_id(EXTRA_PASS_CASE)
        runner = ReplayRunner(default_case=EXTRA_PASS_CASE, delay=0.0, root=tmp_path)
        outcome = await runner(
            question=scenario.question, session_id="s1", max_extra_passes=None,
            output_format="markdown", config_overrides={},
            config_path=str(production_config_path()), event_handler=received.append,
        )

    ids = [event.event_id for event in received]
    assert len(ids) == len(set(ids))
    # notes-progress-report spec §4 item 1: the four progress types are live-only, so
    # they are exactly the received events the state does not hold.
    progress = {event.event_id for event in received if event.event_type in PROGRESS_TYPES}
    assert set(ids) - progress == {event.event_id for event in outcome.state.events}
    open_node: str | None = None
    for event in received:
        if event.event_type == "graph.node.started":
            assert open_node is None, event.metadata
            open_node = event.metadata["node"]
        elif event.event_type == "graph.node.completed":
            assert open_node == event.metadata["node"]
            open_node = None
        elif event.source.startswith("agent."):
            assert open_node == event.source.removeprefix("agent."), event.event_type
    # The loop below would pass vacuously with no tool calls, or with topics run one by one: pin both.
    assert any(event.event_type == "researcher.tool_call" for event in received)
    first_completed = next(
        index for index, event in enumerate(received) if event.event_type == "researcher.sub_topic.completed"
    )
    assert sum(
        1 for event in received[:first_completed] if event.event_type == "researcher.sub_topic.started"
    ) >= 2, "the topics run concurrently: several start before the first one completes"
    started_at: dict[str, int] = {}
    for index, event in enumerate(received):
        if event.event_type == "researcher.sub_topic.started":
            started_at[event.metadata["sub_topic"]] = index
        if event.event_type == "researcher.tool_call":
            title = event.metadata["sub_topic"]
            assert started_at[title] < index
            assert any(
                later.event_type == "researcher.sub_topic.completed"
                and later.metadata["sub_topic"] == title
                for later in received[index + 1:]
            )


@pytest.mark.parametrize("case_id", REPLAY_CASE_IDS)
def test_topic_findings_sum_matches_research_total(case_id: str, tmp_path: Path) -> None:
    """The spec's §4.3 inference: over every replay case, the findings_retained of a
    pass's completed topics sum to that pass's researcher.research.completed.findings,
    so the Researching subtitle's running total lands on the pass total."""
    outcome = replay_outcome(case_id, tmp_path)
    passes = 0
    retained = 0
    for event in outcome.state.events:
        if event.event_type == "graph.node.started" and event.metadata.get("node") == "researcher":
            retained = 0
        elif event.event_type == "researcher.sub_topic.completed":
            retained += event.metadata["findings_retained"]
        elif event.event_type == "researcher.research.completed":
            assert retained == event.metadata["findings"]
            passes += 1
    if passes == 0:
        pytest.skip(f"{case_id} completes no research pass, so it has no total to compare")



# --- notes-progress-report spec §4 item 1, §6.1, §6.10 ------------------------------


def _strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


@pytest.mark.asyncio
async def test_progress_events_live_only(tmp_path: Path) -> None:
    """AC13 on the real graph: each of the four progress types is published, none is in
    the run's state, no node's ``event_count`` counts one, and no string they carry is a
    URL."""
    received: list[ResearchEvent] = []
    with guarded():
        scenario = scenario_by_id(EXTRA_PASS_CASE)
        runner = ReplayRunner(default_case=EXTRA_PASS_CASE, delay=0.0, root=tmp_path)
        outcome = await runner(
            question=scenario.question, session_id="s1", max_extra_passes=None,
            output_format="markdown", config_overrides={},
            config_path=str(production_config_path()), event_handler=received.append,
        )

    assert {event.event_type for event in received} >= PROGRESS_TYPES
    assert not {event.event_type for event in outcome.state.events} & PROGRESS_TYPES
    node: str | None = None
    own = 0
    for event in received:
        if event.event_type == "graph.node.started":
            node, own = event.metadata["node"], 0
        elif event.source == f"agent.{node}" and event.event_type not in PROGRESS_TYPES:
            own += 1
        elif event.event_type == "graph.node.completed" and event.source.startswith("graph.") and node in {
            "planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer",
        }:
            assert event.metadata["event_count"] == own, node
    for event in received:
        if event.event_type in PROGRESS_TYPES:
            assert all("://" not in text for text in _strings(event.metadata)), event.metadata


def test_parse_hold() -> None:
    """§6.10: ``<event_type>[#<n>]``, n at least 1; anything else holds nothing."""
    assert parse_hold("planner.progress") == ("planner.progress", 1)
    assert parse_hold(" evidence_verifier.progress#2 ") == ("evidence_verifier.progress", 2)
    for bad in ("", "#2", "planner.progress#0", "planner.progress#x", "planner progress", "a#-1"):
        assert parse_hold(bad) is None, bad


def _event(event_type: str, n: int) -> ResearchEvent:
    return ResearchEvent(
        event_type=event_type, source="graph", message=f"m{n}",
        timestamp="2026-01-01T00:00:00+00:00", event_id=f"e{n}",
    )


@pytest.mark.asyncio
async def test_replay_restamps_and_holds(tmp_path: Path) -> None:
    """AC21: each event is published with its release time (its id unchanged), and a
    hold releases events through the n-th of its type, then waits until cancelled."""
    runner = ReplayRunner(default_case=EXTRA_PASS_CASE, delay=0.01, root=tmp_path)
    queue: asyncio.Queue = asyncio.Queue()
    for n, event_type in enumerate(["a", "b", "c", "b", "d"]):
        queue.put_nowait(_event(event_type, n))
    queue.put_nowait(None)
    published: list[ResearchEvent] = []

    drain = asyncio.create_task(runner._drain(queue, published.append, ("b", 2)))
    await asyncio.sleep(0.3)

    assert [event.event_id for event in published] == ["e0", "e1", "e2", "e3"]
    assert all(event.timestamp != "2026-01-01T00:00:00+00:00" for event in published)
    assert [event.timestamp for event in published] == sorted(event.timestamp for event in published)
    assert not drain.done()
    drain.cancel()
    with pytest.raises(asyncio.CancelledError):
        await drain
    assert len(published) == 4


@pytest.mark.asyncio
async def test_published_timestamps_are_the_release_times(tmp_path: Path) -> None:
    """AC21 through the runner: the paced copies span the pacing, while the engine's own
    state keeps the times it ran at."""
    delay = 0.02
    received: list[ResearchEvent] = []
    with guarded():
        scenario = scenario_by_id(EXTRA_PASS_CASE)
        runner = ReplayRunner(default_case=EXTRA_PASS_CASE, delay=delay, root=tmp_path)
        outcome = await runner(
            question=scenario.question, session_id="s1", max_extra_passes=None,
            output_format="markdown", config_overrides={},
            config_path=str(production_config_path()), event_handler=received.append,
        )

    from datetime import datetime

    span = lambda events: (  # noqa: E731
        datetime.fromisoformat(events[-1].timestamp) - datetime.fromisoformat(events[0].timestamp)
    ).total_seconds()
    assert span(received) >= 0.8 * (len(received) - 1) * delay
    assert span(received) > span(outcome.state.events)


def test_hold_after_holds_the_stream_until_the_session_is_stopped(tmp_path: Path) -> None:
    """AC21 through the API (notes-progress-report spec §6.10 item 2): with
    ``X-Replay-Hold-After: graph.report.reviewed`` the stream releases events through the
    first review and nothing after it; the session stays running, on Reviewing, while held;
    ``POST /stop`` (Phase D) ends it there, and ``session.stopped`` is the stream's last frame."""
    with guarded(), TestClient(replay_app(tmp_path, delay=0.01)) as client:
        posted = client.post(
            "/research", json={"query": "q"}, headers={"X-Replay-Hold-After": "graph.report.reviewed"},
        )
        assert posted.status_code == 202
        session_id = posted.json()["session_id"]
        deadline = time.monotonic() + 30
        while client.get(f"/research/{session_id}/status").json()["current_agent"] != "report_reviewer":
            assert time.monotonic() < deadline, "the run never reached Reviewing"
            time.sleep(0.02)
        time.sleep(0.5)  # fifty of the pacer's 10 ms beats: an event released past the hold would show
        held = client.get(f"/research/{session_id}/status").json()
        assert (held["status"], held["current_agent"]) == ("running", "report_reviewer")

        stopped = client.post(f"/research/{session_id}/stop")
        assert stopped.status_code == 202
        assert (stopped.json()["status"], stopped.json()["stopped_step"]) == ("stopped", "report_reviewer")
        names = frames(client.get(f"/research/{session_id}/stream").text)

    assert names.count("graph.report.reviewed") == 1
    assert names[-2:] == ["graph.report.reviewed", "session.stopped"]
