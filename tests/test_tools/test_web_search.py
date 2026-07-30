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
    assert result.error.details == {"attempts": 3}
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
    assert result.error.details == {"attempts": 1, "status_code": 400}
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
