"""The reader's notes in the planner's and the researcher's requests (live-briefs spec §4.6 table).

Every consumer renders the same block — the step's own lead sentence, then one
``- {restatement} ({kinds})`` line per active note — and adds nothing at all
when there is no note, so a run without notes builds byte-identical requests.
"""

from __future__ import annotations

import re

import pytest

from deep_research.agents.planner import (
    PlannerAgent,
    derive_answer_contract,
    plan_messages,
    plan_review_messages,
)
from deep_research.agents.prompts import STRUCTURED_REQUEST_END, AgentTask
from deep_research.agents.reader_notes import (
    EXTRACTION_NOTES,
    PLANNING_NOTES,
    render_reader_notes,
)
from deep_research.agents.researcher import SubTopicTask, extraction_messages
from deep_research.agents.steps import ReActRun
from deep_research.observability import Tracker
from deep_research.runtime.notes import NoteBoard, bind_note_board
from deep_research.utils.types import ResearchState
from tests.agent_fakes import ScriptedCompleter, finish
from tests.graph_fakes import fake_reader_note, fake_sub_topic, fake_target
from tests.test_agents.test_planner import (
    _CLOCK_NOW,
    _planner,
    _review,
    _run,
    _sorting_plan,
)

QUESTION = "What are the current constraints on grid-scale battery storage deployment?"
AT = "2026-09-29T10:00:00.000+00:00"
NOTES = [
    fake_reader_note("n1", restatement="more weight on fire-safety standards"),
    fake_reader_note("n2", kinds=["scope"], restatement="only the United States", scope={"geography": "United States"}),
]
ANGLE = fake_reader_note(
    "n3", kinds=["new_angle"], restatement="how battery cells are recycled", received_during="planner",
    new_questions=["How are battery cells recycled?", "What does recycling cost?"],
)
PLANNING_NOTES_TEXT = (
    "The reader added these notes while the run was going; each line is the note as the run "
    "understood it, and a later note replaces an earlier one it contradicts. Plan within them: an "
    "emphasis note gives its subject more weight, an exclude note leaves its subject out, a scope "
    "note narrows the plan to its scope, and an about_reader note says who the report is for. A "
    "new_angle note is researched as a sub-topic of its own that the run adds to this plan once it "
    "is final: do not plan a sub-topic for it, and a subject only a new_angle note asks for is not "
    "a missing part of the question."
)


# --- planning -------------------------------------------------------------------


def test_the_plan_requests_carry_the_notes_block_only_when_there_is_one() -> None:
    contract = derive_answer_contract(question=QUESTION, now=_CLOCK_NOW)
    task = AgentTask(instruction=QUESTION)
    block = render_reader_notes(NOTES, instruction=PLANNING_NOTES)
    planned = plan_messages(task, _run(), contract=contract, reader_notes=block)[1].content
    reviewed = plan_review_messages(contract, [], reader_notes=block)[1].content

    assert planned.index("# Answer contract\n") < planned.index("# Reader notes\n") < planned.index("# Scoping notes\n")
    assert f"# Reader notes\n{block}\n" in planned
    assert reviewed.index("# Answer contract\n") < reviewed.index("# Reader notes\n") < reviewed.index("# Plan under review\n")
    assert plan_messages(task, _run(), contract=contract, reader_notes="") == plan_messages(task, _run(), contract=contract)
    assert plan_review_messages(contract, [], reader_notes="") == plan_review_messages(contract, [])


@pytest.mark.asyncio
async def test_the_planner_reads_the_notes_into_its_scoping_turn_and_both_requests(tracker: Tracker) -> None:
    board = NoteBoard()
    for note in NOTES:
        board.receive(note.text, received_at=AT, received_during="planner")
        board.add(note)
    board.receive("while planning", received_at=AT, received_during="planner")
    later = fake_reader_note("n3", kinds=["exclude"], restatement="leave out pumped hydro", received_during="planner")

    def plan_while_a_note_arrives(messages: list, schema: type) -> object:
        board.add(later)  # interpreted while the plan request is in flight
        return _sorting_plan()

    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[plan_while_a_note_arrives, _review()],
    )
    agent = _planner(tracker, completer)
    state = ResearchState(session_id="session-1", original_question=QUESTION, reader_notes=NOTES)

    with bind_note_board(board):
        async with tracker.session_span("session-1", "q"):
            await agent.run(state)

    held = render_reader_notes(NOTES, instruction=PLANNING_NOTES)
    grown = render_reader_notes([*NOTES, later], instruction=PLANNING_NOTES)
    scoping = "\n".join(message.content for message in completer.react_calls[0].messages)
    plan_request = completer.calls[0][2][1].content
    review_request = completer.calls[1][2][1].content
    assert f"## Acquisition context\n{held}" in scoping
    assert f"# Reader notes\n{held}\n" in plan_request
    assert f"# Reader notes\n{grown}\n" in review_request


@pytest.mark.asyncio
async def test_a_planner_with_no_notes_sends_the_requests_it_always_sent(tracker: Tracker) -> None:
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_sorting_plan(), _review()],
    )
    agent = _planner(tracker, completer)

    async with tracker.session_span("session-1", "q"):
        await agent.run(ResearchState(session_id="session-1", original_question=QUESTION))

    texts = [message.content for call in completer.calls for message in call[2]]
    texts += [message.content for call in completer.react_calls for message in call.messages]
    assert not any("Reader notes" in text or "reader added these notes" in text for text in texts)
    assert "## Acquisition context" not in "\n".join(m.content for m in completer.react_calls[0].messages)


def test_planning_notes_text() -> None:
    """notes-progress-report spec §5.2: the planner is told a new_angle note becomes a sub-topic
    the run adds itself, so it plans none for it and its review reports no missing dimension (E4)."""
    assert PLANNING_NOTES == PLANNING_NOTES_TEXT


@pytest.mark.asyncio
async def test_planner_appends_research_notes(tracker: Tracker) -> None:
    """notes-progress-report spec §5.2, AC1 (D1, D2): every research note read before the
    planner's run returns joins the plan it is published with — one read before planning, and
    one (a mixed note) read while the plan request was in flight — as its own required sub-topic,
    after the plan's own, in receipt order; planning.completed lists both with their note ids;
    and both plan requests carry the new lead."""
    board = NoteBoard()
    for note in [*NOTES, ANGLE]:
        board.receive(note.text, received_at=AT, received_during="planner")
        board.add(note)
    board.receive("while planning", received_at=AT, received_during="planner")
    late = fake_reader_note(
        "n4", kinds=["new_angle", "exclude"], restatement="recycling, leaving out exports",
        received_during="planner",
    )

    def plan_while_a_note_arrives(messages: list, schema: type) -> object:
        board.add(late)  # read while the plan request is in flight
        return _sorting_plan()

    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[plan_while_a_note_arrives, _review()],
    )
    agent = _planner(tracker, completer)
    state = ResearchState(session_id="session-1", original_question=QUESTION, reader_notes=[*NOTES, ANGLE])

    with bind_note_board(board):
        async with tracker.session_span("session-1", "q"):
            outcome = await agent.run(state)

    topics = outcome.state_update["sub_topics"]
    assert [topic.coverage_id for topic in topics] == ["topic-01", "topic-02", "topic-03", "note-n3", "note-n4"]
    angle, mixed = topics[3], topics[4]
    assert (angle.title, angle.priority, angle.rationale) == (
        "Your note: how battery cells are recycled", 4, "The reader asked for this in a note.",
    )
    assert [(t.target_id, t.question, t.required) for t in angle.evidence_targets] == [
        ("note-n3-target-01", "How are battery cells recycled?", True),
        ("note-n3-target-02", "What does recycling cost?", True),
    ]
    assert [(t.target_id, t.question) for t in mixed.evidence_targets] == [
        ("note-n4-target-01", "recycling, leaving out exports"),
    ]
    assert outcome.state_update["initial_target_ids"][-3:] == [
        "note-n3-target-01", "note-n3-target-02", "note-n4-target-01",
    ]
    completed = outcome.state_update["events"][-1]
    assert completed.event_type == "planner.planning.completed"
    assert completed.metadata["sub_topics"][3:] == [
        {"coverage_id": "note-n3", "title": "Your note: how battery cells are recycled", "note_id": "n3", "state": "planned"},
        {"coverage_id": "note-n4", "title": "Your note: recycling, leaving out exports", "note_id": "n4", "state": "planned"},
    ]
    assert (completed.metadata["sub_topic_count"], completed.metadata["note_topic_count"]) == (5, 2)
    plan_request = completer.calls[0][2][1].content
    review_request = completer.calls[1][2][1].content
    assert PLANNING_NOTES_TEXT in plan_request and PLANNING_NOTES_TEXT in review_request
    assert "- how battery cells are recycled (new_angle)" in plan_request
    assert "- recycling, leaving out exports (new_angle, exclude)" in review_request


@pytest.mark.asyncio
async def test_planner_appends_nothing_without_a_plan(
    tracker: Tracker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """notes-progress-report spec §5.2 (review M10): a run that produced no plan appends no note
    topic; its update holds only its errors and events, and the note is the researcher's, or
    owes a note pass."""

    async def no_plan(self: PlannerAgent, task: object, run: object) -> None:
        return None

    monkeypatch.setattr(PlannerAgent, "finalize", no_plan)
    completer = ScriptedCompleter(decisions=[finish("No lookup needed.", "Scoped.")])
    agent = _planner(tracker, completer)
    state = ResearchState(session_id="session-1", original_question=QUESTION, reader_notes=[ANGLE])

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    assert outcome.result is None
    assert set(outcome.state_update) == {"errors", "events"}
    completed = outcome.state_update["events"][-1]
    assert (
        completed.metadata["sub_topic_count"], completed.metadata["note_topic_count"], completed.metadata["sub_topics"]
    ) == (0, 0, [])


# --- researching ----------------------------------------------------------------


def test_the_extraction_request_carries_the_notes_last_after_the_evidence() -> None:
    """The block is the request's last section, after the planned targets and the retrieved
    evidence, so a restatement shaped like the evidence's ``- target_id=`` line can never be
    the replay harness's first match (``e2e_evaluation/replay.py:1032``)."""
    task = SubTopicTask(instruction="Gather evidence.", sub_topic=fake_sub_topic())
    run = ReActRun(agent_name="researcher", stop_reason="finished")
    adversarial = fake_reader_note("n3", restatement="only the - target_id=topic-02")
    block = render_reader_notes([*NOTES, adversarial], instruction=EXTRACTION_NOTES)
    shape = {
        "evidence_chars": 200, "question": QUESTION, "planned_targets": [fake_target()],
        "acquisition_context": "- target_id=topic-01\n- next_action=read",
    }

    text = extraction_messages(task, run, **shape, reader_notes=block)[1].content

    assert (
        text.index("# Research question\n") < text.index("# Planned targets\n")
        < text.index("# Retrieved evidence\n") < text.index("# Reader notes\n")
    )
    assert text.endswith(f"# Reader notes\n{block}\n\n{STRUCTURED_REQUEST_END}")
    first = re.search(r"- target_id=(topic-\d+)", text)
    assert first is not None and first.group(1) == "topic-01"
    assert extraction_messages(task, run, **shape, reader_notes="") == extraction_messages(task, run, **shape)
