"""Assemble the providers, tools, agents, and graph one session runs on.

The wiring root. Every collaborator a research session needs is constructed
here from a loaded ``ConfigSettings``, and every external client is
injectable so this module can be tested without an API key or a network.
"""

from __future__ import annotations

import os
from collections.abc import Callable, MutableMapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from deep_research.agents.base import AgentCompleter
from deep_research.agents.critic import CriticAgent
from deep_research.agents.errors import AgentConfigurationError
from deep_research.agents.fact_checker import FactCheckerAgent
from deep_research.agents.planner import Clock, PlannerAgent
from deep_research.agents.report_review import REPORT_JUDGE_ROLE, ReportReviewer
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
    ProviderConfigurationError,
    build_chat_provider,
    build_embedding_provider,
    validate_agent_model_configs,
)
from deep_research.request_budget import RequestBudget
from deep_research.runtime.errors import configuration_error
from deep_research.runtime.memory_bridge import LongTermMemoryBridge
from deep_research.tools.base import BaseTool
from deep_research.tools.document_reader import DocumentReaderTool
from deep_research.tools.memory_tools import QueryMemoryTool, SaveToMemoryTool
from deep_research.tools.web_scraper import WebScraperTool
from deep_research.tools.web_search import WebSearchTool
from deep_research.tools.write_document import WriteDocumentTool
from deep_research.utils.config import SERVICE_ROLE_NAMES, ConfigSettings
from deep_research.utils.types import ReadRecord

TAVILY_API_KEY_VARIABLE = "TAVILY_API_KEY"


def build_tools(
    settings: ConfigSettings,
    *,
    tracker: Tracker,
    memory: LongTermMemoryBridge,
    tavily_api_key: str | None = None,
    search_client: Any | None = None,
    http_client: Any | None = None,
    request_budget: RequestBudget | None = None,
) -> list[BaseTool]:
    """Build every tool any agent declares, in one shared registry.

    One registry for all six agents rather than a per-agent subset:
    ``AgentToolset`` already selects the names an agent declares and
    ignores the rest, and it raises ``AgentConfigurationError`` when a
    declared tool was never injected — so the wiring guard is kept without
    six lists to keep in step.

    ``request_budget`` goes to ``WebSearchTool`` and nowhere else. Tavily is
    the only transport behind these six tools, and the remaining five make no
    request at all, so handing any of them a budget would create the
    impression of a bound that does not exist. The budget is shared, never
    copied: ``RequestBudget`` holds mutable counters, and a copy per tool
    would spend a second set of them.
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
            request_budget=request_budget,
        ),
        WebScraperTool(tracker, client=http_client),
        DocumentReaderTool(tracker, client=http_client),
        QueryMemoryTool(tracker, memory),
        SaveToMemoryTool(tracker, memory),
        WriteDocumentTool(tracker, settings.output.directory),
    ]


# The six agents, in graph order. Equal to ``graph.state.NODE_NAMES[:6]``
# by construction: node names deliberately equal agent names. The service roles
# below are deliberately not in this tuple — they are not agents, they hold no
# ReAct loop, and no consumer that means "the agents that research" should pick
# one up.
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
# shared kwargs, apart from two that only some constructors accept:
# ``read_cache``, which only the Researcher consumes, and ``clock``, which goes
# to the three agents named in ``_CLOCK_AWARE_AGENTS`` below. Only the Source
# Evaluator consumes ``reputation``. Bodies name the agent classes rather than
# capturing them, so a test that patches a class on this module still sees its
# own class constructed.
#
# The Researcher is the one agent with a cap on how much of the plan one pass
# attempts, and it reads that bound off the very ``AgentRuntimeConfig`` every
# constructor already receives rather than through a second, Researcher-only
# kwarg. Keeping it there means ``agents.max_sub_topics`` cannot reach five
# agents and silently skip the sixth.
_AGENT_CONSTRUCTORS: dict[str, Callable[..., Any]] = {
    "planner": lambda reputation, **shared: PlannerAgent(**shared),
    "researcher": lambda reputation, **shared: ResearcherAgent(
        max_sub_topics=shared["config"].max_sub_topics,
        selected_passages_per_read=shared["config"].selected_passages_per_read,
        evidence_packet_chars=shared["config"].evidence_packet_chars,
        **shared,
    ),
    "source_evaluator": lambda reputation, **shared: SourceEvaluatorAgent(
        reputation=reputation, **shared
    ),
    "fact_checker": lambda reputation, **shared: FactCheckerAgent(
        max_claims=shared["config"].claim_batch_size,
        batches_per_pass=shared["config"].claim_batches_per_pass,
        **shared,
    ),
    "synthesizer": lambda reputation, **shared: SynthesizerAgent(**shared),
    "critic": lambda reputation, **shared: CriticAgent(**shared),
}

# The three agents whose constructors read the run's clock: the planner dates
# the answer contract from it, the researcher stamps every read and finding
# from it, and the synthesizer stamps the reader's ``Generated on`` line from
# it. A caller that injects one clock therefore gets one run with one clock in
# it, and a run whose dates must not move with the machine's can be pinned. The
# other three hold no clock at all — handing them one would be a keyword no
# constructor accepts. An agent that grows a ``clock`` parameter belongs here.
_CLOCK_AWARE_AGENTS = frozenset({"planner", "researcher", "synthesizer"})


def build_agent(
    name: str,
    settings: ConfigSettings,
    *,
    tracker: Tracker,
    provider: AgentCompleter,
    tools: Sequence[BaseTool],
    session_id: str,
    reputation: ReputationSource | None,
    read_cache: MutableMapping[str, ReadRecord] | None = None,
    clock: Clock | None = None,
) -> Any:
    """Construct exactly one production-configured agent.

    The single place any agent is wired. ``build_agents`` calls it six
    times; the evaluation harness calls it once. Sharing the mapping is
    what keeps evaluation from drifting away from production wiring.

    ``read_cache`` is source-cache state the caller already holds — bodies an
    earlier session read, keyed by URL. Only the Researcher looks a URL up
    before downloading it, so only the Researcher is handed the registry; the
    other five never fetch a body and a cache they cannot consult would be a
    parameter with no meaning.

    ``clock`` is the run's clock, read by the three agents that stamp a date or
    a time (``_CLOCK_AWARE_AGENTS``). ``None`` leaves each of them on its own
    wall-clock default, which is what the production entrypoint wants; a caller
    that pins it — the replay harness is the one that does — gets a run whose
    dates are that caller's rather than the machine's.

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
    shared: dict[str, Any] = {
        "provider": provider,
        "tracker": tracker,
        "tools": tools,
        "config": settings.agents,
        # The resolved profile, not the raw ``llm`` mapping: every per-call
        # configuration fingerprint then carries the model and effort this
        # agent's requests actually run under, per-agent overrides included.
        "model_profile": settings.llm.resolve_for(name),
        "scratchpad": _scratchpad(
            settings, session_id=session_id, agent_name=name
        ),
    }
    if read_cache is not None and name == "researcher":
        shared["cache"] = read_cache
    if clock is not None and name in _CLOCK_AWARE_AGENTS:
        shared["clock"] = clock
    return constructor(reputation=reputation, **shared)


def build_agents(
    settings: ConfigSettings,
    *,
    tracker: Tracker,
    provider: AgentCompleter,
    tools: Sequence[BaseTool],
    session_id: str,
    reputation: ReputationSource | None,
    read_cache: MutableMapping[str, ReadRecord] | None = None,
    clock: Clock | None = None,
) -> ResearchAgents:
    """Construct the six agents one graph runs.

    A tool an agent declares but nobody injected is an
    ``AgentConfigurationError`` raised at construction, not a failure
    deferred to the first tool call. That is converted into a
    ``ResearchConfigurationError`` so the CLI can print it without a
    traceback.

    ``clock`` is passed to every agent that reads one, so the six are built
    against a single clock rather than each choosing its own.
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
                    read_cache=read_cache,
                    clock=clock,
                )
                for name in AGENT_NAMES
            }
        )
    except AgentConfigurationError as error:
        raise configuration_error(
            reason="agents_misconfigured",
            message=f"The research agents could not be assembled: {error}",
        ) from error


def build_report_reviewer(
    settings: ConfigSettings,
    *,
    tracker: Tracker,
    provider: AgentCompleter,
) -> ReportReviewer:
    """Construct the terminal semantic reviewer as its own service role.

    Resolved through ``LLMConfig.resolve_for("report_judge")`` rather than
    through an agent's profile: the reviewer is a separate call role with its
    own model and effort, and giving it one of the six agents' configurations
    would silently tie a quality judgement to whichever agent happened to be
    configured that way. It is tool-free by construction — there is no toolset
    parameter to pass it.
    """
    return ReportReviewer(
        provider=provider,
        tracker=tracker,
        config=settings.agents,
        model_profile=settings.llm.resolve_for(REPORT_JUDGE_ROLE),
    )


@dataclass(frozen=True, slots=True)
class ResearchRuntime:
    """One session's compiled graph and the collaborators that outlive it."""

    session_id: str
    settings: ConfigSettings
    tracker: Tracker
    request_budget: RequestBudget
    graph: Any
    long_term: LongTermMemory | None
    procedural: ProceduralMemory | None


async def build_runtime(
    settings: ConfigSettings,
    *,
    session_id: str,
    tracker: Tracker | None = None,
    chat_provider: AgentCompleter | None = None,
    long_term: LongTermMemory | None = None,
    procedural: ProceduralMemory | None = None,
    tavily_api_key: str | None = None,
    search_client: Any | None = None,
    http_client: Any | None = None,
    read_cache: MutableMapping[str, ReadRecord] | None = None,
    clock: Clock | None = None,
) -> ResearchRuntime:
    """Build everything one research session needs, or fail cleanly.

    Every external collaborator is injectable so this whole path is
    testable without an API key, a network, or ChromaDB. Anything that can
    only go wrong at setup time — a missing key, an unopenable vector
    store, a tool an agent declares but nobody built — becomes a
    ``ResearchConfigurationError`` here rather than an exception the user
    sees as a traceback.

    ``read_cache`` is source-cache state the session starts with: bodies an
    earlier session already read, keyed by URL. It is state rather than a
    collaborator — the Researcher validates each entry locally before reusing
    it, and a run that supplies none simply starts with an empty cache.

    ``clock`` is the session's clock, handed to the agents that stamp a date or
    a time. ``None`` runs on the wall clock, which is what the CLI wants; a
    caller that pins it gets a run whose dates are its own, which is how the
    replay harness keeps a row's repetitions identical.
    """
    try:
        validate_agent_model_configs(
            settings.llm, (*AGENT_NAMES, *SERVICE_ROLE_NAMES)
        )
    except ProviderConfigurationError as error:
        raise configuration_error(
            reason="provider_unconfigured",
            message=(
                f"The selected {settings.llm.provider} chat provider is not "
                f"configured: {error}"
            ),
        ) from error

    try:
        embeddings = build_embedding_provider(
            settings.llm.embedding_provider,
            model=settings.llm.embedding_model,
        )
    except ProviderConfigurationError as error:
        raise configuration_error(
            reason="provider_unconfigured",
            message=(
                f"The selected {settings.llm.embedding_provider} embedding "
                f"provider is not configured: {error}"
            ),
        ) from error

    tracker = tracker or Tracker.from_config(settings.langsmith)

    try:
        if long_term is None:
            long_term = LongTermMemory.from_config(
                settings.memory.long_term,
                embeddings=embeddings,
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

    # Exactly one budget for the whole run. The chat transports and the Tavily
    # search tool reserve against this same object, so ``settings.request_budget``
    # bounds the run rather than a single collaborator; a second budget would
    # double every declared ceiling while each half looked correct on its own.
    request_budget = RequestBudget(settings.request_budget)

    try:
        provider = chat_provider or build_chat_provider(
            settings.llm, tracker, request_budget=request_budget
        )
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
        request_budget=request_budget,
    )
    agents = build_agents(
        settings,
        tracker=tracker,
        provider=provider,
        tools=tools,
        session_id=session_id,
        reputation=long_term,
        read_cache=read_cache,
        clock=clock,
    )
    reviewer = build_report_reviewer(
        settings, tracker=tracker, provider=provider
    )
    # One place supplies the reviewer: the dataclass slot the graph reads. A
    # second `report_reviewer=` argument on the graph builders would be a second
    # source of the same fact, and the two could disagree about which reviewer
    # judged a report.
    graph = compile_research_graph(
        replace(agents, report_reviewer=reviewer),
        checkpointer=build_checkpointer(
            enabled=settings.graph.checkpointing_enabled
        ),
    )
    return ResearchRuntime(
        session_id=session_id,
        settings=settings,
        tracker=tracker,
        request_budget=request_budget,
        graph=graph,
        long_term=long_term,
        procedural=procedural,
    )
