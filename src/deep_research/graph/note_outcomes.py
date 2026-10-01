"""What a run concluded about each reader note (notes-progress-report spec §5.6).

Moved here unchanged from ``api/notes.py`` by Phase C, so the finalizer can read
each note's outcome when it stamps the report's note lines (spec §7.2): ``graph``
never imports ``api``, because importing ``deep_research.api`` builds the app
(``api/app.py``), which imports ``deep_research.main``, which imports this package.
``api/notes.py`` re-exports every name here, so each caller keeps its import.
"""

from __future__ import annotations

from typing import Literal, TypeAlias

from deep_research.agents.reader_notes import has_steering_kind, is_research_note
from deep_research.utils.types import (
    NOTE_COVERAGE_PREFIX,
    ReaderNote,
    ResearchState,
    active_reader_notes,
)

NoteOutcome: TypeAlias = Literal[
    "covered", "not_found", "not_addressed", "pending", "not_checked", "replaced"
]
"""``pending`` only while a session goes on; ``not_checked`` once it has ended with nothing
to judge the note by (notes-progress-report spec §4 item 2)."""


_VERDICT_OUTCOMES: dict[str, NoteOutcome] = {
    "honoured": "covered",
    "ignored_with_evidence": "not_addressed",
    "no_evidence": "not_found",
}


def note_outcome(
    note_id: str, state: ResearchState | None, *, terminal: bool
) -> NoteOutcome:
    """What the run concluded about one note (notes-progress-report spec §5.6, D5, D31).

    A research note — its kinds include ``new_angle``, a mixed note's new_angle
    half included — is decided by its own ``note-{id}`` topic's targets, never
    by the review: ``covered`` once a verified finding answers one of them,
    ``not_found`` once the composition lists one as searched and not found. A
    steering note is decided by the latest review's verdict: ``honoured`` is
    ``covered``, ``ignored_with_evidence`` is ``not_addressed`` (never
    ``covered``: live-briefs Phase 3, O8), ``no_evidence`` is ``not_found``. A
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
    """A mixed note's steering half (notes-progress-report spec §5.6, D20), or ``None``.

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
    """A research note's result, from its own topic's targets (spec §5.6, D31)."""
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


__all__ = [
    "NoteOutcome",
    "note_outcome",
    "note_steering_outcome",
]
