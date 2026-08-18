"""Terminal rendering and durable JSON artifacts."""

from __future__ import annotations

import json

import pytest  # noqa: F401 - available for tests that grow a pytest.raises

from deep_research.evaluation.models import ExperimentResult
from deep_research.evaluation.reporting import (
    format_score,
    render_experiment,
    render_listing,
    render_suite,  # noqa: F401 - imported to assert the module's public surface
    write_experiment_artifact,
)


def test_scores_render_to_two_decimals_and_never_lie(
) -> None:
    assert format_score(0.8649) == "0.86"
    assert format_score(0.9) == "0.90"
    assert format_score(None) == "n/a"


def test_the_summary_matches_the_shape_the_spec_shows(
    researcher_experiment_result,
) -> None:
    lines = render_experiment(researcher_experiment_result, verbose=False)
    body = "\n".join(lines)

    assert lines[0] == "Researcher - controlled"
    assert "Cases:       3/3 passed" in body
    assert "Repetitions: 9/9 completed" in body
    assert "Hard gates:  9/9 passed" in body
    assert "Mean score:  0.86" in body
    assert "Status:      REVIEW REQUIRED" in body
    assert "Experiment:  https://" in body
    assert "Review:" in body
    assert "multi-source-coverage" in body
    assert "conflicting-evidence" in body
    assert "partial-search-failure" in body
    assert "Results: " in body
    assert "results.json" in body


def test_the_review_block_links_the_lowest_scoring_trace_per_case(
    researcher_experiment_result,
) -> None:
    body = "\n".join(render_experiment(researcher_experiment_result,
                                       verbose=False))

    for case in researcher_experiment_result.cases:
        assert case.lowest_scoring_trace_url in body


def test_a_failed_experiment_names_the_failed_gates(
    failing_experiment_result,
) -> None:
    body = "\n".join(render_experiment(failing_experiment_result,
                                       verbose=False))

    assert "Status:      FAILED" in body
    assert "citations_known" in body


def test_verbose_output_adds_per_repetition_lines(
    researcher_experiment_result,
) -> None:
    plain = render_experiment(researcher_experiment_result, verbose=False)
    verbose = render_experiment(researcher_experiment_result, verbose=True)

    assert len(verbose) > len(plain)
    assert any("repetition 1" in line for line in verbose)
    assert any("deterministic" in line for line in verbose)
    assert any("judge" in line for line in verbose)


def test_verbose_output_prints_no_model_payload_and_no_secret(
    researcher_experiment_result,
) -> None:
    body = "\n".join(render_experiment(researcher_experiment_result,
                                       verbose=True))

    assert "sk-" not in body
    assert "api_key" not in body.lower()
    assert "messages" not in body.lower()


def test_a_judge_not_run_repetition_is_shown_with_its_reason(
    judge_not_run_experiment_result,
) -> None:
    body = "\n".join(render_experiment(judge_not_run_experiment_result,
                                       verbose=True))

    assert "judge_not_run" in body
    assert "no_evaluable_output" in body


def test_the_listing_shows_every_agent_case_and_dataset(
) -> None:
    body = "\n".join(
        render_listing(
            dataset_version=1,
            repetitions={"controlled": 3, "live": 1},
        )
    )

    for name in (
        "planner",
        "researcher",
        "source-evaluator",
        "fact-checker",
        "synthesizer",
        "critic",
    ):
        assert name in body
        assert f"deep-research-{name}-controlled-v1" in body
        assert f"deep-research-{name}-live-v1" in body

    assert "focused-decomposition" in body
    assert "planner-live-scope" in body
    assert "missing-evidence-or-budget-exhausted" in body
    assert body.count("controlled") >= 6
    assert "3 repetitions" in body
    assert "1 repetition" in body


def test_the_listing_counts_three_controlled_and_one_live_per_agent() -> None:
    from deep_research.evaluation.cases import cases_for
    from deep_research.evaluation.models import AGENT_NAMES

    body = "\n".join(
        render_listing(dataset_version=1,
                       repetitions={"controlled": 3, "live": 1})
    )

    for agent_name in AGENT_NAMES:
        for case in cases_for(agent_name, "controlled"):
            assert case.case_id in body
        assert cases_for(agent_name, "live")[0].case_id in body


def test_the_artifact_lands_at_the_documented_path(
    researcher_experiment_result, tmp_path
) -> None:
    root = tmp_path / "researcher" / researcher_experiment_result.experiment_name

    path = write_experiment_artifact(researcher_experiment_result, root=root)

    assert path == root / "results.json"
    assert path.is_file()


def test_the_artifact_round_trips_through_strict_models(
    researcher_experiment_result, tmp_path
) -> None:
    path = write_experiment_artifact(researcher_experiment_result,
                                     root=tmp_path)

    restored = ExperimentResult.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )

    assert restored == researcher_experiment_result


def test_the_artifact_contains_every_repetition_result(
    researcher_experiment_result, tmp_path
) -> None:
    path = write_experiment_artifact(researcher_experiment_result,
                                     root=tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))

    repetitions = [
        item for case in payload["cases"] for item in case["repetitions"]
    ]
    assert len(repetitions) == 9
    for item in repetitions:
        assert "gates" in item
        assert "deterministic_quality" in item
        assert "judge" in item
        assert "trace_url" in item


def test_the_artifact_records_both_model_identifiers_and_the_fingerprints(
    researcher_experiment_result, tmp_path
) -> None:
    path = write_experiment_artifact(researcher_experiment_result,
                                     root=tmp_path)
    metadata = json.loads(path.read_text(encoding="utf-8"))["metadata"]

    assert metadata["target_model"] == "gpt-5.6-luna"
    assert metadata["target_model_returned"]
    assert metadata["target_reasoning_effort"]
    assert metadata["judge_reasoning_effort"]
    assert metadata["reasoning_mode"] == "standard"
    assert metadata["configuration_fingerprint"]
    assert metadata["judge_configuration_fingerprint"]


def test_the_artifact_never_contains_a_secret_or_a_raw_exception(
    leaking_experiment_result, tmp_path
) -> None:
    path = write_experiment_artifact(leaking_experiment_result, root=tmp_path)
    body = path.read_text(encoding="utf-8")

    assert "sk-abcdefghijklmnop" not in body
    assert "Traceback" not in body


def test_writing_an_artifact_creates_missing_parents(
    researcher_experiment_result, tmp_path
) -> None:
    root = tmp_path / "a" / "b" / "c"

    path = write_experiment_artifact(researcher_experiment_result, root=root)

    assert path.is_file()
