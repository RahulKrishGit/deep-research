"""Observable static HTML scraper using HTTPX and BeautifulSoup."""

from __future__ import annotations

import asyncio
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


class AsyncHttpClient(Protocol):
    async def get(self, url: str, **kwargs: Any) -> httpx.Response: ...


class WebScraperTool(BaseTool):
    """Fetch an allowed static HTML page and extract its visible text."""

    name = "web_scraper"
    description = "Fetch an allowed static HTML page and extract visible text."
    input_schema = {"url": "string"}
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
            raise ToolExecutionError(
                str(error), error_type="ValidationError", recoverable=False
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
                details={"content_type": content_type},
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
                    details={"url": url},
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
    details: dict[str, Any] = {"attempts": attempts}
    if isinstance(error, httpx.HTTPStatusError):
        details["status_code"] = error.response.status_code
    return ToolExecutionError(
        str(error) or type(error).__name__,
        error_type=type(error).__name__,
        details=details,
    )
