"""Memory subsystem exceptions and recoverable-error collection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import JsonValue

from deep_research.utils.types import ResearchError


class MemoryStackError(Exception):
    """Base class for memory subsystem failures.

    Named ``MemoryStackError`` rather than ``MemoryError`` so it never shadows
    the built-in allocation-failure exception.
    """


class MemoryInitializationError(MemoryStackError):
    """Raised when a memory layer cannot start. Not recoverable."""


class MemoryErrorLog:
    """Collect recoverable memory failures as structured research errors."""

    def __init__(self, source: str) -> None:
        if not source.strip():
            raise ValueError("source must not be blank")
        self._source = source.strip()
        self._errors: list[ResearchError] = []

    @property
    def source(self) -> str:
        return self._source

    @property
    def errors(self) -> Sequence[ResearchError]:
        return tuple(self._errors)

    def record(
        self,
        *,
        error_type: str,
        message: str,
        error: BaseException | None = None,
        details: Mapping[str, JsonValue] | None = None,
    ) -> ResearchError:
        """Append one recoverable error and return it.

        Only the exception *type* is recorded. Exception text can carry API
        keys, URLs, and file paths, and these errors are copied into
        ``ResearchState.errors``.
        """
        payload: dict[str, JsonValue] = dict(details or {})
        if error is not None:
            payload["exception_type"] = type(error).__name__
        record = ResearchError(
            error_type=error_type,
            source=self._source,
            message=message,
            recoverable=True,
            details=payload,
        )
        self._errors.append(record)
        return record

    def drain(self) -> list[ResearchError]:
        """Return every recorded error and clear the log."""
        drained = list(self._errors)
        self._errors.clear()
        return drained
