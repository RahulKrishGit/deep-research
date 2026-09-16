"""Observable static HTML scraper using HTTPX and BeautifulSoup."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

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
    description = "Read a web page and extract its visible text."
    input_schema = {"url": "string"}
    required_arguments = ("url",)
    output_schema = {
        "url": "string",
        "title": "string",
        "text": "string",
        "status_code": "integer",
        "content_type": "string",
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
        data = {
            "url": url,
            "title": title,
            "text": text,
            "status_code": response.status_code,
            "content_type": content_type,
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
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    for element in soup(["script", "style", "noscript"]):
        element.decompose()
    return title, " ".join(soup.stripped_strings)


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
