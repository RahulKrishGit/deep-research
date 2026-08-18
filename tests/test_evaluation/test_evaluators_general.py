"""General hard gates and deterministic score normalization."""

from __future__ import annotations

import pytest

from deep_research.agents.sources import normalize_source_url
from deep_research.evaluation.cases import all_cases
from deep_research.evaluation.evaluators import (
    GENERAL_GATE_IDS,
    MissingMetricError,
    deterministic_quality,
    evaluate_general_gates,
    evaluate_target,
)
from deep_research.evaluation.models import (
    DependencyLedger,
    EvaluationCase,
    EvidenceContext,
    ReActSummary,
    TargetOutput,
    TrajectoryStep,
)


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

    general_ids = [item.gate_id for item in report.results][: len(GENERAL_GATE_IDS)]
    assert general_ids == list(GENERAL_GATE_IDS)
    # This fixture only satisfies the general gates; the agent gates follow.
    assert all(item.passed for item in report.results[: len(GENERAL_GATE_IDS)])
    assert score == pytest.approx(1.0)


# --- Golden-output regression test over the real case registry -------------
#
# Every gate test above builds a hand-crafted ``TargetOutput`` whose URLs
# were chosen, by construction, to match a hand-crafted case. That is
# exactly why two Critical defects in ``_gate_citations_known`` survived 28
# scoped task reviews and 1549 passing tests: nothing pinned a case's own
# declared/derived "known" URLs to the gate that consumes them.
#
# This test builds a plausible correct output for every case actually
# registered in ``deep_research.evaluation.cases.all_cases()`` — the real
# registry, not a synthetic fixture — using only that case's own state and
# expectations, and asserts every general gate passes.


def _known_urls_for(case: EvaluationCase) -> list[str]:
    """Every URL this case's own state or expectations makes legitimate.

    Mirrors what a correct agent may cite: the case's declared
    ``known_source_urls``, plus any URL already present on the seeded
    ``ResearchState`` (``raw_findings`` / ``evaluated_sources``) — exactly
    the data ``targets.py`` copies onto ``output.evidence.findings`` /
    ``output.evidence.sources`` for a real repetition. URLs are normalized
    the way a production agent normalizes them before citing (finding 2),
    so a case declaring a ``www.``-prefixed URL still yields a bare,
    citable form.
    """
    raw = [
        *case.expectations.known_source_urls,
        *(finding.source_url for finding in case.state.raw_findings),
        *(source.url for source in case.state.evaluated_sources),
    ]
    seen: set[str] = set()
    ordered: list[str] = []
    for url in raw:
        normalized = normalize_source_url(url)
        if normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def _golden_result(case: EvaluationCase, urls: list[str]) -> dict:
    """A plausible, agent-shaped correct ``result`` citing only ``urls``."""
    if case.agent_name == "planner":
        return {
            "sub_topics": [
                {
                    "title": "Golden sub-topic",
                    "rationale": "A plausible rationale for this case.",
                    "search_queries": ["golden query"],
                    "success_criteria": ["golden criterion"],
                    "priority": 1,
                }
            ]
        }
    if case.agent_name == "researcher":
        return {
            "findings": [
                {
                    "content": f"Evidence drawn from {url}.",
                    "source_url": url,
                    "source_title": "Golden source",
                }
                for url in urls[:2]
            ]
        }
    if case.agent_name == "source_evaluator":
        return {
            "evaluated_sources": [
                {
                    "url": url,
                    "title": "Golden source",
                    "authority_score": 0.8,
                    "recency_score": 0.8,
                    "relevance_score": 0.8,
                    "corroboration_score": 0.8,
                    "overall_score": 0.8,
                    "rationale": "Plausible, well-corroborated source.",
                    "low_confidence": False,
                }
                for url in urls
            ]
        }
    if case.agent_name == "fact_checker":
        claim_urls = urls[:1]
        return {
            "verified_claims": [
                {
                    "text": "A plausible, correctly verified claim.",
                    "source_urls": claim_urls,
                    "verdict": "verified" if claim_urls else "insufficient_evidence",
                    "confidence": 0.8 if claim_urls else 0.3,
                    "evidence": [],
                    "contradictions": [],
                }
            ]
        }
    if case.agent_name == "synthesizer":
        if urls:
            report = (
                "## Summary\n\n"
                f"A plausible, fully cited summary ({urls[0]}).\n\n"
                "## Limitations\n\nThe evidence base is limited."
            )
        else:
            report = (
                "## Summary\n\nA plausible summary with no external "
                "sources to cite.\n\n## Limitations\n\nThe evidence base "
                "is limited."
            )
        return {"report": report}
    if case.agent_name == "critic":
        return {
            "critique": {
                "score": 8,
                "gaps": [],
                "unsupported_claims": [],
                "recommended_queries": [],
                "should_continue": False,
                "rationale": "A plausible, well-supported critique.",
            }
        }
    raise AssertionError(f"no golden result builder for {case.agent_name!r}")


def _build_golden_output(case: EvaluationCase) -> TargetOutput:
    """A plausible correct ``TargetOutput`` for ``case``, from its own data.

    A live case that legitimately has no known URLs anywhere (state or
    expectations) still needs a citable URL to be plausible: this mirrors
    ``_gate_citations_known``'s own live-trajectory fallback by recording a
    "discovered" URL in the trajectory and citing that.
    """
    urls = _known_urls_for(case)
    trajectory: list[TrajectoryStep] = []
    if not urls and case.tier == "live":
        discovered = f"https://discovered.example.com/{case.case_id}"
        urls = [discovered]
        trajectory = [
            TrajectoryStep(
                iteration=0,
                thought=f"Open {discovered}.",
                tool_name="web_search",
                succeeded=True,
                observation_summary=f"Retrieved {discovered}.",
            )
        ]

    return TargetOutput(
        case_id=case.case_id,
        case_version=case.version,
        agent_name=case.agent_name,
        tier=case.tier,
        repetition=1,
        session_id=f"evaluation-golden-{case.case_id}",
        experiment_name=f"{case.agent_name}-{case.tier}-golden-test",
        trace_url="https://smith.langchain.com/o/x/r/golden-1",
        completed=True,
        failure=None,
        result=_golden_result(case, urls),
        state_update={},
        errors=[],
        tracker_errors=[],
        react=ReActSummary(
            iterations=1,
            tool_calls=1 if trajectory else 0,
            stop_reason="completed",
            max_iterations=case.expectations.max_iterations,
            tool_budget=case.expectations.max_tool_calls,
        ),
        dependencies=DependencyLedger(),
        evidence=EvidenceContext(
            sources=[
                source.model_dump(mode="json")
                for source in case.state.evaluated_sources
            ],
            findings=[
                finding.model_dump(mode="json")
                for finding in case.state.raw_findings
            ],
            claims=[
                claim.model_dump(mode="json")
                for claim in case.state.verified_claims
            ],
            scripted_search_urls=[],
        ),
        trajectory=trajectory,
        target_model_requested="gpt-5.6-luna",
        target_model_returned="gpt-5.6-luna",
        target_reasoning_effort="medium",
    )


@pytest.mark.parametrize(
    "case",
    all_cases(),
    ids=lambda case: f"{case.agent_name}/{case.tier}/{case.case_id}",
)
def test_a_plausible_correct_output_passes_every_general_gate_for_every_registered_case(
    case: EvaluationCase,
) -> None:
    """Every case in the real registry, not a synthetic fixture.

    This is the regression test for both Critical findings in
    ``_gate_citations_known``: the Source Evaluator declares no
    ``known_source_urls`` and no ``scripted_search_urls`` at all — its
    sources arrive only through ``state.raw_findings`` /
    ``state.evaluated_sources`` — and several cases declare their known
    URLs in ``www.``-prefixed form while a correct agent cites the
    normalized, bare form. A production-shaped, correct output must still
    pass ``citations_known`` (and every other general gate) for both
    reasons.
    """
    output = _build_golden_output(case)

    results = evaluate_general_gates(output, case, secrets=())

    assert [item.gate_id for item in results] == list(GENERAL_GATE_IDS)
    failed = [item for item in results if not item.passed]
    assert not failed, [(item.gate_id, item.detail) for item in failed]
