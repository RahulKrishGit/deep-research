"""Replay mode: the real graph runs offline inside the API's own loop."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deep_research.api.app import create_app
from deep_research.api.replay import ReplayCaseMiddleware, ReplayRunner
from deep_research.api.sessions import SessionStore
from deep_research.e2e_evaluation.replay import production_config_path
from deep_research.e2e_evaluation.replay_matrix import scenario_by_id
from tests.test_api.fakes import ScriptedRunner
from tests.test_api.replay_support import EXTRA_PASS_CASE, REVIEW_UNAVAILABLE_CASE, guarded
from tests.test_api.test_app import valid_preflight, wait_until_terminal


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
