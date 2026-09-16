"""Tavily-backed, observable web-search tool."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from tavily import TavilyClient

from deep_research.observability import Tracker
from deep_research.tools.base import (
    BaseTool,
    ToolCallContext,
    ToolExecution,
    ToolExecutionError,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle guard for type checkers only
    from deep_research.request_budget import RequestBudget

# A failure published for a search request is bounded to this shape, so an
# exception message or a remote ``Content-Type`` header can never be echoed
# back into public state. ``__init__`` bounds ``max_retries`` to ``0..2``, so a
# published attempt count is ``1.._MAX_ATTEMPTS``, a status code is an integer
# in ``100..599``, and a media type is a lower-case ASCII type of at most
# ``_MEDIA_TYPE_MAX_LENGTH`` characters. Anything else is reported as one of
# this module's static markers.
_MAX_ATTEMPTS = 3
_MEDIA_TYPE_MAX_LENGTH = 64
_UNKNOWN_CONTENT_TYPE = "unknown"
_MEDIA_TYPE_PATTERN = re.compile(r"[a-z0-9!#$%&'*+.^_`|~-]+/[a-z0-9!#$%&'*+.^_`|~-]+")


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
    required_arguments = ("query",)

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
        request_budget: RequestBudget | None = None,
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
            or max_retries > 2
        ):
            raise ValueError("max_retries must be a non-negative integer")
        self._client = client or TavilyClient(api_key=api_key)
        self._search_depth = search_depth.strip()
        self._max_results = max_results
        self._timeout_s = float(timeout_s)
        self._max_retries = max_retries
        self._sleep = sleep
        self._request_budget = request_budget

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
        budget = self._request_budget

        def search_once() -> Mapping[str, Any]:
            """The one transport attempt the loop already reserved a unit for."""
            return self._client.search(
                query=query,
                search_depth=self._search_depth,
                max_results=max_results,
            )

        attempts = self._max_retries + 1
        for attempt in range(attempts):
            # The reservation happens here rather than inside ``search_once``,
            # so it stays outside the cancellable window below. ``reserve`` is
            # synchronous and touches no network, but it is not instantaneous,
            # and a reservation made inside a work item handed to
            # ``asyncio.to_thread`` is cancelled the moment ``wait_for`` times
            # out: the refusal then lands on an already-cancelled future, is
            # dropped, and a spent ceiling is republished as an ordinary
            # timeout — a failed ``ToolResult`` recorded as ``agent_tool_failed``
            # instead of the run-ending refusal this ceiling exists to produce.
            # Reserving in the loop body keeps the ordering the ceiling relies
            # on: every real client call, the first and each retry, is preceded
            # by exactly one reservation, and a refused attempt is neither
            # charged nor sent. The refusal is not inside the caught tuple, so
            # it ends the request instead of being retried, and it never counts
            # as an attempt of its own, because the budget refuses before it
            # increments.
            if budget is not None:
                budget.reserve("tavily")
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(search_once),
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


def _bounded_attempts(value: Any) -> int | None:
    """``value`` as a published attempt count, or ``None`` when it is not one."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not 1 <= value <= _MAX_ATTEMPTS:
        return None
    return value


def _bounded_status_code(value: Any) -> int | None:
    """``value`` as a published status code, or ``None`` when it is not one."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not 100 <= value <= 599:
        return None
    return value


def _content_type(response: Any) -> Any:
    """The ``Content-Type`` of ``response``, when it exposes one."""
    headers = getattr(response, "headers", None)
    if not isinstance(headers, Mapping):
        return None
    return headers.get("content-type")


def _bounded_content_type(value: Any) -> str:
    """The media type of ``value``, or the marker when it has none.

    The result is a lower-case ASCII media type of at most
    ``_MEDIA_TYPE_MAX_LENGTH`` characters or ``_UNKNOWN_CONTENT_TYPE``. The
    value comes from a remote provider and reaches public state, so it is
    revalidated by type and non-ASCII input is rejected before anything else:
    folding and stripping are Unicode-aware (a KELVIN SIGN folds to ASCII
    ``k``, a no-break space is stripped), which would turn malformed input into
    a normalised copy of itself. A malformed or over-long value yields the
    static marker instead of a truncated copy of that text.
    """
    if not isinstance(value, str) or not value.isascii():
        return _UNKNOWN_CONTENT_TYPE
    media_type = value.split(";", 1)[0].strip().lower()
    if len(media_type) > _MEDIA_TYPE_MAX_LENGTH:
        return _UNKNOWN_CONTENT_TYPE
    if _MEDIA_TYPE_PATTERN.fullmatch(media_type) is None:
        return _UNKNOWN_CONTENT_TYPE
    return media_type


def _tool_execution_error(error: BaseException, attempts: Any) -> ToolExecutionError:
    """Build the failure for one exhausted search request.

    The message is one of this module's static sentences and the details hold
    only bounded values, because both reach public state: the retry loop
    catches timeouts and HTTP status errors raised on behalf of a remote
    provider, so ``str(error)`` routinely embeds remote text. Every value is
    revalidated here rather than trusted from the caller. ``attempts`` is
    ``attempt + 1`` for an ``attempt`` in ``0..max_retries`` and ``__init__``
    bounds ``max_retries`` to ``0..2``, so a published count is ``1..3`` and
    ``retries`` is ``0..2``; an unusable count is dropped rather than clamped.
    ``status_code`` is *not* structural — a nonconforming peer can report any
    code — so it is published only inside ``100..599`` and the key is omitted,
    never nulled, otherwise. The content type of the failing response is
    published as a bounded media type, or the static marker when it carries
    none.
    """
    details: dict[str, Any] = {}
    bounded_attempts = _bounded_attempts(attempts)
    if bounded_attempts is not None:
        details["attempts"] = bounded_attempts
        details["retries"] = bounded_attempts - 1
    message = "the search request timed out"
    if isinstance(error, httpx.HTTPStatusError):
        response = getattr(error, "response", None)
        status_code = _bounded_status_code(getattr(response, "status_code", None))
        if status_code is None:
            message = "the search request failed"
        else:
            message = "the search request failed with an HTTP error status"
            details["status_code"] = status_code
        details["content_type"] = _bounded_content_type(_content_type(response))
    return ToolExecutionError(
        message,
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
