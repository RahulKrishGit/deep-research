"""Safe, local persistence for compact Streamlit session-history metadata."""

from __future__ import annotations

import json
import os
import re
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
        self.metadata_directory.mkdir(parents=True, exist_ok=True)
        target = self.metadata_directory / f"{entry.session_id}.json"
        temporary = target.with_suffix(f"{target.suffix}.tmp")
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
            temporary.unlink(missing_ok=True)
            raise

    def get(self, session_id: str) -> SessionHistoryEntry | None:
        """Load one valid entry, returning ``None`` for unknown or bad IDs."""
        if not _is_safe_session_id(session_id):
            return None
        path = self.metadata_directory / f"{session_id}.json"
        return self._read_entry(path, expected_session_id=session_id)

    def list_entries(self, *, limit: int = 50) -> list[SessionHistoryEntry]:
        """Load valid records newest-first while isolating bad files."""
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if limit == 0 or not self.metadata_directory.is_dir():
            return []

        entries: list[SessionHistoryEntry] = []
        for path in self.metadata_directory.glob("*.json"):
            session_id = path.stem
            if not _is_safe_session_id(session_id):
                continue
            entry = self._read_entry(path, expected_session_id=session_id)
            if entry is not None:
                entries.append(entry)

        entries.sort(
            key=lambda entry: (entry.started_at, entry.session_id), reverse=True
        )
        return entries[:limit]

    def read_report(self, entry: SessionHistoryEntry) -> str | None:
        """Read an entry's report only when it resolves beneath the output root."""
        if not entry.report_path:
            return None

        try:
            report_path = (self._output_directory / Path(entry.report_path)).resolve()
        except (OSError, RuntimeError, TypeError, ValueError):
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
        try:
            entry = SessionHistoryEntry.model_validate(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (OSError, TypeError, ValueError, ValidationError):
            return None
        if entry.session_id != expected_session_id:
            return None
        return entry


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


__all__ = ["SessionHistoryStore"]
