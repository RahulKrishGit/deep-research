"""Offline persistence tests for the local Streamlit session history store."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from deep_research.ui.history import SessionHistoryStore
from deep_research.ui.models import (
    SessionHistoryEntry,
    UiClaimDetail,
    UiFactCheckSummary,
    UiSourceDetail,
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


def test_upsert_compacts_detail_payloads_at_the_storage_boundary(
    tmp_path: Path,
) -> None:
    store = SessionHistoryStore(output_directory=tmp_path)
    entry = _entry()
    entry.source_summary.details = [
        UiSourceDetail(
            title="Detailed source",
            url="https://example.org/source",
            tier="high",
            overall_score=0.9,
            rationale="Private source rationale.",
            corroboration_score=0.8,
        )
    ]
    entry.fact_check_summary.details = [
        UiClaimDetail(
            text="Detailed claim",
            verdict="verified",
            confidence=0.9,
            evidence=["Private evidence."],
        )
    ]

    store.upsert(entry)

    persisted = json.loads(
        (store.metadata_directory / f"{entry.session_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted["source_summary"]["details"] == []
    assert persisted["fact_check_summary"]["details"] == []
    assert store.get(entry.session_id).source_summary.details == []
    assert store.get(entry.session_id).fact_check_summary.details == []


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


def test_list_entries_sorts_mixed_naive_and_aware_timestamps(tmp_path: Path) -> None:
    store = SessionHistoryStore(output_directory=tmp_path)
    store.upsert(_entry("a" * 32, started_at=datetime(2026, 9, 9, 12)))
    store.upsert(
        _entry(
            "b" * 32,
            started_at=datetime(2026, 9, 9, 13, tzinfo=timezone.utc),
        )
    )

    assert [entry.session_id for entry in store.list_entries()] == [
        "b" * 32,
        "a" * 32,
    ]


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


def test_read_report_refuses_filesystem_invalid_embedded_nul_path(
    tmp_path: Path,
) -> None:
    store = SessionHistoryStore(output_directory=tmp_path)

    assert store.read_report(_entry(report_path="reports/invalid\x00.md")) is None


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


def _directory_symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError as symlink_error:
        if os.name == "nt":
            junction = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
                text=True,
                check=False,
            )
            if junction.returncode == 0:
                return
            pytest.skip(
                "directory symlinks unavailable: "
                f"{symlink_error}; {junction.stderr.strip()}"
            )
        pytest.skip(f"directory symlinks unavailable: {symlink_error}")


def test_metadata_directory_symlink_fails_closed_for_reads_and_writes(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "output"
    output_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    store = SessionHistoryStore(output_directory=output_root)
    _directory_symlink(store.metadata_directory, outside)

    with pytest.raises(ValueError, match="metadata directory"):
        store.upsert(_entry())

    assert store.get("a" * 32) is None
    assert store.list_entries() == []
    assert not (outside / f"{'a' * 32}.json").exists()


def test_metadata_file_symlink_is_not_followed(tmp_path: Path) -> None:
    store = SessionHistoryStore(output_directory=tmp_path)
    store.upsert(_entry())
    outside = tmp_path.parent / "outside-history.json"
    outside.write_text(
        json.dumps(_entry("b" * 32).model_dump(mode="json")),
        encoding="utf-8",
    )
    metadata_file = store.metadata_directory / f"{'a' * 32}.json"
    metadata_file.unlink()
    try:
        metadata_file.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"file symlinks unavailable: {error}")

    assert store.get("a" * 32) is None
    assert store.list_entries() == []


def test_report_symlink_is_refused_even_when_link_is_under_output_root(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside-report.md"
    outside.write_text("private report", encoding="utf-8")
    link = tmp_path / "linked-report.md"
    try:
        link.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"file symlinks unavailable: {error}")
    store = SessionHistoryStore(output_directory=tmp_path)

    assert store.read_report(_entry(report_path="linked-report.md")) is None


def test_upsert_removes_temporary_file_after_atomic_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SessionHistoryStore(output_directory=tmp_path)
    store.upsert(_entry())
    target = store.metadata_directory / f"{'a' * 32}.json"
    previous = target.read_text(encoding="utf-8")

    def fail_replace(source: os.PathLike[str], destination: os.PathLike[str]) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("deep_research.ui.history.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        store.upsert(_entry(question="new question"))

    assert target.read_text(encoding="utf-8") == previous
    assert not target.with_suffix(".json.tmp").exists()


def test_history_store_preserves_running_status_for_controller_conversion(
    tmp_path: Path,
) -> None:
    store = SessionHistoryStore(output_directory=tmp_path)
    running = _entry(status="running")

    store.upsert(running)

    restored = store.get(running.session_id)
    assert restored is not None
    assert restored.status == "running"
