"""Controlled whole-report campaign case contracts."""

from __future__ import annotations

from pathlib import Path

from deep_research.e2e_evaluation.cases import (
    CASE_SCHEMA_VERSION,
    CONTROLLED_CASE_IDS,
    case_by_id,
    controlled_cases,
    dependencies_for,
)
from deep_research.e2e_evaluation.models import ExpectedResult
from deep_research.e2e_evaluation.replay_matrix import (
    GRAPH_ONLY_HISTORICAL_MANIFEST,
)
from deep_research.e2e_evaluation.runner import run_case
from deep_research.utils.types import counted_evidence_targets, target_is_answered

# The two cases that declare counted obligations where the three legacy cases
# declare none: the one whose open obligation the campaign has to report, and
# the control that shows the stricter reading is not "always fails".
OPEN_OBLIGATION_CASE = "claimed-coverage-open-obligation"
ANSWERED_OBLIGATIONS_CASE = "declared-obligations-answered"


def test_the_graph_historical_registry_matches_its_declared_inventory() -> None:
    """Every controlled case is scripted, and is one the inventory declares.

    These three cases are the graph-historical half of the controlled
    inventory: scripted dependencies and a scripted six-agent double, kept
    because the product result they recorded is the regression baseline the
    real-agent rows cannot reproduce. "Exactly three" was the hardcode this
    derives away from — the count is the manifest's, so a case added to one
    and not the other fails here instead of quietly disagreeing.
    """
    cases = controlled_cases()
    expected = tuple(
        entry.case_id.removesuffix("-graph")
        for entry in GRAPH_ONLY_HISTORICAL_MANIFEST
    )

    assert tuple(case.case_id for case in cases) == expected
    assert tuple(case.case_id for case in cases) == CONTROLLED_CASE_IDS
    assert {case.tier for case in cases} == {"controlled"}
    assert all(case.network_zero for case in cases)
    assert all(case.scripted_dependencies for case in cases)


def test_cases_cover_the_three_required_report_shapes() -> None:
    cases = {case.case_id: case for case in controlled_cases()}

    broad = cases["broad-constraints"]
    assert len(broad.sub_topics) == 5
    assert broad.repeated_snapshots
    assert {topic.title for topic in broad.sub_topics} == {
        "grid connection",
        "supply chain and trade",
        "siting and safety",
        "market rules",
        "project economics",
    }

    comparative = cases["comparative-conflict"]
    assert comparative.expected_contradictions == 1
    assert any(
        claim.verdict == "contradicted" for claim in comparative.final_pass().claims
    )

    refinement = cases["refinement-evidence-recovery"]
    assert refinement.expected_refinement_topics == ["topic-04", "topic-05"]
    assert {
        coverage_id
        for coverage_id in refinement.first_pass().claims[0].consumed_coverage_ids
    } == {"topic-01"}
    assert {
        coverage_id
        for claim in refinement.final_pass().claims
        for coverage_id in claim.consumed_coverage_ids
    } == {"topic-01", "topic-02", "topic-03", "topic-04", "topic-05"}


def test_each_case_dependency_bundle_records_only_scripted_calls() -> None:
    for case in controlled_cases():
        dependencies = dependencies_for(case)
        for topic in case.sub_topics:
            dependencies.web_search(topic.search_queries[0])
        assert dependencies.network_zero
        assert dependencies.scripted
        assert dependencies.real_services_used == []
        assert dependencies.prohibited_calls == []
        assert set(dependencies.request_counts) == {"web_search"}


# --- the cases that declare obligations -------------------------------------
#
# The three legacy cases declare none, so the campaign's coverage reading for
# them is the claimed one — the topics some claim recorded consuming. These
# two declare one obligation per topic, which is the shape the product gates
# on, and they are a pair: one leaves a declared obligation open while its
# topic's evidence is still claimed, and one answers every obligation it
# declares.


def test_the_declared_obligation_cases_carry_their_declared_result() -> None:
    """Each row declares its result where the legacy rows declare theirs.

    A declared result and a passing run are two facts. The legacy rows declare
    an accepted result and are accepted; the open-obligation row declares the
    partial result its fixture exists to produce, names the leg the campaign
    must record, and is not accepted. The declaration is carried by the case
    (tier, version, expected result) and restated by the manifest row, so a row
    that gains a case without gaining a declaration — or the other way round —
    disagrees here rather than in a suite verdict.
    """
    cases = {case.case_id: case for case in controlled_cases()}
    open_obligation = cases[OPEN_OBLIGATION_CASE]
    control = cases[ANSWERED_OBLIGATIONS_CASE]

    for case in (open_obligation, control):
        assert case.tier == "controlled"
        assert case.version == CASE_SCHEMA_VERSION
        # Every topic declares exactly one counted obligation, and every
        # obligation is one the plan owes: the planner stamps ``required``.
        assert len(case.sub_topics) >= 4
        for topic in case.sub_topics:
            targets = counted_evidence_targets(topic.evidence_targets)
            assert len(targets) == 1
            assert targets[0].coverage_id == topic.coverage_id
            assert targets[0].required is True

    assert open_obligation.expected_result == ExpectedResult(
        accepted=False,
        required_failures=["coverage_below_0.80"],
        allowed_failures=["unaccounted_required_targets"],
    )
    assert control.expected_result == ExpectedResult(accepted=True)

    declared = {
        entry.case_id: entry.expected_product_result
        for entry in GRAPH_ONLY_HISTORICAL_MANIFEST
    }
    assert declared[f"{OPEN_OBLIGATION_CASE}-graph"] == "partial / coverage_below_0.80"
    assert declared[f"{ANSWERED_OBLIGATIONS_CASE}-graph"] == "accepted / 0"
    # The declared result and the registry's ids are one declaration: the row
    # is the case it names, under the harness's own suffix.
    assert set(declared) == {
        f"{case_id}-graph" for case_id in CONTROLLED_CASE_IDS
    }


def test_a_claimed_topic_whose_obligation_nothing_answered_is_not_covered(
    tmp_path: Path,
) -> None:
    """The graded ratio is the substantive one, and the leg is recorded.

    The open topic's evidence is a checked claim that names its obligation,
    fills the dimension the plan asked for, and consumes the topic — and the
    plan asked for an independent pair while the evidence is one publisher's
    attribution, so the obligation is not answered. The claimed reading counts
    the topic anyway; the reading the campaign grades must not, and the run
    must record which leg costs it the coverage floor. A harness that went
    back to grading the claimed reading reports 1.00, no leg, and an accepted
    row here — all three of which this test refuses.
    """
    result = run_case(
        OPEN_OBLIGATION_CASE,
        tier="controlled",
        repetitions=3,
        output_directory=tmp_path,
    )

    assert result.accepted is False
    assert result.met_expectation is True
    assert result.expected_result.accepted is False
    # Exactly the legs this case declares: the coverage leg it exists to
    # record, and the undisclosed omission the scripted doubles produce.
    assert sorted(result.hard_failures) == [
        "coverage_below_0.80",
        "unaccounted_required_targets",
    ]
    for repetition in result.repetitions:
        state = repetition.state
        metrics = repetition.deterministic
        assert metrics.claimed_coverage_ratio == 1.0
        assert metrics.coverage_ratio == 0.75
        assert "coverage_below_0.80" in metrics.integrity_failures
        # The two halves of the divergence, read from the run rather than from
        # the number: the open topic's obligation is unanswered, and a checked
        # claim still records consuming that topic.
        open_topic = state.sub_topics[-1]
        target = counted_evidence_targets(open_topic.evidence_targets)[0]
        assert target_is_answered(state, target) is False
        assert open_topic.coverage_id in {
            coverage_id
            for claim in state.verified_claims
            for coverage_id in claim.consumed_coverage_ids
        }
        assert state.quality is not None
        assert state.quality.measured_covered_topics == 3
        assert state.quality.covered_topics == 4


def test_a_plan_whose_obligations_are_all_answered_passes_the_coverage_verdict(
    tmp_path: Path,
) -> None:
    """The control: declaring obligations is not a way to fail by itself.

    Same plan, same evidence, same four topics — every obligation is answered
    by an independently corroborated statement, so the reading is 1.00, no
    coverage leg is recorded, and the row is accepted. Without this the case
    above would prove only that a plan with obligations cannot pass.
    """
    result = run_case(
        ANSWERED_OBLIGATIONS_CASE,
        tier="controlled",
        repetitions=3,
        output_directory=tmp_path,
    )

    assert result.accepted is True
    assert result.met_expectation is True
    assert result.mean_coverage == 1.0
    for repetition in result.repetitions:
        metrics = repetition.deterministic
        assert metrics.coverage_ratio == 1.0
        assert metrics.claimed_coverage_ratio == 1.0
        assert metrics.integrity_failures == []
        assert metrics.hard_failures == []
        assert repetition.state.quality is not None
        assert repetition.state.quality.measured_covered_topics == 4

