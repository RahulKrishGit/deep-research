"""Offline whole-report campaign runner and artifact writer."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
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
    semantic_review_summary,
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
    ReplayCaseResult,
    ReplayRepetitionResult,
    ReplaySuiteResult,
    WholeReportJudgeScore,
)
from deep_research.e2e_evaluation.replay import (
    expectation_failures,
    network_denied,
    run_replay_scenario,
)
from deep_research.e2e_evaluation.replay_matrix import (
    REPLAY_CASE_MANIFEST,
    REPLAY_CASE_MANIFEST_VERSION,
    REPLAY_CASE_VERSION,
    ReplayCaseEntry,
    scenario_by_id,
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

# The two harnesses a controlled suite can run. The tier is "controlled"
# either way; which agents ran is a separate axis, and it is the axis a
# reader has to be told about before reading any result.
REAL_AGENT_MODE = "real-agent"
GRAPH_HISTORICAL_MODE = "graph-historical"
SUITE_MODES = (REAL_AGENT_MODE, GRAPH_HISTORICAL_MODE)
# A different filename from the legacy suite's ``suite.json``: the two modes
# share an output directory, and one harness's evidence must never overwrite
# the other's.
REPLAY_SUITE_FILENAME = "replay-suite.json"
_REPLAY_STORAGE_DIRECTORY = "replay"

_AS_OF_PREFIX = "**As of:**"
_REFERENCE_LINE = re.compile(r"^(\d+)\. (.*)$")
_CITATION = re.compile(r"\[(\d+)\]")
_CITATION_RUN = re.compile(r"(?:\[\d+\]){2,}")


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
        # The terminal semantic review the run recorded, read into the harness's
        # own vocabulary. ``None`` stays reserved for a repetition that holds no
        # review at all; a run whose review could not be made records the
        # incomplete status it has, so "no judgement" is visible as itself
        # rather than as a missing field a reader might read as success.
        semantic_review=(
            None
            if state.report_review is None
            else semantic_review_summary(state.report_review)
        ),
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


def canonical_report_fingerprint(report: str) -> str:
    """The published report's hash, over the part of it the reader was shown.

    Two things about a published report are facts about the *session* that
    made it rather than about the report: the ``As of`` clock read, and the
    ordinal each source was given, which is the order that session's reads
    were recorded in. Read identity is session-scoped by the product's own
    contract, so two repetitions of one fixture cite the same sources
    numbered in whichever order their own reads landed — measured here, that
    renumbering happens in fifteen of the eighteen rows. Hashing the rendered
    text as it stands would report a deterministic harness as non-deterministic
    on five sixths of the matrix.

    So the hash is taken over the canonical form: the clock read dropped, and
    every reference renumbered by its own label. What remains comparable is
    which sources the reader was shown against which sentences, so a citation
    set that gained, lost or moved a source still differs here.
    """
    body: list[str] = []
    references: list[tuple[str, str]] = []
    for line in report.splitlines():
        if line.startswith(_AS_OF_PREFIX):
            continue
        match = (
            _REFERENCE_LINE.match(line)
            if references or line[:1].isdigit()
            else None
        )
        if match is not None:
            references.append((match.group(1), match.group(2)))
            continue
        body.append(line)
    canonical = {
        number: index
        for index, (number, _label) in enumerate(
            sorted(references, key=lambda item: item[1]), start=1
        )
    }

    def _sort_run(run: re.Match[str]) -> str:
        markers = _CITATION.findall(run.group(0))
        return "".join(f"[{marker}]" for marker in sorted(markers, key=int))

    rewritten = [
        _CITATION_RUN.sub(
            _sort_run,
            _CITATION.sub(
                lambda hit: f"[{canonical.get(hit.group(1), int(hit.group(1)))}]",
                line,
            ),
        )
        for line in body
    ]
    listing = sorted(
        (f"{canonical[number]}. {label}" for number, label in references),
        key=lambda line: int(line.split(".", 1)[0]),
    )
    return hashlib.sha256("\n".join(rewritten + listing).encode("utf-8")).hexdigest()


def _replay_repetition(
    entry: ReplayCaseEntry, repetition: int, *, storage: Path
) -> ReplayRepetitionResult:
    """Run one declared row once, with the socket layer denied.

    The guard is not decoration: it is what turns "network-zero" from a claim
    about the fixture into a recorded fact about the run, and the attempts it
    records are carried into the result rather than asserted and dropped.
    """
    session_id = f"replay-{entry.case_id}-r{repetition}"
    with network_denied() as attempts:
        run = run_replay_scenario(
            scenario_by_id(entry.case_id),
            root=storage,
            session_id=session_id,
            repetition=repetition,
        )
    return ReplayRepetitionResult(
        case_id=entry.case_id,
        repetition=repetition,
        session_id=run.session_id,
        terminal_quality=run.quality_status,
        exit_code=run.exit_code,
        expectation_failures=expectation_failures(run),
        answered_target_ids=run.answered_target_ids(),
        network_attempts=list(attempts),
        report_fingerprint=canonical_report_fingerprint(run.report),
    )


def _replay_case_result(
    entry: ReplayCaseEntry, repetitions: Sequence[ReplayRepetitionResult]
) -> ReplayCaseResult:
    """One row's verdict: what it produced, and whether that was its result.

    Determinism is asserted over the whole outcome — the exit code, the
    terminal quality, the answered targets and the report — not over the exit
    code alone, which two runs can agree on while publishing different
    reports.
    """
    outcomes = {
        (
            item.exit_code,
            item.terminal_quality,
            tuple(sorted(item.answered_target_ids)),
            item.report_fingerprint,
        )
        for item in repetitions
    }
    return ReplayCaseResult(
        case_id=entry.case_id,
        version=entry.version,
        expected_product_result=entry.expected_product_result,
        decisive_assertion=entry.decisive_assertion,
        repetitions=list(repetitions),
        deterministic=len(outcomes) == 1,
        passed=all(not item.expectation_failures for item in repetitions),
    )


def run_replay_suite(
    *,
    tier: str = "controlled",
    repetitions: int = CONTROLLED_REPETITIONS,
    output_directory: str | Path | None = None,
) -> ReplaySuiteResult:
    """Run every row of the real-agent matrix and write the suite artifact.

    This is the real thing: six production agents through the real compiled
    graph, the real reviewer, renderer and publisher, with only the external
    boundaries scripted and the socket layer denied for every repetition. The
    inventory is ``REPLAY_CASE_MANIFEST``, so the suite covers every row the
    versioned manifest declares rather than a hardcoded set.
    """
    if tier == "live":
        raise RuntimeError(LIVE_TIER_NOT_RUN)
    if tier != "controlled":
        raise ValueError("tier must be controlled or live")
    if repetitions != CONTROLLED_REPETITIONS:
        raise ValueError("controlled replay suite requires exactly 3 repetitions")
    root = Path(output_directory or DEFAULT_OUTPUT_DIRECTORY)
    results = [
        _replay_case_result(
            entry,
            [
                _replay_repetition(
                    entry,
                    repetition,
                    # Each repetition gets its own storage root, so the
                    # repetitions are isolated from one another and from the
                    # legacy mode's per-case directories.
                    storage=(
                        root
                        / _REPLAY_STORAGE_DIRECTORY
                        / entry.case_id
                        / f"repetition-{repetition}"
                    ),
                )
                for repetition in range(1, repetitions + 1)
            ],
        )
        for entry in REPLAY_CASE_MANIFEST
    ]
    attempts = sum(
        len(item.network_attempts)
        for case in results
        for item in case.repetitions
    )
    metadata: dict[str, JsonValue] = {
        "graph_revision": graph_revision_value(),
        "manifest_version": REPLAY_CASE_MANIFEST_VERSION,
        "case_version": REPLAY_CASE_VERSION,
        "case_ids": [case.case_id for case in results],
        # Written from the recorded attempts, not from the harness's intent:
        # an artifact claiming "zero" beside a non-zero attempt count would be
        # the same silent substitution this suite exists to prevent.
        "network": "zero" if not attempts else "attempted",
        "network_attempts": attempts,
    }
    suite = ReplaySuiteResult(
        campaign_id=(
            "controlled-replay-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + uuid4().hex[:8]
        ),
        tier="controlled",
        mode=REAL_AGENT_MODE,
        manifest_version=REPLAY_CASE_MANIFEST_VERSION,
        case_version=REPLAY_CASE_VERSION,
        repetitions=repetitions,
        cases=results,
        # A suite whose runs reached the network is not a suite that passed,
        # however clean every row's own result was.
        accepted=all(case.passed for case in results) and attempts == 0,
        metadata=metadata,
    )
    root.mkdir(parents=True, exist_ok=True)
    artifact = root / REPLAY_SUITE_FILENAME
    suite = suite.model_copy(update={"artifact_path": str(artifact)})
    artifact.write_text(suite.model_dump_json(indent=2), encoding="utf-8")
    return suite


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
    suite_parser.add_argument(
        "--mode",
        choices=SUITE_MODES,
        default=REAL_AGENT_MODE,
        help=(
            "which controlled harness to run: the real agents over the replay "
            "matrix (default), or the historical scripted doubles"
        ),
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
        if options.mode == GRAPH_HISTORICAL_MODE:
            historical = run_suite(
                tier=options.tier,
                repetitions=options.repetitions,
            )
            for line in graph_historical_suite_lines(historical):
                print(line)
            return 0 if historical.accepted else 1
        suite = run_replay_suite(
            tier=options.tier,
            repetitions=options.repetitions,
        )
        for line in real_agent_suite_lines(suite):
            print(line)
        return 0 if suite.accepted else 1
    except (KeyError, RuntimeError, ValueError) as error:
        print(f"error: {error}")
        return 2


def real_agent_suite_lines(suite: ReplaySuiteResult) -> list[str]:
    """The real-agent suite's own output, one line each."""
    lines = [
        (
            f"{case.case_id}: {'passed' if case.passed else 'failed'} "
            f"({len(case.repetitions)} repetitions, "
            f"{'deterministic' if case.deterministic else 'NON-deterministic'})"
        )
        for case in suite.cases
    ]
    accepted = sum(1 for case in suite.cases if case.passed)
    lines.append(
        f"Suite: {'accepted' if suite.accepted else 'failed'} "
        f"({suite.repetitions} repetitions per case, "
        f"{accepted}/{len(suite.cases)} rows)"
    )
    lines.append(f"Artifact: {suite.artifact_path}")
    lines.append(network_line(suite))
    return lines


def graph_historical_suite_lines(result: CampaignResult) -> list[str]:
    """The historical suite's own output, one line each."""
    lines = [
        (
            f"{case.case_id}: {'accepted' if case.accepted else 'failed'}; "
            f"coverage {case.mean_coverage:.2f}; "
            f"judge {case.mean_judge_score:.2f}"
        )
        for case in result.cases
    ]
    lines.append(
        f"Suite: {'accepted' if result.accepted else 'failed'} "
        f"({result.repetitions} repetitions per case)"
    )
    lines.append(f"Artifact: {result.artifact_path}")
    lines.append("Network: zero (scripted dependencies only)")
    return lines


def network_line(suite: ReplaySuiteResult) -> str:
    """What the socket guard recorded, as the run's own evidence.

    Read from the repetitions rather than from the run's configuration: the
    number is a count of connections the product tried to open, so a suite
    that reached the network cannot print the zero that would have made it
    look acceptable.
    """
    attempts = sum(
        len(item.network_attempts)
        for case in suite.cases
        for item in case.repetitions
    )
    if attempts:
        return (
            f"Network: NOT zero (socket layer denied; "
            f"{attempts} attempts recorded)"
        )
    return "Network: zero (socket layer denied; 0 attempts recorded)"


__all__ = [
    "CASE_REGISTRY_VERSION",
    "CONTROLLED_REPETITIONS",
    "DEFAULT_OUTPUT_DIRECTORY",
    "GRAPH_HISTORICAL_MODE",
    "LIVE_TIER_NOT_RUN",
    "REAL_AGENT_MODE",
    "REPLAY_SUITE_FILENAME",
    "SUITE_MODES",
    "build_judge_metadata",
    "build_parser",
    "canonical_report_fingerprint",
    "graph_historical_suite_lines",
    "main",
    "network_line",
    "real_agent_suite_lines",
    "run_case",
    "run_controlled_suite",
    "run_replay_suite",
    "run_suite",
]
