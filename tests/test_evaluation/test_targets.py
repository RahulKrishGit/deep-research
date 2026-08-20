"""The target: one agent run inside the project's own session trace."""

from __future__ import annotations

import pytest

from deep_research.evaluation.models import TargetOutput
from deep_research.evaluation.targets import (
    TRACE_TAG,
    RepetitionCounter,
    build_target,  # noqa: F401 - imported to assert the module's public surface
    correlation_metadata,
    trace_tags,
)


def test_the_counter_refuses_concurrency_above_one() -> None:
    """Repetition indexing is only exact when execution is sequential."""
    with pytest.raises(ValueError) as caught:
        RepetitionCounter(max_concurrency=2)

    assert "max_concurrency" in str(caught.value)


def test_the_counter_numbers_repetitions_per_case_from_one() -> None:
    counter = RepetitionCounter(max_concurrency=1)

    assert counter.next("a") == 1
    assert counter.next("b") == 1
    assert counter.next("a") == 2
    assert counter.next("a") == 3
    assert counter.next("b") == 2


def test_every_trace_carries_the_tags_the_spec_lists(
    planner_case, runtime_config_for
) -> None:
    runtime = runtime_config_for("planner")

    tags = trace_tags(runtime, planner_case, 2)

    assert TRACE_TAG in tags
    assert "agent:planner" in tags
    assert "tier:controlled" in tags
    assert f"case:{planner_case.case_id}" in tags
    assert f"case_version:{planner_case.version}" in tags
    assert "repetition:2" in tags
    assert f"experiment:{runtime.experiment_name}" in tags
    assert "git:abc1234" in tags
    assert "target_model:deepseek-v4-flash" in tags
    assert "rubric_version:1" in tags


def test_correlation_metadata_links_the_session_to_the_experiment(
    planner_case, runtime_config_for
) -> None:
    """If the SDK creates a sibling root instead of nesting, this metadata
    is what makes the two trees findable from each other."""
    runtime = runtime_config_for("planner")

    metadata = correlation_metadata(
        runtime, planner_case, repetition=1, session_id="evaluation-x"
    )

    assert metadata["experiment_name"] == runtime.experiment_name
    assert metadata["case_id"] == planner_case.case_id
    assert metadata["case_version"] == planner_case.version
    assert metadata["repetition"] == 1
    assert metadata["session_id"] == "evaluation-x"


@pytest.mark.asyncio
async def test_a_successful_target_returns_a_typed_redacted_output(
    settings, tmp_path, runtime_config_for, planner_case, target_harness
) -> None:
    target = target_harness(planner_case, runtime_config_for("planner"))

    payload = await target(
        {
            "case_id": planner_case.case_id,
            "case_version": planner_case.version,
            "agent": "planner",
            "tier": "controlled",
        }
    )
    output = TargetOutput.model_validate(payload)

    assert output.completed is True
    assert output.result is not None
    assert output.agent_name == "planner"
    assert output.repetition == 1
    assert output.session_id.startswith("evaluation-")
    assert output.target_model_requested == "deepseek-v4-flash"
    assert output.target_reasoning_effort == "max"
    assert output.react is not None


@pytest.mark.asyncio
async def test_the_target_opens_the_project_session_span(
    runtime_config_for, planner_case, target_harness, tracker
) -> None:
    """Reuse the tracker, do not reimplement spans."""
    target = target_harness(planner_case, runtime_config_for("planner"))

    await target({"case_id": planner_case.case_id, "case_version": 1,
                  "agent": "planner", "tier": "controlled"})

    span_names = [
        event.metadata["span_name"]
        for event in tracker.events
        if event.event_type == "observability.span.completed"
    ]
    assert "research.session" in span_names
    assert "agent.planner" in span_names


@pytest.mark.asyncio
async def test_each_repetition_gets_a_fresh_state_and_bundle(
    runtime_config_for, planner_case, target_harness
) -> None:
    runtime = runtime_config_for("planner")
    target = target_harness(planner_case, runtime)
    inputs = {
        "case_id": planner_case.case_id,
        "case_version": 1,
        "agent": "planner",
        "tier": "controlled",
    }

    first = TargetOutput.model_validate(await target(inputs))
    second = TargetOutput.model_validate(await target(inputs))

    assert first.repetition == 1
    assert second.repetition == 2
    assert first.session_id != second.session_id
    assert planner_case.state.sub_topics == []  # the case was never mutated


@pytest.mark.asyncio
async def test_an_unknown_case_identity_is_a_typed_setup_failure(
    runtime_config_for, planner_case, target_harness
) -> None:
    target = target_harness(planner_case, runtime_config_for("planner"))

    payload = await target(
        {"case_id": "not-a-case", "case_version": 1, "agent": "planner",
         "tier": "controlled"}
    )
    output = TargetOutput.model_validate(payload)

    assert output.completed is False
    assert output.failure.stage == "setup"
    assert output.failure.reason == "unknown_case"
    assert output.result is None


@pytest.mark.asyncio
async def test_a_provider_failure_is_captured_not_raised(
    runtime_config_for, planner_case, failing_target_harness
) -> None:
    """One repetition's provider failure must not abort the other eight."""
    target = failing_target_harness(planner_case, runtime_config_for("planner"))

    payload = await target(
        {"case_id": planner_case.case_id, "case_version": 1,
         "agent": "planner", "tier": "controlled"}
    )
    output = TargetOutput.model_validate(payload)

    assert output.completed is False
    assert output.failure.stage == "provider"
    assert output.result is None
    assert output.failure.exception_type


@pytest.mark.asyncio
async def test_the_target_output_never_contains_a_secret(
    runtime_config_for, planner_case, leaking_target_harness
) -> None:
    target = leaking_target_harness(
        planner_case,
        runtime_config_for("planner"),
        secrets=("sk-abcdefghijklmnop",),
    )

    payload = await target(
        {"case_id": planner_case.case_id, "case_version": 1,
         "agent": "planner", "tier": "controlled"}
    )

    assert "sk-abcdefghijklmnop" not in repr(payload)
    assert "[REDACTED]" in repr(payload)


@pytest.mark.asyncio
async def test_the_ledger_records_real_services_for_a_live_run(
    runtime_config_for, live_case_for, live_target_harness
) -> None:
    case = live_case_for("researcher")
    target = live_target_harness(case, runtime_config_for("researcher",
                                                          tier="live"))

    payload = await target(
        {"case_id": case.case_id, "case_version": 1, "agent": "researcher",
         "tier": "live"}
    )
    output = TargetOutput.model_validate(payload)

    assert "tavily" in output.dependencies.real_services_used


@pytest.mark.asyncio
async def test_the_output_records_both_model_identifiers(
    runtime_config_for, planner_case, target_harness
) -> None:
    target = target_harness(planner_case, runtime_config_for("planner"))

    output = TargetOutput.model_validate(
        await target({"case_id": planner_case.case_id, "case_version": 1,
                      "agent": "planner", "tier": "controlled"})
    )

    assert output.target_model_requested == "deepseek-v4-flash"
    assert output.target_model_returned == "deepseek-v4-flash-fake"
