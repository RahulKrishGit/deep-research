"""Reader notes on the real graph, offline (live-briefs spec §4.6).

Every request of a replay run is recorded by the scripted completer as
``(agent:schema, text)``. Without notes the whole run's requests are pinned as
one digest per case — the value at the end of Phase 2 — so no Phase 3 change
can alter a single byte of a run that has no notes (spec §4.8 "Replay mode").
With notes on a bound board, each consumer's requests are read back.
"""

from __future__ import annotations

import contextlib
import hashlib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from deep_research.e2e_evaluation.replay import (
    build_replay_runtime,
    production_config_path,
    replay_settings,
)
from deep_research.e2e_evaluation.replay_matrix import scenario_by_id
from deep_research.main import run_research
from deep_research.runtime.notes import NoteBoard, bind_note_board
from deep_research.utils.config import ConfigSettings
from deep_research.utils.types import ReaderNote
from tests.graph_fakes import fake_reader_note
from tests.test_api.replay_support import EXTRA_PASS_CASE, REDRAFT_CASE, guarded

# sha256[:16] over the sorted "agent:schema sha256[:16]" lines of every request a
# run sent, and how many requests that was: observed at the end of Phase 2 and
# unchanged by Phase 3, because every notes section is added only when there are
# notes.
PINNED_RUN_DIGESTS = {
    # Latency plan Task 8 (audit O9): this row reaches its forced last turn,
    # where the replay's script reads a page the prompt tells it not to; with
    # that turn never asked the page is read on the extra pass. Was
    # ("03e113e5584707da", 46);
    # tests/test_e2e_evaluation/test_request_digests.py shows that a model
    # obeying the instruction loses only the forced turn's own request.
    EXTRA_PASS_CASE: ("8e5192b96744de3d", 46),
    REDRAFT_CASE: ("875313d15f3325f2", 29),
}
AT = "2026-09-29T10:00:00.000+00:00"


async def replay_packets(
    tmp_path: Path,
    case_id: str,
    *,
    board: NoteBoard | None = None,
    after_research_turn: Callable[[int], None] | None = None,
) -> tuple[str, list[tuple[str, str]], Any]:
    """Run one replay case through ``run_research``; return its status, every
    request it sent, and its final state.

    ``board`` is bound for the whole run, as the API binds a session's board.
    ``after_research_turn`` is called with the count of researcher decision turns
    answered so far, right after each one — the moment a note can land mid-loop.
    """
    scenario = scenario_by_id(case_id)
    held: dict[str, Any] = {}

    async def builder(current: ConfigSettings, *, session_id: str) -> Any:
        effective = replay_settings(scenario, root=tmp_path, base=current)
        replay = await build_replay_runtime(scenario, root=tmp_path, session_id=session_id, settings=effective)
        completer = replay.completer
        held["completer"] = completer
        if after_research_turn is not None:
            original = completer.complete_react
            turns = [0]

            async def complete_react(messages: Any, tools: Any, **kwargs: Any) -> Any:
                turn = await original(messages, tools, **kwargs)
                if kwargs.get("agent_name") == "researcher":
                    turns[0] += 1
                    after_research_turn(turns[0])
                return turn

            completer.complete_react = complete_react
        return replay.runtime

    bound = bind_note_board(board) if board is not None else contextlib.nullcontext()
    with bound:
        outcome = await run_research(
            question=scenario.question, session_id="s1", config_path=str(production_config_path()),
            max_extra_passes=scenario.max_extra_passes, runtime_builder=builder,
        )
    return outcome.status, list(held["completer"].packet_sequence), outcome.state


def run_digest(sequence: Sequence[tuple[str, str]]) -> tuple[str, int]:
    lines = sorted(f"{key} {hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}" for key, text in sequence)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16], len(lines)


def packets_for(sequence: Sequence[tuple[str, str]], key: str) -> list[str]:
    return [text for packet_key, text in sequence if packet_key == key]


EMPHASIS = fake_reader_note("n1", received_during="planner", restatement="more weight on lithium-ion safety standards")
ANGLE = fake_reader_note(
    "n2", kinds=["new_angle"], received_during="planner", restatement="how battery cells are recycled",
    new_questions=["How are battery cells recycled at end of life?"],
)
EMPHASIS_TEXT = "more weight on lithium-ion safety standards (emphasis)"
EMPHASIS_LINE = f"- {EMPHASIS_TEXT}"
ANGLE_LINE = "- how battery cells are recycled (new_angle)"


def noted_board(*notes: ReaderNote) -> NoteBoard:
    """A board holding ``notes``, each already interpreted, as a run that took them before planning."""
    board = NoteBoard()
    for note in notes:
        board.receive(note.text, received_at=AT, received_during=note.received_during)
        board.add(note)
    return board


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", sorted(PINNED_RUN_DIGESTS))
async def test_without_notes_every_request_of_a_replay_run_is_byte_identical(tmp_path: Path, case_id: str) -> None:
    with guarded():
        status, sequence, _ = await replay_packets(tmp_path, case_id)

    assert status == "completed"
    assert run_digest(sequence) == PINNED_RUN_DIGESTS[case_id]
    assert not any("Reader notes" in text or "reader added these notes" in text for _, text in sequence)


@pytest.mark.asyncio
async def test_the_planner_and_every_research_turn_carry_the_notes(tmp_path: Path) -> None:
    """spec §4.6: planning reads every note; a running research loop reads the board
    before each decision, so a note that lands mid-loop steers the loop's next turn,
    and a ``new_angle`` note never reaches a running loop."""
    board = noted_board(EMPHASIS, ANGLE)
    late = fake_reader_note("n3", kinds=["exclude"], restatement="leave out pumped hydro")

    def note_after_the_first_turn(turns: int) -> None:
        if turns == 1:
            board.receive(late.text, received_at=AT, received_during="researcher")
            board.add(late)

    with guarded():
        status, sequence, _ = await replay_packets(
            tmp_path, EXTRA_PASS_CASE, board=board, after_research_turn=note_after_the_first_turn
        )

    assert status == "completed"
    for key in ("planner:react", "planner:ResearchPlanDraft", "planner:PlanReviewDraft"):
        texts = packets_for(sequence, key)
        assert texts and all(EMPHASIS_LINE in text and ANGLE_LINE in text for text in texts), key
    turns = packets_for(sequence, "researcher:react")
    assert "## Reader notes\n" in turns[0] and EMPHASIS_LINE in turns[0]
    assert "pumped hydro" not in turns[0]
    assert "- leave out pumped hydro (exclude)" in turns[-1]
    assert not any(ANGLE_LINE in text for text in turns)
    extractions = packets_for(sequence, "researcher:SubTopicFindingsDraft")
    assert extractions and all("# Reader notes\n" in text and EMPHASIS_LINE in text for text in extractions)
    assert not any(ANGLE_LINE in text for text in extractions)


@pytest.mark.asyncio
async def test_the_source_evaluator_and_the_writer_carry_the_notes(tmp_path: Path) -> None:
    """spec §4.6: the scoring request's ``# Context`` slot carries the notes, for
    relevance only; the writer's section and bottom-line requests carry
    ``# Reader notes`` right after the answer form."""
    with guarded():
        status, sequence, _ = await replay_packets(tmp_path, EXTRA_PASS_CASE, board=noted_board(EMPHASIS, ANGLE))

    assert status == "completed"
    scoring = packets_for(sequence, "source_evaluator:SourceScoresDraft")
    assert scoring and all(
        "# Context\nThe reader added these notes while the run was going. They bear on how relevant"
        in text and EMPHASIS_LINE in text and ANGLE_LINE in text
        for text in scoring
    )
    for key, after in (
        ("report_writer:SectionDraft", "# This part of the question\n"),
        ("report_writer:BottomLineDraft", "# Checked statements\n"),
    ):
        texts = packets_for(sequence, key)
        assert texts, key
        for text in texts:
            assert text.index("# Answer form\n") < text.index("# Reader notes\n") < text.index(after), key
            assert EMPHASIS_LINE in text and ANGLE_LINE in text, key


@pytest.mark.asyncio
async def test_ac16_every_consumer_reads_the_notes_and_no_verifier_request_does(tmp_path: Path) -> None:
    """AC16 / D10: the notes block reaches the planner, every research turn, the
    source evaluator, the writer and the review — and never an evidence-verifier
    request, whichever agent sends it."""
    with guarded():
        status, sequence, state = await replay_packets(tmp_path, EXTRA_PASS_CASE, board=noted_board(EMPHASIS, ANGLE))

    assert status == "completed"
    consumers = {
        "planner:react", "planner:ResearchPlanDraft", "planner:PlanReviewDraft",
        "researcher:react", "researcher:SubTopicFindingsDraft",
        "source_evaluator:SourceScoresDraft",
        "report_writer:SectionDraft", "report_writer:BottomLineDraft",
        "report_reviewer:ReportReviewNotesDraft",
    }
    keys = {key for key, _ in sequence}
    assert consumers <= keys
    assert "report_reviewer:ReportReviewDraft" not in keys
    for key in consumers:
        assert all(EMPHASIS_TEXT in text for text in packets_for(sequence, key)), key
    review = packets_for(sequence, "report_reviewer:ReportReviewNotesDraft")[0]
    assert "- n1: more weight on lithium-ion safety standards (emphasis)" in review
    assert "- n2: how battery cells are recycled (new_angle)" in review
    checks = [text for key, text in sequence if key.split(":")[1] in {"ContextCheckDraft", "StatementCheckDraft"}]
    assert checks and {key for key, _ in sequence if key.startswith("evidence_verifier:")}
    for text in checks:
        assert "lithium-ion safety standards" not in text and "battery cells are recycled" not in text
        assert "Reader notes" not in text and "reader added these notes" not in text
    assert [(d.note_id, d.status) for d in state.report_review.note_dispositions] == [
        ("n1", "honoured"), ("n2", "honoured"),
    ]
    assert [(note.note_id, note.reviewed) for note in state.reader_notes] == [("n1", True), ("n2", True)]
