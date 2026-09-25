"""Observable static HTML scraper using HTTPX and BeautifulSoup."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Iterator
from html import unescape
from typing import Any, Protocol
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString, PreformattedString

from deep_research.agents.evidence import normalized_content_sha256
from deep_research.observability import Tracker
from deep_research.tools.base import (
    BaseTool,
    ToolCallContext,
    ToolExecution,
    ToolExecutionError,
)

# A media type published in failure details is bounded to this shape, so a
# hostile ``Content-Type`` header can never be echoed back into public state.
# Anything else is reported as ``_UNKNOWN_CONTENT_TYPE`` — never as a truncated
# copy of the header.
_MEDIA_TYPE_MAX_LENGTH = 64
_UNKNOWN_CONTENT_TYPE = "unknown"
_MEDIA_TYPE_PATTERN = re.compile(r"[a-z0-9!#$%&'*+.^_`|~-]+/[a-z0-9!#$%&'*+.^_`|~-]+")

# Statuses that mean "this client may not read this page", as opposed to a
# transient or malformed-response failure. They get their own static sentence
# because the useful next move differs: re-sourcing, not retrying.
_ACCESS_DENIED_STATUSES = frozenset({401, 402, 403, 451})

# A page is a *shell* when its visible text is no longer than this while its
# markup is at least ``_SHELL_MARKUP_MIN_CHARS``: the browser builds the body
# from data the page ships, and the static read saw only the site's chrome.
# Measured on a client-rendered site: every page was 200-315 KB of HTML whose
# visible text was 1.1-1.2 KB of navigation, while the body sat in ``data-*``
# attribute JSON. The text bound is the acquisition policy's shell bound. The
# markup floor keeps a short page in modest markup (a notice, a stub) out of
# the rule: its HTML holds nothing its visible text lacks.
_SHELL_CONTENT_MAX_CHARS = 2000
_SHELL_MARKUP_MIN_CHARS = 50_000

# A string in a shell page's data is prose -- a sentence of the page, not a
# label, an identifier or a class list -- when, tags stripped, it is at least
# this long and carries at least this many spaces.
_PROSE_MIN_CHARS = 60
_PROSE_MIN_SPACES = 8
_TAG_PATTERN = re.compile(r"<[^>]+>")

# The script types that carry a page's data rather than its code.
_JSON_SCRIPT_TYPES = frozenset({"application/json", "application/ld+json"})

# Where a page's own chrome lives: its navigation, masthead and footer by tag,
# and the landmark roles that carry the same regions on markup built from
# ``div``s. Chrome carries prose of its own -- a tagline, a consent banner, a
# legal footer -- and none of it is the page's body, however long a sentence
# the banner holds.
_CHROME_TAGS = frozenset({"nav", "header", "footer"})
_CHROME_ROLES = frozenset(
    {"navigation", "banner", "contentinfo", "dialog", "alert"}
)

# Rows a table needs before its cells are the page's own figures. One row is a
# layout wrapper, and reading a layout as the body marked a client-rendered
# shell a complete read.
_MIN_BODY_TABLE_ROWS = 2


class AsyncHttpClient(Protocol):
    async def get(self, url: str, **kwargs: Any) -> httpx.Response: ...


class WebScraperTool(BaseTool):
    """Read a web page and extract its visible text.

    The robots policy, the HTML content-type check and the URL validation are
    all still enforced by this tool; only the model-facing ``description``
    below is worded as a capability rather than as a restriction.
    """

    name = "web_scraper"
    # Capability-first wording, deliberately. The previous description led with
    # a restriction ("Fetch an allowed static HTML page"), while
    # ``document_reader`` advertises "local or remote documents" — broader-
    # sounding and therefore the tool a model reaches for when it wants to read
    # a URL it just found. A measured live run showed the researcher making 183
    # ``web_search`` calls and **zero** ``web_scraper`` calls, reading only
    # through ``document_reader``. The robots policy is still enforced by the
    # tool; this line only stops the description from steering the model away
    # from the page reader.
    description = (
        "Read one web page by URL and return its visible text. Use it to read a "
        "promising search result before reporting anything from it, and to read an "
        "organisation's own page before a page that repeats it. A host that refused "
        "automated access will refuse again: do not retry it."
    )
    input_schema = {"url": "string"}
    required_arguments = ("url",)
    output_schema = {
        "url": "string",
        "requested_url": "string",
        "resolved_url": "string",
        "title": "string",
        "text": "string",
        "status_code": "integer",
        "content_type": "string",
        "content_sha256": "string",
        "extraction_complete": "boolean",
    }

    def __init__(
        self,
        tracker: Tracker,
        *,
        client: AsyncHttpClient | None = None,
        timeout_s: float = 10.0,
        max_retries: int = 2,
        user_agent: str = "deep-research/0.1",
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
        if not isinstance(user_agent, str) or not user_agent.strip():
            raise ValueError("user_agent must be a non-empty string")
        self._client = client
        self._timeout_s = float(timeout_s)
        self._max_retries = max_retries
        self._user_agent = user_agent.strip()
        self._sleep = sleep

    def _observability_inputs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return {"url": kwargs.get("url")}

    async def _execute(self, context: ToolCallContext, **kwargs: Any) -> ToolExecution:
        try:
            url = _validate_url(kwargs.get("url"))
        except ValueError as error:
            # ``urlsplit`` inside ``_validate_url`` raises its own ``ValueError``
            # for a bracketed host, an NFKC-invalid netloc, and a malformed IPv6
            # literal, and its text embeds the caller-supplied host. This message
            # reaches public state, so it is static project text instead.
            raise ToolExecutionError(
                "url must be a non-empty absolute HTTP(S) URL",
                error_type="ValidationError",
                recoverable=False,
            ) from error

        if self._client is not None:
            return await self._execute_with_client(context, url, self._client)
        async with httpx.AsyncClient(
            headers={"User-Agent": self._user_agent},
            follow_redirects=True,
            timeout=self._timeout_s,
        ) as client:
            return await self._execute_with_client(context, url, client)

    async def _execute_with_client(
        self, context: ToolCallContext, url: str, client: AsyncHttpClient
    ) -> ToolExecution:
        robots_checked = await self._check_robots(client, url)
        response = await self._get_page(context, client, url)
        content_type = response.headers.get("content-type", "")
        if not _is_html_content_type(content_type):
            raise ToolExecutionError(
                "response content type is not HTML",
                error_type="unsupported_content_type",
                recoverable=False,
                details={"content_type": _bounded_content_type(content_type)},
            )
        title, text = _extract_html(response.text)
        if not text.strip():
            if _is_large_markup(response.text):
                # A chrome-shaped shell whose data held no prose: everything
                # the static read saw was navigation, menus and a footer, or
                # nothing at all. Admitting that as the page is how a menu
                # becomes evidence, and the same HTML comes back on a retry,
                # so the useful move is another source.
                raise ToolExecutionError(
                    "the page served no readable body; read the same material "
                    "from another source",
                    error_type="client_rendered_page",
                    recoverable=True,
                )
            # A 200 response with no readable text — an interstitial, a
            # challenge page — read nothing. Returning it as a successful read
            # is how an empty body becomes evidence.
            raise ToolExecutionError(
                "the page returned no readable text",
                error_type="empty_page_content",
                recoverable=True,
            )
        resolved_url = _resolved_url(response, url)
        data = {
            "url": url,
            "requested_url": url,
            "resolved_url": resolved_url,
            "title": title,
            "text": text,
            "status_code": response.status_code,
            "content_type": content_type,
            "content_sha256": normalized_content_sha256(text),
            "extraction_complete": True,
        }
        return ToolExecution(
            data=data,
            output_summary={
                "status_code": response.status_code,
                "character_count": len(text),
                "robots_checked": robots_checked,
            },
            metadata={"robots_checked": robots_checked},
        )

    async def _check_robots(self, client: AsyncHttpClient, url: str) -> bool:
        parts = urlsplit(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        try:
            response = await self._get(client, robots_url)
            response.raise_for_status()
            parser = RobotFileParser()
            parser.parse(response.text.splitlines())
            if not parser.can_fetch(self._user_agent, url):
                raise ToolExecutionError(
                    "robots policy disallows this URL",
                    error_type="robots_disallowed",
                    recoverable=False,
                )
        except ToolExecutionError:
            raise
        except (asyncio.TimeoutError, httpx.HTTPError, UnicodeError, ValueError):
            return False
        return True

    async def _get_page(
        self, context: ToolCallContext, client: AsyncHttpClient, url: str
    ) -> httpx.Response:
        attempts = self._max_retries + 1
        for attempt in range(attempts):
            try:
                response = await self._get(client, url)
                response.raise_for_status()
                return response
            except (
                asyncio.TimeoutError,
                httpx.TimeoutException,
                httpx.HTTPStatusError,
            ) as error:
                if attempt == attempts - 1 or not _is_retryable(error):
                    raise _tool_execution_error(error, attempt + 1) from error
                context.record_retry()
                await self._sleep(_retry_delay(error, attempt))
        raise AssertionError("retry loop must return or raise")

    async def _get(self, client: AsyncHttpClient, url: str) -> httpx.Response:
        headers = {"User-Agent": self._user_agent}
        return await client.get(
            url, headers=headers, timeout=self._timeout_s, follow_redirects=True
        )


def _validate_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("url must be a non-empty absolute HTTP(S) URL")
    url = value.strip()
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("url must be a non-empty absolute HTTP(S) URL")
    return url


def _is_html_content_type(content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type in {"text/html", "application/xhtml+xml"}


def _resolved_url(response: httpx.Response, requested: str) -> str:
    """The URL the response actually came from, after any redirect.

    A redirect must not leave only the requested URL behind: attributing the
    body to the URL that was asked for, rather than the one that served it,
    is how a mirror or a redirect target disappears from the evidence. A
    response object with no request attached (a transport double) has no
    final URL to report, so the requested one stands.
    """
    try:
        resolved = str(response.url)
    except RuntimeError:
        return requested
    return resolved or requested


def _bounded_content_type(content_type: str) -> str:
    """The media type of ``content_type``, or ``"unknown"`` when it has none.

    The result is a lower-case ASCII media type of at most
    ``_MEDIA_TYPE_MAX_LENGTH`` characters. The header is remote-controlled and
    reaches public state, so non-ASCII input is rejected before anything else:
    folding and stripping are Unicode-aware (a KELVIN SIGN folds to ASCII
    ``k``, a no-break space is stripped), which would turn malformed input into
    a normalised copy of itself. A malformed or over-long value yields the
    static marker instead of a truncated copy of that text.
    """
    if not content_type.isascii():
        return _UNKNOWN_CONTENT_TYPE
    media_type = content_type.split(";", 1)[0].strip().lower()
    if len(media_type) > _MEDIA_TYPE_MAX_LENGTH:
        return _UNKNOWN_CONTENT_TYPE
    if _MEDIA_TYPE_PATTERN.fullmatch(media_type) is None:
        return _UNKNOWN_CONTENT_TYPE
    return media_type


def _extract_html(html: str) -> tuple[str, str]:
    """The page's title and its readable text.

    The readable text is the visible text, unless the page is a *shell*:
    visible text within ``_SHELL_CONTENT_MAX_CHARS`` in markup of at least
    ``_SHELL_MARKUP_MIN_CHARS``. A shell's prose is whatever its own data
    carries (``_shell_prose``), and that prose is appended to the visible text
    the page has. Only a shell whose visible text is chrome-shaped
    (``_is_chrome``) and whose data holds no prose has no readable text at all,
    since chrome is not the page: a short paragraph or a table in large markup
    is a body, and is read as before.
    """
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    # Detached rather than decomposed: a shell's JSON payloads live in them.
    scripts = [element.extract() for element in soup("script")]
    for element in soup(["style", "noscript"]):
        element.decompose()
    visible = " ".join(soup.stripped_strings)
    if len(visible) > _SHELL_CONTENT_MAX_CHARS or not _is_large_markup(html):
        return title, visible
    prose = _shell_prose(soup, scripts)
    if not prose and _is_chrome(soup):
        return title, ""
    return title, " ".join([visible, *prose] if visible else prose)


def _is_large_markup(html: str) -> bool:
    """Whether ``html`` is large enough to hold a body its visible text lacks."""
    return len(html) >= _SHELL_MARKUP_MIN_CHARS


def _is_chrome(soup: BeautifulSoup) -> bool:
    """Whether a shell's visible text is the site's chrome, not a page body.

    Chrome is what the page's own shell says: its navigation, masthead, footer
    and dialogs, by tag or by ARIA role, whatever its sentences measure. A
    table is a body only when it sits outside that chrome and has at least
    ``_MIN_BODY_TABLE_ROWS`` rows, because a single row is a layout. Anything
    else the static read holds -- a paragraph, a list, a table of figures -- is
    a body, however short it is.
    """
    chrome = _chrome_element_ids(soup)
    if any(_is_body_table(table, chrome) for table in soup.find_all("table")):
        return False
    return not any(_is_prose(text) for text in _body_strings(soup, chrome))


def _chrome_element_ids(soup: BeautifulSoup) -> set[int]:
    """The ids of the elements whose text is the page's chrome.

    Ids, not the elements themselves: ``Tag`` equality is structural, so a set
    of tags would read two identical menus as one node and could drop a body
    that shares its markup.
    """
    ids: set[int] = set()
    for element in soup.find_all(True):
        if element.name in _CHROME_TAGS or _is_chrome_role(element):
            ids.add(id(element))
    return ids


def _is_chrome_role(element: Tag) -> bool:
    """Whether ``element``'s ``role`` attribute names a chrome landmark."""
    role = element.get("role")
    values = role if isinstance(role, list) else [role]
    tokens = [
        token.casefold()
        for value in values
        if isinstance(value, str)
        for token in value.split()
    ]
    return any(token in _CHROME_ROLES for token in tokens)


def _body_strings(soup: BeautifulSoup, chrome: set[int]) -> list[str]:
    """The visible strings outside the page's chrome, whitespace collapsed.

    Collected with their parents, unlike ``stripped_strings``, because whether
    a string is chrome is a fact about where it sits. Comments and the other
    preformatted nodes are not visible text and are skipped.
    """
    strings: list[str] = []
    for node in soup.find_all(string=True):
        if not isinstance(node, NavigableString) or isinstance(
            node, PreformattedString
        ):
            continue
        text = " ".join(str(node).split())
        if not text or any(id(parent) in chrome for parent in node.parents):
            continue
        strings.append(text)
    return strings


def _is_body_table(table: Tag, chrome: set[int]) -> bool:
    """Whether a table is the page's own figures, not chrome or a layout."""
    if len(table.find_all("tr")) < _MIN_BODY_TABLE_ROWS:
        return False
    return not any(id(parent) in chrome for parent in table.parents)


def _is_prose(text: str) -> bool:
    """Whether ``text``, already plain and whitespace-collapsed, is prose."""
    return len(text) >= _PROSE_MIN_CHARS and text.count(" ") >= _PROSE_MIN_SPACES


def _shell_prose(soup: BeautifulSoup, scripts: list[Tag]) -> list[str]:
    """The prose a shell page ships in its own data, each string once.

    A client-rendered page keeps its words in three places: its description
    meta tags, JSON in ``data-*`` attributes, and JSON scripts, read in that
    order. A plain script is code, not data, and is never read.
    """
    found: list[str] = []
    for meta in soup.find_all("meta"):
        if _is_description_meta(meta):
            _add_prose(meta.get("content"), found)
    for element in soup.find_all(True):
        for name, value in element.attrs.items():
            if (
                name.startswith("data-")
                and isinstance(value, str)
                and value.startswith(("{", "["))
            ):
                _add_json_prose(value, found)
    for script in scripts:
        if _is_json_script(script):
            _add_json_prose(script.get_text(), found)
    return list(dict.fromkeys(found))


def _is_description_meta(meta: Tag) -> bool:
    name = meta.get("name")
    prop = meta.get("property")
    return (isinstance(name, str) and name.strip().casefold() == "description") or (
        isinstance(prop, str) and prop.strip().casefold() == "og:description"
    )


def _is_json_script(script: Tag) -> bool:
    kind = script.get("type")
    return (
        isinstance(kind, str)
        and kind.split(";", 1)[0].strip().casefold() in _JSON_SCRIPT_TYPES
    )


def _add_json_prose(payload: str, found: list[str]) -> None:
    try:
        data = json.loads(payload)
    except (ValueError, RecursionError):
        # Not JSON, or nested past the parser's depth limit: either way the
        # payload has no prose this read can use.
        return
    for value in _json_strings(data):
        _add_prose(value, found)


def _json_strings(data: object) -> Iterator[str]:
    """Every string value in a decoded JSON document, breadth first.

    A queue rather than recursion, because the payload's depth is the page's
    to choose. Object keys are identifiers, not prose, and are skipped.
    """
    queue: list[object] = [data]
    for value in queue:
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            queue.extend(value.values())
        elif isinstance(value, list):
            queue.extend(value)


def _add_prose(value: object, found: list[str]) -> None:
    """Append ``value``, tags stripped, when it reads as a sentence of prose.

    A string that is itself serialized JSON is data, not prose, whatever its
    length.
    """
    if not isinstance(value, str):
        return
    text = " ".join(unescape(_TAG_PATTERN.sub(" ", value)).split())
    if _is_prose(text) and not text.startswith(("{", "[")):
        found.append(text)


def _is_retryable(error: BaseException) -> bool:
    if isinstance(error, (asyncio.TimeoutError, httpx.TimeoutException)):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code == 429 or (
            500 <= error.response.status_code <= 599
        )
    return False


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


def _tool_execution_error(error: BaseException, attempts: int) -> ToolExecutionError:
    """Build the failure for one exhausted page request.

    The message is one of this module's static sentences and the details hold
    only published categorical values, because both reach public state: the
    retry loop catches only timeouts and HTTP status errors, so no exception
    text, URL, or response content can be echoed here. ``attempts`` is
    ``attempt + 1`` for an ``attempt`` in ``0..max_retries`` and ``__init__``
    bounds ``max_retries`` to ``0..2``, so ``attempts`` is ``1..3`` and
    ``retries`` is ``0..2`` by construction. ``status_code`` is *not*
    structural: ``raise_for_status()`` raises for any non-success response,
    including informational and redirect statuses and out-of-range codes a
    nonconforming peer can send, so it is published only when it is inside
    ``100..599`` and the failure is otherwise reported as unclassified.
    """
    details: dict[str, Any] = {"attempts": attempts, "retries": attempts - 1}
    message = "the page request failed"
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        if 100 <= status_code <= 599:
            if status_code in _ACCESS_DENIED_STATUSES:
                # An access denial is actionable in a way the other HTTP
                # failures are not: the page exists but this client may not
                # read it, so the only useful next move is a *different*
                # source. Measured, not assumed: one live run spent seven
                # attempts on ``emp.lbl.gov`` pages that all returned 403,
                # while ``document_reader`` read the same site's PDFs
                # successfully 28 times out of 30. A generic "HTTP error
                # status" told the agent nothing, so it retried the host.
                message = (
                    "the publisher refused automated access to this page; read "
                    "the same material from a document or another publisher"
                )
            else:
                message = "the page request failed with an HTTP error status"
            details["status_code"] = status_code
    else:
        message = "the page request timed out"
    return ToolExecutionError(
        message,
        error_type=type(error).__name__,
        details=details,
    )
