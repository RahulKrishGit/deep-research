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

# The registered ids are not restated here. Their declaration lives in the
# registry module itself (``EXPECTED_CONTROLLED_CASE_IDS`` /
# ``EXPECTED_LIVE_CASE_IDS``) and these tests compare the registry against
# it: an inventory written down twice is two sources of truth, and the
# second one is always the stale one. A literal list here would also have
# to be edited by every round that adds a case, which is how a test that
# was supposed to guard the registry becomes a rubber stamp.


# The four count tests below are expected red until all six case files
# land. strict=True turns the marker into a failure the moment a case
# lands, so Task 15 must delete the remaining markers. The four
# lookup/validation tests lost their markers in Task 10, when the Planner
# cases made them pass.


def test_the_registry_is_valid() -> None:
    validate_registry()


def test_every_agent_carries_its_declared_controlled_cases() -> None:
    from deep_research.evaluation.cases import EXPECTED_CONTROLLED_CASE_IDS

    for agent_name in AGENT_NAMES:
        found = tuple(
            case.case_id for case in cases_for(agent_name, "controlled")
        )
        assert found == EXPECTED_CONTROLLED_CASE_IDS[agent_name], agent_name


def test_every_agent_carries_its_declared_live_cases() -> None:
    from deep_research.evaluation.cases import EXPECTED_LIVE_CASE_IDS

    for agent_name in AGENT_NAMES:
        found = tuple(case.case_id for case in cases_for(agent_name, "live"))
        assert found == EXPECTED_LIVE_CASE_IDS[agent_name], agent_name


def test_the_registry_holds_the_declared_inventory() -> None:
    from deep_research.evaluation.cases import (
        EXPECTED_CONTROLLED_CASE_IDS,
        EXPECTED_LIVE_CASE_IDS,
    )

    declared = sum(
        len(ids) for ids in EXPECTED_CONTROLLED_CASE_IDS.values()
    ) + sum(len(ids) for ids in EXPECTED_LIVE_CASE_IDS.values())

    assert len(all_cases()) == declared


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
    from deep_research.evaluation.cases import EXPECTED_CONTROLLED_CASE_IDS

    with pytest.raises(UnknownCaseError) as caught:
        case_by_id("planner", "controlled", "not-a-case")

    message = str(caught.value)
    assert "not-a-case" in message
    for case_id in EXPECTED_CONTROLLED_CASE_IDS["planner"]:
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


def test_a_missing_controlled_case_fails_validation() -> None:
    cases = [case for case in all_cases() if case.case_id != "ambiguous-scope"]

    with pytest.raises(CaseRegistryError) as caught:
        validate_registry(cases)

    message = str(caught.value)
    assert "planner" in message
    # The mismatch is reported by id. A count check would have caught this
    # one too, but it could not have caught the case that matters more: an
    # id renamed or filed under the wrong agent still satisfies any length.
    assert "ambiguous-scope" in message


def test_the_registry_version_is_recorded() -> None:
    """Pinned exactly: a bump must be a deliberate, visible act.

    A ``>= 1`` lower bound would keep passing through every future bump,
    which is the opposite of what versioning case semantics is for.
    """
    assert CASE_REGISTRY_VERSION == 2


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
        overall=0.85,
        rationale="strong domain fit",
    )
    state = evaluation_state(
        case_id="focused-decomposition",
        question="A question?",
        sub_topics=[topic],
        findings=[item],
        sources=[source],
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
    assert case.state.verified_findings == []
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


def test_validation_rejects_an_inventory_that_does_not_match() -> None:
    two_of_three = [_validation_case("one"), _validation_case("two")]

    with pytest.raises(CaseRegistryError) as caught:
        validate_registry(two_of_three)

    message = str(caught.value)
    assert "planner" in message
    # The message names what the registry declares, not a number to match.
    assert "focused-decomposition" in message
