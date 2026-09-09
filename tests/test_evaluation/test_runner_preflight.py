"""Preflight fails before any experiment is created, without a network call."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from deep_research.evaluation import runner as runner_module
from deep_research.evaluation.cases import case_by_id, cases_for
from deep_research.evaluation.runner import (
    PREFLIGHT_REASONS,
    PreflightError,
    preflight,
    validate_model_capabilities,
)
from tests.evaluation_fakes import FakeLangSmithClient, FakeStructuredProvider

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


def _long_absolute_output_base(tmp_path: Path) -> Path:
    base = tmp_path
    for name in (
        "task10-output-root-" + "a" * 64,
        "windows-long-path-" + "b" * 64,
        "controlled-preflight-" + "c" * 64,
        "legal-component-" + "d" * 64,
    ):
        base /= name
    assert len(str(base)) >= 260
    return base


TASK10_PREFLIGHT_CASES = (
    (
        "researcher",
        "cross-agent-planner-fix-parity-baseline-researcher",
        "multi-source-coverage",
    ),
    (
        "source_evaluator",
        "cross-agent-planner-fix-parity-baseline-source-evaluator",
        "strong-and-weak-sources",
    ),
    (
        "fact_checker",
        "cross-agent-planner-fix-parity-baseline-fact-checker",
        "mixed-verdicts",
    ),
    (
        "synthesizer",
        "cross-agent-planner-fix-parity-baseline-synthesizer",
        "complete-cited-report",
    ),
    (
        "critic",
        "cross-agent-planner-fix-parity-baseline-critic",
        "approve-strong-report",
    ),
    (
        "researcher",
        "cross-agent-planner-fix-parity-confirmation-researcher",
        "multi-source-coverage",
    ),
)


@pytest.mark.skipif(os.name != "nt", reason="Windows long-path regression")
@pytest.mark.parametrize(
    ("agent_name", "experiment_prefix", "case_id"),
    TASK10_PREFLIGHT_CASES,
    ids=[item[1] for item in TASK10_PREFLIGHT_CASES],
)
@pytest.mark.asyncio
async def test_task10_preflight_uses_a_long_runtime_output_root(
    settings,
    runtime_config_for,
    tmp_path,
    monkeypatch,
    agent_name,
    experiment_prefix,
    case_id,
) -> None:
    runtime = runtime_config_for(
        agent_name,
        case_id=case_id,
        output_directory=str(_long_absolute_output_base(tmp_path)),
        experiment_prefix=experiment_prefix,
    )
    case = case_by_id(agent_name, "controlled", case_id)
    dependency_roots: list[Path] = []
    real_dependencies = runner_module.build_controlled_dependencies

    def recording_dependencies(runtime_arg, case_arg, *, root, **kwargs):
        dependency_roots.append(root)
        return real_dependencies(runtime_arg, case_arg, root=root, **kwargs)

    monkeypatch.setattr(
        runner_module, "build_controlled_dependencies", recording_dependencies
    )
    monkeypatch.setattr(
        runner_module,
        "build_chat_provider",
        lambda *args, **kwargs: FakeStructuredProvider(),
    )
    monkeypatch.setattr(runner_module, "build_agent", lambda *args, **kwargs: object())

    await runner_module.preflight(
        settings,
        runtime,
        cases=[case],
        environ=dict(ENVIRONMENT),
        langsmith_client=FakeLangSmithClient(),
        root=runtime.output_root,
    )

    assert str(runtime.output_root).startswith("\\\\?\\")
    assert dependency_roots == [runtime.output_root / "_preflight"]
    assert (runtime.output_root / "_preflight").is_dir()
    assert not (runtime.output_root / ".preflight-write-probe").exists()


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
    live run whose ``evaluation.embedding_provider`` resolves to
    ``"openai"`` builds an ``OpenAIEmbeddingProvider`` (``dependencies.py``).
    Step 4 must catch a missing key here, as ``missing_credentials``, rather
    than passing preflight and failing later at the first memory tool call
    (scored as the agent failing its own gates). This is the restored
    coverage for the deleted ``test_a_live_run_checks_the_embedding_model``.
    """
    runtime = runtime_config_for("source_evaluator", tier="live").model_copy(
        update={
            "embedding_provider": "openai",
            "embedding_model": "text-embedding-3-small",
        }
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
async def test_a_live_run_with_a_typo_d_openai_embedding_model_fails_closed(
    settings, runtime_config_for, tmp_path
) -> None:
    """A typo'd OpenAI embedding model name must fail preflight by name,
    before any dataset write, instead of at the first live-tier embed."""
    runtime = runtime_config_for("source_evaluator", tier="live").model_copy(
        update={
            "embedding_provider": "openai",
            "embedding_model": "text-embedding-3-large-typo",
        }
    )
    environ = {**ENVIRONMENT, "OPENAI_API_KEY": "sk-openai-abcdefgh"}
    client = FakeLangSmithClient()

    with pytest.raises(PreflightError) as caught:
        await run(
            settings,
            runtime,
            tmp_path,
            cases=cases_for("source_evaluator", "live"),
            environ=environ,
            langsmith_client=client,
        )

    assert caught.value.reason == "model_unavailable"
    assert "text-embedding-3-large-typo" in str(caught.value)
    assert client.created_datasets == []


@pytest.mark.asyncio
async def test_a_live_run_with_any_model_name_under_local_embeddings_still_passes(
    settings, runtime_config_for, tmp_path
) -> None:
    """Replaces the deleted
    ``test_a_live_run_with_an_openai_model_name_for_local_embeddings_fails_closed``,
    which rejected a real OpenAI model name under ``embedding_provider ==
    "local"``. That rejection was the design error this correction fixes,
    not a real safeguard: ``LocalEmbeddingProvider`` takes no model
    argument at all, so no name it is given can ever be "wrong" for it --
    there is no such thing as a mismatched local model name to catch. Under
    ``local`` the model string is inert, so any value must pass preflight,
    and the local adapter is selected regardless (no ``OPENAI_API_KEY`` is
    present in ``ENVIRONMENT`` and this must still succeed)."""
    runtime = runtime_config_for("source_evaluator", tier="live").model_copy(
        update={
            "embedding_provider": "local",
            "embedding_model": "text-embedding-3-large",
        }
    )
    client = FakeLangSmithClient()

    await run(
        settings,
        runtime,
        tmp_path,
        cases=cases_for("source_evaluator", "live"),
        langsmith_client=client,
    )


@pytest.mark.asyncio
async def test_the_embedding_model_is_only_checked_for_live_runs(
    settings, runtime_config_for, tmp_path
) -> None:
    """Restored prior art, deleted by the cutover: a controlled run's
    embedding model string is inert -- its memory double is hash-based, so
    nothing ever embeds with it -- and preflight must not reject it."""
    runtime = runtime_config_for("planner").model_copy(
        update={
            "embedding_provider": "openai",
            "embedding_model": "not-a-real-model",
        }
    )

    await run(settings, runtime, tmp_path)


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
