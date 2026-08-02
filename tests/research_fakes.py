"""Offline tool doubles for Planner and Researcher agent tests.

Real tool classes are used throughout — only their network clients and
memory backends are faked — so these tests exercise the same tool contracts
the agents will see in production.

Not collected by pytest: the filename does not match ``test_*.py``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

from deep_research.memory.entries import SourceReputation
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
        self.calls.append(
            {
                "query": query,
                "search_depth": search_depth,
                "max_results": max_results,
            }
        )
        if not self.responses:
            raise AssertionError("no scripted search response left")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeMemory:
    """In-memory stand-in for the long-term memory backend.

    ``matches`` fixes what ``query`` returns. When it is left unset,
    ``query`` instead falls back to what ``save`` has recorded so far
    (rendered as ``{"content": ..., "metadata": ...}`` entries) — enough for
    a "save a finding then recall it" test against a shared backend, but not
    a real similarity search: it ignores the query text and always returns
    every saved entry.
    """

    def __init__(
        self,
        *,
        entry_id: str = "entry-1",
        matches: Sequence[Mapping[str, Any]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.entry_id = entry_id
        self.matches = None if matches is None else [dict(match) for match in matches]
        self.error = error
        self.saved: list[tuple[str, dict[str, Any]]] = []
        self.queried: list[str] = []

    async def save(self, content: str, metadata: Mapping[str, Any]) -> str:
        if self.error is not None:
            raise self.error
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
        if self.error is not None:
            raise self.error
        self.queried.append(query)
        if self.matches is not None:
            return list(self.matches)
        return [
            {"content": content, "metadata": metadata}
            for content, metadata in self.saved
        ]


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
    """A client serving one permissive robots.txt and content-type-aware pages.

    A request path with no suffix (what ``WebScraperTool`` tests exercise)
    gets the existing HTML page. A path ending ``.csv``, ``.json``, or
    ``.md``/``.markdown`` gets a body and content type ``DocumentReaderTool``
    can parse, so ``research_tools()``'s default ``document_reader`` can
    succeed against this same client.

    Backed by ``httpx.MockTransport``, so it holds no sockets and tests do
    not need to close it.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, text="User-agent: *\nAllow: /", request=request
            )
        suffix = Path(request.url.path).suffix.lower()
        if suffix == ".csv":
            return httpx.Response(
                200,
                headers={"Content-Type": "text/csv; charset=utf-8"},
                text=f"title,body\n{title},{body}\n",
                request=request,
            )
        if suffix == ".json":
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                text=json.dumps({"title": title, "body": body}),
                request=request,
            )
        if suffix in {".md", ".markdown"}:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/markdown; charset=utf-8"},
                text=f"# {title}\n\n{body}\n",
                request=request,
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


def fact_checker_tools(
    tracker: Tracker,
    *,
    search: FakeSearchClient | None = None,
    memory: FakeMemory | None = None,
    http: httpx.AsyncClient | None = None,
) -> list[BaseTool]:
    """Build the four tools ``FactCheckerAgent`` declares, all offline.

    No ``save_to_memory``: the Fact Checker reads evidence and never
    writes findings.
    """
    client = http or page_client()
    return [
        WebSearchTool(
            tracker,
            client=search or FakeSearchClient([search_response()]),
        ),
        WebScraperTool(tracker, client=client),
        DocumentReaderTool(tracker, client=client),
        QueryMemoryTool(tracker, memory or FakeMemory()),
    ]


class FakeReputationSource:
    """Serve remembered source reputations without a vector store.

    ``error`` makes every lookup raise, which is how the "reputation
    lookup failed, keep scoring directly" path is exercised.
    """

    def __init__(
        self,
        *,
        reputations: Mapping[str, float] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.reputations = dict(reputations or {})
        self.error = error
        self.queried: list[str] = []

    async def get_source_reputation(self, url: str) -> SourceReputation | None:
        self.queried.append(url)
        if self.error is not None:
            raise self.error
        score = self.reputations.get(url)
        if score is None:
            return None
        return SourceReputation(
            url=url,
            title=url,
            reputation_score=score,
            observations=3,
            notes="",
        )
