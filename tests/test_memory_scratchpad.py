"""Tests for bounded per-agent, per-session scratchpad memory."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from deep_research.memory.entries import ScratchpadEntry
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.utils.config import ShortTermMemoryConfig


def _pad(**overrides: object) -> ScratchpadMemory:
    payload: dict[str, object] = {
        "session_id": "session-1",
        "agent_name": "researcher",
        "max_entries": 4,
    }
    payload.update(overrides)
    return ScratchpadMemory(**payload)  # type: ignore[arg-type]


def test_scratchpad_records_agent_and_kind_on_every_entry() -> None:
    pad = _pad()

    entry = pad.add("Searching for QEC benchmarks.", kind="thought")

    assert entry.agent_name == "researcher"
    assert entry.kind == "thought"
    assert entry.content == "Searching for QEC benchmarks."
    assert pad.entries == (entry,)
    assert len(pad) == 1


def test_scratchpad_defaults_to_observation_entries() -> None:
    pad = _pad()

    assert pad.add("Tool returned 5 results.").kind == "observation"


def test_scratchpad_without_summarizer_slides_the_window() -> None:
    pad = _pad(max_entries=3)
    for index in range(5):
        pad.add(f"note-{index}")

    assert len(pad) == 3
    assert [entry.content for entry in pad.entries] == ["note-2", "note-3", "note-4"]


def test_scratchpad_never_exceeds_its_bound_even_with_a_summarizer() -> None:
    pad = _pad(max_entries=1, summarizer=lambda entries: "summary")
    for index in range(6):
        pad.add(f"note-{index}")

    assert len(pad) == 1
    assert pad.entries[0].content == "note-5"


def test_scratchpad_summarizes_evicted_entries_into_a_summary_entry() -> None:
    seen: list[tuple[str, ...]] = []

    def summarize(entries: Sequence[ScratchpadEntry]) -> str:
        seen.append(tuple(entry.content for entry in entries))
        return "condensed: " + ", ".join(entry.content for entry in entries)

    pad = _pad(max_entries=4, summarizer=summarize)
    for index in range(5):
        pad.add(f"note-{index}")

    assert seen == [("note-0", "note-1")]
    assert [entry.kind for entry in pad.entries] == [
        "summary",
        "observation",
        "observation",
        "observation",
    ]
    assert pad.entries[0].content == "condensed: note-0, note-1"
    assert pad.entries[0].metadata == {"summarized_entries": 2}
    assert [entry.content for entry in pad.entries[1:]] == [
        "note-2",
        "note-3",
        "note-4",
    ]


def test_scratchpad_ignores_a_blank_summary() -> None:
    pad = _pad(max_entries=4, summarizer=lambda entries: "   ")
    for index in range(5):
        pad.add(f"note-{index}")

    assert [entry.content for entry in pad.entries] == [
        "note-2",
        "note-3",
        "note-4",
    ]


def test_scratchpad_records_a_recoverable_error_when_summarization_fails() -> None:
    def explode(entries: Sequence[ScratchpadEntry]) -> str:
        raise RuntimeError("summarizer offline")

    pad = _pad(max_entries=4, summarizer=explode)
    for index in range(5):
        pad.add(f"note-{index}")

    assert len(pad) == 3
    assert [entry.content for entry in pad.entries] == [
        "note-2",
        "note-3",
        "note-4",
    ]
    errors = pad.drain_errors()
    assert len(errors) == 1
    assert errors[0].error_type == "scratchpad_summarization_failed"
    assert errors[0].source == "scratchpad_memory"
    assert errors[0].recoverable is True
    assert errors[0].details["exception_type"] == "RuntimeError"
    assert pad.errors == ()


def test_scratchpad_recent_returns_the_newest_entries_in_order() -> None:
    pad = _pad(max_entries=10)
    for index in range(5):
        pad.add(f"note-{index}")

    assert [entry.content for entry in pad.recent(2)] == ["note-3", "note-4"]
    assert [entry.content for entry in pad.recent(99)] == [
        f"note-{index}" for index in range(5)
    ]
    assert pad.recent(0) == ()
    assert pad.recent() == pad.entries


def test_scratchpad_recent_rejects_negative_counts() -> None:
    with pytest.raises(ValueError, match="count must not be negative"):
        _pad().recent(-1)


def test_scratchpad_clear_empties_the_window() -> None:
    pad = _pad()
    pad.add("note")

    pad.clear()

    assert pad.entries == ()
    assert len(pad) == 0


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"session_id": "  "}, "session_id must not be blank"),
        ({"agent_name": ""}, "agent_name must not be blank"),
        ({"max_entries": 0}, "max_entries must be at least 1"),
    ],
)
def test_scratchpad_rejects_invalid_construction(
    kwargs: dict[str, object], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        _pad(**kwargs)


def test_scratchpad_from_config_uses_the_short_term_turn_limit() -> None:
    pad = ScratchpadMemory.from_config(
        ShortTermMemoryConfig(max_turns=2),
        session_id="session-1",
        agent_name="planner",
    )
    for index in range(4):
        pad.add(f"note-{index}")

    assert pad.max_entries == 2
    assert len(pad) == 2
