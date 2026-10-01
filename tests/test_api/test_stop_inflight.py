"""Stop reaches every call in flight (notes-progress-report spec §8.3; D17, D25; AC29).

The real graph runs with stub agents: the researcher's turn holds a provider call and an
async search in flight until it is cancelled, and the publisher records what the finalizer
would write. No provider, no network.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import pytest

from deep_research.agents.base import AgentRun
from deep_research.api.sessions import SessionStore
from deep_research.graph.orchestrator import ResearchAgents, compile_research_graph, run_research_graph
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.runtime.outcome import ResearchOutcome, build_outcome
from deep_research.tools.web_search import WebSearchTool
from deep_research.utils.config import ConfigSettings
from deep_research.utils.types import ResearchState
from tests.graph_fakes import FakePublisher, fake_research_agents


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


class InFlightResearcher:
    """A researcher whose turn holds a provider call and a search in flight until cancelled."""

    name = "researcher"

    def __init__(self, tracker: Tracker) -> None:
        self.search = HangingSearch()
        self.tool = WebSearchTool(tracker, client=self.search)
        self.calling = asyncio.Event()
        self.cancelled_at: float | None = None

    async def run(self, state: ResearchState) -> AgentRun[Any]:
        async def provider_call() -> None:
            self.calling.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled_at = time.monotonic()
                raise

        await asyncio.gather(provider_call(), self.tool.execute(query="grid storage limits"))
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
    """AC29 on the real graph: a stop cancels the running node's provider call and its async
    search within a second; no later node starts and no later agent is called; nothing is
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

    asked = time.monotonic()
    store.stop("s1")
    assert session.task is not None
    await asyncio.wait({session.task}, timeout=1)

    assert session.task.cancelled()
    assert researcher.cancelled_at is not None and researcher.cancelled_at - asked < 1.0
    assert researcher.search.cancelled is True
    await asyncio.sleep(0.2)
    started = [event.metadata["node"] for event in session.events if event.event_type == "graph.node.started"]
    assert started == ["planner", "researcher"]
    assert (session.stopped_step, session.events[-1].event_type) == ("researcher", "session.stopped")
    assert [agents.source_evaluator.calls, agents.evidence_verifier.calls, agents.report_writer.calls] == [[], [], []]
    assert (publisher.report_writes, publisher.memory_writes) == (0, 0)
