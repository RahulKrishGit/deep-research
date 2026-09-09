"""Safe, local persistence for compact Streamlit session-history metadata."""

from __future__ import annotations

import json
import os
import re
from datetime import timezone
from pathlib import Path

from pydantic import ValidationError

from deep_research.ui.models import SessionHistoryEntry

_SESSION_ID_PATTERN = re.compile(r"[0-9a-f]{32}\Z")


class SessionHistoryStore:
    """Persist session summaries beneath one configured output directory."""

    def __init__(self, *, output_directory: Path) -> None:
        self._output_directory = Path(output_directory).resolve()

    @property
    def metadata_directory(self) -> Path:
        """Return the lazy directory containing one metadata file per session."""
        return self._output_directory / "sessions"

    def upsert(self, entry: SessionHistoryEntry) -> None:
        """Atomically replace the metadata record for ``entry.session_id``."""
        _require_safe_session_id(entry.session_id)
        entry = _compact_entry(entry)
        self._prepare_metadata_directory()
        target = self.metadata_directory / f"{entry.session_id}.json"
        temporary = target.with_suffix(f"{target.suffix}.tmp")
        if _is_link_or_junction(target) or _is_link_or_junction(temporary):
            raise ValueError(
                "metadata target or temporary path is a symlink or junction"
            )
        payload = json.dumps(
            entry.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"

        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def get(self, session_id: str) -> SessionHistoryEntry | None:
        """Load one valid entry, returning ``None`` for unknown or bad IDs."""
        if not _is_safe_session_id(session_id):
            return None
        if not self._metadata_directory_is_safe():
            return None
        path = self.metadata_directory / f"{session_id}.json"
        return self._read_entry(path, expected_session_id=session_id)

    def list_entries(self, *, limit: int = 50) -> list[SessionHistoryEntry]:
        """Load valid records newest-first while isolating bad files."""
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if limit == 0 or not self._metadata_directory_is_safe():
            return []

        entries: list[SessionHistoryEntry] = []
        for path in self.metadata_directory.glob("*.json"):
            session_id = path.stem
            if not _is_safe_session_id(session_id):
                continue
            entry = self._read_entry(path, expected_session_id=session_id)
            if entry is not None:
                entries.append(entry)

        entries.sort(key=_entry_sort_key, reverse=True)
        return entries[:limit]

    def read_report(self, entry: SessionHistoryEntry) -> str | None:
        """Read an entry's report only when it resolves beneath the output root."""
        if not entry.report_path:
            return None

        try:
            candidate = Path(
                os.path.abspath(self._output_directory / Path(entry.report_path))
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        if not _is_within(candidate, self._output_directory):
            return None
        if _has_link_component(candidate, self._output_directory):
            return None
        try:
            report_path = candidate.resolve()
        except (OSError, RuntimeError):
            return None

        if not _is_within(report_path, self._output_directory):
            return None

        try:
            return report_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None

    def _read_entry(
        self,
        path: Path,
        *,
        expected_session_id: str,
    ) -> SessionHistoryEntry | None:
        if _is_link_or_junction(path):
            return None
        try:
            entry = SessionHistoryEntry.model_validate(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (OSError, TypeError, ValueError, ValidationError):
            return None
        if entry.session_id != expected_session_id:
            return None
        return entry

    def _prepare_metadata_directory(self) -> None:
        if _is_link_or_junction(self.metadata_directory):
            raise ValueError("metadata directory is a symlink or junction")
        self.metadata_directory.mkdir(parents=True, exist_ok=True)
        if not self._metadata_directory_is_safe():
            raise ValueError("metadata directory is outside the output root")

    def _metadata_directory_is_safe(self) -> bool:
        directory = self.metadata_directory
        if _is_link_or_junction(directory):
            return False
        try:
            return (
                directory.is_dir()
                and directory.resolve() == directory
                and self._output_directory in directory.parents
            )
        except (OSError, RuntimeError):
            return False


def _is_safe_session_id(session_id: object) -> bool:
    return (
        isinstance(session_id, str)
        and _SESSION_ID_PATTERN.fullmatch(session_id) is not None
    )


def _require_safe_session_id(session_id: object) -> None:
    if not _is_safe_session_id(session_id):
        raise ValueError(
            "session id must be a generated 32-character hexadecimal value"
        )


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _compact_entry(entry: SessionHistoryEntry) -> SessionHistoryEntry:
    return SessionHistoryEntry.model_validate(
        entry.model_dump(
            mode="json",
            exclude={
                "source_summary": {"details"},
                "fact_check_summary": {"details"},
            },
        )
    )


def _entry_sort_key(entry: SessionHistoryEntry) -> tuple[float, str]:
    started_at = entry.started_at
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    else:
        started_at = started_at.astimezone(timezone.utc)
    return started_at.timestamp(), entry.session_id


def _is_link_or_junction(path: Path) -> bool:
    try:
        is_junction = getattr(path, "is_junction", None)
        return path.is_symlink() or (
            is_junction is not None and is_junction()
        )
    except OSError:
        return True


def _has_link_component(path: Path, root: Path) -> bool:
    if not _is_within(path, root):
        return False
    current = path
    while True:
        if _is_link_or_junction(current):
            return True
        if current == root:
            return False
        current = current.parent


__all__ = ["SessionHistoryStore"]
