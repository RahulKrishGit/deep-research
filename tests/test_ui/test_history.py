"""Offline persistence tests for the local Streamlit session history store."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from deep_research.ui.history import SessionHistoryStore
from deep_research.ui.models import (
    SessionHistoryEntry,
    UiFactCheckSummary,
    UiSourceSummary,
    UiTokenUsage,
)


def _entry(
    session_id: str = "a" * 32,
    *,
    started_at: datetime | None = None,
    report_path: str | None = None,
    status: str = "completed",
    question: str = "What happened?",
) -> SessionHistoryEntry:
    return SessionHistoryEntry(
        session_id=session_id,
        question=question,
        status=status,
        started_at=started_at or datetime(2026, 9, 9, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 9, 0, 1, tzinfo=timezone.utc),
        iteration=2,
        max_iterations=3,
        report_path=report_path,
        trace_url="https://smith.langchain.com/trace/1",
        token_usage=UiTokenUsage(input_tokens=10, output_tokens=20),
        source_summary=UiSourceSummary(
            total=1,
            high=1,
            moderate=0,
            low=0,
            unrated=0,
        ),
        fact_check_summary=UiFactCheckSummary(
            verified=1,
            unverified=0,
            contradicted=0,
            insufficient_evidence=0,
        ),
        limitations=["One source was unavailable."],
    )


def test_store_creates_metadata_directory_lazily_and_overwrites_by_session_id(
    tmp_path: Path,
) -> None:
    store = SessionHistoryStore(output_directory=tmp_path)

    assert store.metadata_directory == tmp_path / "sessions"
    assert not store.metadata_directory.exists()

    first = _entry(question="First question")
    store.upsert(first)
    assert store.metadata_directory.is_dir()

    replacement = first.model_copy(update={"question": "Replacement question"})
    store.upsert(replacement)

    assert list(store.metadata_directory.glob("*.json")) == [
        store.metadata_directory / f"{first.session_id}.json"
    ]
    assert store.get(first.session_id) == replacement

    persisted = json.loads(
        (store.metadata_directory / f"{first.session_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert "report" not in persisted
    assert not (store.metadata_directory / f"{first.session_id}.json.tmp").exists()


def test_list_entries_returns_newest_first_and_honors_limit(tmp_path: Path) -> None:
    store = SessionHistoryStore(output_directory=tmp_path)
    base = datetime(2026, 9, 9, tzinfo=timezone.utc)
    entries = [
        _entry("a" * 32, started_at=base),
        _entry("b" * 32, started_at=base + timedelta(minutes=2)),
        _entry("c" * 32, started_at=base + timedelta(minutes=1)),
    ]
    for entry in entries:
        store.upsert(entry)

    assert [entry.session_id for entry in store.list_entries(limit=2)] == [
        "b" * 32,
        "c" * 32,
    ]
    assert store.list_entries(limit=0) == []


def test_list_entries_isolates_malformed_json_files(tmp_path: Path) -> None:
    store = SessionHistoryStore(output_directory=tmp_path)
    valid = _entry()
    store.upsert(valid)
    malformed = store.metadata_directory / f"{'b' * 32}.json"
    malformed.write_text("{not valid json", encoding="utf-8")

    assert store.list_entries() == [valid]
    assert store.get("b" * 32) is None


def test_get_returns_none_for_unknown_or_unsafe_ids(tmp_path: Path) -> None:
    store = SessionHistoryStore(output_directory=tmp_path)

    assert store.get("d" * 32) is None
    assert store.get("../outside") is None
    assert not (tmp_path.parent / "outside.json").exists()


def test_upsert_rejects_ids_that_are_not_generated_session_ids(
    tmp_path: Path,
) -> None:
    store = SessionHistoryStore(output_directory=tmp_path)

    with pytest.raises(ValueError, match="session id"):
        store.upsert(_entry("../outside"))

    assert not store.metadata_directory.exists()


def test_read_report_returns_none_for_missing_report_path(tmp_path: Path) -> None:
    store = SessionHistoryStore(output_directory=tmp_path)

    assert store.read_report(_entry(report_path=None)) is None
    assert (
        store.read_report(_entry(report_path="reports/missing.md")) is None
    )


def test_read_report_reads_a_report_under_the_output_root(tmp_path: Path) -> None:
    store = SessionHistoryStore(output_directory=tmp_path)
    report = tmp_path / "reports" / "session.md"
    report.parent.mkdir()
    report.write_text("# Research report\n", encoding="utf-8")

    assert store.read_report(_entry(report_path="reports/session.md")) == (
        "# Research report\n"
    )


def test_read_report_refuses_a_path_that_escapes_the_output_root(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "output"
    output_root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("private report", encoding="utf-8")
    store = SessionHistoryStore(output_directory=output_root)

    assert store.read_report(_entry(report_path="../outside.md")) is None


def test_history_store_preserves_running_status_for_controller_conversion(
    tmp_path: Path,
) -> None:
    store = SessionHistoryStore(output_directory=tmp_path)
    running = _entry(status="running")

    store.upsert(running)

    restored = store.get(running.session_id)
    assert restored is not None
    assert restored.status == "running"
