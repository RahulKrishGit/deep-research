"""Preflight fails before any experiment is created."""

from __future__ import annotations

import pytest

from deep_research.evaluation.cases import cases_for
from deep_research.evaluation.runner import (
    PREFLIGHT_REASONS,
    PreflightError,
    preflight,
    verify_model_access,
)
from tests.evaluation_fakes import FakeLangSmithClient, FakeOpenAIClient

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
        openai_client=FakeOpenAIClient(
            available=["deepseek-v4-flash", "local"]
        ),
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
async def test_a_live_run_missing_tavily_fails_before_model_access(
    settings, runtime_config_for, tmp_path
) -> None:
    """Researcher's live tier also needs Tavily; step 4 must catch that
    before step 5's real model-access call, not defer it to step 7's
    ``guards_uninstallable``."""
    environ = dict(ENVIRONMENT)
    client = FakeOpenAIClient(available=["deepseek-v4-flash", "local"])

    with pytest.raises(PreflightError) as caught:
        await run(
            settings,
            runtime_config_for("researcher", tier="live"),
            tmp_path,
            cases=cases_for("researcher", "live"),
            environ=environ,
            openai_client=client,
        )

    assert caught.value.reason == "missing_credentials"
    assert "TAVILY_API_KEY" in str(caught.value)
    assert client.models.requested == []


@pytest.mark.asyncio
async def test_an_inaccessible_target_model_never_falls_back(
    settings, runtime_config_for, tmp_path
) -> None:
    with pytest.raises(PreflightError) as caught:
        await run(
            settings,
            runtime_config_for("planner"),
            tmp_path,
            openai_client=FakeOpenAIClient(available=["gpt-4o"]),
        )

    assert caught.value.reason == "model_unavailable"
    assert "deepseek-v4-flash" in str(caught.value)
    assert "gpt-4o" not in str(caught.value)


@pytest.mark.asyncio
async def test_the_embedding_model_is_only_checked_for_live_runs(
    settings, runtime_config_for, tmp_path
) -> None:
    client = FakeOpenAIClient(available=["deepseek-v4-flash"])

    await run(settings, runtime_config_for("planner"), tmp_path,
              openai_client=client)

    assert "local" not in client.models.requested


@pytest.mark.asyncio
async def test_a_live_run_checks_the_embedding_model(
    settings, runtime_config_for, tmp_path
) -> None:
    client = FakeOpenAIClient(available=["deepseek-v4-flash"])

    with pytest.raises(PreflightError) as caught:
        await run(
            settings,
            runtime_config_for("planner", tier="live"),
            tmp_path,
            cases=cases_for("planner", "live"),
            environ={**ENVIRONMENT, "TAVILY_API_KEY": "tvly-abcdefghijklmnop"},
            openai_client=client,
        )

    assert caught.value.reason == "model_unavailable"
    assert "local" in str(caught.value)


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


@pytest.mark.asyncio
async def test_model_access_is_verified_before_any_dataset_write(
    settings, runtime_config_for, tmp_path
) -> None:
    client = FakeLangSmithClient()

    with pytest.raises(PreflightError):
        await run(
            settings,
            runtime_config_for("planner"),
            tmp_path,
            openai_client=FakeOpenAIClient(available=[]),
            langsmith_client=client,
        )

    assert client.created_datasets == []


@pytest.mark.asyncio
async def test_verify_model_access_requests_each_model_once() -> None:
    client = FakeOpenAIClient(available=["a", "b"])

    await verify_model_access(client, ["a", "b"])

    assert client.models.requested == ["a", "b"]
