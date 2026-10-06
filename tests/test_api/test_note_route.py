"""The notes route and a session's notes.

Store-level tests drive ``SessionStore`` with scripted interpreters and a runner
held open; the route tests go through ``TestClient``. No provider is ever reached:
the package's ``live_note_calls`` fixture replaces the live interpreter.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from deep_research.api.app import create_app
from deep_research.api.notes import (
    NoteInterpretation,
    fallback_interpretation,
    reader_note,
    scripted_note_interpreter,
)
from deep_research.api.sessions import NotesClosed, SessionStore, session_note_fields
from deep_research.runtime.notes import NoteBoard, NoteLimitReached, current_note_board
from deep_research.runtime.outcome import ResearchOutcome
from deep_research.utils.config import ConfigSettings, HitlConfig
from deep_research.utils.types import ResearchEvent, ResearchState
from tests.graph_fakes import fake_report_review
from tests.test_api.fakes import GateRunner, make_outcome
from tests.test_api.test_app import valid_preflight
from tests.test_api.test_clarification import Checker
from tests.test_api.test_replay import replay_app

QUESTION = "What limits grid-scale battery storage?"
FAST = HitlConfig(note_interpret_timeout_s=0.2)


class HeldRunner:
    """A run held open until released; it records the board it was bound to and
    ends with every note the board holds judged by ``verdicts`` (default honoured)."""

    def __init__(self, verdicts: Mapping[str, str] | None = None, *, note_passes: int = 0) -> None:
        self.verdicts = dict(verdicts or {})
        self.note_passes = note_passes
        self.release = asyncio.Event()
        self.board: NoteBoard | None = None

    async def __call__(
        self, *, question: str, session_id: str,
        event_handler: Callable[[ResearchEvent], None] | None = None, **kwargs: Any,
    ) -> ResearchOutcome:
        self.board = current_note_board()
        if event_handler is not None:
            event_handler(ResearchEvent(
                event_type="graph.node.started", source="graph.researcher", message="Node researcher started.",
                metadata={"node": "researcher", "iteration": 0},
            ))
            for number in range(1, self.note_passes + 1):
                event_handler(ResearchEvent(
                    event_type="graph.note_pass.started", source="graph", message="Note pass started.",
                    metadata={"iteration": 0, "note_passes": number, "note_ids": [], "targets": []},
                ))
        await self.release.wait()
        notes = self.board.snapshot() if self.board is not None else []
        state = ResearchState(
            session_id=session_id, original_question=question, reader_notes=notes,
            note_passes=self.note_passes,
            report_review=fake_report_review(
                note_dispositions={note.note_id: self.verdicts.get(note.note_id, "honoured") for note in notes}
            ),
        )
        return make_outcome(session_id=session_id, question=question, state=state)


class Interpreter:
    """A scripted ``NoteInterpreter``: reads, raises, or hangs; records each call."""

    def __init__(self, reply: NoteInterpretation | BaseException | None = None, *, hang: bool = False) -> None:
        self.reply, self.hang = reply, hang
        self.calls: list[tuple[str, str, list[str]]] = []

    async def __call__(self, text: str, question: str, earlier: Any, settings: object) -> NoteInterpretation:
        self.calls.append((text, question, [note.note_id for note in earlier]))
        if self.hang:
            await asyncio.Event().wait()
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply or NoteInterpretation(kinds=["exclude"], restatement=f"leave out {text.lower()}")


def _start(store: SessionStore) -> None:
    store.start(
        session_id="s1", query=QUESTION, max_extra_passes=None, output_format="markdown",
        config_overrides={}, config_path="config.yaml", ask_clarifying_questions=False,
        settings=ConfigSettings(), hitl=FAST,
    )


async def _until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


def _types(events: list[ResearchEvent]) -> list[str]:
    return [event.event_type for event in events]


# --- the store ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_note_is_published_at_once_then_read_onto_the_boards_the_run_holds() -> None:
    runner, interpreter = HeldRunner(), Interpreter()
    store = SessionStore(runner=runner, note_interpreter=interpreter)
    _start(store)
    session = store.require("s1")
    await _until(lambda: runner.board is not None)

    received = store.add_note("s1", "Pumped hydro")

    assert (received.note_id, received.text, received.received_during) == ("n1", "Pumped hydro", "researcher")
    assert _types(session.events)[-1] == "session.note.received"
    await _until(lambda: _types(session.events)[-1] == "session.note.interpreted")
    assert session.events[-1].metadata == {
        "note_id": "n1", "restatement": "leave out pumped hydro", "kinds": ["exclude"],
        "replaces": None, "fallback": False,
    }
    assert runner.board is session.note_board
    assert [note.note_id for note in session.note_board.snapshot()] == ["n1"]
    assert interpreter.calls == [("Pumped hydro", QUESTION, [])]
    store.add_note("s1", "Only 2024")
    await _until(lambda: len(session.note_board.snapshot()) == 2)
    assert interpreter.calls[-1] == ("Only 2024", QUESTION, ["n1"])
    runner.release.set()
    await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "interpreter",
    [Interpreter(RuntimeError("provider down")), Interpreter(hang=True), None],
    ids=["fails", "hangs", "missing"],
)
async def test_a_failed_slow_or_missing_interpreter_keeps_the_note_as_written(
    interpreter: Interpreter | None,
) -> None:
    runner = HeldRunner()
    store = SessionStore(runner=runner, note_interpreter=interpreter)
    _start(store)
    session = store.require("s1")
    await _until(lambda: runner.board is not None)
    began = time.monotonic()

    store.add_note("s1", "Mostly the US, please.")
    await _until(lambda: _types(session.events)[-1] == "session.note.interpreted")

    assert time.monotonic() - began < FAST.note_interpret_timeout_s + 1.0
    assert session.events[-1].metadata == {
        "note_id": "n1", "restatement": "Mostly the US, please.", "kinds": ["emphasis"],
        "replaces": None, "fallback": True,
    }
    assert session.note_board.snapshot()[0].restatement == "Mostly the US, please."
    runner.release.set()
    await store.close()


@pytest.mark.asyncio
async def test_notes_close_while_the_session_waits_once_publishing_begins_and_once_it_ends() -> None:
    runner = HeldRunner()
    store = SessionStore(runner=runner, note_interpreter=Interpreter())
    _start(store)
    session = store.require("s1")
    await _until(lambda: runner.board is not None)

    session.status = "needs_input"
    with pytest.raises(NotesClosed):
        store.add_note("s1", "too early")
    session.status = "running"
    session.publish(ResearchEvent(
        event_type="graph.route.decided", source="graph", message="Publish.",
        metadata={"destination": "finalize", "reason": "report_accepted", "iteration": 0},
    ))
    with pytest.raises(NotesClosed):
        store.add_note("s1", "too late")
    assert session.note_board.accepted == 0
    with pytest.raises(KeyError):
        store.add_note("missing", "who?")
    runner.release.set()
    await _until(lambda: session.finished_at is not None)
    with pytest.raises(NotesClosed):
        store.add_note("s1", "after the end")


@pytest.mark.asyncio
async def test_a_route_decision_that_loops_back_keeps_the_notes_open() -> None:
    runner = HeldRunner()
    store = SessionStore(runner=runner, note_interpreter=Interpreter())
    _start(store)
    session = store.require("s1")
    await _until(lambda: runner.board is not None)

    for destination in ("note_pass", "extra_pass", "redraft"):
        session.publish(ResearchEvent(
            event_type="graph.route.decided", source="graph", message="Loop.",
            metadata={"destination": destination, "reason": "note_pass_requested", "iteration": 0},
        ))

    assert store.add_note("s1", "still open").note_id == "n1"
    runner.release.set()
    await store.close()


@pytest.mark.asyncio
async def test_the_eleventh_note_is_refused_and_a_refusal_is_never_counted() -> None:
    runner = HeldRunner()
    store = SessionStore(runner=runner, note_interpreter=Interpreter())
    _start(store)
    session = store.require("s1")
    await _until(lambda: runner.board is not None)

    ids = [store.add_note("s1", f"note {number}").note_id for number in range(1, 11)]

    assert ids == [f"n{number}" for number in range(1, 11)]
    with pytest.raises(NoteLimitReached):
        store.add_note("s1", "one too many")
    assert session.note_board.accepted == 10 and session.note_board.remaining == 0
    await _until(lambda: session.note_board.pending == ())
    runner.release.set()
    await store.close()


@pytest.mark.asyncio
async def test_closing_the_store_drops_a_note_still_being_read() -> None:
    runner = HeldRunner()
    store = SessionStore(runner=runner, note_interpreter=Interpreter(hang=True))
    _start(store)
    session = store.require("s1")
    await _until(lambda: runner.board is not None)
    store.add_note("s1", "never read")
    await asyncio.sleep(0.02)

    await store.close()

    assert session.note_tasks == set()
    assert session.note_board.pending == ()
    assert session.note_board.snapshot() == []
    await asyncio.wait_for(session.note_board.settled(), timeout=1)


@pytest.mark.asyncio
async def test_a_note_is_read_with_only_the_notes_numbered_before_it() -> None:
    """Two notes sent in one breath: the first is read alone, the second after the first."""
    runner, interpreter = HeldRunner(), Interpreter()
    store = SessionStore(runner=runner, note_interpreter=interpreter)
    _start(store)
    session = store.require("s1")
    await _until(lambda: runner.board is not None)

    store.add_note("s1", "first")
    store.add_note("s1", "second")
    await _until(lambda: len(session.note_board.snapshot()) == 2)

    assert [earlier for _text, _question, earlier in interpreter.calls] == [[], ["n1"]]
    runner.release.set()
    await store.close()


@pytest.mark.asyncio
async def test_a_note_never_reads_a_later_note_even_if_that_one_was_read_first() -> None:
    """The store does not lean on scheduling: whatever the board holds, only earlier notes go in."""
    runner, interpreter = HeldRunner(), Interpreter()
    store = SessionStore(runner=runner, note_interpreter=interpreter)
    _start(store)
    session = store.require("s1")
    await _until(lambda: runner.board is not None)
    first = session.note_board.receive("first", received_at="2026-09-29T10:00:00.000+00:00", received_during="planner")
    second = session.note_board.receive("second", received_at="2026-09-29T10:00:01.000+00:00", received_during="planner")
    session.note_board.add(reader_note(second, fallback_interpretation("second")))

    await store._interpret_note(session, first)

    assert interpreter.calls == [("first", QUESTION, [])]
    assert [note.note_id for note in session.note_board.snapshot()] == ["n1", "n2"]
    runner.release.set()
    await store.close()


@pytest.mark.asyncio
async def test_closing_the_store_drops_a_note_whose_reading_had_not_begun() -> None:
    """A task cancelled before its first step never runs its own handler: the board still settles."""
    runner = HeldRunner()
    store = SessionStore(runner=runner, note_interpreter=Interpreter())
    _start(store)
    session = store.require("s1")
    await _until(lambda: runner.board is not None)
    store.add_note("s1", "cancelled before it starts")

    await store.close()

    assert session.note_tasks == set()
    assert session.note_board.pending == ()
    await asyncio.wait_for(session.note_board.settled(), timeout=1)


@pytest.mark.asyncio
async def test_no_note_is_taken_once_the_store_is_closing() -> None:
    """Shutdown: a note posted while ``close()`` cancels the run — the session still reads
    ``running`` with no ``finished_at`` — would outlive ``close()``, so it is refused."""
    refused: list[bool] = []
    store_ref: list[SessionStore] = []

    class Runner(HeldRunner):
        async def __call__(self, **kwargs: Any) -> ResearchOutcome:
            try:
                return await super().__call__(**kwargs)
            except asyncio.CancelledError:
                session = store_ref[0].require("s1")
                assert session.status == "running" and session.finished_at is None
                try:
                    store_ref[0].add_note("s1", "during shutdown")
                except NotesClosed:
                    refused.append(True)
                raise

    runner = Runner()
    store = SessionStore(runner=runner, note_interpreter=Interpreter())
    store_ref.append(store)
    _start(store)
    session = store.require("s1")
    await _until(lambda: runner.board is not None)

    await store.close()

    assert refused == [True]
    assert session.note_board.accepted == 0
    assert session.note_tasks == set()
    with pytest.raises(NotesClosed):
        store.add_note("s1", "after shutdown")


@pytest.mark.asyncio
async def test_a_timeout_firing_inside_the_run_does_not_refuse_a_note() -> None:
    """A timeout that fires in the run's own task (a provider read, the one-time check, the note
    settle) leaves ``task.cancelling()`` above zero until the task resumes; a note arriving then is
    not a shutdown and is accepted."""
    seen: list[tuple[int, str]] = []
    store_ref: list[SessionStore] = []

    class Runner(HeldRunner):
        async def __call__(self, **kwargs: Any) -> ResearchOutcome:
            loop, task = asyncio.get_running_loop(), asyncio.current_task()
            assert task is not None

            def note_arrives() -> None:
                try:
                    received = store_ref[0].add_note("s1", "a note while a read timed out")
                except NotesClosed:
                    seen.append((task.cancelling(), "refused"))
                else:
                    seen.append((task.cancelling(), received.note_id))

            try:
                async with asyncio.timeout(None) as timeout:
                    # The timeout fires first and the note arrives right behind it, in the same
                    # loop iteration: the run's task has been asked to cancel but has not yet
                    # resumed to leave the ``async with`` and uncancel itself.
                    timeout.reschedule(loop.time())
                    loop.call_soon(note_arrives)
                    await asyncio.sleep(5)
            except TimeoutError:
                pass
            return await super().__call__(**kwargs)

    runner = Runner()
    store = SessionStore(runner=runner, note_interpreter=Interpreter())
    store_ref.append(store)
    _start(store)
    session = store.require("s1")
    await _until(lambda: bool(seen))

    assert seen == [(1, "n1")]
    assert session.note_board.accepted == 1
    await _until(lambda: _types(session.events)[-1] == "session.note.interpreted")
    runner.release.set()
    await store.close()


# --- the route ---------------------------------------------------------------------------


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


def test_the_notes_route_takes_a_note_and_the_status_lists_it_with_its_outcome() -> None:
    """Every note, its reading and its outcome are listed on the status with the pass count."""
    runner = HeldRunner({"n2": "no_evidence", "n3": "ignored_with_evidence"}, note_passes=1)
    app = create_app(runner=runner, preflight=valid_preflight, note_interpreter=Interpreter())
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        first = client.post(f"/research/{session_id}/notes", json={"text": "  Pumped hydro  "})
        second = client.post(f"/research/{session_id}/notes", json={"text": "Flow batteries"})
        third = client.post(f"/research/{session_id}/notes", json={"text": "Grid codes"})
        running = _wait(client, session_id, lambda body: all(n["restatement"] for n in body["notes"]))
        client.portal.call(runner.release.set)
        done = _wait(client, session_id, lambda body: body["status"] == "completed")
        closed = client.post(f"/research/{session_id}/notes", json={"text": "after the end"})

    assert (first.status_code, first.json()) == (202, {"note_id": "n1", "status": "received"})
    assert (second.json()["note_id"], third.json()["note_id"]) == ("n2", "n3")
    assert running["notes"] == [
        {"note_id": "n1", "text": "Pumped hydro", "restatement": "leave out pumped hydro", "outcome": "pending",
         "steering_outcome": None},
        {"note_id": "n2", "text": "Flow batteries", "restatement": "leave out flow batteries", "outcome": "pending",
         "steering_outcome": None},
        {"note_id": "n3", "text": "Grid codes", "restatement": "leave out grid codes", "outcome": "pending",
         "steering_outcome": None},
    ]
    assert (running["notes_remaining"], running["note_passes"], running["clarification"]) == (7, 1, None)
    # A note the report still ignores is reported as such, never as covered.
    assert [(n["note_id"], n["outcome"]) for n in done["notes"]] == [
        ("n1", "covered"), ("n2", "not_found"), ("n3", "not_addressed"),
    ]
    assert (done["notes_remaining"], done["note_passes"]) == (7, 1)
    assert closed.status_code == 409
    assert closed.json()["error"]["code"] == "notes_closed"


def test_the_notes_route_refuses_unknown_sessions_bad_text_and_the_eleventh_note() -> None:
    """The eleventh note is a 409 ``note_limit_reached``."""
    runner = GateRunner()
    app = create_app(runner=runner, preflight=valid_preflight, note_interpreter=Interpreter())
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        unknown = client.post("/research/missing/notes", json={"text": "hello"})
        empty = client.post(f"/research/{session_id}/notes", json={"text": "   "})
        long = client.post(f"/research/{session_id}/notes", json={"text": "x" * 501})
        accepted = [client.post(f"/research/{session_id}/notes", json={"text": f"note {n}"}) for n in range(10)]
        eleventh = client.post(f"/research/{session_id}/notes", json={"text": "one too many"})
        status = _status(client, session_id)
        client.portal.call(runner.release.set)

    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "session_not_found"
    assert (empty.status_code, long.status_code) == (422, 422)
    assert [response.status_code for response in accepted] == [202] * 10
    assert eleventh.status_code == 409
    assert eleventh.json()["error"] == {
        "code": "note_limit_reached", "message": "Research session takes no more notes.", "issues": [], "reason": None,
    }
    assert status["notes_remaining"] == 0 and len(status["notes"]) == 10


def test_a_session_without_notes_answers_the_new_fields_empty(live_note_calls: list[str]) -> None:
    app = create_app(runner=GateRunner(), preflight=valid_preflight)
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        body = _status(client, session_id)
        posted = client.post(f"/research/{session_id}/notes", json={"text": "read me"})
        _wait(client, session_id, lambda status: status["notes"][0]["restatement"] is not None)

    assert (body["notes"], body["notes_remaining"], body["note_passes"], body["clarification"]) == ([], 10, 0, None)
    assert posted.status_code == 202
    assert live_note_calls == ["read me"]


def test_a_replay_mode_app_reads_notes_with_the_scripted_interpreter(tmp_path: Path) -> None:
    app = replay_app(tmp_path)

    assert app.state.session_store._note_interpreter is scripted_note_interpreter


def test_the_status_carries_the_one_time_check_the_session_asked() -> None:
    """Status response includes ``clarification: {questions, answers}``."""
    app = create_app(runner=GateRunner(), preflight=valid_preflight, clarity_checker=Checker())
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        waiting = _wait(client, session_id, lambda body: body["status"] == "needs_input")
        refused = client.post(f"/research/{session_id}/notes", json={"text": "too early"})
        client.post(f"/research/{session_id}/answers", json={"answers": [{"question_id": "q1", "choice": "European Union"}]})
        running = _wait(client, session_id, lambda body: body["status"] == "running")

    assert [q["id"] for q in waiting["clarification"]["questions"]] == ["q1", "q2", "q3"]
    assert waiting["clarification"]["answers"] == []
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "notes_closed"
    assert running["clarification"]["answers"] == [
        {"question_id": "q1", "value": "European Union", "source": "chosen"},
        {"question_id": "q2", "value": "Since 2023", "source": "best_guess"},
        {"question_id": "q3", "value": "General understanding", "source": "best_guess"},
    ]


@pytest.mark.parametrize("wait", [1e12, float("inf")])
def test_a_timing_too_long_or_not_finite_is_refused_with_the_request(wait: float) -> None:
    """A request with an answer_wait_s that is too long or not finite is refused with a 422.
    ``1e12`` s would overflow the answer deadline inside the session, so the request itself
    is refused and no session starts. The body is sent as raw JSON:
    ``Infinity`` is not standard JSON, so the test client's encoder refuses it, but
    FastAPI's parser takes it."""
    body = json.dumps({"query": QUESTION, "config_overrides": {"hitl": {"answer_wait_s": wait}}})
    app = create_app(runner=GateRunner(), preflight=valid_preflight, note_interpreter=Interpreter())
    with TestClient(app) as client:
        refused = client.post("/research", content=body, headers={"content-type": "application/json"})
        listed = client.get("/research").json()["sessions"]

    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "validation_error"
    assert listed == []



# --- No session that has ended reports ``pending`` ---------


def test_a_note_nothing_judged_reads_pending_while_the_session_runs_and_not_checked_once_it_ends() -> None:
    runner = GateRunner()
    app = create_app(runner=runner, preflight=valid_preflight, note_interpreter=Interpreter())
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        client.post(f"/research/{session_id}/notes", json={"text": "Pumped hydro"})
        running = _wait(client, session_id, lambda body: body["notes"][0]["restatement"] is not None)
        client.portal.call(runner.release.set)
        done = _wait(client, session_id, lambda body: body["status"] == "completed")

    assert [(note["note_id"], note["outcome"]) for note in running["notes"]] == [("n1", "pending")]
    assert [(note["note_id"], note["outcome"]) for note in done["notes"]] == [("n1", "not_checked")]


@pytest.mark.asyncio
async def test_a_session_closed_out_by_a_shutdown_reads_its_notes_not_checked() -> None:
    """A service shutdown leaves a session ``running`` with ``finished_at`` set: it has ended too."""
    runner = HeldRunner()
    store = SessionStore(runner=runner, note_interpreter=Interpreter())
    _start(store)
    session = store.require("s1")
    await _until(lambda: runner.board is not None)
    store.add_note("s1", "Pumped hydro")
    await _until(lambda: len(session.note_board.snapshot()) == 1)

    assert [note.outcome for note in session_note_fields(session)["notes"]] == ["pending"]
    await store.close()
    assert (session.status, session.finished_at is not None) == ("running", True)
    assert [note.outcome for note in session_note_fields(session)["notes"]] == ["not_checked"]
