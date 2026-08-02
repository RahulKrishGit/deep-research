"""Offline tool doubles for Planner and Researcher agent tests.

Real tool classes are used throughout — only their network clients and
memory backends are faked — so these tests exercise the same tool contracts
the agents will see in production.

Not collected by pytest: the filename does not match ``test_*.py``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from deep_research.observability import Tracker
from deep_research.tools.base import BaseTool
from deep_research.tools.document_reader import DocumentReaderTool
from deep_research.tools.memory_tools import QueryMemoryTool, SaveToMemoryTool
from deep_research.tools.web_scraper import WebScraperTool
from deep_research.tools.web_search import WebSearchTool


class FakeSearchClient:
    """Serve queued Tavily-shaped responses without touching the network."""

    def __init__(
        self,
        responses: Sequence[Mapping[str, Any] | Exception] = (),
    ) -> None:
        self.responses: list[Any] = list(responses)
        self.calls: list[dict[str, object]] = []

    def search(
        self,
        *,
        query: str,
        search_depth: str,
        max_results: int,
    ) -> Mapping[str, Any]:
        self.calls.append({"query": query, "max_results": max_results})
        if not self.responses:
            raise AssertionError("no scripted search response left")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeMemory:
    """In-memory stand-in for the long-term memory backend."""

    def __init__(
        self,
        *,
        entry_id: str = "entry-1",
        matches: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self.entry_id = entry_id
        self.matches = [dict(match) for match in matches]
        self.saved: list[tuple[str, dict[str, Any]]] = []
        self.queried: list[str] = []

    async def save(self, content: str, metadata: Mapping[str, Any]) -> str:
        self.saved.append((content, dict(metadata)))
        return self.entry_id

    async def query(
        self,
        query: str,
        *,
        top_k: int = 5,
        filters: Mapping[str, Any] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        del top_k, filters
        self.queried.append(query)
        return list(self.matches)


def search_response(
    *,
    title: str = "Quantum error correction in 2025",
    url: str = "https://example.test/qec",
    content: str = "Logical error rates fell below break-even.",
) -> dict[str, Any]:
    """One Tavily-shaped response carrying a single result."""
    return {
        "results": [
            {"title": title, "url": url, "content": content, "score": 0.9}
        ]
    }


def page_client(
    *,
    title: str = "Quantum error correction in 2025",
    body: str = "Logical error rates fell below break-even in 2025.",
) -> httpx.AsyncClient:
    """A client serving one permissive robots.txt and one HTML page.

    Backed by ``httpx.MockTransport``, so it holds no sockets and tests do
    not need to close it.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, text="User-agent: *\nAllow: /", request=request
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text=(
                f"<html><head><title>{title}</title></head>"
                f"<body><p>{body}</p></body></html>"
            ),
            request=request,
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def planner_tools(
    tracker: Tracker,
    *,
    search: FakeSearchClient | None = None,
    memory: FakeMemory | None = None,
) -> list[BaseTool]:
    """Build the two tools ``PlannerAgent`` declares, all offline."""
    return [
        QueryMemoryTool(tracker, memory or FakeMemory()),
        WebSearchTool(
            tracker,
            client=search or FakeSearchClient([search_response()]),
        ),
    ]


def research_tools(
    tracker: Tracker,
    *,
    search: FakeSearchClient | None = None,
    memory: FakeMemory | None = None,
    http: httpx.AsyncClient | None = None,
) -> list[BaseTool]:
    """Build the five tools ``ResearcherAgent`` declares, all offline."""
    backend = memory or FakeMemory()
    client = http or page_client()
    return [
        WebSearchTool(
            tracker,
            client=search or FakeSearchClient([search_response()]),
        ),
        WebScraperTool(tracker, client=client),
        DocumentReaderTool(tracker, client=client),
        QueryMemoryTool(tracker, backend),
        SaveToMemoryTool(tracker, backend),
    ]
