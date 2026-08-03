"""Tests for assembling a runnable research session from configuration."""

from __future__ import annotations

import pytest

from deep_research.graph.orchestrator import ResearchAgents
from deep_research.memory.long_term import LongTermMemory
from deep_research.memory.procedural import ProceduralMemory
from deep_research.runtime.assembly import (
    AGENT_NAMES,
    ResearchRuntime,
    build_agents,
    build_runtime,
    build_tools,
)
from deep_research.runtime.errors import ResearchConfigurationError
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


class RecordingProvider:
    """A structured completer that is never called during assembly."""

    def __init__(self) -> None:
        self.calls: list[object] = []

    async def complete_structured(self, messages, schema, *, agent_name=None):
        self.calls.append((messages, schema, agent_name))
        raise AssertionError("assembly must not call the provider")


def test_build_agents_fills_every_slot_with_the_right_agent(tracker) -> None:
    tools = build_tools(
        ConfigSettings(),
        tracker=tracker,
        memory=build_bridge(),
        search_client=FakeSearchClient(),
    )

    agents = build_agents(
        ConfigSettings(),
        tracker=tracker,
        provider=RecordingProvider(),
        tools=tools,
        session_id="session-1",
        reputation=None,
    )

    assert isinstance(agents, ResearchAgents)
    assert AGENT_NAMES == (
        "planner",
        "researcher",
        "source_evaluator",
        "fact_checker",
        "synthesizer",
        "critic",
    )
    assert [
        agents.planner.name,
        agents.researcher.name,
        agents.source_evaluator.name,
        agents.fact_checker.name,
        agents.synthesizer.name,
        agents.critic.name,
    ] == list(AGENT_NAMES)


def test_every_agent_gets_its_own_scratchpad_on_the_shared_session(
    tracker,
) -> None:
    tools = build_tools(
        ConfigSettings(),
        tracker=tracker,
        memory=build_bridge(),
        search_client=FakeSearchClient(),
    )

    agents = build_agents(
        ConfigSettings(),
        tracker=tracker,
        provider=RecordingProvider(),
        tools=tools,
        session_id="session-1",
        reputation=None,
    )

    pads = [
        agents.planner.scratchpad,
        agents.researcher.scratchpad,
        agents.source_evaluator.scratchpad,
        agents.fact_checker.scratchpad,
        agents.synthesizer.scratchpad,
        agents.critic.scratchpad,
    ]
    assert {pad.session_id for pad in pads} == {"session-1"}
    assert [pad.agent_name for pad in pads] == list(AGENT_NAMES)
    assert len({id(pad) for pad in pads}) == 6


def test_build_agents_reports_a_missing_tool_as_a_configuration_failure(
    tracker,
) -> None:
    with pytest.raises(ResearchConfigurationError) as caught:
        build_agents(
            ConfigSettings(),
            tracker=tracker,
            provider=RecordingProvider(),
            tools=[],
            session_id="session-1",
            reputation=None,
        )

    assert caught.value.reason == "agents_misconfigured"


@pytest.mark.asyncio
async def test_build_runtime_compiles_a_graph_from_injected_collaborators(
    tracker, tmp_path
) -> None:
    memory = LongTermMemory(
        collection=FakeCollection(), embeddings=FakeEmbeddings()
    )
    procedural = ProceduralMemory(tmp_path / "strategies.json")
    settings = ConfigSettings.model_validate(
        {"output": {"directory": str(tmp_path)}}
    )

    runtime = await build_runtime(
        settings,
        session_id="session-1",
        tracker=tracker,
        chat_provider=RecordingProvider(),
        long_term=memory,
        procedural=procedural,
        search_client=FakeSearchClient(),
    )

    assert isinstance(runtime, ResearchRuntime)
    assert runtime.session_id == "session-1"
    assert runtime.tracker is tracker
    assert runtime.long_term is memory
    assert runtime.procedural is procedural
    assert procedural.loaded is True
    assert runtime.graph is not None


@pytest.mark.asyncio
async def test_build_runtime_honours_the_checkpointing_setting(
    tracker, tmp_path
) -> None:
    settings = ConfigSettings.model_validate(
        {
            "graph": {"checkpointing_enabled": True},
            "output": {"directory": str(tmp_path)},
        }
    )

    runtime = await build_runtime(
        settings,
        session_id="session-1",
        tracker=tracker,
        chat_provider=RecordingProvider(),
        long_term=LongTermMemory(
            collection=FakeCollection(), embeddings=FakeEmbeddings()
        ),
        procedural=ProceduralMemory(tmp_path / "strategies.json"),
        search_client=FakeSearchClient(),
    )

    assert runtime.graph.checkpointer is not None


@pytest.mark.asyncio
async def test_build_runtime_reports_a_missing_openai_key_cleanly(
    tracker, tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = ConfigSettings.model_validate(
        {"output": {"directory": str(tmp_path)}}
    )

    with pytest.raises(ResearchConfigurationError) as caught:
        await build_runtime(
            settings,
            session_id="session-1",
            tracker=tracker,
            long_term=LongTermMemory(
                collection=FakeCollection(), embeddings=FakeEmbeddings()
            ),
            procedural=ProceduralMemory(tmp_path / "strategies.json"),
            search_client=FakeSearchClient(),
        )

    assert caught.value.reason == "provider_unconfigured"
