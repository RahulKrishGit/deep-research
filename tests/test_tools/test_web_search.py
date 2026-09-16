import json
import time
from collections.abc import Mapping
from typing import Any

import httpx
import pytest

from deep_research.observability import ToolMetric
from deep_research.request_budget import (
    ProviderCategory,
    RequestAttemptLimitError,
    RequestBudget,
    RequestBudgetSnapshot,
)
from deep_research.tools.base import ToolExecutionError, ToolResult
from deep_research.tools.web_search import WebSearchTool
from deep_research.utils.config import RequestBudgetConfig


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


# ---------------------------------------------------------------------------
# Run-wide Tavily attempt ceiling
#
# ``request_budget`` is optional. When it is absent this tool behaves exactly as
# it did before the parameter existed. When it is present, every real
# ``SearchClient.search`` call — the first attempt and every retry of it —
# reserves one ``tavily`` unit *before* that call goes out, so a spent ceiling
# refuses the next attempt instead of letting the retry loop below manufacture
# headroom out of a declaration that was already spent. The budget is real in
# these tests rather than stubbed: the point is that the real primitive is
# reached.
# ---------------------------------------------------------------------------


def _tavily_budget(
    *, ceiling: int | None = None, stop_fraction: float = 1.0
) -> RequestBudget:
    """A real budget whose only declared ceiling is Tavily's."""
    return RequestBudget(
        RequestBudgetConfig(
            deepseek_attempt_ceiling=None,
            openai_attempt_ceiling=None,
            tavily_attempt_ceiling=ceiling,
            stop_fraction=stop_fraction,
        )
    )


class _BudgetAwareSearchClient(FakeSearchClient):
    """A fake client that reads the budget at the moment it is really called."""

    def __init__(
        self, responses: list[Mapping[str, Any] | Exception], budget: RequestBudget
    ) -> None:
        super().__init__(responses)
        self._budget = budget
        self.attempts_at_call: list[int] = []

    def search(
        self,
        *,
        query: str,
        search_depth: str,
        max_results: int,
    ) -> Mapping[str, Any]:
        self.attempts_at_call.append(self._budget.snapshot("tavily").attempts)
        return super().search(
            query=query, search_depth=search_depth, max_results=max_results
        )


@pytest.mark.asyncio
async def test_search_request_budget_reserves_one_tavily_attempt(
    tracker,
) -> None:
    budget = _tavily_budget()
    client = FakeSearchClient([_search_response()])
    tool = WebSearchTool(tracker, client=client, request_budget=budget)

    async with tracker.session_span("session-1", "question"):
        result = await tool.execute(query="topic")

    assert result.success is True
    assert len(client.calls) == 1
    assert budget.snapshot("tavily").attempts == 1


@pytest.mark.asyncio
async def test_search_request_budget_reserves_one_attempt_per_retried_call(
    tracker,
) -> None:
    """Each retried attempt is its own real call, so each reserves its own unit."""
    budget = _tavily_budget()
    client = FakeSearchClient([httpx.TimeoutException("timed out")] * 3)
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    tool = WebSearchTool(
        tracker, client=client, sleep=sleep, request_budget=budget
    )

    async with tracker.session_span("session-1", "question"):
        result = await tool.execute(query="topic")

    assert result.success is False
    assert result.error is not None
    # The retry accounting the tool published before the budget existed is
    # unchanged, and the reservations follow the calls one for one.
    assert result.error.details == {"attempts": 3, "retries": 2}
    assert len(client.calls) == 3
    assert budget.snapshot("tavily").attempts == len(client.calls)
    assert delays == [0.5, 1.0]
    metric = next(
        metric for metric in tracker.metrics if isinstance(metric, ToolMetric)
    )
    assert metric.retry_count == 2


@pytest.mark.asyncio
async def test_search_request_budget_reserves_before_the_client_call(
    tracker,
) -> None:
    """A reserve-after-the-call implementation would observe zero here."""
    budget = _tavily_budget()
    client = _BudgetAwareSearchClient([_search_response()], budget)
    tool = WebSearchTool(tracker, client=client, request_budget=budget)

    async with tracker.session_span("session-1", "question"):
        result = await tool.execute(query="topic")

    assert result.success is True
    assert client.attempts_at_call == [1]
    assert budget.snapshot("tavily").attempts == 1


@pytest.mark.asyncio
async def test_search_request_budget_refuses_the_retry_before_the_next_call(
    tracker,
) -> None:
    """A ceiling of one is spent by the first call, so the retry never leaves."""
    budget = _tavily_budget(ceiling=1)
    client = FakeSearchClient(
        [httpx.TimeoutException("timed out"), _search_response()]
    )
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    tool = WebSearchTool(
        tracker, client=client, sleep=sleep, request_budget=budget
    )
    returned: list[ToolResult] = []

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(RequestAttemptLimitError) as refused:
            returned.append(await tool.execute(query="topic"))

    assert returned == []
    assert len(client.calls) == 1
    assert budget.snapshot("tavily").attempts == 1
    assert refused.value.snapshot.provider == "tavily"
    assert refused.value.snapshot.attempts == 1
    assert refused.value.snapshot.ceiling == 1
    assert refused.value.snapshot.effective_limit == 1
    # The refusal escapes as itself rather than being rebuilt as a tool failure
    # or mistaken for the retryable timeout the first call raised.
    assert not isinstance(refused.value, ToolExecutionError)
    assert delays == [0.5]
    metric = next(
        metric for metric in tracker.metrics if isinstance(metric, ToolMetric)
    )
    assert metric.success is False
    assert metric.error_type == "RequestAttemptLimitError"
    # The retry that was scheduled before the refusal is the only one recorded.
    assert metric.retry_count == 1


@pytest.mark.asyncio
async def test_search_request_budget_refuses_the_first_call_when_the_limit_is_zero(
    tracker,
) -> None:
    """A zero limit is a real declaration: the very first call is refused."""
    budget = _tavily_budget(ceiling=1, stop_fraction=0.5)
    client = FakeSearchClient([_search_response()])
    tool = WebSearchTool(tracker, client=client, request_budget=budget)
    returned: list[ToolResult] = []

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(RequestAttemptLimitError) as refused:
            returned.append(await tool.execute(query="topic"))

    assert returned == []
    assert client.calls == []
    assert refused.value.snapshot.attempts == 0
    assert refused.value.snapshot.effective_limit == 0
    assert budget.snapshot("tavily").attempts == 0
    metric = next(
        metric for metric in tracker.metrics if isinstance(metric, ToolMetric)
    )
    assert metric.error_type == "RequestAttemptLimitError"
    assert metric.retry_count == 0


@pytest.mark.asyncio
async def test_search_request_budget_refusal_survives_a_short_attempt_timeout(
    tracker, monkeypatch
) -> None:
    """A reservation slower than ``timeout_s`` must still refuse, not time out.

    The reservation belongs to the request, not to the cancellable transport
    window. While it runs inside the work item handed to ``asyncio.to_thread``,
    a timeout expiring during ``reserve`` cancels that future, so the refusal
    lands on an already-cancelled future and is dropped: the caller receives a
    timeout-shaped ``ToolExecutionError``, ``BaseTool.execute`` returns a failed
    ``ToolResult``, and the ReAct loop records ``agent_tool_failed`` — the exact
    degradation the run-wide ceiling exists to remove. Because a refusal is a
    statement about the budget rather than a transport event, it must escape
    every timeout on the path regardless of how long ``reserve`` takes.
    """
    # A zero effective limit (``floor(1 * 0.5)``): the very first call is refused.
    budget = _tavily_budget(ceiling=1, stop_fraction=0.5)
    refused_reserve = budget.reserve
    reserves: list[ProviderCategory] = []

    def slow_reserve(provider: ProviderCategory) -> RequestBudgetSnapshot:
        """Reserve as the real one does, but slower than any attempt timeout."""
        reserves.append(provider)
        time.sleep(0.25)
        return refused_reserve(provider)

    monkeypatch.setattr(budget, "reserve", slow_reserve)
    client = FakeSearchClient([_search_response()])
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    tool = WebSearchTool(
        tracker,
        client=client,
        sleep=sleep,
        request_budget=budget,
        timeout_s=0.05,
    )
    returned: list[ToolResult] = []

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(RequestAttemptLimitError) as refused:
            returned.append(await tool.execute(query="topic"))

    assert returned == []
    assert type(refused.value) is RequestAttemptLimitError
    # Not the timeout the short window would have published, and not any other
    # tool failure: the refusal is not a status error and not a timeout.
    assert not isinstance(refused.value, ToolExecutionError)
    assert refused.value.snapshot.provider == "tavily"
    assert refused.value.snapshot.attempts == 0
    assert refused.value.snapshot.ceiling == 1
    assert refused.value.snapshot.effective_limit == 0
    # The refusal reached the budget before any client call, and it ended the
    # request: nothing was charged, nothing was sent, no retry was scheduled.
    assert reserves == ["tavily"]
    assert client.calls == []
    assert budget.snapshot("tavily").attempts == 0
    assert delays == []
    metric = next(
        metric for metric in tracker.metrics if isinstance(metric, ToolMetric)
    )
    assert metric.success is False
    assert metric.error_type == "RequestAttemptLimitError"
    assert metric.retry_count == 0


@pytest.mark.asyncio
async def test_search_without_a_request_budget_stays_uncounted(tracker) -> None:
    """The default is no budget at all: nothing is reserved and nothing refuses."""
    client = FakeSearchClient([_search_response()])
    tool = WebSearchTool(tracker, client=client)

    assert tool._request_budget is None

    async with tracker.session_span("session-1", "question"):
        result = await tool.execute(query="topic")

    assert result.success is True
    assert result.metadata == {
        "provider": "tavily",
        "result_count": 1,
        "retry_count": 0,
    }
    assert len(client.calls) == 1
