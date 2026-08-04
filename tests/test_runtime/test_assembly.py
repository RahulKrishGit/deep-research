"""Tests for assembling a runnable research session from configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

import deep_research.runtime.assembly as assembly
from deep_research.graph.orchestrator import ResearchAgents
from deep_research.memory.long_term import LongTermMemory
from deep_research.memory.procedural import ProceduralMemory
from deep_research.providers import validate_agent_model_configs
from deep_research.runtime.assembly import (
    AGENT_NAMES,
    ResearchRuntime,
    build_agents,
    build_runtime,
    build_tools,
)
from deep_research.runtime.errors import ResearchConfigurationError
from deep_research.runtime.memory_bridge import LongTermMemoryBridge
from deep_research.utils.config import ConfigSettings, LLMConfig
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


def test_build_tools_does_not_fall_back_to_the_environment_for_a_blank_key(
    tracker, monkeypatch
) -> None:
    """An explicitly supplied empty key is honoured, not treated as absent."""
    monkeypatch.setenv("TAVILY_API_KEY", "env-tavily-key")
    received: list[object] = []

    class RecordingWebSearchTool(assembly.WebSearchTool):
        def __init__(self, *args, **kwargs):
            received.append(kwargs.get("api_key"))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(assembly, "WebSearchTool", RecordingWebSearchTool)

    build_tools(
        ConfigSettings(),
        tracker=tracker,
        memory=build_bridge(),
        tavily_api_key="",
    )

    assert received == [""]


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


def _recording_agent_class(real_class, *, kwarg: str, captured: list[object]):
    """A subclass that records one constructor kwarg, then builds for real.

    Lets a test see exactly what ``build_agents`` handed each agent's
    constructor without touching ``AgentToolset`` or the compiled graph.
    """

    class RecordingAgent(real_class):
        def __init__(self, **kwargs):
            captured.append(kwargs[kwarg])
            super().__init__(**kwargs)

    return RecordingAgent


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


def test_validate_agent_models_resolves_all_six_before_runtime() -> None:
    config = LLMConfig(
        model_overrides={
            "critic": {"model": "deepseek-v4-pro", "reasoning_effort": "max"}
        }
    )

    resolved = validate_agent_model_configs(config, AGENT_NAMES)

    assert tuple(resolved) == AGENT_NAMES
    assert resolved["planner"].effective.model == "deepseek-v4-flash"
    assert resolved["critic"].effective.model == "deepseek-v4-pro"
    assert resolved["critic"].reasoning_effort == "max"


@pytest.mark.asyncio
async def test_bad_critic_override_fails_before_any_runtime_collaborator(
    tracker, monkeypatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        assembly,
        "OpenAIEmbeddingProvider",
        lambda **_kwargs: calls.append("embeddings"),
    )
    settings = ConfigSettings.model_validate(
        {"llm": {"model_overrides": {"critic": {"reasoning_effort": "medium"}}}}
    )

    with pytest.raises(ResearchConfigurationError) as caught:
        await build_runtime(settings, session_id="session-1", tracker=tracker)

    assert caught.value.reason == "provider_unconfigured"
    assert "deepseek" in str(caught.value)
    assert "critic" in str(caught.value)
    assert calls == []


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


class CountingProceduralMemory(ProceduralMemory):
    """A strategy store that counts how many times it was loaded."""

    def __init__(self, path: Path | str) -> None:
        super().__init__(path)
        self.load_calls = 0

    async def load(self) -> None:
        self.load_calls += 1
        await super().load()


@pytest.mark.asyncio
async def test_build_runtime_does_not_reload_an_injected_loaded_store(
    tracker, tmp_path
) -> None:
    """An injected store that is already loaded is left alone."""
    procedural = CountingProceduralMemory(tmp_path / "strategies.json")
    await procedural.load()
    assert procedural.loaded is True

    await build_runtime(
        ConfigSettings.model_validate({"output": {"directory": str(tmp_path)}}),
        session_id="session-1",
        tracker=tracker,
        chat_provider=RecordingProvider(),
        long_term=LongTermMemory(
            collection=FakeCollection(), embeddings=FakeEmbeddings()
        ),
        procedural=procedural,
        search_client=FakeSearchClient(),
    )

    assert procedural.load_calls == 1


@pytest.mark.asyncio
async def test_build_runtime_honours_disabled_checkpointing(
    tracker, tmp_path
) -> None:
    settings = ConfigSettings.model_validate(
        {"output": {"directory": str(tmp_path)}}
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

    assert runtime.graph.checkpointer is None


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
async def test_deepseek_chat_still_builds_openai_embeddings(
    tracker, tmp_path, monkeypatch
) -> None:
    built: list[tuple[str, object]] = []
    provider = RecordingProvider()
    embeddings = FakeEmbeddings()
    monkeypatch.setattr(
        assembly,
        "build_chat_provider",
        lambda config, received_tracker: (
            built.append((config.provider, received_tracker)) or provider
        ),
    )
    monkeypatch.setattr(
        assembly,
        "OpenAIEmbeddingProvider",
        lambda *, model: built.append(("embedding", model)) or embeddings,
    )
    monkeypatch.setattr(
        assembly.LongTermMemory,
        "from_config",
        lambda config, *, embeddings, tracker: LongTermMemory(
            collection=FakeCollection(), embeddings=embeddings
        ),
    )

    await build_runtime(
        ConfigSettings.model_validate(
            {"output": {"directory": str(tmp_path)}}
        ),
        session_id="session-1",
        tracker=tracker,
        procedural=ProceduralMemory(tmp_path / "strategies.json"),
        search_client=FakeSearchClient(),
    )

    assert built[0] == ("embedding", "text-embedding-3-small")
    assert built[1] == ("deepseek", tracker)


@pytest.mark.asyncio
async def test_deepseek_key_failure_never_falls_back_to_openai_chat(
    tracker, tmp_path, monkeypatch
) -> None:
    """A DeepSeek construction failure stays failed: no cross-provider fallback."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    factory_calls: list[str] = []
    real_build = assembly.build_chat_provider

    def recording_build(config, received_tracker):
        factory_calls.append(config.provider)
        return real_build(config, received_tracker)

    monkeypatch.setattr(assembly, "build_chat_provider", recording_build)

    import deep_research.providers.factory as factory_module

    openai_constructed: list[object] = []

    class RecordingOpenAIChatProvider:
        def __init__(self, *_args, **_kwargs):
            openai_constructed.append("openai")

    monkeypatch.setattr(
        factory_module, "OpenAIChatProvider", RecordingOpenAIChatProvider
    )

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
    assert factory_calls == ["deepseek"]
    assert openai_constructed == []


@pytest.mark.asyncio
async def test_build_runtime_reports_a_missing_openai_key_cleanly(
    tracker, tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = ConfigSettings.model_validate(
        {
            "llm": {
                "provider": "openai",
                "model": "gpt-4o",
                "thinking_mode": "disabled",
                "reasoning_effort": "none",
            },
            "output": {"directory": str(tmp_path)},
        }
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


@pytest.mark.asyncio
async def test_build_runtime_reports_an_unreadable_procedural_store_cleanly(
    tracker, tmp_path
) -> None:
    """A store ``load()`` cannot open is a configuration error, not a traceback."""
    unreadable = tmp_path / "strategies.json"
    unreadable.mkdir()  # a directory cannot be read as a file
    settings = ConfigSettings.model_validate(
        {"output": {"directory": str(tmp_path)}}
    )

    with pytest.raises(ResearchConfigurationError) as caught:
        await build_runtime(
            settings,
            session_id="session-1",
            tracker=tracker,
            chat_provider=RecordingProvider(),
            long_term=LongTermMemory(
                collection=FakeCollection(), embeddings=FakeEmbeddings()
            ),
            procedural=ProceduralMemory(unreadable),
            search_client=FakeSearchClient(),
        )

    assert caught.value.reason == "memory_unavailable"
    assert str(caught.value).startswith("Memory could not be initialized")


@pytest.mark.asyncio
async def test_build_runtime_wires_the_raw_memory_not_the_bridge_as_reputation(
    tracker, tmp_path, monkeypatch
) -> None:
    """The source evaluator reads reputations from ``LongTermMemory`` itself.

    The bridge is built in the same function for the memory tools; handing
    it over as the reputation source instead of the raw memory would be a
    silent degradation no call site would complain about.
    """
    received: list[object] = []
    monkeypatch.setattr(
        assembly,
        "SourceEvaluatorAgent",
        _recording_agent_class(
            assembly.SourceEvaluatorAgent,
            kwarg="reputation",
            captured=received,
        ),
    )

    memory = LongTermMemory(
        collection=FakeCollection(), embeddings=FakeEmbeddings()
    )
    settings = ConfigSettings.model_validate(
        {"output": {"directory": str(tmp_path)}}
    )

    await build_runtime(
        settings,
        session_id="session-1",
        tracker=tracker,
        chat_provider=RecordingProvider(),
        long_term=memory,
        procedural=ProceduralMemory(tmp_path / "strategies.json"),
        search_client=FakeSearchClient(),
    )

    assert len(received) == 1
    assert received[0] is memory
    assert received[0] is not None
    assert not isinstance(received[0], LongTermMemoryBridge)


def test_all_six_agents_receive_the_same_shared_tool_registry(
    tracker, monkeypatch
) -> None:
    """One registry for every agent, not six hand-filtered lists.

    ``AgentToolset`` only notices a list that dropped a declared tool; a
    hand-filtered list that drifted sideways would silently change what an
    agent can call. Pinning the constructor wire keeps the six in lockstep.
    """
    received: list[object] = []
    for class_name in (
        "PlannerAgent",
        "ResearcherAgent",
        "SourceEvaluatorAgent",
        "FactCheckerAgent",
        "SynthesizerAgent",
        "CriticAgent",
    ):
        monkeypatch.setattr(
            assembly,
            class_name,
            _recording_agent_class(
                getattr(assembly, class_name), kwarg="tools", captured=received
            ),
        )

    tools = build_tools(
        ConfigSettings(),
        tracker=tracker,
        memory=build_bridge(),
        search_client=FakeSearchClient(),
    )
    build_agents(
        ConfigSettings(),
        tracker=tracker,
        provider=RecordingProvider(),
        tools=tools,
        session_id="session-1",
        reputation=None,
    )

    assert len(received) == 6
    assert all(tool_list is tools for tool_list in received)
    assert {tool.name for tool in received[0]} == EXPECTED_TOOL_NAMES
