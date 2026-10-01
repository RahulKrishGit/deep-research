"""Local and remote document extraction with structured partial failures."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
import pdfplumber

from deep_research.agents.evidence import (
    canonical_read_text,
    normalized_content_sha256,
)
from deep_research.observability import Tracker
from deep_research.tools.base import (
    BaseTool,
    ToolCallContext,
    ToolExecution,
    ToolExecutionError,
)
from deep_research.utils.types import INCOMPLETE_CONTENT_SHA256


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
# Suffixes the acquisition policy sends to this reader (``_required_reader``)
# that no parser here reads. A remote source whose own path ends in one is
# refused before any request (latency audit O11), with the error type and
# message its download would have ended in: the download was the whole cost of
# learning that. Two things differ: the refusal comes before any transport
# error the download could have met, and its details name the suffix with an
# empty content type, since nothing was fetched. Only one case could have read
# differently: a server that answers such a URL with a readable format, by its
# content type or a redirect.
_UNPARSED_SUFFIXES = frozenset({".doc", ".docx", ".xls", ".xlsx"})


class DocumentReaderTool(BaseTool):
    """Read supported local documents or HTTP(S) document responses."""

    name = "document_reader"
    description = (
        "Extract the text of a PDF, spreadsheet or data file at a URL, in "
        "chunks. Prefer it for primary reports and datasets, which are usually "
        "published as documents, and when a web page refused access. The source "
        "is a URL this run discovered: a local file is not research evidence."
    )
    input_schema = {"source": "string"}
    required_arguments = ("source",)
    output_schema = {
        "source": "string",
        "requested_source": "string",
        "resolved_source": "string",
        "title": "string",
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
        if _is_remote(source) and _source_suffix(source) in _UNPARSED_SUFFIXES:
            raise ToolExecutionError(
                "unsupported document format",
                error_type="unsupported_document_format",
                recoverable=False,
                details={"suffix": _source_suffix(source), "content_type": ""},
            )
        content_type = ""
        resolved_source = source
        if _is_remote(source):
            payload, content_type, resolved_source = await self._read_remote(
                context, source
            )
        else:
            payload = Path(source).read_bytes()

        requested_suffix = _source_suffix(source)
        resolved_suffix = _source_suffix(resolved_source)
        # A redirect can turn an HTML-looking landing URL into a PDF (or
        # another document type). Prefer the URL that actually served the
        # bytes, then use the response MIME type, and only fall back to the
        # requested suffix for local files and non-descriptive redirects.
        suffix = resolved_suffix or requested_suffix
        document_format = _CONTENT_TYPE_FORMATS.get(_media_type(content_type))
        if document_format is None:
            document_format = _SUFFIX_FORMATS.get(resolved_suffix)
        if document_format is None:
            document_format = _SUFFIX_FORMATS.get(requested_suffix)
        if document_format is None:
            raise ToolExecutionError(
                "unsupported document format",
                error_type="unsupported_document_format",
                recoverable=False,
                details={"suffix": suffix, "content_type": content_type},
            )

        try:
            chunks, failures, extraction_complete, metadata_title = _extract(
                document_format, payload, self._chunk_chars, self._csv_rows_per_chunk
            )
        except Exception as error:
            # A document the format's parser cannot read — a truncated PDF, a
            # malformed JSON body — is an extraction limitation, not a
            # transport failure: the bytes arrived successfully and could not
            # be turned into text. Naming it here is what lets the acquisition
            # policy record "this document had no usable content" instead of
            # mislabelling it as a failed request. Only the exception's type
            # name is published, never its message or the payload.
            raise ToolExecutionError(
                "document extraction failed",
                error_type="document_extraction_failed",
                details={"format": document_format, "error_type": type(error).__name__},
            ) from error
        document_text = _document_text(chunks)
        title = _document_title(
            document_format, metadata_title, document_text, source, resolved_source
        )
        data = {
            "source": source,
            "requested_source": source,
            "resolved_source": resolved_source,
            "title": title,
            "format": document_format,
            "chunks": chunks,
            "failures": failures,
            "content_sha256": _document_sha256(
                document_text, extraction_complete
            ),
            "extraction_complete": extraction_complete,
        }
        if not chunks:
            raise ToolExecutionError(
                "document extraction produced no chunks",
                error_type="document_extraction_failed",
                details={"failure_count": len(failures)},
                data=data,
            )
        if not canonical_read_text(document_text):
            # Whitespace-only content is no content at all, and it is not an
            # incomplete extraction either. It fails here as a documented tool
            # failure, exactly as an empty scraped page does, instead of
            # letting the content-hash helper raise an internal contract error
            # that ``tools.base`` would publish as an unexpected failure.
            raise ToolExecutionError(
                "document extraction produced no text",
                error_type="empty_document_content",
                details={"chunk_count": len(chunks)},
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


def _document_sha256(document_text: str, extraction_complete: bool) -> str:
    """The content hash of ``document_text``, or a placeholder when unusable.

    A windowed extraction keeps only part of the document, and pages whose
    text could not be read are missing from it entirely: neither may be
    hashed as if it identified the complete work, so both report the same
    explicit marker instead of a digest that would look authoritative.

    Total by construction: the caller publishes this value for failures too,
    so it never raises for text that canonicalizes to nothing.
    """
    if not extraction_complete or not canonical_read_text(document_text):
        return INCOMPLETE_CONTENT_SHA256
    return normalized_content_sha256(document_text)


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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool, str | None]:
    """Return ``chunks``, ``failures``, whether the extraction is complete,
    and the document's own metadata title (D3), or ``None`` when its format
    carries no such metadata.

    Completeness is a property of the *document*, not of the transport: text,
    JSON, and CSV payloads are extracted whole, while a PDF that lost a page —
    to a parse failure or to having no extractable text at all — is not.
    """
    if document_format in {"text", "markdown"}:
        return _text_chunks(payload.decode("utf-8"), chunk_chars), [], True, None
    if document_format == "json":
        value = json.loads(payload.decode("utf-8"))
        return (
            _text_chunks(
                json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
                chunk_chars,
            ),
            [],
            True,
            None,
        )
    if document_format == "csv":
        return _csv_chunks(payload, csv_rows_per_chunk), [], True, None
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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool, str | None]:
    """Return ``chunks``, per-page ``failures``, completeness, and the PDF's
    own ``Title`` metadata field (D3), or ``None`` when it has none.

    A page whose text could not be extracted is skipped rather than reported
    as an empty page, and its loss makes the extraction incomplete: a scanned
    page is missing text, and a hash over what remains would identify a
    document that was never fully read.
    """
    chunks: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    skipped_pages = 0
    with pdfplumber.open(io.BytesIO(payload)) as document:
        metadata_title = _pdf_metadata_title(document)
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
    return chunks, failures, not failures and skipped_pages == 0, metadata_title


def _pdf_metadata_title(document: object) -> str | None:
    """A PDF's own ``Title`` metadata field, stripped, or ``None``.

    Read defensively: ``pdfplumber``'s ``metadata`` is a plain dict when
    present, but a malformed or absent document info dictionary must never
    raise here -- a missing title is the honest default, not a failed read.
    """
    metadata = getattr(document, "metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    title = metadata.get("Title")
    return title.strip() if isinstance(title, str) and title.strip() else None


# A heading is short: this is the same length the media-type guard elsewhere
# in this project uses to distinguish a label from a body (D3).
_HEADING_MAX_CHARS = 200

# A page number, running header, download stamp or copyright line is never
# a document's title, however early it sits in the extracted text
# (RevW5Titles P2): a scanned journal article's first text line is
# routinely one of these, not the paper's own title.
_PAGE_NUMBER_LINE_PATTERN = re.compile(
    r"^(?:page\s+)?\d+(?:\s*(?:of|/)\s*\d+)?$", re.IGNORECASE
)
_HEADING_URL_PATTERN = re.compile(r"https?://|www\.", re.IGNORECASE)
_RUNNING_HEADER_PATTERN = re.compile(r"\b(?:Vol|pp|No)\.", re.IGNORECASE)
_SKIPPED_LINE_PREFIXES = ("\u00a9", "copyright", "downloaded from")

# A heading names something in words: a rule of underscores or dashes -- a
# PDF's own page-break ornament -- has none (D3, run 5).
_HEADING_LETTER_PATTERN = re.compile(r"[A-Za-z]")

# A heading is one clause, or occasionally two ("Chapter 3. Results", "Fig.
# 1. Overview"): what separates a genuine compound heading from a run-on
# paragraph (ReRevW5) is not whether a line contains a second sentence at
# all, but how much of one it strings together. A line only fails this
# check when it carries at least two sentence breaks (three-plus clauses),
# or when a single embedded break sits in a line already too long to be a
# heading rather than a caption.
_SENTENCE_BREAK_PATTERN = re.compile(r"[.!?]\s+[A-Z0-9]")
_RUN_ON_LENGTH_THRESHOLD = 120


def _is_run_on_paragraph(line: str) -> bool:
    breaks = len(_SENTENCE_BREAK_PATTERN.findall(line))
    if breaks >= 2:
        return True
    return breaks == 1 and len(line) > _RUN_ON_LENGTH_THRESHOLD


def _is_skippable_heading_line(line: str) -> bool:
    """Whether ``line`` is a running artefact or ordinary prose, not a heading."""
    if not _HEADING_LETTER_PATTERN.search(line):
        return True
    if _PAGE_NUMBER_LINE_PATTERN.match(line):
        return True
    if _HEADING_URL_PATTERN.search(line):
        return True
    if line.casefold().startswith(_SKIPPED_LINE_PREFIXES):
        return True
    if _RUNNING_HEADER_PATTERN.search(line):
        return True
    return _is_run_on_paragraph(line)


# A heading cut off mid-phrase by a PDF's own line wrap ends on a bare,
# lower-case function word, never on terminal punctuation (D3, run 5):
# "... in the Decline of the" continues as "Roman Republic" on the next
# line. The match is case-sensitive: a title-cased heading capitalises its
# own last word ("Appendix A", "What We Work For"), while a genuine wrap
# leaves the word in running, lower case ("... of the").
_INCOMPLETE_HEADING_ENDING_PATTERN = re.compile(
    r"\b(?:the|a|an|of|in|on|for|to|and|or|by|with|at|from)$"
)

# A heading with no terminal punctuation whose continuation starts in lower
# case is also a wrap (D3, run 5 follow-up): "... in the Decline" continuing
# as "of the Late Republic" breaks after a content word, not a bare
# function word, so the ending alone cannot tell it apart from a complete
# heading -- the next line's own case is what does.
_HEADING_TERMINAL_PUNCTUATION = (".", "!", "?", ":")
_LOWERCASE_START_PATTERN = re.compile(r"^[a-z]")


def _looks_incomplete(heading: str) -> bool:
    return bool(_INCOMPLETE_HEADING_ENDING_PATTERN.search(heading))


def _continues_lower_case(heading: str, continuation: str) -> bool:
    return not heading.endswith(
        _HEADING_TERMINAL_PUNCTUATION
    ) and bool(_LOWERCASE_START_PATTERN.match(continuation))


def _next_heading_line(lines: list[str], start: int) -> str | None:
    """The next non-blank line after ``start``, or ``None`` when it is
    itself unusable as a heading's continuation."""
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            continue
        if _is_skippable_heading_line(stripped):
            return None
        return stripped
    return None


def _heading_line(text: str) -> str | None:
    """The document's first heading-shaped line, or ``None`` when it has
    none: a title is short, so a line longer than ``_HEADING_MAX_CHARS`` is
    prose, not a heading, however early it sits in the extracted text, and
    a page number, URL, copyright line, running header or letterless rule
    is skipped rather than mistaken for one. A heading a PDF's own line
    wrap cut off mid-phrase is joined with its continuation, within
    ``_HEADING_MAX_CHARS``.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or len(stripped) > _HEADING_MAX_CHARS:
            continue
        if _is_skippable_heading_line(stripped):
            continue
        heading = stripped
        continuation = _next_heading_line(lines, index + 1)
        if continuation is not None and (
            _looks_incomplete(heading)
            or _continues_lower_case(heading, continuation)
        ):
            joined = f"{heading} {continuation}"
            if len(joined) <= _HEADING_MAX_CHARS:
                heading = joined
        return heading
    return None


def _source_names(requested: str, resolved: str) -> set[str]:
    """Every string that names the source itself -- never its content: the
    requested and resolved locations, and each one's bare file name and
    stem (a file name without its extension, e.g. an authoring tool's
    metadata title echoing ``Hardy2017`` for ``Hardy2017.pdf``).
    """
    names = {requested, resolved}
    for source in (requested, resolved):
        path = Path(urlsplit(source).path if _is_remote(source) else source)
        if path.name:
            names.add(path.name)
        if path.stem:
            names.add(path.stem)
    return names


# Authoring-tool placeholders that name no document at all (RevW5Titles P2):
# a save dialog's default caption, never a document's own title.
_JUNK_METADATA_TITLE_PATTERN = re.compile(
    r"^Microsoft (?:Word|PowerPoint|Excel) - |^untitled$|^PowerPoint Presentation$",
    re.IGNORECASE,
)


def _is_usable_metadata_title(title: str, requested: str, resolved: str) -> bool:
    """Whether ``title`` names the document, rather than its own source or
    an authoring tool's placeholder caption.
    """
    if title in _source_names(requested, resolved):
        return False
    return not _JUNK_METADATA_TITLE_PATTERN.search(title)


def _document_title(
    document_format: str,
    metadata_title: str | None,
    document_text: str,
    requested: str,
    resolved: str,
) -> str:
    """The document's title (D3, RevW5Titles P1-b): a PDF's own metadata
    title, unless it is missing, names only the source itself, or is an
    authoring tool's placeholder, in which case its first heading-shaped
    line stands in for it. Every other format returns ``""`` so the read
    registry's own URL-then-search-candidate title path applies, exactly as
    it did before a document ever carried a title of its own.
    """
    if document_format != "pdf":
        return ""
    if metadata_title and _is_usable_metadata_title(metadata_title, requested, resolved):
        return metadata_title
    return _heading_line(document_text) or ""
