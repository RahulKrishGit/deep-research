"""The local case registry is the source of truth and must be valid."""

from __future__ import annotations

import pytest

from deep_research.evaluation.cases import (
    CASE_REGISTRY_VERSION,
    FIXED_TIMESTAMP,
    CaseRegistryError,
    all_cases,
    build_case,
    case_by_id,
    case_by_identity,
    cases_for,
    claim,
    evaluation_state,
    finding,
    metrics,
    rubric,
    scored_source,
    sub_topic,
    validate_registry,
)
from deep_research.evaluation.models import (
    AGENT_NAMES,
    CaseExpectations,
    EvaluationCase,
    UnknownCaseError,
)
from deep_research.utils.types import MemorySnapshot

CONTROLLED_IDS = {
    "planner": (
        "focused-decomposition",
        "ambiguous-scope",
        "planning-tool-failure",
    ),
    "researcher": (
        "multi-source-coverage",
        "conflicting-evidence",
        "partial-search-failure",
    ),
    "source_evaluator": (
        "strong-and-weak-sources",
        "corroboration-recency-reputation",
        "reputation-provider-failure",
    ),
    "fact_checker": (
        "mixed-verdicts",
        "independent-domain-evidence",
        "verification-search-failure",
    ),
    "synthesizer": (
        "complete-cited-report",
        "conflict-and-limitations",
        "write-or-memory-failure",
    ),
    "critic": (
        "approve-strong-report",
        "request-more-research",
        "missing-evidence-or-budget-exhausted",
    ),
}
LIVE_IDS = {
    "planner": "planner-live-scope",
    "researcher": "researcher-live-evidence",
    "source_evaluator": "source-evaluator-live-ranking",
    "fact_checker": "fact-checker-live-verification",
    "synthesizer": "synthesizer-live-report",
    "critic": "critic-live-review",
}


# The four count tests below are expected red until all six case files
# land. strict=True turns the marker into a failure the moment a case
# lands, so Task 15 must delete the remaining markers. The four
# lookup/validation tests lost their markers in Task 10, when the Planner
# cases made them pass.


@pytest.mark.xfail(reason="cases land in Tasks 10-15", strict=True)
def test_the_registry_is_valid() -> None:
    validate_registry()


@pytest.mark.xfail(reason="cases land in Tasks 10-15", strict=True)
def test_every_agent_has_exactly_three_controlled_cases() -> None:
    for agent_name in AGENT_NAMES:
        controlled = cases_for(agent_name, "controlled")
        assert len(controlled) == 3, agent_name
        assert tuple(case.case_id for case in controlled) == (
            CONTROLLED_IDS[agent_name]
        )


@pytest.mark.xfail(reason="cases land in Tasks 10-15", strict=True)
def test_every_agent_has_exactly_one_live_case() -> None:
    for agent_name in AGENT_NAMES:
        live = cases_for(agent_name, "live")
        assert len(live) == 1, agent_name
        assert live[0].case_id == LIVE_IDS[agent_name]


@pytest.mark.xfail(reason="cases land in Tasks 10-15", strict=True)
def test_the_registry_holds_twenty_four_cases() -> None:
    assert len(all_cases()) == 24


def test_every_case_carries_its_own_agent_and_tier() -> None:
    for agent_name in AGENT_NAMES:
        for tier in ("controlled", "live"):
            for case in cases_for(agent_name, tier):
                assert case.agent_name == agent_name
                assert case.tier == tier


def test_case_ids_are_globally_unique() -> None:
    ids = [case.case_id for case in all_cases()]

    assert len(set(ids)) == len(ids)


def test_every_case_states_a_purpose_and_a_scenario() -> None:
    for case in all_cases():
        assert case.purpose.strip()
        assert case.dependency_scenario.strip()
        assert case.expectations.deterministic_metrics


def test_every_case_state_uses_an_evaluation_session_id() -> None:
    """A case must never carry a production-looking session id."""
    for case in all_cases():
        assert case.state.session_id.startswith("evaluation-")


def test_lookup_by_id_and_by_identity() -> None:
    case = case_by_id("planner", "controlled", "focused-decomposition")

    assert case.case_id == "focused-decomposition"
    assert case_by_identity(case.case_id, case.version) == case


def test_an_unknown_case_id_lists_the_valid_ones() -> None:
    with pytest.raises(UnknownCaseError) as caught:
        case_by_id("planner", "controlled", "not-a-case")

    message = str(caught.value)
    assert "not-a-case" in message
    for case_id in CONTROLLED_IDS["planner"]:
        assert case_id in message


def test_a_duplicate_case_id_fails_validation() -> None:
    cases = list(all_cases())
    cases.append(cases[0])

    with pytest.raises(CaseRegistryError) as caught:
        validate_registry(cases)

    assert "duplicate" in str(caught.value)


def test_two_versions_of_one_case_id_fail_validation() -> None:
    """The same id at two versions is ambiguous for dataset matching."""
    cases = list(all_cases())
    cases.append(cases[0].model_copy(update={"version": 2}))

    with pytest.raises(CaseRegistryError) as caught:
        validate_registry(cases)

    assert "conflicting version" in str(caught.value)


def test_a_wrong_case_count_fails_validation() -> None:
    cases = [case for case in all_cases() if case.case_id != "ambiguous-scope"]

    with pytest.raises(CaseRegistryError) as caught:
        validate_registry(cases)

    assert "planner" in str(caught.value)
    assert "3 controlled" in str(caught.value)


def test_the_registry_version_is_recorded() -> None:
    assert CASE_REGISTRY_VERSION >= 1


# The shared fixture builders are the API every case file (Tasks 10-15)
# imports, and the validation rules are this task's deliverable, so both
# get exercised here on synthetic catalogs rather than waiting for the
# registry to fill.


def test_the_fixture_builders_construct_a_complete_case() -> None:
    topic = sub_topic(
        "First subtopic",
        rationale="covers the question",
        queries=["query one", "query two"],
        criteria=["criterion"],
        priority=1,
    )
    item = finding(
        "A useful finding.",
        url="https://example.com/a",
        title="Example A",
        sub_topic_title="First subtopic",
    )
    source = scored_source(
        "https://example.com/a",
        title="Example A",
        authority=0.9,
        recency=0.8,
        relevance=0.9,
        corroboration=0.7,
        overall=0.85,
        rationale="strong domain fit",
    )
    item_claim = claim(
        "The claim.",
        urls=["https://example.com/a"],
        verdict="verified",
        confidence=0.9,
        evidence=["source text"],
    )
    state = evaluation_state(
        case_id="focused-decomposition",
        question="A question?",
        sub_topics=[topic],
        findings=[item],
        sources=[source],
        claims=[item_claim],
        memory_context=MemorySnapshot(
            similar_findings=[item],
            known_source_reputations={"https://example.com/a": 0.9},
        ),
    )
    case = build_case(
        case_id="focused-decomposition",
        agent_name="planner",
        tier="controlled",
        title="Decompose a focused research question",
        purpose="Purpose.",
        state=state,
        dependency_scenario="planner-clean-memory",
        expectations=CaseExpectations(
            required_output_fields=["sub_topics"],
            max_iterations=5,
            max_tool_calls=10,
            deterministic_metrics=metrics(
                ("subtopic_count", 0.5, "Between 3 and 7 subtopics."),
                ("query_quality", 0.5, "Queries are non-trivial."),
            ),
        ),
        judge_rubric=rubric(
            "planner-decomposition",
            (
                "decomposition_quality",
                "Subtopics partition the question.",
                "Distinct subtopics covering the question.",
                "Overlapping subtopics.",
            ),
        ),
    )

    assert case.identity == ("focused-decomposition", 1)
    assert case.state.session_id == "evaluation-focused-decomposition"
    assert case.state.raw_findings[0].extracted_at == FIXED_TIMESTAMP
    assert case.state.memory_context.known_source_reputations == {
        "https://example.com/a": 0.9
    }
    assert (
        case.judge_rubric.agent_dimensions[0].anchors["1.0"]
        == "Distinct subtopics covering the question."
    )
    assert sum(
        metric.weight for metric in case.expectations.deterministic_metrics
    ) == pytest.approx(1.0)


def _validation_case(case_id: str, *, version: int = 1) -> EvaluationCase:
    return build_case(
        case_id=case_id,
        agent_name="planner",
        tier="controlled",
        title=case_id,
        purpose="Purpose.",
        state=evaluation_state(case_id=case_id, question="A question?"),
        dependency_scenario="planner-clean-memory",
        expectations=CaseExpectations(
            required_output_fields=["sub_topics"],
            max_iterations=5,
            max_tool_calls=10,
            deterministic_metrics=metrics(("m", 1.0, "d")),
        ),
        judge_rubric=rubric("planner-decomposition"),
        version=version,
    )


def test_validation_rejects_duplicates_and_conflicting_versions() -> None:
    duplicate = [_validation_case("dup"), _validation_case("dup")]

    with pytest.raises(CaseRegistryError) as caught:
        validate_registry(duplicate)

    assert "duplicate" in str(caught.value)

    conflicting = [
        _validation_case("same-id"),
        _validation_case("same-id", version=2),
    ]

    with pytest.raises(CaseRegistryError) as caught:
        validate_registry(conflicting)

    assert "conflicting version" in str(caught.value)


def test_validation_rejects_a_wrong_case_count() -> None:
    two_of_three = [_validation_case("one"), _validation_case("two")]

    with pytest.raises(CaseRegistryError) as caught:
        validate_registry(two_of_three)

    assert "planner" in str(caught.value)
    assert "3 controlled" in str(caught.value)
