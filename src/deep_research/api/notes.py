"""Reader notes at the API (live-briefs spec §4.6, D8-D11a): the interpreter,
its fallback, the two session events, and each note's outcome.

A reader may add up to ten notes while a run is going. The notes route accepts
one (``session.note.received``), and a ``NoteInterpreter`` reads it: live mode
asks the configured provider — the same path as the one-time check, thinking
disabled, structured output — and replay mode restates the note as written.
The interpreted note joins the run's board (``runtime/notes.py``), where every
step but verification reads it (D10), and ``session.note.interpreted`` carries
the run's reading back to the page, which acknowledges it (D9).

An interpretation never blocks a note. A call that fails, runs out of time or
answers with something invalid keeps the note as written, as an emphasis, and
its acknowledgement says so (``fallback``).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, NamedTuple, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from deep_research.api.clarify import clarity_llm_config
from deep_research.graph.note_outcomes import (
    NoteOutcome,
    note_outcome,
    note_steering_outcome,
)
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.providers import ChatMessage, build_chat_provider
from deep_research.runtime.notes import NoteBoard, ReceivedNote
from deep_research.utils.config import ConfigSettings
from deep_research.utils.text import collapse_whitespace
from deep_research.utils.types import (
    MAX_NOTE_SHORT_CHARS,
    ReaderNote,
    ReaderNoteKind,
    ReaderNoteScope,
    ResearchEvent,
    ResearchState,
    active_reader_notes,
)

NOTE_MAX_TOKENS = 2048
"""The interpretation's output cap: one short structured reading, thinking off."""
NOTE_TRACE_SESSION = "note-interpreter"
MAX_RESTATEMENT_CHARS = 120
MAX_NEW_QUESTIONS = 3
MAX_NEW_QUESTION_CHARS = 200


class NoteScopeDraft(BaseModel):
    """The scope a note sets, as the provider proposes it."""

    model_config = ConfigDict(extra="forbid")

    geography: str | None = None
    period: str | None = None


class NoteInterpretationDraft(BaseModel):
    """What the provider is asked for; validated by ``validated_interpretation``.

    ``kinds`` holds plain strings so one invented kind is caught here and the
    note falls back, rather than the schema refusing the reply.
    """

    model_config = ConfigDict(extra="forbid")

    kinds: list[str] = Field(default_factory=list)
    restatement: str = ""
    short: str = ""
    scope: NoteScopeDraft | None = None
    new_questions: list[str] = Field(default_factory=list)
    replaces: str | None = None


class NoteInterpretation(BaseModel):
    """One note's reading (live-briefs spec §4.6): what kind of note, in plain words."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    kinds: list[ReaderNoteKind] = Field(min_length=1, max_length=3)
    restatement: str = Field(min_length=1, max_length=500)
    short: str = Field(default="", max_length=MAX_NOTE_SHORT_CHARS)
    """The note's subject in 1-3 words for a label, or ``""`` when the reading named
    none (notes-progress-report spec §7.2); the board's note then derives it."""
    scope: ReaderNoteScope | None = None
    new_questions: list[str] = Field(default_factory=list, max_length=MAX_NEW_QUESTIONS)
    replaces: str | None = None

    @field_validator("restatement", mode="before")
    @classmethod
    def restatement_is_one_line(cls, value: object) -> object:
        """The fallback and replay readings restate the note as written: even then the
        restatement is one line, as it is one line of every agent's request."""
        return collapse_whitespace(value) if isinstance(value, str) else value

    @field_validator("new_questions", mode="before")
    @classmethod
    def new_questions_are_one_line(cls, value: object) -> object:
        if isinstance(value, list):
            return [
                collapse_whitespace(question) if isinstance(question, str) else question
                for question in value
            ]
        return value

    @model_validator(mode="after")
    def kinds_are_distinct_and_questions_short(self) -> NoteInterpretation:
        if len(set(self.kinds)) != len(self.kinds):
            raise ValueError("kinds repeat")
        if any(
            not question.strip() or len(question) > MAX_NEW_QUESTION_CHARS
            for question in self.new_questions
        ):
            raise ValueError("each new question is 1-200 characters")
        return self


NoteInterpreter: TypeAlias = Callable[
    [str, str, Sequence[ReaderNote], Any], Awaitable[NoteInterpretation]
]
"""``(note text, research question, the run's earlier interpreted notes, settings)``."""


def fallback_interpretation(text: str) -> NoteInterpretation:
    """The note as written, as an emphasis (spec §4.6: the interpreter failed)."""
    return NoteInterpretation(kinds=["emphasis"], restatement=text)


def validated_interpretation(
    draft: NoteInterpretationDraft,
    *,
    earlier: Sequence[ReaderNote],
) -> NoteInterpretation | None:
    """The draft as a reading of the note, or ``None`` when any field is invalid.

    Every text field is first collapsed to one single-spaced line, because each
    is printed as one line of an agent's request. Invalid is then: no kind, more
    than three, one outside the vocabulary or repeated; an empty restatement or
    one over 120 characters; more than three new questions, or one empty or
    over 200 characters; a scope field over 120 characters. A ``replaces`` that
    names no earlier note of this run is dropped rather than failing the
    reading: the note itself stands. So is a ``short`` that is not one to three
    words of at most 24 characters (notes-progress-report spec §7.2): a label
    never costs the reading, and the board's note derives one instead.
    """
    earlier_ids = {note.note_id for note in earlier}
    restatement = collapse_whitespace(draft.restatement)
    if not restatement or len(restatement) > MAX_RESTATEMENT_CHARS:
        return None
    short = collapse_whitespace(draft.short)
    if not 1 <= len(short.split()) <= 3 or len(short) > MAX_NOTE_SHORT_CHARS:
        short = ""
    scope = draft.scope
    geography = collapse_whitespace(scope.geography or "") if scope is not None else ""
    period = collapse_whitespace(scope.period or "") if scope is not None else ""
    try:
        reading = NoteInterpretation(
            kinds=[kind.strip() for kind in draft.kinds],  # type: ignore[misc]
            restatement=restatement,
            short=short,
            scope=(
                ReaderNoteScope(geography=geography or None, period=period or None)
                if geography or period
                else None
            ),
            new_questions=[collapse_whitespace(question) for question in draft.new_questions],
            replaces=(
                draft.replaces.strip()
                if draft.replaces and draft.replaces.strip() in earlier_ids
                else None
            ),
        )
    except ValidationError:
        return None
    return reading


NOTE_SYSTEM_PROMPT = (
    "You read one note a reader added to a research run that is already going. "
    "You have no tools and need none: the question, the note and the reader's "
    "earlier notes are printed in the request."
)

NOTE_INSTRUCTION = (
    "Say what the note asks the run to do.\n"
    "- kinds: one to three of emphasis (give a subject more weight), exclude "
    "(leave a subject out), scope (narrow to a place or a period), new_angle "
    "(research something the plan does not cover) and about_reader (who the "
    "report is for, or at what level to write).\n"
    "- restatement: what the note asks, in plain words, at most 120 "
    "characters, starting in lower case and never quoting the note back, for "
    "example \"more weight on fire-safety standards\".\n"
    "- short: the note's subject in one to three words for a label, lower case.\n"
    "- scope: the geography or period a scope note sets, or null.\n"
    "- new_questions: for a new_angle note, or a scope that widens the "
    "question, up to 3 research questions it raises; otherwise an empty list.\n"
    "- replaces: the id of the one earlier note this note contradicts, or "
    "null."
)


def note_messages(
    text: str,
    question: str,
    earlier: Sequence[ReaderNote],
) -> list[ChatMessage]:
    """The one tool-free request a live interpretation sends.

    The new note is collapsed to one line here too: the route already does it,
    but this request is where a line break in reader text would start a line of
    its own, so it does not rely on the caller.
    """
    listed = "\n".join(
        f"- {note.note_id}: {note.restatement}" for note in active_reader_notes(earlier)
    ) or "(none)"
    text = collapse_whitespace(text)
    return [
        ChatMessage(role="developer", content=NOTE_SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content=(
                f"# Reading requirements\n{NOTE_INSTRUCTION}\n\n"
                f"# Research question\n{question}\n\n"
                f"# The reader's earlier notes\n{listed}\n\n"
                f"# The new note\n{text}"
            ),
        ),
    ]


async def live_note_interpreter(
    text: str,
    question: str,
    earlier: Sequence[ReaderNote],
    settings: ConfigSettings,
    *,
    completer: Any | None = None,
) -> NoteInterpretation:
    """Ask the configured provider what the note asks, with thinking disabled.

    ``completer`` replaces the provider in tests; production builds the chat
    adapter from ``settings.llm`` exactly as the one-time check does. A
    provider failure propagates, and so does an invalid reading
    (``ValueError``): the session store falls back on either.
    """
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False, project="deep-research-note-interpreter", api_key=None
        )
    )
    provider = (
        completer
        if completer is not None
        else build_chat_provider(clarity_llm_config(settings.llm), tracker)
    )
    async with tracker.session_span(NOTE_TRACE_SESSION, question):
        draft = await provider.complete_structured(
            note_messages(text, question, earlier),
            NoteInterpretationDraft,
            agent_name=None,
            max_tokens=NOTE_MAX_TOKENS,
        )
    reading = validated_interpretation(draft, earlier=earlier)
    if reading is None:
        raise ValueError("the note interpretation broke its contract")
    return reading


async def scripted_note_interpreter(
    text: str,
    question: str,
    earlier: Sequence[ReaderNote],
    settings: object,
) -> NoteInterpretation:
    """Replay mode's interpreter (spec §4.8): the note restated as written, an emphasis."""
    del question, earlier, settings
    return NoteInterpretation(kinds=["emphasis"], restatement=text)


_NOTE_ID = re.compile(r"n([1-9]\d*)")


def _numbered_before(candidate: str, note_id: str) -> bool:
    """Whether ``candidate`` is a note id numbered strictly below ``note_id``.

    Both must be ``n<number>`` exactly: ``x1`` and ``n-1`` are no note's id, so
    neither is numbered below anything.
    """
    earlier, own = _NOTE_ID.fullmatch(candidate), _NOTE_ID.fullmatch(note_id)
    return earlier is not None and own is not None and int(earlier[1]) < int(own[1])


def reader_note(received: ReceivedNote, reading: NoteInterpretation) -> ReaderNote:
    """The board's record of one interpreted note.

    The interpreter cannot know the note's own id, so this is where a ``replaces``
    that is the note itself or a later note is dropped, as an unknown one is: a
    note replaces only an earlier note, or ``active_reader_notes`` would silence
    the wrong one.
    """
    replaces = (
        reading.replaces
        if reading.replaces and _numbered_before(reading.replaces, received.note_id)
        else None
    )
    return ReaderNote(
        note_id=received.note_id,
        text=received.text,
        received_at=received.received_at,
        received_during=received.received_during,
        kinds=list(reading.kinds),
        restatement=reading.restatement,
        short=reading.short,
        scope=reading.scope,
        new_questions=list(reading.new_questions),
        replaces=replaces,
    )


def note_received_event(received: ReceivedNote) -> ResearchEvent:
    """``session.note.received``: the note as the reader wrote it, published at once."""
    return ResearchEvent(
        event_type="session.note.received",
        source="api",
        message="The research received a note from the reader.",
        metadata={"note_id": received.note_id, "text": received.text},
    )


def note_interpreted_event(note: ReaderNote, *, fallback: bool) -> ResearchEvent:
    """``session.note.interpreted``: the run's reading of the note, for its acknowledgement."""
    return ResearchEvent(
        event_type="session.note.interpreted",
        source="api",
        message="The research read the reader's note.",
        metadata={
            "note_id": note.note_id,
            "restatement": note.restatement,
            "kinds": list(note.kinds),
            "replaces": note.replaces,
            "fallback": fallback,
        },
    )


class NoteRecord(NamedTuple):
    """One accepted note as the session response reports it (notes-progress-report spec §5.6)."""

    received: ReceivedNote
    restatement: str | None
    """The run's reading of the note, ``None`` until it is read."""
    outcome: NoteOutcome
    steering_outcome: NoteOutcome | None
    """A mixed note's steering half (D20); ``None`` for every other note."""


def note_records(
    board: NoteBoard, state: ResearchState | None, *, terminal: bool
) -> list[NoteRecord]:
    """Every accepted note in receipt order: as received, its restatement once read, and both outcomes."""
    records: list[NoteRecord] = []
    for received in board.received():
        interpreted = board.interpreted(received.note_id)
        records.append(
            NoteRecord(
                received=received,
                restatement=interpreted.restatement if interpreted is not None else None,
                outcome=note_outcome(received.note_id, state, terminal=terminal),
                steering_outcome=note_steering_outcome(
                    received.note_id, state, terminal=terminal, note=interpreted
                ),
            )
        )
    return records


__all__ = [
    "MAX_NEW_QUESTIONS",
    "MAX_RESTATEMENT_CHARS",
    "NOTE_INSTRUCTION",
    "NOTE_MAX_TOKENS",
    "NOTE_SYSTEM_PROMPT",
    "NoteInterpretation",
    "NoteInterpretationDraft",
    "NoteInterpreter",
    "NoteOutcome",
    "NoteRecord",
    "NoteScopeDraft",
    "fallback_interpretation",
    "live_note_interpreter",
    "note_interpreted_event",
    "note_messages",
    "note_outcome",
    "note_received_event",
    "note_records",
    "note_steering_outcome",
    "reader_note",
    "scripted_note_interpreter",
    "validated_interpretation",
]
