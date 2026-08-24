"""Experiment execution, aggregation, and the approved thresholds."""

from __future__ import annotations

import pytest

from deep_research.evaluation.cli import _focused_dataset_examples
from deep_research.evaluation.models import EvaluationFailure, GateReport, GateResult
from deep_research.evaluation.runner import (
    PreflightError,
    aggregate_quality,
    build_case_result,
    build_evaluation_summary_feedback,
    decide_status,
    run_agent_evaluation,
)
from tests.evaluation_fakes import (
    FakeDataset,
    FakeEvaluateRunner,
    FakeExample,
    FakeExperimentResults,
    FakeLangSmithClient,
)


def test_the_aggregate_formula_matches_the_spec_exactly() -> None:
    assert aggregate_quality(1.0, 0.0) == pytest.approx(0.40)
    assert aggregate_quality(0.0, 1.0) == pytest.approx(0.60)
    assert aggregate_quality(0.5, 0.5) == pytest.approx(0.50)
    assert aggregate_quality(0.9, 0.8) == pytest.approx(0.84)


def test_the_aggregate_is_not_rounded_before_thresholding() -> None:
    """0.6499… must fail the 0.65 floor, not be rounded up into it."""
    score = aggregate_quality(0.6499, 0.6499)

    assert score < 0.65
    assert f"{score:.2f}" == "0.65"


def test_a_case_passes_only_when_every_repetition_clears_the_floor(
    repetitions_at,
) -> None:
    case_result = build_case_result(
        None, repetitions_at([0.90, 0.90, 0.64]), threshold=0.80
    )

    assert case_result.passed is False


def test_a_case_passes_only_when_the_average_clears_the_average_threshold(
    repetitions_at,
) -> None:
    case_result = build_case_result(
        None, repetitions_at([0.66, 0.80, 0.90]), threshold=0.80
    )

    assert case_result.average_quality == pytest.approx(0.786666, abs=1e-5)
    assert case_result.passed is False


def test_a_case_passes_when_both_rules_are_satisfied(repetitions_at) -> None:
    case_result = build_case_result(
        None, repetitions_at([0.80, 0.82, 0.84]), threshold=0.80
    )

    assert case_result.passed is True


def test_a_case_passes_when_the_floor_and_threshold_are_wired_independently(
    repetitions_at,
) -> None:
    """Every repetition scores between the 0.65 floor and the 0.80
    threshold-clearing average: each repetition clears an explicit
    ``floor=0.65`` on its own, and the 0.8167 average clears a separate
    ``threshold=0.80``. If ``floor``/``threshold`` were ever swapped at a
    call site, every repetition (0.70, 0.85, 0.90) would have to clear an
    0.80 floor instead, and 0.70 would fail it -- so this case flips to
    failing under a swap, making it a genuine regression test for the
    two-threshold wiring, not just a restatement of the single-threshold
    tests above.
    """
    case_result = build_case_result(
        None, repetitions_at([0.70, 0.85, 0.90]), threshold=0.80, floor=0.65
    )

    assert case_result.average_quality == pytest.approx(0.816666, abs=1e-5)
    assert case_result.passed is True


def test_a_repetition_below_the_floor_fails_the_case_despite_a_passing_average(
    repetitions_at,
) -> None:
    """The floor is checked per repetition, independently of the average:
    one repetition at 0.50 is below an explicit ``floor=0.65`` even though
    the case's average (0.80) clears a lower ``threshold=0.60`` -- the
    floor violation must still fail the case.
    """
    case_result = build_case_result(
        None, repetitions_at([0.50, 0.95, 0.95]), threshold=0.60, floor=0.65
    )

    assert case_result.average_quality == pytest.approx(0.80, abs=1e-9)
    assert case_result.passed is False


def test_the_lowest_scoring_repetition_trace_is_selected(
    repetitions_at,
) -> None:
    case_result = build_case_result(
        None, repetitions_at([0.90, 0.71, 0.85]), threshold=0.60
    )

    assert case_result.lowest_scoring_trace_url.endswith("/r2")


def test_a_repetition_without_judge_feedback_fails_the_case(
    repetition_without_judge, repetitions_at
) -> None:
    case_result = build_case_result(
        None,
        [*repetitions_at([0.95, 0.95]), repetition_without_judge],
        threshold=0.80,
    )

    assert case_result.passed is False


def test_a_failed_hard_gate_cannot_be_offset_by_a_high_score(
    repetition_with_failed_gate, repetitions_at
) -> None:
    case_result = build_case_result(
        None,
        [*repetitions_at([1.0, 1.0]), repetition_with_failed_gate],
        threshold=0.80,
    )

    assert case_result.passed is False


def test_controlled_status_is_review_required_when_everything_passes(
    passing_cases, runtime_config_for
) -> None:
    status = decide_status(
        passing_cases, tier="controlled", runtime=runtime_config_for("planner")
    )

    assert status == "REVIEW REQUIRED"


def test_controlled_status_is_failed_when_one_repetition_fails(
    passing_cases, failing_case, runtime_config_for
) -> None:
    status = decide_status(
        [*passing_cases, failing_case],
        tier="controlled",
        runtime=runtime_config_for("planner"),
    )

    assert status == "FAILED"


def _summary_values(payload) -> dict[str, str]:
    return {item["key"]: item["value"] for item in payload["results"]}


def test_the_summary_feedback_matches_the_local_passing_status(
    passing_cases, runtime_config_for
) -> None:
    runtime = runtime_config_for("planner")
    payload = build_evaluation_summary_feedback(
        passing_cases, tier="controlled", runtime=runtime
    )

    assert _summary_values(payload) == {
        "evaluation_status": decide_status(
            passing_cases, tier="controlled", runtime=runtime
        ),
        "evaluation_failure_reason": (
            "all cases passed automated checks; human review required"
        ),
    }


def test_the_summary_feedback_names_the_first_failed_gate(
    failing_experiment_result, runtime_config_for
) -> None:
    payload = build_evaluation_summary_feedback(
        failing_experiment_result.cases,
        tier="controlled",
        runtime=runtime_config_for("synthesizer"),
    )

    assert _summary_values(payload) == {
        "evaluation_status": "FAILED",
        "evaluation_failure_reason": (
            "unsupported-claim repetition 1 failed citations_known"
        ),
    }


def test_the_summary_feedback_names_a_typed_infrastructure_reason(
    runtime_config_for,
) -> None:
    errors = [
        EvaluationFailure(
            stage="trace",
            reason="langsmith_unavailable",
            message="transport text must not be summarized",
        )
    ]
    payload = build_evaluation_summary_feedback(
        [],
        tier="controlled",
        runtime=runtime_config_for("planner"),
        errors=errors,
    )

    assert _summary_values(payload) == {
        "evaluation_status": "INFRASTRUCTURE FAILURE",
        "evaluation_failure_reason": "trace:langsmith_unavailable",
    }


def test_the_failure_summary_never_reads_messages_details_or_rationales(
    repetition_with_failed_gate, runtime_config_for
) -> None:
    secret = "sk-summary-must-not-leak-123456"
    failure = EvaluationFailure(
        stage="provider",
        reason="provider_failure",
        message=f"provider rejected key={secret}",
    )
    repetition = repetition_with_failed_gate.model_copy(
        update={
            "errors": [failure],
            "gates": GateReport(
                results=[
                    GateResult(
                        gate_id="prioritized_subtopics",
                        passed=False,
                        detail=f"raw output contained {secret}",
                    )
                ]
            ),
        }
    )
    case = build_case_result(None, [repetition], threshold=0.80)

    payload = build_evaluation_summary_feedback(
        [case],
        tier="controlled",
        runtime=runtime_config_for("planner"),
    )

    assert _summary_values(payload)["evaluation_failure_reason"] == (
        "focused-decomposition repetition 98 failed provider_failure"
    )
    assert secret not in repr(payload)


def test_the_live_threshold_is_zero_point_seven_five(
    repetitions_at, runtime_config_for
) -> None:
    runtime = runtime_config_for("planner", tier="live")
    passing = build_case_result(None, repetitions_at([0.75]), threshold=0.75)
    failing = build_case_result(None, repetitions_at([0.74]), threshold=0.75)

    assert decide_status([passing], tier="live", runtime=runtime) == (
        "REVIEW REQUIRED"
    )
    assert decide_status([failing], tier="live", runtime=runtime) == "FAILED"


@pytest.mark.asyncio
async def test_a_controlled_experiment_requests_three_repetitions(
    settings, runtime_config_for, tmp_path, evaluation_harness
) -> None:
    runner = FakeEvaluateRunner(examples=evaluation_harness.examples)

    await run_agent_evaluation(
        settings,
        runtime_config_for("planner"),
        cases=evaluation_harness.cases,
        evaluate=runner,
        **evaluation_harness.kwargs(tmp_path),
    )

    assert runner.calls[0]["num_repetitions"] == 3
    assert runner.calls[0]["max_concurrency"] == 1
    assert runner.calls[0]["data"] == "deep-research-planner-controlled-v1"


@pytest.mark.asyncio
async def test_the_langsmith_experiment_url_is_preserved(
    settings, runtime_config_for, tmp_path, evaluation_harness
) -> None:
    runner = FakeEvaluateRunner(examples=evaluation_harness.examples)

    result = await run_agent_evaluation(
        settings,
        runtime_config_for("planner"),
        cases=evaluation_harness.cases,
        evaluate=runner,
        **evaluation_harness.kwargs(tmp_path),
    )

    assert result.experiment_url == "https://smith.langchain.test/experiments/1"


@pytest.mark.asyncio
async def test_a_live_experiment_requests_one_repetition(
    settings, runtime_config_for, tmp_path, live_evaluation_harness
) -> None:
    runner = FakeEvaluateRunner(examples=live_evaluation_harness.examples)

    result = await run_agent_evaluation(
        settings,
        runtime_config_for("planner", tier="live"),
        cases=live_evaluation_harness.cases,
        evaluate=runner,
        **live_evaluation_harness.kwargs(tmp_path),
    )

    assert runner.calls[0]["num_repetitions"] == 1
    assert runner.calls[0]["max_concurrency"] == 1
    repetitions = [r for case in result.cases for r in case.repetitions]
    assert len(repetitions) == 1
    assert repetitions[0].judge is not None
    assert repetitions[0].judge.status == "scored"


@pytest.mark.asyncio
async def test_run_agent_evaluation_passes_one_named_summary_evaluator(
    settings, runtime_config_for, tmp_path, evaluation_harness
) -> None:
    runner = FakeEvaluateRunner(examples=evaluation_harness.examples)

    await run_agent_evaluation(
        settings,
        runtime_config_for("planner"),
        cases=evaluation_harness.cases,
        evaluate=runner,
        **evaluation_harness.kwargs(tmp_path),
    )

    summaries = runner.calls[0]["summary_evaluators"]
    assert len(summaries) == 1
    assert summaries[0].__name__ == "evaluation_status"


@pytest.mark.asyncio
async def test_the_summary_observes_every_completed_row_before_it_runs(
    settings,
    runtime_config_for,
    tmp_path,
    partially_failing_harness,
    monkeypatch,
) -> None:
    import deep_research.evaluation.runner as runner_module

    original_evaluate_target = runner_module.evaluate_target

    def evaluate_with_available_trace(output, case, *, secrets):
        gates, quality = original_evaluate_target(output, case, secrets=secrets)
        return (
            GateReport(
                results=[
                    gate.model_copy(update={"passed": True})
                    if gate.gate_id == "trace_available"
                    else gate
                    for gate in gates.results
                ]
            ),
            quality,
        )

    monkeypatch.setattr(
        runner_module, "evaluate_target", evaluate_with_available_trace
    )
    runner = FakeEvaluateRunner(examples=partially_failing_harness.examples)

    result = await run_agent_evaluation(
        settings,
        runtime_config_for("planner"),
        cases=partially_failing_harness.cases,
        evaluate=runner,
        **partially_failing_harness.kwargs(tmp_path),
    )

    assert len(runner.rows) == 9
    assert _summary_values({"results": runner.summary_feedback}) == {
        "evaluation_status": result.status,
        "evaluation_failure_reason": (
            "focused-decomposition repetition 2 failed provider_failure"
        ),
    }


@pytest.mark.asyncio
async def test_a_full_controlled_run_produces_nine_repetitions(
    settings, runtime_config_for, tmp_path, evaluation_harness
) -> None:
    """Three cases times three repetitions, each with a judge evaluation."""
    runner = FakeEvaluateRunner(examples=evaluation_harness.examples)

    result = await run_agent_evaluation(
        settings,
        runtime_config_for("planner"),
        cases=evaluation_harness.cases,
        evaluate=runner,
        **evaluation_harness.kwargs(tmp_path),
    )

    repetitions = [r for case in result.cases for r in case.repetitions]
    assert len(result.cases) == 3
    assert len(repetitions) == 9
    assert all(r.judge is not None for r in repetitions)
    assert all(r.judge.status == "scored" for r in repetitions)


@pytest.mark.asyncio
async def test_a_focused_case_run_produces_three_repetitions(
    settings, runtime_config_for, tmp_path, evaluation_harness
) -> None:
    focused = evaluation_harness.for_case("focused-decomposition")
    runner = FakeEvaluateRunner(examples=focused.examples)

    result = await run_agent_evaluation(
        settings,
        runtime_config_for("planner", case_id="focused-decomposition"),
        cases=focused.cases,
        evaluate=runner,
        **focused.kwargs(tmp_path),
    )

    assert len(result.cases) == 1
    assert len(result.cases[0].repetitions) == 3
    judges = [r.judge for r in result.cases[0].repetitions]
    assert len(judges) == 3
    assert all(judge.status == "scored" for judge in judges)


@pytest.mark.asyncio
async def test_a_focused_run_passes_only_selected_examples_to_langsmith(
    settings, runtime_config_for, tmp_path, evaluation_harness
) -> None:
    focused = evaluation_harness.for_case("focused-decomposition")
    runtime = runtime_config_for("planner", case_id="focused-decomposition")
    dataset = FakeDataset(runtime.dataset_name, "dataset-1")
    client = FakeLangSmithClient(datasets=[dataset])
    client.create_examples(dataset_id=dataset.id, examples=focused.examples)
    selected = _focused_dataset_examples(client, runtime, focused.cases)
    assert selected is not None

    runner = FakeEvaluateRunner(examples=evaluation_harness.examples)

    await run_agent_evaluation(
        settings,
        runtime,
        cases=focused.cases,
        dataset_examples=selected,
        evaluate=runner,
        **focused.kwargs(tmp_path),
    )

    assert runner.calls[0]["data"] == selected
    assert len(runner.rows) == 3
    assert {
        (row["outputs"]["case_id"], row["outputs"]["case_version"])
        for row in runner.rows
    } == {focused.cases[0].identity}


@pytest.mark.asyncio
async def test_a_wrong_case_dataset_example_is_rejected_before_evaluation(
    settings, runtime_config_for, tmp_path, evaluation_harness
) -> None:
    focused = evaluation_harness.for_case("focused-decomposition")
    wrong = FakeExample(
        "wrong-example",
        {"case_id": "other-case"},
        {},
        {"case_id": "other-case", "case_version": 1},
    )
    runner = FakeEvaluateRunner(examples=focused.examples)

    with pytest.raises(PreflightError) as captured:
        await run_agent_evaluation(
            settings,
            runtime_config_for("planner", case_id="focused-decomposition"),
            cases=focused.cases,
            dataset_examples=[wrong],
            evaluate=runner,
            **focused.kwargs(tmp_path),
        )

    assert captured.value.reason == "dataset_unavailable"
    assert runner.calls == []


@pytest.mark.asyncio
async def test_experiment_url_falls_back_to_comparison_url(
    settings, runtime_config_for, tmp_path, evaluation_harness
) -> None:
    comparison_url = "https://smith.langchain.test/compare/1"
    results = FakeExperimentResults(
        experiment_name="experiment",
        url=None,
        comparison_url=comparison_url,
    )
    runner = FakeEvaluateRunner(
        examples=evaluation_harness.examples, results=results
    )

    result = await run_agent_evaluation(
        settings,
        runtime_config_for("planner"),
        cases=evaluation_harness.cases,
        evaluate=runner,
        **evaluation_harness.kwargs(tmp_path),
    )

    assert result.experiment_url == comparison_url


@pytest.mark.asyncio
async def test_experiment_url_failure_is_auxiliary_and_preserves_quality_status(
    settings, runtime_config_for, tmp_path, evaluation_harness
) -> None:
    baseline = await run_agent_evaluation(
        settings,
        runtime_config_for("planner"),
        cases=evaluation_harness.cases,
        evaluate=FakeEvaluateRunner(examples=evaluation_harness.examples),
        **{
            **evaluation_harness.kwargs(tmp_path / "baseline"),
        },
    )
    results = FakeExperimentResults(
        experiment_name="experiment",
        url=None,
        comparison_error=ConnectionError("url unavailable"),
    )
    runner = FakeEvaluateRunner(
        examples=evaluation_harness.examples, results=results
    )

    result = await run_agent_evaluation(
        settings,
        runtime_config_for("planner"),
        cases=evaluation_harness.cases,
        evaluate=runner,
        **evaluation_harness.kwargs(tmp_path),
    )

    assert result.status == baseline.status
    assert result.experiment_url is None
    assert any(
        error.reason == "experiment_url_unavailable" for error in result.errors
    )


@pytest.mark.asyncio
async def test_a_gate_evaluation_exception_is_recorded_not_dropped(
    settings, runtime_config_for, tmp_path, evaluation_harness, monkeypatch
) -> None:
    """Finding 16's defense in depth: even though the root-cause fix makes
    ``normalize_source_url`` total, ``_dispatch_code``'s ``try`` around
    ``evaluate_target`` must widen past ``ValidationError`` so that ANY
    unexpected exception from gate evaluation is caught, rather than
    relying solely on ``normalize_source_url`` never raising again.

    Pre-fix, an exception escaping ``evaluate_target`` here would leave
    ``pending_gates[key]`` unset, and ``_dispatch_judge``'s
    ``if gates is not None`` guard would then silently omit the repetition
    from ``repetitions_by_case`` -- finding 14's failure mode, reachable
    through any exception, not only the one this round's root-cause fix
    closes. Post-fix, the repetition must still appear, scored as a
    failed gate, never silently dropped.
    """
    import deep_research.evaluation.runner as runner_module

    def _boom(output, case, *, secrets):
        raise ValueError("simulated: Port out of range 0-65535")

    monkeypatch.setattr(runner_module, "evaluate_target", _boom)

    focused = evaluation_harness.for_case("focused-decomposition")
    runner = FakeEvaluateRunner(examples=focused.examples)

    result = await run_agent_evaluation(
        settings,
        runtime_config_for("planner", case_id="focused-decomposition"),
        cases=focused.cases,
        evaluate=runner,
        **focused.kwargs(tmp_path),
    )

    assert len(result.cases) == 1
    repetitions = result.cases[0].repetitions
    # Not dropped: all three repetitions are still present.
    assert len(repetitions) == 3
    assert all(r.gates.passed is False for r in repetitions)
    assert all(
        "gate_evaluation_error" in r.gates.failed_ids for r in repetitions
    )
    assert all(r.deterministic_quality == 0.0 for r in repetitions)


@pytest.mark.asyncio
async def test_run_agent_evaluation_wires_the_threshold_and_floor_correctly(
    settings, runtime_config_for, tmp_path, evaluation_harness, monkeypatch
) -> None:
    """Spies on ``build_case_result`` to capture the exact ``threshold=``
    and ``floor=`` keywords ``run_agent_evaluation`` passes for a
    controlled run, and asserts they equal ``runtime.case_average_threshold``
    (0.80) and ``runtime.repetition_floor`` (0.65) respectively -- two
    genuinely different runtime values, not the same one twice.

    A ``case.passed`` assertion cannot exercise this: every repetition in
    this offline harness fails the ``trace_available`` gate (the fake
    target never runs inside real LangSmith tracing, so ``trace_url`` is
    always blank), which forces ``passed`` to ``False`` regardless of the
    quality thresholds -- a swapped or collapsed ``threshold=``/``floor=``
    would be invisible to a ``case.passed`` assertion in this harness. The
    call-site keywords themselves are the one place a swap or collapse is
    actually observable end to end, without touching the confirmed-correct
    logic inside ``build_case_result`` itself.
    """
    import deep_research.evaluation.runner as runner_module

    captured_calls: list[dict[str, float | None]] = []
    original_build_case_result = runner_module.build_case_result

    def spying_build_case_result(case, repetitions, *, threshold, floor=None):
        captured_calls.append({"threshold": threshold, "floor": floor})
        return original_build_case_result(
            case, repetitions, threshold=threshold, floor=floor
        )

    monkeypatch.setattr(
        runner_module, "build_case_result", spying_build_case_result
    )

    runner = FakeEvaluateRunner(examples=evaluation_harness.examples)
    runtime = runtime_config_for("planner")

    await run_agent_evaluation(
        settings,
        runtime,
        cases=evaluation_harness.cases,
        evaluate=runner,
        **evaluation_harness.kwargs(tmp_path),
    )

    assert captured_calls, "build_case_result was never invoked"
    assert runtime.case_average_threshold == pytest.approx(0.80)
    assert runtime.repetition_floor == pytest.approx(0.65)
    assert runtime.case_average_threshold != runtime.repetition_floor
    for call in captured_calls:
        assert call["threshold"] == pytest.approx(
            runtime.case_average_threshold
        )
        assert call["floor"] == pytest.approx(runtime.repetition_floor)


@pytest.mark.asyncio
async def test_the_experiment_metadata_travels_to_langsmith(
    settings, runtime_config_for, tmp_path, evaluation_harness
) -> None:
    runner = FakeEvaluateRunner(examples=evaluation_harness.examples)

    await run_agent_evaluation(
        settings,
        runtime_config_for("planner"),
        cases=evaluation_harness.cases,
        evaluate=runner,
        **evaluation_harness.kwargs(tmp_path),
    )

    metadata = runner.calls[0]["metadata"]
    assert metadata["target_reasoning_effort"] == "max"
    assert metadata["judge_reasoning_effort"] == "max"
    assert metadata["configuration_fingerprint"]
    assert metadata["target_prompt_fingerprint"]
    assert "api_key" not in repr(metadata).lower()


@pytest.mark.asyncio
async def test_one_failed_repetition_does_not_stop_the_other_cases(
    settings, runtime_config_for, tmp_path, partially_failing_harness
) -> None:
    runner = FakeEvaluateRunner(examples=partially_failing_harness.examples)

    result = await run_agent_evaluation(
        settings,
        runtime_config_for("planner"),
        cases=partially_failing_harness.cases,
        evaluate=runner,
        **partially_failing_harness.kwargs(tmp_path),
    )

    assert len(result.cases) == 3
    assert result.status == "FAILED"
    assert sum(len(case.repetitions) for case in result.cases) == 9


@pytest.mark.asyncio
async def test_a_langsmith_transport_failure_is_infrastructure_failure(
    settings, runtime_config_for, tmp_path, evaluation_harness
) -> None:
    async def exploding(target, /, **kwargs):
        raise ConnectionError("langsmith is unreachable")

    result = await run_agent_evaluation(
        settings,
        runtime_config_for("planner"),
        cases=evaluation_harness.cases,
        evaluate=exploding,
        **evaluation_harness.kwargs(tmp_path),
    )

    assert result.status == "INFRASTRUCTURE FAILURE"
    assert result.errors[0].stage == "trace"


@pytest.mark.asyncio
async def test_the_artifact_is_written_and_revalidates(
    settings, runtime_config_for, tmp_path, evaluation_harness
) -> None:
    import json

    from deep_research.evaluation.models import ExperimentResult

    runtime = runtime_config_for("planner", output_directory=str(tmp_path))
    runner = FakeEvaluateRunner(examples=evaluation_harness.examples)

    result = await run_agent_evaluation(
        settings,
        runtime,
        cases=evaluation_harness.cases,
        evaluate=runner,
        **evaluation_harness.kwargs(tmp_path),
    )

    path = runtime.output_root / "results.json"
    assert path.is_file()
    restored = ExperimentResult.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )
    assert restored == result
    assert len(
        [r for case in restored.cases for r in case.repetitions]
    ) == 9
    assert set(_summary_values({"results": runner.summary_feedback})) == {
        "evaluation_status",
        "evaluation_failure_reason",
    }
    assert all(
        "evaluation_status" not in repr(row["feedback"])
        and "evaluation_failure_reason" not in repr(row["feedback"])
        for row in runner.rows
    )
