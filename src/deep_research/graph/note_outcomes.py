"""What a run concluded about each reader note.

The finalizer reads each note's outcome when it stamps the report's note lines,
so this lives in ``graph``, which never imports ``api``: importing
``deep_research.api`` builds the app (``api/app.py``), which imports
``deep_research.main``, which imports this package. ``api/notes.py`` re-exports
every name here, so each caller keeps its import.
"""

from __future__ import annotations

from typing import Literal, TypeAlias

from deep_research.agents.reader_notes import has_steering_kind, is_research_note
from deep_research.utils.types import (
    NOTE_COVERAGE_PREFIX,
    ReaderNote,
    ReportComposition,
    ReportNoteLine,
    ResearchState,
    active_reader_notes,
    note_label,
)

NoteOutcome: TypeAlias = Literal[
    "covered", "not_found", "not_addressed", "pending", "not_checked", "replaced"
]
"""``pending`` only while a session goes on; ``not_checked`` once it has ended with nothing
to judge the note by."""


_VERDICT_OUTCOMES: dict[str, NoteOutcome] = {
    "honoured": "covered",
    "ignored_with_evidence": "not_addressed",
    "no_evidence": "not_found",
}


def note_outcome(
    note_id: str, state: ResearchState | None, *, terminal: bool
) -> NoteOutcome:
    """What the run concluded about one note.

    A research note — its kinds include ``new_angle``, a mixed note's new_angle
    half included — is decided by its own ``note-{id}`` topic's targets, never
    by the review: ``covered`` once a verified finding answers one of them,
    ``not_found`` once the composition lists one as searched and not found. A
    steering note is decided by the latest review's verdict: ``honoured`` is
    ``covered``, ``ignored_with_evidence`` is ``not_addressed`` (never
    ``covered``), ``no_evidence`` is ``not_found``. A
    note a later note replaced is ``replaced``. Anything else — no state, a
    note the state does not hold, no topic, no verdict — is ``pending`` while
    the run goes on, and ``not_checked`` once it has ended (``terminal``): a
    finished session never reports a note ``pending``.
    """
    waiting: NoteOutcome = "not_checked" if terminal else "pending"
    if state is None:
        return waiting
    note = next((held for held in state.reader_notes if held.note_id == note_id), None)
    if note is None:
        return waiting
    if note_id not in {held.note_id for held in active_reader_notes(state.reader_notes)}:
        return "replaced"
    if is_research_note(note):
        return _research_outcome(note_id, state, waiting=waiting)
    return _verdict_outcome(note_id, state, waiting=waiting)


def note_steering_outcome(
    note_id: str,
    state: ResearchState | None,
    *,
    terminal: bool,
    note: ReaderNote | None = None,
) -> NoteOutcome | None:
    """A mixed note's steering half, or ``None``.

    ``None`` for every note that is not mixed. For a mixed note: ``pending``
    (``not_checked`` once the run has ended) with no state, ``replaced`` when a
    later note replaced it, and otherwise the latest review's verdict mapped as
    ``note_outcome`` maps a steering note's. ``note`` is the board's reading of
    the note, which says whether it is mixed while no state holds it.
    """
    waiting: NoteOutcome = "not_checked" if terminal else "pending"
    held = (
        None
        if state is None
        else next((item for item in state.reader_notes if item.note_id == note_id), None)
    )
    reading = held if held is not None else note
    if reading is None or not (is_research_note(reading) and has_steering_kind(reading)):
        return None
    if state is None or held is None:
        return waiting
    if note_id not in {item.note_id for item in active_reader_notes(state.reader_notes)}:
        return "replaced"
    return _verdict_outcome(note_id, state, waiting=waiting)


def _research_outcome(
    note_id: str, state: ResearchState, *, waiting: NoteOutcome
) -> NoteOutcome:
    """A research note's result, from its own topic's targets."""
    coverage_id = f"{NOTE_COVERAGE_PREFIX}{note_id}"
    topic = next((item for item in state.sub_topics if item.coverage_id == coverage_id), None)
    if topic is None:
        return waiting
    targets = {target.target_id for target in topic.evidence_targets}
    answered = set(state.quality.answered_target_ids) if state.quality is not None else set()
    if targets & answered:
        return "covered"
    not_found = state.composition.not_found if state.composition is not None else []
    if any(row.target_id in targets and row.searched for row in not_found):
        return "not_found"
    return waiting


def _verdict_outcome(
    note_id: str, state: ResearchState, *, waiting: NoteOutcome
) -> NoteOutcome:
    """A steering note's result, or a mixed note's steering half's: the latest review's verdict."""
    review = state.report_review
    verdicts = (
        {entry.note_id: entry.status for entry in review.note_dispositions}
        if review is not None
        else {}
    )
    verdict = verdicts.get(note_id)
    return waiting if verdict is None else _VERDICT_OUTCOMES[verdict]


#: A steering note's line, by its terminal outcome.
STEERING_NOTE_TEXT: dict[str, str] = {
    "covered": "Followed: {restatement}",
    "not_addressed": "Not followed in this report: {restatement}",
    "not_found": "No source we could check covers this: {restatement}",
    "not_checked": "Not checked: {restatement}",
}
#: A research note's line when its topic kept no line. A research
#: note's terminal outcome is covered, not_found or not_checked.
RESEARCH_NOTE_TEXT: dict[str, str] = {
    "covered": "See the section below.",
    "not_found": "No source we could check covers this.",
    "not_checked": "Not researched.",
}
#: The sentence a mixed note's steering half adds.
STEERING_HALF_TEXT: dict[str, str] = {
    "covered": "The rest of your note was followed.",
    "not_addressed": "The rest of your note was not followed in this report.",
    "not_found": "No source we could check bears on the rest of your note.",
    "not_checked": "The rest of your note was not checked.",
}


def report_note_lines(state: ResearchState, composition: ReportComposition) -> list[ReportNoteLine]:
    """One line per active reader note, in
    receipt order, with its terminal outcome (and a mixed note's steering
    half's). Whatever the note's kind, a note whose ``note-{id}`` topic kept a
    bottom-line line points at it, so that line prints once, inside the note's
    row, and the note has one row. A research note without a kept line names
    its outcome in words; a steering note's line is its outcome and its
    restatement, after its kept topic line when it has one (a steering note the
    run bought a note pass for owns a topic); a mixed note adds one sentence
    for its steering half. A replaced note has no line."""
    kept = (
        {line.coverage_id: line.statement_id for line in composition.bottom_line.topic_lines}
        if composition.bottom_line is not None else {}
    )
    lines: list[ReportNoteLine] = []
    for note in active_reader_notes(state.reader_notes):
        outcome = note_outcome(note.note_id, state, terminal=True)
        steering = note_steering_outcome(note.note_id, state, terminal=True)
        statement_id = kept.get(f"{NOTE_COVERAGE_PREFIX}{note.note_id}")
        if is_research_note(note):
            parts = [] if statement_id else [RESEARCH_NOTE_TEXT.get(outcome, RESEARCH_NOTE_TEXT["not_checked"])]
            if steering is not None:
                parts.append(STEERING_HALF_TEXT[steering])
            text = " ".join(parts)
        else:
            text = STEERING_NOTE_TEXT[outcome].format(restatement=note.restatement)
        lines.append(ReportNoteLine(
            note_id=note.note_id, label=note_label(note), outcome=outcome,
            steering_outcome=steering, statement_id=statement_id, text=text,
        ))
    return lines


__all__ = [
    "RESEARCH_NOTE_TEXT",
    "STEERING_HALF_TEXT",
    "STEERING_NOTE_TEXT",
    "NoteOutcome",
    "note_outcome",
    "note_steering_outcome",
    "report_note_lines",
]
