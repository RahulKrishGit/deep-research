"""Preflight fails before any experiment is created, without a network call."""

from __future__ import annotations

import pytest

from deep_research.evaluation.cases import cases_for
from deep_research.evaluation.runner import (
    PREFLIGHT_REASONS,
    PreflightError,
    preflight,
    validate_model_capabilities,
)
from tests.evaluation_fakes import FakeLangSmithClient

ENVIRONMENT = {
    "DEEPSEEK_API_KEY": "sk-deepseek-abcdefgh",
    "LANGSMITH_API_KEY": "ls-abcdefghijklmnop",
    "LANGSMITH_PROJECT": "evaluation",
}


async def run(settings, runtime, tmp_path, **overrides):
    kwargs = dict(
        cases=cases_for(runtime.agent_name, runtime.tier),
        environ=dict(ENVIRONMENT),
        langsmith_client=FakeLangSmithClient(),
        root=tmp_path,
    )
    kwargs.update(overrides)
    return await preflight(settings, runtime, **kwargs)


def test_every_spec_preflight_failure_has_a_reason() -> None:
    assert set(PREFLIGHT_REASONS) == {
        "invalid_registry",
        "unknown_case",
        "missing_credentials",
        "model_unavailable",
        "invalid_reasoning_effort",
        "agent_unbuildable",
        "dataset_unavailable",
        "output_root_unwritable",
        "guards_uninstallable",
    }


@pytest.mark.asyncio
async def test_a_clean_preflight_passes(
    settings, runtime_config_for, tmp_path
) -> None:
    await run(settings, runtime_config_for("planner"), tmp_path)


@pytest.mark.asyncio
async def test_a_missing_credential_fails_with_its_reason(
    settings, runtime_config_for, tmp_path
) -> None:
    environ = dict(ENVIRONMENT)
    environ.pop("LANGSMITH_API_KEY")

    with pytest.raises(PreflightError) as caught:
        await run(settings, runtime_config_for("planner"), tmp_path,
                  environ=environ)

    assert caught.value.reason == "missing_credentials"
    assert "LANGSMITH_API_KEY" in str(caught.value)


@pytest.mark.asyncio
async def test_a_live_run_missing_tavily_fails_with_missing_credentials(
    settings, runtime_config_for, tmp_path
) -> None:
    """Researcher's live tier also needs Tavily; step 4 must catch that as
    ``missing_credentials``, not defer it to step 7's
    ``guards_uninstallable``."""
    with pytest.raises(PreflightError) as caught:
        await run(
            settings,
            runtime_config_for("researcher", tier="live"),
            tmp_path,
            cases=cases_for("researcher", "live"),
        )

    assert caught.value.reason == "missing_credentials"
    assert "TAVILY_API_KEY" in str(caught.value)


@pytest.mark.asyncio
async def test_a_live_run_with_an_openai_embedding_model_needs_its_key(
    settings, runtime_config_for, tmp_path
) -> None:
    """Task 8 dropped ``OPENAI_API_KEY`` from ``required_credentials``
    unconditionally; Task 11 then reintroduced a path that needs it -- a
    live run whose ``evaluation.embedding_model`` names an OpenAI model
    builds an ``OpenAIEmbeddingProvider`` (``dependencies.py``). Step 4
    must catch a missing key here, as ``missing_credentials``, rather than
    passing preflight and failing later at the first memory tool call
    (scored as the agent failing its own gates). This is the restored
    coverage for the deleted ``test_a_live_run_checks_the_embedding_model``.
    """
    runtime = runtime_config_for("source_evaluator", tier="live").model_copy(
        update={"embedding_model": "text-embedding-3-small"}
    )

    with pytest.raises(PreflightError) as caught:
        await run(
            settings,
            runtime,
            tmp_path,
            cases=cases_for("source_evaluator", "live"),
        )

    assert caught.value.reason == "missing_credentials"
    assert "OPENAI_API_KEY" in str(caught.value)


@pytest.mark.asyncio
async def test_a_live_run_with_the_default_local_embedding_needs_no_openai_key(
    settings, runtime_config_for, tmp_path
) -> None:
    """Guard against over-fixing: the default stack (DeepSeek chat, local
    embeddings) must keep passing live-tier preflight with no
    ``OPENAI_API_KEY`` present anywhere in the environment."""
    assert "OPENAI_API_KEY" not in ENVIRONMENT
    await run(
        settings,
        runtime_config_for("source_evaluator", tier="live"),
        tmp_path,
        cases=cases_for("source_evaluator", "live"),
    )


@pytest.mark.asyncio
async def test_an_invalid_case_registry_fails_before_any_remote_call(
    settings, runtime_config_for, tmp_path
) -> None:
    duplicated = list(cases_for("planner", "controlled"))
    duplicated.append(duplicated[0])
    client = FakeLangSmithClient()

    with pytest.raises(PreflightError) as caught:
        await run(settings, runtime_config_for("planner"), tmp_path,
                  cases=duplicated, langsmith_client=client)

    assert caught.value.reason == "invalid_registry"
    assert client.created_datasets == []


@pytest.mark.asyncio
async def test_an_unbuildable_agent_fails_preflight(
    settings, runtime_config_for, tmp_path, monkeypatch
) -> None:
    from deep_research.agents.errors import AgentConfigurationError

    def exploding(*args, **kwargs):
        raise AgentConfigurationError("query_memory was never injected")

    monkeypatch.setattr(
        "deep_research.evaluation.runner.build_agent", exploding
    )

    with pytest.raises(PreflightError) as caught:
        await run(settings, runtime_config_for("planner"), tmp_path)

    assert caught.value.reason == "agent_unbuildable"


@pytest.mark.asyncio
async def test_an_uncreatable_output_root_fails_preflight(
    settings, runtime_config_for, tmp_path
) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")

    with pytest.raises(PreflightError) as caught:
        await run(settings, runtime_config_for("planner"), blocked)

    assert caught.value.reason == "output_root_unwritable"


@pytest.mark.asyncio
async def test_an_unknown_override_key_fails_before_execution(
    settings, runtime_config_for, tmp_path
) -> None:
    from deep_research.utils.config import EvaluationConfig

    with pytest.raises(ValueError) as caught:
        EvaluationConfig(target_reasoning_effort_overrides={"planner2": "low"})

    assert "planner2" in str(caught.value)


def _settings_with_target_model(settings, model):
    evaluation = settings.evaluation.model_copy(update={"target_model": model})
    return settings.model_copy(update={"evaluation": evaluation})


@pytest.mark.asyncio
async def test_an_unsupported_target_model_fails_closed_with_no_network(
    settings, runtime_config_for, tmp_path
) -> None:
    broken = _settings_with_target_model(settings, "deepseek-v9-imaginary")
    runtime = runtime_config_for("planner").model_copy(
        update={"target_model": "deepseek-v9-imaginary"}
    )
    client = FakeLangSmithClient()

    with pytest.raises(PreflightError) as caught:
        await run(broken, runtime, tmp_path, langsmith_client=client)

    assert caught.value.reason == "model_unavailable"
    assert "deepseek-v9-imaginary" in str(caught.value)
    assert client.created_datasets == []


@pytest.mark.asyncio
async def test_an_unsupported_effort_for_a_supported_model_fails_closed(
    settings, runtime_config_for, tmp_path
) -> None:
    """DeepSeek V4 Flash accepts only high and max with thinking enabled."""
    runtime = runtime_config_for("planner").model_copy(
        update={"target_reasoning_effort": "low"}
    )

    with pytest.raises(PreflightError) as caught:
        await run(settings, runtime, tmp_path)

    assert caught.value.reason == "model_unavailable"
    assert "low" in str(caught.value)


@pytest.mark.asyncio
async def test_an_unsupported_judge_model_fails_closed(
    settings, runtime_config_for, tmp_path
) -> None:
    runtime = runtime_config_for("planner").model_copy(
        update={"judge_model": "gpt-5.6-luna"}
    )

    with pytest.raises(PreflightError) as caught:
        await run(settings, runtime, tmp_path)

    assert caught.value.reason == "model_unavailable"
    assert "gpt-5.6-luna" in str(caught.value)


def test_capability_validation_accepts_the_openai_provider_too(
    settings, runtime_config_for
) -> None:
    """Fail-closed applies symmetrically; OpenAI stays selectable."""
    openai_settings = settings.model_copy(
        update={
            "llm": settings.llm.model_copy(update={"provider": "openai"}),
            "evaluation": settings.evaluation.model_copy(
                update={
                    "target_model": "gpt-5.6-luna",
                    "judge_model": "gpt-5.6-luna",
                }
            ),
        }
    )
    runtime = runtime_config_for("planner").model_copy(
        update={"target_model": "gpt-5.6-luna", "judge_model": "gpt-5.6-luna"}
    )

    validate_model_capabilities(openai_settings, runtime)
