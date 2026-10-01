"""The run's note board (live-briefs spec §4.6, D11a): receive, interpret, settle, bind."""

from __future__ import annotations

import asyncio

import pytest

from deep_research.agents.reader_notes import board_notes, notes_settled
from deep_research.runtime.notes import (
    NoteBoard,
    NoteLimitReached,
    bind_note_board,
    current_note_board,
)
from deep_research.utils.types import MAX_NOTES_PER_RUN
from tests.graph_fakes import fake_reader_note

AT = "2026-09-29T10:00:00.000+00:00"


def _receive(board: NoteBoard, text: str = "Focus on grid storage."):
    return board.receive(text, received_at=AT, received_during="researcher")


def test_notes_are_numbered_in_receipt_order_and_readable_only_once_interpreted() -> None:
    board = NoteBoard()
    first, second = _receive(board, "one"), _receive(board, "two")

    assert (first.note_id, second.note_id) == ("n1", "n2")
    assert (first.text, first.received_at, first.received_during) == ("one", AT, "researcher")
    assert board.accepted == 2 and board.remaining == MAX_NOTES_PER_RUN - 2
    assert board.pending == ("n1", "n2")
    assert board.snapshot() == [] and board.interpreted("n1") is None

    board.add(fake_reader_note("n2"))
    board.add(fake_reader_note("n1"))

    assert [note.note_id for note in board.snapshot()] == ["n1", "n2"]
    assert board.pending == ()
    assert [received.note_id for received in board.received()] == ["n1", "n2"]


def test_the_eleventh_note_is_refused_and_never_counted() -> None:
    board = NoteBoard()
    for _ in range(MAX_NOTES_PER_RUN):
        _receive(board)

    with pytest.raises(NoteLimitReached):
        _receive(board)
    assert board.accepted == MAX_NOTES_PER_RUN == 10
    assert board.remaining == 0
    assert board.received()[-1].note_id == "n10"


def test_a_note_is_added_once_and_only_after_it_was_received() -> None:
    board = NoteBoard()
    _receive(board)

    with pytest.raises(ValueError, match="never received"):
        board.add(fake_reader_note("n2"))
    board.add(fake_reader_note("n1"))
    with pytest.raises(ValueError, match="already settled"):
        board.add(fake_reader_note("n1"))


def test_the_snapshot_is_a_copy_the_caller_cannot_change() -> None:
    board = NoteBoard()
    _receive(board)
    board.add(fake_reader_note("n1"))

    board.snapshot()[0].kinds.append("exclude")

    assert board.snapshot()[0].kinds == ["emphasis"]
    assert board.interpreted("n1") == fake_reader_note("n1")


@pytest.mark.asyncio
async def test_settled_waits_for_every_received_note_to_be_added_or_dropped() -> None:
    board = NoteBoard()
    await asyncio.wait_for(board.settled(), timeout=1)  # nothing received yet
    _receive(board)
    _receive(board)

    waiter = asyncio.create_task(board.settled())
    await asyncio.sleep(0.01)
    assert not waiter.done()
    board.add(fake_reader_note("n1"))
    await asyncio.sleep(0.01)
    assert not waiter.done()
    board.drop("n2")
    await asyncio.wait_for(waiter, timeout=1)

    assert board.pending == ()
    assert [note.note_id for note in board.snapshot()] == ["n1"]
    assert board.interpreted("n2") is None


@pytest.mark.asyncio
async def test_the_board_is_bound_for_a_block_and_reaches_tasks_started_inside_it() -> None:
    board = NoteBoard()
    _receive(board)
    board.add(fake_reader_note("n1"))

    async def read() -> list[str]:
        await notes_settled()
        return [note.note_id for note in board_notes()]

    assert current_note_board() is None
    assert board_notes() == []
    await notes_settled()  # no board bound: returns at once
    with bind_note_board(board):
        assert current_note_board() is board
        task = asyncio.create_task(read())
    assert current_note_board() is None
    assert await task == ["n1"]


@pytest.mark.asyncio
async def test_the_board_counts_every_add_and_drop_and_wakes_a_waiter() -> None:
    """notes-progress-report spec §5.3: the researcher reads ``version`` before it scans the
    board and then waits for the next add or drop, so no change between the two is lost.
    Receiving a note is not a change: nothing can act on a note before it is read."""
    board = NoteBoard()
    _receive(board)
    _receive(board)
    assert board.version == 0
    assert await asyncio.wait_for(board.wait_for_change(-1), timeout=1) == 0

    waiter = asyncio.create_task(board.wait_for_change(0))
    _receive(board)
    await asyncio.sleep(0.01)
    assert not waiter.done()
    board.add(fake_reader_note("n1"))
    assert await asyncio.wait_for(waiter, timeout=1) == 1
    board.drop("n2")
    board.drop("n3")
    assert board.version == 3
    assert await asyncio.wait_for(board.wait_for_change(1), timeout=1) == 3
