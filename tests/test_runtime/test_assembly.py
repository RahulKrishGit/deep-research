"""Tests for assembling a runnable research session from configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

import deep_research.runtime.assembly as assembly
from deep_research.agents.errors import AgentConfigurationError
from deep_research.graph.orchestrator import ResearchAgents
from deep_research.memory.long_term import LongTermMemory
from deep_research.memory.procedural import ProceduralMemory
from deep_research.providers import validate_agent_model_configs
from deep_research.request_budget import RequestBudget
from deep_research.runtime.assembly import (
    AGENT_NAMES,
    ResearchRuntime,
    build_agent,
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
    """A full agent provider that is never called during assembly."""

    def __init__(self) -> None:
        self.calls: list[object] = []

    async def complete_structured(
        self, messages, schema, *, agent_name=None, max_tokens=None
    ):
        self.calls.append((messages, schema, agent_name))
        raise AssertionError("assembly must not call the provider")

    async def complete_react(
        self, messages, tools, *, agent_name=None, max_tokens=None
    ):
        self.calls.append((messages, tools, agent_name))
        raise AssertionError("assembly must not call the provider")


class StructuredOnlyProvider:
    """A provider missing the native tool capability.

    Construction must reject it: an agent that reached its first ReAct
    iteration without ``complete_react`` would fail mid-loop, after the
    session had already begun.
    """

    async def complete_structured(
        self, messages, schema, *, agent_name=None, max_tokens=None
    ):
        raise AssertionError("a rejected provider must never be called")


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


@pytest.mark.parametrize("agent_name", AGENT_NAMES)
def test_a_provider_without_native_react_is_rejected_before_an_agent_runs(
    agent_name,
    tracker,
) -> None:
    """A structured-only provider must fail at construction, not mid-loop."""
    settings = ConfigSettings()
    with pytest.raises(AgentConfigurationError) as caught:
        build_agent(
            agent_name,
            settings,
            tracker=tracker,
            provider=StructuredOnlyProvider(),
            tools=build_tools(
                settings,
                tracker=tracker,
                memory=build_bridge(),
                search_client=FakeSearchClient(),
            ),
            session_id="session-1",
            reputation=None,
        )

    assert "native ReAct" in str(caught.value)


def test_build_agent_matches_production_build_agents_for_every_agent(
    tracker,
) -> None:
    """One agent built alone must be configured exactly like the same agent
    built by production ``build_agents``."""
    settings = ConfigSettings()
    tools = build_tools(
        settings,
        tracker=tracker,
        memory=build_bridge(),
        search_client=FakeSearchClient(),
    )
    provider = RecordingProvider()
    memory = LongTermMemory(
        collection=FakeCollection(), embeddings=FakeEmbeddings()
    )
    production = build_agents(
        settings,
        tracker=tracker,
        provider=provider,
        tools=tools,
        session_id="session-1",
        reputation=memory,
    )

    for name in AGENT_NAMES:
        single = build_agent(
            name,
            settings,
            tracker=tracker,
            provider=provider,
            tools=tools,
            session_id="session-1",
            reputation=memory,
        )
        expected = getattr(production, name)
        assert type(single) is type(expected)
        assert single.name == expected.name
        assert single.config == expected.config
        assert single.provider is provider
        assert single.tracker is tracker
        assert single.toolset.names == expected.toolset.names
        assert single.scratchpad.session_id == "session-1"
        assert single.scratchpad.agent_name == name
        assert single.scratchpad.max_entries == expected.scratchpad.max_entries


def test_build_agent_gives_the_source_evaluator_the_reputation_source(
    tracker,
) -> None:
    memory = LongTermMemory(
        collection=FakeCollection(), embeddings=FakeEmbeddings()
    )
    tools = build_tools(
        ConfigSettings(),
        tracker=tracker,
        memory=build_bridge(),
        search_client=FakeSearchClient(),
    )

    agent = build_agent(
        "source_evaluator",
        ConfigSettings(),
        tracker=tracker,
        provider=RecordingProvider(),
        tools=tools,
        session_id="session-1",
        reputation=memory,
    )

    assert agent._reputation is memory


def test_the_production_researcher_gets_only_read_and_discovery_tools(
    tracker,
) -> None:
    """Four tools, and nothing that writes.

    A finding is not evidence until the Source Evaluator and the Fact Checker
    have judged it, so the agent that gathers one must not be able to keep it
    in long-term memory first. ``build_tools`` still builds
    ``save_to_memory`` for the agents that finalize evidence; this is about
    what the Researcher's own toolset admits.
    """
    settings = ConfigSettings()
    tools = build_tools(
        settings,
        tracker=tracker,
        memory=build_bridge(),
        search_client=FakeSearchClient(),
    )

    agent = build_agent(
        "researcher",
        settings,
        tracker=tracker,
        provider=RecordingProvider(),
        tools=tools,
        session_id="session-1",
        reputation=None,
    )

    assert agent.toolset.names == (
        "web_search",
        "web_scraper",
        "document_reader",
        "query_memory",
    )
    assert "save_to_memory" not in agent.toolset
    assert "save_to_memory" in {tool.name for tool in tools}


def test_the_production_researcher_receives_the_configured_sub_topic_cap(
    tracker,
) -> None:
    """The configured bound must reach the agent, not just the settings object.

    ``agents.max_sub_topics`` exists so a deployment can decide how much of a
    plan one pass attempts; a value nothing passes to the Researcher is a
    knob that silently does nothing.
    """
    settings = ConfigSettings.model_validate(
        {"agents": {"max_sub_topics": 2}}
    )
    tools = build_tools(
        settings,
        tracker=tracker,
        memory=build_bridge(),
        search_client=FakeSearchClient(),
    )

    agent = build_agent(
        "researcher",
        settings,
        tracker=tracker,
        provider=RecordingProvider(),
        tools=tools,
        session_id="session-1",
        reputation=None,
    )

    assert agent._max_sub_topics == 2


def test_an_agent_without_a_sub_topic_cap_is_not_given_one(tracker) -> None:
    """The four sub-topic cap belongs to the Researcher alone."""
    tools = build_tools(
        ConfigSettings(),
        tracker=tracker,
        memory=build_bridge(),
        search_client=FakeSearchClient(),
    )

    for name in (
        "planner",
        "source_evaluator",
        "fact_checker",
        "synthesizer",
        "critic",
    ):
        agent = build_agent(
            name,
            ConfigSettings(),
            tracker=tracker,
            provider=RecordingProvider(),
            tools=tools,
            session_id="session-1",
            reputation=None,
        )
        assert not hasattr(agent, "_max_sub_topics"), name


def test_build_agent_rejects_an_unknown_agent_name(tracker) -> None:
    with pytest.raises(AgentConfigurationError) as caught:
        build_agent(
            "librarian",
            ConfigSettings(),
            tracker=tracker,
            provider=RecordingProvider(),
            tools=[],
            session_id="session-1",
            reputation=None,
        )

    assert "librarian" in str(caught.value)


def test_build_agent_fails_when_a_declared_tool_is_missing(tracker) -> None:
    """Matches ``AgentToolset``: construction fails, not the first tool call."""
    with pytest.raises(AgentConfigurationError):
        build_agent(
            "planner",
            ConfigSettings(),
            tracker=tracker,
            provider=RecordingProvider(),
            tools=[],
            session_id="session-1",
            reputation=None,
        )


def test_build_agents_uses_the_shared_constructor_mapping(
    tracker, monkeypatch
) -> None:
    """Production must go through the same mapping evaluation uses."""
    calls: list[str] = []
    real = assembly.build_agent

    def recording_build_agent(name, settings, **kwargs):
        calls.append(name)
        return real(name, settings, **kwargs)

    monkeypatch.setattr(assembly, "build_agent", recording_build_agent)

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

    assert calls == list(AGENT_NAMES)


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
        "build_embedding_provider",
        lambda *_args, **_kwargs: calls.append("embeddings"),
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
async def test_bad_critic_override_fails_before_the_default_tracker(
    monkeypatch,
) -> None:
    """The non-injected tracker path is preflighted too.

    Leaves ``tracker=None`` so ``Tracker.from_config`` would run before
    validation on the current implementation; an invalid critic-only
    override must fail with the safe provider/agent error before the
    tracker factory is ever called.
    """
    tracker_calls: list[object] = []
    monkeypatch.setattr(
        assembly.Tracker,
        "from_config",
        classmethod(lambda cls, config: tracker_calls.append(config)),
    )
    settings = ConfigSettings.model_validate(
        {"llm": {"model_overrides": {"critic": {"reasoning_effort": "medium"}}}}
    )

    with pytest.raises(ResearchConfigurationError) as caught:
        await build_runtime(settings, session_id="session-1")

    assert caught.value.reason == "provider_unconfigured"
    assert "deepseek" in str(caught.value)
    assert "critic" in str(caught.value)
    assert tracker_calls == []


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
async def test_deepseek_chat_still_builds_the_configured_embedding_provider(
    tracker, tmp_path, monkeypatch
) -> None:
    """Chat and embedding vendor selection are independent of each other.

    Renamed and updated from the branch's
    ``test_deepseek_chat_still_builds_openai_embeddings``: that test
    asserted OpenAI embeddings were built for DeepSeek chat, which assumed
    OpenAI was the only embedding backend. Task 3 makes ``local`` the
    default embedding provider, so the correct assertion is that
    ``build_embedding_provider`` is called with the configured provider and
    model -- ``local``/``text-embedding-3-small`` here -- alongside the
    DeepSeek chat provider, not that OpenAI embeddings are always built.
    """
    built: list[tuple[str, object]] = []
    provider = RecordingProvider()
    embeddings = FakeEmbeddings()
    monkeypatch.setattr(
        assembly,
        "build_chat_provider",
        lambda config, received_tracker, **_kwargs: (
            built.append((config.provider, received_tracker)) or provider
        ),
    )
    monkeypatch.setattr(
        assembly,
        "build_embedding_provider",
        lambda embedding_provider, *, model: (
            built.append(("embedding", embedding_provider, model)) or embeddings
        ),
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

    assert built[0] == ("embedding", "local", "text-embedding-3-small")
    assert built[1] == ("deepseek", tracker)


@pytest.mark.asyncio
async def test_deepseek_key_failure_never_falls_back_to_openai_chat(
    tracker, tmp_path, monkeypatch
) -> None:
    """A DeepSeek construction failure stays failed: no cross-provider fallback."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    factory_calls: list[str] = []
    real_build = assembly.build_chat_provider

    def recording_build(config, received_tracker, **kwargs):
        factory_calls.append(config.provider)
        return real_build(config, received_tracker, **kwargs)

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


@pytest.mark.asyncio
async def test_build_runtime_uses_the_local_embedding_provider_by_default(
    tracker, tmp_path, monkeypatch
) -> None:
    from deep_research.providers import LocalEmbeddingProvider

    captured: list[object] = []

    def recording_from_config(config, *, embeddings, tracker):
        captured.append(embeddings)
        return LongTermMemory(collection=FakeCollection(), embeddings=FakeEmbeddings())

    monkeypatch.setattr(
        "deep_research.runtime.assembly.LongTermMemory.from_config",
        recording_from_config,
    )
    settings = ConfigSettings()
    settings = settings.model_copy(
        update={
            "memory": settings.memory.model_copy(
                update={
                    "long_term": settings.memory.long_term.model_copy(
                        update={"persist_directory": str(tmp_path)}
                    )
                }
            )
        }
    )

    await build_runtime(
        settings,
        session_id="session-1",
        tracker=tracker,
        chat_provider=RecordingProvider(),
        procedural=ProceduralMemory(tmp_path / "strategies.json"),
    )

    assert isinstance(captured[0], LocalEmbeddingProvider)


# --- the run's request budget -------------------------------------------------
#
# One budget per run: the chat transports and the Tavily search tool reserve
# against the same counters, so a declared ceiling bounds the run rather than a
# single collaborator. Every assertion about that budget is an ``is``
# comparison, never ``==``: ``RequestBudget`` holds mutable counters, so a copy
# would spend a *second* set of them and the ceiling would be whatever each
# collaborator happened to be handed.


def _recording_tool_class(real_class, *, captured: list[dict[str, object]]):
    """A tool subclass that records the keyword arguments it was built with."""

    class RecordingTool(real_class):
        def __init__(self, *args, **kwargs):
            captured.append(dict(kwargs))
            super().__init__(*args, **kwargs)

    return RecordingTool


class _BudgetSeams:
    """Capture what ``build_runtime`` hands each seam, without a network.

    ``build_tools`` is wrapped rather than replaced, so the tools that come out
    are the real ones; the provider and embedding factories are replaced by
    doubles that record their arguments and build nothing external.
    """

    TOOL_CLASSES = (
        "WebScraperTool",
        "DocumentReaderTool",
        "QueryMemoryTool",
        "SaveToMemoryTool",
        "WriteDocumentTool",
    )

    def __init__(self, monkeypatch) -> None:
        self.provider: list[object] = []
        self.tools: list[object] = []
        self.search: list[dict[str, object]] = []
        self.embeddings: list[dict[str, object]] = []
        self.other_tools: dict[str, list[dict[str, object]]] = {
            name: [] for name in self.TOOL_CLASSES
        }
        self.constructed: list[object] = []
        constructed = self.constructed
        real_build_tools = assembly.build_tools

        class RecordingRequestBudget(RequestBudget):
            """Counts every budget the wiring constructs."""

            def __init__(self, config=None) -> None:
                super().__init__(config)
                constructed.append(self)

        def recording_build_tools(*args, **kwargs):
            self.tools.append(kwargs.get("request_budget"))
            return real_build_tools(*args, **kwargs)

        def recording_build_chat_provider(*args, **kwargs):
            self.provider.append(kwargs.get("request_budget"))
            return RecordingProvider()

        def recording_build_embedding_provider(provider, *, model, **kwargs):
            self.embeddings.append(
                {"provider": provider, "model": model, **kwargs}
            )
            return FakeEmbeddings()

        # ``raising=False``: a module that has not imported the name yet must
        # fail the behaviour assertions below, not the patch itself.
        monkeypatch.setattr(
            assembly, "RequestBudget", RecordingRequestBudget, raising=False
        )
        monkeypatch.setattr(assembly, "build_tools", recording_build_tools)
        monkeypatch.setattr(
            assembly, "build_chat_provider", recording_build_chat_provider
        )
        monkeypatch.setattr(
            assembly,
            "build_embedding_provider",
            recording_build_embedding_provider,
        )
        monkeypatch.setattr(
            assembly,
            "WebSearchTool",
            _recording_tool_class(assembly.WebSearchTool, captured=self.search),
        )
        for class_name in self.TOOL_CLASSES:
            monkeypatch.setattr(
                assembly,
                class_name,
                _recording_tool_class(
                    getattr(assembly, class_name),
                    captured=self.other_tools[class_name],
                ),
            )


async def _runtime_through_the_seams(tracker, tmp_path, *, settings=None):
    """Build one runtime through the captured seams, with nothing external."""
    return await build_runtime(
        settings
        if settings is not None
        else ConfigSettings.model_validate(
            {"output": {"directory": str(tmp_path)}}
        ),
        session_id="session-1",
        tracker=tracker,
        long_term=LongTermMemory(
            collection=FakeCollection(), embeddings=FakeEmbeddings()
        ),
        procedural=ProceduralMemory(tmp_path / "strategies.json"),
        search_client=FakeSearchClient(),
    )


@pytest.mark.asyncio
async def test_build_runtime_shares_one_request_budget_by_identity(
    tracker, tmp_path, monkeypatch
) -> None:
    """Exactly one budget, and every seam receives that same object."""
    seams = _BudgetSeams(monkeypatch)

    runtime = await _runtime_through_the_seams(tracker, tmp_path)

    assert len(seams.constructed) == 1
    budget = seams.constructed[0]
    assert isinstance(budget, RequestBudget)
    assert len(seams.provider) == 1
    assert len(seams.tools) == 1
    assert len(seams.search) == 1
    assert seams.provider[0] is budget
    assert seams.tools[0] is budget
    assert seams.search[0].get("request_budget") is budget
    assert runtime.request_budget is budget


@pytest.mark.asyncio
async def test_the_configured_request_budget_reaches_the_run_budget(
    tracker, tmp_path, monkeypatch
) -> None:
    """A declared ceiling and stop fraction survive the wiring unchanged."""
    seams = _BudgetSeams(monkeypatch)
    settings = ConfigSettings.model_validate(
        {
            "request_budget": {
                "deepseek_attempt_ceiling": 10,
                "openai_attempt_ceiling": 6,
                "tavily_attempt_ceiling": 4,
                "stop_fraction": 0.5,
            }
        }
    )

    runtime = await _runtime_through_the_seams(
        tracker, tmp_path, settings=settings
    )

    budget = seams.provider[0]
    assert isinstance(budget, RequestBudget)
    assert [
        (snapshot.provider, snapshot.ceiling, snapshot.effective_limit)
        for snapshot in budget.snapshots()
    ] == [("deepseek", 10, 5), ("openai", 6, 3), ("tavily", 4, 2)]
    assert runtime.request_budget is budget


@pytest.mark.asyncio
async def test_only_the_search_tool_receives_the_run_request_budget(
    tracker, tmp_path, monkeypatch
) -> None:
    """The budget belongs to the Tavily transport. No other collaborator gets one."""
    seams = _BudgetSeams(monkeypatch)
    settings = ConfigSettings.model_validate(
        {"request_budget": {"tavily_attempt_ceiling": 3}}
    )

    await _runtime_through_the_seams(tracker, tmp_path, settings=settings)

    assert {
        name: [kwargs.get("request_budget") for kwargs in calls]
        for name, calls in seams.other_tools.items()
    } == dict.fromkeys(_BudgetSeams.TOOL_CLASSES, [None])
    # Embeddings are not transport attempts, so the factory is called with its
    # own two arguments and nothing else.
    assert seams.embeddings == [
        {"provider": "local", "model": "text-embedding-3-small"}
    ]

    assert len(seams.search) == 1
    assert seams.search[0].get("request_budget") is not None
    assert seams.search[0].get("request_budget") is seams.provider[0]
