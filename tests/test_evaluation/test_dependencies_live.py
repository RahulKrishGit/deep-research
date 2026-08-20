"""Live dependency resolution: only applicable credentials and services."""

from __future__ import annotations

from pathlib import Path

import pytest

from deep_research.evaluation.cases import cases_for
from deep_research.evaluation.dependencies import (
    LIVE_DEPENDENCIES,
    MissingCredentialError,
    build_live_dependencies,
    required_credentials,
)
from deep_research.evaluation.models import (
    AGENT_NAMES,
    AgentName,
    CaseExpectations,
    DeterministicMetric,
    EvaluationCase,
    JudgeRubric,
)
from deep_research.utils.types import ResearchState

# The live case registry is empty until Tasks 10-15 land the case files, so
# the fixtures below fall back to minimal sample cases built from the real
# future case ids. The moment a case file lands, the registry lookup wins
# and these tests exercise the real cases.

_SAMPLE_CASE_IDS = {
    "planner": "focused-decomposition",
    "researcher": "multi-source-coverage",
    "source_evaluator": "strong-and-weak-sources",
    "fact_checker": "mixed-verdicts",
    "synthesizer": "complete-cited-report",
    "critic": "approve-strong-report",
}
_SAMPLE_SCENARIOS = {
    "planner": "planner-clean-memory",
    "researcher": "researcher-multi-source",
    "source_evaluator": "source-evaluator-mixed",
    "fact_checker": "fact-checker-mixed",
    "synthesizer": "synthesizer-complete",
    "critic": "critic-strong-report",
}


def _sample_live_case(agent_name: AgentName) -> EvaluationCase:
    case_id = _SAMPLE_CASE_IDS[agent_name]
    return EvaluationCase(
        case_id=case_id,
        version=1,
        agent_name=agent_name,
        tier="live",
        title=f"{agent_name} sample live case",
        purpose="Sample live case for the Task 8 live-bundle tests.",
        state=ResearchState(
            session_id=f"evaluation-{case_id}",
            original_question="Sample research question?",
        ),
        dependency_scenario=_SAMPLE_SCENARIOS[agent_name],
        expectations=CaseExpectations(
            required_output_fields=["result"],
            max_iterations=2,
            max_tool_calls=10,
            deterministic_metrics=[
                DeterministicMetric(
                    metric_id="completeness",
                    weight=1.0,
                    description="sample metric",
                )
            ],
        ),
        judge_rubric=JudgeRubric(
            rubric_id=f"{agent_name}-sample",
            version=1,
        ),
        metadata={},
    )


def _live_case(agent_name: AgentName) -> EvaluationCase:
    available = cases_for(agent_name, "live")
    if available:
        return available[0]
    return _sample_live_case(agent_name)


@pytest.fixture
def live_case_for():
    def factory(agent_name):
        return _live_case(agent_name)

    return factory


FULL_ENVIRONMENT = {
    "DEEPSEEK_API_KEY": "sk-deepseek-abcdefgh",
    "LANGSMITH_API_KEY": "ls-abcdefghijklmnop",
    "LANGSMITH_PROJECT": "evaluation",
    "TAVILY_API_KEY": "tvly-abcdefghijklmnop",
}


def test_live_dependencies_are_derived_from_declared_tools() -> None:
    assert "tavily" in LIVE_DEPENDENCIES["researcher"]
    assert "http" in LIVE_DEPENDENCIES["researcher"]
    assert "tavily" not in LIVE_DEPENDENCIES["source_evaluator"]
    assert "documents" in LIVE_DEPENDENCIES["synthesizer"]
    assert "memory" in LIVE_DEPENDENCIES["planner"]


def test_only_applicable_credentials_are_required() -> None:
    assert required_credentials(
        "source_evaluator", provider="deepseek", embedding_model="local"
    ) == (
        "DEEPSEEK_API_KEY",
        "LANGSMITH_API_KEY",
    )
    assert "TAVILY_API_KEY" in required_credentials(
        "researcher", provider="deepseek", embedding_model="local"
    )
    assert "TAVILY_API_KEY" not in required_credentials(
        "source_evaluator", provider="deepseek", embedding_model="local"
    )


@pytest.mark.parametrize("agent_name", AGENT_NAMES)
def test_the_chat_provider_and_langsmith_are_always_required(agent_name) -> None:
    required = required_credentials(
        agent_name, provider="deepseek", embedding_model="local"
    )

    assert "DEEPSEEK_API_KEY" in required
    assert "LANGSMITH_API_KEY" in required
    assert "OPENAI_API_KEY" not in required


@pytest.mark.parametrize("agent_name", AGENT_NAMES)
def test_selecting_openai_chat_requires_the_openai_key(agent_name) -> None:
    assert "OPENAI_API_KEY" in required_credentials(
        agent_name, provider="openai", embedding_model="local"
    )


@pytest.mark.parametrize("agent_name", AGENT_NAMES)
def test_a_named_embedding_model_requires_the_openai_key(agent_name) -> None:
    """A live run selecting an OpenAI embedding model must require
    ``OPENAI_API_KEY`` even when the chat provider is DeepSeek -- this is
    the restored coverage for the Task 8/11 composition gap. (Guard against
    under-fixing: ``test_the_chat_provider_and_langsmith_are_always_required``
    above already pins that the local sentinel requires no such key.)"""
    assert "OPENAI_API_KEY" in required_credentials(
        agent_name,
        provider="deepseek",
        embedding_model="text-embedding-3-small",
    )


@pytest.mark.parametrize("agent_name", AGENT_NAMES)
def test_openai_chat_and_embedding_do_not_double_the_credential(
    agent_name,
) -> None:
    """Selecting OpenAI for both chat and embeddings names
    ``OPENAI_API_KEY`` once, de-duplicated in first-seen order."""
    required = required_credentials(
        agent_name,
        provider="openai",
        embedding_model="text-embedding-3-small",
    )

    assert required.count("OPENAI_API_KEY") == 1


def test_a_missing_applicable_credential_names_only_what_is_missing(
    tracker, settings, tmp_path, runtime_config_for, live_case_for
) -> None:
    environ = dict(FULL_ENVIRONMENT)
    environ.pop("TAVILY_API_KEY")

    with pytest.raises(MissingCredentialError) as caught:
        build_live_dependencies(
            runtime_config_for("researcher", tier="live"),
            live_case_for("researcher"),
            tracker=tracker,
            settings=settings,
            root=tmp_path,
            environ=environ,
        )

    assert caught.value.reason == "missing_credentials"
    assert caught.value.variables == ("TAVILY_API_KEY",)
    assert "TAVILY_API_KEY" in str(caught.value)


def test_an_agent_without_search_does_not_need_tavily(
    tracker, settings, tmp_path, runtime_config_for, live_case_for
) -> None:
    environ = dict(FULL_ENVIRONMENT)
    environ.pop("TAVILY_API_KEY")

    bundle = build_live_dependencies(
        runtime_config_for("source_evaluator", tier="live"),
        live_case_for("source_evaluator"),
        tracker=tracker,
        settings=settings,
        root=tmp_path,
        environ=environ,
        embeddings=object(),
    )

    assert bundle is not None


def test_live_memory_and_documents_stay_in_the_evaluation_namespace(
    tracker, settings, tmp_path, runtime_config_for, live_case_for
) -> None:
    bundle = build_live_dependencies(
        runtime_config_for("synthesizer", tier="live"),
        live_case_for("synthesizer"),
        tracker=tracker,
        settings=settings,
        root=tmp_path,
        environ=FULL_ENVIRONMENT,
        embeddings=object(),
    )

    assert bundle.collection_name.startswith("evaluation_")
    assert bundle.collection_name != settings.memory.long_term.collection_name
    assert tmp_path in bundle.document_directory.parents
    assert tmp_path in bundle.strategies_path.parents


def test_a_live_bundle_builds_the_local_embedding_provider_by_default(
    tracker, settings, tmp_path, runtime_config_for, live_case_for, monkeypatch
) -> None:
    """``embedding_model: local`` selects the offline provider, and the
    real ONNX model is never constructed in an offline test."""
    captured: list[tuple[str, str]] = []

    class RecordingEmbeddings:
        def embed_query(self, text):  # pragma: no cover - never called offline
            raise AssertionError("offline tests must not embed")

        def embed_documents(self, texts):  # pragma: no cover
            raise AssertionError("offline tests must not embed")

    def recording_build(provider, *, model):
        captured.append((provider, model))
        return RecordingEmbeddings()

    monkeypatch.setattr(
        "deep_research.evaluation.dependencies.build_embedding_provider",
        recording_build,
    )

    build_live_dependencies(
        runtime_config_for("planner", tier="live"),
        live_case_for("planner"),
        tracker=tracker,
        settings=settings,
        root=tmp_path,
        environ=FULL_ENVIRONMENT,
    )

    assert captured == [("local", "local")]


def test_a_live_bundle_selects_openai_for_a_named_embedding_model(
    tracker, settings, tmp_path, runtime_config_for, live_case_for, monkeypatch
) -> None:
    captured: list[tuple[str, str]] = []

    def recording_build(provider, *, model):
        captured.append((provider, model))
        return object()

    monkeypatch.setattr(
        "deep_research.evaluation.dependencies.build_embedding_provider",
        recording_build,
    )
    runtime = runtime_config_for("planner", tier="live").model_copy(
        update={"embedding_model": "text-embedding-3-small"}
    )

    build_live_dependencies(
        runtime,
        live_case_for("planner"),
        tracker=tracker,
        settings=settings,
        root=tmp_path,
        # Selecting an OpenAI embedding model now requires OPENAI_API_KEY
        # (the fix for the Task 8/11 fail-open) -- present here so this
        # test still exercises the provider selection itself.
        environ={**FULL_ENVIRONMENT, "OPENAI_API_KEY": "sk-openai-abcdefgh"},
    )

    assert captured == [("openai", "text-embedding-3-small")]


def test_a_named_embedding_model_without_the_openai_key_fails_closed(
    tracker, settings, tmp_path, runtime_config_for, live_case_for
) -> None:
    """The Task 8/11 composition gap this branch fixes: a live run
    selecting an OpenAI embedding model with no ``OPENAI_API_KEY`` must be
    rejected by ``build_live_dependencies`` itself, before any embedding
    provider is constructed -- not left to fail later at the first
    ``query_memory``/``save_to_memory`` call."""
    runtime = runtime_config_for("planner", tier="live").model_copy(
        update={"embedding_model": "text-embedding-3-small"}
    )

    with pytest.raises(MissingCredentialError) as caught:
        build_live_dependencies(
            runtime,
            live_case_for("planner"),
            tracker=tracker,
            settings=settings,
            root=tmp_path,
            environ=FULL_ENVIRONMENT,
        )

    assert caught.value.reason == "missing_credentials"
    assert "OPENAI_API_KEY" in caught.value.variables


def test_live_bundles_record_the_real_services_they_expose(
    tracker, settings, tmp_path, runtime_config_for, live_case_for
) -> None:
    bundle = build_live_dependencies(
        runtime_config_for("researcher", tier="live"),
        live_case_for("researcher"),
        tracker=tracker,
        settings=settings,
        root=tmp_path,
        environ=FULL_ENVIRONMENT,
        search_client=object(),
        http_client=object(),
        embeddings=object(),
    )

    assert set(bundle.available_services) == set(
        LIVE_DEPENDENCIES["researcher"]
    )


def test_live_repetitions_never_share_a_persist_path(
    tracker, settings, tmp_path, runtime_config_for, live_case_for
) -> None:
    """Live Chroma persists per repetition, never under a shared root.

    Task 7's ``isolated_settings`` default of ``root/memory`` is inert for
    the in-memory controlled collection but would be a collision trap for
    the real Chroma collection live mode opens: repetition 2 would recall
    repetition 1's findings. The live tier overrides it per repetition.
    """
    case = live_case_for("planner")

    first = build_live_dependencies(
        runtime_config_for("planner", tier="live"),
        case,
        tracker=tracker,
        settings=settings,
        root=tmp_path,
        environ=FULL_ENVIRONMENT,
        embeddings=object(),
    )
    second = build_live_dependencies(
        runtime_config_for("planner", tier="live"),
        case,
        tracker=tracker,
        settings=settings,
        root=tmp_path,
        environ=FULL_ENVIRONMENT,
        repetition=2,
        embeddings=object(),
    )

    assert (
        first.settings.memory.long_term.persist_directory
        != second.settings.memory.long_term.persist_directory
    )
    assert tmp_path in Path(
        first.settings.memory.long_term.persist_directory
    ).parents
    assert first.collection_name != second.collection_name
    assert first.document_directory != second.document_directory
