"""Controlled whole-report campaign case contracts."""

from __future__ import annotations

from deep_research.e2e_evaluation.cases import (
    CONTROLLED_CASE_IDS,
    controlled_cases,
    dependencies_for,
)
from deep_research.e2e_evaluation.replay_matrix import (
    GRAPH_ONLY_HISTORICAL_MANIFEST,
)


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
