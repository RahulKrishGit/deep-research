"""The reader's answers to the one-time check in the run's state."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from deep_research.graph.state import dump_state, initial_graph_state, load_state
from deep_research.utils.types import ReaderAnswer, ResearchState, merge_research_state

ANSWER = ReaderAnswer(
    question_id="q1", dimension="geography", text="Which region should this cover?",
    short="Region", value="United States", source="chosen",
)


def test_a_reader_answer_names_its_question_dimension_value_and_source() -> None:
    assert ANSWER.model_dump() == {
        "question_id": "q1", "dimension": "geography", "text": "Which region should this cover?",
        "short": "Region", "value": "United States", "source": "chosen",
    }
    for bad in (
        {"dimension": "budget"},
        {"source": "guessed"},
        {"value": "   "},
    ):
        with pytest.raises(ValidationError):
            ReaderAnswer.model_validate({**ANSWER.model_dump(), **bad})


def test_the_state_starts_without_answers_and_replaces_them_on_write() -> None:
    state = ResearchState(session_id="session-1", original_question="Why?")
    first = merge_research_state(state, {"reader_answers": [ANSWER]})
    second = merge_research_state(
        first, {"reader_answers": [ANSWER.model_copy(update={"value": "Global"})]}
    )

    assert state.reader_answers == []
    assert first.reader_answers == [ANSWER]
    assert [answer.value for answer in second.reader_answers] == ["Global"]


def test_the_initial_channel_carries_the_answers_and_an_older_checkpoint_loads_none() -> None:
    channel = initial_graph_state(session_id="session-1", question="Why?", reader_answers=[ANSWER])
    older = initial_graph_state(session_id="session-1", question="Why?")
    del older["state"]["reader_answers"]

    assert load_state(channel).reader_answers == [ANSWER]
    assert load_state(dump_state(load_state(channel))).reader_answers == [ANSWER]
    assert load_state(older).reader_answers == []
    assert load_state(initial_graph_state(session_id="session-1", question="Why?")).reader_answers == []
