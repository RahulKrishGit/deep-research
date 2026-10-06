"""The reader's notes for one run: the board the API
writes and the graph reads.

``SessionStore`` makes one ``NoteBoard`` per session and binds it for the run
through a ContextVar, exactly as the live sink is bound (``graph/live.py``): the
LangGraph node tasks and every task an agent starts copy the context, so a node
or a loop in flight reads the board without it being threaded through a call.

The board is append-only. The notes route *receives* a note (which numbers it
``n1``..``n10`` and counts it against ``MAX_NOTES_PER_RUN``), then *adds* the
interpreted note once its interpretation finishes. Only added notes are
readable: an agent never sees a note before it is interpreted. ``settled``
waits until every received note has been added or dropped, which is how the
review node takes in a note that was still being interpreted when the review
ended.

Nothing here is imported by an agent at module level: ``deep_research.runtime``
imports every agent while its package initialises, so agents and graph nodes
reach the board through ``agents.reader_notes`` at call time.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from deep_research.utils.types import MAX_NOTES_PER_RUN, ReaderNote


class NoteLimitReached(Exception):
    """The run already holds ``MAX_NOTES_PER_RUN`` accepted notes."""


@dataclass(frozen=True, slots=True)
class ReceivedNote:
    """A note the API accepted, before (or without) its interpretation."""

    note_id: str
    text: str
    received_at: str
    received_during: str


class NoteBoard:
    """One run's reader notes: received in order, readable once interpreted."""

    def __init__(self) -> None:
        self._received: list[ReceivedNote] = []
        self._notes: dict[str, ReaderNote] = {}
        self._dropped: set[str] = set()
        self._settled = asyncio.Event()
        self._settled.set()
        self._version = 0
        self._changed = asyncio.Event()

    @property
    def version(self) -> int:
        """How many times a note was added or dropped.

        A reader that reads it before it looks at the board, and then waits with
        ``wait_for_change``, misses no add or drop in between.
        """
        return self._version

    @property
    def accepted(self) -> int:
        """Notes received so far, interpreted or not; a refused note never counts."""
        return len(self._received)

    @property
    def remaining(self) -> int:
        """How many more notes this run accepts."""
        return max(0, MAX_NOTES_PER_RUN - len(self._received))

    @property
    def pending(self) -> tuple[str, ...]:
        """Received notes whose interpretation has neither finished nor been dropped."""
        return tuple(
            note.note_id
            for note in self._received
            if note.note_id not in self._notes and note.note_id not in self._dropped
        )

    def received(self) -> list[ReceivedNote]:
        """Every accepted note, in receipt order."""
        return list(self._received)

    def receive(
        self, text: str, *, received_at: str, received_during: str
    ) -> ReceivedNote:
        """Accept one note and number it; raise ``NoteLimitReached`` past the tenth."""
        if len(self._received) >= MAX_NOTES_PER_RUN:
            raise NoteLimitReached
        note = ReceivedNote(
            note_id=f"n{len(self._received) + 1}",
            text=text,
            received_at=received_at,
            received_during=received_during,
        )
        self._received.append(note)
        self._settled.clear()
        return note

    def add(self, note: ReaderNote) -> None:
        """Make one received note readable, interpreted."""
        if note.note_id not in {received.note_id for received in self._received}:
            raise ValueError(f"note {note.note_id!r} was never received")
        if note.note_id in self._notes or note.note_id in self._dropped:
            raise ValueError(f"note {note.note_id!r} was already settled")
        self._notes[note.note_id] = note.model_copy(deep=True)
        self._bump()
        self._settle()

    def drop(self, note_id: str) -> None:
        """Give up on one received note's interpretation (the session is closing)."""
        if note_id not in self._notes:
            self._dropped.add(note_id)
        self._bump()
        self._settle()

    async def wait_for_change(self, seen: int) -> int:
        """Return the board's ``version`` once it differs from ``seen``."""
        while self._version == seen:
            await self._changed.wait()
        return self._version

    def interpreted(self, note_id: str) -> ReaderNote | None:
        """The interpreted note, or ``None`` while it is pending or when it was dropped."""
        note = self._notes.get(note_id)
        return None if note is None else note.model_copy(deep=True)

    def snapshot(self) -> list[ReaderNote]:
        """Every interpreted note, in receipt order; deep copies."""
        return [
            self._notes[note.note_id].model_copy(deep=True)
            for note in self._received
            if note.note_id in self._notes
        ]

    async def settled(self) -> None:
        """Return once no received note is still being interpreted."""
        await self._settled.wait()

    def _settle(self) -> None:
        if not self.pending:
            self._settled.set()

    def _bump(self) -> None:
        """Count one add or drop and wake every waiter; later waiters wait on a fresh event."""
        self._version += 1
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()


_NOTE_BOARD: ContextVar[NoteBoard | None] = ContextVar(
    "deep_research_note_board", default=None
)


def current_note_board() -> NoteBoard | None:
    """The board bound for this run, or ``None`` (the CLI, or a test with none)."""
    return _NOTE_BOARD.get()


@contextmanager
def bind_note_board(board: NoteBoard) -> Iterator[None]:
    """Bind ``board`` for the body of the ``with`` block, then restore the previous one."""
    token = _NOTE_BOARD.set(board)
    try:
        yield
    finally:
        _NOTE_BOARD.reset(token)


__all__ = [
    "NoteBoard",
    "NoteLimitReached",
    "ReceivedNote",
    "bind_note_board",
    "current_note_board",
]
