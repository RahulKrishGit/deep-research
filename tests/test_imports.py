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
        ProviderError,
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
    assert OpenAIProviderError is ProviderError
    assert issubclass(ProviderConfigurationError, OpenAIProviderError)
    assert issubclass(ProviderRateLimitError, OpenAIProviderError)
    assert issubclass(ProviderResponseError, OpenAIProviderError)
    assert issubclass(ProviderTimeoutError, OpenAIProviderError)
    assert issubclass(StructuredOutputError, OpenAIProviderError)


def test_agent_runtime_contracts_import_from_package() -> None:
    from deep_research.agents import (  # noqa: F401
        ACCEPTANCE_SCORE,
        CRITIC_CLAIM_DIGEST,
        CRITIC_EVIDENCE_CHARS,
        CRITIC_NAME,
        CRITIC_REPORT_CHARS,
        CRITIQUE_FALLBACK_REASONS,
        DEFAULT_MAX_NOTES,
        DEFAULT_SUMMARY_LIMIT,
        FACT_CHECKER_NAME,
        LOW_CONFIDENCE_THRESHOLD,
        MAX_CRITIC_SCORE,
        MAX_SUB_TOPICS,
        MIN_CRITIC_SCORE,
        MIN_SUB_TOPICS,
        REACT_RESPONSE_CONTRACT,
        REPORT_SECTIONS,
        ROUTING_REASONS,
        SOURCE_EVALUATOR_NAME,
        SYNTHESIZER_NAME,
        VERDICT_VALUES,
        WRITE_FAILURE_REASONS,
        AgentConfigurationError,
        AgentError,
        AgentRun,
        AgentTask,
        AgentToolset,
        BaseAgent,
        Citation,
        ClaimDraft,
        ClaimsDraft,
        ClaimTask,
        ClaimVerdictDraft,
        CriticAgent,
        CritiqueDraft,
        CritiqueTask,
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
        ReportDraft,
        ReportSection,
        ReportSectionDraft,
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
        SynthesisTask,
        SynthesizedReport,
        SynthesizerAgent,
        ToolDescriptor,
        VerifiedClaims,
        agent_error,
        agent_event,
        assemble_report,
        build_citation_index,
        build_claim,
        build_critique,
        build_findings,
        build_report_sections,
        build_scored_source,
        clamp_score,
        compose_report,
        corroboration_score,
        critique_completed_event,
        critique_messages,
        critique_provider_error,
        critique_started_event,
        existing_sources_for,
        extraction_provider_error,
        fallback_critique,
        group_findings_by_url,
        invalid_section_error,
        is_high_priority,
        limitation_reasons,
        memory_save_error,
        merge_react_runs,
        missing_report_error,
        no_evidence_error,
        normalize_notes,
        normalize_source_url,
        normalize_verdict,
        parse_tool_input,
        render_claim_digest,
        render_memory_guidance,
        render_react_messages,
        render_scratchpad,
        render_tool_catalog,
        report_filename,
        report_not_written_error,
        report_provider_error,
        resolve_verdict,
        retrieved_source_urls,
        route_decision,
        run_react_loop,
        select_sub_topics,
        source_domain,
        summarize_text,
        synthesis_completed_event,
        synthesis_started_event,
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
        "critic",
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
        CriticAgent,
        FactCheckerAgent,
        PlannerAgent,
        ResearcherAgent,
        SourceEvaluatorAgent,
        SynthesizerAgent,
    )

    assert CriticAgent.name == "critic"
    assert CriticAgent.allowed_tools == ("web_search", "query_memory")
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
    assert SynthesizerAgent.name == "synthesizer"
    assert SynthesizerAgent.allowed_tools == ("write_document", "save_to_memory")


def test_graph_contracts_import_from_package() -> None:
    from deep_research.graph import (  # noqa: F401
        AGENT_NODE_ORDER,
        CRITIC_NODE,
        DEFAULT_MAX_ITERATIONS,
        FACT_CHECKER_NODE,
        GRAPH_ERROR_REASONS,
        GRAPH_ROUTES,
        GRAPH_SOURCE,
        GRAPH_STATUSES,
        HALTING_ERROR_TYPES,
        NODE_NAMES,
        PLANNER_NODE,
        REFINE_NODE,
        RESEARCHER_NODE,
        ROUTE_END,
        ROUTE_REFINE,
        SOURCE_EVALUATOR_NODE,
        SYNTHESIZER_NODE,
        GraphConfigurationError,
        GraphError,
        GraphNode,
        GraphResumeError,
        GraphRun,
        ResearchAgent,
        ResearchAgents,
        ResearchGraphState,
        agent_configuration_error,
        agent_node,
        build_checkpointer,
        build_research_graph,
        compile_research_graph,
        critic_node,
        dump_state,
        graph_error,
        graph_event,
        graph_recursion_limit,
        graph_route,
        graph_status,
        initial_graph_state,
        invalid_agent_state_error,
        invalid_route_error,
        is_halted,
        load_state,
        node_completed_event,
        node_skipped_event,
        node_started_event,
        planning_failed_error,
        provider_configuration_error,
        refine_node,
        refinement_started_event,
        resume_research_graph,
        route_after_critic,
        route_decided_event,
        run_research_graph,
        session_completed_event,
        session_config,
        session_started_event,
    )


def test_graph_all_surface_is_fully_covered() -> None:
    """Every name in ``deep_research.graph.__all__`` must actually resolve."""
    import deep_research.graph as graph_pkg

    missing = [name for name in graph_pkg.__all__ if not hasattr(graph_pkg, name)]
    assert not missing, f"__all__ entries missing from package: {missing}"


def test_graph_submodule_public_names_all_reach_all() -> None:
    """Every public module-level name in ``graph/*.py`` must be exported.

    The graph twin of ``test_agent_submodule_public_names_all_reach_all``:
    it catches a public name that exists in a submodule but was never wired
    into ``__all__``, which the import list above cannot.
    """
    import ast
    from pathlib import Path

    import deep_research.graph as graph_pkg

    graph_dir = Path(graph_pkg.__file__).parent
    submodules = ["errors", "events", "nodes", "orchestrator", "state"]

    missing: list[str] = []
    for module_name in submodules:
        path = graph_dir / f"{module_name}.py"
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
            if name not in graph_pkg.__all__:
                missing.append(f"{module_name}.{name}")

    assert not missing, (
        "public submodule names missing from `deep_research.graph.__all__`: "
        f"{missing}"
    )


def test_the_graph_nodes_cover_the_designed_sequence() -> None:
    from deep_research.graph import AGENT_NODE_ORDER, CRITIC_NODE, NODE_NAMES

    assert AGENT_NODE_ORDER == (
        "planner",
        "researcher",
        "source_evaluator",
        "fact_checker",
        "synthesizer",
    )
    assert CRITIC_NODE == "critic"
    assert NODE_NAMES[-1] == "refine"


def test_runtime_contracts_import_from_package() -> None:
    from deep_research.runtime import (  # noqa: F401
        AGENT_NAMES,
        CONFIGURATION_HINTS,
        DEFAULT_BRIDGE_AGENT_ID,
        DEFAULT_BRIDGE_ENTRY_TYPE,
        DEFAULT_RECALL_TOP_K,
        MAX_SUGGESTED_STRATEGIES,
        RECALLED_SUB_TOPIC,
        REPORT_WRITTEN_EVENT,
        TAVILY_API_KEY_VARIABLE,
        LongTermMemoryBridge,
        ResearchConfigurationError,
        ResearchOutcome,
        ResearchRuntime,
        ToolCallSummary,
        build_agents,
        build_outcome,
        build_runtime,
        build_tools,
        configuration_error,
        recall_memory_context,
        report_path_from_state,
        tool_call_summaries,
        total_token_usage,
    )


def test_runtime_all_surface_is_fully_covered() -> None:
    """Every name in ``deep_research.runtime.__all__`` must actually resolve."""
    import deep_research.runtime as runtime_pkg

    missing = [
        name for name in runtime_pkg.__all__ if not hasattr(runtime_pkg, name)
    ]
    assert not missing, f"__all__ entries missing from package: {missing}"


def test_the_research_entry_point_is_importable() -> None:
    from deep_research.main import (  # noqa: F401
        DEFAULT_CONFIG_PATH,
        SUPPORTED_OUTPUT_FORMATS,
        load_settings,
        new_session_id,
        resolve_output_format,
        run_research,
        run_research_sync,
    )


def test_the_cli_surface_is_importable() -> None:
    from deep_research.cli import (  # noqa: F401
        EXIT_CONFIGURATION_ERROR,
        EXIT_GRAPH_FAILED,
        EXIT_INTERRUPTED,
        EXIT_OK,
        INTERACTIVE_PROMPT,
        PROGRAM_NAME,
        PROGRESS_EVENT_TYPES,
        SPAN_EVENT_PREFIX,
        STATUS_NOTES,
        CliOptions,
        build_parser,
        main,
        parse_arguments,
        render_progress,
        render_summary,
        render_warnings,
        resolve_question,
    )


def test_the_agent_names_match_the_graph_node_names() -> None:
    from deep_research.graph import NODE_NAMES
    from deep_research.runtime import AGENT_NAMES

    assert AGENT_NAMES == NODE_NAMES[:6]
