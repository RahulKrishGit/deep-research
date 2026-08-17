"""Planner evaluation cases: decomposition, ambiguity, and recovery."""

from __future__ import annotations

import pytest

from deep_research.evaluation.cases import cases_for
from deep_research.evaluation.dependencies import SCENARIOS

CONTROLLED = ("focused-decomposition", "ambiguous-scope", "planning-tool-failure")

_METRICS = {
    "focused-decomposition": (
        ("subtopic_count", 0.25),
        ("distinct_titles", 0.25),
        ("priority_ordering", 0.20),
        ("query_quality", 0.15),
        ("question_preserved", 0.15),
    ),
    "ambiguous-scope": (
        ("subtopic_count", 0.20),
        ("distinct_titles", 0.20),
        ("balanced_coverage", 0.30),
        ("no_invented_constraints", 0.30),
    ),
    "planning-tool-failure": (
        ("plan_still_valid", 0.40),
        ("failure_recorded", 0.35),
        ("bounded_recovery", 0.25),
    ),
    "planner-live-scope": (
        ("subtopic_count", 0.25),
        ("distinct_titles", 0.25),
        ("priority_ordering", 0.20),
        ("query_quality", 0.15),
        ("question_preserved", 0.15),
    ),
}

_REFERENCES = {
    "focused-decomposition": {
        "minimum_sub_topics": 3,
        "maximum_sub_topics": 7,
        "expected_themes": [
            "dendrite formation",
            "interfacial resistance",
            "mechanical stress and cracking",
            "cycle-life measurement",
        ],
    },
    "ambiguous-scope": {
        "minimum_sub_topics": 4,
        "maximum_sub_topics": 7,
        "forbidden_assumptions": [
            "specific country",
            "specific vendor",
            "specific year",
        ],
        "required_balance": ["benefits", "risks", "evidence quality"],
    },
    "planning-tool-failure": {
        "minimum_sub_topics": 3,
        "expected_error_sources": ["query_memory"],
    },
}


def _all_planner_cases():
    return [*cases_for("planner", "controlled"), *cases_for("planner", "live")]


def test_the_three_controlled_cases_are_registered() -> None:
    assert tuple(
        case.case_id for case in cases_for("planner", "controlled")
    ) == CONTROLLED


def test_the_single_live_case_is_registered() -> None:
    live = cases_for("planner", "live")

    assert len(live) == 1
    assert live[0].case_id == "planner-live-scope"


def test_every_scenario_is_scripted() -> None:
    for case in cases_for("planner", "controlled"):
        assert case.dependency_scenario in SCENARIOS


def test_the_live_case_uses_the_literal_live_scenario() -> None:
    live = cases_for("planner", "live")[0]

    assert live.dependency_scenario == "live"


@pytest.mark.parametrize("case_id", CONTROLLED)
def test_each_case_declares_weighted_metrics(case_id: str) -> None:
    case = next(
        item
        for item in cases_for("planner", "controlled")
        if item.case_id == case_id
    )

    assert case.expectations.deterministic_metrics
    assert (
        abs(
            sum(
                metric.weight
                for metric in case.expectations.deterministic_metrics
            )
            - 1.0
        )
        < 1e-9
    )


def test_the_failure_case_requires_a_recorded_recoverable_error() -> None:
    case = next(
        item
        for item in cases_for("planner", "controlled")
        if item.case_id == "planning-tool-failure"
    )

    assert case.expectations.must_record_recoverable_error is True


def test_no_case_state_contains_a_url_outside_its_known_sources() -> None:
    """Citations are gated against ``known_source_urls``; a case whose own
    state cites a URL it does not declare would fail its own gate."""
    for case in cases_for("planner", "controlled"):
        declared = set(case.expectations.known_source_urls)
        cited = {
            finding.source_url for finding in case.state.raw_findings
        } | {source.url for source in case.state.evaluated_sources}
        assert cited <= declared, case.case_id


def test_live_cases_declare_the_real_dependencies_they_need() -> None:
    live = cases_for("planner", "live")[0]

    assert live.expectations.required_live_dependencies == ["memory"]


@pytest.mark.parametrize("case_id", sorted(_METRICS))
def test_each_case_pins_its_metric_ids_and_weights(case_id: str) -> None:
    case = next(
        item
        for item in _all_planner_cases()
        if item.case_id == case_id
    )

    assert tuple(
        (metric.metric_id, metric.weight)
        for metric in case.expectations.deterministic_metrics
    ) == _METRICS[case_id]


@pytest.mark.parametrize("case_id", sorted(_REFERENCES))
def test_each_case_pins_its_reference(case_id: str) -> None:
    case = next(
        item
        for item in cases_for("planner", "controlled")
        if item.case_id == case_id
    )

    assert case.expectations.reference == _REFERENCES[case_id]


def test_each_case_has_its_own_judge_rubric_instance() -> None:
    """build_case does not copy judge_rubric, so sharing one instance across
    cases would let one case's mutations leak into the others."""
    cases = _all_planner_cases()

    assert len({id(case.judge_rubric) for case in cases}) == len(cases)


@pytest.mark.parametrize(
    ("case_id", "dimensions"),
    [
        (
            "focused-decomposition",
            {"decomposition_quality", "search_framing"},
        ),
        (
            "ambiguous-scope",
            {"decomposition_quality", "search_framing", "ambiguity_handling"},
        ),
        (
            "planning-tool-failure",
            {"decomposition_quality", "search_framing", "failure_transparency"},
        ),
        (
            "planner-live-scope",
            {"decomposition_quality", "search_framing"},
        ),
    ],
)
def test_each_case_pins_its_rubric_dimensions(
    case_id: str, dimensions: set[str]
) -> None:
    case = next(
        item for item in _all_planner_cases() if item.case_id == case_id
    )

    assert {
        dimension.dimension_id
        for dimension in case.judge_rubric.agent_dimensions
    } == dimensions


def test_the_ambiguous_scope_case_seeds_unrelated_memory() -> None:
    """Recall is available, but nothing resolves the ambiguity."""
    script = SCENARIOS["planner-ambiguous-scope"]

    assert [
        entry["content"] for entry in script.memory_entries
    ] == [
        "Hospital triage pilots reported mixed results.",
        "Diagnostic imaging models improved recall.",
    ]


def test_the_failure_case_scripts_a_memory_error_and_recovery_search() -> None:
    script = SCENARIOS["planner-memory-failure"]

    failure = script.failures["query_memory"]
    assert isinstance(failure, RuntimeError)
    assert "long-term memory is unavailable" in str(failure)
    assert script.scripted_search_urls == (
        "https://www.nih.gov/fasting-review",
        "https://www.bmj.com/fasting-trial",
    )
    assert "intermittent fasting metabolic health review" in (
        script.search_responses
    )
