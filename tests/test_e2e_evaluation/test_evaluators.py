"""Deterministic whole-report gate and judge-contract tests."""

from __future__ import annotations

from deep_research.e2e_evaluation.cases import (
    case_by_id,
    dependencies_for,
    terminal_state,
)
from deep_research.e2e_evaluation.evaluators import (
    build_judge_input,
    deterministic_evaluation,
    judge_whole_report,
)


def _accepted_fixture(case_id: str = "broad-constraints"):
    case = case_by_id(case_id)
    dependencies = dependencies_for(case)
    for topic in case.sub_topics:
        dependencies.web_search(topic.search_queries[0])
    for source in case.final_pass().sources:
        dependencies.web_scraper(source.url)
    state = terminal_state(case)
    metrics = deterministic_evaluation(
        case,
        state,
        passes=case.passes,
        dependencies=dependencies,
    )
    return case, state, dependencies, metrics


def test_deterministic_evaluator_covers_all_integrity_surfaces() -> None:
    case, state, dependencies, metrics = _accepted_fixture()

    assert metrics.integrity_passed
    assert metrics.coverage_ratio == 1.0
    assert metrics.read_sources == len(state.evaluated_sources)
    assert metrics.source_read_provenance_ratio == 1.0
    assert metrics.checked_claim_provenance_ratio == 1.0
    assert metrics.citation_linkage_ratio == 1.0
    assert metrics.duplicate_claims == 0
    assert metrics.duplicate_source_rows == 0
    assert metrics.uncited_settled_points == 0
    assert metrics.repeated_source_snapshot_passes >= 1
    assert metrics.repeated_claim_snapshot_passes >= 1
    assert dependencies.network_zero


def test_judge_contract_contains_only_bounded_report_inputs() -> None:
    case, state, _dependencies, metrics = _accepted_fixture()

    judge_input = build_judge_input(case, state, metrics)
    assert set(judge_input.model_dump()) == {
        "question",
        "scoped_plan",
        "reader_report",
        "deterministic_metrics",
        "evidence_ledger_summary",
    }
    assert len(judge_input.reader_report) <= 16_000
    assert len(judge_input.evidence_ledger_summary.source_titles) <= 16
    assert "provider_output" not in repr(judge_input).casefold()
    assert "tool_payload" not in repr(judge_input).casefold()
    assert judge_whole_report(judge_input).score >= 0.80


def test_integrity_failure_hard_fails_even_with_perfect_judge_score() -> None:
    case, state, dependencies, _metrics = _accepted_fixture()
    bad_state = state.model_copy(
        update={
            "composition": state.composition.model_copy(update={"summary": []}),
        }
    )
    metrics = deterministic_evaluation(
        case,
        bad_state,
        passes=case.passes,
        dependencies=dependencies,
    )
    assert not metrics.integrity_passed
    judge_input = build_judge_input(case, bad_state, metrics)
    perfect = judge_whole_report(judge_input).model_copy(update={"score": 1.0})
    assert metrics.integrity_failures or metrics.hard_failures
    assert not (metrics.integrity_passed and perfect.score >= 0.80)


def test_comparative_case_requires_contradiction_disclosure() -> None:
    _case, state, dependencies, metrics = _accepted_fixture("comparative-conflict")

    assert metrics.contradicted_claims == 1
    assert metrics.disclosed_contradictions == 1
    assert metrics.integrity_passed


def test_gate_forced_refinement_without_targets_is_measured() -> None:
    _case, _state, _dependencies, metrics = _accepted_fixture()

    assert metrics.gate_forced_refinement_passes == 1
    assert metrics.critic_targets == 0
    assert metrics.new_evidence_in_refinement == 0


def test_unclosed_critic_target_is_a_deterministic_integrity_failure() -> None:
    case = case_by_id("refinement-evidence-recovery")
    bad_case = case.model_copy(
        update={
            "passes": [
                case.passes[0].model_copy(update={"critic_targets": ["topic-99"]}),
                case.passes[1],
            ]
        }
    )
    dependencies = dependencies_for(bad_case)
    for topic in bad_case.sub_topics:
        dependencies.web_search(topic.search_queries[0])
    for source in bad_case.final_pass().sources:
        dependencies.web_scraper(source.url)

    metrics = deterministic_evaluation(
        bad_case,
        terminal_state(bad_case),
        passes=bad_case.passes,
        dependencies=dependencies,
    )

    assert "critic_targets_unresolved" in metrics.integrity_failures
