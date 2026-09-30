"""Reader notes in the agents' requests (live-briefs spec §4.6, D9-D10).

One block, one line per note — ``- {restatement} ({kinds})`` — under a lead
sentence that says how *this* step uses the notes. Only interpreted notes that
no later note replaces are rendered, and every renderer returns ``""`` when
there is none, so a run without notes builds byte-identical requests.

The Evidence Verifier has no entry here, deliberately: verification is never
affected by a note (D10), and no verifier request carries this block.

The run's board is reached at call time: ``deep_research.runtime`` imports every
agent while its package initialises, so a module-level import of
``runtime.notes`` from an agent (or from ``graph.nodes``) would close an import
cycle whenever the graph is imported first.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol

from deep_research.utils.types import (
    ReaderNote,
    active_reader_notes,
    with_board_notes,
)

PLANNING_NOTES = (
    "The reader added these notes while the run was going; each line is the "
    "note as the run understood it, and a later note replaces an earlier one it "
    "contradicts. Plan within them: an emphasis note gives its subject more "
    "weight, an exclude note leaves its subject out, a scope note narrows the "
    "plan to its scope, an about_reader note says who the report is for, and a "
    "new_angle note can become a sub-topic of its own."
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
    """The notes a running research loop applies: ``new_angle`` notes wait for the review's note pass."""
    return [note for note in notes if "new_angle" not in note.kinds]


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


__all__ = [
    "EXTRACTION_NOTES",
    "NoteLine",
    "PLANNING_NOTES",
    "RESEARCH_NOTES",
    "REVIEW_NOTES",
    "SOURCE_NOTES",
    "WRITING_NOTES",
    "board_notes",
    "live_reader_notes",
    "notes_settled",
    "render_reader_notes",
    "research_reader_notes",
]
