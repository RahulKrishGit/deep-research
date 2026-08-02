
import pytest

from deep_research.observability import ToolMetric
from deep_research.tools.write_document import WriteDocumentTool


@pytest.mark.asyncio
async def test_writer_creates_utf8_markdown_report_with_relative_summary(
    tracker, tmp_path
) -> None:
    output_root = tmp_path / "output"
    content = "# Quantum Security\n\nCaf\u00e9 \U0001f512\n"

    async with tracker.session_span("session-1", "question"):
        result = await WriteDocumentTool(tracker, output_root).execute(
            filename="reports/quantum-security", content=content
        )

    assert result.success is True
    assert result.data == {
        "path": "reports/quantum-security.md",
        "bytes_written": 31,
    }
    assert (output_root / "reports" / "quantum-security.md").read_text(
        encoding="utf-8"
    ) == content
    assert result.metadata == {"format": "markdown", "retry_count": 0}
    metric = next(
        item for item in tracker.metrics if isinstance(item, ToolMetric)
    )
    assert metric.success is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filename",
    [
        "/absolute/report.md",
        "C:\\absolute\\report.md",
        "../escape.md",
        "reports/../../escape.md",
        "reports/",
        "",
        "report.pdf",
        "report.txt",
        "report.MD",
    ],
)
async def test_writer_rejects_unsafe_or_non_markdown_paths(
    tracker, tmp_path, filename: str
) -> None:
    output_root = tmp_path / "output"
    outside = tmp_path / "escape.md"
    outside.write_text("original", encoding="utf-8")
    (output_root / "reports").mkdir(parents=True)

    async with tracker.session_span("session-1", "question"):
        result = await WriteDocumentTool(tracker, output_root).execute(
            filename=filename, content="# Report\n"
        )

    assert result.success is False
    assert result.error is not None
    assert result.error.recoverable is False
    assert outside.read_text(encoding="utf-8") == "original"
    assert list(output_root.rglob("*.md")) == []
    metric = next(
        item for item in tracker.metrics if isinstance(item, ToolMetric)
    )
    assert metric.success is False


@pytest.mark.asyncio
async def test_writer_rejects_an_existing_directory_as_a_target(
    tracker, tmp_path
) -> None:
    output_root = tmp_path / "output"
    (output_root / "report.md").mkdir(parents=True)

    async with tracker.session_span("session-1", "question"):
        result = await WriteDocumentTool(tracker, output_root).execute(
            filename="report.md", content="# Report\n"
        )

    assert result.success is False
    assert result.error is not None
    assert result.error.type == "ValidationError"
    assert (output_root / "report.md").is_dir()


@pytest.mark.asyncio
async def test_writer_atomically_replaces_existing_markdown_file(
    tracker, tmp_path
) -> None:
    output_root = tmp_path / "output"
    target = output_root / "report.md"
    target.parent.mkdir()
    target.write_text("incomplete", encoding="utf-8")

    async with tracker.session_span("session-1", "question"):
        result = await WriteDocumentTool(tracker, output_root).execute(
            filename="report.md", content="# Complete Report\n"
        )

    assert result.success is True
    assert result.data == {"path": "report.md", "bytes_written": 18}
    assert target.read_text(encoding="utf-8") == "# Complete Report\n"
    assert not list(output_root.glob("tmp*"))


@pytest.mark.asyncio
async def test_writer_redacts_report_body_from_span_inputs(tmp_path) -> None:
    from contextlib import asynccontextmanager

    from deep_research.observability.tracker import SpanHandle

    class RecordingTracker:
        def __init__(self) -> None:
            self.inputs: dict[str, object] | None = None

        @asynccontextmanager
        async def tool_span(self, name: str, inputs: dict[str, object]):
            assert name == "write_document"
            self.inputs = inputs
            yield SpanHandle(context=None)  # type: ignore[arg-type]

    tracker = RecordingTracker()
    content = "private report body"

    result = await WriteDocumentTool(tracker, tmp_path / "output").execute(
        filename="report", content=content
    )

    assert result.success is True
    assert tracker.inputs == {"filename": "report", "content_chars": 19}
