"""Stop reaches every call in flight (notes-progress-report spec §8.3; D17, D25; AC29).

The real graph runs with stub agents: the researcher's turn holds a provider call, an
async search and a page fetch shared by two research loops in flight until it is cancelled,
and the publisher records what the finalizer would write. No provider, no network.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

import httpx
import pytest

from deep_research.agents.acquisition import ToolPolicyDecision
from deep_research.agents.base import AgentRun
from deep_research.agents.react import ToolGate, run_react_loop
from deep_research.agents.steps import ReActDecision, ReActStep
from deep_research.agents.toolset import AgentToolset
from deep_research.api.sessions import SessionStore
from deep_research.graph.orchestrator import (
    ResearchAgents,
    compile_research_graph,
    run_research_graph,
)
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.runtime.outcome import ResearchOutcome, build_outcome
from deep_research.tools.web_scraper import WebScraperTool
from deep_research.tools.web_search import WebSearchTool
from deep_research.utils.config import ConfigSettings
from deep_research.utils.types import ResearchState
from tests.agent_fakes import agent_scope, finish, use_tool
from tests.graph_fakes import FakePublisher, fake_research_agents

PAGE_URL = "https://example.test/shared-page"


class HangingSearch:
    """An async search client whose request never answers until it is cancelled."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def search(self, *, query: str, search_depth: str, max_results: int) -> Mapping[str, Any]:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("a hanging search never answers")


class HangingPage:
    """A host whose robots.txt answers and whose one page never does, until cancelled.

    It is the run's shared connection pool (the latency work's ``transport``), so the
    page's download is the in-flight request a stop must reach.
    """

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.page_requests = 0
        self.cancelled_at: float | None = None

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        self.page_requests += 1
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled_at = time.monotonic()
            raise
        raise AssertionError("a hanging page never answers")


class SamePageFlight:
    """The acquisition policy's hooks for this run: a page read is single-flighted on its URL."""

    def flight_key(self, tool_name: str, tool_input: Mapping[str, object]) -> str | None:
        del tool_input
        return PAGE_URL if tool_name == "web_scraper" else None

    async def __call__(
        self, decision: ReActDecision, tool_input: Mapping[str, object] | None = None, *_args: object
    ) -> ToolPolicyDecision:
        del decision, tool_input
        return ToolPolicyDecision()


class InFlightResearcher:
    """A researcher whose turn holds a provider call, a search and a shared page fetch in
    flight until cancelled.

    Two research loops want one page through the run's one ``ToolGate`` (latency audit O4,
    D38): the first one's download is the shared fetch, and the second waits behind it on
    the page's flight lock, so one request is ever in flight (spec §8.3).
    """

    name = "researcher"

    def __init__(self, tracker: Tracker) -> None:
        self.tracker = tracker
        self.search = HangingSearch()
        self.tool = WebSearchTool(tracker, client=self.search)
        self.page = HangingPage()
        self.scraper = WebScraperTool(tracker, transport=httpx.MockTransport(self.page.handler))
        self.gate = ToolGate()
        self.calling = asyncio.Event()
        self.cancelled_at: float | None = None

    async def read_the_shared_page(self, label: str) -> None:
        """One research loop: read ``PAGE_URL`` through the run's gate, then finish."""
        queue = [
            use_tool(f"Read the page for {label}.", "web_scraper", f'{{"url": "{PAGE_URL}"}}'),
            finish(f"{label} is done.", f"{label} answer."),
        ]

        async def decide(iteration: int, steps: Sequence[ReActStep]) -> tuple[ReActDecision, ...]:
            del iteration, steps
            return (queue.pop(0),)

        async with agent_scope(self.tracker):
            await run_react_loop(
                agent_name="researcher", tracker=self.tracker,
                tools=AgentToolset([self.scraper], allowed=["web_scraper"]),
                decide=decide, max_iterations=4, tool_budget=4,
                tool_policy=SamePageFlight(), tool_lock=self.gate,
            )

    async def run(self, state: ResearchState) -> AgentRun[Any]:
        async def provider_call() -> None:
            self.calling.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled_at = time.monotonic()
                raise

        await asyncio.gather(
            provider_call(), self.tool.execute(query="grid storage limits"),
            self.read_the_shared_page("loop A"), self.read_the_shared_page("loop B"),
        )
        raise AssertionError("a turn held in flight never returns")


def _graph_runner(agents: ResearchAgents, tracker: Tracker) -> Callable[..., Awaitable[ResearchOutcome]]:
    """The ``ResearchRunner`` shape over the real graph, compiled from ``agents``."""

    async def runner(*, question: str, session_id: str, event_handler: Any = None, **_: Any) -> ResearchOutcome:
        run = await run_research_graph(
            graph=compile_research_graph(agents), tracker=tracker, session_id=session_id,
            question=question, event_handler=event_handler,
        )
        return build_outcome(run, metrics=())

    return runner


@pytest.mark.asyncio
async def test_stop_cancels_inflight_calls() -> None:
    """AC29 on the real graph: a stop cancels the running node's provider call, its async
    search and the page fetch two research loops share through the run's ToolGate within a
    second; no later node starts and no later agent is called; nothing is
    published, so no file and no memory entry is written (§8.3)."""
    tracker = Tracker(LangSmithRuntimeConfig(tracing_enabled=False, project="stop-tests", api_key=None))
    researcher = InFlightResearcher(tracker)
    publisher = FakePublisher()
    agents = fake_research_agents(researcher=researcher, publisher=publisher)
    store = SessionStore(runner=_graph_runner(agents, tracker))
    store.start(
        session_id="s1", query="What limits grid-scale battery storage?", max_extra_passes=None,
        output_format="markdown", config_overrides={}, config_path="config.yaml", settings=ConfigSettings(),
    )
    session = store.require("s1")
    await asyncio.wait_for(researcher.calling.wait(), timeout=5)
    await asyncio.wait_for(researcher.search.started.wait(), timeout=5)
    await asyncio.wait_for(researcher.page.started.wait(), timeout=5)
    await asyncio.sleep(0.1)  # both loops have reached the page's section; one request is out

    asked = time.monotonic()
    store.stop("s1")
    assert session.task is not None
    await asyncio.wait({session.task}, timeout=1)

    assert session.task.cancelled()
    assert researcher.cancelled_at is not None and researcher.cancelled_at - asked < 1.0
    assert researcher.search.cancelled is True
    # The shared fetch is cancelled with the run, within a second of the stop, and it was one
    # download for the two loops that wanted the page (D38; spec §8.3).
    assert researcher.page.page_requests == 1
    assert researcher.page.cancelled_at is not None and researcher.page.cancelled_at - asked < 1.0
    # The cancellation released the page's flight lock: it can be taken again at once.
    async with asyncio.timeout(1):
        async with researcher.gate.flight(PAGE_URL):
            pass
    await asyncio.sleep(0.2)
    started = [event.metadata["node"] for event in session.events if event.event_type == "graph.node.started"]
    assert started == ["planner", "researcher"]
    assert (session.stopped_step, session.events[-1].event_type) == ("researcher", "session.stopped")
    assert [agents.source_evaluator.calls, agents.evidence_verifier.calls, agents.report_writer.calls] == [[], [], []]
    assert (publisher.report_writes, publisher.memory_writes) == (0, 0)
