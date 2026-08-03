"""Tests for assembling a runnable research session from configuration."""

from __future__ import annotations

import pytest

from deep_research.memory.long_term import LongTermMemory
from deep_research.runtime.assembly import build_tools
from deep_research.runtime.memory_bridge import LongTermMemoryBridge
from deep_research.utils.config import ConfigSettings
from tests.memory_fakes import FakeCollection, FakeEmbeddings
from tests.research_fakes import FakeSearchClient, search_response

EXPECTED_TOOL_NAMES = {
    "web_search",
    "web_scraper",
    "document_reader",
    "query_memory",
    "save_to_memory",
    "write_document",
}


def build_bridge() -> LongTermMemoryBridge:
    memory = LongTermMemory(
        collection=FakeCollection(), embeddings=FakeEmbeddings()
    )
    return LongTermMemoryBridge(memory, session_id="session-1")


def test_build_tools_covers_every_tool_the_agents_declare(tracker) -> None:
    tools = build_tools(
        ConfigSettings(),
        tracker=tracker,
        memory=build_bridge(),
        search_client=FakeSearchClient(),
    )

    assert {tool.name for tool in tools} == EXPECTED_TOOL_NAMES


def test_build_tools_covers_the_union_of_every_agent_allowlist(tracker) -> None:
    """No agent may declare a tool this assembly does not build."""
    from deep_research.agents import (
        CriticAgent,
        FactCheckerAgent,
        PlannerAgent,
        ResearcherAgent,
        SourceEvaluatorAgent,
        SynthesizerAgent,
    )

    declared = {
        name
        for agent in (
            PlannerAgent,
            ResearcherAgent,
            SourceEvaluatorAgent,
            FactCheckerAgent,
            SynthesizerAgent,
            CriticAgent,
        )
        for name in agent.allowed_tools
    }
    tools = build_tools(
        ConfigSettings(),
        tracker=tracker,
        memory=build_bridge(),
        search_client=FakeSearchClient(),
    )

    assert declared <= {tool.name for tool in tools}


@pytest.mark.asyncio
async def test_build_tools_applies_the_tavily_settings(tracker) -> None:
    settings = ConfigSettings.model_validate(
        {"tavily": {"search_depth": "advanced", "max_results": 9}}
    )
    client = FakeSearchClient(responses=[search_response()])

    tools = build_tools(
        settings,
        tracker=tracker,
        memory=build_bridge(),
        search_client=client,
    )
    search = next(tool for tool in tools if tool.name == "web_search")
    async with tracker.session_span("session-1", "a question"):
        result = await search.execute(query="quantum error correction")

    assert result.success, result.error
    assert client.calls == [
        {
            "query": "quantum error correction",
            "search_depth": "advanced",
            "max_results": 9,
        }
    ]


@pytest.mark.asyncio
async def test_build_tools_writes_reports_under_the_configured_directory(
    tracker, tmp_path
) -> None:
    settings = ConfigSettings.model_validate(
        {"output": {"directory": str(tmp_path)}}
    )

    tools = build_tools(
        settings,
        tracker=tracker,
        memory=build_bridge(),
        search_client=FakeSearchClient(),
    )
    writer = next(tool for tool in tools if tool.name == "write_document")
    async with tracker.session_span("session-1", "a question"):
        result = await writer.execute(filename="a-report.md", content="# Hi")

    assert result.success, result.error
    assert (tmp_path / "a-report.md").read_text(encoding="utf-8") == "# Hi"


@pytest.mark.asyncio
async def test_the_memory_tools_are_wired_to_the_bridge(tracker) -> None:
    bridge = build_bridge()
    tools = build_tools(
        ConfigSettings(),
        tracker=tracker,
        memory=bridge,
        search_client=FakeSearchClient(),
    )
    save = next(tool for tool in tools if tool.name == "save_to_memory")

    async with tracker.session_span("session-1", "a question"):
        result = await save.execute(
            content="Break-even was reached in 2025.",
            metadata={"agent_id": "researcher"},
        )

    assert result.success, result.error
