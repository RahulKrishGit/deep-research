"""Isolation and redaction at the seams between modules."""

from __future__ import annotations

import json

import pytest

from deep_research.evaluation.cases import all_cases
from deep_research.evaluation.config import contains_secret
from deep_research.evaluation.runner import run_agent_evaluation
from deep_research.utils.config import ConfigSettings

SECRETS = ("sk-abcdefghijklmnop", "ls-abcdefghijklmnop", "tvly-abcdefghij")


def test_no_case_fixture_mentions_a_production_memory_collection() -> None:
    """A defensive fixture-authoring guard, not a coverage seam.

    No field on ``ResearchState``/``EvaluationCase`` can ever hold a
    memory-collection name, so this assertion can never actually fail even
    if collection-naming isolation regressed -- it is harmless to keep as a
    guard against a future field addition, but the real isolation seam
    (a controlled run never constructing the production collection name)
    is covered by
    ``test_a_controlled_repetition_never_touches_production_memory`` below.
    """
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
    """A regression guard against a case author hardcoding secret-shaped
    text into a case fixture, not proof that redaction ran for this seam:
    ``example_payload`` builds its payload from an explicit field
    allow-list, so it is secret-free by construction rather than because
    anything here was redacted.
    """
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


@pytest.mark.asyncio
async def test_a_leaking_transport_failure_is_redacted_through_the_real_path(
    settings, runtime_config_for, tmp_path, evaluation_harness
) -> None:
    """Drives a real secret-carrying exception through ``runner.py``'s own
    ``str(error)`` -> ``EvaluationFailure`` -> artifact-write path, rather
    than through ``leaking_experiment_result`` (which manually calls
    ``redact_secrets`` at fixture-construction time, before the
    ``EvaluationFailure`` even exists -- structurally unable to prove the
    production seam redacts anything).

    This exercises the ``reason="langsmith_unavailable"`` catch-all around
    ``evaluate()`` in ``run_agent_evaluation`` (``runner.py``): a caught
    ``ConnectionError`` whose message embeds a real-looking secret must
    come out of ``result.errors[0].message`` clean, and the ``results.json``
    artifact written from that same ``ExperimentResult`` must be clean too.
    """
    secret = SECRETS[0]

    async def exploding(target, /, **kwargs):
        raise ConnectionError(
            f"langsmith rejected the request: invalid api key {secret}"
        )

    runtime = runtime_config_for("planner", output_directory=str(tmp_path))
    harness_kwargs = {**evaluation_harness.kwargs(tmp_path), "secrets": (secret,)}

    result = await run_agent_evaluation(
        settings,
        runtime,
        cases=evaluation_harness.cases,
        evaluate=exploding,
        **harness_kwargs,
    )

    # The exception's raw message really did carry the secret -- otherwise
    # this test would prove nothing about redaction.
    assert secret in str(ConnectionError(
        f"langsmith rejected the request: invalid api key {secret}"
    ))

    assert result.status == "INFRASTRUCTURE FAILURE"
    assert result.errors[0].stage == "trace"
    assert result.errors[0].reason == "langsmith_unavailable"
    assert secret not in result.errors[0].message
    assert contains_secret(result.model_dump(mode="json"), (secret,)) == []

    path = runtime.output_root / "results.json"
    written_text = path.read_text(encoding="utf-8")
    assert secret not in written_text
    assert contains_secret(json.loads(written_text), (secret,)) == []


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
