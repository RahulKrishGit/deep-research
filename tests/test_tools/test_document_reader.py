import json
from pathlib import Path

import httpx
import pytest

from deep_research.tools.document_reader import DocumentReaderTool


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
