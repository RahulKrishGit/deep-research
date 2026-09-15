"""Offline whole-report campaign runner and artifact writer."""

from __future__ import annotations

import argparse
import asyncio
import subprocess
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from pydantic import JsonValue

from deep_research.cli import render_summary
from deep_research.e2e_evaluation.cases import (
    CONTROLLED_CASE_IDS,
    LIVE_CASE_IDS,
    ScriptedGraphPublisher,
    case_by_id,
    controlled_cases,
    dependencies_for,
    scripted_research_agents,
)
from deep_research.e2e_evaluation.evaluators import (
    build_judge_input,
    deterministic_evaluation,
    judge_whole_report,
    production_cli_summary,
)
from deep_research.e2e_evaluation.models import (
    AGENT_NAMES,
    CAMPAIGN_SCHEMA_VERSION,
    CASE_REGISTRY_VERSION,
    CASE_SCHEMA_VERSION,
    QUALITY_GATE_VERSION,
    REPORT_SCHEMA_VERSION,
    CampaignMetadata,
    CampaignRepetition,
    CampaignResult,
    CaseCampaignResult,
    ControlledCase,
    WholeReportJudgeScore,
)
from deep_research.graph.orchestrator import (
    compile_research_graph,
    run_research_graph,
)
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.runtime.outcome import build_outcome

LIVE_TIER_NOT_RUN = (
    "live tier is declared only and has no runner; running it requires a "
    "separately authorized canary"
)
DEFAULT_OUTPUT_DIRECTORY = Path("output/evaluations/e2e")
CONTROLLED_REPETITIONS = 3
JUDGE_FLOOR = 0.70
JUDGE_MEAN_FLOOR = 0.80
_SCORE_EPSILON = 1e-9


def _score_at_least(value: float, floor: float) -> bool:
    """Treat an exact decimal boundary as inclusive despite binary floats."""
    return value + _SCORE_EPSILON >= floor


def graph_revision() -> str:
    """Return the local graph revision without contacting a remote service."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    revision = result.stdout.strip()
    return revision if result.returncode == 0 and revision else "unknown"


def target_prompt_fingerprints() -> dict[str, str]:
    """Fingerprint all six production prompts without loading configuration."""
    from deep_research.evaluation.config import agent_prompt_fingerprint

    return {
        name: agent_prompt_fingerprint(name)  # type: ignore[arg-type]
        for name in AGENT_NAMES
    }


def build_judge_metadata(
    *,
    graph_revision: str | None = None,
    case_id: str,
    repetition: int,
    request_counts: Mapping[str, int],
    tier: str = "controlled",
    target_model: str = "scripted-target",
    target_reasoning_effort: str = "none",
    judge_model: str = "scripted-whole-report-judge",
    judge_reasoning_effort: str = "none",
    case_version: int = CASE_SCHEMA_VERSION,
) -> dict[str, JsonValue]:
    """Build secret-free metadata shared by local and trace-shaped artifacts."""
    payload = CampaignMetadata(
        campaign_schema_version=CAMPAIGN_SCHEMA_VERSION,
        case_schema_version=CASE_SCHEMA_VERSION,
        case_registry_version=CASE_REGISTRY_VERSION,
        report_schema_version=REPORT_SCHEMA_VERSION,
        quality_gate_version=QUALITY_GATE_VERSION,
        graph_revision=graph_revision or graph_revision_value(),
        target_prompt_fingerprints=target_prompt_fingerprints(),
        target_model=target_model,
        target_reasoning_effort=target_reasoning_effort,
        judge_model=judge_model,
        judge_reasoning_effort=judge_reasoning_effort,
        case_id=case_id,
        case_version=case_version,
        tier=(tier if tier in {"controlled", "live"} else "controlled"),
        repetition=repetition,
        request_counts=dict(request_counts),
    )
    return payload.model_dump(mode="json")


def graph_revision_value() -> str:
    """Named wrapper makes the metadata source easy to replace in tests."""
    return graph_revision()


def _scripted_repetition(
    case: ControlledCase,
    repetition: int,
    *,
    artifact_directory: Path,
    judge: Callable[[Any], WholeReportJudgeScore] = judge_whole_report,
) -> CampaignRepetition:
    dependencies = dependencies_for(case)
    artifact_directory.mkdir(parents=True, exist_ok=True)
    publisher = ScriptedGraphPublisher(dependencies, artifact_directory)
    agents = scripted_research_agents(case, dependencies, publisher)
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False,
            project="controlled-e2e",
            api_key=None,
        )
    )
    graph = compile_research_graph(agents)
    graph_run = asyncio.run(
        run_research_graph(
            graph=graph,
            tracker=tracker,
            session_id=f"controlled-{case.case_id}-r{repetition}",
            question=case.question,
            max_iterations=max(2, len(case.passes)),
        )
    )
    state = graph_run.state
    outcome = build_outcome(graph_run, metrics=tracker.metrics)
    cli_output = render_summary(outcome, verbose=False)
    metrics = deterministic_evaluation(
        case,
        state,
        dependencies=dependencies,
        cli_output=cli_output,
    )
    summary = production_cli_summary(cli_output)
    judge_input = build_judge_input(case, state, metrics)
    judge_result = judge(judge_input)
    metadata = build_judge_metadata(
        graph_revision=graph_revision_value(),
        case_id=case.case_id,
        case_version=case.version,
        repetition=repetition,
        request_counts={**dependencies.request_counts, "judge": 1},
    )
    return CampaignRepetition(
        case_id=case.case_id,
        repetition=repetition,
        state=state,
        composition=state.composition,
        report=state.report or "",
        evidence_ledger=state.report_evidence or "",
        deterministic=metrics,
        judge=judge_result,
        cli_summary=summary,
        cli_output=list(cli_output),
        judge_input=judge_input,
        publication_operations=list(dependencies.publication_operations),
        metadata=CampaignMetadata.model_validate(metadata),
        langsmith_metadata=dict(metadata),
    )


def _case_result(
    case: ControlledCase, repetitions: Sequence[CampaignRepetition]
) -> CaseCampaignResult:
    coverages = [item.deterministic.coverage_ratio for item in repetitions]
    judges = [item.judge.score for item in repetitions]
    hard_failures: list[str] = []
    for item in repetitions:
        for failure in [
            *item.deterministic.integrity_failures,
            *item.deterministic.hard_failures,
        ]:
            if failure not in hard_failures:
                hard_failures.append(failure)
    accepted = (
        len(repetitions) == CONTROLLED_REPETITIONS
        and all(item.deterministic.integrity_passed for item in repetitions)
        and all(item.deterministic.coverage_ratio >= 0.80 for item in repetitions)
        and sum(coverages) / len(coverages) >= 0.90
        and all(
            item.deterministic.scored_cited_sources == item.deterministic.cited_sources
            for item in repetitions
        )
        and all(item.deterministic.duplicate_claims == 0 for item in repetitions)
        and all(item.deterministic.duplicate_source_rows == 0 for item in repetitions)
        and all(item.deterministic.uncited_settled_points == 0 for item in repetitions)
        and all(_score_at_least(item.judge.score, JUDGE_FLOOR) for item in repetitions)
        and _score_at_least(sum(judges) / len(judges), JUDGE_MEAN_FLOOR)
    )
    return CaseCampaignResult(
        case_id=case.case_id,
        repetitions=list(repetitions),
        mean_coverage=sum(coverages) / len(coverages),
        mean_judge_score=sum(judges) / len(judges),
        accepted=accepted,
        hard_failures=hard_failures,
    )


def run_case(
    case_id: str,
    *,
    tier: str = "controlled",
    repetitions: int = CONTROLLED_REPETITIONS,
    output_directory: str | Path | None = None,
    judge: Callable[[Any], WholeReportJudgeScore] = judge_whole_report,
) -> CaseCampaignResult:
    """Run one controlled case exactly three times and write its artifact."""
    if tier == "live":
        raise RuntimeError(LIVE_TIER_NOT_RUN)
    if tier != "controlled":
        raise ValueError("tier must be controlled or live")
    if repetitions != CONTROLLED_REPETITIONS:
        raise ValueError("controlled whole-report cases require exactly 3 repetitions")
    case = case_by_id(case_id)
    root = Path(output_directory or DEFAULT_OUTPUT_DIRECTORY) / case.case_id
    root.mkdir(parents=True, exist_ok=True)
    rows = [
        _scripted_repetition(
            case,
            repetition,
            artifact_directory=root / f"repetition-{repetition}",
            judge=judge,
        )
        for repetition in range(1, repetitions + 1)
    ]
    result = _case_result(case, rows)
    artifact = root / "case.json"
    result = result.model_copy(update={"artifact_path": str(artifact)})
    artifact.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result


def run_suite(
    *,
    tier: str = "controlled",
    repetitions: int = CONTROLLED_REPETITIONS,
    output_directory: str | Path | None = None,
    case_ids: Sequence[str] | None = None,
    judge: Callable[[Any], WholeReportJudgeScore] = judge_whole_report,
) -> CampaignResult:
    """Run the offline controlled campaign and write a suite artifact."""
    if tier == "live":
        raise RuntimeError(LIVE_TIER_NOT_RUN)
    if tier != "controlled":
        raise ValueError("tier must be controlled or live")
    if repetitions != CONTROLLED_REPETITIONS:
        raise ValueError("controlled whole-report suite requires exactly 3 repetitions")
    selected = tuple(case_ids or CONTROLLED_CASE_IDS)
    if selected != CONTROLLED_CASE_IDS:
        # A full controlled suite is intentionally closed over exactly the
        # three required cases; use run_case for a focused case invocation.
        raise ValueError(
            "controlled suite must contain exactly the three required cases"
        )
    root = Path(output_directory or DEFAULT_OUTPUT_DIRECTORY)
    results = [
        run_case(
            case.case_id,
            tier=tier,
            repetitions=repetitions,
            output_directory=root,
            judge=judge,
        )
        for case in controlled_cases()
    ]
    accepted = all(case.accepted for case in results)
    request_counts: dict[str, int] = {}
    for case_result in results:
        for repetition in case_result.repetitions:
            for name, count in repetition.metadata.request_counts.items():
                request_counts[name] = request_counts.get(name, 0) + count
    suite_metadata: dict[str, JsonValue] = {
        "campaign_schema_version": CAMPAIGN_SCHEMA_VERSION,
        "case_schema_version": CASE_SCHEMA_VERSION,
        "case_registry_version": CASE_REGISTRY_VERSION,
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "quality_gate_version": QUALITY_GATE_VERSION,
        "graph_revision": graph_revision_value(),
        "target_prompt_fingerprints": target_prompt_fingerprints(),
        "target_model": "scripted-target",
        "target_reasoning_effort": "none",
        "judge_model": "scripted-whole-report-judge",
        "judge_reasoning_effort": "none",
        "request_counts": request_counts,
        "network": "zero",
        "case_ids": list(selected),
    }
    suite = CampaignResult(
        campaign_id=(
            "controlled-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + uuid4().hex[:8]
        ),
        tier="controlled",
        repetitions=repetitions,
        cases=results,
        accepted=accepted,
        metadata=suite_metadata,
    )
    root.mkdir(parents=True, exist_ok=True)
    suite_artifact = root / "suite.json"
    suite = suite.model_copy(
        update={
            "artifact_path": str(suite_artifact),
            "langsmith_metadata": dict(suite_metadata),
        }
    )
    suite_artifact.write_text(
        suite.model_dump_json(indent=2), encoding="utf-8"
    )
    return suite


def run_controlled_suite(**kwargs: Any) -> CampaignResult:
    """Named alias for callers that want to state the tier explicitly."""
    return run_suite(tier="controlled", **kwargs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m deep_research.e2e_evaluation",
        description="Run the network-zero whole-report quality campaign.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="list controlled case ids")
    case_parser = subparsers.add_parser("case", help="run one whole-report case")
    case_parser.add_argument("case_id", choices=(*CONTROLLED_CASE_IDS, *LIVE_CASE_IDS))
    case_parser.add_argument(
        "--tier", choices=("controlled", "live"), default="controlled"
    )
    case_parser.add_argument("--repetitions", type=int, default=CONTROLLED_REPETITIONS)
    suite_parser = subparsers.add_parser("suite", help="run the controlled suite")
    suite_parser.add_argument(
        "--tier", choices=("controlled", "live"), default="controlled"
    )
    suite_parser.add_argument("--repetitions", type=int, default=CONTROLLED_REPETITIONS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI for list, case, and suite; report bodies never go to stdout."""
    options = build_parser().parse_args(argv)
    try:
        if options.command == "list":
            print("Controlled whole-report cases (network-zero):")
            for case in controlled_cases():
                print(f"  {case.case_id}: {case.title}")
            print("Live cases: declared only; no live runner exists")
            for case_id in LIVE_CASE_IDS:
                print(f"  {case_id}")
            return 0
        if options.command == "case":
            result = run_case(
                options.case_id,
                tier=options.tier,
                repetitions=options.repetitions,
            )
            print(
                f"Case {result.case_id}: "
                f"{'accepted' if result.accepted else 'failed'}; "
                f"coverage {result.mean_coverage:.2f}; "
                f"judge {result.mean_judge_score:.2f}"
            )
            print(f"Artifact: {result.artifact_path}")
            print("Network: zero (scripted dependencies only)")
            return 0 if result.accepted else 1
        result = run_suite(
            tier=options.tier,
            repetitions=options.repetitions,
        )
        for case in result.cases:
            print(
                f"{case.case_id}: "
                f"{'accepted' if case.accepted else 'failed'}; "
                f"coverage {case.mean_coverage:.2f}; "
                f"judge {case.mean_judge_score:.2f}"
            )
        print(
            f"Suite: {'accepted' if result.accepted else 'failed'} "
            f"({result.repetitions} repetitions per case)"
        )
        print(f"Artifact: {result.artifact_path}")
        print("Network: zero (scripted dependencies only)")
        return 0 if result.accepted else 1
    except (KeyError, RuntimeError, ValueError) as error:
        print(f"error: {error}")
        return 2


__all__ = [
    "CASE_REGISTRY_VERSION",
    "CONTROLLED_REPETITIONS",
    "DEFAULT_OUTPUT_DIRECTORY",
    "LIVE_TIER_NOT_RUN",
    "build_judge_metadata",
    "build_parser",
    "main",
    "run_case",
    "run_controlled_suite",
    "run_suite",
]
