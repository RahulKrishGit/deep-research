"""General hard gates and deterministic score normalization."""

from __future__ import annotations

import pytest

from deep_research.evaluation.evaluators import (
    GENERAL_GATE_IDS,
    MissingMetricError,
    deterministic_quality,
    evaluate_general_gates,
    evaluate_target,
)
from deep_research.evaluation.models import TrajectoryStep


def gate(results, gate_id):
    return next(item for item in results if item.gate_id == gate_id)


def test_the_general_gates_cover_every_rule_the_spec_names() -> None:
    assert set(GENERAL_GATE_IDS) == {
        "agent_constructed",
        "run_completed",
        "contracts_valid",
        "required_fields_present",
        "budgets_respected",
        "errors_typed",
        "citations_known",
        "no_secret_in_output",
        "no_prohibited_calls",
        "trace_available",
        "no_tracker_transport_failure",
    }


def test_a_clean_run_passes_every_general_gate(
    planner_case, clean_target_output
) -> None:
    results = evaluate_general_gates(
        clean_target_output, planner_case, secrets=()
    )

    assert [item.gate_id for item in results] == list(GENERAL_GATE_IDS)
    assert all(item.passed for item in results), [
        item.gate_id for item in results if not item.passed
    ]


def test_a_construction_failure_fails_the_construction_gate(
    planner_case, clean_target_output
) -> None:
    output = clean_target_output.model_copy(
        update={
            "failure": {
                "stage": "construction",
                "reason": "model_unavailable",
                "message": "redacted",
            }
        }
    )

    results = evaluate_general_gates(output, planner_case, secrets=())

    assert gate(results, "agent_constructed").passed is False


def test_a_non_mapping_result_fails_the_contracts_gate(
    planner_case, clean_target_output
) -> None:
    output = clean_target_output.model_copy(update={"result": None})

    results = evaluate_general_gates(output, planner_case, secrets=())

    assert gate(results, "contracts_valid").passed is False


def test_an_unhandled_failure_fails_run_completed(
    planner_case, clean_target_output
) -> None:
    output = clean_target_output.model_copy(
        update={
            "completed": False,
            "result": None,
            "failure": {
                "stage": "unhandled",
                "reason": "provider_exploded",
                "message": "redacted",
            },
        }
    )

    results = evaluate_general_gates(output, planner_case, secrets=())

    assert gate(results, "run_completed").passed is False


def test_a_missing_required_field_fails_its_gate(
    planner_case, clean_target_output
) -> None:
    output = clean_target_output.model_copy(update={"result": {"other": 1}})

    results = evaluate_general_gates(output, planner_case, secrets=())

    assert gate(results, "required_fields_present").passed is False
    assert "sub_topics" in gate(results, "required_fields_present").detail


def test_exceeding_the_iteration_budget_fails_the_budget_gate(
    planner_case, clean_target_output
) -> None:
    react = clean_target_output.react.model_copy(update={"iterations": 99})
    output = clean_target_output.model_copy(update={"react": react})

    results = evaluate_general_gates(output, planner_case, secrets=())

    assert gate(results, "budgets_respected").passed is False


def test_exceeding_the_tool_budget_fails_the_budget_gate(
    planner_case, clean_target_output
) -> None:
    react = clean_target_output.react.model_copy(update={"tool_calls": 99})
    output = clean_target_output.model_copy(update={"react": react})

    results = evaluate_general_gates(output, planner_case, secrets=())

    assert gate(results, "budgets_respected").passed is False


def test_an_untyped_error_record_fails_the_typed_error_gate(
    planner_case, clean_target_output
) -> None:
    output = clean_target_output.model_copy(
        update={"errors": [{"message": "something went wrong"}]}
    )

    results = evaluate_general_gates(output, planner_case, secrets=())

    assert gate(results, "errors_typed").passed is False


def test_an_unknown_citation_fails_the_citation_gate(
    researcher_case, researcher_target_output
) -> None:
    result = dict(researcher_target_output.result)
    result["findings"] = [
        {"source_url": "https://invented.example.com/page"}
    ]
    output = researcher_target_output.model_copy(update={"result": result})

    results = evaluate_general_gates(output, researcher_case, secrets=())

    assert gate(results, "citations_known").passed is False
    assert "invented.example.com" in gate(results, "citations_known").detail


def test_known_citations_pass_the_citation_gate(
    researcher_case, researcher_target_output
) -> None:
    results = evaluate_general_gates(
        researcher_target_output, researcher_case, secrets=()
    )

    assert gate(results, "citations_known").passed is True


def test_a_url_embedded_in_prose_with_trailing_punctuation_passes(
    researcher_case, researcher_target_output
) -> None:
    """Markdown parens, commas, and periods must not mangle extracted URLs."""
    known = researcher_case.expectations.known_source_urls
    result = dict(researcher_target_output.result)
    result["findings"] = [
        {
            "content": (
                "See (the [NREL study]"
                f"({known[0]})), which is extended in "
                f"[the IEA report]({known[1]}), and the "
                f"ScienceDirect analysis at {known[2]}."
            ),
            "source_url": known[0],
            "source_title": "NREL cold-climate heat pump study",
        }
    ]
    output = researcher_target_output.model_copy(update={"result": result})

    results = evaluate_general_gates(output, researcher_case, secrets=())

    assert gate(results, "citations_known").passed is True


def test_a_live_case_without_known_urls_fails_on_unknown_citations(
    live_case_for, researcher_target_output
) -> None:
    """A live case with no known urls must not auto-pass the citation gate."""
    live_case = live_case_for("researcher")
    assert live_case.expectations.known_source_urls == []

    result = dict(researcher_target_output.result)
    result["findings"] = [
        {"source_url": "https://invented.example.com/page"}
    ]
    output = researcher_target_output.model_copy(
        update={"result": result, "trajectory": []}
    )

    results = evaluate_general_gates(output, live_case, secrets=())

    assert gate(results, "citations_known").passed is False
    assert "invented.example.com" in gate(results, "citations_known").detail


def test_a_live_case_without_known_urls_accepts_trajectory_urls(
    live_case_for, researcher_target_output
) -> None:
    """The trajectory check must run even when no known urls are declared."""
    live_case = live_case_for("researcher")
    assert live_case.expectations.known_source_urls == []
    discovered = "https://discovered.example.com/page"

    result = dict(researcher_target_output.result)
    result["findings"] = [
        {"source_url": discovered, "source_title": "Discovered source"}
    ]
    output = researcher_target_output.model_copy(
        update={
            "result": result,
            "trajectory": [
                TrajectoryStep(
                    iteration=1,
                    thought=f"open {discovered}",
                    observation_summary=f"retrieved {discovered}",
                )
            ],
        }
    )

    results = evaluate_general_gates(output, live_case, secrets=())

    assert gate(results, "citations_known").passed is True
    # Not the old auto-pass skip: the trajectory branch really ran.
    assert "skipped" not in gate(results, "citations_known").detail


def test_a_live_case_accepts_urls_from_the_recorded_trajectory(
    researcher_case, researcher_target_output
) -> None:
    live_case = researcher_case.model_copy(update={"tier": "live"})
    discovered = "https://discovered.example.com/page"
    output = researcher_target_output.model_copy(
        update={
            "result": {"findings": [{"source_url": discovered}]},
            "trajectory": [
                TrajectoryStep(
                    iteration=1,
                    observation_summary=f"retrieved {discovered}",
                )
            ],
        }
    )

    results = evaluate_general_gates(output, live_case, secrets=())

    assert gate(results, "citations_known").passed is True


def test_a_secret_anywhere_in_the_output_fails_the_secret_gate(
    planner_case, clean_target_output
) -> None:
    output = clean_target_output.model_copy(
        update={"state_update": {"note": "key sk-abcdefghijklmnop"}}
    )

    results = evaluate_general_gates(
        output, planner_case, secrets=("sk-abcdefghijklmnop",)
    )

    failed = gate(results, "no_secret_in_output")
    assert failed.passed is False
    assert "sk-abcdefghijklmnop" not in failed.detail


def test_a_prohibited_call_fails_its_gate(
    planner_case, clean_target_output
) -> None:
    ledger = clean_target_output.dependencies.model_copy(
        update={"prohibited_calls": ["tavily.search"]}
    )
    output = clean_target_output.model_copy(update={"dependencies": ledger})

    results = evaluate_general_gates(output, planner_case, secrets=())

    assert gate(results, "no_prohibited_calls").passed is False


def test_a_missing_trace_url_fails_the_trace_gate(
    planner_case, clean_target_output
) -> None:
    output = clean_target_output.model_copy(update={"trace_url": None})

    results = evaluate_general_gates(output, planner_case, secrets=())

    assert gate(results, "trace_available").passed is False


def test_a_tracker_transport_failure_fails_its_gate(
    planner_case, clean_target_output
) -> None:
    output = clean_target_output.model_copy(
        update={
            "tracker_errors": [
                {
                    "error_type": "langsmith_tracing_failure",
                    "source": "langsmith",
                    "message": "transport failed",
                }
            ]
        }
    )

    results = evaluate_general_gates(output, planner_case, secrets=())

    assert gate(results, "no_tracker_transport_failure").passed is False


def test_deterministic_quality_is_the_weighted_sum_of_passing_metrics(
    planner_case, clean_target_output
) -> None:
    functions = {
        metric.metric_id: (lambda output, case, index=index: index == 0)
        for index, metric in enumerate(
            planner_case.expectations.deterministic_metrics
        )
    }

    score = deterministic_quality(
        clean_target_output, planner_case, metric_functions=functions
    )

    first = planner_case.expectations.deterministic_metrics[0].weight
    assert score == pytest.approx(first)


def test_deterministic_quality_is_one_when_everything_passes(
    planner_case, clean_target_output
) -> None:
    functions = {
        metric.metric_id: (lambda output, case: True)
        for metric in planner_case.expectations.deterministic_metrics
    }

    assert deterministic_quality(
        clean_target_output, planner_case, metric_functions=functions
    ) == pytest.approx(1.0)


def test_deterministic_quality_is_zero_when_everything_fails(
    planner_case, clean_target_output
) -> None:
    functions = {
        metric.metric_id: (lambda output, case: False)
        for metric in planner_case.expectations.deterministic_metrics
    }

    assert deterministic_quality(
        clean_target_output, planner_case, metric_functions=functions
    ) == pytest.approx(0.0)


def test_a_metric_without_an_implementation_is_a_defect_not_a_zero(
    planner_case, clean_target_output
) -> None:
    """Silently scoring an unimplemented metric zero would hide the bug."""
    with pytest.raises(MissingMetricError) as caught:
        deterministic_quality(
            clean_target_output, planner_case, metric_functions={}
        )

    assert planner_case.expectations.deterministic_metrics[0].metric_id in str(
        caught.value
    )


def test_a_metric_that_raises_scores_zero_and_does_not_abort(
    planner_case, clean_target_output
) -> None:
    """One broken metric must not lose the other nine repetitions' results."""
    metrics_list = planner_case.expectations.deterministic_metrics
    functions = {metric.metric_id: (lambda o, c: True) for metric in metrics_list}

    def boom(output, case):
        raise ValueError("bad metric")

    functions[metrics_list[0].metric_id] = boom

    score = deterministic_quality(
        clean_target_output, planner_case, metric_functions=functions
    )

    assert score == pytest.approx(1.0 - metrics_list[0].weight)


def test_evaluate_target_returns_a_populated_report_and_score(
    planner_case, clean_target_output
) -> None:
    """The gate report must never come back empty (vacuous-pass guard)."""
    report, score = evaluate_target(
        clean_target_output,
        planner_case,
        secrets=(),
        metric_functions={
            metric.metric_id: (lambda output, case: True)
            for metric in planner_case.expectations.deterministic_metrics
        },
    )

    assert [item.gate_id for item in report.results] == list(GENERAL_GATE_IDS)
    assert report.passed is True
    assert score == pytest.approx(1.0)
