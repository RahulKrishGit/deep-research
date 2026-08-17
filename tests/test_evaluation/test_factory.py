"""The evaluation factory must build exactly what production builds."""

from __future__ import annotations

import pytest

import deep_research.runtime.assembly as assembly
from deep_research.evaluation.factory import (
    AgentConstructionError,
    build_evaluation_agent,
    evaluation_session_id,
)
from deep_research.evaluation.models import AGENT_NAMES
from deep_research.memory.long_term import LongTermMemory
from deep_research.runtime.assembly import build_agents, build_tools
from deep_research.runtime.memory_bridge import LongTermMemoryBridge
from tests.memory_fakes import FakeCollection, FakeEmbeddings
from tests.research_fakes import FakeSearchClient


class RecordingProvider:
    """A structured completer construction must never call."""

    async def complete_structured(self, messages, schema, *, agent_name=None):
        raise AssertionError("construction must not call the provider")


def build_tool_registry(tracker, settings):
    memory = LongTermMemory(
        collection=FakeCollection(), embeddings=FakeEmbeddings()
    )
    return build_tools(
        settings,
        tracker=tracker,
        memory=LongTermMemoryBridge(memory, session_id="session-1"),
        tavily_api_key="",
        search_client=FakeSearchClient(),
    )


@pytest.mark.parametrize("agent_name", AGENT_NAMES)
def test_the_factory_matches_production_build_agents(
    tracker, settings, runtime_config_for, agent_name
) -> None:
    provider = RecordingProvider()
    tools = build_tool_registry(tracker, settings)
    production = getattr(
        build_agents(
            settings,
            tracker=tracker,
            provider=provider,
            tools=tools,
            session_id="session-1",
            reputation=None,
        ),
        agent_name,
    )

    build = build_evaluation_agent(
        runtime_config_for(agent_name),
        settings,
        tracker=tracker,
        provider=provider,
        tools=tools,
        session_id="session-1",
        reputation=None,
    )

    assert type(build.agent) is type(production)
    assert build.agent.name == production.name
    assert build.agent.config == production.config
    assert build.agent.toolset.names == production.toolset.names
    assert build.agent.provider is provider
    assert build.agent.tracker is tracker
    assert build.agent.scratchpad.agent_name == agent_name
    assert (
        build.agent.scratchpad.max_entries
        == production.scratchpad.max_entries
    )


def test_the_factory_builds_one_agent_not_six(
    tracker, settings, runtime_config_for, monkeypatch
) -> None:
    """Assembling six agents to test one is exactly what the spec forbids."""
    built: list[str] = []
    real = assembly.build_agent

    def recording(name, config, **kwargs):
        built.append(name)
        return real(name, config, **kwargs)

    monkeypatch.setattr(
        "deep_research.evaluation.factory.build_agent", recording
    )

    build_evaluation_agent(
        runtime_config_for("critic"),
        settings,
        tracker=tracker,
        provider=RecordingProvider(),
        tools=build_tool_registry(tracker, settings),
        session_id="session-1",
        reputation=None,
    )

    assert built == ["critic"]


def test_a_missing_declared_tool_fails_at_construction(
    tracker, settings, runtime_config_for
) -> None:
    with pytest.raises(AgentConstructionError) as caught:
        build_evaluation_agent(
            runtime_config_for("planner"),
            settings,
            tracker=tracker,
            provider=RecordingProvider(),
            tools=[],
            session_id="session-1",
            reputation=None,
        )

    assert caught.value.reason == "agent_unbuildable"
    assert "planner" in str(caught.value)


def test_the_source_evaluator_receives_the_reputation_source(
    tracker, settings, runtime_config_for
) -> None:
    memory = LongTermMemory(
        collection=FakeCollection(), embeddings=FakeEmbeddings()
    )

    build = build_evaluation_agent(
        runtime_config_for("source_evaluator"),
        settings,
        tracker=tracker,
        provider=RecordingProvider(),
        tools=build_tool_registry(tracker, settings),
        session_id="session-1",
        reputation=memory,
    )

    assert build.agent._reputation is memory


def test_session_ids_are_unique_per_case_and_repetition(
    runtime_config_for,
) -> None:
    runtime = runtime_config_for("planner")

    first = evaluation_session_id(
        runtime, case_id="focused-decomposition", repetition=1
    )
    second = evaluation_session_id(
        runtime, case_id="focused-decomposition", repetition=2
    )

    assert first != second
    assert first.startswith("evaluation-")
    assert "focused-decomposition" in first
    assert runtime.experiment_name in first
