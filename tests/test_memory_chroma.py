"""Integration tests for the ChromaDB adapter, against a temporary directory."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from deep_research.memory.entries import MemoryEntry
from deep_research.memory.errors import MemoryInitializationError
from deep_research.memory.long_term import LongTermMemory, build_chroma_collection
from deep_research.utils.config import LongTermMemoryConfig
from tests.memory_fakes import FakeEmbeddings

pytest.importorskip(
    "chromadb",
    reason="chromadb is a declared runtime dependency; install it with "
    'pip install -e ".[dev]"',
)


def _config(tmp_path: Path, name: str = "test_memory") -> LongTermMemoryConfig:
    return LongTermMemoryConfig(
        collection_name=name, persist_directory=str(tmp_path / "memory")
    )


def _finding(content: str, **overrides: object) -> MemoryEntry:
    payload: dict[str, object] = {
        "entry_type": "finding",
        "content": content,
        "session_id": "session-1",
        "agent_id": "researcher",
        "confidence": 0.9,
    }
    payload.update(overrides)
    return MemoryEntry.model_validate(payload)


@pytest.mark.asyncio
async def test_entries_round_trip_through_a_temporary_chroma_directory(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    memory = LongTermMemory.from_config(config, embeddings=FakeEmbeddings())

    assert await memory.save(_finding("Surface codes improved in 2026.")) is True

    results = await memory.query("Surface codes improved in 2026.", top_k=3)

    assert (tmp_path / "memory" / "chroma").is_dir()
    assert len(results) == 1
    assert results[0].entry.content == "Surface codes improved in 2026."
    assert results[0].entry.session_id == "session-1"
    assert results[0].entry.confidence == pytest.approx(0.9)
    assert memory.errors == ()


@pytest.mark.asyncio
async def test_metadata_filters_reach_the_real_backend(tmp_path: Path) -> None:
    memory = LongTermMemory.from_config(
        _config(tmp_path), embeddings=FakeEmbeddings()
    )
    await memory.save(
        _finding("shared text", attributes={"sub_topic": "hardware"})
    )
    await memory.save(
        _finding("shared text", attributes={"sub_topic": "software"})
    )
    await memory.save(
        _finding("shared text", entry_type="report_summary")
    )

    typed = await memory.query("shared text", top_k=5, entry_type="finding")
    scoped = await memory.query(
        "shared text", top_k=5, entry_type="finding", where={"sub_topic": "hardware"}
    )

    # Two "finding" entries exist (hardware and software sub_topics), so this
    # only discriminates a working `where=` filter if entry_type alone would
    # return both — proving the where clause, not just the entry_type filter,
    # narrowed the result set.
    assert [result.entry.entry_type for result in typed] == ["finding", "finding"]
    assert len(scoped) == 1
    assert scoped[0].entry.attributes["sub_topic"] == "hardware"


@pytest.mark.asyncio
async def test_source_reputation_updates_persist_across_instances(
    tmp_path: Path,
) -> None:
    """A second `LongTermMemory` handle opened on the same directory sees prior writes.

    Both handles live in this one process, so this proves the deterministic
    ``Settings`` construction lets a second `PersistentClient` share Chroma's
    per-path system and see the first client's data — not literal
    cross-process or cross-restart on-disk durability.
    """
    config = _config(tmp_path)
    first = LongTermMemory.from_config(config, embeddings=FakeEmbeddings())
    await first.update_source_reputation(
        url="https://example.org/a",
        title="Example",
        reputation_score=1.0,
        session_id="session-1",
        agent_id="source_evaluator",
    )

    second = LongTermMemory.from_config(config, embeddings=FakeEmbeddings())
    updated = await second.update_source_reputation(
        url="https://example.org/a",
        title="Example",
        reputation_score=0.0,
        session_id="session-2",
        agent_id="source_evaluator",
    )

    assert updated is not None
    assert updated.observations == 2
    assert updated.reputation_score == pytest.approx(0.5)


def test_unusable_persist_directory_fails_startup(tmp_path: Path) -> None:
    blocker = tmp_path / "memory"
    blocker.write_text("not a directory", encoding="utf-8")

    with pytest.raises(MemoryInitializationError):
        build_chroma_collection(_config(tmp_path))


def test_importing_the_memory_package_does_not_import_chromadb() -> None:
    """Regression guard for the lazy-import constraint.

    ``chromadb`` is a real installed dependency in this environment, so a
    plain ``import deep_research.memory`` cannot tell a lazy ``import
    chromadb`` (inside ``build_chroma_collection``) apart from an eager one
    hoisted to module scope — both would pass identically, and
    ``deep_research/memory/__init__.py`` is a bare stub that doesn't even
    load ``long_term.py``, so it wouldn't catch either case anyway. This test
    runs in a fresh subprocess, poisons ``sys.modules["chromadb"]`` before
    importing, and imports the concrete ``long_term`` module (not just the
    package) so an eager ``import chromadb`` statement there would raise
    ``ImportError`` immediately. If the lazy import inside
    ``build_chroma_collection`` is ever hoisted to module level, this
    subprocess exits non-zero and the test fails.
    """
    script = textwrap.dedent(
        """
        import sys

        sys.modules["chromadb"] = None  # any `import chromadb` now raises ImportError

        import deep_research.memory  # noqa: F401
        from deep_research.memory.long_term import build_chroma_collection  # noqa: F401

        assert sys.modules["chromadb"] is None, "chromadb must not be imported yet"
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
