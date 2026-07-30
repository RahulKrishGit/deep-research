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


def test_provider_public_api_imports() -> None:
    from deep_research.providers import (
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
    assert issubclass(ProviderConfigurationError, OpenAIProviderError)
    assert issubclass(ProviderRateLimitError, OpenAIProviderError)
    assert issubclass(ProviderResponseError, OpenAIProviderError)
    assert issubclass(ProviderTimeoutError, OpenAIProviderError)
    assert issubclass(StructuredOutputError, OpenAIProviderError)
