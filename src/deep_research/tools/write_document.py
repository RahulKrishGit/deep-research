"""Safely write UTF-8 Markdown and JSON artifacts beneath an injected root."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from pydantic import JsonValue

from deep_research.observability import Tracker
from deep_research.tools.base import (
    BaseTool,
    ToolCallContext,
    ToolExecution,
    ToolExecutionError,
)

# The two artifact formats this project publishes, and the only two. The
# quality record is JSON because a consumer reads it by key; the reader report
# and the evidence ledger are Markdown because a reader reads them. Nothing
# else is writable through this tool, and the suffix decides which one a
# filename names — a caller cannot declare a format the suffix contradicts.
ARTIFACT_SUFFIX_FORMATS: dict[str, str] = {
    ".md": "markdown",
    ".json": "json",
}
DEFAULT_ARTIFACT_SUFFIX = ".md"


class WriteDocumentTool(BaseTool):
    """Write one Markdown or JSON document beneath a caller-provided root."""

    name = "write_document"
    description = (
        "Write a UTF-8 Markdown research report, or a JSON quality record, "
        "to the output directory."
    )
    input_schema = {"filename": "string", "content": "string"}
    required_arguments = ("filename", "content")
    output_schema = {"path": "string", "bytes_written": "integer"}

    def __init__(self, tracker: Tracker, output_root: Path | str) -> None:
        super().__init__(tracker)
        self._output_root = Path(output_root)

    def _observability_inputs(
        self, kwargs: dict[str, Any]
    ) -> dict[str, JsonValue]:
        filename = kwargs.get("filename")
        content = kwargs.get("content")
        return {
            "filename": filename if isinstance(filename, str) else "",
            "content_chars": len(content) if isinstance(content, str) else 0,
        }

    async def _execute(
        self, context: ToolCallContext, **kwargs: Any
    ) -> ToolExecution:
        del context
        filename = kwargs.get("filename")
        content = kwargs.get("content")
        if not isinstance(filename, str):
            raise _validation_error("filename must be a non-empty string")
        if not isinstance(content, str):
            raise _validation_error("content must be a string")

        root, target, relative_path = _resolve_document_target(
            self._output_root, filename
        )
        if target.exists() and target.is_dir():
            raise _validation_error("filename must identify a file, not a directory")

        root.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = content.encode("utf-8")
        _atomic_write(target, payload)

        data = {"path": relative_path.as_posix(), "bytes_written": len(payload)}
        return ToolExecution(
            data=data,
            output_summary=data,
            metadata={
                "format": ARTIFACT_SUFFIX_FORMATS[relative_path.suffix]
            },
        )


def _resolve_document_target(
    output_root: Path, filename: str
) -> tuple[Path, Path, PurePosixPath]:
    if not filename or not filename.strip():
        raise _validation_error("filename must be a non-empty string")
    if filename.endswith(("/", "\\")):
        raise _validation_error("filename must not end with a directory separator")

    normalized = filename.replace("\\", "/")
    windows_path = PureWindowsPath(normalized)
    candidate_path = PurePosixPath(normalized)
    raw_components = normalized.split("/")
    if (
        candidate_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or any(component in {".", ".."} for component in raw_components)
    ):
        raise _validation_error("filename must be a relative path without traversal")
    if normalized.endswith("."):
        raise _validation_error(_suffix_message())
    if candidate_path.suffix and candidate_path.suffix not in ARTIFACT_SUFFIX_FORMATS:
        raise _validation_error(_suffix_message())

    relative_path = (
        candidate_path
        if candidate_path.suffix
        else PurePosixPath(
            f"{candidate_path.as_posix()}{DEFAULT_ARTIFACT_SUFFIX}"
        )
    )
    root = output_root.resolve()
    target = (root / Path(relative_path.as_posix())).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise _validation_error("filename resolves outside the output root") from error
    return root, target, relative_path


def _suffix_message() -> str:
    suffixes = " or ".join(sorted(ARTIFACT_SUFFIX_FORMATS))
    return f"filename must use the {suffixes} suffix"


def _atomic_write(target: Path, payload: bytes) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as file:
            temporary_path = Path(file.name)
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, target)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def _validation_error(message: str) -> ToolExecutionError:
    return ToolExecutionError(
        message, error_type="ValidationError", recoverable=False
    )
