"""The LangSmith target: one agent run inside the project's own session trace.

``build_target`` wires the production-parity factory chain (Task 6), the
controlled/live dependency bundles (Tasks 7-8), and the case registry
(Task 9) into a single callable LangSmith's ``aevaluate`` invokes once per
repetition. The target never raises: every failure path is captured as a
typed ``TargetOutput`` with ``completed=False``, because an exception
escaping the returned callable would abort every remaining repetition in
the experiment.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, JsonValue, ValidationError

from deep_research.evaluation.cases import UnknownCaseError, case_by_identity
from deep_research.evaluation.config import (
    EvaluationRuntimeConfig,
    contains_secret,
    redact_secrets,
)
from deep_research.evaluation.dependencies import SCENARIOS, DependencyBundle
from deep_research.evaluation.factory import (
    AgentConstructionError,
    build_evaluation_agent,
    evaluation_session_id,
)
from deep_research.evaluation.models import (
    AgentName,
    EvaluationCase,
    EvaluationFailure,
    EvaluationTier,
    EvidenceContext,
    FailureStage,
    ReActSummary,
    TargetOutput,
    TrajectoryStep,
)
from deep_research.observability import ToolMetric, Tracker
from deep_research.providers import OpenAIProviderError
from deep_research.tools.base import ToolExecutionError
from deep_research.utils.config import ConfigSettings

TRACE_TAG = "evaluation"

# Mirrors ``deep_research.evaluation.dependencies._TOOL_SERVICES``; the same
# small mapping is duplicated here the same way that module duplicates
# ``memory.entries._RESERVED_METADATA_KEYS`` — reading a private symbol
# across modules would couple this task to dependencies.py's internals more
# tightly than calling its public functions does.
_TOOL_SERVICES: Mapping[str, str] = {
    "web_search": "tavily",
    "web_scraper": "http",
    "document_reader": "http",
    "query_memory": "memory",
    "save_to_memory": "memory",
    "write_document": "documents",
}

TrackerFactory = Callable[[], Tracker]
DependencyFactory = Callable[..., DependencyBundle]
ProviderFactory = Callable[[], Any]
Target = Callable[[dict[str, JsonValue]], Awaitable[dict[str, JsonValue]]]


class TargetRunError(RuntimeError):
    """A last-resort invariant failed while building the artifact payload.

    Raised only by the target's own secret-leak safety net, never by an
    agent run itself, and always caught before it can escape the target.
    """


class RepetitionCounter:
    """Per-case sequential repetition numbering, starting at 1.

    Exact numbering requires sequential execution: two repetitions of the
    same case racing each other could both observe the same "next" value.
    ``max_concurrency`` is validated at construction so that precondition
    is a hard failure, not a comment someone might miss.
    """

    def __init__(self, *, max_concurrency: int) -> None:
        if max_concurrency != 1:
            raise ValueError(
                "max_concurrency must be 1: per-case repetition counting "
                "is only exact under sequential execution, got "
                f"{max_concurrency!r}"
            )
        self._max_concurrency = max_concurrency
        self._counts: dict[str, int] = {}

    def next(self, case_id: str) -> int:
        value = self._counts.get(case_id, 0) + 1
        self._counts[case_id] = value
        return value


def trace_tags(
    runtime: EvaluationRuntimeConfig, case: EvaluationCase, repetition: int
) -> list[str]:
    """The ten tags every repetition's trace carries, in a fixed order."""
    return [
        TRACE_TAG,
        f"agent:{case.agent_name}",
        f"tier:{case.tier}",
        f"case:{case.case_id}",
        f"case_version:{case.version}",
        f"repetition:{repetition}",
        f"experiment:{runtime.experiment_name}",
        f"git:{runtime.git.short_sha}",
        f"target_model:{runtime.target_model}",
        f"rubric_version:{runtime.rubric_version}",
    ]


def correlation_metadata(
    runtime: EvaluationRuntimeConfig,
    case: EvaluationCase,
    *,
    repetition: int,
    session_id: str,
) -> dict[str, JsonValue]:
    """Metadata that links this repetition's trace back to the experiment.

    If LangSmith's SDK creates a sibling root run for the target instead of
    nesting it under the evaluator's run, this is what makes the two trees
    findable from each other.
    """
    return {
        "experiment_name": runtime.experiment_name,
        "case_id": case.case_id,
        "case_version": case.version,
        "repetition": repetition,
        "session_id": session_id,
    }


def _json_safe(value: Any) -> JsonValue:
    """Recursively turn model/dict/list values into plain JSON values."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _classify_failure(error: BaseException) -> tuple[FailureStage, str]:
    """Classify a run-time exception, walking the ``__cause__`` chain.

    An agent may wrap the true failure in its own domain exception (for
    example ``PlanningError`` chains the ``OpenAIProviderError`` it caught
    via ``raise ... from error``); the classification must see through that
    wrapping to still report the real stage.
    """
    candidate: BaseException | None = error
    seen: set[int] = set()
    while candidate is not None and id(candidate) not in seen:
        seen.add(id(candidate))
        if isinstance(candidate, OpenAIProviderError):
            return "provider", "provider_failure"
        if isinstance(candidate, ToolExecutionError):
            return "tool", "tool_failure"
        if isinstance(candidate, ValidationError):
            return "validation", "validation_failure"
        candidate = candidate.__cause__
    return "unhandled", "unhandled_failure"


def _observed_real_services(
    tracker: Tracker, available_services: Sequence[str]
) -> list[str]:
    """Real services this repetition actually reached, from tool spans.

    Restricted to ``available_services`` (empty for a controlled bundle, the
    agent's declared services for a live one): a controlled bundle's tools
    still emit ``ToolMetric`` records even though they only ever touch
    scripted doubles, so without this restriction a controlled run would
    look like it reached a real service it never could.
    """
    observed: list[str] = []
    for metric in tracker.metrics:
        if not isinstance(metric, ToolMetric) or not metric.success:
            continue
        service = _TOOL_SERVICES.get(metric.tool_name)
        if service and service in available_services and service not in observed:
            observed.append(service)
    return observed


def _minimal_output(
    *,
    case_id: str,
    case_version: int,
    agent_name: AgentName,
    tier: EvaluationTier,
    repetition: int,
    session_id: str,
    experiment_name: str,
    target_model: str,
    target_reasoning_effort: str,
    stage: FailureStage,
    reason: str,
    message: str,
    exception_type: str | None,
    trace_url: str | None = None,
) -> TargetOutput:
    return TargetOutput(
        case_id=case_id,
        case_version=case_version,
        agent_name=agent_name,
        tier=tier,
        repetition=repetition,
        session_id=session_id,
        experiment_name=experiment_name,
        trace_url=trace_url,
        completed=False,
        failure=EvaluationFailure(
            stage=stage,
            reason=reason,
            message=message,
            exception_type=exception_type,
        ),
        result=None,
        target_model_requested=target_model,
        target_reasoning_effort=target_reasoning_effort,  # type: ignore[arg-type]
    )


def _finish(
    output: TargetOutput, secrets: Sequence[str]
) -> dict[str, JsonValue]:
    """Redact the finished dump, verify it, and return it. Never raises.

    Belt-and-braces: ``redact_secrets`` is applied, then ``contains_secret``
    re-checks the result. If a secret somehow survives redaction, the
    payload is replaced with a minimal, secret-free failure record rather
    than letting the leak — or an exception — escape.
    """
    payload = output.model_dump(mode="json")
    redacted = redact_secrets(payload, secrets)
    try:
        leaked = contains_secret(redacted, secrets)
        if leaked:
            raise TargetRunError(
                "a known secret value survived redaction at: "
                + ", ".join(leaked)
            )
    except TargetRunError:
        pass
    else:
        return redacted  # type: ignore[return-value]

    safe = TargetOutput(
        schema_version=output.schema_version,
        case_id=output.case_id,
        case_version=output.case_version,
        agent_name=output.agent_name,
        tier=output.tier,
        repetition=output.repetition,
        session_id=output.session_id,
        experiment_name=output.experiment_name,
        completed=False,
        failure=EvaluationFailure(
            stage="artifact",
            reason="secret_leak_detected",
            message="a known secret value could not be safely redacted",
        ),
        result=None,
        target_model_requested=output.target_model_requested,
        target_reasoning_effort=output.target_reasoning_effort,
    )
    return safe.model_dump(mode="json")


def build_target(
    runtime: EvaluationRuntimeConfig,
    settings: ConfigSettings,
    *,
    tracker_factory: TrackerFactory,
    dependency_factory: DependencyFactory,
    provider_factory: ProviderFactory,
    counter: RepetitionCounter,
    secrets: Sequence[str],
    root: Path,
) -> Target:
    """Build the callable LangSmith's ``aevaluate`` invokes once per repetition.

    The returned callable never raises. Every failure path — an unknown
    case, a dependency-bundle setup failure, an ``AgentConstructionError``,
    or any exception during the run itself — is captured as a typed,
    redacted ``TargetOutput`` with ``completed=False`` instead.
    """

    async def target(inputs: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        try:
            return await _run_one_repetition(inputs)
        except Exception as error:  # pragma: no cover - defense in depth
            # Every step below has its own try/except; this is the ultimate
            # safety net so a bug in this module can never abort the
            # remaining repetitions of an experiment.
            case_id = str(inputs.get("case_id", "unknown"))
            case_version = _safe_int(inputs.get("case_version"), default=1)
            agent_name = _safe_agent_name(inputs.get("agent"))
            tier = _safe_tier(inputs.get("tier"))
            output = _minimal_output(
                case_id=case_id,
                case_version=case_version,
                agent_name=agent_name,
                tier=tier,
                repetition=1,
                session_id=f"evaluation-{runtime.experiment_name}-{case_id}",
                experiment_name=runtime.experiment_name,
                target_model=runtime.target_model,
                target_reasoning_effort=runtime.target_reasoning_effort,
                stage="unhandled",
                reason="unhandled_failure",
                message=str(error) or type(error).__name__,
                exception_type=type(error).__name__,
            )
            return _finish(output, secrets)

    async def _run_one_repetition(
        inputs: Mapping[str, JsonValue],
    ) -> dict[str, JsonValue]:
        case_id = str(inputs["case_id"])
        case_version = _safe_int(inputs["case_version"], default=1)

        # Step 1: resolve the case identity. An unknown case is a typed
        # setup failure, never a raised exception.
        try:
            case = case_by_identity(case_id, case_version)
        except UnknownCaseError as error:
            output = _minimal_output(
                case_id=case_id,
                case_version=case_version,
                agent_name=_safe_agent_name(inputs.get("agent")),
                tier=_safe_tier(inputs.get("tier")),
                repetition=1,
                session_id=f"evaluation-{runtime.experiment_name}-{case_id}",
                experiment_name=runtime.experiment_name,
                target_model=runtime.target_model,
                target_reasoning_effort=runtime.target_reasoning_effort,
                stage="setup",
                reason="unknown_case",
                message=redact_secrets(  # type: ignore[arg-type]
                    str(error) or type(error).__name__, secrets
                ),
                exception_type=type(error).__name__,
            )
            return _finish(output, secrets)

        # Step 2-3: repetition number and its session id.
        repetition = counter.next(case_id)
        session_id = evaluation_session_id(
            runtime, case_id=case_id, repetition=repetition
        )
        tracker = tracker_factory()

        def failure(
            *, stage: FailureStage, reason: str, error: BaseException,
            trace_url: str | None = None,
        ) -> dict[str, JsonValue]:
            output = _minimal_output(
                case_id=case.case_id,
                case_version=case.version,
                agent_name=case.agent_name,
                tier=case.tier,
                repetition=repetition,
                session_id=session_id,
                experiment_name=runtime.experiment_name,
                target_model=runtime.target_model,
                target_reasoning_effort=runtime.target_reasoning_effort,
                stage=stage,
                reason=reason,
                message=redact_secrets(  # type: ignore[arg-type]
                    str(error) or type(error).__name__, secrets
                ),
                exception_type=type(error).__name__,
                trace_url=trace_url,
            )
            return _finish(output, secrets)

        # Step 4: build the dependency bundle, controlled or live.
        try:
            bundle = dependency_factory(
                runtime,
                case,
                tracker=tracker,
                settings=settings,
                root=root / case_id / f"r{repetition}",
                repetition=repetition,
            )
        except Exception as error:
            reason = getattr(error, "reason", "dependency_setup_failed")
            return failure(stage="setup", reason=reason, error=error)

        # Step 5: build the provider, then the production-parity agent.
        try:
            provider = provider_factory()
            build = build_evaluation_agent(
                runtime,
                bundle.settings,
                tracker=tracker,
                provider=provider,
                tools=bundle.tools,
                session_id=session_id,
                reputation=bundle.reputation,
            )
        except AgentConstructionError as error:
            return failure(
                stage="construction", reason=error.reason, error=error
            )
        except Exception as error:
            return failure(
                stage="construction",
                reason="construction_failed",
                error=error,
            )

        agent = build.agent

        # Step 6: a genuine deep copy of state, session id overridden.
        state = case.fresh_state().model_copy(
            update={"session_id": session_id}
        )

        # Step 7: run inside the project's own session span.
        span = None
        try:
            async with tracker.session_span(
                session_id, state.original_question
            ) as active_span:
                span = active_span
                run = await agent.run(state)
                span.set_outputs(
                    {
                        "agent_name": case.agent_name,
                        "case_id": case.case_id,
                        "repetition": repetition,
                        "completed": run.result is not None,
                        "evaluation_tags": trace_tags(
                            runtime, case, repetition
                        ),
                        **correlation_metadata(
                            runtime,
                            case,
                            repetition=repetition,
                            session_id=session_id,
                        ),
                    }
                )
        except Exception as error:
            # Step 8: classify the exception; never store an unredacted
            # exception message.
            stage, reason = _classify_failure(error)
            trace_url = span.trace_url if span is not None else None
            return failure(
                stage=stage, reason=reason, error=error, trace_url=trace_url
            )

        trace_url = span.trace_url if span is not None else None

        # Step 9-10: build the typed output and redact it before returning.
        try:
            output = _success_output(
                runtime=runtime,
                case=case,
                repetition=repetition,
                session_id=session_id,
                run=run,
                bundle=bundle,
                tracker=tracker,
                provider=provider,
                trace_url=trace_url,
            )
        except Exception as error:
            return failure(
                stage="artifact", reason="artifact_build_failed", error=error
            )
        return _finish(output, secrets)

    return target


def _success_output(
    *,
    runtime: EvaluationRuntimeConfig,
    case: EvaluationCase,
    repetition: int,
    session_id: str,
    run: Any,
    bundle: DependencyBundle,
    tracker: Tracker,
    provider: Any,
    trace_url: str | None,
) -> TargetOutput:
    """Build the typed output of one successful run. Not yet redacted."""
    script = SCENARIOS.get(case.dependency_scenario)
    limit = bundle.settings.agents.observation_summary_chars

    ledger = bundle.recorder.ledger()
    observed_services = _observed_real_services(
        tracker, bundle.available_services
    )
    dependencies = ledger.model_copy(
        update={"real_services_used": observed_services}
    )

    trajectory = [
        TrajectoryStep(
            iteration=step.iteration,
            thought=step.thought[:limit],
            tool_name=step.tool_name,
            succeeded=(
                None if step.observation is None else step.observation.success
            ),
            observation_summary=(
                step.observation.summary[:limit]
                if step.observation is not None
                else ""
            ),
        )
        for step in run.react.steps
    ]

    return TargetOutput(
        case_id=case.case_id,
        case_version=case.version,
        agent_name=case.agent_name,
        tier=case.tier,
        repetition=repetition,
        session_id=session_id,
        experiment_name=runtime.experiment_name,
        trace_url=trace_url,
        completed=run.result is not None,
        failure=None,
        result=run.result.model_dump(mode="json") if run.result else None,
        state_update=_json_safe(dict(run.state_update)),  # type: ignore[arg-type]
        errors=[_json_safe(error) for error in run.errors],  # type: ignore[misc]
        tracker_errors=[_json_safe(error) for error in tracker.errors],  # type: ignore[misc]
        react=ReActSummary(
            iterations=run.react.iterations,
            tool_calls=run.react.tool_calls,
            stop_reason=run.react.stop_reason,
            max_iterations=case.expectations.max_iterations,
            tool_budget=case.expectations.max_tool_calls,
        ),
        dependencies=dependencies,
        evidence=EvidenceContext(
            sources=[
                source.model_dump(mode="json")
                for source in case.state.evaluated_sources
            ],
            findings=[
                finding.model_dump(mode="json")
                for finding in case.state.raw_findings
            ],
            claims=[
                claim.model_dump(mode="json")
                for claim in case.state.verified_claims
            ],
            scripted_search_urls=(
                list(script.scripted_search_urls) if script is not None else []
            ),
        ),
        trajectory=trajectory,
        target_model_requested=runtime.target_model,
        target_model_returned=getattr(provider, "last_model_returned", None),
        target_reasoning_effort=runtime.target_reasoning_effort,
    )


def _safe_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_agent_name(value: Any) -> AgentName:
    if value in (
        "planner",
        "researcher",
        "source_evaluator",
        "fact_checker",
        "synthesizer",
        "critic",
    ):
        return value  # type: ignore[return-value]
    return "planner"


def _safe_tier(value: Any) -> EvaluationTier:
    if value in ("controlled", "live"):
        return value  # type: ignore[return-value]
    return "controlled"
