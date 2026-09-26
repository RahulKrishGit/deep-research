import asyncio
import html
import json
from contextlib import asynccontextmanager

import httpx
import pytest

from deep_research.agents.evidence import normalized_content_sha256
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
        "requested_url": "https://example.test/article",
        "resolved_url": "https://example.test/article",
        "title": "Example Article",
        "text": "Example Article Navigation First paragraph. Second paragraph.",
        "status_code": 200,
        "content_type": "text/html; charset=utf-8",
        "content_sha256": normalized_content_sha256(
            "Example Article Navigation First paragraph. Second paragraph."
        ),
        "extraction_complete": True,
    }
    assert result.metadata == {"robots_checked": True, "retry_count": 0}
    assert tracker.span.outputs == {
        "status_code": 200,
        "character_count": 61,
        "robots_checked": True,
        "success": True,
    }


@pytest.mark.asyncio
async def test_a_redirect_reports_the_url_the_content_came_from(tracker) -> None:
    """The requested URL alone would misattribute everything read through it."""
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="", request=request)
        if request.url.path == "/start":
            return httpx.Response(
                302,
                headers={"Location": "https://example.test/moved"},
                request=request,
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            text="<html><body><p>Final page body.</p></body></html>",
            request=request,
        )

    async with _client(handler) as client:
        async with tracker.session_span("session-1", "question"):
            result = await WebScraperTool(tracker, client=client).execute(
                url="https://example.test/start"
            )

    assert result.success is True
    assert result.data["url"] == "https://example.test/start"
    assert result.data["requested_url"] == "https://example.test/start"
    assert result.data["resolved_url"] == "https://example.test/moved"
    assert result.data["text"] == "Final page body."


@pytest.mark.asyncio
async def test_an_empty_page_is_not_evidence(tracker) -> None:
    """A 200 response with no readable text must not become a read."""
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="", request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            text="<html><head><title></title></head><body>   </body></html>",
            request=request,
        )

    async with _client(handler) as client:
        async with tracker.session_span("session-1", "question"):
            result = await WebScraperTool(tracker, client=client).execute(
                url="https://example.test/challenge"
            )

    assert result.success is False
    assert result.error is not None
    assert result.error.type == "empty_page_content"
    assert "text" in result.error.message
    assert "challenge" not in result.model_dump_json()


# A client-rendered page as it is served: markup in the hundreds of kilobytes,
# navigation words as the visible text, and the body only in the data the
# browser renders it from. Each prose string below carries well over the
# minimum length and space count, and each is about a neutral fixture subject.
_SCRIPT_BUNDLE = "var module = {};" * 4000

# The words the static read sees: the page's own navigation labels.
_SHELL_NAV = "Charts Methods About Contact"

_DESCRIPTION_PROSE = (
    "The lightest load-bearing bracket in the survey carried more than every "
    "laminated alternative, the Example Institute's own testing found."
)
_SOCIAL_PROSE = (
    "A laminated bracket failed at its joint long before the solid one did, "
    "so the solid bracket wins wherever a load repeats."
)
_DATA_PROSE = (
    "Folding the sheet instead of welding it keeps the bracket's stiffness "
    "and removes the joint that cracks first under repeated loading."
)
_ARTICLE_PROSE = (
    "Nine hundred loading cycles later every folded bracket was still true "
    "to the shape it was stamped in, the Example Institute reported."
)
_LD_PROSE = (
    "A field trial of folded brackets ran nine hundred loading cycles and "
    "recorded the deflection of each bracket after every one of them."
)
# Long and spaced like prose, but it is a string inside a plain script: code,
# not page data, and never the document's words.
_SCRIPT_PROSE = (
    "This alert text is long enough and spaced enough to read like prose but "
    "it is only a string inside a script element."
)
# Too short to be prose, though it is spaced like a sentence.
_SHORT_PROSE = "Loading the chart data now please"


def _client_rendered_page(*, head: str = "", body: str = "") -> str:
    """The served markup of a page whose own scripts render its body."""
    return (
        "<html><head><title>Example Charts</title>"
        f"{head}<script>{_SCRIPT_BUNDLE}"
        f'var notice = "{_SCRIPT_PROSE}";</script>'
        "</head><body>"
        "<nav><a href='/charts'>Charts</a><a href='/methods'>Methods</a>"
        "<a href='/about'>About</a><a href='/contact'>Contact</a></nav>"
        f"{body}</body></html>"
    )


def _props_attribute(payload: object) -> str:
    """A ``data-props`` attribute carrying ``payload`` as a server escapes it."""
    return f'data-props="{html.escape(json.dumps(payload))}"'


async def _read_served_page(
    tracker, page: str, *, url: str = "https://example.test/charts"
) -> ToolResult:
    """Read ``page`` as the one HTML response of a host that allows every path."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text=page,
            request=request,
        )

    async with _client(handler) as client:
        async with tracker.session_span("session-1", "question"):
            return await WebScraperTool(tracker, client=client).execute(url=url)


@pytest.mark.asyncio
async def test_a_client_rendered_page_recovers_its_body_from_its_data(
    tracker,
) -> None:
    """Chrome is not the page: its body sits in the data the browser renders.

    A client-rendered page serves a large script bundle, its navigation words
    as visible text, and its document as JSON in element attributes and JSON
    scripts. Reading only the visible strings recorded chunks of menu as a
    complete read of a real page; the page's own prose is in the response, and
    a read must return it.
    """
    props = {"props": {"lead": f'<p class="lead">{_DATA_PROSE}</p>'}}
    repeated = {"meta": {"description": _DESCRIPTION_PROSE}}
    serialized = json.dumps(
        {"caption": "a caption long enough and spaced enough to pass for prose"}
    )
    not_prose = {"label": _SHORT_PROSE, "digest": "x" * 80, "blob": serialized}
    article = json.dumps({"article": {"verdict": _ARTICLE_PROSE}})
    linked = json.dumps({"@type": "Article", "description": _LD_PROSE})

    page = _client_rendered_page(
        head=(
            f'<meta name="description" content="{_DESCRIPTION_PROSE}">'
            f'<meta property="og:description" content="{_SOCIAL_PROSE}">'
        ),
        body=(
            f"<div {_props_attribute(props)}></div>"
            f"<div {_props_attribute(not_prose)}></div>"
            f'<script type="application/json">{article}</script>'
            f'<script type="application/ld+json">{linked}</script>'
            f"<div {_props_attribute(repeated)}></div>"
        ),
    )
    result = await _read_served_page(tracker, page)

    assert result.success is True
    text = result.data["text"]
    # The visible navigation is kept; the recovered body is added to it.
    assert _SHELL_NAV in text
    for prose in (
        _DESCRIPTION_PROSE,
        _SOCIAL_PROSE,
        _DATA_PROSE,
        _ARTICLE_PROSE,
        _LD_PROSE,
    ):
        assert prose in text
    # Tags and attribute JSON are not carried into the text.
    assert "<p" not in text
    assert '"lead"' not in text
    # One string held by two payloads is contributed once.
    assert text.count(_DESCRIPTION_PROSE) == 1
    # A short string, a space-poor digest and a serialized JSON blob are not
    # prose, and a string inside a plain script is code, not data.
    assert _SHORT_PROSE not in text
    assert "x" * 80 not in text
    assert '{"caption"' not in text
    assert _SCRIPT_PROSE not in text
    assert result.data["extraction_complete"] is True


@pytest.mark.asyncio
async def test_a_client_rendered_page_with_no_recoverable_body_is_a_failed_read(
    tracker,
) -> None:
    """Navigation is not a body: a menu must never stand in for a document.

    The visible text is the site's chrome and the served data holds no prose,
    so there is no document to read. Returning the chrome as a complete read
    is how a menu becomes evidence and how a page that was read is reported as
    though its subject had not been found.
    """
    page = _client_rendered_page(
        head='<meta charset="utf-8"><style>body { margin: 0; }</style>',
        body="<div id='app'></div>",
    )
    result = await _read_served_page(tracker, page)

    _assert_failure_is_bounded(result, ("example.test", "https://", _SHELL_NAV))
    assert result.success is False
    assert result.error is not None
    assert result.error.type == "client_rendered_page"
    assert result.error.message == (
        "the page served no readable body; read the same material from "
        "another source"
    )
    assert result.error.details == {}


@pytest.mark.asyncio
async def test_a_page_with_its_own_visible_text_is_read_unchanged(tracker) -> None:
    """Recovery repairs a shell; it never adds to a page that has a body.

    The fixture carries the same large markup and the same data as the shell
    page, with one difference: its body is served as visible paragraphs. That
    text is the whole read — the description, the attribute JSON and the JSON
    scripts must not be appended to it.
    """
    paragraph = (
        "The Example Institute publishes its method beside every result it "
        "reports, so any reader can repeat the comparison from the published "
        "data alone."
    )
    paragraphs = f"<p>{paragraph}</p>" * 16
    props = {"props": {"lead": _DATA_PROSE}}
    article = json.dumps({"verdict": _ARTICLE_PROSE})
    page = (
        "<html><head><title>Example Charts</title>"
        f'<meta name="description" content="{_DESCRIPTION_PROSE}">'
        f"<script>{_SCRIPT_BUNDLE}</script>"
        f"</head><body>{paragraphs}"
        f"<div {_props_attribute(props)}></div>"
        f'<script type="application/json">{article}</script>'
        "</body></html>"
    )
    result = await _read_served_page(tracker, page)

    assert result.success is True
    assert result.data["text"] == (
        "Example Charts " + " ".join([paragraph] * 16)
    )
    assert _DESCRIPTION_PROSE not in result.data["text"]
    assert _DATA_PROSE not in result.data["text"]
    assert _ARTICLE_PROSE not in result.data["text"]
    assert result.data["extraction_complete"] is True


@pytest.mark.asyncio
async def test_a_short_page_in_small_markup_is_read_unchanged(tracker) -> None:
    """A short notice is not a shell: the gate also needs large markup.

    Nothing is recoverable here because there is nothing to recover: the
    served HTML is most of what the page carries. Refusing it a read would
    lose a short authoritative page.
    """
    page = (
        "<html><head><title>Harbour Notice</title></head><body>"
        "<p>The harbour ferry moves to its winter timetable on the first "
        "Sunday of next month.</p></body></html>"
    )
    result = await _read_served_page(
        tracker, page, url="https://example.test/notice"
    )

    assert result.success is True
    assert result.data["text"] == (
        "Harbour Notice The harbour ferry moves to its winter timetable on "
        "the first Sunday of next month."
    )
    assert result.data["extraction_complete"] is True


@pytest.mark.asyncio
async def test_a_short_real_page_in_large_markup_keeps_its_body(tracker) -> None:
    """A page whose few visible words are a paragraph is not a shell.

    Large markup alone does not make a page client-rendered. A page can serve
    a framework bundle and still render a real body of a few hundred
    characters, and that body can be a single paragraph: refusing it a read
    would lose a short authoritative page, the very page the shell gate
    exists to protect.
    """
    paragraph = (
        "The Example Institute measured every bracket in the survey and "
        "published the loading limits beside each one."
    )
    page = (
        "<html><head><title>Example Charts</title>"
        f"<script>{_SCRIPT_BUNDLE}</script>"
        "</head><body><nav><a href='/charts'>Charts</a>"
        f"<a href='/methods'>Methods</a></nav><p>{paragraph}</p></body></html>"
    )
    result = await _read_served_page(tracker, page)

    assert result.success is True
    assert result.data["text"] == f"Example Charts Charts Methods {paragraph}"
    assert result.data["extraction_complete"] is True


@pytest.mark.asyncio
async def test_a_short_data_table_in_large_markup_keeps_its_table(tracker) -> None:
    """A page whose body is a table is not chrome, whatever its labels say.

    A data page carries its body as short cells — names, numbers, units —
    that no prose test accepts, and its markup can be large because of a
    script bundle. The table is the page, so the read keeps it.
    """
    rows = "".join(
        f"<tr><td>Gauge {index}</td><td>{index * 7}</td><td>cubic metres</td></tr>"
        for index in range(1, 31)
    )
    page = (
        "<html><head><title>Example Gauges</title>"
        f"<script>{_SCRIPT_BUNDLE}</script>"
        "</head><body><table><thead><tr><th>Gauge</th><th>Flow</th>"
        f"<th>Unit</th></tr></thead><tbody>{rows}</tbody></table></body></html>"
    )
    result = await _read_served_page(
        tracker, page, url="https://example.test/gauges"
    )

    assert result.success is True
    text = result.data["text"]
    assert "Gauge 1 7 cubic metres" in text
    assert "Gauge 30 210 cubic metres" in text
    assert text.count("cubic metres") == 30
    assert result.data["extraction_complete"] is True


# Chrome carries prose of its own: a consent banner, a masthead tagline, a
# legal footer. Long enough to pass the prose test, and never the page.
_CHROME_PROSE = (
    "We use cookies and similar technologies to run this site and to measure "
    "how its pages are used."
)
_TAGLINE_PROSE = (
    "Example Charts is the home of the survey that every reader can repeat."
)


@pytest.mark.asyncio
async def test_a_shell_page_whose_only_prose_is_its_chrome_is_a_failed_read(
    tracker,
) -> None:
    """A tagline or a legal footer is not the page's body.

    Chrome carries prose, and reading it as the body is how a page whose
    content the browser renders was recorded as a complete read: a shell whose
    visible text is a masthead sentence and a cookie notice holds no document.
    """
    page = (
        "<html><head><title>Example Charts</title>"
        f"<script>{_SCRIPT_BUNDLE}</script></head><body>"
        f"<header><p>{_TAGLINE_PROSE}</p></header>"
        "<nav><a href='/charts'>Charts</a><a href='/methods'>Methods</a></nav>"
        f"<footer><p>{_CHROME_PROSE}</p></footer>"
        "</body></html>"
    )
    result = await _read_served_page(tracker, page)

    _assert_failure_is_bounded(result, ("example.test", "https://", _SHELL_NAV))
    assert result.success is False
    assert result.error is not None
    assert result.error.type == "client_rendered_page"


@pytest.mark.asyncio
async def test_a_shell_page_whose_only_prose_is_a_dialog_is_a_failed_read(
    tracker,
) -> None:
    """The landmark roles name the same chrome on markup that uses divs.

    A consent dialog, a banner and an alert are chrome wherever a page puts
    them, and their sentences are still not the page's document.
    """
    page = (
        "<html><head><title>Example Charts</title>"
        f"<script>{_SCRIPT_BUNDLE}</script></head><body>"
        f"<div role='dialog'><p>{_CHROME_PROSE}</p></div>"
        "<div id='app'></div>"
        "</body></html>"
    )
    result = await _read_served_page(tracker, page)

    assert result.success is False
    assert result.error is not None
    assert result.error.type == "client_rendered_page"


@pytest.mark.asyncio
async def test_a_single_row_layout_table_is_not_a_body(tracker) -> None:
    """One row is a layout wrapper, not a page's own figures.

    Any ``td`` used to make the text a body, so a shell whose markup wraps its
    chrome in a one-row table was read as complete: a layout says nothing
    about where the page's content is.
    """
    page = (
        "<html><head><title>Example Charts</title>"
        f"<script>{_SCRIPT_BUNDLE}</script></head><body>"
        "<nav><a href='/charts'>Charts</a><a href='/methods'>Methods</a></nav>"
        f"<table><tr><td>{_SHORT_PROSE}</td><td>Loading</td></tr></table>"
        "</body></html>"
    )
    result = await _read_served_page(tracker, page)

    assert result.success is False
    assert result.error is not None
    assert result.error.type == "client_rendered_page"


@pytest.mark.asyncio
async def test_a_two_row_table_is_a_body(tracker) -> None:
    """Two rows are the page's own table, and its read keeps them."""
    page = (
        "<html><head><title>Example Gauges</title>"
        f"<script>{_SCRIPT_BUNDLE}</script></head><body>"
        "<nav><a href='/charts'>Charts</a><a href='/methods'>Methods</a></nav>"
        "<table><tr><td>Gauge 1</td><td>7</td></tr>"
        "<tr><td>Gauge 2</td><td>14</td></tr></table>"
        "</body></html>"
    )
    result = await _read_served_page(
        tracker, page, url="https://example.test/gauges"
    )

    assert result.success is True
    assert "Gauge 1 7" in result.data["text"]
    assert "Gauge 2 14" in result.data["text"]


@pytest.mark.asyncio
async def test_a_client_rendered_page_with_no_visible_text_reads_its_data(
    tracker,
) -> None:
    """A page that renders every word from data is a page with a body.

    Its visible text is empty, which the old read reported as no readable
    text at all; the words it renders from are in the response it served.
    """
    verdict = json.dumps({"article": {"verdict": _ARTICLE_PROSE}})
    page = (
        "<html><head><title>Example Charts</title>"
        f"<script>{_SCRIPT_BUNDLE}</script>"
        f'<script type="application/json">{verdict}</script>'
        "</head><body><div id='app'></div></body></html>"
    )
    result = await _read_served_page(tracker, page)

    assert result.success is True
    assert result.data["text"] == f"Example Charts {_ARTICLE_PROSE}"
    assert result.data["extraction_complete"] is True


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


# ---------------------------------------------------------------------------
# D14: the page's own dates, captured once at scrape time, metadata only.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scraper_captures_published_time_from_open_graph_meta(
    tracker,
) -> None:
    """A page's own ``article:published_time`` meta names its publish date."""
    page = (
        "<html><head><title>Grid Storage Outlook</title>"
        '<meta property="article:published_time" content="2026-09-17T10:00:00Z">'
        "</head><body><p>Battery storage capacity grew across every region.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["page_published"] == "2026-09-17"
    assert "page_updated" not in result.data


@pytest.mark.asyncio
async def test_scraper_captures_modified_time_separately_from_published_time(
    tracker,
) -> None:
    """A page's last-edit date is its own field, never a published-date stand-in."""
    page = (
        "<html><head><title>Grid Storage Outlook</title>"
        '<meta property="article:modified_time" content="2026-08-01">'
        "</head><body><p>Battery storage capacity grew across every region.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert "page_published" not in result.data
    assert result.data["page_updated"] == "2026-08-01"


@pytest.mark.asyncio
async def test_scraper_captures_og_updated_time(tracker) -> None:
    """``og:updated_time`` is read the same way as ``article:modified_time``."""
    page = (
        "<html><head><title>Grid Storage Outlook</title>"
        '<meta property="og:updated_time" content="2026-08-01">'
        "</head><body><p>Battery storage capacity grew across every region.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["page_updated"] == "2026-08-01"


@pytest.mark.asyncio
async def test_scraper_captures_microdata_date_published(tracker) -> None:
    """A microdata ``itemprop="datePublished"`` scoped to the article itself
    names the page's own date (RevDatesR3 round 2: an itemprop with no
    ``itemscope`` ancestor names nothing)."""
    page = (
        "<html><head><title>Grid Storage Outlook</title></head>"
        '<body><article itemscope itemtype="https://schema.org/NewsArticle">'
        '<time itemprop="datePublished" datetime="2026-09-17">'
        "Sep 17, 2026</time>"
        "<p>Battery storage capacity grew across every region.</p>"
        "</article></body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["page_published"] == "2026-09-17"


@pytest.mark.asyncio
async def test_scraper_captures_a_citation_meta_date(tracker) -> None:
    """A ``citation_publication_date`` meta names the page's own date."""
    page = (
        "<html><head><title>Grid Storage Outlook</title>"
        '<meta name="citation_publication_date" content="2026-09-17">'
        "</head><body><p>Battery storage capacity grew across every region.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["page_published"] == "2026-09-17"


@pytest.mark.asyncio
async def test_scraper_captures_a_dublin_core_modified_meta_date(tracker) -> None:
    """``dcterms.modified`` is a modification date, never a publish date."""
    page = (
        "<html><head><title>Grid Storage Outlook</title>"
        '<meta name="dcterms.modified" content="2026-08-01">'
        "</head><body><p>Battery storage capacity grew across every region.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert "page_published" not in result.data
    assert result.data["page_updated"] == "2026-08-01"


@pytest.mark.asyncio
async def test_scraper_captures_date_published_only_from_article_shaped_json_ld_nodes(
    tracker,
) -> None:
    """RevDatesR3 P1: a site-wide ``WebSite`` node never outranks an article node."""
    ld_json = json.dumps(
        {
            "@graph": [
                {"@type": "WebSite", "dateModified": "2020-01-01"},
                {"@type": "NewsArticle", "datePublished": "2026-09-17"},
            ]
        }
    )
    page = (
        "<html><head><title>Grid Storage Outlook</title>"
        f'<script type="application/ld+json">{ld_json}</script>'
        "</head><body><p>Battery storage capacity grew across every region.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["page_published"] == "2026-09-17"
    assert "page_updated" not in result.data


@pytest.mark.asyncio
async def test_scraper_reads_a_json_ld_date_given_as_a_single_item_array(
    tracker,
) -> None:
    """A schema.org date property may be published as an array of one value
    rather than a bare string; a page whose JSON-LD wraps ``dateModified``
    this way must not lose its own edit date (D3, run 5)."""
    ld_json = json.dumps(
        {
            "@type": "WebPage",
            "datePublished": "2009-01-23",
            "dateModified": ["2026-07-28"],
        }
    )
    page = (
        "<html><head><title>Grid Storage Outlook</title>"
        f'<script type="application/ld+json">{ld_json}</script>'
        "</head><body><p>Battery storage capacity grew across every region.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["page_published"] == "2009-01-23"
    assert result.data["page_updated"] == "2026-07-28"


@pytest.mark.asyncio
async def test_scraper_never_reads_a_comment_nodes_json_ld_date(tracker) -> None:
    """RevDatesR3 P1: a ``Comment`` node's timestamp is never the page's date."""
    ld_json = json.dumps(
        {
            "@graph": [
                {"@type": "Comment", "datePublished": "2026-10-01"},
                {"@type": "Article", "datePublished": "2026-09-17"},
            ]
        }
    )
    page = (
        "<html><head><title>Grid Storage Outlook</title>"
        f'<script type="application/ld+json">{ld_json}</script>'
        "</head><body><p>Battery storage capacity grew across every region.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["page_published"] == "2026-09-17"


@pytest.mark.asyncio
async def test_scraper_reads_each_json_ld_date_from_its_own_article_shaped_node(
    tracker,
) -> None:
    """RevDatesR3 P1: an Article's own modified date and a WebPage's published
    date are each kept, never crossed with the other node's field."""
    ld_json = json.dumps(
        {
            "@graph": [
                {"@type": "Article", "dateModified": "2026-09-20"},
                {"@type": "WebPage", "datePublished": "2026-09-01"},
            ]
        }
    )
    page = (
        "<html><head><title>Grid Storage Outlook</title>"
        f'<script type="application/ld+json">{ld_json}</script>'
        "</head><body><p>Battery storage capacity grew across every region.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["page_published"] == "2026-09-01"
    assert result.data["page_updated"] == "2026-09-20"


@pytest.mark.asyncio
async def test_scraper_never_captures_a_byline_date_from_prose(tracker) -> None:
    """RevDatesR3 P1: the prose byline fallback is removed -- structured
    metadata only. A page with no meta/microdata/JSON-LD gets no date, even
    when its opening text carries what looks like a byline."""
    page = (
        "<html><head><title>Grid Storage Outlook</title></head><body>"
        "<p>By Jane Doe. Published September 17, 2026.</p>"
        "<p>Battery storage capacity grew across every region.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert "page_published" not in result.data
    assert "page_updated" not in result.data


@pytest.mark.asyncio
async def test_scraper_omits_both_date_keys_when_the_page_states_neither(
    tracker,
) -> None:
    """Never invent a date: a page that carries none gets no keys at all."""
    page = (
        "<html><head><title>Grid Storage Outlook</title></head><body>"
        "<p>Battery storage capacity grew across every region this decade.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert "page_published" not in result.data
    assert "page_updated" not in result.data


@pytest.mark.asyncio
async def test_scraper_keeps_only_the_precision_the_page_states(tracker) -> None:
    """A month with no day is recorded at that precision, never a guessed day."""
    page = (
        "<html><head><title>Grid Storage Outlook</title>"
        '<meta property="article:published_time" content="2026-09">'
        "</head><body><p>Battery storage capacity grew across every region.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["page_published"] == "2026-09"


@pytest.mark.asyncio
async def test_scraper_refuses_an_impossible_calendar_date(tracker) -> None:
    """Never invent a date: an impossible day is not salvaged into a month."""
    page = (
        "<html><head><title>Grid Storage Outlook</title>"
        '<meta property="article:published_time" content="2026-02-30">'
        "</head><body><p>Battery storage capacity grew across every region.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert "page_published" not in result.data


@pytest.mark.asyncio
async def test_scraper_refuses_epoch_seconds_content(tracker) -> None:
    """RevDatesR3 P2: an unanchored ISO prefix let epoch seconds through as a
    fabricated year; the digits after a valid date's own end must be end of
    string, a time separator, or a timezone designator."""
    page = (
        "<html><head><title>Grid Storage Outlook</title>"
        '<meta property="article:published_time" content="1758067200">'
        "</head><body><p>Battery storage capacity grew across every region.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert "page_published" not in result.data


# ---------------------------------------------------------------------------
# RevDatesR3 round 2: JSON-LD outranks microdata, microdata is scoped to the
# page's own content, and the generic citation names are gone.
# ---------------------------------------------------------------------------

_ARTICLE_JSON_LD = json.dumps(
    {
        "@type": "NewsArticle",
        "datePublished": "2026-09-17",
        "dateModified": "2026-09-20",
    }
)


@pytest.mark.asyncio
async def test_a_sidebar_cards_microdata_date_never_beats_the_articles_json_ld(
    tracker,
) -> None:
    """RevDatesR3 P1: a related-post card before the article is real
    ``BlogPosting`` microdata -- itemscope-valid on its own -- but the
    article's own JSON-LD is read first and settles both fields before the
    card is ever consulted."""
    page = (
        "<html><head><title>Grid Storage Outlook</title>"
        f'<script type="application/ld+json">{_ARTICLE_JSON_LD}</script>'
        "</head><body>"
        '<aside itemscope itemtype="https://schema.org/BlogPosting">'
        '<time itemprop="datePublished" datetime="2024-01-05">Jan 5, 2024</time>'
        "</aside>"
        "<p>Battery storage capacity grew across every region.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["page_published"] == "2026-09-17"
    assert result.data["page_updated"] == "2026-09-20"


@pytest.mark.asyncio
async def test_a_sidebar_cards_microdata_date_is_excluded_with_no_json_ld_at_all(
    tracker,
) -> None:
    """RevDatesR3 P2 (round 3): itemtype scoping alone cannot tell a related-
    post card apart from the article, since a card is itself validly typed
    ``BlogPosting``. With no JSON-LD to mask it, the card's own date must
    still never win: it sits in an ``<aside>``, which is never the article
    regardless of its itemtype."""
    page = (
        "<html><head><title>Grid Storage Outlook</title></head><body>"
        '<aside itemscope itemtype="https://schema.org/BlogPosting">'
        '<time itemprop="datePublished" datetime="2024-01-05">Jan 5, 2024</time>'
        "</aside>"
        '<article itemscope itemtype="https://schema.org/Article">'
        '<time itemprop="datePublished" datetime="2026-09-17">Sep 17, 2026</time>'
        "<p>Battery storage capacity grew across every region.</p>"
        "</article>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["page_published"] == "2026-09-17"


@pytest.mark.asyncio
async def test_two_disagreeing_article_scoped_dates_give_no_date_at_all(
    tracker,
) -> None:
    """A wrong date is worse than none: two article-shaped items outside any
    chrome region that disagree on the date are not resolved by picking
    either one -- the page's own date is not established, so neither is
    recorded."""
    page = (
        "<html><head><title>Grid Storage Outlook</title></head><body>"
        '<article itemscope itemtype="https://schema.org/Article">'
        '<time itemprop="datePublished" datetime="2026-09-17">Sep 17, 2026</time>'
        "<p>Battery storage capacity grew across every region.</p>"
        "</article>"
        '<article itemscope itemtype="https://schema.org/Article">'
        '<time itemprop="datePublished" datetime="2025-01-01">Jan 1, 2025</time>'
        "<p>A second, unrelated article snippet on the same page.</p>"
        "</article>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert "page_published" not in result.data

@pytest.mark.asyncio
async def test_a_comments_microdata_date_never_beats_the_articles_json_ld(
    tracker,
) -> None:
    """RevDatesR3 P1: a comment's own timestamp after the article is never
    read as the page's date, whether or not JSON-LD is present."""
    page = (
        "<html><head><title>Grid Storage Outlook</title>"
        f'<script type="application/ld+json">{_ARTICLE_JSON_LD}</script>'
        "</head><body>"
        "<p>Battery storage capacity grew across every region.</p>"
        '<div itemscope itemtype="https://schema.org/Comment">'
        '<time itemprop="datePublished" datetime="2026-10-02">Oct 2, 2026</time>'
        "</div>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["page_published"] == "2026-09-17"
    assert result.data["page_updated"] == "2026-09-20"


@pytest.mark.asyncio
async def test_a_comment_scoped_microdata_date_is_never_captured_alone(
    tracker,
) -> None:
    """The scoping guard itself, isolated: with no JSON-LD to mask it, a
    ``Comment``-scoped ``itemprop`` still yields no date at all."""
    page = (
        "<html><head><title>Grid Storage Outlook</title></head><body>"
        "<p>Battery storage capacity grew across every region.</p>"
        '<div itemscope itemtype="https://schema.org/Comment">'
        '<time itemprop="datePublished" datetime="2026-10-02">Oct 2, 2026</time>'
        "</div>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert "page_published" not in result.data


@pytest.mark.asyncio
async def test_an_unscoped_microdata_date_is_never_captured(tracker) -> None:
    """An ``itemprop`` with no ``itemscope`` ancestor at all names nothing:
    it is not attached to any item, article-shaped or otherwise."""
    page = (
        "<html><head><title>Grid Storage Outlook</title></head><body>"
        '<time itemprop="datePublished" datetime="2026-10-02">Oct 2, 2026</time>'
        "<p>Battery storage capacity grew across every region.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert "page_published" not in result.data


@pytest.mark.asyncio
async def test_a_date_meta_build_stamp_never_beats_the_articles_json_ld(
    tracker,
) -> None:
    """RevDatesR3 P2: a generic ``name="date"`` meta -- often a template's
    build stamp -- never outranks the article's own JSON-LD."""
    page = (
        "<html><head><title>Grid Storage Outlook</title>"
        '<meta name="date" content="2026-09-25">'
        f'<script type="application/ld+json">{_ARTICLE_JSON_LD}</script>'
        "</head><body><p>Battery storage capacity grew across every region.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["page_published"] == "2026-09-17"
    assert result.data["page_updated"] == "2026-09-20"


@pytest.mark.asyncio
async def test_a_dcterms_date_meta_never_beats_the_articles_json_ld(tracker) -> None:
    """RevDatesR3 P2: Dublin Core's generic ``dcterms.date`` -- often a
    last-modified date, not a publication date -- never outranks JSON-LD."""
    page = (
        "<html><head><title>Grid Storage Outlook</title>"
        '<meta name="dcterms.date" content="2019-01-01">'
        f'<script type="application/ld+json">{_ARTICLE_JSON_LD}</script>'
        "</head><body><p>Battery storage capacity grew across every region.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["page_published"] == "2026-09-17"
    assert result.data["page_updated"] == "2026-09-20"


@pytest.mark.asyncio
async def test_the_generic_date_and_dcterms_date_names_are_never_read(
    tracker,
) -> None:
    """RevDatesR3 P2: ``date``, ``dc.date`` and ``dcterms.date`` are removed
    entirely, not merely reordered -- with no JSON-LD to mask them, they
    yield nothing at all."""
    page = (
        "<html><head><title>Grid Storage Outlook</title>"
        '<meta name="date" content="2026-09-25">'
        '<meta name="dc.date" content="2026-09-26">'
        '<meta name="dcterms.date" content="2026-09-27">'
        "</head><body><p>Battery storage capacity grew across every region.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert "page_published" not in result.data


@pytest.mark.asyncio
async def test_two_disagreeing_article_json_ld_nodes_give_no_date_at_all(
    tracker,
) -> None:
    """WholeBranchReview P3-3: JSON-LD requires the same agreement microdata
    already does -- two article-shaped nodes with different ``datePublished``
    values are not resolved by letting the first one win; the page's own
    date is not established, so neither is recorded."""
    ld_json = json.dumps(
        [
            {"@type": "NewsArticle", "datePublished": "2026-09-17"},
            {"@type": "Article", "datePublished": "2025-01-01"},
        ]
    )
    page = (
        "<html><head><title>Grid Storage Outlook</title>"
        f'<script type="application/ld+json">{ld_json}</script>'
        "</head><body><p>Battery storage capacity grew across every region.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert "page_published" not in result.data


# ---------------------------------------------------------------------------
# D3: title precedence (RevW5Titles P1-a, P2). The raw <title> tag is kept
# whenever it is not empty and does not name only the site; only then does
# og:title, twitter:title or a non-banner h1 stand in for it, skipping any
# of those equal to og:site_name while a later, differing one remains.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scraper_prefers_og_title_over_a_bare_site_name_title(tracker) -> None:
    """A platform's bare site name in ``<title>`` never outranks ``og:title``."""
    page = (
        "<html><head><title>Example Register</title>"
        '<meta property="og:site_name" content="Example Register">'
        '<meta property="og:title" content="New filing rule takes effect in 2026">'
        "</head><body><p>The filing rule changes take effect next year.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["title"] == "New filing rule takes effect in 2026"


@pytest.mark.asyncio
async def test_scraper_uses_the_title_tag_when_no_other_candidate_exists(
    tracker,
) -> None:
    """With no og:title, twitter:title or h1, the raw ``<title>`` still wins."""
    page = (
        "<html><head><title>Example Institute Briefing</title>"
        "</head><body><p>A short briefing on the survey results.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["title"] == "Example Institute Briefing"


@pytest.mark.asyncio
async def test_scraper_keeps_the_raw_headline_publisher_title_over_og_title(
    tracker,
) -> None:
    """RevW5Titles P1-a: a 'Headline - Publisher' ``<title>`` is kept over
    ``og:title``, since only the raw title carries the publisher segment the
    issuer-evidence and page-owner checks read."""
    page = (
        "<html><head>"
        "<title>Battery storage capacity grew in 2024 - "
        "U.S. Energy Information Administration (EIA)</title>"
        '<meta property="og:title" content="Battery storage capacity grew in 2024">'
        "</head><body><p>Battery storage capacity grew across every region.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["title"] == (
        "Battery storage capacity grew in 2024 - "
        "U.S. Energy Information Administration (EIA)"
    )


@pytest.mark.asyncio
async def test_scraper_prefers_h1_when_og_title_only_names_the_site(tracker) -> None:
    """An ``og:title`` equal to the site's own name never beats a real ``h1``
    headline (RevW5Titles P3#1)."""
    page = (
        "<html><head><title>Example Register</title>"
        '<meta property="og:site_name" content="Example Register">'
        '<meta property="og:title" content="Example Register">'
        "</head><body><h1>Model A outperforms Model B in the trial</h1>"
        "<p>Further detail about the trial follows in the body text.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["title"] == "Model A outperforms Model B in the trial"


@pytest.mark.asyncio
async def test_scraper_skips_a_banner_h1_when_choosing_the_fallback_heading(
    tracker,
) -> None:
    """RevW5Titles P2: a theme's site-title banner heading is never read as
    the page's own headline; the first non-banner ``h1`` stands in when the
    title is just the site's own name."""
    page = (
        "<html><head><title>Example Register</title>"
        '<meta property="og:site_name" content="Example Register">'
        "</head><body>"
        '<header><h1 class="site-title">Example Register</h1></header>'
        "<h1>Real headline about filings</h1>"
        "<p>Further detail follows in the body text.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["title"] == "Real headline about filings"


@pytest.mark.asyncio
async def test_scraper_falls_back_from_a_generic_one_word_title(tracker) -> None:
    """D3 (run 5): a generic single-word ``<title>`` ('Work', 'Home',
    'Index', 'Untitled', 'Document') carries no information, and counts as
    a bare site name -- the next candidate is used."""
    page = (
        "<html><head><title>Work</title>"
        "</head><body><h1>Real headline about filings</h1>"
        "<p>Further detail follows in the body text.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["title"] == "Real headline about filings"


@pytest.mark.asyncio
async def test_scraper_falls_back_when_the_titles_own_segment_is_generic(
    tracker,
) -> None:
    """D3 (run 5 follow-up): 'Work - Example Register' names nothing once
    its site segment is set aside; the next candidate is used even though
    the raw title, as a whole, is neither the bare site name nor a
    single-word placeholder."""
    page = (
        "<html><head><title>Work - Example Register</title>"
        '<meta property="og:site_name" content="Example Register">'
        "</head><body><h1>A summary of recent filings and their outcomes</h1>"
        "<p>Further detail follows in the body text.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["title"] == "A summary of recent filings and their outcomes"


@pytest.mark.asyncio
async def test_scraper_keeps_a_raw_title_when_no_segment_matches_the_site(
    tracker,
) -> None:
    """D3 (run 5 follow-up P1): with no og:site_name and no title segment
    matching the page's own host, the site's own segment is never
    identified, so the generic-apart-from-site rule must not apply -- the
    raw title is kept even though one of its segments is a generic word."""
    page = (
        "<html><head>"
        "<title>Home - Really Important Headline About Regional Housing "
        "Filings</title>"
        "</head><body><h1>Site Banner</h1>"
        "<p>Further detail follows in the body text.</p>"
        "</body></html>"
    )

    result = await _read_served_page(
        tracker, page, url="https://example.test/article"
    )

    assert result.data["title"] == (
        "Home - Really Important Headline About Regional Housing Filings"
    )


@pytest.mark.asyncio
async def test_scraper_falls_back_when_the_site_segment_matches_the_host(
    tracker,
) -> None:
    """D3 (run 5 follow-up): with no og:site_name, a title segment whose
    text, normalised, matches the page's own host label -- 'ToposText' for
    ``topostext.org`` -- is identified as the site's own segment; 'Work -
    ToposText' then names nothing once it is set aside."""
    page = (
        "<html><head><title>Work - ToposText</title>"
        "</head><body><h1>A summary of recent filings and their outcomes</h1>"
        "<p>Further detail follows in the body text.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page, url="https://topostext.org/work")

    assert result.data["title"] == "A summary of recent filings and their outcomes"


@pytest.mark.asyncio
async def test_scraper_skips_a_generic_one_word_og_title(tracker) -> None:
    """D3 (run 5): a generic single-word ``og:title`` is skipped the same
    way a title equal to the site's own name is, while a later, differing
    candidate remains."""
    page = (
        "<html><head><title>Example Register</title>"
        '<meta property="og:site_name" content="Example Register">'
        '<meta property="og:title" content="Index">'
        "</head><body><h1>Real headline about filings</h1>"
        "<p>Further detail follows in the body text.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["title"] == "Real headline about filings"


@pytest.mark.asyncio
async def test_scraper_falls_back_to_h2_after_the_existing_chain(tracker) -> None:
    """D10: after the existing chain (og:title, twitter:title, h1) is
    exhausted -- here, the raw title is generic apart from its own site
    segment and no h1 exists -- the first h2 stands in."""
    page = (
        "<html><head><title>Work - Example Register</title>"
        '<meta property="og:site_name" content="Example Register">'
        "</head><body><h2>Example Author, Collected Works</h2>"
        "<p>Further detail follows in the body text.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["title"] == "Example Author, Collected Works"


@pytest.mark.asyncio
async def test_scraper_falls_back_to_dc_title_when_there_is_no_h2(tracker) -> None:
    """D10: with no h1 or h2 either, a ``DC.title`` meta name stands in."""
    page = (
        "<html><head><title>Work - Example Register</title>"
        '<meta property="og:site_name" content="Example Register">'
        '<meta name="DC.title" content="Example Author, Collected Works">'
        "</head><body><p>Further detail follows in the body text.</p>"
        "</body></html>"
    )

    result = await _read_served_page(tracker, page)

    assert result.data["title"] == "Example Author, Collected Works"
