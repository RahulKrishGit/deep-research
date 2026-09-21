"""Deterministic whole-report gate and judge-contract tests."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

from deep_research.agents.report import render_reader_report
from deep_research.e2e_evaluation.cases import (
    _terminal_state,
    case_by_id,
    dependencies_for,
)
from deep_research.e2e_evaluation.evaluators import (
    build_judge_input,
    deterministic_evaluation,
    judge_whole_report,
)
from deep_research.e2e_evaluation.models import WholeReportJudgeInput
from deep_research.e2e_evaluation.runner import run_case
from deep_research.utils.types import ResearchEvent


def _accepted_fixture_parts(case_id: str = "broad-constraints"):
    """One accepted repetition, plus the production formatter's own output."""
    case = case_by_id(case_id)
    result = run_case(
        case_id,
        tier="controlled",
        repetitions=3,
        output_directory=Path(tempfile.mkdtemp()),
    )
    state = result.repetitions[0].state
    dependencies = dependencies_for(case)
    dependencies.publication_operations = list(
        result.repetitions[0].publication_operations
    )
    for source in state.evaluated_sources:
        dependencies.web_scraper(source.url)
    for claim in state.verified_claims:
        for passage in claim.verification_evidence:
            dependencies.web_scraper(passage.source_url)
    cli_output = list(result.repetitions[0].cli_output)
    metrics = deterministic_evaluation(
        case,
        state,
        dependencies=dependencies,
        cli_output=cli_output,
    )
    return case, state, dependencies, metrics, cli_output


def _accepted_fixture(case_id: str = "broad-constraints"):
    return _accepted_fixture_parts(case_id)[:4]


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
    """The campaign's null result is recorded, not silently accepted.

    The broad case empties its reader summary at iteration 0, so the quality
    gate forces a refinement the Critic names no target for. The pass spends
    budget and adds nothing, and no integrity gate fails on it: the gate is the
    graph's own verdict, and this case exists to exercise it. The count makes
    the null result a visible artifact observation.
    """
    _case, _state, _dependencies, metrics = _accepted_fixture()

    assert metrics.gate_forced_refinement_passes == 1
    assert metrics.critic_targets == 0
    assert metrics.new_evidence_in_refinement == 0
    assert metrics.targetless_gate_forced_refinements == 1


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
        _terminal_state(bad_case),
        passes=bad_case.passes,
        dependencies=dependencies,
    )

    assert "critic_targets_unresolved" in metrics.integrity_failures


def test_the_evaluation_records_whether_the_production_graph_was_observed() -> None:
    """A fixture-based evaluation must be labelled as one.

    Every *observed* metric leg is gated on a production
    ``graph.node.completed`` event for the planner; when that event is absent
    the evaluator substitutes fixture values that are true by construction. A
    verdict that cannot say which branch ran is a verdict an operator running
    the suite CLI cannot audit, so the branch is recorded on the evaluation.
    """
    case, state, dependencies, metrics = _accepted_fixture()

    blind = state.model_copy(
        update={
            "events": [
                event
                for event in state.events
                if event.event_type != "graph.node.completed"
            ]
        }
    )
    fabricated = deterministic_evaluation(
        case, blind, passes=case.passes, dependencies=dependencies
    )

    assert "production_graph_unobserved" in fabricated.integrity_failures
    assert not fabricated.integrity_passed
    assert fabricated.graph_observed is False
    assert metrics.graph_observed is True


def test_a_fixture_based_state_is_never_terminal_state() -> None:
    """``state_for_pass``/``terminal_state`` build a state, not a graph run.

    They exist so a gate can be driven from a hand-made pass, and nothing in
    this package's accepted path may be derived from one. Demoting them to
    private helpers keeps the public surface honest about that.
    """
    import deep_research.e2e_evaluation.cases as cases_module

    assert not hasattr(cases_module, "terminal_state")
    assert not hasattr(cases_module, "state_for_pass")
    assert hasattr(cases_module, "_terminal_state")
    assert hasattr(cases_module, "_state_for_pass")


def test_the_judge_does_not_score_headings_every_report_always_carries() -> None:
    """A term that is constant on every real input is not evidence.

    ``render_reader_report`` prints all six ``REPORT_SECTIONS`` headings
    unconditionally, so a "heading present" term scores 1.0 on every rendered
    report and can never discriminate reader quality — it only inflates the
    mean. Completeness and readability therefore read the coverage the
    evaluator computed and the report's length, and neither moves when the
    headings that are always there are taken away.
    """
    case, state, _dependencies, metrics = _accepted_fixture()
    base = build_judge_input(case, state, metrics)
    stripped = base.model_copy(
        update={
            "reader_report": re.sub(
                r"(?m)^## .*\n", "", base.reader_report
            )
        }
    )
    assert "## " not in stripped.reader_report

    base_dims = judge_whole_report(base).dimensions
    stripped_dims = judge_whole_report(stripped).dimensions

    assert stripped_dims["completeness"] == base_dims["completeness"]
    assert stripped_dims["readability"] == base_dims["readability"]


def test_uncertainty_is_satisfied_when_nothing_is_contradicted() -> None:
    """Scoring ``contradicted / claims`` rewarded having a contradiction.

    A report with nothing contradictory to disclose has satisfied the
    dimension, and a report that contradicts a claim and discloses it has too.
    The deficit reading scored the first at 0.0 and the second at 1/3, which
    is exactly backwards as a reader-quality signal.
    """
    case, state, _dependencies, metrics = _accepted_fixture()
    base = build_judge_input(case, state, metrics)
    assert metrics.contradicted_claims == 0

    assert judge_whole_report(base).dimensions["uncertainty"] == 1.0

    undisclosed = base.model_copy(
        update={
            "deterministic_metrics": {
                **base.deterministic_metrics,
                "disclosed_contradictions": 0,
            },
            "evidence_ledger_summary": (
                base.evidence_ledger_summary.model_copy(
                    update={"contradicted_claim_count": 1}
                )
            ),
        }
    )
    assert (
        judge_whole_report(undisclosed).dimensions["uncertainty"] == 0.0
    )


def test_prioritization_measures_the_ranked_share_of_the_scoped_plan() -> None:
    """The dimension must read what the report ranks, not the plan's size.

    ``case.sub_topics`` cardinality is a property of the fixture. What the
    reader sees is how many of those planned topics the report presents as
    ranked points, which is the evaluator's own typed ``composition.summary``
    count.
    """
    case, state, _dependencies, metrics = _accepted_fixture(
        "comparative-conflict"
    )
    judge_input = build_judge_input(case, state, metrics)

    assert judge_whole_report(judge_input).dimensions["prioritization"] == (
        pytest.approx(2 / 3)
    )

    unranked = judge_input.model_copy(
        update={
            "deterministic_metrics": {
                **judge_input.deterministic_metrics,
                "ranked_reader_points": 0,
            }
        }
    )
    assert judge_whole_report(unranked).dimensions["prioritization"] == 0.0

    ranked = judge_input.deterministic_metrics["ranked_reader_points"]
    planned = len(judge_input.scoped_plan)
    assert (ranked, planned) == (2, 3)


def test_the_judge_can_score_an_integrity_clean_report_below_its_floor() -> None:
    """The missing positive control for the judge's own floor.

    Every integrity gate can pass on a report that ranks one of its five
    planned topics and states no decision, and the plan's judge floor is
    supposed to be independent reader-quality evidence. Without a fixture that
    lands between the per-repetition floor and the mean floor, "the judge
    passed" cannot be told apart from "the judge always passes".
    """
    case, state, _dependencies, metrics = _accepted_fixture(
        "comparative-conflict"
    )
    base = build_judge_input(case, state, metrics)
    thin = base.model_copy(
        update={
            "deterministic_metrics": {
                **base.deterministic_metrics,
                "ranked_reader_points": 1,
            }
        }
    )
    score = judge_whole_report(thin).score

    assert not any(
        word in thin.reader_report.casefold()
        for word in ("decision", "recommend", "should", "implication")
    )
    assert 0.70 <= score < 0.80


def test_judge_rubric_distinguishes_integrity_clean_reader_quality() -> None:
    """Three points on the scale, all of them integrity-clean.

    The rubric is only a gate if it separates reader quality while every hard
    integrity gate passes. The fixtures therefore move the judge's own
    reader-facing inputs — how much of the scoped plan the report ranks, how
    long it is, and whether it states a decision — and never an integrity
    gate, which would make the comparison vacuous.
    """
    case, state, _dependencies, metrics = _accepted_fixture()
    base = build_judge_input(case, state, metrics)
    high = base
    borderline = base.model_copy(
        update={
            "reader_report": (
                "# Research report\n\n## Executive summary\n\n"
                "A short answer with limited prioritization."
            ),
            "deterministic_metrics": {
                **base.deterministic_metrics,
                "coverage_ratio": 0.8,
                "ranked_reader_points": 1,
            },
        }
    )
    low = base.model_copy(
        update={
            "reader_report": "One unsupported sentence.",
            "deterministic_metrics": {
                **base.deterministic_metrics,
                "coverage_ratio": 0.8,
                "ranked_reader_points": 0,
            },
        }
    )

    high_score = judge_whole_report(high)
    borderline_score = judge_whole_report(borderline)
    low_score = judge_whole_report(low)

    assert high_score.score > borderline_score.score > low_score.score
    assert high_score.score >= 0.80
    assert low_score.score < 0.70


def test_cli_agreement_fails_closed_without_formatter_output() -> None:
    """A required gate must not pass because its evidence was never supplied.

    Every other leg of the evaluator fails closed. ``cli_matches`` defaulted
    open, so a caller that supplied neither ``cli_output`` nor
    ``cli_summary`` — including the ten ``_accepted_fixture()`` call sites
    before this test — exercised a required gate as a pass.
    """
    case, state, dependencies, _metrics = _accepted_fixture()
    assert dependencies is not None

    metrics = deterministic_evaluation(case, state, dependencies=dependencies)

    assert metrics.cli_summary_matches is False
    assert "cli_summary_mismatch" in metrics.integrity_failures
    assert not metrics.integrity_passed


def test_the_judge_ledger_summary_carries_the_real_duplicate_counts() -> None:
    """The judge's only ledger surface must not fabricate a clean duplicate count.

    ``EvidenceLedgerSummary`` hardcoded ``duplicate_source_rows`` and
    ``duplicate_claims`` to zero, so a repetition whose ``integrity_failures``
    named ``duplicate_claims`` still handed the judge a clean duplicate count —
    the one bounded ledger surface a whole-report judge is allowed to see.
    """
    case, state, _dependencies, metrics = _accepted_fixture()
    duplicated = metrics.model_copy(
        update={"duplicate_claims": 2, "duplicate_source_rows": 1}
    )

    judge_input = build_judge_input(case, state, duplicated)

    assert judge_input.evidence_ledger_summary.duplicate_claims == 2
    assert judge_input.evidence_ledger_summary.duplicate_source_rows == 1


def test_the_cli_agreement_gate_compares_every_printed_evidence_count() -> None:
    """``verified`` and ``contradicted`` are two of the six printed numbers.

    ``_expected_cli_summary`` never compared the ``verified_claims`` /
    ``contradicted_claims`` the parser extracts, so a formatter that printed
    either count wrongly still agreed with state.
    """
    case, state, dependencies, _metrics, cli_output = _accepted_fixture_parts()
    assert any("verified" in line for line in cli_output)

    agreed = deterministic_evaluation(
        case, state, dependencies=dependencies, cli_output=cli_output
    )
    assert agreed.cli_summary_matches is True

    tampered = [
        re.sub(r"\d+ verified,", "9 verified,", line) for line in cli_output
    ]
    assert tampered != cli_output

    metrics = deterministic_evaluation(
        case, state, dependencies=dependencies, cli_output=tampered
    )

    assert metrics.cli_summary_matches is False
    assert "cli_summary_mismatch" in metrics.integrity_failures


def test_rendered_citation_marker_mismatch_is_a_hard_integrity_failure() -> None:
    case, state, dependencies, _metrics = _accepted_fixture()
    report = state.report or ""
    assert "[1]" in report
    bad_state = state.model_copy(update={"report": report.replace("[1]", "", 1)})

    metrics = deterministic_evaluation(
        case,
        bad_state,
        passes=case.passes,
        dependencies=dependencies,
    )

    assert "rendered_citation_resolution" in metrics.integrity_failures


def test_rendered_citation_mapping_rejects_swapped_point_markers() -> None:
    case, state, dependencies, _metrics = _accepted_fixture()
    report = state.report or ""
    assert "[1]" in report and "[2]" in report
    swapped = (
        report.replace("[1]", "[__one__]")
        .replace("[2]", "[1]")
        .replace("[__one__]", "[2]")
    )
    assert swapped != render_reader_report(state.composition)
    metrics = deterministic_evaluation(
        case,
        state.model_copy(update={"report": swapped}),
        passes=case.passes,
        dependencies=dependencies,
    )

    assert "rendered_citation_resolution" in metrics.integrity_failures


def test_publication_operations_are_reader_then_ledger_then_memory() -> None:
    case, state, dependencies, metrics = _accepted_fixture()

    assert dependencies.publication_operations[:2] == [
        "reader_document",
        "evidence_document",
    ]
    assert all(
        operation == "memory_claim"
        for operation in dependencies.publication_operations[2:]
    )
    assert len(dependencies.publication_operations) == 2 + metrics.memory_writes


def test_reordered_publication_operations_are_a_hard_integrity_failure() -> None:
    case, state, dependencies, _metrics = _accepted_fixture()
    dependencies.publication_operations[:2] = [
        "evidence_document",
        "reader_document",
    ]

    metrics = deterministic_evaluation(
        case,
        state,
        passes=case.passes,
        dependencies=dependencies,
    )

    assert "publication_operation_order" in metrics.integrity_failures


def test_publication_must_follow_the_last_quality_assessment() -> None:
    case, state, dependencies, _metrics = _accepted_fixture()
    quality_event = next(
        event
        for event in reversed(state.events)
        if event.event_type == "graph.quality.assessed"
    )
    extra_quality = ResearchEvent(
        event_type=quality_event.event_type,
        source=quality_event.source,
        message=quality_event.message,
        timestamp=quality_event.timestamp,
        metadata=dict(quality_event.metadata),
    )
    bad_state = state.model_copy(update={"events": [*state.events, extra_quality]})

    metrics = deterministic_evaluation(
        case,
        bad_state,
        passes=case.passes,
        dependencies=dependencies,
    )

    assert "publication_memory_timing" in metrics.integrity_failures


def test_attempt_accounting_uses_observed_attempt_events() -> None:
    case, state, dependencies, _metrics = _accepted_fixture()
    without_attempt_events = state.model_copy(
        update={
            "events": [
                event
                for event in state.events
                if not event.event_type.endswith(".topic.attempted")
                and not event.event_type.endswith(".topic.skipped")
            ]
        }
    )

    metrics = deterministic_evaluation(
        case,
        without_attempt_events,
        passes=case.passes,
        dependencies=dependencies,
    )

    assert metrics.attempted_topics == 0
    assert "planned_topic_attempts" in metrics.integrity_failures


def test_judge_input_rejects_raw_provider_and_tool_fields() -> None:
    case, state, _dependencies, metrics = _accepted_fixture()
    payload = build_judge_input(case, state, metrics)
    with pytest.raises(ValueError, match="prohibited field"):
        WholeReportJudgeInput.model_validate(
            payload.model_dump() | {
                "deterministic_metrics": {
                    **payload.deterministic_metrics,
                    "raw_provider_output": True,
                }
            }
        )


# --- Task 10: structural diagnostics versus semantic judgements -------------


def test_the_whole_report_judge_is_labelled_structural_only() -> None:
    """The legacy formula stays available and stops claiming to be a judge.

    Its inputs are ratios, a length band, and decision language — several of
    which restate hard integrity gates — so a consumer must be able to see that
    from the record alone rather than from a comment in this repository.
    """
    case, state, _dependencies, metrics = _accepted_fixture()
    verdict = judge_whole_report(build_judge_input(case, state, metrics))

    assert verdict.structural_only is True
    assert "structural-only" in verdict.rationale.casefold()
    assert "not an independent report judge" in verdict.rationale


def _review(**overrides: object):
    from deep_research.utils.types import REVIEW_DIMENSIONS, ReportReview

    payload: dict[str, object] = {
        "status": "scored",
        "dimensions": {name: 1.0 for name in REVIEW_DIMENSIONS},
        "defects": [],
        "per_statement_dispositions": {"S001": "supported"},
        "reviewed_statement_ids": ["S001"],
        "reviewed_evidence_ids": ["e1"],
        "reviewed_batch_ids": ["batch-01"],
        "expected_batch_ids": ["batch-01"],
        "input_fingerprint": "packet-1",
        "rubric_version": 2,
        "rationale": "Recorded for the evaluator tests.",
    }
    payload.update(overrides)
    return ReportReview.model_validate(payload)


def test_the_semantic_review_evaluator_reads_every_dimension_and_defect() -> None:
    from deep_research.e2e_evaluation.evaluators import semantic_review_summary
    from deep_research.utils.types import REVIEW_DIMENSIONS, CritiqueGap

    summary = semantic_review_summary(_review())

    assert summary.status == "scored"
    assert summary.score == 1.0
    assert summary.accepted is True
    assert set(summary.dimensions) == REVIEW_DIMENSIONS
    assert summary.coverage_complete is True
    assert summary.defect_count == 0
    assert summary.input_fingerprint == "packet-1"

    # A critical defect with every dimension at 1.0 is not an acceptance, and
    # the summary has to say so from the defect list rather than from the mean.
    rejected = semantic_review_summary(
        _review(
            defects=[
                CritiqueGap(
                    gap_id="review-01",
                    target_ids=["t1"],
                    statement_ids=["S001"],
                    kind="missing_support",
                    severity="critical",
                    repair_action="adjudicate",
                    problem="The main number is not in the source.",
                )
            ],
            per_statement_dispositions={"S001": "unsupported"},
        )
    )
    assert rejected.score == 1.0
    assert rejected.accepted is False
    assert rejected.material_defect_count == 1
    assert "S001" in rejected.reviewed_statement_ids


def test_a_missing_semantic_review_is_recorded_as_missing_not_as_a_pass() -> None:
    from deep_research.e2e_evaluation.evaluators import semantic_review_summary

    summary = semantic_review_summary(None)

    assert summary.missing is True
    assert summary.scored is False
    assert summary.accepted is False
    assert summary.score is None
    assert summary.dimensions == {}
    assert summary.status == ""


def test_an_incomplete_semantic_review_is_never_accepted() -> None:
    from deep_research.e2e_evaluation.evaluators import semantic_review_summary

    summary = semantic_review_summary(
        _review().model_copy(update={"status": "incomplete", "dimensions": {}})
    )

    assert summary.missing is True
    assert summary.accepted is False
    assert summary.score is None
