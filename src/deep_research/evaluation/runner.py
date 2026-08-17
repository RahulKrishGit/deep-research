"""Preflight: fail fast, cheaply, and safely before any experiment starts.

``preflight`` runs nine checks in a fixed order, cheapest and safest first,
so a broken local input never reaches a remote call and a remote call
never reaches a remote write. Nothing here creates a LangSmith experiment;
that is Task 23's job, once ``preflight`` has passed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, get_args

from langsmith.evaluation import aevaluate
from pydantic import ValidationError

from deep_research.agents.base import StructuredCompleter
from deep_research.agents.errors import AgentConfigurationError
from deep_research.evaluation.cases import (
    CaseRegistryError,
    UnknownCaseError,
    case_by_id,
    validate_registry,
)
from deep_research.evaluation.config import (
    EvaluationRuntimeConfig,
    experiment_metadata,
    known_secret_values,
    resolve_judge_effort,
    resolve_target_effort,
)
from deep_research.evaluation.datasets import DatasetSyncError, synchronize_dataset
from deep_research.evaluation.dependencies import (
    build_controlled_dependencies,
    build_live_dependencies,
    required_credentials,
)
from deep_research.evaluation.evaluators import code_evaluator, evaluate_target
from deep_research.evaluation.factory import evaluation_session_id
from deep_research.evaluation.judging import (
    COMMON_DIMENSION_WEIGHTS,
    JUDGE_PROMPT_ID,
    build_judge_evaluator,
    judge_prompt_fingerprint,
)
from deep_research.evaluation.models import (
    CaseResult,
    EvaluationCase,
    EvaluationFailure,
    EvaluationStatus,
    EvaluationTier,
    ExperimentResult,
    GateReport,
    JudgeFeedback,
    JudgeNotRunReason,
    JudgeScores,
    JudgeVerdict,
    RepetitionResult,
    TargetOutput,
)
from deep_research.evaluation.targets import (
    DependencyFactory,
    RepetitionCounter,
    Target,
    build_target,
)
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.providers import OpenAIChatProvider
from deep_research.runtime.assembly import build_agent
from deep_research.utils.config import ConfigSettings
from deep_research.utils.types import JsonValue

PREFLIGHT_REASONS: tuple[str, ...] = (
    "invalid_registry",
    "unknown_case",
    "missing_credentials",
    "model_unavailable",
    "invalid_reasoning_effort",
    "agent_unbuildable",
    "dataset_unavailable",
    "output_root_unwritable",
    "guards_uninstallable",
)

# Reasons that are local-input errors under the CLI exit-code spec (Task 25):
# a broken local registry or an unknown requested case is the caller's
# mistake, not an environment or remote failure, so both map to exit code 2
# instead of the general failure code 3. Every other ``PreflightError``
# reason -- including the dataset-sync pass-through reason
# "secret_in_dataset", which is deliberately not one of ``PREFLIGHT_REASONS``
# -- maps to 3. Exposed as ``preflight_exit_code`` (a lookup function, not
# data carried on ``PreflightError`` itself) so Task 25's CLI wiring has one
# place to call instead of duplicating this table.
_LOCAL_INPUT_REASONS = frozenset({"invalid_registry", "unknown_case"})


def preflight_exit_code(reason: str) -> int:
    """The CLI exit code Task 25 must use for one ``PreflightError.reason``."""
    return 2 if reason in _LOCAL_INPUT_REASONS else 3


class PreflightError(RuntimeError):
    """A preflight check failed before any dataset write, by ``reason``.

    ``reason`` is one of ``PREFLIGHT_REASONS`` for every check this module
    owns; ``synchronize_dataset`` failures pass their own ``DatasetSyncError
    .reason`` straight through (``"dataset_unavailable"``, already listed,
    or ``"secret_in_dataset"``, which is not, since it is not one of this
    module's own nine checks).
    """

    def __init__(self, reason: str, message: str | None = None) -> None:
        super().__init__(message or f"preflight failed: {reason}")
        self.reason = reason


async def verify_model_access(client: Any, model_ids: Sequence[str]) -> None:
    """Request each model id once, in order; never fall back to another.

    Raises ``PreflightError("model_unavailable", ...)`` naming only the
    identifier that failed -- never a substitute that happens to be
    available -- and stops at the first failure, so no later model in
    ``model_ids`` is requested once one has already failed.
    """
    for model_id in model_ids:
        try:
            await client.models.retrieve(model_id)
        except Exception as error:
            raise PreflightError(
                "model_unavailable",
                f"model {model_id!r} is not accessible with the "
                "configured credentials",
            ) from error


def _validate_case_identities(cases: Sequence[EvaluationCase]) -> None:
    """Reject duplicate identities or one case id at conflicting versions.

    Mirrors ``datasets.py``'s private ``_validate_sync_case_identities``:
    the per-agent case counts are a whole-registry property already
    enforced by the bare ``validate_registry()`` call above this, so only
    the two identity rules apply to a preflight run's own (possibly
    one-agent, one-tier) case subset.
    """
    seen: dict[tuple[str, int], int] = {}
    versions: dict[str, set[int]] = {}
    for case in cases:
        seen[case.identity] = seen.get(case.identity, 0) + 1
        versions.setdefault(case.case_id, set()).add(case.version)
    duplicates = sorted(key[0] for key, count in seen.items() if count > 1)
    if duplicates:
        raise CaseRegistryError(
            f"duplicate case identities: {', '.join(duplicates)}"
        )
    conflicting = sorted(
        case_id for case_id, found in versions.items() if len(found) > 1
    )
    if conflicting:
        raise CaseRegistryError(
            f"conflicting versions for case ids: {', '.join(conflicting)}"
        )


async def preflight(
    settings: ConfigSettings,
    runtime: EvaluationRuntimeConfig,
    *,
    cases: Sequence[EvaluationCase],
    environ: Mapping[str, str],
    langsmith_client: Any,
    openai_client: Any,
    root: Path,
) -> None:
    """Run the nine preflight checks in order; raise on the first failure.

    Each check is cheaper and safer than the next: local, in-process
    checks (registry, case lookup, reasoning efforts, credential presence)
    come before any client call, and the one client call that only reads
    (model access) comes before every client call that could write
    (``synchronize_dataset``). Nothing before the last step ever touches a
    remote dataset.
    """
    # 1. The registry as a whole is sound, and this run's own case subset
    # has no duplicate or conflicting-version identities.
    try:
        validate_registry()
        _validate_case_identities(cases)
    except CaseRegistryError as error:
        raise PreflightError("invalid_registry", str(error)) from error

    # 2. A specifically requested case actually exists.
    if runtime.case_id is not None:
        try:
            smoke_case = case_by_id(
                runtime.agent_name, runtime.tier, runtime.case_id
            )
        except UnknownCaseError as error:
            raise PreflightError("unknown_case", str(error)) from error
    else:
        if not cases:
            raise PreflightError(
                "unknown_case", "no cases were supplied to preflight"
            )
        smoke_case = cases[0]

    # 3. Reasoning efforts re-resolve without error against the current
    # settings file -- catches a hand-edited config between the runtime
    # config being built and preflight running.
    try:
        resolve_target_effort(settings.evaluation, runtime.agent_name, override=None)
        resolve_judge_effort(settings.evaluation, override=None)
    except ValueError as error:
        raise PreflightError("invalid_reasoning_effort", str(error)) from error

    # 4. Every credential this run will actually need is present and
    # non-blank. OpenAI and LangSmith are required for every tier (model
    # access below, dataset sync at the end). For a live-tier run,
    # ``required_credentials`` also names ``TAVILY_API_KEY`` for any agent
    # whose declared tools reach it -- a controlled bundle never
    # constructs a real Tavily client (it always injects the scripted
    # search double), so the controlled tier only needs the fixed pair.
    # Checking the live tier's full credential set here, before step 5's
    # real network call, means a missing Tavily key is caught as
    # ``missing_credentials`` up front instead of surfacing later, at
    # step 7, as the less-specific ``guards_uninstallable``.
    required = (
        required_credentials(runtime.agent_name)
        if runtime.tier == "live"
        else ("OPENAI_API_KEY", "LANGSMITH_API_KEY")
    )
    missing = [
        variable
        for variable in required
        if not environ.get(variable, "").strip()
    ]
    if missing:
        raise PreflightError(
            "missing_credentials",
            "missing required credentials: " + ", ".join(missing),
        )

    # 5. Every model this run will call is actually reachable. Never a
    # substitute: a missing model fails preflight by name, full stop.
    model_ids = [runtime.target_model, runtime.judge_model]
    if runtime.tier == "live":
        model_ids.append(runtime.embedding_model)
    await verify_model_access(openai_client, model_ids)

    # 6. The output root can be created and actually written to.
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".preflight-write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as error:
        raise PreflightError("output_root_unwritable", str(error)) from error

    # 7. The controlled or live dependency bundle builds once, for one
    # representative case -- a smoke test of the guard wiring itself, kept
    # under a dedicated subdirectory so it never collides with a real
    # repetition's own output paths.
    tracker = Tracker(LangSmithRuntimeConfig(tracing_enabled=False))
    smoke_root = root / "_preflight"
    try:
        if runtime.tier == "controlled":
            bundle = build_controlled_dependencies(
                runtime,
                smoke_case,
                tracker=tracker,
                settings=settings,
                root=smoke_root,
            )
        else:
            bundle = build_live_dependencies(
                runtime,
                smoke_case,
                tracker=tracker,
                settings=settings,
                root=smoke_root,
                environ=environ,
            )
    except Exception as error:
        raise PreflightError("guards_uninstallable", str(error)) from error

    # 8. The agent itself builds with those tools -- never deferred to the
    # first real repetition.
    provider = OpenAIChatProvider(settings.llm, tracker, client=openai_client)
    try:
        build_agent(
            runtime.agent_name,
            bundle.settings,
            tracker=tracker,
            provider=provider,
            tools=bundle.tools,
            session_id=evaluation_session_id(
                runtime, case_id=smoke_case.case_id, repetition=1
            ),
            reputation=bundle.reputation,
        )
    except AgentConfigurationError as error:
        raise PreflightError("agent_unbuildable", str(error)) from error

    # 9. The dataset itself is reachable and secret-free. The last check,
    # because it is the only one that can write.
    try:
        synchronize_dataset(
            langsmith_client,
            agent_name=runtime.agent_name,
            tier=runtime.tier,
            dataset_version=runtime.dataset_version,
            rubric_version=runtime.rubric_version,
            cases=cases,
            secrets=known_secret_values(environ),
        )
    except DatasetSyncError as error:
        raise PreflightError(error.reason, str(error)) from error


# --- Task 23: experiment execution, aggregation, and thresholds ------------

DETERMINISTIC_WEIGHT = 0.40
JUDGE_WEIGHT = 0.60

EXPERIMENT_EXIT_CODES: dict[EvaluationStatus, int] = {
    "REVIEW REQUIRED": 0,
    "FAILED": 1,
    "INFRASTRUCTURE FAILURE": 3,
}

EvaluateCallable = Callable[..., Awaitable[Any]]

_JUDGE_NOT_RUN_REASONS = frozenset(get_args(JudgeNotRunReason))


def aggregate_quality(deterministic: float, judge: float) -> float:
    """The spec's fixed composition, computed without intermediate rounding.

    Rendering to two decimals is a display concern and belongs in
    ``reporting``; rounding here would let 0.6499 clear a 0.65 floor.
    """
    return (DETERMINISTIC_WEIGHT * deterministic) + (JUDGE_WEIGHT * judge)


def build_repetition_result(
    output: TargetOutput,
    gates: GateReport,
    deterministic: float | None,
    judge: JudgeFeedback | None,
) -> RepetitionResult:
    """One repetition's typed result. ``aggregate_quality`` is ``None``
    whenever the judge did not score the run or no deterministic score was
    produced -- a repetition with no aggregate score never passes.
    """
    aggregate: float | None = None
    if (
        judge is not None
        and judge.status == "scored"
        and judge.judge_quality is not None
        and deterministic is not None
    ):
        aggregate = aggregate_quality(deterministic, judge.judge_quality)
    return RepetitionResult(
        case_id=output.case_id,
        case_version=output.case_version,
        repetition=output.repetition,
        completed=output.completed,
        gates=gates,
        deterministic_quality=deterministic,
        judge=judge,
        aggregate_quality=aggregate,
        trace_url=output.trace_url,
        errors=[output.failure] if output.failure is not None else [],
    )


def build_case_result(
    case: EvaluationCase | None,
    repetitions: Sequence[RepetitionResult],
    *,
    threshold: float,
    floor: float | None = None,
) -> CaseResult:
    """Aggregate one case's repetitions under the approved thresholds.

    ``threshold`` gates the case's average quality; ``floor`` gates every
    individual repetition's aggregate quality and defaults to ``threshold``
    itself so a caller that only cares about one number (every pure test
    in this module) never has to pass both. ``run_agent_evaluation`` passes
    them independently for the controlled tier, where the spec's
    per-repetition floor and case-average threshold differ; for the live
    tier the two rules collapse onto ``runtime.live_threshold`` by
    construction.

    A failed hard gate is never blended numerically with the score: it is
    checked as its own independent AND-condition, so no aggregate quality,
    however high, can offset it.
    """
    effective_floor = threshold if floor is None else floor
    scores = [repetition.aggregate_quality for repetition in repetitions]
    all_scored = bool(scores) and all(score is not None for score in scores)
    average_quality = (
        sum(score for score in scores if score is not None) / len(scores)
        if all_scored
        else None
    )
    passed = (
        all(repetition.completed for repetition in repetitions)
        and all(repetition.gates.passed for repetition in repetitions)
        and all_scored
        and all(
            score is not None and score >= effective_floor for score in scores
        )
        and average_quality is not None
        and average_quality >= threshold
    )

    scored_indexed = [
        (index, repetition)
        for index, repetition in enumerate(repetitions)
        if repetition.aggregate_quality is not None
    ]
    if scored_indexed:
        _, lowest = min(
            scored_indexed,
            key=lambda item: (item[1].aggregate_quality, item[0]),
        )
        lowest_scoring_trace_url = lowest.trace_url
    else:
        lowest_scoring_trace_url = (
            repetitions[0].trace_url if repetitions else None
        )

    if case is not None:
        case_id, case_version = case.case_id, case.version
    else:
        first = repetitions[0]
        case_id, case_version = first.case_id, first.case_version

    return CaseResult(
        case_id=case_id,
        case_version=case_version,
        repetitions=list(repetitions),
        average_quality=average_quality,
        passed=passed,
        lowest_scoring_trace_url=lowest_scoring_trace_url,
    )


def decide_status(
    cases: Sequence[CaseResult],
    *,
    tier: EvaluationTier,
    runtime: EvaluationRuntimeConfig,
    errors: Sequence[EvaluationFailure] = (),
) -> EvaluationStatus:
    """The harness's pass/fail verdict. There is deliberately no
    ``"APPROVED"`` status: the spec forbids an automatic human-approval
    state, so a clean run still requires a human to say ``"REVIEW
    REQUIRED"`` is actually approved.

    ``tier``/``runtime`` are accepted for symmetry with the rest of this
    module's decisioning surface and so a future rule can consult them;
    every case's ``passed`` field was already computed against the correct
    tier-specific thresholds by ``build_case_result``, so this function's
    own logic does not need to re-derive them.
    """
    del tier, runtime
    if any(error.stage in ("trace", "setup") for error in errors):
        return "INFRASTRUCTURE FAILURE"
    if any(not case.passed for case in cases):
        return "FAILED"
    return "REVIEW REQUIRED"


def _row_identity(
    outputs: Mapping[str, JsonValue],
) -> tuple[str, int, int] | None:
    """``(case_id, case_version, repetition)`` read out of one row's
    ``TargetOutput``, never assumed from row order: a reordered or
    partially failed run must still land in the right case.
    """
    case_id = outputs.get("case_id")
    case_version = outputs.get("case_version")
    repetition = outputs.get("repetition")
    if (
        not isinstance(case_id, str)
        or not isinstance(case_version, int)
        or isinstance(case_version, bool)
        or not isinstance(repetition, int)
        or isinstance(repetition, bool)
    ):
        return None
    return (case_id, case_version, repetition)


def _judge_feedback_from_result(
    payload: Mapping[str, Any], *, runtime: EvaluationRuntimeConfig
) -> JudgeFeedback:
    """Reconstruct the typed ``JudgeFeedback`` the judge evaluator's
    LangSmith-shaped feedback dict already carries, without a second
    provider invocation: ``build_judge_evaluator``'s evaluator calls the
    judge exactly once and reports the outcome as a flattened feedback
    list, and this is the one place that dict is turned back into the
    typed record the local artifact and ``aggregate_quality`` need.
    """
    entries: dict[str, Mapping[str, Any]] = {
        item["key"]: item
        for item in payload.get("results", [])
        if isinstance(item, Mapping) and "key" in item
    }
    status_entry = entries.get("judge_status")
    quality_entry = entries.get("judge_quality")
    metadata: Mapping[str, Any] = {}
    if status_entry is not None and isinstance(status_entry.get("metadata"), Mapping):
        metadata = status_entry["metadata"]
    elif quality_entry is not None and isinstance(
        quality_entry.get("metadata"), Mapping
    ):
        metadata = quality_entry["metadata"]

    common = dict(
        prompt_id=str(metadata.get("prompt_id") or JUDGE_PROMPT_ID),
        rubric_version=int(metadata.get("rubric_version") or runtime.rubric_version),
        prompt_fingerprint=str(
            metadata.get("prompt_fingerprint")
            or judge_prompt_fingerprint(rubric_version=runtime.rubric_version)
        ),
        judge_model=str(metadata.get("judge_model") or runtime.judge_model),
        judge_configuration_fingerprint=str(
            metadata.get("judge_configuration_fingerprint")
            or runtime.judge_configuration_fingerprint
        ),
    )

    scored = status_entry is not None and status_entry.get("value") == "scored"
    if scored and quality_entry is not None:
        scores_kwargs = {
            name: float(entries[f"judge:{name}"]["score"])
            if f"judge:{name}" in entries
            else 0.0
            for name in COMMON_DIMENSION_WEIGHTS
        }
        agent_specific = {
            key[len("judge:") :]: float(item["score"])
            for key, item in entries.items()
            if key.startswith("judge:")
            and key[len("judge:") :] not in COMMON_DIMENSION_WEIGHTS
        }
        rationale = str(quality_entry.get("comment") or "no rationale recorded")
        return JudgeFeedback(
            status="scored",
            verdict=JudgeVerdict(
                scores=JudgeScores(**scores_kwargs),
                agent_specific=agent_specific,
                rationale=rationale,
            ),
            judge_quality=float(quality_entry["score"]),
            **common,
        )

    reason: str | None = None
    if status_entry is not None:
        candidate = status_entry.get("comment")
        if candidate in _JUDGE_NOT_RUN_REASONS:
            reason = candidate
    if reason is None:
        reason = "unhandled_exception"
    return JudgeFeedback(status="judge_not_run", not_run_reason=reason, **common)


def _unknown_case_code_result() -> dict[str, JsonValue]:
    return {
        "results": [
            {
                "key": "hard_gates_passed",
                "score": 0,
                "comment": "unknown case identity",
            },
            {
                "key": "deterministic_quality",
                "score": 0.0,
                "comment": "unknown case identity",
            },
        ]
    }


def _unknown_case_judge_result() -> dict[str, JsonValue]:
    return {
        "results": [
            {
                "key": "judge_status",
                "value": "judge_not_run",
                "comment": "unknown case identity",
            }
        ]
    }


async def run_agent_evaluation(
    settings: ConfigSettings,
    runtime: EvaluationRuntimeConfig,
    *,
    cases: Sequence[EvaluationCase],
    evaluate: EvaluateCallable = aevaluate,
    target_provider_factory: Callable[[], Any],
    judge_provider_factory: Callable[[], StructuredCompleter],
    tracker_factory: Callable[[], Tracker],
    dependency_factory: DependencyFactory,
    secrets: Sequence[str] = (),
    root: Path,
) -> ExperimentResult:
    """Run one experiment end to end: target, gates, judge, aggregation,
    thresholds, and the local artifact.

    Builds the real production-parity target (Task 21) once, then a
    dispatching pair of LangSmith evaluators -- one case's worth of
    ``code_evaluator`` (Task 18) and judge evaluator (Task 20) apiece,
    looked up per row by the ``(case_id, case_version)`` read out of that
    row's own ``TargetOutput`` rather than assumed from submission order --
    and calls ``evaluate`` exactly once for the whole batch. Every
    ``RepetitionResult`` is built as a side effect of the judge evaluator's
    own single invocation, so the judge is never called twice for the sake
    of local bookkeeping.
    """
    case_by_identity: dict[tuple[str, int], EvaluationCase] = {
        case.identity: case for case in cases
    }
    counter = RepetitionCounter(max_concurrency=runtime.max_concurrency)
    target: Target = build_target(
        runtime,
        settings,
        tracker_factory=tracker_factory,
        dependency_factory=dependency_factory,
        provider_factory=target_provider_factory,
        counter=counter,
        secrets=secrets,
        root=root,
    )

    pending_gates: dict[tuple[str, int, int], GateReport] = {}
    pending_deterministic: dict[tuple[str, int, int], float] = {}
    repetitions_by_case: dict[tuple[str, int], list[RepetitionResult]] = {
        identity: [] for identity in case_by_identity
    }

    code_evaluators = {
        identity: code_evaluator(case, secrets=secrets)
        for identity, case in case_by_identity.items()
    }

    def _gate_lookup(output: TargetOutput) -> GateReport | None:
        return pending_gates.get(
            (output.case_id, output.case_version, output.repetition)
        )

    judge_evaluators = {
        identity: build_judge_evaluator(
            judge_provider_factory(),
            case,
            runtime=runtime,
            secrets=secrets,
            gate_lookup=_gate_lookup,
        )
        for identity, case in case_by_identity.items()
    }

    def _dispatch_code(run: Any, example: Any) -> dict[str, JsonValue]:
        key = _row_identity(run.outputs)
        case_identity = (key[0], key[1]) if key is not None else None
        if key is None or case_identity not in case_by_identity:
            return _unknown_case_code_result()
        case = case_by_identity[case_identity]
        try:
            output = TargetOutput.model_validate(run.outputs)
        except ValidationError:
            return code_evaluators[case_identity](run, example)
        gates, deterministic = evaluate_target(output, case, secrets=secrets)
        pending_gates[key] = gates
        pending_deterministic[key] = deterministic
        return code_evaluators[case_identity](run, example)

    _dispatch_code.__name__ = "code_evaluator"

    async def _dispatch_judge(run: Any, example: Any) -> dict[str, JsonValue]:
        key = _row_identity(run.outputs)
        case_identity = (key[0], key[1]) if key is not None else None
        if key is None or case_identity not in case_by_identity:
            return _unknown_case_judge_result()
        payload = await judge_evaluators[case_identity](run, example)
        try:
            output = TargetOutput.model_validate(run.outputs)
        except ValidationError:
            return payload
        gates = pending_gates.get(key)
        deterministic = pending_deterministic.get(key)
        if gates is not None:
            feedback = _judge_feedback_from_result(payload, runtime=runtime)
            repetitions_by_case[case_identity].append(
                build_repetition_result(output, gates, deterministic, feedback)
            )
        return payload

    _dispatch_judge.__name__ = JUDGE_PROMPT_ID

    errors: list[EvaluationFailure] = []
    try:
        await evaluate(
            target,
            data=runtime.dataset_name,
            evaluators=[_dispatch_code, _dispatch_judge],
            experiment_prefix=runtime.experiment_name,
            num_repetitions=runtime.repetitions,
            max_concurrency=runtime.max_concurrency,
            metadata=experiment_metadata(runtime, settings),
        )
    except Exception as error:
        errors.append(
            EvaluationFailure(
                stage="trace",
                reason="langsmith_unavailable",
                message=str(error) or type(error).__name__,
                exception_type=type(error).__name__,
            )
        )

    threshold = (
        runtime.live_threshold
        if runtime.tier == "live"
        else runtime.case_average_threshold
    )
    floor = (
        runtime.live_threshold
        if runtime.tier == "live"
        else runtime.repetition_floor
    )
    case_results = [
        build_case_result(
            case_by_identity[identity],
            repetitions_by_case[identity],
            threshold=threshold,
            floor=floor,
        )
        for identity in case_by_identity
        if repetitions_by_case[identity]
    ]

    result = ExperimentResult(
        agent_name=runtime.agent_name,
        tier=runtime.tier,
        experiment_name=runtime.experiment_name,
        dataset_name=runtime.dataset_name,
        cases=case_results,
        status=decide_status(
            case_results, tier=runtime.tier, runtime=runtime, errors=errors
        ),
        metadata=experiment_metadata(runtime, settings),
        errors=errors,
    )

    path = runtime.output_root / "results.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    except OSError as error:
        result = result.model_copy(
            update={
                "errors": [
                    *result.errors,
                    EvaluationFailure(
                        stage="artifact",
                        reason="artifact_write_failed",
                        message=str(error) or type(error).__name__,
                        exception_type=type(error).__name__,
                    ),
                ]
            }
        )

    return result
