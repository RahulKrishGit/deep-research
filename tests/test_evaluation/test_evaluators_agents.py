"""Agent-specific hard gates and every case metric implementation."""

from __future__ import annotations

from deep_research.evaluation.cases import all_cases
from deep_research.evaluation.evaluators import (
    AGENT_GATE_IDS,
    METRIC_FUNCTIONS,
    code_evaluator,
    evaluate_agent_gates,
    evaluate_target,
)
from deep_research.evaluation.models import AGENT_NAMES


def gate(results, gate_id):
    return next(item for item in results if item.gate_id == gate_id)


def test_every_case_metric_has_an_implementation() -> None:
    """A case naming a metric nobody implemented is a plan-time bug, and
    ``deterministic_quality`` raises rather than silently scoring zero."""
    missing = sorted(
        {
            metric.metric_id
            for case in all_cases()
            for metric in case.expectations.deterministic_metrics
        }
        - set(METRIC_FUNCTIONS)
    )

    assert missing == []


def test_every_agent_declares_its_specific_gates() -> None:
    for agent_name in AGENT_NAMES:
        assert AGENT_GATE_IDS[agent_name], agent_name


# --- Planner ---------------------------------------------------------------


def test_the_planner_gate_rejects_fewer_than_three_subtopics(
    planner_case, planner_output
) -> None:
    output = planner_output.with_sub_topics(2)

    assert gate(
        evaluate_agent_gates(output, planner_case), "subtopic_count"
    ).passed is False


def test_the_planner_gate_rejects_more_than_seven_subtopics(
    planner_case, planner_output
) -> None:
    output = planner_output.with_sub_topics(8)

    assert gate(
        evaluate_agent_gates(output, planner_case), "subtopic_count"
    ).passed is False


def test_the_planner_gate_rejects_duplicate_titles(
    planner_case, planner_output
) -> None:
    output = planner_output.with_titles(["Alpha", "alpha ", "Beta"])

    assert gate(
        evaluate_agent_gates(output, planner_case), "distinct_subtopics"
    ).passed is False


def test_the_planner_gate_requires_the_question_to_survive(
    planner_case, planner_output
) -> None:
    output = planner_output.model_copy(
        update={"state_update": {"original_question": "a different question"}}
    )

    assert gate(
        evaluate_agent_gates(output, planner_case), "question_preserved"
    ).passed is False


# --- Researcher ------------------------------------------------------------


def test_the_researcher_gate_requires_coverage_or_a_recorded_reason(
    researcher_case, researcher_output
) -> None:
    output = researcher_output.with_findings_for(
        ["Coefficient of performance below freezing"]
    )

    result = gate(
        evaluate_agent_gates(output, researcher_case), "sub_topic_covered"
    )

    assert result.passed is False
    assert "Cold-climate field trial outcomes" in result.detail


def test_an_uncovered_subtopic_passes_when_a_skip_error_was_recorded(
    researcher_case, researcher_output
) -> None:
    output = researcher_output.with_findings_for(
        ["Coefficient of performance below freezing"]
    ).model_copy(
        update={
            "errors": [
                {
                    "error_type": "sub_topic_skipped",
                    "source": "researcher",
                    "message": "no evidence was available",
                    "timestamp": "2026-08-01T00:00:00+00:00",
                    "details": {
                        "sub_topic": "Cold-climate field trial outcomes"
                    },
                }
            ]
        }
    )

    assert gate(
        evaluate_agent_gates(output, researcher_case), "sub_topic_covered"
    ).passed is True


def test_the_researcher_gate_rejects_an_invented_source_url(
    researcher_case, researcher_output
) -> None:
    output = researcher_output.with_finding_url("https://invented.example/x")

    assert gate(
        evaluate_agent_gates(output, researcher_case), "no_invented_sources"
    ).passed is False


# --- Source Evaluator ------------------------------------------------------


def test_the_source_evaluator_gate_requires_one_evaluation_per_source(
    source_evaluator_case, source_evaluator_output
) -> None:
    output = source_evaluator_output.drop_one_source()

    assert gate(
        evaluate_agent_gates(output, source_evaluator_case),
        "one_evaluation_per_source",
    ).passed is False


def test_the_source_evaluator_gate_rejects_a_non_finite_score(
    source_evaluator_case, source_evaluator_output
) -> None:
    output = source_evaluator_output.with_score("overall_score", 1.5)

    assert gate(
        evaluate_agent_gates(output, source_evaluator_case),
        "bounded_scores",
    ).passed is False


def test_the_source_evaluator_gate_requires_the_expected_low_confidence_flag(
    source_evaluator_case, source_evaluator_output
) -> None:
    output = source_evaluator_output.clear_low_confidence()

    assert gate(
        evaluate_agent_gates(output, source_evaluator_case),
        "low_confidence_flagged",
    ).passed is False


# --- Fact Checker ----------------------------------------------------------


def test_the_fact_checker_gate_enforces_independent_domains(
    fact_checker_dependent_case, fact_checker_dependent_output
) -> None:
    """The dependent case declares minimum_independent_domains: a claim
    whose corroboration is only same-family stays rejected even when the
    evidence strings paraphrase without pasting URLs — the scripted
    same-family results the trajectory recorded still resolve to the
    claim's own family."""
    assert gate(
        evaluate_agent_gates(
            fact_checker_dependent_output, fact_checker_dependent_case
        ),
        "independent_domains",
    ).passed is False


def test_two_genuinely_independent_domains_pass_the_gate(
    fact_checker_dependent_case, fact_checker_dependent_output
) -> None:
    """Trajectory-recorded URLs count toward independence when the evidence
    strings quote rather than paste URLs."""
    output = fact_checker_dependent_output.with_trajectory_urls(
        [
            "https://cern.example.int/outage-audit",
            "https://eia.example.gov/outage-bulletin",
        ]
    )

    assert gate(
        evaluate_agent_gates(output, fact_checker_dependent_case),
        "independent_domains",
    ).passed is True


def test_the_mixed_verdicts_case_passes_without_a_declared_minimum(
    fact_checker_case, fact_checker_output
) -> None:
    """The ordinary case never declares minimum_independent_domains, so the
    gate cannot fail a well-supported verified claim whose evidence
    paraphrases instead of pasting URLs."""
    output = fact_checker_output.with_evidence_texts(
        [
            "The IAEA framework covers SMR designs and the NRC applies the "
            "same review."
        ]
    )

    assert gate(
        evaluate_agent_gates(output, fact_checker_case),
        "independent_domains",
    ).passed is True


def test_the_fact_checker_gate_requires_evidence_on_a_verified_claim(
    fact_checker_case, fact_checker_output
) -> None:
    output = fact_checker_output.with_empty_evidence()

    assert gate(
        evaluate_agent_gates(output, fact_checker_case), "evidence_linked"
    ).passed is False


def test_insufficient_evidence_must_stay_low_confidence(
    fact_checker_case, fact_checker_output
) -> None:
    output = fact_checker_output.with_claim_verdict(
        "insufficient_evidence", confidence=0.95
    )

    assert gate(
        evaluate_agent_gates(output, fact_checker_case),
        "conservative_insufficiency",
    ).passed is False


# --- Synthesizer -----------------------------------------------------------


def test_the_synthesizer_gate_rejects_an_unknown_citation(
    synthesizer_case, synthesizer_output
) -> None:
    output = synthesizer_output.with_report_citing(
        "https://invented.example.com/page"
    )

    assert gate(
        evaluate_agent_gates(output, synthesizer_case), "citations_known_only"
    ).passed is False


def test_the_synthesizer_gate_requires_limitations_to_be_represented(
    synthesizer_case, synthesizer_output
) -> None:
    output = synthesizer_output.without_limitations()

    assert gate(
        evaluate_agent_gates(output, synthesizer_case),
        "limitations_represented",
    ).passed is False


def test_the_synthesizer_gate_rejects_a_false_persistence_claim(
    synthesizer_failure_case, synthesizer_failure_output
) -> None:
    """The write failed; a report claiming it was saved is a lie."""
    output = synthesizer_failure_output.with_report_text(
        "The full report was written to the output directory."
    )

    assert gate(
        evaluate_agent_gates(output, synthesizer_failure_case),
        "persistence_truthful",
    ).passed is False


# --- Critic ----------------------------------------------------------------


def test_the_critic_gate_requires_a_bounded_score(
    critic_case, critic_output
) -> None:
    output = critic_output.with_score(0)

    assert gate(
        evaluate_agent_gates(output, critic_case), "bounded_component_scores"
    ).passed is False


def test_the_critic_gate_uses_the_production_routing_rule(
    critic_case, critic_output
) -> None:
    """The gate calls ``agents.critic.route_decision`` rather than
    re-deriving the threshold, so the two can never disagree."""
    output = critic_output.with_score(9).with_should_continue(True)

    assert gate(
        evaluate_agent_gates(output, critic_case), "route_consistent"
    ).passed is False


def test_the_critic_gate_forbids_continuing_with_no_budget_left(
    critic_budget_case, critic_budget_output
) -> None:
    output = critic_budget_output.with_should_continue(True)

    assert gate(
        evaluate_agent_gates(output, critic_budget_case), "route_consistent"
    ).passed is False


def test_the_critic_gate_requires_actionable_critique(
    critic_gap_case, critic_gap_output
) -> None:
    output = critic_gap_output.with_gaps([]).with_recommended_queries([])

    assert gate(
        evaluate_agent_gates(output, critic_gap_case), "critique_actionable"
    ).passed is False


# --- The LangSmith adapter -------------------------------------------------


def test_the_code_evaluator_emits_gate_and_quality_feedback(
    planner_case, clean_target_output
) -> None:
    from tests.evaluation_fakes import FakeExampleRow, FakeRun

    evaluator = code_evaluator(planner_case, secrets=())
    results = evaluator(
        FakeRun(outputs=clean_target_output.model_dump(mode="json")),
        FakeExampleRow({"inputs": {"case_id": planner_case.case_id}}),
    )

    keys = {item["key"] for item in results["results"]}
    assert "hard_gates_passed" in keys
    assert "deterministic_quality" in keys
    assert any(key.startswith("gate:") for key in keys)


def test_the_code_evaluator_never_raises_on_a_malformed_run(
    planner_case,
) -> None:
    """A broken run must fail its gates, not abort the whole experiment."""
    from tests.evaluation_fakes import FakeExampleRow, FakeRun

    evaluator = code_evaluator(planner_case, secrets=())
    results = evaluator(FakeRun(outputs={"nonsense": True}), FakeExampleRow({}))

    scores = {item["key"]: item["score"] for item in results["results"]}
    assert scores["hard_gates_passed"] == 0
    assert scores["deterministic_quality"] == 0.0


def test_the_code_evaluator_never_raises_on_a_malformed_cited_url(
    planner_case, clean_target_output
) -> None:
    """Finding 14, traced through the actual runner dispatch path.

    ``code_evaluator``'s inner ``evaluate`` closure is exactly what
    ``runner.py``'s ``_dispatch_code`` wraps in a LangSmith evaluator call,
    and ``runner.py`` only catches ``ValidationError`` around that
    dispatch — never ``ValueError``. Pre-fix, a malformed cited URL (e.g.
    an out-of-range port) made ``_gate_citations_known`` raise
    ``ValueError`` from ``normalize_source_url``'s ``urlsplit(...).port``,
    which would escape here uncaught, and in the real runner that means
    ``pending_gates[key]`` is never set and the whole repetition silently
    vanishes from ``repetitions_by_case`` instead of failing its gates.
    This must now return a normal, fully populated gate report with
    ``citations_known`` scored 0 -- not raise.

    Uses the planner (no agent-specific gate touches
    ``normalize_source_url``) so this test isolates ``citations_known``'s
    own resilience rather than the separately-scoped, pre-existing
    ``normalize_source_url`` non-totality reachable from other gates
    (e.g. ``no_invented_sources``'s ``source_domain`` call, out of scope
    for this fix per the finding).
    """
    from tests.evaluation_fakes import FakeExampleRow, FakeRun

    result = dict(clean_target_output.result)
    result["sub_topics"] = [
        {
            **result["sub_topics"][0],
            "rationale": "See https://ex.com:99999/page for details.",
        },
        *result["sub_topics"][1:],
    ]
    output = clean_target_output.model_copy(update={"result": result})

    evaluator = code_evaluator(planner_case, secrets=())
    results = evaluator(
        FakeRun(outputs=output.model_dump(mode="json")),
        FakeExampleRow({"inputs": {"case_id": planner_case.case_id}}),
    )

    scores = {item["key"]: item["score"] for item in results["results"]}
    assert scores["gate:citations_known"] == 0
    assert scores["hard_gates_passed"] == 0
    # The repetition was scored, not silently dropped: every other gate
    # still ran and reported a real result.
    assert len(results["results"]) > 2


def test_evaluate_target_combines_general_and_agent_gates(
    planner_case, clean_target_output
) -> None:
    report, quality = evaluate_target(
        clean_target_output, planner_case, secrets=()
    )

    ids = {item.gate_id for item in report.results}
    assert "run_completed" in ids
    assert "subtopic_count" in ids
    assert 0.0 <= quality <= 1.0

