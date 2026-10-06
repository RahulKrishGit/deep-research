"""Reader notes in the agents' requests.

One block, one line per note — ``- {restatement} ({kinds})`` — under a lead
sentence that says how *this* step uses the notes. Only interpreted notes that
no later note replaces are rendered, and every renderer returns ``""`` when
there is none, so a run without notes builds byte-identical requests.

The Evidence Verifier has no entry here, deliberately: verification is never
affected by a note, and no verifier request carries this block.

The run's board is reached at call time: ``deep_research.runtime`` imports every
agent while its package initialises, so a module-level import of
``runtime.notes`` from an agent (or from ``graph.nodes``) would close an import
cycle whenever the graph is imported first.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Sequence
from typing import Literal, Protocol, TypeAlias

from deep_research.utils.types import (
    NOTE_COVERAGE_PREFIX,
    NOTE_TOPIC_TITLE_PREFIX,
    EvidenceTarget,
    ReaderNote,
    SubTopic,
    active_reader_notes,
    with_board_notes,
)

STEERING_KINDS: frozenset[str] = frozenset({"emphasis", "exclude", "scope", "about_reader"})
"""The kinds that steer the run's own steps; ``new_angle``
asks for research of its own instead."""

NOTES_WAIT_S = 30.0
"""The longest a step waits for a note still being read before it moves on.

Shared by the review node, before it reads its route, and the
researcher, before its research window closes: twice
``hitl.note_interpret_timeout_s``'s default of 15 s, so with the default every reading in
flight when the wait begins has ended first, and a raised timeout (up to ten minutes) holds
either step for this long at most. A note still being read then is left out, and a later
step takes it in once it is read.
"""

NoteTopicReason: TypeAlias = Literal["reader_note", "no_evidence"]
"""Why a note has a sub-topic: the reader asked for research (a research note), or the
review found no evidence for a steering note (its one note pass)."""

_NOTE_TOPIC_RATIONALES: dict[str, str] = {
    "reader_note": "The reader asked for this in a note.",
    "no_evidence": (
        "The reader asked for this in a note, and the review found no evidence for it yet."
    ),
}

PLANNING_NOTES = (
    "The reader added these notes while the run was going; each line is the "
    "note as the run understood it, and a later note replaces an earlier one it "
    "contradicts. Plan within them: an emphasis note gives its subject more "
    "weight, an exclude note leaves its subject out, a scope note narrows the "
    "plan to its scope, and an about_reader note says who the report is for. A "
    "new_angle note is researched as a sub-topic of its own that the run adds to "
    "this plan once it is final: do not plan a sub-topic for it, and a subject "
    "only a new_angle note asks for is not a missing part of the question."
)
RESEARCH_NOTES = (
    "The reader added these notes while the run was going. From your next "
    "search on, apply each one that bears on this sub-topic: an emphasis note "
    "gives its subject more weight, an exclude note means stop pursuing what "
    "it leaves out, and a scope note keeps later searches and reads inside its "
    "scope. Ignore a note that does not bear on this sub-topic."
)
EXTRACTION_NOTES = (
    "The reader added these notes while the run was going. A fact outside a "
    "scope note's scope, or about what an exclude note leaves out, is not a "
    "finding for this run; every other rule above still applies."
)
SOURCE_NOTES = (
    "The reader added these notes while the run was going. They bear on how "
    "relevant a source is to what the reader wants, and never on its authority "
    "or its recency."
)
WRITING_NOTES = (
    "The reader added these notes while the run was going. An emphasis note "
    "gives its subject more of the report, an exclude note leaves its subject "
    "out, a scope note leaves out-of-scope findings out and says so in one "
    "sentence, and an about_reader note sets the level of the writing. A note "
    "never changes what a finding says, and never licenses a sentence no "
    "verified finding carries."
)
REVIEW_NOTES = (
    "The reader added these notes while the run was going. For each note id "
    "below, return exactly one entry in note_dispositions: honoured when the "
    "report follows the note; ignored_with_evidence when the findings shown "
    "would let the report follow it and the report does not; no_evidence when "
    "no finding shown bears on it. A note never changes how a statement is "
    "judged against its findings."
)


class NoteLine(Protocol):
    """What one rendered line reads: a ``ReaderNote``, or the review's own view of one."""

    note_id: str
    restatement: str
    kinds: Sequence[str]


def is_research_note(note: ReaderNote) -> bool:
    """A research note asks the run to research something: its kinds include ``new_angle``."""
    return "new_angle" in note.kinds


def has_steering_kind(note: ReaderNote) -> bool:
    """Whether the note steers the run's steps: emphasis, exclude, scope or about_reader.

    Every steering note has one; a research note that has one too is a mixed note.
    """
    return any(kind in STEERING_KINDS for kind in note.kinds)


def steering_view(note: ReaderNote) -> ReaderNote | None:
    """The note as every steering request prints it.

    A note without ``new_angle`` is returned unchanged; a mixed note is a copy with
    ``new_angle`` left out of its kinds; a note whose only kind is ``new_angle`` has no
    steering half, so ``None``.
    """
    if not is_research_note(note):
        return note
    if not has_steering_kind(note):
        return None
    return note.model_copy(
        update={"kinds": [kind for kind in note.kinds if kind != "new_angle"]}
    )


def steering_notes(notes: Sequence[ReaderNote]) -> list[ReaderNote]:
    """``steering_view`` of each note, the ``None`` ones dropped, in the given order."""
    return [view for note in notes if (view := steering_view(note)) is not None]


def board_notes() -> list[ReaderNote]:
    """The run's interpreted notes from its bound board, or ``[]`` with none bound."""
    from deep_research.runtime import notes  # noqa: PLC0415 - see the module docstring

    board = notes.current_note_board()
    return [] if board is None else board.snapshot()


async def notes_settled(*, timeout: float | None = None) -> bool:
    """Wait until the bound board has no note still being interpreted.

    ``timeout`` bounds the wait in seconds (``None``: no bound). ``True`` once
    nothing is being read, and at once with no board bound; ``False`` when the
    bound passed first, with the notes still being read left on the board.
    """
    from deep_research.runtime import notes  # noqa: PLC0415 - see the module docstring

    board = notes.current_note_board()
    if board is None:
        return True
    try:
        async with asyncio.timeout(timeout):
            await board.settled()
    except TimeoutError:
        return False
    return True


def live_reader_notes(state_notes: Sequence[ReaderNote]) -> list[ReaderNote]:
    """The notes a request built *now* reads: the state's, then the board's newer ones, active only."""
    return active_reader_notes(with_board_notes(state_notes, board_notes()))


def research_reader_notes(notes: Sequence[ReaderNote]) -> list[ReaderNote]:
    """The notes a research loop's turns and its extraction apply: their steering views.

    A note whose only kind is ``new_angle`` is researched
    as its own topic instead, and a mixed note steers with ``new_angle`` left out.
    """
    return steering_notes(notes)


def render_reader_notes(
    notes: Sequence[NoteLine],
    *,
    instruction: str,
    with_ids: bool = False,
) -> str:
    """``instruction``, then one line per note; ``""`` when there is none.

    ``with_ids`` prefixes each line with the note's id, for the review, whose
    reply names each note it judged.
    """
    if not notes:
        return ""
    lines = [instruction]
    for note in notes:
        prefix = f"{note.note_id}: " if with_ids else ""
        lines.append(f"- {prefix}{note.restatement} ({', '.join(note.kinds)})")
    return "\n".join(lines)


def note_sub_topic(
    note: ReaderNote, *, priority: int, reason: NoteTopicReason
) -> SubTopic:
    """The sub-topic that researches one reader note.

    Titled ``Your note: {restatement}``, with coverage id ``note-{note_id}`` and one
    required target per question the note raised — or, for a note that raised none, the
    note's own restatement — each carrying the note's scope. ``reason`` says why the
    topic exists, in its rationale: the reader asked for research (``reader_note``), or
    the review found no evidence for a steering note (``no_evidence``).
    """
    coverage_id = f"{NOTE_COVERAGE_PREFIX}{note.note_id}"
    questions = list(note.new_questions) or [note.restatement]
    geography = note.scope.geography if note.scope else None
    period = note.scope.period if note.scope else None
    return SubTopic(
        coverage_id=coverage_id,
        title=f"{NOTE_TOPIC_TITLE_PREFIX}{note.restatement}",
        rationale=_NOTE_TOPIC_RATIONALES[reason],
        search_queries=questions,
        success_criteria=[
            f"A checked source answers: {question}" for question in questions
        ],
        priority=priority,
        evidence_targets=[
            EvidenceTarget(
                target_id=f"{coverage_id}-target-{number:02d}",
                coverage_id=coverage_id,
                question=question,
                required=True,
                measure=question,
                geography=geography,
                period=period,
            )
            for number, question in enumerate(questions, start=1)
        ],
    )


def board_version() -> int | None:
    """The bound board's change count, or ``None`` with no board bound."""
    from deep_research.runtime import notes  # noqa: PLC0415 - see the module docstring

    board = notes.current_note_board()
    return None if board is None else board.version


async def wait_for_board_change(seen: int) -> int:
    """Return the bound board's change count once it differs from ``seen``.

    Called only while a board is bound: ``board_version`` returned ``seen``.
    """
    from deep_research.runtime import notes  # noqa: PLC0415 - see the module docstring

    board = notes.current_note_board()
    if board is None:
        raise RuntimeError("no note board is bound for this run")
    return await board.wait_for_change(seen)


def notes_being_read() -> bool:
    """Whether the bound board holds a received note whose reading has not ended."""
    from deep_research.runtime import notes  # noqa: PLC0415 - see the module docstring

    board = notes.current_note_board()
    return board is not None and bool(board.pending)


def research_notes_without_a_topic(
    state_notes: Sequence[ReaderNote], known_coverage_ids: Collection[str]
) -> list[ReaderNote]:
    """The active research notes, the state's then the board's, whose ``note-{id}`` is
    not in ``known_coverage_ids``, in receipt order."""
    return [
        note
        for note in live_reader_notes(state_notes)
        if is_research_note(note)
        and f"{NOTE_COVERAGE_PREFIX}{note.note_id}" not in known_coverage_ids
    ]


__all__ = [
    "EXTRACTION_NOTES",
    "NOTES_WAIT_S",
    "NoteLine",
    "NoteTopicReason",
    "PLANNING_NOTES",
    "RESEARCH_NOTES",
    "REVIEW_NOTES",
    "SOURCE_NOTES",
    "STEERING_KINDS",
    "WRITING_NOTES",
    "board_notes",
    "board_version",
    "has_steering_kind",
    "is_research_note",
    "live_reader_notes",
    "note_sub_topic",
    "notes_being_read",
    "notes_settled",
    "render_reader_notes",
    "research_notes_without_a_topic",
    "research_reader_notes",
    "steering_notes",
    "steering_view",
    "wait_for_board_change",
]
