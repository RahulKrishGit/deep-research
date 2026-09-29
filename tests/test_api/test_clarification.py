"""The one-time check's lifecycle and route (live-briefs spec §4.4, §4.8; AC10-AC14).

Store-level tests drive ``SessionStore`` with scripted checkers and runners; the
route tests go through ``TestClient``; the replay tests run the real graph
offline with the replay-mode checker. No provider is ever reached.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from deep_research.api.app import create_app
from deep_research.api.clarify import (
    NO_QUESTIONS,
    REPLAY_CLARITY_CHECK,
    AnswerValidationError,
    ClarityCheck,
    resolve_answers,
    scripted_clarity_check,
)
from deep_research.api.models import ClarificationAnswer, ClarificationAnswersRequest
from deep_research.api.sessions import NotWaitingForInput, SessionStore
from deep_research.utils.config import ConfigSettings, HitlConfig, apply_config_overrides
from deep_research.utils.types import ResearchEvent
from tests.test_api.fakes import ScriptedRunner
from tests.test_api.replay_support import guarded
from tests.test_api.test_app import valid_preflight
from tests.test_api.test_replay import replay_app

TERMINAL = {"completed", "max_iterations", "incomplete", "failed"}
QUESTION = "What limits grid-scale battery storage?"
FAST = HitlConfig(check_timeout_s=0.5, answer_wait_s=5.0)


class Checker:
    """A scripted ``ClarityChecker``: returns, raises, or hangs; records each call."""

    def __init__(self, reply: ClarityCheck | BaseException | None = REPLAY_CLARITY_CHECK) -> None:
        self.reply = reply
        self.calls: list[tuple[str, object]] = []

    async def __call__(self, question: str, settings: object) -> ClarityCheck:
        self.calls.append((question, settings))
        if self.reply is None:
            await asyncio.Event().wait()  # hangs until cancelled by the check's timeout
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


def _start(store: SessionStore, *, hitl: HitlConfig = FAST, ask: bool = True) -> None:
    store.start(
        session_id="s1", query=QUESTION, max_extra_passes=None, output_format="markdown",
        config_overrides={}, config_path="config.yaml",
        ask_clarifying_questions=ask, settings=ConfigSettings(), hitl=hitl,
    )


async def _until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


def _types(events: list[ResearchEvent]) -> list[str]:
    return [event.event_type for event in events]


def _request(*answers: dict[str, str], skip: bool = False) -> ClarificationAnswersRequest:
    return ClarificationAnswersRequest(answers=[ClarificationAnswer(**a) for a in answers], skip=skip)


# --- the lifecycle ------------------------------------------------------------


@pytest.mark.asyncio
async def test_answers_move_the_session_from_needs_input_to_the_run() -> None:
    runner = ScriptedRunner()
    store = SessionStore(runner=runner, clarity_checker=Checker())
    _start(store)
    session = store.require("s1")
    await _until(lambda: session.status == "needs_input")

    requested = session.events[-1]
    assert requested.event_type == "session.clarification.requested"
    assert [q["id"] for q in requested.metadata["questions"]] == ["q1", "q2", "q3"]
    assert runner.calls == []

    store.submit_answers("s1", _request({"question_id": "q1", "choice": "United States"}, {"question_id": "q2", "text": "since 2021"}))
    await session.task

    expected = resolve_answers(
        REPLAY_CLARITY_CHECK.questions,
        [ClarificationAnswer(question_id="q1", choice="United States"), ClarificationAnswer(question_id="q2", text="since 2021")],
    )
    assert runner.calls[0]["reader_answers"] == expected
    assert _types(session.events) == ["session.clarification.requested", "session.clarification.answered"]
    assert session.events[-1].metadata["reason"] == "answered"
    assert session.status == "completed"
    assert session.clarification is None


@pytest.mark.asyncio
async def test_just_start_skips_with_what_was_answered_and_best_guesses_for_the_rest() -> None:
    runner = ScriptedRunner()
    store = SessionStore(runner=runner, clarity_checker=Checker())
    _start(store)
    session = store.require("s1")
    await _until(lambda: session.status == "needs_input")

    store.submit_answers("s1", _request({"question_id": "q1", "choice": "Global"}, skip=True))
    await session.task

    answered = session.events[1]
    assert answered.metadata["reason"] == "skipped"
    assert [(a["value"], a["source"]) for a in answered.metadata["answers"]] == [
        ("Global", "chosen"), ("Since 2023", "best_guess"), ("General understanding", "best_guess"),
    ]
    assert [a.source for a in runner.calls[0]["reader_answers"]] == ["chosen", "best_guess", "best_guess"]


@pytest.mark.asyncio
async def test_no_answer_starts_the_run_on_best_guesses_when_the_wait_ends() -> None:
    """AC12: the run starts within answer_wait_s + 2 s, every answer a best guess."""
    runner = ScriptedRunner()
    store = SessionStore(runner=runner, clarity_checker=Checker())
    started = time.monotonic()
    _start(store, hitl=HitlConfig(answer_wait_s=0.3))
    session = store.require("s1")
    await session.task

    assert time.monotonic() - started < 0.3 + 2
    answered = session.events[1]
    assert answered.metadata["reason"] == "timed_out"
    assert {a["source"] for a in answered.metadata["answers"]} == {"best_guess"}
    assert [a.value for a in runner.calls[0]["reader_answers"]] == ["Global", "Since 2023", "General understanding"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [RuntimeError("provider down"), NO_QUESTIONS], ids=["check_failed", "no_questions"])
async def test_a_failed_or_empty_check_starts_the_run_exactly_as_before(reply: Any) -> None:
    runner = ScriptedRunner()
    store = SessionStore(runner=runner, clarity_checker=Checker(reply))
    _start(store)
    session = store.require("s1")
    await session.task

    assert "reader_answers" not in runner.calls[0]
    assert session.events == []
    assert session.status == "completed"


@pytest.mark.asyncio
async def test_a_check_that_hangs_delays_the_run_by_at_most_check_timeout_s() -> None:
    """AC14."""
    runner = ScriptedRunner()
    store = SessionStore(runner=runner, clarity_checker=Checker(None))
    started = time.monotonic()
    _start(store, hitl=HitlConfig(check_timeout_s=0.2))
    session = store.require("s1")
    await session.task

    assert time.monotonic() - started < 0.2 + 0.5
    assert "reader_answers" not in runner.calls[0]


@pytest.mark.asyncio
async def test_with_the_setting_off_no_check_call_is_made() -> None:
    """AC13."""
    checker = Checker()
    runner = ScriptedRunner()
    store = SessionStore(runner=runner, clarity_checker=checker)
    _start(store, ask=False)
    await store.require("s1").task

    assert checker.calls == []
    assert "reader_answers" not in runner.calls[0]


@pytest.mark.asyncio
async def test_a_store_without_a_checker_never_asks() -> None:
    runner = ScriptedRunner()
    store = SessionStore(runner=runner)
    _start(store)
    await store.require("s1").task

    assert "reader_answers" not in runner.calls[0]


@pytest.mark.asyncio
async def test_closing_during_the_wait_reads_as_an_interrupted_run() -> None:
    store = SessionStore(runner=ScriptedRunner(), clarity_checker=Checker())
    _start(store)
    session = store.require("s1")
    await _until(lambda: session.status == "needs_input")

    await store.close()

    assert session.status == "running"
    assert session.finished_at is not None
    assert session.clarification is None
    assert _types(session.events) == ["session.clarification.requested"]
    assert [e.event_type async for e in store.iter_events("s1")] == ["session.clarification.requested"]


@pytest.mark.asyncio
async def test_answers_are_taken_once_and_only_while_the_session_waits() -> None:
    store = SessionStore(runner=ScriptedRunner(), clarity_checker=Checker())
    _start(store)
    session = store.require("s1")
    with pytest.raises(KeyError):
        store.submit_answers("nope", _request())
    await _until(lambda: session.status == "needs_input")
    with pytest.raises(AnswerValidationError):
        store.submit_answers("s1", _request({"question_id": "q1", "choice": "Asia"}))
    assert session.status == "needs_input"

    store.submit_answers("s1", _request())
    with pytest.raises(NotWaitingForInput):
        store.submit_answers("s1", _request())
    await session.task
    with pytest.raises(NotWaitingForInput):
        store.submit_answers("s1", _request())


@pytest.mark.asyncio
async def test_answers_after_the_deadline_are_refused() -> None:
    store = SessionStore(runner=ScriptedRunner(), clarity_checker=Checker())
    _start(store, hitl=HitlConfig(answer_wait_s=5.0))
    session = store.require("s1")
    await _until(lambda: session.status == "needs_input")
    assert session.clarification is not None
    session.clarification.deadline_at = session.clarification.deadline_at.replace(year=2000)

    with pytest.raises(NotWaitingForInput):
        store.submit_answers("s1", _request())
    session.task.cancel()
    await asyncio.gather(session.task, return_exceptions=True)


# --- the route ------------------------------------------------------------------


def _wait(client: TestClient, session_id: str, statuses: set[str], timeout: float = 10) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/research/{session_id}/status").json()
        if body["status"] in statuses:
            return body
        time.sleep(0.01)
    raise AssertionError(f"session never reached {statuses}")


def _overriding_preflight(**kwargs: Any) -> ConfigSettings:
    return apply_config_overrides(ConfigSettings(), kwargs["config_overrides"])


def test_the_answers_route_takes_answers_once_and_the_run_starts() -> None:
    runner = ScriptedRunner()
    app = create_app(runner=runner, preflight=valid_preflight, clarity_checker=Checker())
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        assert _wait(client, session_id, {"needs_input"})["status"] == "needs_input"

        posted = client.post(
            f"/research/{session_id}/answers",
            json={"answers": [{"question_id": "q1", "choice": "European Union"}, {"question_id": "q3", "text": "a grant proposal"}], "skip": False},
        )
        again = client.post(f"/research/{session_id}/answers", json={"answers": []})
        status = _wait(client, session_id, TERMINAL)

    assert posted.status_code == 202
    assert posted.json()["session_id"] == session_id
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "not_waiting_for_input"
    assert status["status"] == "completed"
    assert [(a.value, a.source) for a in runner.calls[0]["reader_answers"]] == [
        ("European Union", "chosen"), ("Since 2023", "best_guess"), ("a grant proposal", "typed"),
    ]


@pytest.mark.parametrize(
    ("body", "issue"),
    [
        ({"answers": [{"question_id": "q7", "choice": "Global"}]}, ("body.answers.0.question_id", "unknown_question")),
        ({"answers": [{"question_id": "q1", "choice": "Asia"}]}, ("body.answers.0.choice", "choice_not_offered")),
        ({"answers": [{"question_id": "q1", "choice": "Global"}, {"question_id": "q1", "choice": "Global"}]}, ("body.answers.1.question_id", "duplicate_question")),
        ({"answers": [{"question_id": "q2", "text": "x" * 201}]}, ("body.answers.0.text", "string_too_long")),
        ({"answers": [{"question_id": "q2", "text": "x", "choice": "Global"}]}, ("body.answers.0", "value_error")),
        ({"answers": [{"question_id": "q1", "choice": "x" * 81}]}, ("body.answers.0.choice", "string_too_long")),
    ],
    ids=["unknown_question", "choice_not_offered", "duplicate", "text_over_200", "choice_and_text", "choice_over_80"],
)
def test_answers_that_do_not_fit_are_a_safe_422_and_the_session_keeps_waiting(body: dict[str, Any], issue: tuple[str, str]) -> None:
    app = create_app(runner=ScriptedRunner(), preflight=valid_preflight, clarity_checker=Checker())
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        _wait(client, session_id, {"needs_input"})
        response = client.post(f"/research/{session_id}/answers", json=body)
        still = client.get(f"/research/{session_id}/status").json()["status"]

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert (issue[0], issue[1]) in {(i["location"], i["type"]) for i in error["issues"]}
    assert "Asia" not in response.text and "xxxx" not in response.text
    assert still == "needs_input"


def test_the_answers_route_is_404_for_an_unknown_session_and_409_when_nothing_was_asked() -> None:
    app = create_app(runner=ScriptedRunner(), preflight=valid_preflight, clarity_checker=Checker(NO_QUESTIONS))
    with TestClient(app) as client:
        unknown = client.post("/research/nope/answers", json={"answers": []})
        session_id = client.post("/research", json={"query": QUESTION}).json()["session_id"]
        _wait(client, session_id, TERMINAL)
        never_asked = client.post(f"/research/{session_id}/answers", json={"answers": []})

    assert (unknown.status_code, unknown.json()["error"]["code"]) == (404, "session_not_found")
    assert (never_asked.status_code, never_asked.json()["error"]["code"]) == (409, "not_waiting_for_input")


def test_the_request_flag_defaults_on_and_off_makes_no_check_call(live_check_calls: list[str]) -> None:
    """AC10 and AC13 at the route: the live-mode default checker (replaced by the
    package guard) runs for a request without the flag and never for one with it off."""
    runner = ScriptedRunner()
    app = create_app(runner=runner, preflight=valid_preflight)
    with TestClient(app) as client:
        first = client.post("/research", json={"query": "First question"}).json()["session_id"]
        second = client.post("/research", json={"query": "Second question", "ask_clarifying_questions": False}).json()["session_id"]
        statuses = [_wait(client, sid, TERMINAL)["status"] for sid in (first, second)]

    assert live_check_calls == ["First question"]
    assert statuses == ["completed", "completed"]
    assert all("reader_answers" not in call for call in runner.calls)


def test_the_wait_is_read_from_the_requests_own_settings() -> None:
    app = create_app(runner=ScriptedRunner(), preflight=_overriding_preflight, clarity_checker=Checker())
    started = time.monotonic()
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": QUESTION, "config_overrides": {"hitl": {"answer_wait_s": 0.4}}}).json()["session_id"]
        status = _wait(client, session_id, TERMINAL)
        frames = client.get(f"/research/{session_id}/stream").text

    assert status["status"] == "completed"
    assert time.monotonic() - started < 0.4 + 2
    assert '"reason":"timed_out"' in frames


def test_a_replay_mode_app_checks_with_the_scripted_checker(tmp_path: Path) -> None:
    app = replay_app(tmp_path)

    assert app.state.session_store._clarity_checker is scripted_clarity_check


# --- replay mode, the real graph ----------------------------------------------------


def test_replay_asks_nothing_without_the_header(tmp_path: Path) -> None:
    """AC10: the default flag is on and the question is clear, so nothing changes."""
    with guarded(), TestClient(replay_app(tmp_path)) as client:
        session_id = client.post("/research", json={"query": "q"}).json()["session_id"]
        status = _wait(client, session_id, TERMINAL, timeout=30)
        names = [line[7:] for line in client.get(f"/research/{session_id}/stream").text.splitlines() if line.startswith("event: ")]

    assert status["status"] == "completed"
    assert not any(name.startswith("session.clarification") for name in names)
    assert names[0] == "graph.session.started"


def test_replay_with_the_header_asks_then_plans_with_the_readers_answers(tmp_path: Path) -> None:
    """AC11 on the replay server: needs_input, the answers, then the run completes."""
    app = replay_app(tmp_path)
    with guarded(), TestClient(app) as client:
        session_id = client.post("/research", json={"query": "q"}, headers={"X-Replay-Clarify": "on"}).json()["session_id"]
        waiting = _wait(client, session_id, {"needs_input"}, timeout=30)
        posted = client.post(
            f"/research/{session_id}/answers",
            json={"answers": [{"question_id": "q1", "choice": "United States"}, {"question_id": "q2", "text": "since 2021"}]},
        )
        status = _wait(client, session_id, TERMINAL, timeout=30)
        names = [line[7:] for line in client.get(f"/research/{session_id}/stream").text.splitlines() if line.startswith("event: ")]
        contract = app.state.session_store.require(session_id).outcome.state.answer_contract

    assert waiting["status"] == "needs_input"
    assert posted.status_code == 202
    assert status["status"] == "completed"
    assert names[:3] == ["session.clarification.requested", "session.clarification.answered", "graph.session.started"]
    assert contract.geographic_scope == "United States"
    assert contract.assumptions[-3:] == [
        "Reader said: Region = United States",
        "Reader said: Period = since 2021",
        "Assumed (best guess): For = General understanding",
    ]
