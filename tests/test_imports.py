"""Verify all packages import correctly."""

import deep_research
from deep_research import (
    agents,
    graph,
    memory,
    observability,
    providers,
    tools,
    utils,
)


def test_package_version() -> None:
    """Package exposes a version string."""
    assert deep_research.__version__ == "0.1.0"


def test_all_subpackages_import() -> None:
    """All sub-packages import without error."""
    assert agents is not None
    assert graph is not None
    assert memory is not None
    assert tools is not None
    assert providers is not None
    assert observability is not None
    assert utils is not None


def test_shared_research_types_import_from_utils_package() -> None:
    from deep_research.utils import (  # noqa: F401
        AwareISOString,
        Claim,
        ClaimVerdict,
        CriticScore,
        Critique,
        Finding,
        MemorySnapshot,
        ResearchError,
        ResearchEvent,
        ResearchState,
        ResearchStateUpdate,
        ScoredSource,
        SubTopic,
        UnitScore,
        advance_research_iteration,
        merge_research_state,
    )


def test_observability_contracts_import_from_package() -> None:
    from deep_research.observability import (  # noqa: F401
        AgentMetric,
        LangSmithRuntimeConfig,
        MetricRecord,
        SessionMetric,
        SpanHandle,
        TokenUsage,
        TokenUsageMetric,
        ToolMetric,
        TraceContext,
        Tracker,
        bind_trace_context,
        build_trace_metadata,
        current_trace_context,
        load_langsmith_runtime_config,
    )


def test_core_tool_contracts_import_from_package() -> None:
    """Core tool integrations are available from the public tools package."""
    from deep_research.tools import (  # noqa: F401
        BaseTool,
        DocumentReaderTool,
        LongTermMemory,
        QueryMemoryTool,
        SaveToMemoryTool,
        ToolCallContext,
        ToolError,
        ToolExecution,
        ToolExecutionError,
        ToolResult,
        WebScraperTool,
        WebSearchTool,
        WriteDocumentTool,
    )


def test_memory_contracts_import_from_package() -> None:
    from deep_research.memory import (  # noqa: F401
        EmbeddingProvider,
        LongTermMemory,
        MemoryEntry,
        MemoryEntryType,
        MemoryErrorLog,
        MemoryInitializationError,
        MemoryQueryResult,
        MemoryStackError,
        ProceduralMemory,
        ScratchpadEntry,
        ScratchpadMemory,
        SourceReputation,
        StrategyRecord,
        Summarizer,
        VectorCollection,
        build_chroma_collection,
        memory_operation,
        source_reputation_entry_id,
    )


def test_provider_public_api_imports() -> None:
    from deep_research.providers import (
        DEFAULT_EMBEDDING_MODEL,
        ChatMessage,
        ChatResult,
        OpenAIChatProvider,
        OpenAIEmbeddingProvider,
        OpenAIProviderError,
        ProviderConfigurationError,
        ProviderRateLimitError,
        ProviderResponseError,
        ProviderTimeoutError,
        StructuredOutputError,
    )

    assert OpenAIChatProvider.__name__ == "OpenAIChatProvider"
    assert OpenAIEmbeddingProvider.__name__ == "OpenAIEmbeddingProvider"
    assert ChatMessage.__name__ == "ChatMessage"
    assert ChatResult.__name__ == "ChatResult"
    assert DEFAULT_EMBEDDING_MODEL
    assert issubclass(ProviderConfigurationError, OpenAIProviderError)
    assert issubclass(ProviderRateLimitError, OpenAIProviderError)
    assert issubclass(ProviderResponseError, OpenAIProviderError)
    assert issubclass(ProviderTimeoutError, OpenAIProviderError)
    assert issubclass(StructuredOutputError, OpenAIProviderError)


def test_agent_runtime_contracts_import_from_package() -> None:
    from deep_research.agents import (  # noqa: F401
        DEFAULT_SUMMARY_LIMIT,
        REACT_RESPONSE_CONTRACT,
        AgentConfigurationError,
        AgentError,
        AgentRun,
        AgentTask,
        AgentToolset,
        BaseAgent,
        DecideCallback,
        ReActActionType,
        ReActDecision,
        ReActObservation,
        ReActRun,
        ReActStep,
        StepCallback,
        StopReason,
        StructuredCompleter,
        SufficiencyCallback,
        ToolDescriptor,
        agent_error,
        parse_tool_input,
        render_react_messages,
        render_scratchpad,
        render_tool_catalog,
        run_react_loop,
        summarize_text,
    )


def test_agent_runtime_all_surface_is_fully_covered() -> None:
    """Every name in ``deep_research.agents.__all__`` must actually resolve.

    Guards against a typo'd or dropped ``__all__`` entry going unnoticed —
    the import-list test above only proves the names it enumerates exist,
    not that it enumerates everything ``__all__`` claims to export.
    """
    import deep_research.agents as agents_pkg

    missing = [name for name in agents_pkg.__all__ if not hasattr(agents_pkg, name)]
    assert not missing, f"__all__ entries missing from package: {missing}"


def test_agent_runtime_config_imports_from_utils_config() -> None:
    from deep_research.utils.config import AgentRuntimeConfig

    assert AgentRuntimeConfig().max_iterations >= 1
