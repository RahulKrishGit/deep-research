"""A reader's research note gets its own research thread inside the running researcher
(notes-progress-report spec §5.3, D3; AC2, AC3, AC4, AC35)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pytest

from deep_research.agents.reader_notes import note_sub_topic
from deep_research.agents.researcher import ResearcherAgent
from deep_research.graph.live import bind_live_sink
from deep_research.graph.nodes import agent_node
from deep_research.graph.state import ROUTE_NOTE_PASS, dump_state, graph_route, load_state, notes_due_a_pass
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ProviderTimeoutError
from deep_research.runtime.notes import NoteBoard, bind_note_board
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import ResearchEvent, ResearchState, SubTopic, merge_research_state, with_board_notes
from tests.agent_fakes import TargetKeyedCompleter, finish
from tests.graph_fakes import fake_reader_note, fake_report_review, fake_sub_topic, fake_target
from tests.research_fakes import research_tools

AT = "2026-09-30T10:00:00.000+00:00"
QUESTION = "Where can we get the best tasting lattes in San Jose?"


class GatedCompleter(TargetKeyedCompleter):
    """``TargetKeyedCompleter`` whose named keys wait on an ``asyncio.Event`` before each
    reply, recording each key whose wait was cancelled."""

    def __init__(self, *, gates: Mapping[str, asyncio.Event] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.gates = dict(gates or {})
        self.cancelled: list[str] = []

    async def _delay_for(self, key: str) -> None:
        gate = self.gates.get(key)
        if gate is not None:
            try:
                await gate.wait()
            except asyncio.CancelledError:
                self.cancelled.append(key)
                raise
        await super()._delay_for(key)


def _researcher(
    tracker: Tracker, completer: TargetKeyedCompleter, *, concurrency: int = 1, max_sub_topics: int = 10
) -> ResearcherAgent:
    return ResearcherAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(session_id="session-1", agent_name="researcher", max_entries=20),
        tools=research_tools(tracker),
        config=AgentRuntimeConfig(max_iterations=4, tool_budget=4),
        max_sub_topics=max_sub_topics,
        sub_topic_concurrency=concurrency,
    )


def _topic(number: int) -> SubTopic:
    coverage_id = f"topic-{number:02d}"
    return fake_sub_topic(
        f"Topic {number}", coverage_id=coverage_id, priority=number,
        targets=[fake_target(f"{coverage_id}-target-01", coverage_id=coverage_id)],
    )


def _state(*topics: SubTopic) -> ResearchState:
    return ResearchState(session_id="session-1", original_question=QUESTION, sub_topics=list(topics))


def _done(key: str) -> object:
    return finish(f"{key} is covered.", f"{key} answer.")


def _seen(events: Sequence[ResearchEvent], event_type: str, coverage_id: str) -> list[ResearchEvent]:
    return [e for e in events if e.event_type == event_type and e.metadata.get("coverage_id") == coverage_id]


async def _until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def _read(board: NoteBoard, note_id: str, **overrides: Any) -> None:
    """Receive one note on the board and read it at once."""
    board.receive("a note", received_at=AT, received_during="researcher")
    board.add(fake_reader_note(note_id, **overrides))


@pytest.mark.asyncio
async def test_researcher_note_topics_uncapped(tracker: Tracker) -> None:
    """AC2: ten planned topics at max_sub_topics = 10 and one note topic: all eleven are
    researched, and the cap skips none of them."""
    planned = [_topic(number) for number in range(1, 11)]
    noted = note_sub_topic(fake_reader_note("n1", kinds=["new_angle"]), priority=11, reason="reader_note")
    completer = TargetKeyedCompleter(decisions={t.coverage_id: [_done(t.coverage_id)] for t in [*planned, noted]})
    agent = _researcher(tracker, completer, concurrency=10, max_sub_topics=10)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state(*planned, noted))

    events = outcome.state_update["events"]
    started = [e.metadata["coverage_id"] for e in events if e.event_type == "researcher.sub_topic.started"]
    assert sorted(started) == sorted([*(t.coverage_id for t in planned), "note-n1"])
    assert not [e for e in outcome.errors if e.error_type == "researcher_sub_topic_skipped"]
    research = events[-1]
    assert research.event_type == "researcher.research.completed"
    assert (research.metadata["sub_topics_researched"], research.metadata["sub_topics_skipped"]) == (11, 0)


@pytest.mark.asyncio
async def test_research_note_thread_starts_ungated(tracker: Tracker) -> None:
    """AC3 (D3): with the only loop slot held by a blocked planned loop, a research note read
    meanwhile starts its own thread at once — its started event names the note and is published
    before the blocked loop completes — and the researcher's node completes only after the note's
    own loop has."""
    planned_gate, note_gate = asyncio.Event(), asyncio.Event()
    completer = GatedCompleter(
        gates={"topic-01": planned_gate, "note-n1": note_gate},
        decisions={"topic-01": [_done("topic-01")], "note-n1": [_done("note-n1")]},
    )
    node = agent_node(_researcher(tracker, completer, concurrency=1))
    board = NoteBoard()
    published: list[ResearchEvent] = []

    with bind_note_board(board), bind_live_sink(published.append):
        async with tracker.session_span("session-1", "q"):
            running = asyncio.create_task(node(dump_state(_state(_topic(1)))))
            await _until(lambda: bool(_seen(published, "researcher.sub_topic.started", "topic-01")))
            _read(board, "n1", kinds=["new_angle"], restatement="pastries in the cafe")
            await _until(lambda: bool(_seen(published, "researcher.sub_topic.started", "note-n1")))
            assert not _seen(published, "researcher.sub_topic.completed", "topic-01")
            planned_gate.set()
            await _until(lambda: bool(_seen(published, "researcher.sub_topic.completed", "topic-01")))
            await asyncio.sleep(0.02)
            assert not running.done()
            note_gate.set()
            final = load_state(await asyncio.wait_for(running, timeout=5))

    [started] = _seen(published, "researcher.sub_topic.started", "note-n1")
    assert (started.metadata["note_id"], started.metadata["index"], started.metadata["sub_topic"]) == (
        "n1", 2, "Your note: pastries in the cafe",
    )
    assert "note_id" not in _seen(published, "researcher.sub_topic.started", "topic-01")[0].metadata
    kinds = [(e.event_type, e.metadata.get("coverage_id") or e.metadata.get("node")) for e in final.events]
    assert kinds.index(("researcher.sub_topic.completed", "note-n1")) < kinds.index(("graph.node.completed", "researcher"))
    assert [topic.coverage_id for topic in final.sub_topics] == ["topic-01", "note-n1"]
    [research] = [e for e in final.events if e.event_type == "researcher.research.completed"]
    assert (research.metadata["sub_topics_planned"], research.metadata["sub_topics_researched"]) == (2, 2)


@pytest.mark.asyncio
async def test_late_note_waits_then_threads(tracker: Tracker) -> None:
    """AC4 (§5.3): a note received while the last loop runs, and read only once every loop has
    ended, is waited for and still gets its thread before the researcher returns."""
    completer = TargetKeyedCompleter(decisions={"topic-01": [_done("topic-01")], "note-n1": [_done("note-n1")]})
    agent = _researcher(tracker, completer)
    board = NoteBoard()
    board.receive("Pastries too", received_at=AT, received_during="researcher")
    published: list[ResearchEvent] = []

    with bind_note_board(board), bind_live_sink(published.append):
        async with tracker.session_span("session-1", "q"):
            running = asyncio.create_task(agent.run(_state(_topic(1))))
            await _until(lambda: bool(_seen(published, "researcher.sub_topic.completed", "topic-01")))
            await asyncio.sleep(0.05)
            assert not running.done()
            board.add(fake_reader_note("n1", kinds=["new_angle"]))
            outcome = await asyncio.wait_for(running, timeout=5)

    assert [topic.coverage_id for topic in outcome.state_update["sub_topics"]] == ["note-n1"]
    assert [
        e.metadata["coverage_id"] for e in outcome.state_update["events"] if e.event_type == "researcher.sub_topic.completed"
    ] == ["topic-01", "note-n1"]


@pytest.mark.asyncio
async def test_late_note_wait_times_out_and_closes_window(tracker: Tracker, monkeypatch: pytest.MonkeyPatch) -> None:
    """§5.3, §5.8: a reading that outlasts the wait closes the window when the wait times out;
    the researcher returns with no thread for it, and the note stays on the board to be read."""
    monkeypatch.setattr("deep_research.agents.researcher.NOTES_WAIT_S", 0.05)
    completer = TargetKeyedCompleter(decisions={"topic-01": [_done("topic-01")]})
    agent = _researcher(tracker, completer)
    board = NoteBoard()
    board.receive("Pastries too", received_at=AT, received_during="researcher")

    with bind_note_board(board):
        async with tracker.session_span("session-1", "q"):
            outcome = await asyncio.wait_for(agent.run(_state(_topic(1))), timeout=5)

    assert board.pending == ("n1",)
    assert "sub_topics" not in outcome.state_update
    assert [
        e.metadata["coverage_id"] for e in outcome.state_update["events"] if e.event_type == "researcher.sub_topic.started"
    ] == ["topic-01"]


@pytest.mark.asyncio
async def test_note_thread_provider_failure_sets_stop(tracker: Tracker) -> None:
    """AC35 (review I5): a note thread that ends in a provider failure stops the pass like any
    loop: the planned loop already running finishes, no later note gets a thread in this run,
    and the failed thread keeps its topic, its completed event recording the failure."""
    planned_gate = asyncio.Event()
    completer = GatedCompleter(
        gates={"topic-01": planned_gate},
        decisions={"topic-01": [_done("topic-01")], "note-n1": [ProviderTimeoutError("timed out")]},
    )
    agent = _researcher(tracker, completer, concurrency=1)
    board = NoteBoard()
    published: list[ResearchEvent] = []

    with bind_note_board(board), bind_live_sink(published.append):
        async with tracker.session_span("session-1", "q"):
            running = asyncio.create_task(agent.run(_state(_topic(1))))
            await _until(lambda: bool(_seen(published, "researcher.sub_topic.started", "topic-01")))
            _read(board, "n1", kinds=["new_angle"])
            await _until(lambda: bool(_seen(published, "researcher.sub_topic.completed", "note-n1")))
            # The failed thread sets stop only after its completed event is published; give it
            # the turns it needs first, so the next note is read once stop is set however many
            # awaits lie between that event and the thread's return.
            await asyncio.sleep(0.02)
            _read(board, "n2", kinds=["new_angle"])
            planned_gate.set()
            outcome = await asyncio.wait_for(running, timeout=5)

    [failed] = _seen(published, "researcher.sub_topic.completed", "note-n1")
    assert failed.metadata["stop_reason"] == "provider_error"
    assert not _seen(published, "researcher.sub_topic.started", "note-n2")
    [planned] = _seen(outcome.state_update["events"], "researcher.sub_topic.completed", "topic-01")
    assert planned.metadata["stop_reason"] == "finished"
    assert [topic.coverage_id for topic in outcome.state_update["sub_topics"]] == ["note-n1"]


@pytest.mark.asyncio
async def test_a_planning_time_note_topic_waits_its_turn_and_stop_leaves_it_unstarted(tracker: Tracker) -> None:
    """§5.3 (review 2, I-1): a planning-time note topic is gated and sorts last; when another
    loop's provider failure sets stop before it starts, it never opens, records only
    ``provider_failure_stopped_processing`` and has no completed event."""
    noted = note_sub_topic(fake_reader_note("n1", kinds=["new_angle"]), priority=2, reason="reader_note")
    completer = TargetKeyedCompleter(
        decisions={"topic-01": [ProviderTimeoutError("timed out")], "note-n1": [_done("note-n1")]},
    )
    agent = _researcher(tracker, completer, concurrency=1)

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(_state(_topic(1), noted))

    events = outcome.state_update["events"]
    assert not _seen(events, "researcher.sub_topic.started", "note-n1")
    assert not _seen(events, "researcher.sub_topic.completed", "note-n1")
    skipped = [e.details for e in outcome.errors if e.error_type == "researcher_sub_topic_skipped"]
    assert [(d["coverage_id"], d["reason"]) for d in skipped] == [("note-n1", "provider_failure_stopped_processing")]
    assert completer.remaining("note-n1") == 1
    assert "sub_topics" not in outcome.state_update


@pytest.mark.asyncio
async def test_dispatcher_cancels_threads_on_cancel(tracker: Tracker) -> None:
    """§5.3: the dispatcher's tasks are not a gather's children, so a cancelled run (Phase D's
    stop) cancels every thread still running — a planned loop and a note's — and awaits each
    before the cancellation leaves the researcher."""
    completer = GatedCompleter(
        gates={"topic-01": asyncio.Event(), "note-n1": asyncio.Event()},
        decisions={"topic-01": [_done("topic-01")], "note-n1": [_done("note-n1")]},
    )
    agent = _researcher(tracker, completer, concurrency=1)
    board = NoteBoard()
    published: list[ResearchEvent] = []

    with bind_note_board(board), bind_live_sink(published.append):
        async with tracker.session_span("session-1", "q"):
            running = asyncio.create_task(agent.run(_state(_topic(1))))
            await _until(lambda: bool(_seen(published, "researcher.sub_topic.started", "topic-01")))
            _read(board, "n1", kinds=["new_angle"])
            await _until(lambda: len(completer.react_calls) == 2)
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running

    assert sorted(completer.cancelled) == ["note-n1", "topic-01"]


@pytest.mark.asyncio
async def test_note_after_window_owes_pass(tracker: Tracker, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC4: a note read only after the research window closed gets no thread in that run; once
    it is read, the next node takes it in, and after the review notes_due_a_pass returns it."""
    monkeypatch.setattr("deep_research.agents.researcher.NOTES_WAIT_S", 0.05)
    completer = TargetKeyedCompleter(decisions={"topic-01": [_done("topic-01")]})
    node = agent_node(_researcher(tracker, completer))
    board = NoteBoard()
    board.receive("Pastries too", received_at=AT, received_during="researcher")

    with bind_note_board(board):
        async with tracker.session_span("session-1", "q"):
            researched = load_state(await node(dump_state(_state(_topic(1)))))
        board.add(fake_reader_note("n1", kinds=["new_angle"]))
        reviewed = merge_research_state(researched, {
            "reader_notes": with_board_notes(researched.reader_notes, board.snapshot()),
            "report_review": fake_report_review(),
        })

    assert [topic.coverage_id for topic in researched.sub_topics] == ["topic-01"]
    assert [note.note_id for note in notes_due_a_pass(reviewed)] == ["n1"]
    assert graph_route(reviewed) == (ROUTE_NOTE_PASS, "note_pass_requested")
