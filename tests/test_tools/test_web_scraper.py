import asyncio

import httpx
import pytest

from deep_research.observability import ToolMetric
from deep_research.tools.web_scraper import WebScraperTool


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_scraper_extracts_static_html_and_reports_observable_summary(tracker) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text="""<html><head><title>Example Article</title><style>hidden</style></head>
            <body><nav>Navigation</nav><p>First paragraph.</p><script>ignored</script>
            <p>Second paragraph.</p><noscript>ignored fallback</noscript></body></html>""",
            request=request,
        )

    async with _client(handler) as client:
        tool = WebScraperTool(tracker, client=client)
        async with tracker.session_span("session-1", "question"):
            result = await tool.execute(url="https://example.test/article")

    assert result.data == {
        "url": "https://example.test/article",
        "title": "Example Article",
        "text": "Example Article Navigation First paragraph. Second paragraph.",
        "status_code": 200,
        "content_type": "text/html; charset=utf-8",
    }
    assert result.metadata == {"robots_checked": True, "retry_count": 0}
    metric = next(item for item in tracker.metrics if isinstance(item, ToolMetric))
    assert metric.success is True


@pytest.mark.asyncio
async def test_scraper_stops_before_page_when_robots_disallows_url(tracker) -> None:
    paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, text="User-agent: *\nDisallow: /private", request=request)

    async with _client(handler) as client:
        tool = WebScraperTool(tracker, client=client)
        async with tracker.session_span("session-1", "question"):
            result = await tool.execute(url="https://example.test/private/article")

    assert result.success is False
    assert result.error is not None
    assert result.error.type == "robots_disallowed"
    assert result.error.details == {"url": "https://example.test/private/article"}
    assert paths == ["/robots.txt"]


@pytest.mark.asyncio
async def test_scraper_allows_page_when_robots_is_unavailable(tracker) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, request=request)
        return httpx.Response(200, headers={"Content-Type": "text/html"}, text="<title>T</title>", request=request)

    async with _client(handler) as client:
        tool = WebScraperTool(tracker, client=client)
        async with tracker.session_span("session-1", "question"):
            result = await tool.execute(url="https://example.test/article")

    assert result.success is True
    assert result.metadata["robots_checked"] is False


@pytest.mark.asyncio
async def test_scraper_retries_rate_limited_page_using_retry_after(tracker) -> None:
    calls = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, request=request)
        return httpx.Response(200, headers={"Content-Type": "text/html"}, text="<title>T</title>", request=request)

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async with _client(handler) as client:
        tool = WebScraperTool(tracker, client=client, sleep=sleep)
        async with tracker.session_span("session-1", "question"):
            result = await tool.execute(url="https://example.test/article")

    assert result.success is True
    assert calls == 2
    assert delays == [1.0]
    assert result.metadata["retry_count"] == 1


@pytest.mark.asyncio
async def test_scraper_exhausts_two_retries_for_server_errors(tracker) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        calls += 1
        return httpx.Response(503, request=request)

    async def sleep(_: float) -> None:
        return None

    async with _client(handler) as client:
        tool = WebScraperTool(tracker, client=client, sleep=sleep)
        async with tracker.session_span("session-1", "question"):
            result = await tool.execute(url="https://example.test/article")

    assert result.success is False
    assert result.error is not None
    assert result.error.type == "HTTPStatusError"
    assert result.error.details == {"attempts": 3, "status_code": 503}
    assert calls == 3
    assert result.metadata["retry_count"] == 2


@pytest.mark.asyncio
async def test_scraper_rejects_non_html_content(tracker) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        return httpx.Response(200, headers={"Content-Type": "application/pdf"}, content=b"pdf", request=request)

    async with _client(handler) as client:
        tool = WebScraperTool(tracker, client=client)
        async with tracker.session_span("session-1", "question"):
            result = await tool.execute(url="https://example.test/article")

    assert result.success is False
    assert result.error is not None
    assert result.error.type == "unsupported_content_type"


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["   ", "file:///tmp/article.html"])
async def test_scraper_rejects_blank_and_non_http_urls(tracker, url) -> None:
    tool = WebScraperTool(tracker)
    async with tracker.session_span("session-1", "question"):
        result = await tool.execute(url=url)

    assert result.success is False
    assert result.error is not None
    assert result.error.type == "ValidationError"


@pytest.mark.asyncio
async def test_scraper_propagates_cancellation(tracker) -> None:
    class CancelledClient:
        async def get(self, *args, **kwargs):
            raise asyncio.CancelledError

    tool = WebScraperTool(tracker, client=CancelledClient())
    async with tracker.session_span("session-1", "question"):
        with pytest.raises(asyncio.CancelledError):
            await tool.execute(url="https://example.test/article")


@pytest.mark.asyncio
async def test_scraper_uses_one_owned_client_for_robots_and_page(
    tracker, monkeypatch
) -> None:
    class OwnedClient:
        async def __aenter__(self) -> "OwnedClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            self.closed = True

        def __init__(self, **_: object) -> None:
            self.closed = False
            clients.append(self)

        async def get(self, url: str, **_: object) -> httpx.Response:
            request = httpx.Request("GET", url)
            if request.url.path == "/robots.txt":
                return httpx.Response(
                    200, text="User-agent: *\nAllow: /", request=request
                )
            return httpx.Response(
                200,
                headers={"Content-Type": "text/html"},
                text="<title>T</title>",
                request=request,
            )

    clients: list[OwnedClient] = []
    monkeypatch.setattr(
        "deep_research.tools.web_scraper.httpx.AsyncClient", OwnedClient
    )
    tool = WebScraperTool(tracker)
    async with tracker.session_span("session-1", "question"):
        result = await tool.execute(url="https://example.test/article")

    assert result.success is True
    assert len(clients) == 1
    assert clients[0].closed is True


def test_scraper_constructor_rejects_invalid_limits(tracker) -> None:
    with pytest.raises(ValueError, match="timeout_s"):
        WebScraperTool(tracker, timeout_s=0)
    with pytest.raises(ValueError, match="max_retries"):
        WebScraperTool(tracker, max_retries=3)
