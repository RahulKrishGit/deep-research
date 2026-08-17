"""Agent-agnostic hard gates and deterministic quality scoring.

This is the general half of the evaluation harness: the deterministic
hard gates that apply to every agent, plus the weighted deterministic
quality score. Agent-specific gates and metrics are Task 18 and are
wired in by the caller of ``evaluate_target``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import TypeAlias

from deep_research.evaluation.config import contains_secret
from deep_research.evaluation.models import (
    EvaluationCase,
    GateReport,
    GateResult,
    TargetOutput,
)

GENERAL_GATE_IDS: tuple[str, ...] = (
    "agent_constructed",
    "run_completed",
    "contracts_valid",
    "required_fields_present",
    "budgets_respected",
    "errors_typed",
    "citations_known",
    "no_secret_in_output",
    "no_prohibited_calls",
    "trace_available",
    "no_tracker_transport_failure",
)

MetricFunction: TypeAlias = Callable[[TargetOutput, EvaluationCase], bool]

_RESEARCH_ERROR_KEYS = frozenset(
    {"error_type", "source", "message", "timestamp"}
)
_URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+")
# Greedy URL matching keeps trailing punctuation that belongs to prose
# (markdown parens, commas, periods, ...). Strip it so the extracted string
# compares equal to the canonical ``known_source_urls`` entry.
_URL_TRAILING_PUNCTUATION = ".,;:!?)]}'\"'"
_TRANSPORT_FAILURE_TYPE = "langsmith_tracing_failure"


class MissingMetricError(RuntimeError):
    """A case declares a deterministic metric with no implementation."""


def _field(value: object, name: str) -> object:
    """Read one field from a contract model or a plain dict.

    ``model_copy(update=...)`` does not re-validate, so a fixture can hold
    a raw dict where ``models.py`` declares a model; both shapes must be
    readable without assuming which one arrived.
    """
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _url_like_strings(payload: object) -> set[str]:
    """Every URL-looking substring, recursively, in ``payload``.

    Trailing punctuation is trimmed off each match so a URL embedded in
    prose (``...report).``) compares equal to the bare canonical entry;
    ``rstrip`` removes stacked punctuation in one pass.
    """
    found: set[str] = set()
    if isinstance(payload, str):
        found.update(
            url.rstrip(_URL_TRAILING_PUNCTUATION)
            for url in _URL_PATTERN.findall(payload)
        )
    elif isinstance(payload, Mapping):
        for item in payload.values():
            found.update(_url_like_strings(item))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            found.update(_url_like_strings(item))
    return found


def _gate_agent_constructed(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    stage = _field(output.failure, "stage")
    passed = stage is None or stage != "construction"
    return GateResult(
        gate_id="agent_constructed",
        passed=passed,
        detail="" if passed else "agent construction failed",
    )


def _gate_run_completed(output: TargetOutput, case: EvaluationCase) -> GateResult:
    if output.completed is not True:
        return GateResult(
            gate_id="run_completed",
            passed=False,
            detail="run did not complete",
        )
    if output.failure is not None:
        return GateResult(
            gate_id="run_completed",
            passed=False,
            detail="run ended in a recorded failure",
        )
    return GateResult(gate_id="run_completed", passed=True, detail="")


def _gate_contracts_valid(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    problems: list[str] = []
    if not isinstance(output.result, Mapping):
        problems.append("result is not a mapping")
    if not isinstance(output.state_update, Mapping):
        problems.append("state_update is not a mapping")
    return GateResult(
        gate_id="contracts_valid",
        passed=not problems,
        detail="; ".join(problems),
    )


def _gate_required_fields_present(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    result = output.result if isinstance(output.result, Mapping) else {}
    missing = [
        name
        for name in case.expectations.required_output_fields
        if name not in result
    ]
    return GateResult(
        gate_id="required_fields_present",
        passed=not missing,
        detail=(
            "missing required fields: " + ", ".join(missing) if missing else ""
        ),
    )


def _gate_budgets_respected(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    react = output.react
    if react is None:
        return GateResult(
            gate_id="budgets_respected",
            passed=False,
            detail="react summary missing; budget cannot be assessed",
        )
    iterations = _field(react, "iterations")
    tool_calls = _field(react, "tool_calls")
    violations: list[str] = []
    if (
        not isinstance(iterations, int)
        or iterations > case.expectations.max_iterations
    ):
        violations.append(
            f"iterations {iterations} exceed "
            f"{case.expectations.max_iterations}"
        )
    if (
        not isinstance(tool_calls, int)
        or tool_calls > case.expectations.max_tool_calls
    ):
        violations.append(
            f"tool_calls {tool_calls} exceed "
            f"{case.expectations.max_tool_calls}"
        )
    return GateResult(
        gate_id="budgets_respected",
        passed=not violations,
        detail="; ".join(violations),
    )


def _gate_errors_typed(output: TargetOutput, case: EvaluationCase) -> GateResult:
    untyped = [
        index
        for index, entry in enumerate(output.errors)
        if not isinstance(entry, Mapping)
        or not _RESEARCH_ERROR_KEYS.issubset(entry.keys())
    ]
    return GateResult(
        gate_id="errors_typed",
        passed=not untyped,
        detail=(
            "untyped error records at indices: " + ", ".join(map(str, untyped))
            if untyped
            else ""
        ),
    )


def _gate_citations_known(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    # Live cases fall through to the trajectory check even when no known
    # source urls are declared: for those cases the recorded tool trajectory
    # is the only evidence a cited url was genuinely retrieved.
    expectations = case.expectations
    allowed: set[str] = set(expectations.known_source_urls)
    scripted = _field(output.evidence, "scripted_search_urls") or ()
    allowed.update(url for url in scripted if isinstance(url, str))
    if case.tier == "live":
        for step in output.trajectory:
            allowed.update(_url_like_strings(_field(step, "thought")))
            allowed.update(
                _url_like_strings(_field(step, "observation_summary"))
            )
    cited = _url_like_strings(output.result) | _url_like_strings(
        output.state_update
    )
    unknown = sorted(cited - allowed)
    return GateResult(
        gate_id="citations_known",
        passed=not unknown,
        detail=(
            "unknown source urls: " + ", ".join(unknown) if unknown else ""
        ),
    )


def _gate_no_secret_in_output(
    output: TargetOutput,
    case: EvaluationCase,
    *,
    secrets: Sequence[str],
) -> GateResult:
    # ``warnings=False``: ``model_copy(update=...)`` does not re-validate,
    # so a caller can hold a raw dict where models.py declares a model;
    # the serializer still emits the value, only the warning is noise.
    payload = output.model_dump(mode="json", warnings=False)
    paths = contains_secret(payload, secrets)
    return GateResult(
        gate_id="no_secret_in_output",
        passed=not paths,
        detail="secret found at: " + ", ".join(paths) if paths else "",
    )


def _gate_no_prohibited_calls(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    calls = _field(output.dependencies, "prohibited_calls") or []
    calls = [name for name in calls if isinstance(name, str)]
    return GateResult(
        gate_id="no_prohibited_calls",
        passed=not calls,
        detail="prohibited calls: " + ", ".join(calls) if calls else "",
    )


def _gate_trace_available(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    trace_url = output.trace_url
    passed = isinstance(trace_url, str) and bool(trace_url.strip())
    return GateResult(
        gate_id="trace_available",
        passed=passed,
        detail="" if passed else "no non-blank trace_url",
    )


def _gate_no_tracker_transport_failure(
    output: TargetOutput, case: EvaluationCase
) -> GateResult:
    failures = [
        index
        for index, entry in enumerate(output.tracker_errors)
        if isinstance(entry, Mapping)
        and entry.get("error_type") == _TRANSPORT_FAILURE_TYPE
    ]
    return GateResult(
        gate_id="no_tracker_transport_failure",
        passed=not failures,
        detail=(
            "langsmith transport failures at indices: "
            + ", ".join(map(str, failures))
            if failures
            else ""
        ),
    )


_GENERAL_GATE_FUNCTIONS: dict[
    str, Callable[[TargetOutput, EvaluationCase], GateResult]
] = {
    "agent_constructed": _gate_agent_constructed,
    "run_completed": _gate_run_completed,
    "contracts_valid": _gate_contracts_valid,
    "required_fields_present": _gate_required_fields_present,
    "budgets_respected": _gate_budgets_respected,
    "errors_typed": _gate_errors_typed,
    "citations_known": _gate_citations_known,
    "no_prohibited_calls": _gate_no_prohibited_calls,
    "trace_available": _gate_trace_available,
    "no_tracker_transport_failure": _gate_no_tracker_transport_failure,
}

if set(_GENERAL_GATE_FUNCTIONS) != set(GENERAL_GATE_IDS) - {
    "no_secret_in_output"
}:
    raise RuntimeError("every general gate id needs exactly one gate function")


def evaluate_general_gates(
    output: TargetOutput,
    case: EvaluationCase,
    *,
    secrets: Sequence[str],
) -> list[GateResult]:
    """One result per general gate, in ``GENERAL_GATE_IDS`` order, always.

    A gate that cannot be assessed is a failed gate with a detail saying
    why — never an omitted one, which would silently shrink the
    requirement set.
    """
    results: list[GateResult] = []
    for gate_id in GENERAL_GATE_IDS:
        if gate_id == "no_secret_in_output":
            results.append(
                _gate_no_secret_in_output(output, case, secrets=secrets)
            )
        else:
            results.append(_GENERAL_GATE_FUNCTIONS[gate_id](output, case))
    return results


def deterministic_quality(
    output: TargetOutput,
    case: EvaluationCase,
    *,
    metric_functions: Mapping[str, MetricFunction],
) -> float:
    """The weighted sum of passing deterministic metrics, in ``[0.0, 1.0]``.

    ``CaseExpectations`` verifies the weights sum to 1.0, so the raw sum
    is already normalized; nothing here rounds. A metric with no
    registered function is a defect and raises ``MissingMetricError``; a
    function that raises is a failed metric (weight zero) and must not
    abort the other metrics.
    """
    score = 0.0
    for metric in case.expectations.deterministic_metrics:
        function = metric_functions.get(metric.metric_id)
        if function is None:
            raise MissingMetricError(
                f"no metric function registered for {metric.metric_id!r}"
            )
        try:
            if function(output, case):
                score += metric.weight
        except Exception:
            # One broken metric must not lose every other metric's score.
            continue
    return score


def evaluate_target(
    output: TargetOutput,
    case: EvaluationCase,
    *,
    secrets: Sequence[str],
    metric_functions: Mapping[str, MetricFunction],
) -> tuple[GateReport, float]:
    """General gates plus the deterministic score for one repetition.

    Task 18 extends this wrapper with the agent-specific gates and
    metrics; the ``(GateReport, float)`` return shape is fixed.
    """
    results = evaluate_general_gates(output, case, secrets=secrets)
    score = deterministic_quality(
        output, case, metric_functions=metric_functions
    )
    return GateReport(results=results), score
