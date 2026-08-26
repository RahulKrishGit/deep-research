"""The judge must be an openable, named evaluator in the LangSmith UI."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from deep_research.evaluation.judging import (
    COMMON_DIMENSION_WEIGHTS,
    JUDGE_PROMPT_ID,
    build_judge_evaluator,
    judge_evaluator_metadata,
    judge_feedback_payload,
)
from deep_research.evaluation.models import JudgeScores, JudgeVerdict
from deep_research.observability import TokenUsage
from deep_research.providers import (
    OpenAIProviderError,
    ProviderOutputLimitError,
    ProviderResponseTelemetry,
    StructuredOutputError,
    StructuredValidationDiagnostic,
)
from tests.evaluation_fakes import (
    FakeExampleRow,
    FakeRun,
    FakeStructuredProvider,
)


class TrackerBoundStructuredProvider(FakeStructuredProvider):
    """Exercise the provider contract that requires a session span."""

    def __init__(self, tracker, responses):
        super().__init__(responses=responses)
        self._tracker = tracker

    async def complete_structured(self, messages, schema, *, agent_name=None):
        async with self._tracker.llm_span(
            "judge-test-model", {"operation": "judge"}
        ):
            return await super().complete_structured(
                messages, schema, agent_name=agent_name
            )


class RecordingSessionTracker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.active = False

    @asynccontextmanager
    async def session_span(self, session_id, question):
        self.calls.append((session_id, question))
        self.active = True
        try:
            yield
        finally:
            self.active = False


class SessionAwareStructuredProvider(FakeStructuredProvider):
    def __init__(self, tracker, responses):
        super().__init__(responses=responses)
        self._tracker = tracker

    async def complete_structured(self, messages, schema, *, agent_name=None):
        assert self._tracker.active is True
        return await super().complete_structured(
            messages, schema, agent_name=agent_name
        )


class RecordingTraceFactory:
    """Stands in for ``langsmith.traceable``; records what was traced."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, *, name, run_type, metadata, **kwargs):
        record = {
            "name": name,
            "run_type": run_type,
            "metadata": dict(metadata),
            "inputs": None,
            "outputs": None,
        }
        self.calls.append(record)

        def decorate(function):
            async def wrapper(*args, **inner):
                record["inputs"] = inner.get("judge_input") or args
                result = await function(*args, **inner)
                record["outputs"] = result
                return result

            return wrapper

        return decorate


class FakeRunTree:
    """The shape of LangSmith's injected ``run_tree`` the judge reads."""

    def __init__(self, url: str | None = None) -> None:
        self._url = url

    def get_url(self) -> str | None:
        return self._url


class RunTreeInjectingTraceFactory:
    """Stands in for ``langsmith.traceable`` when tracing is enabled:
    it injects a fake ``run_tree`` the way the real decorator does."""

    def __init__(self, url: str | None) -> None:
        self._url = url

    def __call__(self, *, name, run_type, metadata, **kwargs):
        del name, run_type, metadata, kwargs

        def decorate(function):
            async def wrapper(*args, **inner):
                inner["run_tree"] = FakeRunTree(self._url)
                return await function(*args, **inner)

            return wrapper

        return decorate


class _SequentialRunTreeFactory:
    """Injects one fake ``run_tree`` per invocation, in call order."""

    def __init__(self, urls: list[str]) -> None:
        self._urls = iter(urls)

    def __call__(self, *, name, run_type, metadata, **kwargs):
        del name, run_type, metadata, kwargs

        def decorate(function):
            async def wrapper(*args, **inner):
                inner["run_tree"] = FakeRunTree(next(self._urls))
                return await function(*args, **inner)

            return wrapper

        return decorate


class _GatedStructuredProvider(FakeStructuredProvider):
    """A provider that parks every judge call at one shared gate.

    With two concurrent invocations this forces a deterministic
    interleaving at the awaited model call: the first invocation has
    already captured its run-tree URL (``entered`` is set) and is parked
    at the gate before the second invocation starts, so any shared
    per-instance capture state would be overwritten by the second
    invocation before the first resumes.
    """

    def __init__(self, responses, *, gate, entered) -> None:
        super().__init__(responses=responses)
        self._gate = gate
        self._entered = entered

    async def complete_structured(self, messages, schema, *, agent_name=None):
        self._entered.set()
        await self._gate.wait()
        return await super().complete_structured(
            messages, schema, agent_name=agent_name
        )


def verdict(value: float = 0.8) -> JudgeVerdict:
    return JudgeVerdict(
        scores=JudgeScores(**{n: value for n in COMMON_DIMENSION_WEIGHTS}),
        agent_specific={"decomposition_quality": value},
        rationale="Concise and grounded.",
    )


def test_the_evaluator_is_named_so_the_column_is_identifiable(
    planner_case, runtime_config_for
) -> None:
    evaluator = build_judge_evaluator(
        FakeStructuredProvider(responses=[verdict()]),
        planner_case,
        runtime=runtime_config_for("planner"),
        secrets=(),
        gate_lookup=lambda output: None,
    )

    assert evaluator.__name__ == JUDGE_PROMPT_ID


def test_evaluator_metadata_carries_the_prompt_and_fingerprints(
    runtime_config_for,
) -> None:
    metadata = judge_evaluator_metadata(runtime_config_for("planner"))

    assert metadata["prompt_id"] == JUDGE_PROMPT_ID
    assert metadata["rubric_version"] == 1
    assert metadata["prompt_fingerprint"]
    assert metadata["judge_model"] == "deepseek-v4-flash"
    assert metadata["judge_configuration_fingerprint"]
    assert metadata["judge_reasoning_effort"] == "max"


@pytest.mark.asyncio
async def test_the_judge_invocation_is_traced_with_its_sanitized_input(
    planner_case, clean_target_output, clean_gate_report, runtime_config_for
) -> None:
    factory = RecordingTraceFactory()
    evaluator = build_judge_evaluator(
        FakeStructuredProvider(responses=[verdict()]),
        planner_case,
        runtime=runtime_config_for("planner"),
        secrets=("sk-abcdefghijklmnop",),
        gate_lookup=lambda output: clean_gate_report,
        trace_factory=factory,
    )

    await evaluator(
        FakeRun(outputs=clean_target_output.model_dump(mode="json")),
        FakeExampleRow({"inputs": {"case_id": planner_case.case_id}}),
    )

    assert len(factory.calls) == 1
    traced = factory.calls[0]
    assert traced["name"] == JUDGE_PROMPT_ID
    assert traced["run_type"] == "llm"
    assert traced["metadata"]["prompt_id"] == JUDGE_PROMPT_ID
    assert traced["metadata"]["rubric_version"] == 1
    assert "sk-abcdefghijklmnop" not in repr(traced)


@pytest.mark.asyncio
async def test_judge_opens_a_session_for_tracker_bound_provider(
    planner_case,
    clean_target_output,
    clean_gate_report,
    runtime_config_for,
    tracker,
) -> None:
    evaluator = build_judge_evaluator(
        TrackerBoundStructuredProvider(tracker, [verdict()]),
        planner_case,
        runtime=runtime_config_for("planner"),
        secrets=(),
        gate_lookup=lambda output: clean_gate_report,
        tracker=tracker,
    )

    result = await evaluator(
        FakeRun(outputs=clean_target_output.model_dump(mode="json")),
        FakeExampleRow({"inputs": {"case_id": planner_case.case_id}}),
    )

    assert {item["key"] for item in result["results"]} >= {
        "judge_quality",
        "judge_status",
    }


@pytest.mark.asyncio
async def test_judge_binds_provider_call_to_the_target_session(
    planner_case, clean_target_output, clean_gate_report, runtime_config_for
) -> None:
    tracker = RecordingSessionTracker()
    evaluator = build_judge_evaluator(
        SessionAwareStructuredProvider(tracker, [verdict()]),
        planner_case,
        runtime=runtime_config_for("planner"),
        secrets=(),
        gate_lookup=lambda output: clean_gate_report,
        tracker=tracker,
    )

    await evaluator(
        FakeRun(outputs=clean_target_output.model_dump(mode="json")),
        FakeExampleRow({"inputs": {"case_id": planner_case.case_id}}),
    )

    assert tracker.calls == [
        (
            clean_target_output.session_id,
            planner_case.state.original_question,
        )
    ]
    assert tracker.active is False


@pytest.mark.asyncio
async def test_the_feedback_payload_matches_the_local_artifact_identity(
    planner_case, clean_target_output, clean_gate_report, runtime_config_for
) -> None:
    """LangSmith feedback and the local JSON must name the same evaluator."""
    runtime = runtime_config_for("planner")
    evaluator = build_judge_evaluator(
        FakeStructuredProvider(responses=[verdict(0.9)]),
        planner_case,
        runtime=runtime,
        secrets=(),
        gate_lookup=lambda output: clean_gate_report,
    )

    result = await evaluator(
        FakeRun(outputs=clean_target_output.model_dump(mode="json")),
        FakeExampleRow({"inputs": {"case_id": planner_case.case_id}}),
    )

    entries = {item["key"]: item for item in result["results"]}
    payload = entries["judge_quality"]
    metadata = judge_evaluator_metadata(runtime)

    assert payload["score"] == pytest.approx(0.9)
    assert payload["comment"]
    assert payload["metadata"]["prompt_id"] == metadata["prompt_id"]
    assert (
        payload["metadata"]["prompt_fingerprint"]
        == metadata["prompt_fingerprint"]
    )
    assert (
        payload["metadata"]["judge_configuration_fingerprint"]
        == metadata["judge_configuration_fingerprint"]
    )


@pytest.mark.asyncio
async def test_each_common_dimension_is_reported_as_its_own_feedback_key(
    planner_case, clean_target_output, clean_gate_report, runtime_config_for
) -> None:
    evaluator = build_judge_evaluator(
        FakeStructuredProvider(responses=[verdict()]),
        planner_case,
        runtime=runtime_config_for("planner"),
        secrets=(),
        gate_lookup=lambda output: clean_gate_report,
    )

    result = await evaluator(
        FakeRun(outputs=clean_target_output.model_dump(mode="json")),
        FakeExampleRow({"inputs": {"case_id": planner_case.case_id}}),
    )

    keys = {item["key"] for item in result["results"]}
    for dimension in COMMON_DIMENSION_WEIGHTS:
        assert f"judge:{dimension}" in keys
    assert "judge:decomposition_quality" in keys


@pytest.mark.asyncio
async def test_judge_not_run_is_reported_without_a_score(
    planner_case, failed_target_output, failing_gate_report, runtime_config_for
) -> None:
    evaluator = build_judge_evaluator(
        FakeStructuredProvider(responses=[]),
        planner_case,
        runtime=runtime_config_for("planner"),
        secrets=(),
        gate_lookup=lambda output: failing_gate_report,
    )

    result = await evaluator(
        FakeRun(outputs=failed_target_output.model_dump(mode="json")),
        FakeExampleRow({"inputs": {"case_id": planner_case.case_id}}),
    )

    entries = {item["key"]: item for item in result["results"]}
    assert "judge_quality" not in entries
    assert entries["judge_status"]["value"] == "judge_not_run"
    assert entries["judge_status"]["comment"] == "no_evaluable_output"


@pytest.mark.asyncio
async def test_a_validation_error_does_not_leak_the_raw_field_value(
    planner_case, clean_target_output, runtime_config_for
) -> None:
    """A ``ValidationError``'s ``str(error)`` embeds pydantic's raw,
    unredacted ``input_value`` for the failing field. The not-run comment
    must be a static reason, never that raw text.
    """
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    bad_outputs = clean_target_output.model_dump(mode="json")
    bad_outputs["case_version"] = secret
    evaluator = build_judge_evaluator(
        FakeStructuredProvider(responses=[]),
        planner_case,
        runtime=runtime_config_for("planner"),
        secrets=(),
        gate_lookup=lambda output: None,
    )

    result = await evaluator(
        FakeRun(outputs=bad_outputs),
        FakeExampleRow({"inputs": {"case_id": planner_case.case_id}}),
    )

    entries = {item["key"]: item for item in result["results"]}
    assert entries["judge_status"]["value"] == "judge_not_run"
    assert entries["judge_status"]["comment"] == "target_output_invalid"
    assert secret not in repr(result)


@pytest.mark.asyncio
async def test_judge_not_run_entry_carries_evaluator_metadata(
    planner_case, failed_target_output, failing_gate_report, runtime_config_for
) -> None:
    """A ``judge_not_run`` row must be traceable to the exact evaluator
    definition, the same way a scored ``judge_quality`` row already is.
    """
    runtime = runtime_config_for("planner")
    evaluator = build_judge_evaluator(
        FakeStructuredProvider(responses=[]),
        planner_case,
        runtime=runtime,
        secrets=(),
        gate_lookup=lambda output: failing_gate_report,
    )

    result = await evaluator(
        FakeRun(outputs=failed_target_output.model_dump(mode="json")),
        FakeExampleRow({"inputs": {"case_id": planner_case.case_id}}),
    )

    entries = {item["key"]: item for item in result["results"]}
    expected = judge_evaluator_metadata(runtime)
    metadata = entries["judge_status"]["metadata"]
    assert metadata["prompt_id"] == expected["prompt_id"]
    assert metadata["prompt_fingerprint"] == expected["prompt_fingerprint"]
    assert (
        metadata["judge_configuration_fingerprint"]
        == expected["judge_configuration_fingerprint"]
    )


def test_the_feedback_payload_round_trips_the_typed_feedback(
    judge_feedback,
) -> None:
    payload = judge_feedback_payload(judge_feedback)

    assert payload["prompt_id"] == judge_feedback.prompt_id
    assert payload["rubric_version"] == judge_feedback.rubric_version
    assert payload["prompt_fingerprint"] == judge_feedback.prompt_fingerprint
    assert "sk-" not in repr(payload)


@pytest.mark.asyncio
async def test_the_traceable_judge_captures_the_run_tree_url(
    planner_case, clean_target_output, clean_gate_report, runtime_config_for
) -> None:
    """The evaluator's traced judge callback accepts the installed
    ``run_tree`` injection and retains the URL it directly exposes."""
    url = "https://smith.langchain.com/o/x/r/judge-trace-1"
    evaluator = build_judge_evaluator(
        FakeStructuredProvider(responses=[verdict()]),
        planner_case,
        runtime=runtime_config_for("planner"),
        secrets=(),
        gate_lookup=lambda output: clean_gate_report,
        trace_factory=RunTreeInjectingTraceFactory(url),
    )

    result = await evaluator(
        FakeRun(outputs=clean_target_output.model_dump(mode="json")),
        FakeExampleRow({"inputs": {"case_id": planner_case.case_id}}),
    )

    entries = {item["key"]: item for item in result["results"]}
    assert entries["judge_status"]["value"] == "scored"
    assert (
        entries["judge_quality"]["metadata"]["evaluator_trace_url"] == url
    )


@pytest.mark.asyncio
async def test_an_unsupported_source_url_stays_none_and_is_never_derived(
    planner_case, clean_target_output, clean_gate_report, runtime_config_for
) -> None:
    """A trace URL must never be recycled into a source URL: the evaluator
    integration does not expose one, so the field stays explicitly absent."""
    url = "https://smith.langchain.com/o/x/r/judge-trace-2"
    evaluator = build_judge_evaluator(
        FakeStructuredProvider(responses=[verdict()]),
        planner_case,
        runtime=runtime_config_for("planner"),
        secrets=(),
        gate_lookup=lambda output: clean_gate_report,
        trace_factory=RunTreeInjectingTraceFactory(url),
    )

    result = await evaluator(
        FakeRun(outputs=clean_target_output.model_dump(mode="json")),
        FakeExampleRow({"inputs": {"case_id": planner_case.case_id}}),
    )

    entries = {item["key"]: item for item in result["results"]}
    metadata = entries["judge_quality"]["metadata"]
    assert metadata["evaluator_trace_url"] == url
    assert "evaluator_source_url" not in metadata


@pytest.mark.asyncio
async def test_a_directly_supplied_evaluator_source_url_is_preserved(
    planner_case, clean_target_output, clean_gate_report, runtime_config_for
) -> None:
    """Only a URL the evaluator integration directly supplies is retained;
    nothing is derived from inputs, prompts, traces, or exceptions."""
    source_url = "https://smith.langchain.com/o/x/evaluators/judge-1"
    evaluator = build_judge_evaluator(
        FakeStructuredProvider(responses=[verdict()]),
        planner_case,
        runtime=runtime_config_for("planner"),
        secrets=(),
        gate_lookup=lambda output: clean_gate_report,
        evaluator_source_url=source_url,
    )

    result = await evaluator(
        FakeRun(outputs=clean_target_output.model_dump(mode="json")),
        FakeExampleRow({"inputs": {"case_id": planner_case.case_id}}),
    )

    entries = {item["key"]: item for item in result["results"]}
    assert (
        entries["judge_quality"]["metadata"]["evaluator_source_url"]
        == source_url
    )


@pytest.mark.asyncio
async def test_a_judge_output_limit_failure_reports_a_typed_diagnostic(
    planner_case, clean_target_output, clean_gate_report, runtime_config_for
) -> None:
    telemetry = ProviderResponseTelemetry(
        finish_reason_category="length",
        configured_max_tokens=4096,
        usage=TokenUsage(input_tokens=100, output_tokens=4096),
        request_attempt=2,
    )
    evaluator = build_judge_evaluator(
        FakeStructuredProvider(
            responses=[ProviderOutputLimitError(telemetry)]
        ),
        planner_case,
        runtime=runtime_config_for("planner"),
        secrets=(),
        gate_lookup=lambda output: clean_gate_report,
    )

    result = await evaluator(
        FakeRun(outputs=clean_target_output.model_dump(mode="json")),
        FakeExampleRow({"inputs": {"case_id": planner_case.case_id}}),
    )

    entries = {item["key"]: item for item in result["results"]}
    assert "judge_quality" not in entries
    status = entries["judge_status"]
    assert status["value"] == "judge_not_run"
    assert status["comment"] == "judge_output_limit"
    assert status["metadata"]["judge_diagnostics"] == [
        {"kind": "output_limit", "attempt": 2, "field_paths": []}
    ]


@pytest.mark.asyncio
async def test_a_schema_judge_failure_reports_bounded_diagnostics(
    planner_case, clean_target_output, clean_gate_report, runtime_config_for
) -> None:
    error = StructuredOutputError(
        "schema failed after one repair",
        diagnostics=[
            StructuredValidationDiagnostic(
                attempt=1,
                field_paths=("scores.completeness",),
            )
        ],
    )
    evaluator = build_judge_evaluator(
        FakeStructuredProvider(responses=[error]),
        planner_case,
        runtime=runtime_config_for("planner"),
        secrets=(),
        gate_lookup=lambda output: clean_gate_report,
    )

    result = await evaluator(
        FakeRun(outputs=clean_target_output.model_dump(mode="json")),
        FakeExampleRow({"inputs": {"case_id": planner_case.case_id}}),
    )

    entries = {item["key"]: item for item in result["results"]}
    status = entries["judge_status"]
    assert status["value"] == "judge_not_run"
    assert status["comment"] == "judge_schema_failure"
    assert status["metadata"]["judge_diagnostics"] == [
        {
            "kind": "schema_output",
            "attempt": 1,
            "field_paths": ["scores.completeness"],
        }
    ]
    assert "schema failed after one repair" not in repr(result)


@pytest.mark.asyncio
async def test_a_generic_judge_provider_failure_in_a_cause_chain_is_reported(
    planner_case, clean_target_output, clean_gate_report, runtime_config_for
) -> None:
    try:
        try:
            raise OpenAIProviderError("the model provider is unavailable")
        except Exception as cause:
            raise RuntimeError("wrapped by the harness") from cause
    except Exception as error:
        evaluator = build_judge_evaluator(
            FakeStructuredProvider(responses=[error]),
            planner_case,
            runtime=runtime_config_for("planner"),
            secrets=(),
            gate_lookup=lambda output: clean_gate_report,
        )

    result = await evaluator(
        FakeRun(outputs=clean_target_output.model_dump(mode="json")),
        FakeExampleRow({"inputs": {"case_id": planner_case.case_id}}),
    )

    entries = {item["key"]: item for item in result["results"]}
    status = entries["judge_status"]
    assert status["value"] == "judge_not_run"
    assert status["comment"] == "judge_provider_failure"
    assert "judge_diagnostics" not in status["metadata"]
    assert "evaluator_trace_url" not in status["metadata"]
    assert "the model provider is unavailable" not in repr(result)


@pytest.mark.asyncio
async def test_concurrent_judge_invocations_keep_their_own_trace_urls(
    planner_case, clean_target_output, clean_gate_report, runtime_config_for
) -> None:
    """One evaluator serves every repetition row of its case under
    ``max_concurrency > 1``; each concurrent invocation must retain the
    URL of its own run tree, never another row's. A shared field on the
    evaluator would race: the second invocation's reset/capture would wipe
    the first's captured URL before the first reads it."""
    url_a = "https://smith.langchain.com/o/x/r/judge-concurrent-a"
    url_b = "https://smith.langchain.com/o/x/r/judge-concurrent-b"
    gate = asyncio.Event()
    entered = asyncio.Event()
    provider = _GatedStructuredProvider(
        [verdict(0.7), verdict(0.8)], gate=gate, entered=entered
    )
    evaluator = build_judge_evaluator(
        provider,
        planner_case,
        runtime=runtime_config_for("planner"),
        secrets=(),
        gate_lookup=lambda output: clean_gate_report,
        trace_factory=_SequentialRunTreeFactory([url_a, url_b]),
    )

    row = FakeRun(outputs=clean_target_output.model_dump(mode="json"))
    example = FakeExampleRow({"inputs": {"case_id": planner_case.case_id}})

    first = asyncio.create_task(evaluator(row, example))
    # The first invocation has captured its URL and is parked at the gate.
    await entered.wait()
    second = asyncio.create_task(evaluator(row, example))
    gate.set()
    result_a, result_b = await asyncio.gather(first, second)

    def _trace_url(result) -> str:
        entries = {item["key"]: item for item in result["results"]}
        assert entries["judge_status"]["value"] == "scored"
        return entries["judge_quality"]["metadata"]["evaluator_trace_url"]

    assert _trace_url(result_a) == url_a
    assert _trace_url(result_b) == url_b
