"""Observable static HTML scraper using HTTPX and BeautifulSoup."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Iterator, Mapping
from datetime import date
from html import unescape
from typing import Any, Protocol
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString, PreformattedString

from deep_research.agents.evidence import normalized_content_sha256
from deep_research.agents.wording import title_segments
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
        "page_published": "string|null",
        "page_updated": "string|null",
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
        resolved_url = _resolved_url(response, url)
        title, text, page_published, page_updated = _extract_html(response.text, resolved_url)
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
        if page_published is not None:
            data["page_published"] = page_published
        if page_updated is not None:
            data["page_updated"] = page_updated
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


def _extract_html(html: str, url: str) -> tuple[str, str, str | None, str | None]:
    """The page's title, its readable text, and its own (published, updated)
    dates (D14).

    The readable text is the visible text, unless the page is a *shell*:
    visible text within ``_SHELL_CONTENT_MAX_CHARS`` in markup of at least
    ``_SHELL_MARKUP_MIN_CHARS``. A shell's prose is whatever its own data
    carries (``_shell_prose``), and that prose is appended to the visible text
    the page has. Only a shell whose visible text is chrome-shaped
    (``_is_chrome``) and whose data holds no prose has no readable text at all,
    since chrome is not the page: a short paragraph or a table in large markup
    is a body, and is read as before.

    The page's own dates are read once, from its own structured metadata only
    (see :func:`_extract_page_date`) -- never from the extracted text, which
    is prose a reader wrote for a person, not a machine-readable claim.
    """
    soup = BeautifulSoup(html, "html.parser")
    title = _page_title(soup, url)
    # Detached rather than decomposed: a shell's JSON payloads live in them.
    scripts = [element.extract() for element in soup("script")]
    for element in soup(["style", "noscript"]):
        element.decompose()
    visible = " ".join(soup.stripped_strings)
    if len(visible) > _SHELL_CONTENT_MAX_CHARS or not _is_large_markup(html):
        text = visible
    else:
        prose = _shell_prose(soup, scripts)
        text = (
            ""
            if not prose and _is_chrome(soup)
            else " ".join([visible, *prose] if visible else prose)
        )
    page_published, page_updated = _extract_page_date(soup, scripts)
    return title, text, page_published, page_updated


# --------------------------------------------------------------------------
# D3: title precedence (RevW5Titles P1-a, P2). The raw ``<title>`` tag
# usually carries a publisher-credit segment a browser tab shows and a
# reader relies on ("Headline - Publisher"), and the issuer-evidence and
# page-owner checks read that segment from nowhere else. It is kept
# whenever it says anything beyond the site's own name for itself. Only
# when it is empty, or names only the site (verbatim, or as its one
# segment, per ``title_segments``), does a page's own claim about *this*
# page stand in for it: ``og:title``, then ``twitter:title``, then a
# heading that is not itself a site banner -- skipping any of those equal
# to the site name while a later, differing one remains. A probe of a live
# page found ``<title>`` holding only the site's own name while ``og:title``
# held the page's real headline (the audited case, ``<title>Medium</title>``).
# --------------------------------------------------------------------------


def _meta_property_content(soup: BeautifulSoup, prop: str) -> str | None:
    meta = soup.find("meta", attrs={"property": prop})
    if meta is None:
        return None
    content = meta.get("content")
    return content.strip() if isinstance(content, str) and content.strip() else None


def _meta_name_content(soup: BeautifulSoup, name: str) -> str | None:
    meta = soup.find(
        "meta", attrs={"name": re.compile(rf"^{re.escape(name)}$", re.IGNORECASE)}
    )
    if meta is None:
        return None
    content = meta.get("content")
    return content.strip() if isinstance(content, str) and content.strip() else None


# A banner or logo heading names the site, not the page: a theme's
# ``<header><h1 class="site-title">...</h1></header>`` or a ``<nav>``
# heading is never a headline (RevW5Titles P2), so it is excluded before
# the first ``h1`` or ``h2`` is read as a fallback title candidate. The
# same is true of a heading under ``<aside>``/``<footer>``, or under any
# ancestor whose own id or class names sidebar, comment, related, or
# footer chrome even without that tag (RevZ3 P2): "Related articles" in an
# aside, or "Comments" in a ``<section id="comments">``, names that block,
# not the page.
_BANNER_HEADING_ANCESTORS = ("header", "nav", "aside", "footer")
_BANNER_HEADING_CLASSES = frozenset({"site-title", "logo"})
_CHROME_ANCESTOR_MARKERS = ("sidebar", "comment", "related", "footer")


def _has_chrome_ancestor_marker(heading: Tag) -> bool:
    node = heading.parent
    while isinstance(node, Tag):
        identifiers = [str(node.get("id") or "")]
        identifiers.extend(str(cls) for cls in node.get("class") or [])
        joined = " ".join(identifiers).casefold()
        if any(marker in joined for marker in _CHROME_ANCESTOR_MARKERS):
            return True
        node = node.parent
    return False


def _is_banner_heading(heading: Tag) -> bool:
    if heading.find_parent(_BANNER_HEADING_ANCESTORS) is not None:
        return True
    classes = heading.get("class") or []
    if any(str(cls).casefold() in _BANNER_HEADING_CLASSES for cls in classes):
        return True
    return _has_chrome_ancestor_marker(heading)


def _first_usable_heading_text(soup: BeautifulSoup, name: str) -> str | None:
    for heading in soup.find_all(name):
        if _is_banner_heading(heading):
            continue
        text = heading.get_text(strip=True)
        if text:
            return text
    return None


def _first_usable_h1_text(soup: BeautifulSoup) -> str | None:
    return _first_usable_heading_text(soup, "h1")


def _first_usable_h2_text(soup: BeautifulSoup) -> str | None:
    return _first_usable_heading_text(soup, "h2")


# D10: the leading clause of a page's own description meta -- up to a
# sentence end followed by whitespace and a new capital-letter sentence,
# never merely a period, or the whole text when no such cut leaves at
# least two words (RevZ3 P3) -- when nothing else names the page: "Example
# Author, Collected Works, translated by J. Smith" from a longer
# DC.description still names the work, without the rest of the
# description's own prose, and "Dr. Example Author, Collected Works" keeps
# its abbreviation rather than being cut down to "Dr" alone.
_SENTENCE_CUT_PATTERN = re.compile(r"[.;:]\s+(?=[A-Z])")


def _leading_clause(text: str) -> str | None:
    for match in _SENTENCE_CUT_PATTERN.finditer(text):
        clause = text[: match.start()].strip()
        if len(clause.split()) >= 2:
            return clause
    clause = text.strip()
    return clause if len(clause.split()) >= 2 else None


# A generic single-word title names no page at all -- a template's default
# caption for an untitled work, not a headline -- and is treated the same
# as the site's own bare name (D3, run 5): the next candidate is used.
_GENERIC_TITLE_WORDS = frozenset({"work", "home", "index", "untitled", "document"})


def _is_generic_placeholder(value: str) -> bool:
    return value.strip().casefold() in _GENERIC_TITLE_WORDS


def _is_just_site_name(raw_title: str, site_name: str | None) -> bool:
    """Whether ``raw_title`` names only the site itself: it is ``og:site_name``
    verbatim, or has exactly one segment (see :func:`title_segments`) equal
    to it.
    """
    if not site_name:
        return False
    if raw_title == site_name:
        return True
    segments = title_segments(raw_title)
    return len(segments) == 1 and segments[0] == site_name


_LABEL_CHAR_PATTERN = re.compile(r"[a-z0-9]+")


def _normalized_label(text: str) -> str:
    """``text``, casefolded with spaces and punctuation removed, so a title
    segment can be compared against a host's own label regardless of
    spacing or hyphenation (D3, run 5 follow-up): 'Example Register' and
    'example-register' both normalise to 'exampleregister'.
    """
    return "".join(_LABEL_CHAR_PATTERN.findall(text.casefold()))


def _host_label(url: str) -> str:
    """The page's own host's main label: its first DNS label, with a
    leading ``www`` dropped -- ``topostext`` for ``topostext.org``,
    ``example-register`` for ``www.example-register.test``.
    """
    try:
        host = urlsplit(url).netloc.split(":", 1)[0].casefold()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host.split(".", 1)[0]


def _site_segment(segments: list[str], site_name: str | None, host_label: str) -> str | None:
    """Whichever of ``segments`` is the site's own, or ``None`` when none
    is actually identified (D3, run 5 follow-up).

    A segment equal to ``og:site_name`` is the site's own. Absent that, a
    segment whose normalised text matches the page's own host label is --
    the position is never guessed: a title's other segment might just as
    well come first ('Home - Really Important Headline') as last, and
    trying both ends blindly misclassified a perfectly good headline as
    unhelpful.
    """
    if site_name is not None:
        return site_name if site_name in segments else None
    normalized_host = _normalized_label(host_label)
    if not normalized_host:
        return None
    return next(
        (segment for segment in segments if _normalized_label(segment) == normalized_host),
        None,
    )


def _is_generic_apart_from_site(value: str, site_name: str | None, host_label: str) -> bool:
    """Whether every segment of ``value`` other than the site's own is a
    generic placeholder word (D3, run 5 follow-up): 'Work - ToposText'
    names nothing once the site segment is set aside, even though neither
    the whole title nor any one segment alone is the bare site name.

    Never applied unless the site's own segment is actually identified
    (by ``og:site_name`` or by the host label): guessing which end of an
    unrelated two-segment title is the site's own discarded real headlines
    such as 'Home - Really Important Headline About Regional Housing
    Filings'.
    """
    segments = title_segments(value)
    if len(segments) < 2:
        return False
    site_segment = _site_segment(segments, site_name, host_label)
    if site_segment is None:
        return False
    remaining = [segment for segment in segments if segment != site_segment]
    return bool(remaining) and all(_is_generic_placeholder(segment) for segment in remaining)


def _is_unhelpful_title(value: str, site_name: str | None, host_label: str) -> bool:
    """Whether ``value`` names nothing useful: the site's own bare name, a
    generic single-word placeholder, or a title that is generic apart from
    its own site segment (D3, run 5)."""
    return (
        value == site_name
        or _is_generic_placeholder(value)
        or _is_generic_apart_from_site(value, site_name, host_label)
    )


# D9: a publisher's own <title> is sometimes truncated mid-word ("... for
# the northern distr"), while a metadata title carries it in full. When a
# metadata title starts with the raw title -- compared case- and
# space-insensitively -- and is strictly longer, it is a fuller version of
# the same title, not a different candidate, so it is preferred over the
# cut one.
def _uncut_metadata_title(raw_title: str, soup: BeautifulSoup) -> str | None:
    normalized_raw = " ".join(raw_title.casefold().split())
    best: str | None = None
    for candidate in (
        _meta_name_content(soup, "citation_title"),
        _meta_name_content(soup, "DC.title"),
        _meta_property_content(soup, "og:title"),
    ):
        if not candidate or len(candidate) <= len(raw_title):
            continue
        normalized_candidate = " ".join(candidate.casefold().split())
        if normalized_candidate.startswith(normalized_raw) and (
            best is None or len(candidate) > len(best)
        ):
            best = candidate
    return best


def _page_title(soup: BeautifulSoup, url: str) -> str:
    """The page's title (D3, RevW5Titles P1-a/P2; run 5 D3, D10; run 8 D9).

    The raw ``<title>`` tag is kept whenever it is not empty and does not
    name only the site, a generic placeholder, or a title that is generic
    apart from its own site segment -- unless a metadata title
    (``citation_title``, ``DC.title``, ``og:title``) is a strict, longer
    superstring of it, in which case that fuller title is used instead
    (D9); only otherwise does ``og:title``, ``twitter:title``, a
    non-banner ``h1`` or ``h2``, ``DC.title``, ``citation_title``, or the
    leading clause of ``DC.description`` stand in for it, in that order,
    skipping any of those that are themselves unhelpful while a later,
    differing one remains.
    """
    site_name = _meta_property_content(soup, "og:site_name")
    host_label = _host_label(url)
    raw_title = soup.title.get_text(strip=True) if soup.title else ""
    if (
        raw_title
        and not _is_just_site_name(raw_title, site_name)
        and not _is_unhelpful_title(raw_title, site_name, host_label)
    ):
        return _uncut_metadata_title(raw_title, soup) or raw_title
    description = _meta_name_content(soup, "DC.description")
    candidates: list[str | None] = [
        _meta_property_content(soup, "og:title"),
        _meta_name_content(soup, "twitter:title"),
        _first_usable_h1_text(soup),
        _meta_name_content(soup, "DC.title"),
        _meta_name_content(soup, "citation_title"),
        _first_usable_h2_text(soup),
        _leading_clause(description) if description else None,
    ]
    for index, candidate in enumerate(candidates):
        if not candidate:
            continue
        if _is_unhelpful_title(candidate, site_name, host_label) and any(
            later and not _is_unhelpful_title(later, site_name, host_label)
            for later in candidates[index + 1 :]
        ):
            continue
        return candidate
    return raw_title


# --------------------------------------------------------------------------
# D14: the page's own dates, captured once at scrape time, from structured
# metadata only.
# --------------------------------------------------------------------------
#
# ``page_published`` and ``page_updated`` are two different facts kept apart:
# collapsing them let a last-edit timestamp stand in for the page's original
# date. Read from a publisher's own syndication metadata only -- Open Graph,
# microdata, citation/Dublin Core meta names, then JSON-LD, in that order --
# because that is the page's own structured claim about itself. Prose is
# deliberately never read for this: a byline is not addressed to a machine,
# and a probe of the first cut of this rule found it reading event dates,
# data-period dates, effective dates, a related article's date and even a
# comment's date as the page's own (RevDatesR3). Every reader normalises what
# it found and refuses what it cannot parse instead of guessing at a day or
# month the page never gave -- an impossible calendar date is dropped rather
# than salvaged into a coarser one, and a value is refused unless a real date
# ends where the value does (never an epoch timestamp's leading digits).

# ``article:published_time`` names when a page first published; ``og:
# updated_time`` and ``article:modified_time`` both name a later edit. Never
# read one for the other -- a probe of the first cut of this rule found a
# page's ``article:modified_time`` standing in as its publication date.
_OG_PUBLISHED_PROPERTIES = ("article:published_time",)
_OG_UPDATED_PROPERTIES = ("article:modified_time", "og:updated_time")

# Citation and Dublin Core ``<meta name="...">`` conventions -- only the
# names that specifically mean *publication*. The generic ``date``,
# ``dc.date`` and ``dcterms.date`` are deliberately absent: a probe of an
# earlier cut of this rule found ``name="date"`` holding a template's build
# stamp and ``dcterms.date`` holding a last-modified date, both outranking a
# page's own correct JSON-LD (RevDatesR3 P2). ``dcterms.modified`` is the one
# name in this family that specifically means an edit.
_CITATION_PUBLISHED_META_NAMES = (
    "citation_publication_date",
    "citation_date",
    "dc.date.issued",
    "dcterms.issued",
)
_CITATION_UPDATED_META_NAMES = ("dcterms.modified",)

# A JSON-LD node states the page's own date only when it is shaped like the
# page's own content. ``WebPage`` is the fallback for a page whose content
# type carries no more specific label. A ``WebSite``, ``Organization``,
# ``Comment`` or ``Person`` node is never read for it at all: a probe of the
# first cut of this rule found a site-wide ``WebSite`` node's edit date, and a
# ``Comment`` node's own timestamp, both outranking the article's own node.
_JSON_LD_ARTICLE_TYPES = frozenset(
    {
        "article",
        "newsarticle",
        "blogposting",
        "report",
        "scholarlyarticle",
        "techarticle",
        "review",
    }
)
_JSON_LD_FALLBACK_TYPES = frozenset({"webpage"})
_JSON_LD_EXCLUDED_TYPES = frozenset({"website", "organization", "comment", "person"})

# An ISO 8601 date or timestamp's calendar prefix, anchored so what follows it
# must be the end of the value, a time separator, or a timezone designator --
# never another digit. Without this anchor, epoch-seconds content
# ("1758067200") read its leading four digits as the year 1758.
_ISO_DATE_PATTERN = re.compile(
    r"^\s*(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?(?=$|[T\s]|[+-]\d{2}:?\d{2}|Z)"
)


def _normalize_page_date(raw: str) -> str | None:
    """``raw`` at the calendar precision it states, or ``None``.

    Accepts an ISO 8601 date or timestamp the way page metadata publishes it
    ("2026-09-17T10:00:00Z", "2026-09-17", "2026-09", "2026") and keeps only
    the calendar portion, never inventing a month or day the source did not
    carry. An impossible calendar date ("2026-02-30") is not a date the page
    carries, so it is refused rather than salvaged into its year or month --
    and so is a value whose digits merely happen to start like one (epoch
    seconds, an identifier), which the anchor after the matched prefix rules
    out.
    """
    match = _ISO_DATE_PATTERN.match(raw)
    if match is None:
        return None
    year_s, month_s, day_s = match.groups()
    if not (1000 <= int(year_s) <= 9999):
        return None
    if month_s is None:
        return year_s
    if not 1 <= int(month_s) <= 12:
        return None
    if day_s is None:
        return f"{year_s}-{month_s}"
    try:
        date(int(year_s), int(month_s), int(day_s))
    except ValueError:
        return None
    return f"{year_s}-{month_s}-{day_s}"


def _first_meta_property_date(soup: BeautifulSoup, properties: tuple[str, ...]) -> str | None:
    for prop in properties:
        meta = soup.find("meta", attrs={"property": prop})
        if meta is None:
            continue
        content = meta.get("content")
        if isinstance(content, str) and content.strip():
            normalized = _normalize_page_date(content)
            if normalized is not None:
                return normalized
    return None


def _first_meta_name_date(soup: BeautifulSoup, names: tuple[str, ...]) -> str | None:
    for name in names:
        meta = soup.find(
            "meta", attrs={"name": re.compile(rf"^{re.escape(name)}$", re.IGNORECASE)}
        )
        if meta is None:
            continue
        content = meta.get("content")
        if isinstance(content, str) and content.strip():
            normalized = _normalize_page_date(content)
            if normalized is not None:
                return normalized
    return None


# A microdata itemprop's date is read only when the item it belongs to is
# shaped like the page's own content -- the same Article family JSON-LD
# reads, plus a bare ``WebPage``. Reusing that vocabulary keeps "what shape
# counts as the page's own content" one answer, not two that could drift.
_ITEMSCOPE_ALLOWED_TYPES = _JSON_LD_ARTICLE_TYPES | _JSON_LD_FALLBACK_TYPES


def _itemtype_names(itemtype: str) -> set[str]:
    """The type keywords one ``itemtype`` attribute names, from its URL(s).

    ``itemtype`` is one or more space-separated schema URLs
    ("https://schema.org/BlogPosting"); the keyword is the final path
    segment, which is what this module's JSON-LD ``@type`` vocabulary
    already names its own types by.
    """
    return {
        token.rstrip("/").rsplit("/", 1)[-1].casefold()
        for token in itemtype.split()
        if token.rstrip("/")
    }


def _nearest_itemscope_types(tag: Tag) -> set[str] | None:
    """The type keywords of ``tag``'s nearest ``itemscope`` item, or ``None``.

    ``None`` means the element carries no ``itemprop`` context at all -- no
    ancestor (or itself) declares ``itemscope`` -- which is not the page's
    own content by any reading and is never a candidate here.
    """
    node: Tag | None = tag
    while node is not None:
        if node.has_attr("itemscope"):
            itemtype = node.get("itemtype")
            return _itemtype_names(itemtype) if isinstance(itemtype, str) else set()
        node = node.parent if isinstance(node.parent, Tag) else None
    return None


# A date-bearing itemprop inside the page's navigation, masthead, footer or
# an aside is never the page's own date: a related-post card, a "you might
# also like" widget and a site's masthead all sit in one of these regions,
# never in the article itself. Itemtype scoping alone cannot tell such a
# card apart from the article -- a card is itself validly typed
# ``BlogPosting`` -- so the region it sits in is what decides this, not its
# claimed type (RevDatesR3 round 3).
_MICRODATA_CHROME_TAGS = frozenset({"aside", "nav", "header", "footer"})


def _has_chrome_ancestor(tag: Tag) -> bool:
    node: Tag | None = tag
    while node is not None:
        if node.name in _MICRODATA_CHROME_TAGS:
            return True
        node = node.parent if isinstance(node.parent, Tag) else None
    return False


def _first_itemprop_date(soup: BeautifulSoup, prop: str) -> str | None:
    """The date every article-shaped item's own ``itemprop`` agrees on.

    An itemprop with no ``itemscope`` ancestor, one scoped to a ``Comment``,
    ``Person``, ``Organization`` or ``WebSite`` item, or one sitting inside
    the page's navigation, masthead, footer or an aside, is never a
    candidate: item *type* alone cannot tell a related-post card or a
    recent-posts widget apart from the article, since such a card is itself
    validly typed ``BlogPosting`` -- it is the *region* it sits in that
    marks it as something other than the article (RevDatesR3 P1/P2).

    Multiple surviving candidates that disagree are worse than none: a
    second article-shaped item elsewhere on the page whose own date differs
    from the first means this page's own date is not established, so
    nothing is returned rather than guessing which one is right.
    """
    found: set[str] = set()
    for tag in soup.find_all(attrs={"itemprop": prop}):
        if _has_chrome_ancestor(tag):
            continue
        scope_types = _nearest_itemscope_types(tag)
        if not scope_types or scope_types & _JSON_LD_EXCLUDED_TYPES:
            continue
        if not (scope_types & _ITEMSCOPE_ALLOWED_TYPES):
            continue
        value = tag.get("content") or tag.get("datetime")
        if not isinstance(value, str) or not value.strip():
            value = tag.get_text(strip=True)
        if isinstance(value, str) and value.strip():
            normalized = _normalize_page_date(value)
            if normalized is not None:
                found.add(normalized)
    return found.pop() if len(found) == 1 else None


def _og_dates(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    """Open Graph article dates: published, then modified/updated."""
    return (
        _first_meta_property_date(soup, _OG_PUBLISHED_PROPERTIES),
        _first_meta_property_date(soup, _OG_UPDATED_PROPERTIES),
    )


def _microdata_dates(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    """Microdata ``itemprop="datePublished"`` / ``"dateModified"`` dates."""
    return (
        _first_itemprop_date(soup, "datePublished"),
        _first_itemprop_date(soup, "dateModified"),
    )


def _citation_dates(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    """Citation and Dublin Core ``<meta name="...">`` dates."""
    return (
        _first_meta_name_date(soup, _CITATION_PUBLISHED_META_NAMES),
        _first_meta_name_date(soup, _CITATION_UPDATED_META_NAMES),
    )


def _is_json_ld_script(script: Tag) -> bool:
    kind = script.get("type")
    return isinstance(kind, str) and kind.strip().casefold() == "application/ld+json"


def _json_ld_nodes(payload: object) -> Iterator[object]:
    """Every mapping in one JSON-LD payload, walking an ``@graph`` array too."""
    if isinstance(payload, list):
        for item in payload:
            yield from _json_ld_nodes(item)
    elif isinstance(payload, Mapping):
        yield payload
        graph = payload.get("@graph")
        if graph is not None:
            yield from _json_ld_nodes(graph)


def _json_ld_node_types(node: Mapping[str, object]) -> set[str]:
    raw = node.get("@type")
    if isinstance(raw, str):
        values: list[object] = [raw]
    elif isinstance(raw, list):
        values = raw
    else:
        values = []
    return {
        value.strip().casefold()
        for value in values
        if isinstance(value, str) and value.strip()
    }


def _json_ld_date_strings(value: object) -> Iterator[str]:
    """Every date-shaped string ``value`` carries.

    A schema.org property may be published as a single value or as a JSON
    array of values (D3, run 5): a page's own ``dateModified`` given as
    ``["2026-07-28"]`` is read the same as a bare string, never silently
    dropped for not being one.
    """
    if isinstance(value, str):
        if value.strip():
            yield value
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                yield item


def _first_json_ld_date(nodes: list[Mapping[str, object]], key: str) -> str | None:
    """The date every node in ``nodes`` agrees on for ``key``, or ``None``.

    Collected from every node rather than the first match: two article-
    shaped nodes on one page that disagree on ``datePublished`` are not
    resolved by letting the first one win -- the same agreement rule
    microdata already applies (WholeBranchReview P3-3).
    """
    found: set[str] = set()
    for node in nodes:
        for raw in _json_ld_date_strings(node.get(key)):
            normalized = _normalize_page_date(raw)
            if normalized is not None:
                found.add(normalized)
    return found.pop() if len(found) == 1 else None


def _json_ld_candidate_nodes(
    scripts: list[Tag],
) -> tuple[list[Mapping[str, object]], list[Mapping[str, object]]]:
    """Every article-shaped node, and every ``WebPage`` fallback node.

    A ``WebSite``, ``Organization``, ``Comment`` or ``Person`` node is
    dropped here, before either list, so neither a site-wide date nor a
    comment's own timestamp can ever reach a caller.
    """
    article_nodes: list[Mapping[str, object]] = []
    webpage_nodes: list[Mapping[str, object]] = []
    for script in scripts:
        if not _is_json_ld_script(script):
            continue
        try:
            payload = json.loads(script.get_text())
        except (ValueError, RecursionError):
            continue
        for node in _json_ld_nodes(payload):
            if not isinstance(node, Mapping):
                continue
            types = _json_ld_node_types(node)
            if types & _JSON_LD_EXCLUDED_TYPES:
                continue
            if types & _JSON_LD_ARTICLE_TYPES:
                article_nodes.append(node)
            elif types & _JSON_LD_FALLBACK_TYPES:
                webpage_nodes.append(node)
    return article_nodes, webpage_nodes


def _json_ld_dates(scripts: list[Tag]) -> tuple[str | None, str | None]:
    """The page's own (published, updated) dates from its JSON-LD, if any.

    An article-shaped node's own date is preferred; a ``WebPage`` node fills
    in only whichever of the two fields no article-shaped node stated, so an
    article node that names only its edit date and a ``WebPage`` node that
    names only the publish date are each read for their own field rather
    than one discarding the other.
    """
    article_nodes, webpage_nodes = _json_ld_candidate_nodes(scripts)
    published = _first_json_ld_date(article_nodes, "datePublished") or (
        _first_json_ld_date(webpage_nodes, "datePublished")
    )
    updated = _first_json_ld_date(article_nodes, "dateModified") or (
        _first_json_ld_date(webpage_nodes, "dateModified")
    )
    return published, updated


def _extract_page_date(soup: BeautifulSoup, scripts: list[Tag]) -> tuple[str | None, str | None]:
    """The page's own (published, updated) dates, from its own metadata only.

    Each field independently takes the first source, in order, that states
    it: Open Graph, then JSON-LD, then microdata scoped to the page's own
    content, then a citation/Dublin Core meta name naming publication
    specifically -- so a page whose Open Graph tags name only a publish date
    still gets its edit date from wherever else it states one. JSON-LD ranks
    above microdata (RevDatesR3 P1): a correct article node then settles both
    fields before a mis-scoped or unscoped ``itemprop`` elsewhere on the page
    is ever consulted. ``None`` for a field the page's metadata never states
    -- never a guess, and never read from prose.
    """
    published, updated = _og_dates(soup)
    for source in (
        lambda: _json_ld_dates(scripts),
        lambda: _microdata_dates(soup),
        lambda: _citation_dates(soup),
    ):
        if published and updated:
            break
        found_published, found_updated = source()
        published = published or found_published
        updated = updated or found_updated
    return published, updated


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
