"""Stop (notes-progress-report spec §8; D17, D26, D33): one session ends at once.

The store tests drive ``SessionStore`` with scripted runners and checkers; the rule tests
read the captured replays the web app's own tests read; the replay test runs a scripted
case offline. No provider is ever reached.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from deep_research.api.app import create_app
from deep_research.api.models import ClarificationAnswersRequest
from deep_research.api.notes import NoteInterpretation
from deep_research.api.replay import ReplayRunner
from deep_research.api.sessions import (
    NotesClosed,
    NotStoppable,
    NotWaitingForInput,
    SessionStore,
    session_note_fields,
)
from deep_research.api.stop import active_row
from deep_research.e2e_evaluation.replay import production_config_path
from deep_research.e2e_evaluation.replay_matrix import scenario_by_id
from deep_research.runtime.outcome import ResearchOutcome
from deep_research.utils.config import ConfigSettings, HitlConfig
from deep_research.utils.types import ResearchEvent
from tests.test_api.fakes import GateRunner, ScriptedRunner, make_outcome
from tests.test_api.replay_support import EXTRA_PASS_CASE, guarded
from tests.test_api.test_app import valid_preflight, wait_until_terminal
from tests.test_api.test_clarification import Checker
from tests.test_api.test_replay import frames

QUESTION = "What limits grid-scale battery storage?"
HITL = HitlConfig(check_timeout_s=0.5, answer_wait_s=5.0, note_interpret_timeout_s=0.2)
WEB_FIXTURES = Path(__file__).resolve().parents[2] / "web" / "test" / "fixtures"


def _start(store: SessionStore, *, ask: bool = False) -> None:
    store.start(
        session_id="s1", query=QUESTION, max_extra_passes=None, output_format="markdown",
        config_overrides={}, config_path="config.yaml", ask_clarifying_questions=ask,
        settings=ConfigSettings(), hitl=HITL,
    )


async def _until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


def _event(event_type: str, **metadata: Any) -> ResearchEvent:
    return ResearchEvent(event_type=event_type, source="graph", message="Event.", metadata=metadata)


def _types(events: list[ResearchEvent]) -> list[str]:
    return [event.event_type for event in events]


# --- the store (spec §8.2) -------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_running_session_stops_at_once_and_says_where() -> None:
    """AC28's store half: the step read from what the session published, ``session.stopped``
    as its last event, the terminal status, ``finished_at`` and the cancelled task — all
    before ``stop`` returns control; a subscriber's stream closes after ``session.stopped``;
    a second stop is refused as ``finished``."""
    runner = GateRunner()
    store = SessionStore(runner=runner)
    _start(store)
    session = store.require("s1")
    await runner.started.wait()
    received: list[ResearchEvent] = []

    async def follow() -> None:
        async for event in store.iter_events("s1"):
            received.append(event)

    follower = asyncio.create_task(follow())
    await asyncio.sleep(0)

    assert store.stop("s1") is session

    assert (session.status, session.stopped_step, session.current_agent) == ("stopped", "planner", None)
    stopped_at = session.finished_at
    assert stopped_at is not None
    last = session.events[-1]
    assert (last.event_type, last.source, last.message) == (
        "session.stopped", "api", "The reader stopped the research.",
    )
    assert last.metadata == {
        "step": "planner",
        "stopped_at": stopped_at.isoformat(timespec="seconds"),
        "elapsed_seconds": int((stopped_at - session.started_at).total_seconds()),
    }
    assert session.task is not None
    await asyncio.wait({session.task}, timeout=1)
    assert session.task.cancelled()
    assert (session.status, session.finished_at) == ("stopped", stopped_at)
    await asyncio.wait_for(follower, timeout=1)
    assert _types(received) == ["graph.node.started", "session.stopped"]
    assert (session.outcome, session.report_path, session.errors) == (None, None, [])
    with pytest.raises(NotStoppable) as again:
        store.stop("s1")
    assert again.value.reason == "finished"


@pytest.mark.asyncio
async def test_stop_during_needs_input() -> None:
    """A stop while the one-time check waits for the reader (D33): step ``check``, the check's
    pending answers cancelled, the runner never called, and the run's close-out keeps both the
    status and the time of the stop; answers and notes are refused afterwards (AC31's store
    half); the check it asked is kept for the status."""
    runner = ScriptedRunner()
    store = SessionStore(runner=runner, clarity_checker=Checker())
    _start(store, ask=True)
    session = store.require("s1")
    await _until(lambda: session.status == "needs_input")
    pending = session.clarification
    assert pending is not None

    store.stop("s1")

    assert (session.status, session.stopped_step, session.clarification) == ("stopped", "check", None)
    assert pending.submitted.cancelled()
    stopped_at = session.finished_at
    assert session.task is not None
    await asyncio.wait({session.task}, timeout=1)
    assert session.task.cancelled()
    assert (session.status, session.finished_at) == ("stopped", stopped_at)
    assert _types(session.events) == ["session.clarification.requested", "session.stopped"]
    assert session.events[-1].metadata["step"] == "check"
    assert runner.calls == []
    assert session.check is not None and session.check.answers == ()
    with pytest.raises(NotWaitingForInput):
        store.submit_answers("s1", ClarificationAnswersRequest(answers=[], skip=True))
    with pytest.raises(NotesClosed):
        store.add_note("s1", "too late")


@pytest.mark.asyncio
async def test_publish_after_stop_dropped() -> None:
    """§8.2: a stopped session takes no more events, so ``session.stopped`` stays last whatever a
    task finishing its own cancellation still hands over — a replay's pacer, say — and nothing
    it drops can close the notes or wake a subscriber."""
    runner = GateRunner()
    store = SessionStore(runner=runner)
    _start(store)
    session = store.require("s1")
    await runner.started.wait()
    store.stop("s1")
    session.changed.clear()

    session.publish(_event("graph.node.completed", node="planner", iteration=0))
    session.publish(_event("graph.route.decided", destination="finalize", reason="report_accepted", iteration=0))

    assert _types(session.events) == ["graph.node.started", "session.stopped"]
    assert (session.notes_closed, session.changed.is_set()) == (False, False)
    assert session.task is not None
    await asyncio.wait({session.task}, timeout=1)


@pytest.mark.asyncio
async def test_a_stop_is_refused_once_the_session_has_ended_is_publishing_or_the_service_is_closing() -> None:
    """§8.1's 409 reasons, in this plan's order: ``finished`` (completed, failed), ``publishing``
    (the route decided to publish), ``closing`` (the store is shutting down), and ``KeyError``
    for an unknown id (AC30's store half). A refused stop changes nothing."""
    done = SessionStore(runner=ScriptedRunner())
    _start(done)
    failed = SessionStore(runner=ScriptedRunner(error=RuntimeError("boom")))
    _start(failed)
    for store in (done, failed):
        task = store.require("s1").task
        assert task is not None
        await task
        with pytest.raises(NotStoppable) as finished:
            store.stop("s1")
        assert finished.value.reason == "finished"

    runner = GateRunner()
    store = SessionStore(runner=runner)
    _start(store)
    session = store.require("s1")
    await runner.started.wait()
    session.publish(_event("graph.route.decided", destination="finalize", reason="report_accepted", iteration=0))
    with pytest.raises(NotStoppable) as publishing:
        store.stop("s1")
    assert publishing.value.reason == "publishing"
    assert (session.status, _types(session.events)[-1]) == ("running", "graph.route.decided")
    with pytest.raises(KeyError):
        store.stop("missing")
    runner.release.set()
    await store.close()

    seen: list[str] = []
    stores: list[SessionStore] = []

    class Closing(GateRunner):
        async def __call__(self, **kwargs: Any) -> ResearchOutcome:
            try:
                return await super().__call__(**kwargs)
            except asyncio.CancelledError:
                try:
                    stores[0].stop("s1")
                except NotStoppable as refusal:
                    seen.append(refusal.reason)
                raise

    closing = Closing()
    shutting = SessionStore(runner=closing)
    stores.append(shutting)
    _start(shutting)
    await closing.started.wait()
    await shutting.close()
    assert seen == ["closing"]
    with pytest.raises(NotStoppable) as after:
        shutting.stop("s1")
    assert after.value.reason == "finished"


@pytest.mark.asyncio
async def test_a_stopped_sessions_notes_read_not_checked_and_a_reading_in_flight_is_dropped() -> None:
    """§8.4: a stopped session keeps the notes it took, each ``not_checked`` (§4 item 2); a note
    still being read is cancelled with the run, so nothing waits on the board; no note is taken
    afterwards."""

    class SecondNoteHangs:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self, text: str, question: str, earlier: Any, settings: object) -> NoteInterpretation:
            self.calls += 1
            if self.calls == 2:
                await asyncio.Event().wait()
            return NoteInterpretation(kinds=["emphasis"], restatement=text)

    runner = GateRunner()
    store = SessionStore(runner=runner, note_interpreter=SecondNoteHangs())
    _start(store)
    session = store.require("s1")
    await runner.started.wait()
    store.add_note("s1", "first")
    await _until(lambda: len(session.note_board.snapshot()) == 1)
    store.add_note("s1", "second")
    await asyncio.sleep(0.02)
    readings = set(session.note_tasks)
    assert len(readings) == 1

    store.stop("s1")
    assert session.task is not None
    await asyncio.wait({session.task, *readings}, timeout=1)
    await asyncio.sleep(0)

    assert session.note_tasks == set()
    assert session.note_board.pending == ()
    assert [note.note_id for note in session.note_board.snapshot()] == ["n1"]
    fields = session_note_fields(session)
    assert [(note.note_id, note.restatement, note.outcome) for note in fields["notes"]] == [
        ("n1", "first", "not_checked"), ("n2", None, "not_checked"),
    ]
    assert fields["notes_remaining"] == 8
    with pytest.raises(NotesClosed):
        store.add_note("s1", "third")


@pytest.mark.asyncio
async def test_a_run_that_swallows_its_cancellation_keeps_the_stop() -> None:
    """A runner that catches the stop's cancellation and returns an outcome anyway, or fails while
    it unwinds, cannot turn a stopped session into a finished or failed one: no outcome, no error,
    ``session.stopped`` last (spec ambiguity 6)."""

    class Stubborn(GateRunner):
        def __init__(self, *, fail: bool) -> None:
            super().__init__()
            self.fail = fail

        async def __call__(self, **kwargs: Any) -> ResearchOutcome:
            try:
                return await super().__call__(**kwargs)
            except asyncio.CancelledError:
                if self.fail:
                    raise RuntimeError("cleanup failed") from None
                return make_outcome(status="completed", report="# A report written anyway")

    for fail in (False, True):
        runner = Stubborn(fail=fail)
        store = SessionStore(runner=runner)
        _start(store)
        session = store.require("s1")
        await runner.started.wait()
        store.stop("s1")
        assert session.task is not None
        await asyncio.wait({session.task}, timeout=1)

        assert (session.status, session.outcome, session.report_path, session.errors) == ("stopped", None, None, [])
        assert _types(session.events)[-1] == "session.stopped"


# --- the step a stop records (spec §8.2 step 2, §4 item 3; AC32) -------------------------


def test_active_row_follows_the_consoles_rule() -> None:
    """``active_row`` is ``run.active`` (web/lib/run-state.ts; DESIGN.md §3.5) on the cases the
    captured replays do not reach: a hop, the reviewer's own completion, every route destination,
    a halt's skipped rows, Publishing's completion, the session's completion, an unknown node."""

    def row(*events: ResearchEvent) -> str | None:
        return active_row(events)

    planned = _event("graph.node.completed", node="planner")
    assert row() == "planner"
    assert row(_event("graph.node.started", node="planner", iteration=0)) == "planner"
    assert row(planned) == "researcher"
    assert row(_event("graph.node.completed", node="report_writer")) == "report_reviewer"
    assert row(_event("graph.node.completed", node="report_writer"), _event("graph.node.completed", node="report_reviewer")) == "report_reviewer"
    for hop in ("extra_pass", "note_pass", "writer_redraft"):
        assert row(_event("graph.route.decided", destination="redraft"), _event("graph.node.completed", node=hop)) == "report_writer"
    assert [row(_event("graph.route.decided", destination=d)) for d in ("extra_pass", "note_pass", "redraft", "finalize", "end")] == [
        "researcher", "researcher", "report_writer", "finalize_report", None,
    ]
    assert row(planned, _event("graph.node.skipped", node="source_evaluator", iteration=0)) == "researcher"
    assert row(planned, _event("graph.node.skipped", node="researcher", iteration=0)) is None
    assert row(_event("graph.node.completed", node="finalize_report")) is None
    assert row(planned, _event("graph.session.completed", status="completed")) is None
    assert row(_event("graph.node.completed", node="unknown_node")) is None


def test_active_row_matches_web_rule() -> None:
    """AC32: after every prefix of every captured replay, ``active_row`` names the row the page
    shows as active. ``web/test/fixtures/active-rows.json`` is the page's own rule run over the
    same captures — ``web/test/active-row.test.ts`` recomputes it on every Vitest run — so the
    API and the page cannot disagree about the step a stop records."""
    golden = json.loads((WEB_FIXTURES / "active-rows.json").read_text(encoding="utf-8"))
    captured = sorted(path.stem for path in (WEB_FIXTURES / "events").glob("*.json"))
    assert sorted(golden) == captured
    for case_id in captured:
        record = json.loads((WEB_FIXTURES / "events" / f"{case_id}.json").read_text(encoding="utf-8"))
        events = [ResearchEvent.model_validate(raw) for raw in record["events"]]
        expected = [row for row, count in golden[case_id] for _ in range(count)]
        assert [active_row(events[:k]) for k in range(len(events) + 1)] == expected, case_id


@pytest.mark.asyncio
async def test_replay_stop_mid_stream(tmp_path: Path) -> None:
    """AC32 on the replay runner: replay runs the engine ahead of its paced stream, and a stop
    records the row the page shows at that moment — read from what the session has published,
    not from the engine — and the pacer publishes nothing after ``session.stopped``."""
    with guarded():
        scenario = scenario_by_id(EXTRA_PASS_CASE)
        store = SessionStore(runner=ReplayRunner(default_case=EXTRA_PASS_CASE, delay=0.05, root=tmp_path))
        store.start(
            session_id="s1", query=scenario.question, max_extra_passes=None, output_format="markdown",
            config_overrides={}, config_path=str(production_config_path()),
        )
        session = store.require("s1")
        # The first topic's start is event 8 of 72; Researching lasts until event 28, a second away.
        await _until(lambda: "researcher.sub_topic.started" in _types(session.events), timeout=15)
        before = list(session.events)
        store.stop("s1")
        assert session.task is not None
        await asyncio.wait({session.task}, timeout=2)
        await asyncio.sleep(0.3)  # six of the pacer's 50 ms beats: anything it still held would show

    assert session.task.cancelled()
    assert session.stopped_step == active_row(before) == "researcher"
    assert [event.event_id for event in session.events[:-1]] == [event.event_id for event in before]
    assert _types(session.events)[-1] == "session.stopped"
    assert (session.status, session.outcome) == ("stopped", None)


# --- the route (spec §8.1, §8.4) -------------------------------------------------------


def _status(client: TestClient, session_id: str) -> dict[str, Any]:
    return client.get(f"/research/{session_id}/status").json()


def _wait(client: TestClient, session_id: str, predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        body = _status(client, session_id)
        if predicate(body):
            return body
        time.sleep(0.01)
    raise AssertionError("status never matched")


def _error(response: Any) -> tuple[int, str, str | None]:
    error = response.json()["error"]
    return response.status_code, error["code"], error["reason"]


def test_stop_route_codes() -> None:
    """AC28, AC30, AC31 through the route: a running session stops with 202 and the stopped
    session; its stream ends with ``session.stopped``; /status, /trace and the list name it;
    its note reads ``not_checked``; /report and /evidence (both formats) answer 409 as a halted
    run does, a note 409 ``notes_closed``, answers 409 ``not_waiting_for_input``; a second stop
    is 409 ``not_stoppable`` ``finished``; an unknown id is 404."""
    runner = GateRunner()
    app = create_app(runner=runner, preflight=valid_preflight)
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        _wait(client, session_id, lambda body: body["current_agent"] == "planner")
        client.post(f"/research/{session_id}/notes", json={"text": "Pumped hydro"})
        _wait(client, session_id, lambda body: body["notes"][0]["restatement"] is not None)
        stopped = client.post(f"/research/{session_id}/stop")
        # Checked before the stream is read: a session that did not stop never ends its stream.
        assert stopped.status_code == 202, stopped.text
        stream = client.get(f"/research/{session_id}/stream").text
        status = _status(client, session_id)
        trace = client.get(f"/research/{session_id}/trace").json()
        listed = client.get("/research").json()["sessions"]
        later = {
            "report": client.get(f"/research/{session_id}/report"),
            "evidence": client.get(f"/research/{session_id}/evidence"),
            "evidence_md": client.get(f"/research/{session_id}/evidence?format=markdown"),
            "note": client.post(f"/research/{session_id}/notes", json={"text": "too late"}),
            "answers": client.post(f"/research/{session_id}/answers", json={"answers": [], "skip": True}),
            "again": client.post(f"/research/{session_id}/stop"),
        }
        unknown = client.post("/research/missing/stop")

    body = stopped.json()
    assert (body["status"], body["stopped_step"], body["current_agent"]) == ("stopped", "planner", None)
    assert body["finished_at"] is not None
    assert frames(stream)[-1] == "session.stopped"
    assert (status["status"], status["stopped_step"], status["finished_at"]) == ("stopped", "planner", body["finished_at"])
    assert [(note["note_id"], note["outcome"]) for note in status["notes"]] == [("n1", "not_checked")]
    assert trace["metadata"]["status"] == "stopped"
    assert [(item["session_id"], item["status"], item["stopped_step"]) for item in listed] == [
        (session_id, "stopped", "planner"),
    ]
    assert {name: _error(response) for name, response in later.items()} == {
        "report": (409, "report_unavailable", None),
        "evidence": (409, "evidence_unavailable", None),
        "evidence_md": (409, "evidence_unavailable", None),
        "note": (409, "notes_closed", None),
        "answers": (409, "not_waiting_for_input", None),
        "again": (409, "not_stoppable", "finished"),
    }
    assert later["again"].json()["error"]["message"] == "Research session can no longer be stopped."
    assert _error(unknown) == (404, "session_not_found", None)


def test_the_stop_route_says_why_it_refuses() -> None:
    """AC30: 409 ``not_stoppable`` with ``finished`` once a run has completed or failed,
    ``publishing`` once the route decided to publish, ``closing`` while the store shuts down."""
    with TestClient(create_app(runner=ScriptedRunner(), preflight=valid_preflight)) as client:
        completed_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        wait_until_terminal(client, completed_id)
        completed = client.post(f"/research/{completed_id}/stop")
    failing = create_app(runner=ScriptedRunner(error=RuntimeError("boom")), preflight=valid_preflight)
    with TestClient(failing) as client:
        failed_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        wait_until_terminal(client, failed_id)
        failed = client.post(f"/research/{failed_id}/stop")
    runner = GateRunner()
    app = create_app(runner=runner, preflight=valid_preflight)
    store = app.state.session_store
    with TestClient(app) as client:
        publishing_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        closing_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        for session_id in (publishing_id, closing_id):
            _wait(client, session_id, lambda body: body["current_agent"] == "planner")
        client.portal.call(
            store.require(publishing_id).publish,
            _event("graph.route.decided", destination="finalize", reason="report_accepted", iteration=0),
        )
        publishing = client.post(f"/research/{publishing_id}/stop")
        store._closing = True
        closing = client.post(f"/research/{closing_id}/stop")
        store._closing = False
        client.portal.call(runner.release.set)

    assert [_error(response) for response in (completed, failed, publishing, closing)] == [
        (409, "not_stoppable", "finished"),
        (409, "not_stoppable", "finished"),
        (409, "not_stoppable", "publishing"),
        (409, "not_stoppable", "closing"),
    ]


def test_a_stop_while_the_check_waits_answers_202_and_refuses_the_answers() -> None:
    """AC28 for a session in ``needs_input`` (D33's step ``check``) and AC31's answers half; the
    check's questions stay on the status, answered by nobody."""
    app = create_app(runner=GateRunner(), preflight=valid_preflight, clarity_checker=Checker())
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        _wait(client, session_id, lambda body: body["status"] == "needs_input")
        stopped = client.post(f"/research/{session_id}/stop")
        answers = client.post(f"/research/{session_id}/answers", json={"answers": [], "skip": True})
        status = _status(client, session_id)

    assert stopped.status_code == 202
    assert (stopped.json()["status"], stopped.json()["stopped_step"]) == ("stopped", "check")
    assert _error(answers) == (409, "not_waiting_for_input", None)
    assert [question["id"] for question in status["clarification"]["questions"]] == ["q1", "q2", "q3"]
    assert status["clarification"]["answers"] == []
