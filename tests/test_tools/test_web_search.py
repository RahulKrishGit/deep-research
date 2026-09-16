import json
from collections.abc import Mapping
from typing import Any

import httpx
import pytest

from deep_research.observability import ToolMetric
from deep_research.tools.web_search import WebSearchTool


class FakeSearchClient:
    def __init__(self, responses: list[Mapping[str, Any] | Exception]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def search(
        self,
        *,
        query: str,
        search_depth: str,
        max_results: int,
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "query": query,
                "search_depth": search_depth,
                "max_results": max_results,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _search_response() -> dict[str, object]:
    return {
        "results": [
            {
                "title": "Primary source",
                "url": "https://example.test/source",
                "content": "Relevant excerpt",
                "score": 0.91,
            }
        ]
    }


@pytest.mark.asyncio
async def test_search_normalizes_results_and_honors_call_configuration(tracker) -> None:
    client = FakeSearchClient([_search_response()])
    tool = WebSearchTool(tracker, client=client, search_depth="advanced", max_results=5)

    async with tracker.session_span("session-1", "question"):
        result = await tool.execute(query="topic", max_results=3)

    assert client.calls == [
        {"query": "topic", "search_depth": "advanced", "max_results": 3}
    ]
    assert result.success is True
    assert result.data == {
        "results": [
            {
                "title": "Primary source",
                "url": "https://example.test/source",
                "snippet": "Relevant excerpt",
                "rank": 1,
                "provider_score": 0.91,
            }
        ]
    }
    assert result.metadata == {
        "provider": "tavily",
        "result_count": 1,
        "retry_count": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(("query", "max_results"), [("   ", None), ("topic", 0)])
async def test_search_rejects_invalid_arguments(tracker, query, max_results) -> None:
    client = FakeSearchClient([_search_response()])
    tool = WebSearchTool(tracker, client=client)

    async with tracker.session_span("session-1", "question"):
        result = await tool.execute(query=query, max_results=max_results)

    assert result.success is False
    assert result.error is not None
    assert result.error.type == "ValidationError"
    assert client.calls == []


@pytest.mark.asyncio
async def test_search_retries_rate_limits_using_retry_after(tracker) -> None:
    request = httpx.Request("GET", "https://api.tavily.com/search")
    response = httpx.Response(429, headers={"Retry-After": "2"}, request=request)
    client = FakeSearchClient(
        [
            httpx.HTTPStatusError("rate limited", request=request, response=response),
            _search_response(),
        ]
    )
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    tool = WebSearchTool(tracker, client=client, sleep=sleep)
    async with tracker.session_span("session-1", "question"):
        result = await tool.execute(query="topic")

    assert result.success is True
    assert len(client.calls) == 2
    assert delays == [2.0]
    metric = next(
        metric for metric in tracker.metrics if isinstance(metric, ToolMetric)
    )
    assert metric.success is True
    assert metric.retry_count == 1


@pytest.mark.asyncio
async def test_search_returns_timeout_failure_after_retries(tracker) -> None:
    client = FakeSearchClient([httpx.TimeoutException("timed out")] * 3)
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    tool = WebSearchTool(tracker, client=client, sleep=sleep)
    async with tracker.session_span("session-1", "question"):
        result = await tool.execute(query="topic")

    assert result.success is False
    assert result.error is not None
    assert result.error.type == "TimeoutException"
    assert result.error.details == {"attempts": 3, "retries": 2}
    assert len(client.calls) == 3
    assert delays == [0.5, 1.0]
    metric = next(
        metric for metric in tracker.metrics if isinstance(metric, ToolMetric)
    )
    assert metric.success is False
    assert metric.retry_count == 2


@pytest.mark.asyncio
async def test_search_does_not_retry_client_errors(tracker) -> None:
    request = httpx.Request("GET", "https://api.tavily.com/search")
    response = httpx.Response(400, request=request)
    client = FakeSearchClient(
        [httpx.HTTPStatusError("bad request", request=request, response=response)]
    )
    tool = WebSearchTool(tracker, client=client)

    async with tracker.session_span("session-1", "question"):
        result = await tool.execute(query="topic")

    assert result.success is False
    assert result.error is not None
    assert result.error.type == "HTTPStatusError"
    assert result.error.details == {
        "attempts": 1,
        "retries": 0,
        "status_code": 400,
        "content_type": "unknown",
    }
    assert len(client.calls) == 1


def test_constructor_rejects_invalid_limits(tracker) -> None:
    with pytest.raises(ValueError, match="max_results"):
        WebSearchTool(tracker, client=FakeSearchClient([]), max_results=0)
    with pytest.raises(ValueError, match="timeout_s"):
        WebSearchTool(tracker, client=FakeSearchClient([]), timeout_s=0)
    with pytest.raises(ValueError, match="max_retries"):
        WebSearchTool(tracker, client=FakeSearchClient([]), max_retries=-1)
    with pytest.raises(ValueError, match="max_retries"):
        WebSearchTool(tracker, client=FakeSearchClient([]), max_retries=3)


def test_observability_inputs_exclude_client_and_api_key(tracker) -> None:
    tool = WebSearchTool(tracker, api_key="secret", client=FakeSearchClient([]))

    assert tool._observability_inputs(
        {"query": "topic", "max_results": 3, "api_key": "secret", "client": object()}
    ) == {"query": "topic", "max_results": 3}


# A published failure is one of this module's static sentences and its details
# hold only bounded categorical values, so neither exception text nor a remote
# ``Content-Type`` header can reach public state.
_HOSTILE_SENTINEL = "HOSTILE-SENTINEL-c41f"
_STATIC_TIMEOUT_MESSAGE = "the search request timed out"
_STATIC_HTTP_MESSAGE = "the search request failed with an HTTP error status"
_STATIC_UNCLASSIFIED_MESSAGE = "the search request failed"


def _status_error(
    status_code: int, *, content_type: str | None = None
) -> httpx.HTTPStatusError:
    """A status failure whose message and headers are all hostile text."""
    request = httpx.Request("GET", "https://api.tavily.com/search")
    headers = {} if content_type is None else {"Content-Type": content_type}
    response = httpx.Response(status_code, headers=headers, request=request)
    return httpx.HTTPStatusError(_HOSTILE_SENTINEL, request=request, response=response)


def _serialized(result: Any) -> str:
    return json.dumps(result.model_dump(mode="json"), sort_keys=True)


@pytest.mark.asyncio
async def test_search_timeout_failure_publishes_bounded_static_details(tracker) -> None:
    client = FakeSearchClient([httpx.TimeoutException(_HOSTILE_SENTINEL)] * 3)
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    tool = WebSearchTool(tracker, client=client, sleep=sleep)
    async with tracker.session_span("session-1", "question"):
        result = await tool.execute(query="topic")

    assert result.success is False
    assert result.error is not None
    assert result.error.type == "TimeoutException"
    assert result.error.message == _STATIC_TIMEOUT_MESSAGE
    assert result.error.details == {"attempts": 3, "retries": 2}
    assert _HOSTILE_SENTINEL not in _serialized(result)
    # Retry decisions and call counts are untouched by the bounded diagnostics.
    assert len(client.calls) == 3
    assert delays == [0.5, 1.0]
    metric = next(
        metric for metric in tracker.metrics if isinstance(metric, ToolMetric)
    )
    assert metric.retry_count == 2


@pytest.mark.asyncio
async def test_search_http_error_details_publish_bounded_values(tracker) -> None:
    client = FakeSearchClient(
        [_status_error(404, content_type="Application/JSON; charset=utf-8")]
    )
    tool = WebSearchTool(tracker, client=client)

    async with tracker.session_span("session-1", "question"):
        result = await tool.execute(query="topic")

    assert result.success is False
    assert result.error is not None
    assert result.error.type == "HTTPStatusError"
    assert result.error.message == _STATIC_HTTP_MESSAGE
    assert result.error.details == {
        "attempts": 1,
        "retries": 0,
        "status_code": 404,
        "content_type": "application/json",
    }
    assert _HOSTILE_SENTINEL not in _serialized(result)
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_search_retryable_http_error_keeps_calls_with_bounded_content_type(
    tracker,
) -> None:
    hostile_content_type = f"{_HOSTILE_SENTINEL} <script>alert(1)</script>"
    client = FakeSearchClient(
        [_status_error(503, content_type=hostile_content_type)] * 3
    )
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    tool = WebSearchTool(tracker, client=client, sleep=sleep)
    async with tracker.session_span("session-1", "question"):
        result = await tool.execute(query="topic")

    assert result.success is False
    assert result.error is not None
    assert result.error.type == "HTTPStatusError"
    assert result.error.message == _STATIC_HTTP_MESSAGE
    assert result.error.details == {
        "attempts": 3,
        "retries": 2,
        "status_code": 503,
        "content_type": "unknown",
    }
    assert _HOSTILE_SENTINEL not in _serialized(result)
    assert len(client.calls) == 3
    assert delays == [0.5, 1.0]


@pytest.mark.asyncio
async def test_search_out_of_range_status_code_is_omitted_from_bounded_details(
    tracker,
) -> None:
    client = FakeSearchClient([_status_error(700, content_type="text/plain")])
    tool = WebSearchTool(tracker, client=client)

    async with tracker.session_span("session-1", "question"):
        result = await tool.execute(query="topic")

    assert result.success is False
    assert result.error is not None
    assert result.error.type == "HTTPStatusError"
    assert result.error.message == _STATIC_UNCLASSIFIED_MESSAGE
    assert "status_code" not in result.error.details
    assert None not in result.error.details.values()
    assert result.error.details == {
        "attempts": 1,
        "retries": 0,
        "content_type": "text/plain",
    }
    assert len(client.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_type",
    [
        None,
        _HOSTILE_SENTINEL,
        "",
        "   ",
        "not a media type",
        f"{'a' * 70}/plain",
        "<script>alert(1)</script>",
    ],
    ids=[
        "absent",
        "sentinel",
        "empty",
        "blank",
        "spaces",
        "over-long",
        "markup",
    ],
)
async def test_search_content_type_classification_is_bounded(
    tracker, content_type
) -> None:
    client = FakeSearchClient([_status_error(404, content_type=content_type)])
    tool = WebSearchTool(tracker, client=client)

    async with tracker.session_span("session-1", "question"):
        result = await tool.execute(query="topic")

    assert result.error is not None
    assert result.error.details["content_type"] == "unknown"
    assert _HOSTILE_SENTINEL not in _serialized(result)
    if content_type:
        # A malformed header is mapped to the marker, never truncated or
        # normalised into a valid-looking copy of itself.
        assert content_type not in _serialized(result)


def test_search_failure_details_revalidate_bounded_producer_values() -> None:
    from deep_research.tools.web_search import _tool_execution_error

    for attempt_count in ("3", True, 0, 10, None):
        failure = _tool_execution_error(
            httpx.TimeoutException(_HOSTILE_SENTINEL), attempt_count
        )
        assert str(failure) == _STATIC_TIMEOUT_MESSAGE
        assert failure.details == {}

    request = httpx.Request("GET", "https://api.tavily.com/search")

    class _HostileResponse:
        status_code = "503"
        headers = {"content-type": 12345}

    hostile = _tool_execution_error(
        httpx.HTTPStatusError(
            _HOSTILE_SENTINEL, request=request, response=_HostileResponse()
        ),
        1,
    )
    assert hostile.details == {
        "attempts": 1,
        "retries": 0,
        "content_type": "unknown",
    }

    class _NonAsciiResponse:
        # ``httpx`` refuses to build such a header itself, so this response
        # stands in for a peer that sends one anyway.
        status_code = 404
        headers = {"content-type": "text/h\u212atml"}

    non_ascii = _tool_execution_error(
        httpx.HTTPStatusError(
            _HOSTILE_SENTINEL, request=request, response=_NonAsciiResponse()
        ),
        2,
    )
    assert non_ascii.details == {
        "attempts": 2,
        "retries": 1,
        "status_code": 404,
        "content_type": "unknown",
    }
