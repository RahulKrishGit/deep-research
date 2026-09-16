"""Local and remote document extraction with structured partial failures."""

from __future__ import annotations

import asyncio
import csv
import io
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
import pdfplumber

from deep_research.agents.evidence import normalized_content_sha256
from deep_research.observability import Tracker
from deep_research.tools.base import (
    BaseTool,
    ToolCallContext,
    ToolExecution,
    ToolExecutionError,
)


class AsyncHttpClient(Protocol):
    async def get(self, url: str, **kwargs: Any) -> httpx.Response: ...


_SUFFIX_FORMATS = {
    ".pdf": "pdf",
    ".csv": "csv",
    ".json": "json",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
}
_CONTENT_TYPE_FORMATS = {
    "application/pdf": "pdf",
    "text/csv": "csv",
    "application/json": "json",
    "text/markdown": "markdown",
    "text/plain": "text",
}

# The published value of ``content_sha256`` when the reader cannot hash a
# complete document. It is deliberately not a digest: a partial extraction
# hashed as if it were the whole work would give two different documents one
# identity, and every consumer of this payload must treat the marker as
# "no usable content hash" instead.
_INCOMPLETE_HASH = "incomplete"


class DocumentReaderTool(BaseTool):
    """Read supported local documents or HTTP(S) document responses."""

    name = "document_reader"
    description = "Extract structured text chunks from local or remote documents."
    input_schema = {"source": "string"}
    required_arguments = ("source",)
    output_schema = {
        "source": "string",
        "requested_source": "string",
        "resolved_source": "string",
        "format": "string",
        "chunks": "array",
        "failures": "array",
        "content_sha256": "string",
        "extraction_complete": "boolean",
    }

    def __init__(
        self,
        tracker: Tracker,
        *,
        client: AsyncHttpClient | None = None,
        timeout_s: float = 20.0,
        max_retries: int = 2,
        chunk_chars: int = 8000,
        csv_rows_per_chunk: int = 100,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        super().__init__(tracker)
        if (
            isinstance(timeout_s, bool)
            or not isinstance(timeout_s, (int, float))
            or timeout_s <= 0
        ):
            raise ValueError("timeout_s must be greater than zero")
        if (
            isinstance(max_retries, bool)
            or not isinstance(max_retries, int)
            or max_retries < 0
            or max_retries > 2
        ):
            raise ValueError(
                "max_retries must be a non-negative integer no greater than two"
            )
        if (
            isinstance(chunk_chars, bool)
            or not isinstance(chunk_chars, int)
            or chunk_chars <= 0
        ):
            raise ValueError("chunk_chars must be greater than zero")
        if (
            isinstance(csv_rows_per_chunk, bool)
            or not isinstance(csv_rows_per_chunk, int)
            or csv_rows_per_chunk <= 0
        ):
            raise ValueError("csv_rows_per_chunk must be greater than zero")
        self._client = client
        self._timeout_s = float(timeout_s)
        self._max_retries = max_retries
        self._chunk_chars = chunk_chars
        self._csv_rows_per_chunk = csv_rows_per_chunk
        self._sleep = sleep

    async def _execute(self, context: ToolCallContext, **kwargs: Any) -> ToolExecution:
        source = kwargs.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ToolExecutionError(
                "source must be a non-empty string",
                error_type="ValidationError",
                recoverable=False,
            )
        source = source.strip()
        content_type = ""
        resolved_source = source
        if _is_remote(source):
            payload, content_type, resolved_source = await self._read_remote(
                context, source
            )
        else:
            payload = Path(source).read_bytes()

        suffix = _source_suffix(source)
        document_format = _SUFFIX_FORMATS.get(suffix)
        if document_format is None and not suffix:
            document_format = _CONTENT_TYPE_FORMATS.get(_media_type(content_type))
        if document_format is None:
            raise ToolExecutionError(
                "unsupported document format",
                error_type="unsupported_document_format",
                recoverable=False,
                details={"suffix": suffix, "content_type": content_type},
            )

        chunks, failures, extraction_complete = _extract(
            document_format, payload, self._chunk_chars, self._csv_rows_per_chunk
        )
        data = {
            "source": source,
            "requested_source": source,
            "resolved_source": resolved_source,
            "format": document_format,
            "chunks": chunks,
            "failures": failures,
            "content_sha256": _document_sha256(chunks, extraction_complete),
            "extraction_complete": extraction_complete,
        }
        if not chunks:
            raise ToolExecutionError(
                "document extraction produced no chunks",
                error_type="document_extraction_failed",
                details={"failure_count": len(failures)},
                data=data,
            )
        return ToolExecution(
            data=data,
            output_summary={
                "format": document_format,
                "chunk_count": len(chunks),
                "failure_count": len(failures),
            },
            metadata={
                "format": document_format,
                "chunk_count": len(chunks),
                "partial": bool(failures) or not extraction_complete,
            },
        )

    async def _read_remote(
        self, context: ToolCallContext, source: str
    ) -> tuple[bytes, str, str]:
        if self._client is not None:
            return await self._get_remote(context, self._client, source)
        async with httpx.AsyncClient(
            timeout=self._timeout_s, follow_redirects=True
        ) as client:
            return await self._get_remote(context, client, source)

    async def _get_remote(
        self, context: ToolCallContext, client: AsyncHttpClient, source: str
    ) -> tuple[bytes, str, str]:
        for attempt in range(self._max_retries + 1):
            try:
                response = await client.get(
                    source, timeout=self._timeout_s, follow_redirects=True
                )
                response.raise_for_status()
                return (
                    response.content,
                    response.headers.get("content-type", ""),
                    _resolved_source(response, source),
                )
            except (
                asyncio.TimeoutError,
                httpx.TimeoutException,
                httpx.HTTPStatusError,
            ) as error:
                retryable = isinstance(
                    error, (asyncio.TimeoutError, httpx.TimeoutException)
                ) or (
                    isinstance(error, httpx.HTTPStatusError)
                    and (
                        error.response.status_code == 429
                        or 500 <= error.response.status_code <= 599
                    )
                )
                if attempt == self._max_retries or not retryable:
                    details: dict[str, Any] = {"attempts": attempt + 1}
                    if isinstance(error, httpx.HTTPStatusError):
                        details["status_code"] = error.response.status_code
                    raise ToolExecutionError(
                        str(error) or type(error).__name__,
                        error_type=type(error).__name__,
                        details=details,
                    ) from error
                context.record_retry()
                await self._sleep(_retry_delay(error, attempt))
        raise AssertionError("retry loop must return or raise")


def _is_remote(source: str) -> bool:
    return urlsplit(source).scheme.lower() in {"http", "https"}


def _resolved_source(response: httpx.Response, requested: str) -> str:
    """The source the bytes actually came from, after any redirect.

    A mirror or a redirect target must stay visible in the read's provenance;
    a transport double with no request attached has no final URL to report,
    so the requested source stands.
    """
    try:
        resolved = str(response.url)
    except RuntimeError:
        return requested
    return resolved or requested


def _document_text(chunks: list[dict[str, Any]]) -> str:
    """The complete extracted text of one document, in reading order."""
    return "".join(str(chunk.get("text", "")) for chunk in chunks)


def _document_sha256(
    chunks: list[dict[str, Any]], extraction_complete: bool
) -> str:
    """The content hash of ``chunks``, or a placeholder when unusable.

    A windowed extraction keeps only part of the document, and pages whose
    text could not be read are missing from it entirely: neither may be
    hashed as if it identified the complete work, so both report the same
    explicit marker instead of a digest that would look authoritative.
    """
    if not chunks or not extraction_complete:
        return _INCOMPLETE_HASH
    return normalized_content_sha256(_document_text(chunks))


def _source_suffix(source: str) -> str:
    return Path(urlsplit(source).path if _is_remote(source) else source).suffix.lower()


def _media_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def _retry_delay(error: BaseException, retry_index: int) -> float:
    if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 429:
        retry_after = error.response.headers.get("Retry-After")
        if retry_after is not None:
            try:
                delay = float(retry_after)
            except ValueError:
                pass
            else:
                if delay >= 0:
                    return delay
    return 0.5 * (2**retry_index)


def _extract(
    document_format: str,
    payload: bytes,
    chunk_chars: int,
    csv_rows_per_chunk: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Return ``chunks``, ``failures``, and whether the extraction is complete.

    Completeness is a property of the *document*, not of the transport: text,
    JSON, and CSV payloads are extracted whole, while a PDF that lost a page —
    to a parse failure or to having no extractable text at all — is not.
    """
    if document_format in {"text", "markdown"}:
        return _text_chunks(payload.decode("utf-8"), chunk_chars), [], True
    if document_format == "json":
        value = json.loads(payload.decode("utf-8"))
        return (
            _text_chunks(
                json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
                chunk_chars,
            ),
            [],
            True,
        )
    if document_format == "csv":
        return _csv_chunks(payload, csv_rows_per_chunk), [], True
    return _pdf_chunks(payload, chunk_chars)


def _text_chunks(text: str, chunk_chars: int) -> list[dict[str, Any]]:
    return [
        {"text": text[start : start + chunk_chars], "chunk_index": index}
        for index, start in enumerate(range(0, len(text), chunk_chars))
    ]


def _csv_chunks(payload: bytes, rows_per_chunk: int) -> list[dict[str, Any]]:
    rows = list(csv.reader(io.StringIO(payload.decode("utf-8-sig"), newline="")))
    if not rows:
        return []
    header, records = rows[0], rows[1:]
    chunks: list[dict[str, Any]] = []
    for index, start in enumerate(range(0, len(records), rows_per_chunk)):
        group = records[start : start + rows_per_chunk]
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        if index == 0:
            writer.writerow(header)
        writer.writerows(group)
        chunks.append(
            {
                "text": output.getvalue(),
                "chunk_index": index,
                "row_start": start + 1,
                "row_end": start + len(group),
            }
        )
    return chunks


def _pdf_chunks(
    payload: bytes, chunk_chars: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Return ``chunks``, per-page ``failures``, and completeness.

    A page whose text could not be extracted is skipped rather than reported
    as an empty page, and its loss makes the extraction incomplete: a scanned
    page is missing text, and a hash over what remains would identify a
    document that was never fully read.
    """
    chunks: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    skipped_pages = 0
    with pdfplumber.open(io.BytesIO(payload)) as document:
        for page_number, page in enumerate(document.pages, start=1):
            try:
                text = page.extract_text()
                if not text:
                    skipped_pages += 1
                    continue
                for item in _text_chunks(text, chunk_chars):
                    item["chunk_index"] = len(chunks)
                    item["page"] = page_number
                    chunks.append(item)
            except Exception as error:
                failures.append(
                    {
                        "scope": "page",
                        "reference": page_number,
                        "error_type": type(error).__name__,
                        "message": str(error),
                    }
                )
    return chunks, failures, not failures and skipped_pages == 0
