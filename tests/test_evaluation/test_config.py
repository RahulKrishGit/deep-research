"""Effort precedence, fingerprints, naming, and secret handling."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from deep_research.evaluation.config import (
    GitMetadata,
    SecretLeakError,
    build_runtime_config,
    contains_secret,
    dataset_name,
    experiment_metadata,
    experiment_name,
    fingerprint,
    judge_llm_config,
    known_secret_values,
    redact_secrets,
    resolve_git_metadata,
    resolve_judge_effort,
    resolve_target_effort,
    target_llm_config,
)
from deep_research.utils.config import ConfigSettings, EvaluationConfig

NOW = datetime(2026, 8, 16, 10, 15, 0, tzinfo=timezone.utc)
GIT = GitMetadata(commit="abc1234def", short_sha="abc1234", dirty=False)


def test_the_baseline_efforts_match_the_approved_profile() -> None:
    config = EvaluationConfig()

    assert resolve_target_effort(config, "planner", override=None) == "medium"
    assert resolve_target_effort(config, "researcher", override=None) == "low"
    assert (
        resolve_target_effort(config, "source_evaluator", override=None)
        == "low"
    )
    assert (
        resolve_target_effort(config, "fact_checker", override=None) == "medium"
    )
    assert (
        resolve_target_effort(config, "synthesizer", override=None) == "medium"
    )
    assert resolve_target_effort(config, "critic", override=None) == "medium"
    assert resolve_judge_effort(config, override=None) == "high"


def test_an_invocation_override_beats_the_per_agent_override() -> None:
    assert (
        resolve_target_effort(
            EvaluationConfig(), "researcher", override="xhigh"
        )
        == "xhigh"
    )


def test_the_per_agent_override_beats_the_global_default() -> None:
    config = EvaluationConfig(
        target_reasoning_effort="high",
        target_reasoning_effort_overrides={"researcher": "low"},
    )

    assert resolve_target_effort(config, "researcher", override=None) == "low"
    assert resolve_target_effort(config, "planner", override=None) == "high"


def test_the_global_default_applies_when_no_override_exists() -> None:
    config = EvaluationConfig(
        target_reasoning_effort="xhigh",
        target_reasoning_effort_overrides={},
    )

    assert resolve_target_effort(config, "critic", override=None) == "xhigh"


def test_judge_effort_is_independent_of_every_target_effort() -> None:
    config = EvaluationConfig(
        target_reasoning_effort="low",
        target_reasoning_effort_overrides={"planner": "low"},
        judge_reasoning_effort="high",
    )

    assert resolve_judge_effort(config, override=None) == "high"
    assert resolve_judge_effort(config, override="max") == "max"


def test_an_invalid_effort_lists_the_valid_levels() -> None:
    with pytest.raises(ValueError) as caught:
        resolve_target_effort(EvaluationConfig(), "planner", override="turbo")

    assert "xhigh" in str(caught.value)


def build(**kwargs):
    defaults = dict(
        agent_name="researcher",
        tier="controlled",
        case_id=None,
        reasoning_effort=None,
        judge_reasoning_effort=None,
        output_directory=None,
        experiment_prefix=None,
        now=NOW,
        git=GIT,
    )
    defaults.update(kwargs)
    return build_runtime_config(ConfigSettings(), **defaults)


def test_the_runtime_config_freezes_both_efforts() -> None:
    runtime = build()

    assert runtime.target_reasoning_effort == "low"
    assert runtime.judge_reasoning_effort == "high"
    assert runtime.reasoning_mode == "standard"
    with pytest.raises(ValueError):
        runtime.target_reasoning_effort = "high"


def test_controlled_and_live_repetition_counts() -> None:
    assert build(tier="controlled").repetitions == 3
    assert build(tier="live").repetitions == 1
    assert build().max_concurrency == 1


def test_changing_a_target_effort_refingerprints_but_reuses_the_dataset() -> None:
    baseline = build()
    changed = build(reasoning_effort="medium")

    assert (
        changed.configuration_fingerprint != baseline.configuration_fingerprint
    )
    assert changed.dataset_name == baseline.dataset_name


def test_changing_the_judge_effort_refingerprints_the_judge() -> None:
    baseline = build()
    changed = build(judge_reasoning_effort="max")

    assert (
        changed.judge_configuration_fingerprint
        != baseline.judge_configuration_fingerprint
    )
    assert changed.dataset_name == baseline.dataset_name


def test_dataset_names_carry_the_agent_tier_and_schema_version() -> None:
    assert (
        dataset_name("source_evaluator", "controlled", 1)
        == "deep-research-source-evaluator-controlled-v1"
    )
    assert dataset_name("planner", "live", 2) == "deep-research-planner-live-v2"


def test_experiment_names_follow_the_agreed_shape() -> None:
    assert (
        experiment_name(
            "planner", "controlled", now=NOW, git_sha="abc1234", prefix=None
        )
        == "planner-controlled-20260816T101500Z-abc1234"
    )
    assert experiment_name(
        "planner", "controlled", now=NOW, git_sha="abc1234", prefix="tuning"
    ) == "tuning-planner-controlled-20260816T101500Z-abc1234"


def test_the_target_llm_config_carries_the_frozen_effort_and_model() -> None:
    llm = target_llm_config(build(agent_name="planner"), ConfigSettings().llm)

    assert llm.model == "gpt-5.6-luna"
    assert llm.reasoning_effort == "medium"
    assert llm.reasoning_mode == "standard"
    assert llm.model_overrides == {}


def test_the_judge_llm_config_is_independent_of_the_target() -> None:
    llm = judge_llm_config(
        build(agent_name="researcher"), ConfigSettings().llm
    )

    assert llm.model == "gpt-5.6-luna"
    assert llm.reasoning_effort == "high"
    assert llm.temperature == 0.0


def test_the_output_root_is_per_agent_and_per_experiment() -> None:
    runtime = build(agent_name="source_evaluator")

    assert runtime.output_root.parts[-3:] == (
        "evaluations",
        "source-evaluator",
        runtime.experiment_name,
    )


def test_experiment_metadata_records_everything_the_spec_names() -> None:
    metadata = experiment_metadata(build(agent_name="planner"), ConfigSettings())

    for key in (
        "agent",
        "tier",
        "git_commit",
        "git_dirty",
        "target_model",
        "target_reasoning_effort",
        "reasoning_mode",
        "configuration_fingerprint",
        "judge_model",
        "judge_reasoning_effort",
        "judge_configuration_fingerprint",
        "case_registry_version",
        "rubric_version",
        "dependency_mode",
        "target_prompt_fingerprint",
        "evaluation_package_version",
        "experiment_name",
    ):
        assert key in metadata, key


def test_fingerprints_are_stable_and_order_insensitive() -> None:
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})
    assert len(fingerprint({"a": 1})) == 12
    assert fingerprint({"a": 1}) != fingerprint({"a": 2})


def test_git_metadata_survives_a_missing_git_binary() -> None:
    def failing_run(*args, **kwargs):
        raise FileNotFoundError("git")

    metadata = resolve_git_metadata(run=failing_run)

    assert metadata.commit == "unknown"
    assert metadata.short_sha == "unknown"
    assert metadata.dirty is True


def test_known_secret_values_ignores_blank_and_short_values() -> None:
    environ = {
        "OPENAI_API_KEY": "sk-abcdefghijklmnop",
        "LANGSMITH_API_KEY": "   ",
        "TAVILY_API_KEY": "tvly-1234567890",
        "SOMETHING_ELSE": "not-a-secret",
    }

    assert known_secret_values(environ) == (
        "sk-abcdefghijklmnop",
        "tvly-1234567890",
    )


def test_contains_secret_finds_a_key_nested_anywhere() -> None:
    payload = {"metadata": {"notes": ["prefix sk-abcdefghijklmnop suffix"]}}

    assert contains_secret(payload, ("sk-abcdefghijklmnop",)) == [
        "metadata.notes[0]"
    ]


def test_contains_secret_returns_empty_for_clean_payloads() -> None:
    assert contains_secret({"a": "clean"}, ("sk-abcdefghijklmnop",)) == []


def test_redact_secrets_replaces_every_occurrence() -> None:
    payload = {"a": "sk-abcdefghijklmnop", "b": ["x sk-abcdefghijklmnop"]}

    assert redact_secrets(payload, ("sk-abcdefghijklmnop",)) == {
        "a": "[REDACTED]",
        "b": ["x [REDACTED]"],
    }


def test_a_secret_leak_error_never_repeats_the_secret() -> None:
    error = SecretLeakError.for_paths(["metadata.notes[0]"])

    assert "sk-" not in str(error)
    assert "metadata.notes[0]" in str(error)

