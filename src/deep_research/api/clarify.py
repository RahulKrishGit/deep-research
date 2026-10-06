"""The one-time check: before planning, ask the reader.

When the question leaves something material open, the session asks the reader
up to three questions before the planner starts, each with a few options and
one best guess. A ``ClarityChecker`` decides what to ask: live mode asks the
configured provider (thinking disabled, structured output), and replay mode
asks nothing unless the request carried ``X-Replay-Clarify: on``, in which case
it asks a fixed set, so every existing scripted flow stays exactly as it was.

The check never blocks a run. A checker that fails, times out or answers with
something invalid asks nothing, and the run starts as it always did
(``SessionStore._clarify`` owns the timeout and the waiting).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from contextvars import ContextVar
from datetime import datetime
from typing import Any, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.providers import ChatMessage, build_chat_provider
from deep_research.utils.config import ConfigSettings, LLMConfig
from deep_research.utils.text import collapse_whitespace
from deep_research.utils.types import (
    ClarityDimension,
    ReaderAnswer,
    ResearchEvent,
)

MAX_CLARITY_QUESTIONS = 3
CLARITY_MAX_TOKENS = 4096
"""The check's output cap: at most three short questions, with thinking off."""

CLARITY_TRACE_SESSION = "clarity-check"


class ClarityQuestionDraft(BaseModel):
    """One question as the provider proposes it; validated by ``validated_check``."""

    model_config = ConfigDict(extra="forbid")

    dimension: ClarityDimension
    text: str
    short: str
    options: list[str]
    best_guess: str


class ClarityCheckDraft(BaseModel):
    """What the provider is asked for: the questions, possibly none."""

    model_config = ConfigDict(extra="forbid")

    questions: list[ClarityQuestionDraft] = Field(default_factory=list)


class ClarityQuestion(BaseModel):
    """One question the reader is asked (the ``ClarityCheck`` contract)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    id: str = Field(pattern=r"^q[1-3]$")
    dimension: ClarityDimension
    text: str = Field(min_length=1, max_length=200)
    short: str = Field(min_length=1, max_length=40)
    options: list[str] = Field(min_length=2, max_length=4)
    best_guess: str = Field(min_length=1)

    @field_validator("text", "short", "best_guess", mode="before")
    @classmethod
    def one_line(cls, value: object) -> object:
        """Model-generated text reaches the planner's prompt through ``ReaderAnswer``
        (``text``, ``short``, ``value``), so each is one single-spaced line, and
        ``best_guess`` is collapsed the same way as the options it must match."""
        return collapse_whitespace(value) if isinstance(value, str) else value

    @field_validator("options", mode="before")
    @classmethod
    def one_line_options(cls, value: object) -> object:
        if isinstance(value, list):
            return [
                collapse_whitespace(option) if isinstance(option, str) else option
                for option in value
            ]
        return value

    @model_validator(mode="after")
    def options_are_distinct_and_hold_the_best_guess(self) -> ClarityQuestion:
        if any(not option.strip() or len(option) > 80 for option in self.options):
            raise ValueError("each option is 1-80 characters")
        if len(set(self.options)) != len(self.options):
            raise ValueError("options repeat")
        if self.best_guess not in self.options:
            raise ValueError("best_guess is not one of the options")
        return self


class ClarityCheck(BaseModel):
    """The check's result: 0-3 questions, ``q1``..``q3`` in order."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    questions: list[ClarityQuestion] = Field(
        default_factory=list, max_length=MAX_CLARITY_QUESTIONS
    )

    @model_validator(mode="after")
    def ids_follow_the_order(self) -> ClarityCheck:
        if [q.id for q in self.questions] != [
            f"q{n}" for n in range(1, len(self.questions) + 1)
        ]:
            raise ValueError("question ids must be q1..qn in order")
        return self


NO_QUESTIONS = ClarityCheck()
ClarityChecker: TypeAlias = Callable[[str, ConfigSettings], Awaitable[ClarityCheck]]


def validated_check(draft: ClarityCheckDraft) -> ClarityCheck:
    """Stamp ``q1``..``qn`` on the drafted questions, or ask nothing at all.

    More than three questions, a best guess that is not one of its options, or
    any other invalid field drops the whole output: a check that
    cannot be trusted asks nothing rather than asking something half-formed.
    """
    try:
        return ClarityCheck(
            questions=[
                ClarityQuestion(id=f"q{n}", **question.model_dump())
                for n, question in enumerate(draft.questions, start=1)
            ]
        )
    except ValidationError:
        return NO_QUESTIONS


CLARITY_SYSTEM_PROMPT = (
    "You check a research question before any research starts. You have no "
    "tools and need none: the question is printed in the request."
)

CLARITY_INSTRUCTION = (
    "Decide whether the question leaves open something whose answer would "
    "change the research plan, and ask the reader about it.\n"
    "- Ask only about these dimensions: geography (which place the answer "
    "covers), period (which time span it covers), purpose (what the reader "
    "needs it for) and scope (how broad it should be).\n"
    "- Ask only when the answer would change the plan. Never ask about "
    "something the question already states.\n"
    "- Ask at most 3 questions. When nothing material is open, return an empty "
    "questions list.\n"
    "- For each question give: dimension; text, the question in plain words "
    "(at most 200 characters); short, a one- or two-word label for it (at most "
    "40 characters); options, 2 to 4 short answers the reader can pick (each at "
    "most 80 characters); best_guess, the option you would assume if the reader "
    "does not answer, copied exactly from options."
)


def clarity_messages(question: str) -> list[ChatMessage]:
    """The one tool-free request the live check sends."""
    return [
        ChatMessage(role="developer", content=CLARITY_SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content=(
                f"# Check requirements\n{CLARITY_INSTRUCTION}\n\n"
                f"# Research question\n{question}"
            ),
        ),
    ]


def clarity_llm_config(llm: LLMConfig) -> LLMConfig:
    """The configured provider and model, with thinking disabled."""
    return llm.model_copy(update={"thinking_mode": "disabled"})


async def live_clarity_check(
    question: str,
    settings: ConfigSettings,
    *,
    completer: Any | None = None,
) -> ClarityCheck:
    """Ask the configured provider what, if anything, to ask the reader.

    ``completer`` replaces the provider in tests; production builds the chat
    adapter from ``settings.llm`` with thinking disabled. Provider failures
    propagate: the session store treats any exception as "ask nothing".
    """
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False, project="deep-research-clarity-check", api_key=None
        )
    )
    provider = (
        completer
        if completer is not None
        else build_chat_provider(clarity_llm_config(settings.llm), tracker)
    )
    async with tracker.session_span(CLARITY_TRACE_SESSION, question):
        draft = await provider.complete_structured(
            clarity_messages(question),
            ClarityCheckDraft,
            agent_name=None,
            max_tokens=CLARITY_MAX_TOKENS,
        )
    return validated_check(draft)


# --- replay mode --------------------------------------------------------------

REPLAY_CLARIFY_HEADER = "x-replay-clarify"
requested_clarify: ContextVar[bool] = ContextVar(
    "deep_research_replay_clarify", default=False
)
"""Set by ``ReplayCaseMiddleware`` from ``X-Replay-Clarify`` on ``POST /research``.

It travels into the session's task the way ``requested_case`` does: the task is
created inside the request's own context (``SessionStore.start``)."""

REPLAY_CLARITY_CHECK = ClarityCheck(
    questions=[
        ClarityQuestion(
            id="q1",
            dimension="geography",
            text="Which region should this cover?",
            short="Region",
            options=["United States", "European Union", "Global"],
            best_guess="Global",
        ),
        ClarityQuestion(
            id="q2",
            dimension="period",
            text="How recent should the sources be?",
            short="Period",
            options=["Last 12 months", "Since 2023", "Any time"],
            best_guess="Since 2023",
        ),
        ClarityQuestion(
            id="q3",
            dimension="purpose",
            text="What will you use it for?",
            short="For",
            options=[
                "General understanding",
                "A project or investment decision",
                "Policy or regulation work",
            ],
            best_guess="General understanding",
        ),
    ]
)
"""The fixed set the replay checker asks when ``X-Replay-Clarify: on``."""


async def scripted_clarity_check(question: str, settings: object) -> ClarityCheck:
    """Replay mode's checker: no questions unless the request asked for them."""
    del question, settings
    return REPLAY_CLARITY_CHECK if requested_clarify.get() else NO_QUESTIONS


# --- answers --------------------------------------------------------------------


class AnswerValidationError(ValueError):
    """Submitted answers that do not fit the questions asked (a 422)."""

    def __init__(self, issues: Sequence[tuple[tuple[str | int, ...], str]]) -> None:
        super().__init__("the answers do not fit the questions asked")
        self.issues = list(issues)

    def errors(self) -> list[dict[str, Any]]:
        """The issues in ``RequestValidationError``'s own shape: location and type only."""
        return [
            {"loc": location, "type": kind, "msg": "invalid answer"}
            for location, kind in self.issues
        ]


def resolve_answers(
    questions: Sequence[ClarityQuestion],
    submitted: Iterable[Any] = (),
) -> tuple[ReaderAnswer, ...]:
    """Every question's answer, in question order; a skipped one takes its best guess.

    ``submitted`` holds objects with ``question_id`` and exactly one of
    ``choice`` or ``text`` (``api.models.ClarificationAnswer``). An unknown
    question, a question answered twice, or a choice that is not one of its
    options raises ``AnswerValidationError`` naming each one.
    """
    by_id = {question.id: question for question in questions}
    given: dict[str, tuple[str, str]] = {}
    issues: list[tuple[tuple[str | int, ...], str]] = []
    for index, answer in enumerate(submitted):
        location: tuple[str | int, ...] = ("body", "answers", index)
        question = by_id.get(answer.question_id)
        if question is None:
            issues.append(((*location, "question_id"), "unknown_question"))
            continue
        if answer.question_id in given:
            issues.append(((*location, "question_id"), "duplicate_question"))
            continue
        if answer.choice is not None:
            if answer.choice not in question.options:
                issues.append(((*location, "choice"), "choice_not_offered"))
                continue
            given[answer.question_id] = (answer.choice, "chosen")
        else:
            given[answer.question_id] = (answer.text, "typed")
    if issues:
        raise AnswerValidationError(issues)
    return tuple(
        ReaderAnswer(
            question_id=question.id,
            dimension=question.dimension,
            text=question.text,
            short=question.short,
            value=given.get(question.id, (question.best_guess, "best_guess"))[0],
            source=given.get(question.id, (question.best_guess, "best_guess"))[1],
        )
        for question in questions
    )


def clarification_requested_event(
    questions: Sequence[ClarityQuestion], deadline_at: datetime
) -> ResearchEvent:
    """``session.clarification.requested``: the questions and when the wait ends."""
    return ResearchEvent(
        event_type="session.clarification.requested",
        source="api",
        message="The research is waiting for the reader's answers.",
        metadata={
            "questions": [question.model_dump(mode="json") for question in questions],
            "deadline_at": deadline_at.isoformat(timespec="milliseconds"),
        },
    )


def clarification_answered_event(
    answers: Sequence[ReaderAnswer], reason: str
) -> ResearchEvent:
    """``session.clarification.answered``: what the run starts with, and why now."""
    return ResearchEvent(
        event_type="session.clarification.answered",
        source="api",
        message="The research starts with the reader's answers.",
        metadata={
            "answers": [
                {
                    "question_id": answer.question_id,
                    "value": answer.value,
                    "source": answer.source,
                }
                for answer in answers
            ],
            "reason": reason,
        },
    )
