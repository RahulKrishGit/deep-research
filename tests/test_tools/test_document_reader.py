import json
from pathlib import Path

import httpx
import pytest

from deep_research.agents.evidence import (
    normalized_content_sha256,
    passages_from_chunks,
)
from deep_research.tools.document_reader import DocumentReaderTool
from deep_research.utils.types import INCOMPLETE_CONTENT_SHA256


@pytest.mark.asyncio
async def test_a_read_reports_its_hash_completeness_and_locators(tracker) -> None:
    """Everything a read registry needs, from the boundary that fetched it."""
    original = Path("tests/fixtures/documents/sample.md").read_text(encoding="utf-8")

    async with tracker.session_span("session-1", "question"):
        result = await DocumentReaderTool(tracker, chunk_chars=32).execute(
            source="tests/fixtures/documents/sample.md"
        )

    assert result.data is not None
    assert result.data["requested_source"] == "tests/fixtures/documents/sample.md"
    assert result.data["resolved_source"] == "tests/fixtures/documents/sample.md"
    assert result.data["extraction_complete"] is True
    assert result.data["content_sha256"] == normalized_content_sha256(original)
    # The chunks carry the locators a passage can be cited and re-read by.
    assert passages_from_chunks(result.data["chunks"]) == {
        f"chunk-{index // 32}": original[index : index + 32]
        for index in range(0, len(original), 32)
    }


@pytest.mark.asyncio
async def test_a_remote_redirect_reports_the_resolved_source(tracker) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.test":
            return httpx.Response(
                301,
                headers={"Location": "https://cdn.example.test/report.txt"},
                request=request,
            )
        return httpx.Response(200, content=b"remote document", request=request)

    source = "https://example.test/report.txt"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async with tracker.session_span("session-1", "question"):
            result = await DocumentReaderTool(tracker, client=client).execute(
                source=source
            )

    assert result.data is not None
    assert result.data["source"] == source
    assert result.data["requested_source"] == source
    assert result.data["resolved_source"] == "https://cdn.example.test/report.txt"
    assert result.data["extraction_complete"] is True


@pytest.mark.asyncio
async def test_redirected_pdf_uses_resolved_suffix_even_when_landing_page_is_html(
    tracker, monkeypatch,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/landing.html":
            return httpx.Response(
                302,
                headers={"Location": "https://cdn.example.test/report.pdf"},
                request=request,
            )
        # The extractor is patched below; this body only needs to reach the
        # format dispatch after the redirect has been followed.
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"pdf bytes",
            request=request,
        )

    class Page:
        def extract_text(self) -> str:
            return "Late report finding."

    class Pdf:
        pages = [Page()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(
        "deep_research.tools.document_reader.pdfplumber.open", lambda _: Pdf()
    )
    source = "https://example.test/landing.html"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async with tracker.session_span("session-1", "question"):
            result = await DocumentReaderTool(tracker, client=client).execute(
                source=source
            )

    assert result.success is True
    assert result.data is not None
    assert result.data["format"] == "pdf"
    assert result.data["resolved_source"] == "https://cdn.example.test/report.pdf"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "document_format"),
    [("sample.md", "markdown"), ("sample.txt", "text")],
)
async def test_reader_chunks_local_text_documents_in_order(
    tracker, filename, document_format
) -> None:
    source = Path("tests/fixtures/documents") / filename
    original = source.read_text(encoding="utf-8")

    async with tracker.session_span("session-1", "question"):
        result = await DocumentReaderTool(tracker, chunk_chars=32).execute(
            source=str(source)
        )

    assert result.success is True
    assert result.data is not None
    assert result.data["source"] == str(source)
    assert result.data["format"] == document_format
    assert result.data["failures"] == []
    assert result.data["chunks"] == [
        {"text": original[index : index + 32], "chunk_index": index // 32}
        for index in range(0, len(original), 32)
    ]
    assert "".join(chunk["text"] for chunk in result.data["chunks"]) == original


@pytest.mark.asyncio
async def test_reader_formats_csv_and_json_documents(tracker, tmp_path) -> None:
    csv_source = tmp_path / "table.csv"
    csv_source.write_text("name,city\nAda,London\nLin,Paris\n", encoding="utf-8")
    json_source = tmp_path / "value.json"
    value = {"z": ["é"], "a": 1}
    json_source.write_text(json.dumps(value), encoding="utf-8")

    async with tracker.session_span("session-1", "question"):
        csv_result = await DocumentReaderTool(tracker, csv_rows_per_chunk=1).execute(
            source=str(csv_source)
        )
        json_result = await DocumentReaderTool(tracker, chunk_chars=6).execute(
            source=str(json_source)
        )

    assert csv_result.data is not None
    assert csv_result.data["chunks"] == [
        {
            "text": "name,city\nAda,London\n",
            "chunk_index": 0,
            "row_start": 1,
            "row_end": 1,
        },
        {"text": "Lin,Paris\n", "chunk_index": 1, "row_start": 2, "row_end": 2},
    ]
    formatted = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    assert json_result.data is not None
    assert "".join(item["text"] for item in json_result.data["chunks"]) == formatted


@pytest.mark.asyncio
async def test_reader_fetches_remote_text_with_timeout_and_source_preserved(
    tracker,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"remote document", request=request)

    source = "https://example.test/report.txt"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async with tracker.session_span("session-1", "question"):
            result = await DocumentReaderTool(
                tracker, client=client, timeout_s=7
            ).execute(source=source)

    assert result.data is not None
    assert result.data["source"] == source
    assert result.data["format"] == "text"
    assert requests[0].url == httpx.URL(source)
    assert requests[0].extensions["timeout"]["connect"] == 7


@pytest.mark.asyncio
async def test_reader_preserves_pdf_page_failures(monkeypatch, tracker) -> None:
    class Page:
        def __init__(self, value):
            self.value = value

        def extract_text(self):
            if isinstance(self.value, Exception):
                raise self.value
            return self.value

    class Pdf:
        pages = [
            Page("page one"),
            Page(RuntimeError("damaged page")),
            Page("page three"),
        ]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(
        "deep_research.tools.document_reader.pdfplumber.open", lambda _: Pdf()
    )
    source = "report.pdf"
    report = Path(source)
    report.write_bytes(b"not-a-real-pdf")
    try:
        async with tracker.session_span("session-1", "question"):
            result = await DocumentReaderTool(tracker).execute(source=source)
    finally:
        report.unlink()

    assert result.success is True
    assert result.data is not None
    assert result.data["chunks"] == [
        {"text": "page one", "chunk_index": 0, "page": 1},
        {"text": "page three", "chunk_index": 1, "page": 3},
    ]
    assert result.data["failures"] == [
        {
            "scope": "page",
            "reference": 2,
            "error_type": "RuntimeError",
            "message": "damaged page",
        }
    ]
    # One page is missing from this extraction, so the text is incomplete and
    # its content hash must never be treated as the identity of the document.
    assert result.data["extraction_complete"] is False


@pytest.mark.asyncio
async def test_reader_marks_a_scanned_page_extraction_incomplete(
    monkeypatch, tracker
) -> None:
    """A page with no extractable text is missing text, not empty text."""
    class Page:
        def __init__(self, value):
            self.value = value

        def extract_text(self):
            return self.value

    class Pdf:
        pages = [Page("page one"), Page(""), Page("page three")]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(
        "deep_research.tools.document_reader.pdfplumber.open", lambda _: Pdf()
    )
    source = "scanned.pdf"
    report = Path(source)
    report.write_bytes(b"not-a-real-pdf")
    try:
        async with tracker.session_span("session-1", "question"):
            result = await DocumentReaderTool(tracker).execute(source=source)
    finally:
        report.unlink()

    assert result.success is True
    assert result.data is not None
    assert [chunk["page"] for chunk in result.data["chunks"]] == [1, 3]
    assert result.data["failures"] == []
    assert result.data["extraction_complete"] is False


@pytest.mark.asyncio
async def test_a_whitespace_only_document_is_a_documented_failure(
    tracker, tmp_path
) -> None:
    """A canonical-empty document is no content at all, not a partial read.

    Its chunk list is non-empty, so the old ordering reached the content-hash
    helper first and let an internal contract error escape as
    ``error_type="EvidenceContractError"`` — an undocumented failure neither
    the model nor an audit can act on.
    """
    source = tmp_path / "blank.txt"
    source.write_text("\n", encoding="utf-8")

    async with tracker.session_span("session-1", "question"):
        result = await DocumentReaderTool(tracker).execute(source=str(source))

    assert result.success is False
    assert result.error is not None
    assert result.error.type == "empty_document_content"
    assert result.error.message == "document extraction produced no text"
    assert result.error.recoverable is True
    assert result.data is not None
    # The extraction itself completed — the document simply has no text — so
    # the completeness flag stays true while the hash reports that there is
    # nothing usable to identify. No ReadRecord can be built from this pairing
    # (the contract rejects a complete read without a digest), and the read
    # never succeeds, so the pairing only ever appears on a failure payload.
    assert result.data["extraction_complete"] is True
    assert result.data["content_sha256"] == INCOMPLETE_CONTENT_SHA256
    assert "EvidenceContractError" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_reader_returns_partial_error_when_every_pdf_page_fails(
    monkeypatch, tracker
) -> None:
    class Page:
        def extract_text(self):
            raise RuntimeError("damaged page")

    class Pdf:
        pages = [Page()]

        def __enter__(self): return self
        def __exit__(self, *args): return None

    monkeypatch.setattr(
        "deep_research.tools.document_reader.pdfplumber.open", lambda _: Pdf()
    )
    source = "report.pdf"
    report = Path(source)
    report.write_bytes(b"not-a-real-pdf")
    try:
        async with tracker.session_span("session-1", "question"):
            result = await DocumentReaderTool(tracker).execute(source=source)
    finally:
        report.unlink()

    assert result.success is False
    assert result.error is not None
    assert result.error.type == "document_extraction_failed"
    assert result.data is not None
    assert result.data["failures"]


@pytest.mark.asyncio
async def test_reader_reports_unsupported_and_missing_local_documents(
    tracker, tmp_path
) -> None:
    unsupported = tmp_path / "file.docx"
    unsupported.write_text("ignored", encoding="utf-8")
    missing = tmp_path / "missing.txt"
    async with tracker.session_span("session-1", "question"):
        unsupported_result = await DocumentReaderTool(tracker).execute(
            source=str(unsupported)
        )
        missing_result = await DocumentReaderTool(tracker).execute(
            source=str(missing)
        )

    assert unsupported_result.error is not None
    assert unsupported_result.error.type == "unsupported_document_format"
    assert unsupported_result.error.details["suffix"] == ".docx"
    assert missing_result.error is not None
    assert missing_result.error.type == "FileNotFoundError"


@pytest.mark.asyncio
async def test_reader_records_two_retries_for_repeated_remote_timeout(tracker) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out", request=request)

    async def sleep(_: float) -> None:
        return None

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async with tracker.session_span("session-1", "question"):
            result = await DocumentReaderTool(
                tracker, client=client, sleep=sleep
            ).execute(source="https://example.test/report.txt")

    assert result.success is False
    assert calls == 3
    assert result.metadata["retry_count"] == 2


@pytest.mark.asyncio
async def test_reader_retries_rate_limit_using_numeric_retry_after(tracker) -> None:
    calls = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429, headers={"Retry-After": "1.25"}, request=request
            )
        return httpx.Response(200, text="remote document", request=request)

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async with tracker.session_span("session-1", "question"):
            result = await DocumentReaderTool(
                tracker, client=client, sleep=sleep
            ).execute(source="https://example.test/report.txt")

    assert result.success is True
    assert calls == 2
    assert delays == [1.25]
    assert result.metadata["retry_count"] == 1


# ---------------------------------------------------------------------------
# D3: a document's title, when its metadata carries none.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reader_uses_the_first_heading_line_when_a_pdf_has_no_metadata_title(
    monkeypatch, tracker
) -> None:
    """No metadata title: the document's own first heading line stands in."""

    class Page:
        def extract_text(self):
            return "Findings on Filing Delays\nA longer body paragraph follows here."

    class Pdf:
        pages = [Page()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(
        "deep_research.tools.document_reader.pdfplumber.open", lambda _: Pdf()
    )
    source = "report.pdf"
    report = Path(source)
    report.write_bytes(b"not-a-real-pdf")
    try:
        async with tracker.session_span("session-1", "question"):
            result = await DocumentReaderTool(tracker).execute(source=source)
    finally:
        report.unlink()

    assert result.success is True
    assert result.data is not None
    assert result.data["title"] == "Findings on Filing Delays"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "content"),
    [("value.json", '{"z": 1}'), ("table.csv", "name,city\nAda,London\n")],
)
async def test_reader_returns_no_title_for_non_pdf_formats(
    tracker, tmp_path, filename, content
) -> None:
    """RevW5Titles P1-b: only a PDF's own metadata or heading stands in for a
    title; json, csv, text and markdown defer to the URL/search-candidate
    title path (acquisition.py's ``_resolve_read_title``), exactly as before
    a document ever carried a title of its own."""
    source = tmp_path / filename
    source.write_text(content, encoding="utf-8")

    async with tracker.session_span("session-1", "question"):
        result = await DocumentReaderTool(tracker).execute(source=str(source))

    assert result.data["title"] == ""


@pytest.mark.asyncio
async def test_reader_uses_heading_when_pdf_metadata_title_is_just_the_file_stem(
    monkeypatch, tracker
) -> None:
    """RevW5Titles P2: a metadata title that only echoes the file name (its
    stem, without the extension) carries no information the reader does not
    already have, so the heading line stands in for it."""

    class Page:
        def extract_text(self):
            return "Findings on Filing Delays\nA longer body paragraph follows here."

    class Pdf:
        pages = [Page()]
        metadata = {"Title": "report"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(
        "deep_research.tools.document_reader.pdfplumber.open", lambda _: Pdf()
    )
    source = "report.pdf"
    report = Path(source)
    report.write_bytes(b"not-a-real-pdf")
    try:
        async with tracker.session_span("session-1", "question"):
            result = await DocumentReaderTool(tracker).execute(source=source)
    finally:
        report.unlink()

    assert result.data["title"] == "Findings on Filing Delays"


@pytest.mark.asyncio
async def test_reader_skips_a_page_number_and_copyright_line_for_the_heading(
    monkeypatch, tracker
) -> None:
    """RevW5Titles P2: a running page number and a copyright line are never
    a document's title, however early they sit in the extracted text."""

    class Page:
        def extract_text(self):
            return (
                "12\n"
                "© 2019 Example Institute\n"
                "Findings on Filing Delays\n"
                "Body text follows."
            )

    class Pdf:
        pages = [Page()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(
        "deep_research.tools.document_reader.pdfplumber.open", lambda _: Pdf()
    )
    source = "report.pdf"
    report = Path(source)
    report.write_bytes(b"not-a-real-pdf")
    try:
        async with tracker.session_span("session-1", "question"):
            result = await DocumentReaderTool(tracker).execute(source=source)
    finally:
        report.unlink()

    assert result.data["title"] == "Findings on Filing Delays"


@pytest.mark.asyncio
async def test_reader_returns_no_title_when_the_only_line_is_several_sentences(
    monkeypatch, tracker
) -> None:
    """A PDF with no real heading line -- its whole body is one paragraph of
    several sentences with no line break -- must not adopt its first
    sentence as a title; the URL/search-candidate title path applies
    (regression: e2e case blocked-html-pdf-fallback)."""

    class Page:
        def extract_text(self):
            return (
                "Adoption report. Published by Example Institute. The report "
                "states the widget adoption rate was 40 percent in 2024."
            )

    class Pdf:
        pages = [Page()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(
        "deep_research.tools.document_reader.pdfplumber.open", lambda _: Pdf()
    )
    source = "report.pdf"
    report = Path(source)
    report.write_bytes(b"not-a-real-pdf")
    try:
        async with tracker.session_span("session-1", "question"):
            result = await DocumentReaderTool(tracker).execute(source=source)
    finally:
        report.unlink()

    assert result.data["title"] == ""


@pytest.mark.asyncio
async def test_reader_keeps_a_compound_heading_with_one_sentence_break(
    monkeypatch, tracker
) -> None:
    """RevW5Titles P2 (ReRevW5): a short two-clause heading -- a chapter or
    figure caption with one embedded full stop -- is not a run-on
    paragraph, and must still become the title."""

    class Page:
        def extract_text(self):
            return "Chapter 3. Results\nBody text follows here."

    class Pdf:
        pages = [Page()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(
        "deep_research.tools.document_reader.pdfplumber.open", lambda _: Pdf()
    )
    source = "report.pdf"
    report = Path(source)
    report.write_bytes(b"not-a-real-pdf")
    try:
        async with tracker.session_span("session-1", "question"):
            result = await DocumentReaderTool(tracker).execute(source=source)
    finally:
        report.unlink()

    assert result.data["title"] == "Chapter 3. Results"


@pytest.mark.asyncio
async def test_reader_skips_a_rule_of_underscores_for_the_heading(
    monkeypatch, tracker
) -> None:
    """D3 (run 5): a heading line must contain letters; a rule of
    underscores or dashes -- a PDF's own page-break ornament -- is skipped."""

    class Page:
        def extract_text(self):
            return (
                "__________________________________________________________________\n"
                "Findings on Filing Delays\n"
                "Body text follows."
            )

    class Pdf:
        pages = [Page()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(
        "deep_research.tools.document_reader.pdfplumber.open", lambda _: Pdf()
    )
    source = "report.pdf"
    report = Path(source)
    report.write_bytes(b"not-a-real-pdf")
    try:
        async with tracker.session_span("session-1", "question"):
            result = await DocumentReaderTool(tracker).execute(source=source)
    finally:
        report.unlink()

    assert result.data["title"] == "Findings on Filing Delays"


@pytest.mark.asyncio
async def test_reader_joins_a_heading_wrapped_onto_the_next_line(
    monkeypatch, tracker
) -> None:
    """D3 (run 5): a heading that continues onto the next line -- the first
    line ends on a bare function word with no terminal punctuation -- is
    joined with it, within the 200-character cap."""

    class Page:
        def extract_text(self):
            return (
                "The Effect of a New Filing Rule on the Growth of the\n"
                "Regional Housing Market\n"
                "Body text follows."
            )

    class Pdf:
        pages = [Page()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(
        "deep_research.tools.document_reader.pdfplumber.open", lambda _: Pdf()
    )
    source = "report.pdf"
    report = Path(source)
    report.write_bytes(b"not-a-real-pdf")
    try:
        async with tracker.session_span("session-1", "question"):
            result = await DocumentReaderTool(tracker).execute(source=source)
    finally:
        report.unlink()

    assert result.data["title"] == (
        "The Effect of a New Filing Rule on the Growth of the "
        "Regional Housing Market"
    )


@pytest.mark.asyncio
async def test_reader_does_not_join_a_heading_ending_in_a_capitalised_word(
    monkeypatch, tracker
) -> None:
    """D3 (run 5 follow-up): a heading ending in a capitalised word that
    happens to spell a function word ('Appendix A') is complete; only a
    bare lower-case function word signals a genuine line wrap."""

    class Page:
        def extract_text(self):
            return (
                "Appendix A\n"
                "The committee met on three occasions during the year and "
                "reviewed each filing in turn."
            )

    class Pdf:
        pages = [Page()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(
        "deep_research.tools.document_reader.pdfplumber.open", lambda _: Pdf()
    )
    source = "report.pdf"
    report = Path(source)
    report.write_bytes(b"not-a-real-pdf")
    try:
        async with tracker.session_span("session-1", "question"):
            result = await DocumentReaderTool(tracker).execute(source=source)
    finally:
        report.unlink()

    assert result.data["title"] == "Appendix A"


@pytest.mark.asyncio
async def test_reader_joins_a_heading_whose_continuation_starts_lower_case(
    monkeypatch, tracker
) -> None:
    """D3 (run 5 follow-up): a heading with no terminal punctuation is
    joined with its continuation whenever that next line starts lower
    case, even when the first line's last word is not a bare function
    word."""

    class Page:
        def extract_text(self):
            return (
                "The Effect of a New Filing Rule on the Decline\n"
                "of the Regional Housing Market\n"
                "Body text follows here."
            )

    class Pdf:
        pages = [Page()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(
        "deep_research.tools.document_reader.pdfplumber.open", lambda _: Pdf()
    )
    source = "report.pdf"
    report = Path(source)
    report.write_bytes(b"not-a-real-pdf")
    try:
        async with tracker.session_span("session-1", "question"):
            result = await DocumentReaderTool(tracker).execute(source=source)
    finally:
        report.unlink()

    assert result.data["title"] == (
        "The Effect of a New Filing Rule on the Decline "
        "of the Regional Housing Market"
    )



@pytest.mark.asyncio
async def test_a_remote_format_no_parser_reads_is_refused_before_any_request(
    tracker,
) -> None:
    """Latency audit O11: a .doc/.docx/.xls/.xlsx URL fails exactly as its
    download would have failed, without the download."""
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, content=b"PK\x03\x04", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async with tracker.session_span("session-1", "question"):
            results = [
                await DocumentReaderTool(tracker, client=client).execute(
                    source=f"https://example.test/report.{suffix}"
                )
                for suffix in ("doc", "docx", "xls", "xlsx")
            ]

    assert requests == []
    for result in results:
        assert result.success is False
        assert result.error is not None
        assert (result.error.type, result.error.message) == (
            "unsupported_document_format",
            "unsupported document format",
        )


def test_every_document_suffix_the_policy_routes_here_is_parsed_or_refused_unread() -> None:
    """The routing table and the reader agree: a suffix the acquisition policy
    sends to ``document_reader`` is one a parser reads or one refused before
    any download."""
    from deep_research.agents.acquisition import _required_reader
    from deep_research.tools.document_reader import (
        _SUFFIX_FORMATS,
        _UNPARSED_SUFFIXES,
    )

    routed = [
        suffix
        for suffix in (
            "pdf", "csv", "json", "txt", "md", "markdown",
            "doc", "docx", "xls", "xlsx", "html", "htm", "xhtml", "png",
        )
        if _required_reader(f"https://example.test/a.{suffix}") == "document_reader"
    ]

    assert routed == [
        "pdf", "csv", "json", "txt", "md", "markdown", "doc", "docx", "xls", "xlsx",
    ]
    for suffix in routed:
        assert (f".{suffix}" in _SUFFIX_FORMATS) != (f".{suffix}" in _UNPARSED_SUFFIXES)



@pytest.mark.asyncio
async def test_a_remote_read_goes_through_the_runs_pool(tracker) -> None:
    """Latency audit O4: the reader builds its own client over the pool it was
    given, and its request is the one it always sent."""
    seen: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("user-agent"))
        return httpx.Response(200, content=b"remote document", request=request)

    async with tracker.session_span("session-1", "question"):
        result = await DocumentReaderTool(
            tracker, transport=httpx.MockTransport(handler)
        ).execute(source="https://example.test/report.txt")

    assert result.success is True
    assert seen == [f"python-httpx/{httpx.__version__}"]
