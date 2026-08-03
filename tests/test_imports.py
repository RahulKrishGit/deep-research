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
        FACT_CHECKER_NAME,
        LOW_CONFIDENCE_THRESHOLD,
        MAX_SUB_TOPICS,
        MIN_SUB_TOPICS,
        REACT_RESPONSE_CONTRACT,
        SOURCE_EVALUATOR_NAME,
        VERDICT_VALUES,
        AgentConfigurationError,
        AgentError,
        AgentRun,
        AgentTask,
        AgentToolset,
        BaseAgent,
        ClaimDraft,
        ClaimsDraft,
        ClaimTask,
        ClaimVerdictDraft,
        DecideCallback,
        EvaluatedSources,
        FactCheckerAgent,
        FindingDraft,
        PlannerAgent,
        PlanningError,
        ReActActionType,
        ReActDecision,
        ReActObservation,
        ReActRun,
        ReActStep,
        ReputationSource,
        ResearcherAgent,
        ResearchFindings,
        ResearchPlan,
        ResearchPlanDraft,
        SourceEvaluationTask,
        SourceEvaluatorAgent,
        SourceGroup,
        SourceScoreDraft,
        SourceScoresDraft,
        StepCallback,
        StopReason,
        StructuredCompleter,
        SubTopicDraft,
        SubTopicFindingsDraft,
        SubTopicTask,
        SufficiencyCallback,
        ToolDescriptor,
        VerifiedClaims,
        agent_error,
        agent_event,
        build_claim,
        build_findings,
        build_scored_source,
        corroboration_score,
        existing_sources_for,
        extraction_provider_error,
        group_findings_by_url,
        is_high_priority,
        merge_react_runs,
        normalize_source_url,
        normalize_verdict,
        parse_tool_input,
        render_memory_guidance,
        render_react_messages,
        render_scratchpad,
        render_tool_catalog,
        resolve_verdict,
        retrieved_source_urls,
        run_react_loop,
        select_sub_topics,
        source_domain,
        summarize_text,
        validate_plan_draft,
        verdict_counts,
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


def test_agent_submodule_public_names_all_reach_all() -> None:
    """Every public (non-underscored) module-level name in ``agents/*.py``
    must be reachable via ``deep_research.agents.__all__``.

    ``test_agent_runtime_all_surface_is_fully_covered`` only checks that
    every ``__all__`` entry resolves — it doesn't catch a public name that
    exists in a submodule but was never wired into ``__all__`` (the exact
    gap that let ``extraction_provider_error`` and ``invalid_fields`` go
    unexported after later fix-round commits added/renamed them). This
    test walks the submodule source with ``ast`` and asserts the
    complement: every public top-level name is either exported or on the
    small allowlist of deliberate internals below.
    """
    import ast
    from pathlib import Path

    import deep_research.agents as agents_pkg

    # Names that are legitimately module-level and public-looking (no
    # leading underscore) but are not meant to be part of the package's
    # public surface.
    allowlist = {
        # Generic TypeVar used only for `BaseAgent`/`AgentRun` typing;
        # re-exporting it would imply callers should parameterize with it
        # directly, which they don't.
        "ResultT",
    }

    agents_dir = Path(agents_pkg.__file__).parent
    submodules = [
        "base",
        "errors",
        "events",
        "fact_checker",
        "planner",
        "prompts",
        "react",
        "report",
        "researcher",
        "source_evaluator",
        "sources",
        "steps",
        "synthesizer",
        "toolset",
        "validation",
    ]

    missing: list[str] = []
    for module_name in submodules:
        path = agents_dir / f"{module_name}.py"
        assert path.is_file(), f"expected submodule file missing: {path}"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            name: str | None = None
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = node.name
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        name = target.id
                        break
            elif isinstance(node, ast.AnnAssign) and isinstance(
                node.target, ast.Name
            ):
                name = node.target.id

            if name is None or name.startswith("_"):
                continue
            if name in allowlist:
                continue
            if name not in agents_pkg.__all__:
                missing.append(f"{module_name}.{name}")

    assert not missing, (
        "public submodule names missing from `deep_research.agents.__all__`: "
        f"{missing}"
    )


def test_agent_runtime_config_imports_from_utils_config() -> None:
    from deep_research.utils.config import AgentRuntimeConfig

    assert AgentRuntimeConfig().max_iterations >= 1


def test_concrete_agents_expose_their_identity_and_tools() -> None:
    from deep_research.agents import (
        FactCheckerAgent,
        PlannerAgent,
        ResearcherAgent,
        SourceEvaluatorAgent,
    )

    assert PlannerAgent.name == "planner"
    assert PlannerAgent.allowed_tools == ("query_memory", "web_search")
    assert ResearcherAgent.name == "researcher"
    assert set(ResearcherAgent.allowed_tools) == {
        "web_search",
        "web_scraper",
        "document_reader",
        "query_memory",
        "save_to_memory",
    }
    assert SourceEvaluatorAgent.name == "source_evaluator"
    assert SourceEvaluatorAgent.allowed_tools == ()
    assert FactCheckerAgent.name == "fact_checker"
    assert set(FactCheckerAgent.allowed_tools) == {
        "web_search",
        "web_scraper",
        "document_reader",
        "query_memory",
    }
