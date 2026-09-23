"""The target: one agent run inside the project's own session trace."""

from __future__ import annotations

import asyncio
import json
from hashlib import sha256

import pytest

from deep_research.agents.errors import PlanningError
from deep_research.agents.planner import (
    EvidenceTargetDraft,
    PlanReviewDraft,
    ResearchPlanDraft,
    SubTopicDraft,
)
from deep_research.agents.steps import ReActDecision
from deep_research.evaluation.dependencies import (
    bounded_url_fingerprints,
    build_controlled_dependencies,
)
from deep_research.evaluation.models import StructuredCallSummary, TargetOutput
from deep_research.evaluation.targets import (
    TRACE_TAG,
    RepetitionCounter,
    _classify_failure,
    build_target,
    correlation_metadata,
    trace_tags,
)
from deep_research.observability import TokenUsage
from deep_research.providers import (
    ProviderOutputLimitError,
    ProviderResponseTelemetry,
    StructuredOutputError,
)
from tests.evaluation_fakes import FakeStructuredProvider


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


def test_target_classifier_preserves_output_limit_through_planner_wrapper() -> None:
    cause = ProviderOutputLimitError(
        ProviderResponseTelemetry(
            finish_reason_category="length",
            configured_max_tokens=4096,
            usage=TokenUsage(input_tokens=4, output_tokens=4096),
            request_attempt=1,
        )
    )
    try:
        raise PlanningError("The planner operation failed") from cause
    except PlanningError as error:
        stage, reason = _classify_failure(error)

    assert (stage, reason) == ("provider", "output_limit")
    assert "reach" not in reason


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
async def test_a_non_planner_fallback_preserves_typed_provider_diagnostics(
    tmp_path, tracker, settings, runtime_config_for, controlled_case_for_id
) -> None:
    case = controlled_case_for_id(
        "source_evaluator", "strong-and-weak-sources"
    )
    runtime = runtime_config_for("source_evaluator", case_id=case.case_id)
    cause = ProviderOutputLimitError(
        ProviderResponseTelemetry(
            finish_reason_category="length",
            configured_max_tokens=4096,
            usage=TokenUsage(input_tokens=4, output_tokens=4096),
            request_attempt=1,
        )
    )
    target = build_target(
        runtime,
        settings,
        tracker_factory=lambda: tracker,
        dependency_factory=build_controlled_dependencies,
        provider_factory=lambda: FakeStructuredProvider([cause]),
        counter=RepetitionCounter(max_concurrency=1),
        secrets=(),
        root=tmp_path,
    )

    payload = await target(
        {
            "case_id": case.case_id,
            "case_version": case.version,
            "agent": "source_evaluator",
            "tier": "controlled",
        }
    )
    output = TargetOutput.model_validate(payload)

    assert output.completed is True
    assert output.failure is None
    assert output.result is not None
    assert output.errors[0]["details"]["provider_failure"]["kind"] == (
        "output_limit"
    )
    assert output.errors[0]["details"]["provider_failure"][
        "configured_max_tokens"
    ] == 4096


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
async def test_a_live_researcher_records_only_source_url_fingerprints(
    runtime_config_for, live_case_for, live_target_harness
) -> None:
    case = live_case_for("researcher")
    target = live_target_harness(case, runtime_config_for("researcher", tier="live"))

    output = TargetOutput.model_validate(
        await target(
            {
                "case_id": case.case_id,
                "case_version": case.version,
                "agent": "researcher",
                "tier": "live",
            }
        )
    )

    expected = sha256(
        "https://example.com/sodium-ion-energy-density".encode("utf-8")
    ).hexdigest()
    assert output.dependencies.source_url_fingerprints == [expected]
    assert all(
        len(step.observation_summary) <= 200 for step in output.trajectory
    )
    assert "https://example.com/sodium-ion-energy-density" not in " ".join(
        step.observation_summary for step in output.trajectory
    )
    assert "example.com" not in repr(output.dependencies)


@pytest.mark.asyncio
async def test_live_researcher_artifact_marks_complete_source_provenance(
    runtime_config_for, live_case_for, live_target_harness
) -> None:
    case = live_case_for("researcher")
    target = live_target_harness(case, runtime_config_for("researcher", tier="live"))

    payload = await target(
        {
            "case_id": case.case_id,
            "case_version": case.version,
            "agent": "researcher",
            "tier": "live",
        }
    )

    assert payload["dependencies"]["source_url_fingerprints_complete"] is True
    assert (
        TargetOutput.model_validate(payload)
        .dependencies.source_url_fingerprints_complete
        is True
    )


@pytest.mark.asyncio
async def test_the_artifact_separates_discovery_from_read_provenance(
    runtime_config_for, live_case_for, live_target_harness
) -> None:
    """Task 5, R5/R6: the artifact proves a READ, not a search.

    This scripted live run only ever searches, so its discovery ledger holds
    the URL's identity while its read ledger — the one a verification-passage
    gate may trust — is empty AND explicitly complete. That is exactly why
    ``source_url_fingerprints`` cannot serve as read-bearing proof: it records
    ``web_search`` result URLs too.
    """
    case = live_case_for("researcher")
    target = live_target_harness(
        case, runtime_config_for("researcher", tier="live")
    )

    output = TargetOutput.model_validate(
        await target(
            {
                "case_id": case.case_id,
                "case_version": case.version,
                "agent": "researcher",
                "tier": "live",
            }
        )
    )

    expected = sha256(
        "https://example.com/sodium-ion-energy-density".encode("utf-8")
    ).hexdigest()
    assert output.dependencies.source_url_fingerprints == [expected]
    assert output.dependencies.read_url_fingerprints == []
    assert output.dependencies.read_url_fingerprints_complete is True


def test_read_provenance_is_bounded_and_reports_itself_incomplete() -> None:
    """An identity that does not fit is dropped, never silently implied."""
    urls = [f"https://example.test/page-{index}" for index in range(5)]

    bounded, complete = bounded_url_fingerprints(urls, limit=3)
    exact, exact_complete = bounded_url_fingerprints(urls, limit=5)

    assert complete is False
    assert len(bounded) == 3
    assert (len(exact), exact_complete) == (5, True)


def test_read_provenance_skips_urls_that_cannot_carry_an_identity() -> None:
    """A non-HTTP or malformed URL contributes nothing rather than a hash."""
    assert bounded_url_fingerprints(
        ["not-a-url", "file:///tmp/x", "https://example.test/page"]
    )[0] == [sha256("https://example.test/page".encode("utf-8")).hexdigest()]


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


# --- The content-free structured-repair ledger --------------------------------

_LEDGER_SENTINEL = "sentinel-provider-payload"


def _planner_script() -> list[object]:
    """The script a real planner agent accepts: one finish, one valid plan.

    The plan carries the three sub-topics the planner's own validation
    requires, so exactly one structured call is made for it; a plan the
    planner rejected would be re-requested and the ledger would count two
    logical calls instead of one.
    """
    return [
        ReActDecision(
            thought="I have enough context to proceed.",
            action="finish",
            tool_input_json="{}",
            final_answer="Scoping complete.",
        ),
        ResearchPlanDraft(
            sub_topics=[
                SubTopicDraft(
                    title=title,
                    rationale=f"Rationale for {title}.",
                    search_queries=[f"query about {title}"],
                    success_criteria=[f"evidence about {title}"],
                    priority=index,
                    evidence_targets=[
                        EvidenceTargetDraft(
                            question=f"What does {title} measure?",
                            required_dimensions=[f"measure: {title}"],
                            critical=index == 1,
                        )
                    ],
                )
                for index, title in enumerate(
                    (
                        "Solid-state electrolyte degradation",
                        "Cathode interface resistance",
                        "Mechanical stress and cracking",
                    ),
                    start=1,
                )
            ]
        ),
        PlanReviewDraft(
            sound=True,
            missing_dimensions=[],
            atomicity_defects=[],
            unsupported_premises=[],
            repair_instruction="",
        ),
    ]


class _LedgerProvider(FakeStructuredProvider):
    """A fake provider that opens the spans the real adapter opens.

    With ``repair`` set it performs the provider's own single structured
    repair: attempt 1 fails with a payload-bearing error, attempt 2
    succeeds. Both attempts are real tracker spans, so the ledger the
    target summarizes is the one the production path would produce. The
    sentinel rides both the span inputs and the failed attempt's error, so
    any path that copied span content into the artifact would show it.
    """

    def __init__(self, tracker, responses, *, repair: bool = False) -> None:
        super().__init__(responses=responses)
        self._tracker = tracker
        self._repair = repair

    async def complete_structured(
        self,
        messages,
        schema,
        *,
        agent_name=None,
        max_tokens=None,
        reasoning_effort=None,
    ):
        attempts = (1, 2) if self._repair else (1,)
        for attempt in attempts:
            try:
                async with self._tracker.llm_span(
                    "deepseek-v4-flash",
                    {
                        "operation": "structured_output",
                        "attempt": attempt,
                        "prompt": _LEDGER_SENTINEL,
                    },
                ):
                    if self._repair and attempt == 1:
                        raise StructuredOutputError(
                            f"invalid JSON {_LEDGER_SENTINEL}"
                        )
                    return await super().complete_structured(
                        messages,
                        schema,
                        agent_name=agent_name,
                        max_tokens=max_tokens,
                        reasoning_effort=reasoning_effort,
                    )
            except StructuredOutputError:
                if attempt == len(attempts):
                    raise
        raise AssertionError("the ledger provider never returned")


def _ledger_target(tracker, settings, runtime, tmp_path, *, repair: bool):
    return build_target(
        runtime,
        settings,
        tracker_factory=lambda: tracker,
        dependency_factory=build_controlled_dependencies,
        provider_factory=lambda: _LedgerProvider(
            tracker, _planner_script(), repair=repair
        ),
        counter=RepetitionCounter(max_concurrency=1),
        secrets=(),
        root=tmp_path,
    )


def _planner_inputs(case) -> dict[str, object]:
    return {
        "case_id": case.case_id,
        "case_version": case.version,
        "agent": "planner",
        "tier": "controlled",
    }


@pytest.mark.asyncio
async def test_a_repaired_structured_call_is_counted_without_its_content(
    tracker, settings, tmp_path, runtime_config_for, planner_case
) -> None:
    target = _ledger_target(
        tracker,
        settings,
        runtime_config_for("planner"),
        tmp_path,
        repair=True,
    )

    payload = await target(_planner_inputs(planner_case))
    output = TargetOutput.model_validate(payload)

    assert output.completed is True, output.failure
    # Two structured calls, and this scripted provider fails the first
    # attempt of each: the plan draft and the planner's tool-free plan
    # review. Neither call's content may appear in the ledger.
    assert output.structured_calls == StructuredCallSummary(
        calls=2, repaired_calls=2, failed_attempts=2
    )
    assert output.model_dump(mode="json")["structured_calls"] == {
        "calls": 2,
        "repaired_calls": 2,
        "failed_attempts": 2,
    }
    serialized = json.dumps(payload)
    assert _LEDGER_SENTINEL not in serialized


@pytest.mark.asyncio
async def test_a_first_try_structured_call_is_not_counted_as_repaired(
    tracker, settings, tmp_path, runtime_config_for, planner_case
) -> None:
    target = _ledger_target(
        tracker,
        settings,
        runtime_config_for("planner"),
        tmp_path,
        repair=False,
    )

    payload = await target(_planner_inputs(planner_case))
    output = TargetOutput.model_validate(payload)

    assert output.completed is True
    # The plan draft and the plan review, both first-try.
    assert output.structured_calls == StructuredCallSummary(
        calls=2, repaired_calls=0, failed_attempts=0
    )


@pytest.mark.asyncio
async def test_concurrent_repetitions_count_only_their_own_attempts(
    tracker, settings, tmp_path, runtime_config_for, planner_case
) -> None:
    """Two live sessions on one tracker must not merge into one ledger."""
    target = _ledger_target(
        tracker,
        settings,
        runtime_config_for("planner"),
        tmp_path,
        repair=True,
    )
    inputs = _planner_inputs(planner_case)

    first, second = await asyncio.gather(target(inputs), target(inputs))
    outputs = [TargetOutput.model_validate(payload) for payload in (first, second)]

    assert len({output.session_id for output in outputs}) == 2
    for output in outputs:
        assert output.completed is True
        # A session-blind count would report four calls and four repairs here.
        assert output.structured_calls == StructuredCallSummary(
            calls=2, repaired_calls=2, failed_attempts=2
        )
