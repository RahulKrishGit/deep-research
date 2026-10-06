"""The one-time check's service: contract, checkers, answers, events.

The live checker is driven only with a scripted completer: no provider is built
and no request leaves the process.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from deep_research.api.clarify import (
    CLARITY_MAX_TOKENS,
    NO_QUESTIONS,
    REPLAY_CLARITY_CHECK,
    AnswerValidationError,
    ClarityCheck,
    ClarityCheckDraft,
    ClarityQuestionDraft,
    clarification_answered_event,
    clarification_requested_event,
    clarity_llm_config,
    live_clarity_check,
    requested_clarify,
    resolve_answers,
    scripted_clarity_check,
    validated_check,
)
from deep_research.api.models import ClarificationAnswer, ResearchRequest
from deep_research.utils.config import ConfigSettings, LLMConfig

QUESTIONS = REPLAY_CLARITY_CHECK.questions


def _draft(n: int = 1, **over: Any) -> ClarityCheckDraft:
    question = {
        "dimension": "geography",
        "text": "Which region should this cover?",
        "short": "Region",
        "options": ["United States", "European Union", "Global"],
        "best_guess": "Global",
        **over,
    }
    return ClarityCheckDraft(questions=[ClarityQuestionDraft(**question) for _ in range(n)])


class RecordingCompleter:
    """Answers ``complete_structured`` with one scripted draft and records the call."""

    def __init__(self, reply: ClarityCheckDraft | BaseException) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def complete_structured(self, messages, schema, **kwargs: Any) -> Any:
        self.calls.append({"messages": list(messages), "schema": schema, **kwargs})
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


def test_a_valid_draft_is_stamped_q1_to_qn_in_order() -> None:
    check = validated_check(_draft(3))

    assert [q.id for q in check.questions] == ["q1", "q2", "q3"]
    assert check.questions[0].best_guess == "Global"


def test_check_text_from_the_model_is_collapsed_to_one_line_each() -> None:
    """The checker's text reaches the planner's prompt through ``ReaderAnswer``
    (text, short, value), so a multi-line question, label, option or best guess
    is collapsed to one single-spaced line, and the best guess still matches its
    option after the collapse."""
    check = validated_check(
        _draft(
            1,
            text="Which\nregion  should\r\nthis\tcover?\n",
            short="  Re\ngion ",
            options=["United\n# Reader answers\nStates", "European\t\tUnion", "Global"],
            best_guess="United\n# Reader answers  \r\nStates",
        )
    )

    [question] = check.questions
    assert question.text == "Which region should this cover?"
    assert question.short == "Re gion"
    assert question.options == ["United # Reader answers States", "European Union", "Global"]
    assert question.best_guess == "United # Reader answers States"
    assert question.best_guess in question.options
    answers = resolve_answers(check.questions)
    assert (answers[0].text, answers[0].short, answers[0].value) == (
        "Which region should this cover?",
        "Re gion",
        "United # Reader answers States",
    )
    assert all("\n" not in part and "\r" not in part for part in (answers[0].text, answers[0].short, answers[0].value))


@pytest.mark.parametrize(
    "draft",
    [
        _draft(4),
        _draft(1, best_guess="Asia"),
        _draft(1, options=["Global"]),
        _draft(1, options=["A", "B", "C", "D", "Global"]),
        _draft(1, options=["Global", "Global"], best_guess="Global"),
        _draft(1, text="x" * 201),
        _draft(1, short=""),
    ],
    ids=["four_questions", "best_guess_not_an_option", "one_option", "five_options", "repeated_option", "long_text", "empty_short"],
)
def test_an_invalid_draft_asks_nothing_at_all(draft: ClarityCheckDraft) -> None:
    assert validated_check(draft) == NO_QUESTIONS


def test_an_empty_draft_asks_nothing() -> None:
    assert validated_check(ClarityCheckDraft()) == NO_QUESTIONS
    assert NO_QUESTIONS.questions == []


def test_the_check_runs_on_the_configured_model_with_thinking_disabled() -> None:
    llm = LLMConfig(provider="deepseek", model="deepseek-v4-pro", thinking_mode="enabled")

    config = clarity_llm_config(llm)

    assert (config.provider, config.model, config.thinking_mode) == ("deepseek", "deepseek-v4-pro", "disabled")
    assert llm.thinking_mode == "enabled"


@pytest.mark.asyncio
async def test_the_live_check_asks_one_structured_tool_free_request() -> None:
    completer = RecordingCompleter(_draft(2))

    check = await live_clarity_check("What limits storage?", ConfigSettings(), completer=completer)

    [call] = completer.calls
    assert call["schema"] is ClarityCheckDraft
    assert (call["agent_name"], call["max_tokens"]) == (None, CLARITY_MAX_TOKENS)
    request = call["messages"][1].content
    assert request.endswith("# Research question\nWhat limits storage?")
    assert "Never ask about something the question already states." in request
    assert "Ask at most 3 questions." in request
    assert [q.id for q in check.questions] == ["q1", "q2"]


@pytest.mark.asyncio
async def test_a_live_check_whose_reply_breaks_the_contract_asks_nothing() -> None:
    check = await live_clarity_check("Q?", ConfigSettings(), completer=RecordingCompleter(_draft(4)))

    assert check == NO_QUESTIONS


@pytest.mark.asyncio
async def test_a_provider_failure_propagates_to_the_store_that_owns_the_fallback() -> None:
    with pytest.raises(RuntimeError, match="provider down"):
        await live_clarity_check("Q?", ConfigSettings(), completer=RecordingCompleter(RuntimeError("provider down")))


@pytest.mark.asyncio
async def test_the_replay_checker_asks_the_fixed_set_only_when_the_header_asked() -> None:
    assert await scripted_clarity_check("Q?", object()) == NO_QUESTIONS

    async def with_header() -> ClarityCheck:
        requested_clarify.set(True)
        return await scripted_clarity_check("Q?", object())

    assert await asyncio.create_task(with_header()) == REPLAY_CLARITY_CHECK
    assert requested_clarify.get() is False
    assert [(q.id, q.dimension, q.short, q.best_guess) for q in REPLAY_CLARITY_CHECK.questions] == [
        ("q1", "geography", "Region", "Global"),
        ("q2", "period", "Period", "Since 2023"),
        ("q3", "purpose", "For", "General understanding"),
    ]


def test_answers_resolve_in_question_order_and_skipped_ones_take_the_best_guess() -> None:
    answers = resolve_answers(
        QUESTIONS,
        [
            ClarificationAnswer(question_id="q2", text="since 2021"),
            ClarificationAnswer(question_id="q1", choice="United States"),
        ],
    )

    assert [(a.question_id, a.dimension, a.short, a.value, a.source) for a in answers] == [
        ("q1", "geography", "Region", "United States", "chosen"),
        ("q2", "period", "Period", "since 2021", "typed"),
        ("q3", "purpose", "For", "General understanding", "best_guess"),
    ]
    assert answers[0].text == "Which region should this cover?"
    assert [a.source for a in resolve_answers(QUESTIONS)] == ["best_guess"] * 3


def test_answers_that_do_not_fit_the_questions_name_each_problem() -> None:
    with pytest.raises(AnswerValidationError) as caught:
        resolve_answers(
            QUESTIONS,
            [
                ClarificationAnswer(question_id="q9", choice="Global"),
                ClarificationAnswer(question_id="q1", choice="Asia"),
                ClarificationAnswer(question_id="q2", text="recent"),
                ClarificationAnswer(question_id="q2", text="older"),
            ],
        )

    assert caught.value.errors() == [
        {"loc": ("body", "answers", 0, "question_id"), "type": "unknown_question", "msg": "invalid answer"},
        {"loc": ("body", "answers", 1, "choice"), "type": "choice_not_offered", "msg": "invalid answer"},
        {"loc": ("body", "answers", 3, "question_id"), "type": "duplicate_question", "msg": "invalid answer"},
    ]


def test_the_two_events_carry_the_expected_metadata() -> None:
    deadline = datetime(2026, 9, 29, 10, 0, 30, 123456, tzinfo=timezone.utc)
    requested = clarification_requested_event(QUESTIONS, deadline)
    answered = clarification_answered_event(resolve_answers(QUESTIONS), "timed_out")

    assert requested.event_type == "session.clarification.requested"
    assert requested.metadata["deadline_at"] == "2026-09-29T10:00:30.123+00:00"
    assert requested.metadata["questions"][0] == {
        "id": "q1", "dimension": "geography", "text": "Which region should this cover?", "short": "Region",
        "options": ["United States", "European Union", "Global"], "best_guess": "Global",
    }
    assert answered.event_type == "session.clarification.answered"
    assert answered.metadata == {
        "answers": [
            {"question_id": "q1", "value": "Global", "source": "best_guess"},
            {"question_id": "q2", "value": "Since 2023", "source": "best_guess"},
            {"question_id": "q3", "value": "General understanding", "source": "best_guess"},
        ],
        "reason": "timed_out",
    }


def test_the_request_asks_by_default_and_can_turn_the_check_off() -> None:
    """The setting is on by default unless the request says otherwise."""
    assert ResearchRequest(query="Q?").ask_clarifying_questions is True
    assert ResearchRequest(query="Q?", ask_clarifying_questions=False).ask_clarifying_questions is False


@pytest.mark.parametrize(
    "body",
    [
        {"question_id": "q1"},
        {"question_id": "q1", "choice": "Global", "text": "anywhere"},
        {"question_id": "q1", "text": "   "},
        {"question_id": "q1", "text": "x" * 201},
        {"question_id": "", "choice": "Global"},
    ],
    ids=["neither", "both", "blank_text", "text_over_200", "blank_question_id"],
)
def test_an_answer_carries_exactly_one_of_choice_or_text(body: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        ClarificationAnswer.model_validate(body)
    assert ClarificationAnswer(question_id="q1", text=" x" * 100).text == ("x " * 99) + "x"


def test_an_answer_is_one_bounded_line() -> None:
    """A typed answer is collapsed to one single-spaced line before its length is
    checked, so it can never start a new line in the planner's # Reader answers
    section; ids and choices are capped at what a check can ask."""
    assert ClarificationAnswer(question_id="q1", text="since\n\n2021\t (roughly)\r\n").text == "since 2021 (roughly)"
    assert ClarificationAnswer(question_id="q1", text="a" + " " * 300 + "b").text == "a b"
    for body in ({"question_id": "q" * 9, "choice": "Global"}, {"question_id": "q1", "choice": "x" * 81}):
        with pytest.raises(ValueError):
            ClarificationAnswer.model_validate(body)
