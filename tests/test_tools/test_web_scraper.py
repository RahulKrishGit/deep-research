import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest

from deep_research.observability.tracker import SpanHandle
from deep_research.tools.base import ToolResult
from deep_research.tools.web_scraper import WebScraperTool


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class _UnreachableClient:
    """A client that fails the test instead of reaching the network.

    Used where URL validation is supposed to reject the URL before any request
    is made, so a validation regression cannot turn into a live call.
    """

    async def get(self, *_args: object, **_kwargs: object) -> httpx.Response:
        raise AssertionError("an invalid URL must not reach the transport")


# Sentinels standing in for attacker-controlled text. None of them may reach a
# public failure field (message, error type, or details).
_HOSTILE_EXCEPTION_TEXT = "ATTACKER-EXCEPTION-TEXT-<script>alert(1)</script>"
_HOSTILE_RESPONSE_TEXT = "ATTACKER-RESPONSE-TEXT-<script>alert(2)</script>"
_HOSTILE_CONTENT_TYPE = "ATTACKER-CONTENT-TYPE-<script>alert(3)</script>"
_HOSTILE_URL = "https://example.test/private/ATTACKER-URL-SENTINEL?key=secret"
_FAILURE_DETAIL_KEYS = {"attempts", "retries", "status_code", "content_type"}


def _assert_failure_is_bounded(
    result: ToolResult, forbidden: tuple[str, ...]
) -> None:
    """A failed scrape reports only static text and bounded categorical values."""
    assert result.success is False
    assert result.error is not None
    # Public text is project-authored ASCII, so no remote text can hide in it.
    assert result.error.message.isascii()
    details = result.error.details
    assert set(details) <= _FAILURE_DETAIL_KEYS
    attempts = details.get("attempts")
    if attempts is not None:
        assert type(attempts) is int
        assert 1 <= attempts <= 3
    retries = details.get("retries")
    if retries is not None:
        assert type(retries) is int
        assert 0 <= retries <= 2
    status_code = details.get("status_code")
    if status_code is not None:
        assert type(status_code) is int
        assert 100 <= status_code <= 599
    content_type = details.get("content_type")
    if content_type is not None:
        assert type(content_type) is str
        assert content_type == content_type.lower()
        assert content_type.isascii()
        assert 0 < len(content_type) <= 64
    serialized = result.model_dump_json()
    for marker in forbidden:
        assert marker not in serialized


class RecordingToolTracker:
    def __init__(self) -> None:
        self.span = SpanHandle(context=None)  # type: ignore[arg-type]

    @asynccontextmanager
    async def tool_span(self, name: str, inputs: dict[str, object]):
        del name, inputs
        yield self.span


@pytest.mark.asyncio
async def test_scraper_extracts_static_html_and_reports_observable_summary(
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text=(
                "<html><head><title>Example Article</title><style>hidden</style>"
                "</head><body><nav>Navigation</nav><p>First paragraph.</p><script>"
                "ignored</script><p>Second paragraph.</p><noscript>ignored fallback"
                "</noscript></body></html>"
            ),
            request=request,
        )

    async with _client(handler) as client:
        tracker = RecordingToolTracker()
        result = await WebScraperTool(tracker, client=client).execute(
            url="https://example.test/article"
        )

    assert result.data == {
        "url": "https://example.test/article",
        "title": "Example Article",
        "text": "Example Article Navigation First paragraph. Second paragraph.",
        "status_code": 200,
        "content_type": "text/html; charset=utf-8",
    }
    assert result.metadata == {"robots_checked": True, "retry_count": 0}
    assert tracker.span.outputs == {
        "status_code": 200,
        "character_count": 61,
        "robots_checked": True,
        "success": True,
    }


@pytest.mark.asyncio
async def test_scraper_stops_before_page_when_robots_disallows_url(tracker) -> None:
    paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(
            200, text="User-agent: *\nDisallow: /private", request=request
        )

    async with _client(handler) as client:
        tool = WebScraperTool(tracker, client=client)
        async with tracker.session_span("session-1", "question"):
            result = await tool.execute(url="https://example.test/private/article")

    assert result.success is False
    assert result.error is not None
    assert result.error.type == "robots_disallowed"
    assert result.error.message == "robots policy disallows this URL"
    assert result.error.details == {}
    assert paths == ["/robots.txt"]


@pytest.mark.asyncio
async def test_scraper_robots_disallows_without_leaking_the_hostile_url(
    tracker,
) -> None:
    paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(
            200, text="User-agent: *\nDisallow: /private", request=request
        )

    async with _client(handler) as client:
        tool = WebScraperTool(tracker, client=client)
        async with tracker.session_span("session-1", "question"):
            result = await tool.execute(url=_HOSTILE_URL)

    _assert_failure_is_bounded(result, ("ATTACKER-URL-SENTINEL", "example.test"))
    assert result.error is not None
    assert result.error.type == "robots_disallowed"
    assert result.error.details == {}
    assert paths == ["/robots.txt"]


@pytest.mark.asyncio
async def test_scraper_allows_page_when_robots_is_unavailable(tracker) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            text="<title>T</title>",
            request=request,
        )

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
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            text="<title>T</title>",
            request=request,
        )

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
    assert result.error.details == {"attempts": 3, "retries": 2, "status_code": 503}
    assert calls == 3
    assert result.metadata["retry_count"] == 2


@pytest.mark.asyncio
async def test_scraper_retries_page_timeout_without_changing_the_delay(
    tracker,
) -> None:
    calls = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout(_HOSTILE_EXCEPTION_TEXT, request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            text="<title>T</title>",
            request=request,
        )

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async with _client(handler) as client:
        tool = WebScraperTool(tracker, client=client, sleep=sleep)
        async with tracker.session_span("session-1", "question"):
            result = await tool.execute(url="https://example.test/article")

    assert result.success is True
    assert calls == 2
    assert delays == [0.5]
    assert result.metadata["retry_count"] == 1


@pytest.mark.asyncio
async def test_scraper_timeout_failure_classification_is_bounded(tracker) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        calls += 1
        raise httpx.ReadTimeout(_HOSTILE_EXCEPTION_TEXT, request=request)

    async with _client(handler) as client:
        tool = WebScraperTool(tracker, client=client, max_retries=0)
        async with tracker.session_span("session-1", "question"):
            result = await tool.execute(url="https://example.test/article")

    _assert_failure_is_bounded(
        result, (_HOSTILE_EXCEPTION_TEXT, "example.test", "https://")
    )
    assert result.error is not None
    assert result.error.type == "ReadTimeout"
    assert result.error.message == "the page request timed out"
    assert result.error.details == {"attempts": 1, "retries": 0}
    assert calls == 1
    assert result.metadata["retry_count"] == 0


@pytest.mark.asyncio
async def test_scraper_exhausted_retry_classification_is_bounded(tracker) -> None:
    calls = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        calls += 1
        return httpx.Response(
            503,
            headers={"Content-Type": _HOSTILE_CONTENT_TYPE},
            text=_HOSTILE_RESPONSE_TEXT,
            request=request,
        )

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async with _client(handler) as client:
        tool = WebScraperTool(tracker, client=client, sleep=sleep)
        async with tracker.session_span("session-1", "question"):
            result = await tool.execute(url="https://example.test/article")

    _assert_failure_is_bounded(
        result,
        (_HOSTILE_RESPONSE_TEXT, _HOSTILE_CONTENT_TYPE, "example.test", "https://"),
    )
    assert result.error is not None
    assert result.error.type == "HTTPStatusError"
    assert result.error.message == "the page request failed with an HTTP error status"
    assert result.error.details == {"attempts": 3, "retries": 2, "status_code": 503}
    assert calls == 3
    assert delays == [0.5, 1.0]
    assert result.metadata["retry_count"] == 2


@pytest.mark.asyncio
async def test_scraper_http_status_classification_is_bounded(tracker) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        calls += 1
        return httpx.Response(
            404,
            headers={"X-Hostile": _HOSTILE_RESPONSE_TEXT},
            text=_HOSTILE_RESPONSE_TEXT,
            request=request,
        )

    async with _client(handler) as client:
        tool = WebScraperTool(tracker, client=client)
        async with tracker.session_span("session-1", "question"):
            result = await tool.execute(url="https://example.test/article")

    _assert_failure_is_bounded(
        result, (_HOSTILE_RESPONSE_TEXT, "example.test", "https://")
    )
    assert result.error is not None
    assert result.error.type == "HTTPStatusError"
    assert result.error.message == "the page request failed with an HTTP error status"
    assert result.error.details == {"attempts": 1, "retries": 0, "status_code": 404}
    assert calls == 1
    assert result.metadata["retry_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 402, 403, 451])
async def test_an_access_denial_says_to_re_source_not_to_retry(
    tracker, status_code
) -> None:
    """A refusal is actionable, so it must not read as a generic HTTP failure.

    Measured, not assumed: a live run spent seven ``web_scraper`` attempts on
    ``emp.lbl.gov`` pages that all returned 403, while ``document_reader`` read
    that same host's PDFs successfully in 28 of 30 attempts. The generic
    "HTTP error status" sentence told the agent nothing about what to do next,
    so it kept retrying the host. This sentence names the only useful move.
    """
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        calls += 1
        return httpx.Response(
            status_code,
            headers={"X-Hostile": _HOSTILE_RESPONSE_TEXT},
            text=_HOSTILE_RESPONSE_TEXT,
            request=request,
        )

    async with _client(handler) as client:
        tool = WebScraperTool(tracker, client=client)
        async with tracker.session_span("session-1", "question"):
            result = await tool.execute(url="https://example.test/article")

    _assert_failure_is_bounded(
        result, (_HOSTILE_RESPONSE_TEXT, "example.test", "https://")
    )
    assert result.error is not None
    assert result.error.type == "HTTPStatusError"
    assert result.error.message == (
        "the publisher refused automated access to this page; read the same "
        "material from a document or another publisher"
    )
    assert result.error.details == {
        "attempts": 1,
        "retries": 0,
        "status_code": status_code,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [0, 600, 999])
async def test_scraper_out_of_range_status_classification_is_bounded(
    tracker, status_code
) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        calls += 1
        return httpx.Response(
            status_code, text=_HOSTILE_RESPONSE_TEXT, request=request
        )

    async with _client(handler) as client:
        tool = WebScraperTool(tracker, client=client)
        async with tracker.session_span("session-1", "question"):
            result = await tool.execute(url="https://example.test/article")

    _assert_failure_is_bounded(
        result, (_HOSTILE_RESPONSE_TEXT, "example.test", "https://")
    )
    assert result.error is not None
    assert result.error.type == "HTTPStatusError"
    assert result.error.message == "the page request failed"
    assert result.error.details == {"attempts": 1, "retries": 0}
    assert "status_code" not in result.error.details
    assert calls == 1
    assert result.metadata["retry_count"] == 0


@pytest.mark.asyncio
async def test_scraper_rejects_non_html_content(tracker) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"pdf",
            request=request,
        )

    async with _client(handler) as client:
        tool = WebScraperTool(tracker, client=client)
        async with tracker.session_span("session-1", "question"):
            result = await tool.execute(url="https://example.test/article")

    assert result.success is False
    assert result.error is not None
    assert result.error.type == "unsupported_content_type"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        (None, "unknown"),
        ("application/pdf", "application/pdf"),
        ("APPLICATION/PDF; charset=binary", "application/pdf"),
        ("text/plain; ATTACKER-CONTENT-TYPE-SENTINEL", "text/plain"),
        (_HOSTILE_CONTENT_TYPE, "unknown"),
        ("application/pdf<script>alert(4)</script>", "unknown"),
        ("a" * 100 + "/b", "unknown"),
        ("/", "unknown"),
        # Non-ASCII input is never folded or stripped into a media type: a
        # KELVIN SIGN would become ASCII "k", a leading NBSP would be stripped.
        ("\u212a/x", "unknown"),
        ("\u00a0application/pdf", "unknown"),
        ("text/plain; name=h\u00e9llo", "unknown"),
    ],
)
async def test_scraper_unsupported_content_type_details_are_bounded(
    tracker, content_type, expected
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        # A header value arrives as bytes on the wire. httpx refuses a non-ASCII
        # ``str`` value outright, so the value is encoded here the way a server
        # would send it and the tool sees the decoded media type.
        headers = (
            {} if content_type is None else {"Content-Type": content_type.encode()}
        )
        return httpx.Response(
            200, content=b"not html", headers=headers, request=request
        )

    async with _client(handler) as client:
        tool = WebScraperTool(tracker, client=client)
        async with tracker.session_span("session-1", "question"):
            result = await tool.execute(url="https://example.test/article")

    _assert_failure_is_bounded(
        result, (_HOSTILE_CONTENT_TYPE, "ATTACKER-CONTENT-TYPE-SENTINEL")
    )
    assert result.error is not None
    assert result.error.type == "unsupported_content_type"
    assert result.error.message == "response content type is not HTML"
    assert result.error.details == {"content_type": expected}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "forbidden"),
    [
        ("   ", ()),
        ("file:///tmp/article.html", ("file:///tmp/article.html",)),
    ],
)
async def test_scraper_rejects_blank_and_non_http_urls(
    tracker, url, forbidden
) -> None:
    tool = WebScraperTool(tracker, client=_UnreachableClient())
    async with tracker.session_span("session-1", "question"):
        result = await tool.execute(url=url)

    _assert_failure_is_bounded(result, forbidden)
    assert result.error is not None
    assert result.error.type == "ValidationError"
    assert result.error.message == "url must be a non-empty absolute HTTP(S) URL"
    assert result.error.details == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        pytest.param("http://[ATTACKER-URL-SENTINEL]/x", id="bracketed-host"),
        pytest.param("http://exam\u2100ple.test/x", id="nfkc-netloc"),
    ],
)
async def test_scraper_unparseable_url_classification_is_bounded(tracker, url) -> None:
    tool = WebScraperTool(tracker, client=_UnreachableClient())
    async with tracker.session_span("session-1", "question"):
        result = await tool.execute(url=url)

    _assert_failure_is_bounded(
        result,
        (
            "ATTACKER-URL-SENTINEL",
            "exam\u2100ple.test",
            "exam\\u2100ple.test",
        ),
    )
    assert result.error is not None
    assert result.error.type == "ValidationError"
    assert result.error.message == "url must be a non-empty absolute HTTP(S) URL"
    assert result.error.details == {}


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
