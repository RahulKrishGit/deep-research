"""Validate and project a bounded offline evaluation inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

EXPECTED_CASES = {
    "researcher": {
        "multi-source-coverage",
        "conflicting-evidence",
        "partial-search-failure",
    },
    "source_evaluator": {
        "strong-and-weak-sources",
        "corroboration-recency-reputation",
        "reputation-provider-failure",
    },
    "fact_checker": {
        "mixed-verdicts",
        "independent-domain-evidence",
        "verification-search-failure",
    },
    "synthesizer": {
        "complete-cited-report",
        "conflict-and-limitations",
        "write-or-memory-failure",
    },
    "critic": {
        "approve-strong-report",
        "request-more-research",
        "missing-evidence-or-budget-exhausted",
    },
}

_REQUIRED_REPETITION_TELEMETRY = frozenset(
    {
        "deterministic_metrics",
        "prohibited_call_count",
        "react_stop_reason",
        "fallback_provider_diagnostic",
    }
)
_SAFE_FAILURE_REASONS = frozenset(
    {
        "unknown_case",
        "construction_failed",
        "artifact_build_failed",
        "output_limit",
        "schema_output",
        "provider_timeout",
        "provider_rate_limit",
        "provider_transport",
        "provider_http",
        "provider_response",
        "provider_failure",
        "tool_failure",
        "validation_failure",
        "unhandled_failure",
    }
)
_SAFE_GATE_ID = re.compile(r"^[a-z][a-z0-9_]{0,95}$")
_MAX_GATE_RESULTS = 64
_MAX_JUDGE_DIMENSIONS = 32
_MAX_DIAGNOSTICS = 16
_MAX_URL_LENGTH = 2048
_MAX_METADATA_VALUE_LENGTH = 256
_SAFE_METADATA_KEYS = frozenset(
    {
        "agent",
        "tier",
        "git_commit",
        "git_dirty",
        "target_model",
        "target_reasoning_effort",
        "thinking_mode",
        "configuration_fingerprint",
        "judge_model",
        "judge_reasoning_effort",
        "judge_temperature",
        "judge_configuration_fingerprint",
        "case_registry_version",
        "rubric_version",
        "dependency_mode",
        "target_prompt_fingerprint",
        "evaluation_package_version",
        "experiment_name",
        "dataset_name",
        "dataset_version",
    }
)


class InventoryInputError(ValueError):
    """A safe validation failure or typed-contract gap."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _ensure_src_importable() -> None:
    source_root = _repo_root() / "src"
    if source_root.is_dir() and str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate an evaluation result and write a bounded inventory."
    )
    parser.add_argument("--agent", required=True)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--provenance", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def _normalize_agent(value: str) -> str:
    candidate = value.strip().casefold().replace("-", "_")
    if candidate not in EXPECTED_CASES:
        raise InventoryInputError("unknown non-Planner agent")
    return candidate


def _literal_results_path(path: Path) -> Path:
    raw = str(path)
    if any(character in raw for character in "*?[]") or "..." in raw:
        raise InventoryInputError("results path must be one literal path")
    resolved = path.resolve()
    if resolved.name != "results.json" or not resolved.is_file():
        raise InventoryInputError("results path must name an existing results.json")
    return resolved


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InventoryInputError("JSON input could not be read") from error
    if not isinstance(value, dict):
        raise InventoryInputError("JSON input must be an object")
    return value


def _load_result(path: Path):
    _ensure_src_importable()
    from deep_research.evaluation.models import ExperimentResult

    try:
        return ExperimentResult.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise InventoryInputError(
            "results.json failed typed ExperimentResult validation"
        ) from error


def _require_provenance(provenance: dict[str, object]) -> str:
    candidate = provenance.get("candidate_sha")
    if not isinstance(candidate, str) or len(candidate) != 40:
        raise InventoryInputError("provenance candidate SHA is missing or invalid")
    if any(character not in "0123456789abcdef" for character in candidate):
        raise InventoryInputError("provenance candidate SHA is missing or invalid")
    return candidate


def _results_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, UnicodeError) as error:
        raise InventoryInputError("results SHA-256 could not be computed") from error


def _metadata_string(metadata: dict[str, object], key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value:
        raise InventoryInputError(f"results metadata is missing {key}")
    return value


def _validate_identity(result, agent: str, provenance: dict[str, object]) -> None:
    if result.agent_name != agent or result.tier != "controlled":
        raise InventoryInputError("result agent or tier does not match the request")
    if provenance.get("agent") != agent or provenance.get("cli_agent") != agent.replace(
        "_", "-"
    ):
        raise InventoryInputError("provenance agent does not match the request")
    expected_cases = EXPECTED_CASES[agent]
    if (
        len(result.cases) != 3
        or {case.case_id for case in result.cases} != expected_cases
    ):
        raise InventoryInputError("result case set is not the frozen three-case set")
    for case in result.cases:
        repetitions = []
        for item in case.repetitions:
            if item.case_id != case.case_id or item.case_version != case.case_version:
                raise InventoryInputError(
                    "repetition case identity or version does not match its case"
                )
            repetitions.append(item.repetition)
        if len(repetitions) != 3 or set(repetitions) != {1, 2, 3}:
            raise InventoryInputError("each case must contain repetitions 1, 2, and 3")

    candidate = _require_provenance(provenance)
    git_commit = result.metadata.get("git_commit")
    if git_commit != candidate:
        raise InventoryInputError("result candidate SHA does not match provenance")
    for result_key, provenance_key in (
        ("configuration_fingerprint", "configuration_fingerprint"),
        ("judge_configuration_fingerprint", "judge_configuration_fingerprint"),
        ("target_prompt_fingerprint", "prompt_fingerprint"),
    ):
        result_fingerprint = _metadata_string(result.metadata, result_key)
        provenance_fingerprint = provenance.get(provenance_key)
        if not isinstance(provenance_fingerprint, str) or not provenance_fingerprint:
            raise InventoryInputError(f"provenance is missing {provenance_key}")
        if result_fingerprint != provenance_fingerprint:
            raise InventoryInputError(f"{result_key} does not match provenance")


def _require_telemetry_keys(payload: dict[str, object]) -> None:
    """Reject older artifacts whose additive fields were filled by defaults."""
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise InventoryInputError("results cases must be an array")
    for case in cases:
        if not isinstance(case, dict):
            raise InventoryInputError("result case must be an object")
        repetitions = case.get("repetitions")
        if not isinstance(repetitions, list):
            raise InventoryInputError("case repetitions must be an array")
        for repetition in repetitions:
            if not isinstance(repetition, dict):
                raise InventoryInputError("repetition must be an object")
            missing = _REQUIRED_REPETITION_TELEMETRY.difference(repetition)
            if missing:
                raise InventoryInputError(
                    "repetition is missing required typed telemetry"
                )


def _direct_url(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > _MAX_URL_LENGTH:
        return None
    if not value.startswith(("https://", "http://")):
        return None
    return value


def _safe_metadata(metadata: dict[str, object]) -> dict[str, object]:
    """Copy only the finite scalar metadata fields used for provenance."""
    projected: dict[str, object] = {}
    for key in _SAFE_METADATA_KEYS:
        value = metadata.get(key)
        if isinstance(value, str) and len(value) <= _MAX_METADATA_VALUE_LENGTH:
            projected[key] = value
        elif isinstance(value, (bool, int, float)) or value is None:
            projected[key] = value
    return projected


def _safe_gate_projection(repetition) -> tuple[list[str], list[dict[str, str]]]:
    if len(repetition.gates.results) > _MAX_GATE_RESULTS:
        raise InventoryInputError("repetition has too many gate results")
    failed_ids: list[str] = []
    failed_details: list[dict[str, str]] = []
    for gate in repetition.gates.results:
        if not gate.passed:
            if _SAFE_GATE_ID.fullmatch(gate.gate_id) is None:
                raise InventoryInputError("gate id is not a safe identifier")
            failed_ids.append(gate.gate_id)
            failed_details.append({"gate_id": gate.gate_id, "detail": "gate_failed"})
    return failed_ids, failed_details


def _safe_failure_projection(repetition) -> dict[str, object]:
    failure = repetition.errors[0] if repetition.errors else None
    if failure is None:
        return {
            "target_failure_stage": None,
            "target_failure_reason": None,
            "target_failure_details_kind": None,
        }
    stage = (
        failure.stage
        if failure.stage in {"provider", "trace", "artifact", "setup"}
        else None
    )
    reason = failure.reason if failure.reason in _SAFE_FAILURE_REASONS else None
    details_kind = getattr(failure.details, "kind", None)
    if details_kind is not None and not isinstance(details_kind, str):
        details_kind = None
    return {
        "target_failure_stage": stage,
        "target_failure_reason": reason,
        "target_failure_details_kind": details_kind,
    }


def _safe_judge_projection(repetition) -> dict[str, object]:
    judge = repetition.judge
    if judge is None:
        return {
            "judge_status": None,
            "judge_not_run_reason": None,
            "judge_dimensions": {},
            "judge_quality": None,
            "judge_diagnostic_kinds": [],
            "evaluator_trace_url": None,
            "evaluator_source_url": None,
        }
    dimensions: dict[str, float] = {}
    if judge.verdict is not None:
        dimensions.update(judge.verdict.scores.model_dump(mode="json"))
        for key, value in judge.verdict.agent_specific.items():
            if _SAFE_GATE_ID.fullmatch(key) is None:
                raise InventoryInputError("judge dimension is not a safe identifier")
            dimensions[key] = value
    if len(dimensions) > _MAX_JUDGE_DIMENSIONS:
        raise InventoryInputError("judge has too many dimensions")
    if len(judge.diagnostics) > _MAX_DIAGNOSTICS:
        raise InventoryInputError("judge has too many diagnostics")
    return {
        "judge_status": judge.status,
        "judge_not_run_reason": judge.not_run_reason,
        "judge_dimensions": dimensions,
        "judge_quality": judge.judge_quality,
        "judge_diagnostic_kinds": [item.kind for item in judge.diagnostics],
        "evaluator_trace_url": _direct_url(judge.evaluator_trace_url),
        "evaluator_source_url": _direct_url(judge.evaluator_source_url),
    }


def _project_repetition(repetition) -> dict[str, object]:
    failed_gate_ids, failed_gate_details = _safe_gate_projection(repetition)
    failure = _safe_failure_projection(repetition)
    judge = _safe_judge_projection(repetition)
    fallback = repetition.fallback_provider_diagnostic
    fallback_kinds = [fallback.kind] if fallback is not None else []
    fallback_operations = [fallback.operation] if fallback is not None else []
    return {
        "repetition": repetition.repetition,
        "completed": repetition.completed,
        "failed_gate_ids": failed_gate_ids,
        "failed_gate_details": failed_gate_details,
        "deterministic_metrics": dict(repetition.deterministic_metrics),
        "deterministic_quality": repetition.deterministic_quality,
        **judge,
        "aggregate_quality": repetition.aggregate_quality,
        **failure,
        "fallback_provider_failure_kinds": fallback_kinds,
        "fallback_provider_operations": fallback_operations,
        "react_stop_reason": repetition.react_stop_reason,
        "prohibited_call_count": repetition.prohibited_call_count,
        "target_trace_url": _direct_url(repetition.trace_url),
    }


def _contract_gap(result, results_sha256: str) -> dict[str, object]:
    return {
        "schema_version": result.schema_version,
        "agent_name": result.agent_name,
        "cli_agent_name": result.agent_name.replace("_", "-"),
        "tier": result.tier,
        "experiment_name": result.experiment_name,
        "experiment_url": _direct_url(result.experiment_url),
        "dataset_name": result.dataset_name,
        "dataset_url": _direct_url(result.dataset_url),
        "status": result.status,
        "metadata": _safe_metadata(result.metadata),
        "results_sha256": results_sha256,
        "cases": [
            {
                "case_id": case.case_id,
                "case_version": case.case_version,
                "average_quality": case.average_quality,
                "passed": case.passed,
                "lowest_scoring_trace_url": _direct_url(case.lowest_scoring_trace_url),
                "repetitions": [
                    _project_repetition(repetition) for repetition in case.repetitions
                ],
            }
            for case in result.cases
        ],
    }


def _bind_results_sha256(
    payload: dict[str, object], results_sha256: str
) -> dict[str, object]:
    """Bind the exact input-file digest to every successful inventory payload."""
    existing = payload.get("results_sha256")
    if existing is not None and existing != results_sha256:
        raise InventoryInputError("inventory results SHA-256 does not match results")
    return {**payload, "results_sha256": results_sha256}


def build_inventory(
    *, agent: str, results_path: Path, provenance_path: Path
) -> dict[str, object]:
    normalized_agent = _normalize_agent(agent)
    results_path = _literal_results_path(results_path)
    results_sha256 = _results_sha256(results_path)
    provenance = _read_json(provenance_path.resolve())
    raw_result = _read_json(results_path)
    _require_telemetry_keys(raw_result)
    result = _load_result(results_path)
    _validate_identity(result, normalized_agent, provenance)
    payload = _contract_gap(result, results_sha256)
    return _bind_results_sha256(payload, results_sha256)


def _write_once(path: Path, payload: dict[str, object]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as error:
        raise InventoryInputError("output already exists") from error
    except OSError as error:
        raise InventoryInputError("inventory output could not be written") from error


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = build_inventory(
            agent=args.agent,
            results_path=args.results,
            provenance_path=args.provenance,
        )
        _write_once(args.output, payload)
    except InventoryInputError as error:
        print(f"inventory validation failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
