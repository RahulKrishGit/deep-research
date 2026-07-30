"""Tavily-backed, observable web-search tool."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

import httpx
from tavily import TavilyClient

from deep_research.observability import Tracker
from deep_research.tools.base import (
    BaseTool,
    ToolCallContext,
    ToolExecution,
    ToolExecutionError,
)


class SearchClient(Protocol):
    """The synchronous subset of the Tavily client used by this tool."""

    def search(
        self,
        *,
        query: str,
        search_depth: str,
        max_results: int,
    ) -> Mapping[str, Any]: ...


class WebSearchTool(BaseTool):
    """Search the web with Tavily and return ranked results."""

    name = "web_search"
    description = "Search the web with Tavily and return ranked results."
    input_schema = {"query": "string", "max_results": "integer|null"}
    output_schema = {"results": "array"}

    def __init__(
        self,
        tracker: Tracker,
        *,
        api_key: str | None = None,
        client: SearchClient | None = None,
        search_depth: str = "basic",
        max_results: int = 5,
        timeout_s: float = 10.0,
        max_retries: int = 2,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        super().__init__(tracker)
        if not isinstance(search_depth, str) or not search_depth.strip():
            raise ValueError("search_depth must be a non-empty string")
        if (
            isinstance(max_results, bool)
            or not isinstance(max_results, int)
            or max_results < 1
        ):
            raise ValueError("max_results must be an integer greater than zero")
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
        ):
            raise ValueError("max_retries must be a non-negative integer")
        self._client = client or TavilyClient(api_key=api_key)
        self._search_depth = search_depth.strip()
        self._max_results = max_results
        self._timeout_s = float(timeout_s)
        self._max_retries = max_retries
        self._sleep = sleep

    def _observability_inputs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return {"query": kwargs.get("query"), "max_results": kwargs.get("max_results")}

    async def _execute(self, context: ToolCallContext, **kwargs: Any) -> ToolExecution:
        query = kwargs.get("query")
        requested_max_results = kwargs.get("max_results")
        try:
            query = _validate_query(query)
            max_results = _validate_requested_max_results(
                requested_max_results, self._max_results
            )
        except ValueError as error:
            raise ToolExecutionError(
                str(error), error_type="ValidationError", recoverable=False
            ) from error

        response = await self._search_with_retries(context, query, max_results)
        normalized = _normalize_results(response)
        return ToolExecution(
            data={"results": normalized},
            output_summary={"result_count": len(normalized)},
            metadata={"provider": "tavily", "result_count": len(normalized)},
        )

    async def _search_with_retries(
        self, context: ToolCallContext, query: str, max_results: int
    ) -> Mapping[str, Any]:
        attempts = self._max_retries + 1
        for attempt in range(attempts):
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(
                        self._client.search,
                        query=query,
                        search_depth=self._search_depth,
                        max_results=max_results,
                    ),
                    timeout=self._timeout_s,
                )
            except (
                asyncio.TimeoutError,
                httpx.TimeoutException,
                httpx.HTTPStatusError,
            ) as error:
                is_last_attempt = attempt == attempts - 1
                if is_last_attempt or not _is_retryable(error):
                    raise _tool_execution_error(error, attempt + 1) from error
                context.record_retry()
                await self._sleep(_retry_delay(error, attempt))
        raise AssertionError("retry loop must return or raise")


def _validate_query(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("query must be a non-empty string")
    return value.strip()


def _validate_requested_max_results(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("max_results must be an integer greater than zero")
    return value


def _is_retryable(error: BaseException) -> bool:
    if isinstance(error, (asyncio.TimeoutError, httpx.TimeoutException)):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        return (
            error.response.status_code == 429
            or 500 <= error.response.status_code <= 599
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


def _normalize_results(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    results = response.get("results")
    if not isinstance(results, list):
        raise ToolExecutionError(
            "Tavily response must contain a results list",
            error_type="ResponseValidationError",
        )
    normalized: list[dict[str, Any]] = []
    for rank, result in enumerate(results, start=1):
        if not isinstance(result, Mapping):
            raise ToolExecutionError(
                "Tavily result must be a mapping", error_type="ResponseValidationError"
            )
        title = result.get("title")
        url = result.get("url")
        if (
            not isinstance(title, str)
            or not title.strip()
            or not isinstance(url, str)
            or not url.strip()
        ):
            raise ToolExecutionError(
                "Tavily results require non-empty title and url",
                error_type="ResponseValidationError",
            )
        content = result.get("content")
        score = result.get("score")
        normalized.append(
            {
                "title": title.strip(),
                "url": url.strip(),
                "snippet": content if isinstance(content, str) else "",
                "rank": rank,
                "provider_score": score
                if isinstance(score, (int, float)) and not isinstance(score, bool)
                else None,
            }
        )
    return normalized
