"""Assemble the providers, tools, agents, and graph one session runs on.

The wiring root. Every collaborator a research session needs is constructed
here from a loaded ``ConfigSettings``, and every external client is
injectable so this module can be tested without an API key or a network.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from deep_research.agents.base import StructuredCompleter
from deep_research.agents.critic import CriticAgent
from deep_research.agents.errors import AgentConfigurationError
from deep_research.agents.fact_checker import FactCheckerAgent
from deep_research.agents.planner import PlannerAgent
from deep_research.agents.researcher import ResearcherAgent
from deep_research.agents.source_evaluator import (
    ReputationSource,
    SourceEvaluatorAgent,
)
from deep_research.agents.synthesizer import SynthesizerAgent
from deep_research.graph.orchestrator import (
    ResearchAgents,
    build_checkpointer,
    compile_research_graph,
)
from deep_research.memory.errors import MemoryInitializationError
from deep_research.memory.long_term import LongTermMemory
from deep_research.memory.procedural import ProceduralMemory
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import (
    OpenAIEmbeddingProvider,
    ProviderConfigurationError,
    build_chat_provider,
    validate_agent_model_configs,
)
from deep_research.runtime.errors import configuration_error
from deep_research.runtime.memory_bridge import LongTermMemoryBridge
from deep_research.tools.base import BaseTool
from deep_research.tools.document_reader import DocumentReaderTool
from deep_research.tools.memory_tools import QueryMemoryTool, SaveToMemoryTool
from deep_research.tools.web_scraper import WebScraperTool
from deep_research.tools.web_search import WebSearchTool
from deep_research.tools.write_document import WriteDocumentTool
from deep_research.utils.config import ConfigSettings

TAVILY_API_KEY_VARIABLE = "TAVILY_API_KEY"


def build_tools(
    settings: ConfigSettings,
    *,
    tracker: Tracker,
    memory: LongTermMemoryBridge,
    tavily_api_key: str | None = None,
    search_client: Any | None = None,
    http_client: Any | None = None,
) -> list[BaseTool]:
    """Build every tool any agent declares, in one shared registry.

    One registry for all six agents rather than a per-agent subset:
    ``AgentToolset`` already selects the names an agent declares and
    ignores the rest, and it raises ``AgentConfigurationError`` when a
    declared tool was never injected — so the wiring guard is kept without
    six lists to keep in step.
    """
    return [
        WebSearchTool(
            tracker,
            api_key=(
                tavily_api_key
                if tavily_api_key is not None
                else os.getenv(TAVILY_API_KEY_VARIABLE)
            ),
            client=search_client,
            search_depth=settings.tavily.search_depth,
            max_results=settings.tavily.max_results,
        ),
        WebScraperTool(tracker, client=http_client),
        DocumentReaderTool(tracker, client=http_client),
        QueryMemoryTool(tracker, memory),
        SaveToMemoryTool(tracker, memory),
        WriteDocumentTool(tracker, settings.output.directory),
    ]


# The six agents, in graph order. Equal to ``graph.state.NODE_NAMES[:6]``
# by construction: node names deliberately equal agent names.
AGENT_NAMES = (
    "planner",
    "researcher",
    "source_evaluator",
    "fact_checker",
    "synthesizer",
    "critic",
)


def _scratchpad(
    settings: ConfigSettings,
    *,
    session_id: str,
    agent_name: str,
) -> ScratchpadMemory:
    return ScratchpadMemory.from_config(
        settings.memory.short_term,
        session_id=session_id,
        agent_name=agent_name,
    )


# Keyed by the six canonical agent names. Every entry receives the identical
# shared kwargs; only the Source Evaluator consumes ``reputation``. Bodies
# name the agent classes rather than capturing them, so a test that patches
# a class on this module still sees its own class constructed.
_AGENT_CONSTRUCTORS: dict[str, Callable[..., Any]] = {
    "planner": lambda reputation, **shared: PlannerAgent(**shared),
    "researcher": lambda reputation, **shared: ResearcherAgent(**shared),
    "source_evaluator": lambda reputation, **shared: SourceEvaluatorAgent(
        reputation=reputation, **shared
    ),
    "fact_checker": lambda reputation, **shared: FactCheckerAgent(**shared),
    "synthesizer": lambda reputation, **shared: SynthesizerAgent(**shared),
    "critic": lambda reputation, **shared: CriticAgent(**shared),
}


def build_agent(
    name: str,
    settings: ConfigSettings,
    *,
    tracker: Tracker,
    provider: StructuredCompleter,
    tools: Sequence[BaseTool],
    session_id: str,
    reputation: ReputationSource | None,
) -> Any:
    """Construct exactly one production-configured agent.

    The single place any agent is wired. ``build_agents`` calls it six
    times; the evaluation harness calls it once. Sharing the mapping is
    what keeps evaluation from drifting away from production wiring.

    ``AgentConfigurationError`` is raised, not converted: the graph path
    wants a ``ResearchConfigurationError`` and converts in ``build_agents``,
    while evaluation preflight wants the raw error.
    """
    constructor = _AGENT_CONSTRUCTORS.get(name)
    if constructor is None:
        valid = ", ".join(AGENT_NAMES)
        raise AgentConfigurationError(
            f"unknown agent name {name!r}; expected one of: {valid}"
        )
    return constructor(
        reputation=reputation,
        provider=provider,
        tracker=tracker,
        tools=tools,
        config=settings.agents,
        scratchpad=_scratchpad(
            settings, session_id=session_id, agent_name=name
        ),
    )


def build_agents(
    settings: ConfigSettings,
    *,
    tracker: Tracker,
    provider: StructuredCompleter,
    tools: Sequence[BaseTool],
    session_id: str,
    reputation: ReputationSource | None,
) -> ResearchAgents:
    """Construct the six agents one graph runs.

    A tool an agent declares but nobody injected is an
    ``AgentConfigurationError`` raised at construction, not a failure
    deferred to the first tool call. That is converted into a
    ``ResearchConfigurationError`` so the CLI can print it without a
    traceback.
    """
    try:
        return ResearchAgents(
            **{
                name: build_agent(
                    name,
                    settings,
                    tracker=tracker,
                    provider=provider,
                    tools=tools,
                    session_id=session_id,
                    reputation=reputation,
                )
                for name in AGENT_NAMES
            }
        )
    except AgentConfigurationError as error:
        raise configuration_error(
            reason="agents_misconfigured",
            message=f"The research agents could not be assembled: {error}",
        ) from error


@dataclass(frozen=True, slots=True)
class ResearchRuntime:
    """One session's compiled graph and the collaborators that outlive it."""

    session_id: str
    settings: ConfigSettings
    tracker: Tracker
    graph: Any
    long_term: LongTermMemory | None
    procedural: ProceduralMemory | None


async def build_runtime(
    settings: ConfigSettings,
    *,
    session_id: str,
    tracker: Tracker | None = None,
    chat_provider: StructuredCompleter | None = None,
    long_term: LongTermMemory | None = None,
    procedural: ProceduralMemory | None = None,
    tavily_api_key: str | None = None,
    search_client: Any | None = None,
    http_client: Any | None = None,
) -> ResearchRuntime:
    """Build everything one research session needs, or fail cleanly.

    Every external collaborator is injectable so this whole path is
    testable without an API key, a network, or ChromaDB. Anything that can
    only go wrong at setup time — a missing key, an unopenable vector
    store, a tool an agent declares but nobody built — becomes a
    ``ResearchConfigurationError`` here rather than an exception the user
    sees as a traceback.
    """
    try:
        validate_agent_model_configs(settings.llm, AGENT_NAMES)
    except ProviderConfigurationError as error:
        raise configuration_error(
            reason="provider_unconfigured",
            message=(
                f"The selected {settings.llm.provider} chat provider is not "
                f"configured: {error}"
            ),
        ) from error

    tracker = tracker or Tracker.from_config(settings.langsmith)

    try:
        if long_term is None:
            long_term = LongTermMemory.from_config(
                settings.memory.long_term,
                embeddings=OpenAIEmbeddingProvider(
                    model=settings.llm.embedding_model
                ),
                tracker=tracker,
            )
        if procedural is None:
            procedural = ProceduralMemory.from_config(
                settings.memory.procedural, tracker=tracker
            )
            await procedural.load()
        elif not procedural.loaded:
            await procedural.load()
    except MemoryInitializationError as error:
        raise configuration_error(
            reason="memory_unavailable",
            message=f"Memory could not be initialized: {error}",
        ) from error

    try:
        provider = chat_provider or build_chat_provider(settings.llm, tracker)
    except ProviderConfigurationError as error:
        raise configuration_error(
            reason="provider_unconfigured",
            message=(
                f"The selected {settings.llm.provider} chat provider is not "
                f"configured: {error}"
            ),
        ) from error

    bridge = LongTermMemoryBridge(long_term, session_id=session_id)
    tools = build_tools(
        settings,
        tracker=tracker,
        memory=bridge,
        tavily_api_key=tavily_api_key,
        search_client=search_client,
        http_client=http_client,
    )
    agents = build_agents(
        settings,
        tracker=tracker,
        provider=provider,
        tools=tools,
        session_id=session_id,
        reputation=long_term,
    )
    graph = compile_research_graph(
        agents,
        checkpointer=build_checkpointer(
            enabled=settings.graph.checkpointing_enabled
        ),
    )
    return ResearchRuntime(
        session_id=session_id,
        settings=settings,
        tracker=tracker,
        graph=graph,
        long_term=long_term,
        procedural=procedural,
    )
