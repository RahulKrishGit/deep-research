"""The reader's notes in the planner's and the researcher's requests (live-briefs spec §4.6 table).

Every consumer renders the same block — the step's own lead sentence, then one
``- {restatement} ({kinds})`` line per active note — and adds nothing at all
when there is no note, so a run without notes builds byte-identical requests.
"""

from __future__ import annotations

import re

import pytest

from deep_research.agents.planner import (
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
