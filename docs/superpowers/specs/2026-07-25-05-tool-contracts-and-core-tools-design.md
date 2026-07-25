# Tool Contracts And Core Tools Design

Date: 2026-07-25
Status: Draft for review
Parent Spec: 2026-07-25-agentic-deep-research-design.md

## Purpose

Define the tool interface and implement the first tools needed for research: web search, web scraping, document reading, memory read/write adapters, and report writing.

## Scope

This feature adds:

- `BaseTool`
- `ToolResult`
- `WebSearchTool`
- `WebScraperTool`
- `DocumentReaderTool`
- `SaveToMemoryTool`
- `QueryMemoryTool`
- `WriteDocumentTool`

## Non-Goals

- No agent implementations.
- No browser automation beyond practical page extraction.
- No PDF report export in the first tool version.
- No memory backend implementation beyond adapters that can call the memory interface.

## Design

All tools expose:

```python
async def execute(self, **kwargs) -> ToolResult:
    ...
```

`ToolResult` includes:

- `tool_name`
- `success`
- `data`
- `error`
- `latency_ms`
- `metadata`

Core tools:

- `web_search`: Tavily-backed search returning structured search results.
- `web_scrape`: `httpx` plus BeautifulSoup extraction, with Playwright reserved for JavaScript-heavy pages.
- `document_read`: PDF, CSV, JSON, Markdown, and plain text extraction.
- `save_to_memory`: Delegates to long-term memory.
- `query_memory`: Delegates to long-term memory.
- `write_document`: Writes Markdown reports to `output/`.

## Observability

Every tool call uses the observability tracker and emits:

- Tool name.
- Inputs with secrets redacted.
- Output summary.
- Latency.
- Retry count.
- Error type and message.

## Error Handling

Network tools enforce:

- Request timeout.
- Retry count.
- Rate-limit aware backoff where practical.
- Structured failure results instead of raw exceptions for recoverable failures.

Document tools return partial extraction results when possible and record page-level or file-level failures.

## Testing

Tests should cover:

- Tool result schema.
- Tavily search with mocked client.
- Scraper behavior with static HTML.
- Document reader for Markdown and plain text.
- PDF extraction behind a focused fixture if practical.
- Write document output path behavior.
- Observability span calls with mocked tracker.

## Acceptance Criteria

- Each core tool returns a `ToolResult`.
- Tool failures are structured and observable.
- Search, scrape, read, and write tools are independently testable.
- Memory tools can operate against a mocked memory backend.
