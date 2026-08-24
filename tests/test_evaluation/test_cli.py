"""The evaluation CLI: commands, flags, rendering, and exit codes."""

from __future__ import annotations

import io

import pytest  # noqa: F401 - available for tests that grow a pytest.raises

from deep_research.evaluation.cli import (
    EXIT_FAILED,
    EXIT_INFRASTRUCTURE,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_USAGE,
    _focused_dataset_examples,
    main,
    parse_arguments,
)
from deep_research.evaluation.runner import PreflightError
from tests.evaluation_fakes import FakeDataset, FakeLangSmithClient


def run(argv, **kwargs):
    stream = io.StringIO()
    code = main(argv, stream=stream, **kwargs)
    return code, stream.getvalue()


def _dataset_client(name, examples):
    dataset = FakeDataset(name, "dataset-1")
    client = FakeLangSmithClient(datasets=[dataset])
    client.create_examples(dataset_id=dataset.id, examples=examples)
    return client


def test_focused_selection_returns_only_the_requested_case_in_case_order(
    runtime_config_for, planner_case
) -> None:
    other = {
        "inputs": {"case_id": "other-case"},
        "outputs": {},
        "metadata": {"case_id": "other-case", "case_version": 1},
    }
    wanted = {
        "inputs": {"case_id": planner_case.case_id},
        "outputs": {},
        "metadata": {
            "case_id": planner_case.case_id,
            "case_version": planner_case.version,
        },
    }
    runtime = runtime_config_for("planner", case_id=planner_case.case_id)
    client = _dataset_client(runtime.dataset_name, [other, wanted])

    selected = _focused_dataset_examples(client, runtime, [planner_case])

    assert selected is not None
    assert len(selected) == 1
    assert selected[0].metadata == wanted["metadata"]


@pytest.mark.parametrize("copies", [0, 2])
def test_focused_selection_rejects_missing_or_duplicate_case_rows(
    runtime_config_for, planner_case, copies
) -> None:
    runtime = runtime_config_for("planner", case_id=planner_case.case_id)
    payload = {
        "inputs": {"case_id": planner_case.case_id},
        "outputs": {},
        "metadata": {
            "case_id": planner_case.case_id,
            "case_version": planner_case.version,
        },
    }
    client = _dataset_client(runtime.dataset_name, [payload] * copies)

    with pytest.raises(PreflightError) as captured:
        _focused_dataset_examples(client, runtime, [planner_case])

    assert captured.value.reason == "dataset_unavailable"


def test_non_focused_selection_leaves_dataset_name_path_untouched(
    runtime_config_for, planner_case
) -> None:
    runtime = runtime_config_for("planner")

    assert _focused_dataset_examples(None, runtime, [planner_case]) is None


# --- parsing ---------------------------------------------------------------


def test_the_default_tier_is_controlled() -> None:
    options = parse_arguments(["agent", "researcher"])

    assert options.command == "agent"
    assert options.agent_name == "researcher"
    assert options.tier == "controlled"
    assert options.case_id is None
    assert options.config == "config.yaml"


def test_kebab_agent_names_are_accepted_and_canonicalized() -> None:
    assert parse_arguments(["agent", "source-evaluator"]).agent_name == (
        "source_evaluator"
    )
    assert parse_arguments(["agent", "fact-checker"]).agent_name == (
        "fact_checker"
    )


def test_an_unknown_agent_exits_two_and_lists_the_valid_names() -> None:
    code, output = run(["agent", "librarian"])

    assert code == EXIT_USAGE
    assert "librarian" in output
    for name in ("planner", "source-evaluator", "critic"):
        assert name in output


def test_an_unknown_case_exits_two_and_lists_the_valid_cases() -> None:
    code, output = run(["agent", "researcher", "--case", "not-a-case"])

    assert code == EXIT_USAGE
    assert "not-a-case" in output
    assert "conflicting-evidence" in output


def test_an_unknown_tier_exits_two() -> None:
    code, output = run(["agent", "researcher", "--tier", "staging"])

    assert code == EXIT_USAGE
    assert "controlled" in output


def test_case_selection_is_rejected_for_the_suite() -> None:
    code, output = run(["suite", "--case", "focused-decomposition"])

    assert code == EXIT_USAGE
    assert "--case" in output


def test_a_target_effort_override_is_rejected_for_the_suite() -> None:
    """A suite must not silently mix the approved per-agent profile."""
    code, output = run(["suite", "--reasoning-effort", "high"])

    assert code == EXIT_USAGE
    assert "--reasoning-effort" in output
    assert "suite" in output


def test_a_judge_effort_override_is_accepted_for_the_suite() -> None:
    options = parse_arguments(["suite", "--judge-reasoning-effort", "max"])

    assert options.judge_reasoning_effort == "max"


def test_a_target_effort_override_is_accepted_for_one_agent() -> None:
    options = parse_arguments(
        ["agent", "researcher", "--reasoning-effort", "medium"]
    )

    assert options.reasoning_effort == "medium"


def test_an_invalid_effort_exits_two_and_lists_the_levels() -> None:
    code, output = run(["agent", "researcher", "--reasoning-effort", "turbo"])

    assert code == EXIT_USAGE
    assert "xhigh" in output


def test_every_shared_option_is_parsed() -> None:
    options = parse_arguments(
        [
            "agent",
            "researcher",
            "--config",
            "other.yaml",
            "--tier",
            "live",
            "--output-directory",
            "artifacts/",
            "--experiment-prefix",
            "tuning",
            "--judge-reasoning-effort",
            "max",
            "--verbose",
        ]
    )

    assert options.config == "other.yaml"
    assert options.tier == "live"
    assert options.output_directory == "artifacts/"
    assert options.experiment_prefix == "tuning"
    assert options.judge_reasoning_effort == "max"
    assert options.verbose is True


def test_there_is_no_repetition_count_flag() -> None:
    """The counts are config defaults so the canonical workflow repeats."""
    code, _ = run(["agent", "researcher", "--repetitions", "5"])

    assert code == EXIT_USAGE


# --- list ------------------------------------------------------------------


def test_list_shows_all_six_agents_and_all_cases() -> None:
    code, output = run(["list"])

    assert code == EXIT_OK
    for name in (
        "planner",
        "researcher",
        "source-evaluator",
        "fact-checker",
        "synthesizer",
        "critic",
    ):
        assert name in output
    assert output.count("deep-research-") == 12
    assert "focused-decomposition" in output
    assert "critic-live-review" in output


# --- agent -----------------------------------------------------------------


def test_a_passing_agent_run_exits_zero_and_prints_the_summary(
    passing_runner,
) -> None:
    code, output = run(["agent", "researcher"], runner=passing_runner)

    assert code == EXIT_OK
    assert "REVIEW REQUIRED" in output
    assert "Experiment:" in output
    assert "Review:" in output
    assert "results.json" in output


def test_a_failing_agent_run_exits_one(failing_runner) -> None:
    code, output = run(["agent", "researcher"], runner=failing_runner)

    assert code == EXIT_FAILED
    assert "FAILED" in output


def test_a_preflight_configuration_failure_exits_three(
    preflight_failing_runner,
) -> None:
    code, output = run(["agent", "researcher"], runner=preflight_failing_runner)

    assert code == EXIT_INFRASTRUCTURE
    assert "LANGSMITH_API_KEY" in output


def test_an_invalid_local_registry_exits_two(registry_failing_runner) -> None:
    code, output = run(["agent", "researcher"], runner=registry_failing_runner)

    assert code == EXIT_USAGE


def test_an_interruption_exits_one_hundred_thirty(interrupting_runner) -> None:
    code, output = run(["agent", "researcher"], runner=interrupting_runner)

    assert code == EXIT_INTERRUPTED
    assert "cancelled" in output.lower()


def test_a_partial_failure_still_prints_completed_results(
    partially_failing_runner,
) -> None:
    code, output = run(["agent", "researcher"], runner=partially_failing_runner)

    assert code == EXIT_FAILED
    assert "Repetitions: 9/9" in output


def test_the_live_tier_runs_the_single_live_case(live_runner) -> None:
    code, output = run(["agent", "researcher", "--tier", "live"],
                       runner=live_runner)

    assert code == EXIT_OK
    assert "researcher - live" in output.lower()


def test_the_output_directory_override_reaches_the_runner(
    recording_runner,
) -> None:
    run(["agent", "researcher", "--output-directory", "artifacts/"],
        runner=recording_runner)

    assert recording_runner.calls[0]["output_directory"] == "artifacts/"


def test_nothing_printed_to_the_stream_contains_a_secret(
    leaking_runner, monkeypatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-abcdefgh")

    _, output = run(["agent", "researcher", "--verbose"], runner=leaking_runner)

    assert "sk-abcdefghijklmnop" not in output
