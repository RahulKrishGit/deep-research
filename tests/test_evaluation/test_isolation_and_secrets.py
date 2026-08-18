"""Isolation and redaction at the seams between modules."""

from __future__ import annotations

import json

import pytest

from deep_research.evaluation.cases import all_cases
from deep_research.evaluation.config import contains_secret
from deep_research.utils.config import ConfigSettings

SECRETS = ("sk-abcdefghijklmnop", "ls-abcdefghijklmnop", "tvly-abcdefghij")


def test_no_case_fixture_mentions_a_production_memory_collection() -> None:
    production = ConfigSettings().memory.long_term.collection_name
    for case in all_cases():
        assert production not in case.state.model_dump_json()


def test_no_case_fixture_carries_an_absolute_local_path() -> None:
    for case in all_cases():
        body = case.model_dump_json()
        assert "C:\\\\" not in body
        assert ":/Users/" not in body
        assert "/home/" not in body


@pytest.mark.asyncio
async def test_a_controlled_repetition_never_touches_production_memory(
    settings, tracker, tmp_path, runtime_config_for, planner_case
) -> None:
    """The production collection name must never be constructed."""
    from deep_research.evaluation.dependencies import (
        build_controlled_dependencies,
    )

    bundle = build_controlled_dependencies(
        runtime_config_for("planner"),
        planner_case,
        tracker=tracker,
        settings=settings,
        root=tmp_path,
    )

    assert bundle.collection_name != (
        settings.memory.long_term.collection_name
    )
    assert not (tmp_path / settings.memory.long_term.persist_directory).exists()


@pytest.mark.asyncio
async def test_no_evaluation_run_writes_into_the_production_output_directory(
    settings, tracker, tmp_path, runtime_config_for, synthesizer_case
) -> None:
    from deep_research.evaluation.dependencies import (
        build_controlled_dependencies,
    )

    before = sorted(p.name for p in tmp_path.iterdir())
    bundle = build_controlled_dependencies(
        runtime_config_for("synthesizer"),
        synthesizer_case,
        tracker=tracker,
        settings=settings,
        root=tmp_path,
    )

    assert str(bundle.document_directory).startswith(str(tmp_path))
    assert "output" not in before


def test_every_experiment_metadata_block_is_secret_free(
    runtime_config_for, settings
) -> None:
    from deep_research.evaluation.config import experiment_metadata

    for agent_name in ("planner", "researcher", "critic"):
        metadata = experiment_metadata(
            runtime_config_for(agent_name), settings
        )
        assert contains_secret(metadata, SECRETS) == []
        assert "api_key" not in json.dumps(metadata).lower()


def test_every_dataset_example_is_secret_free() -> None:
    from deep_research.evaluation.datasets import example_payload

    for case in all_cases():
        payload = example_payload(case, rubric_version=1)
        assert contains_secret(payload, SECRETS) == []


@pytest.mark.asyncio
async def test_a_target_output_with_a_leaking_error_is_redacted(
    leaking_target_harness, planner_case, runtime_config_for
) -> None:
    target = leaking_target_harness(
        planner_case, runtime_config_for("planner"), secrets=SECRETS
    )

    payload = await target(
        {"case_id": planner_case.case_id, "case_version": 1,
         "agent": "planner", "tier": "controlled"}
    )

    assert contains_secret(payload, SECRETS) == []


def test_the_judge_input_is_redacted_before_it_becomes_a_trace(
    planner_case, leaking_target_output, clean_gate_report
) -> None:
    from deep_research.evaluation.judging import build_judge_input

    judge_input = build_judge_input(
        leaking_target_output, planner_case, clean_gate_report, secrets=SECRETS
    )

    assert contains_secret(judge_input.model_dump(mode="json"), SECRETS) == []


def test_the_artifact_of_a_leaking_run_is_redacted(
    leaking_experiment_result, tmp_path
) -> None:
    from deep_research.evaluation.reporting import write_experiment_artifact

    path = write_experiment_artifact(leaking_experiment_result, root=tmp_path)

    assert contains_secret(
        json.loads(path.read_text(encoding="utf-8")), SECRETS
    ) == []


def test_the_tracker_redactor_still_covers_evaluation_spans(tracker) -> None:
    """Reuse the tracker's redaction rather than reimplementing it."""
    from pydantic import SecretStr

    from deep_research.observability import LangSmithRuntimeConfig, Tracker

    secret_tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False,
            project="evaluation-tests",
            api_key=SecretStr("sk-abcdefghijklmnop"),
        )
    )
    redacted = secret_tracker._anonymize(
        {"note": "key sk-abcdefghijklmnop", "api_key": "sk-abcdefghijklmnop"}
    )

    assert "sk-abcdefghijklmnop" not in json.dumps(redacted)


def test_no_two_repetitions_share_a_session_id(
    runtime_config_for,
) -> None:
    from deep_research.evaluation.factory import evaluation_session_id

    runtime = runtime_config_for("planner")
    ids = {
        evaluation_session_id(runtime, case_id=case.case_id, repetition=n)
        for case in all_cases()
        for n in (1, 2, 3)
    }

    assert len(ids) == len(all_cases()) * 3


def test_the_env_file_is_ignored_and_absent_from_the_worktree() -> None:
    from pathlib import Path

    assert ".env" in Path(".gitignore").read_text(encoding="utf-8")
    assert not Path(".env").exists()
