"""Terminal rendering and durable JSON artifacts for evaluation results.

A pure rendering/serialization layer: it reads ``ExperimentResult`` /
``SuiteResult`` (Task 4) and the local case registry (Task 9), and produces
either a list of terminal lines or a JSON file on disk. It never rounds a
score anywhere but ``format_score``, and it never prints or writes a raw
provider payload, a raw exception, or a secret -- the data it is handed is
already redacted and typed by the time it reaches this module; this module's
own job is to not undo that.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from deep_research.evaluation.cases import cases_for
from deep_research.evaluation.config import dataset_name
from deep_research.evaluation.models import (
    AGENT_NAMES,
    AgentName,
    CaseResult,
    ExperimentResult,
    RepetitionResult,
    SuiteResult,
    cli_agent_name,
)

_LABEL_WIDTH = 13


def format_score(value: float | None) -> str:
    """Render a score to two decimals, or ``"n/a"`` for a missing score.

    This is the only place in the reporting module a score is rounded:
    every caller here compares/reads scores unrounded and only formats
    them for display through this function.
    """
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def _title(agent_name: AgentName) -> str:
    """``source-evaluator`` -> ``Source Evaluator``."""
    kebab = cli_agent_name(agent_name)
    return " ".join(word.capitalize() for word in kebab.split("-"))


def _label(text: str) -> str:
    return text.ljust(_LABEL_WIDTH)


def _all_repetitions(result: ExperimentResult) -> list[RepetitionResult]:
    return [
        repetition for case in result.cases for repetition in case.repetitions
    ]


def _gate_counts(repetitions: list[RepetitionResult]) -> tuple[int, int]:
    total = sum(len(rep.gates.results) for rep in repetitions)
    passed = sum(
        1
        for rep in repetitions
        for gate_result in rep.gates.results
        if gate_result.passed
    )
    return passed, total


def _results_path(result: ExperimentResult) -> str:
    return (
        f"output/evaluations/{cli_agent_name(result.agent_name)}/"
        f"{result.experiment_name}/results.json"
    )


def _judge_score(repetition: RepetitionResult) -> float | None:
    judge = repetition.judge
    if judge is None:
        return None
    return judge.judge_quality


def _review_lines(cases: list[CaseResult]) -> list[str]:
    lines = ["Review:"]
    longest = max((len(case.case_id) for case in cases), default=0)
    for case in cases:
        url = case.lowest_scoring_trace_url or "n/a"
        lines.append(f"  {case.case_id.ljust(longest + 3)}{url}")
    return lines


def _failures_lines(cases: list[CaseResult]) -> list[str]:
    lines = ["", "Failures:"]
    for case in cases:
        if case.passed:
            continue
        for repetition in case.repetitions:
            if repetition.gates.passed:
                continue
            failed_ids = ", ".join(repetition.gates.failed_ids)
            lines.append(
                f"  {case.case_id} repetition {repetition.repetition}: "
                f"{failed_ids}"
            )
    return lines


def _verbose_lines(cases: list[CaseResult]) -> list[str]:
    lines = ["", "Verbose:"]
    for case in cases:
        for repetition in case.repetitions:
            gates_passed, gates_total = _gate_counts([repetition])
            lines.append(
                f"  {case.case_id} repetition {repetition.repetition}: "
                f"gates {gates_passed}/{gates_total}, "
                f"deterministic {format_score(repetition.deterministic_quality)}, "
                f"judge {format_score(_judge_score(repetition))}, "
                f"aggregate {format_score(repetition.aggregate_quality)}"
            )
            judge = repetition.judge
            if judge is not None and judge.status == "judge_not_run":
                lines.append(f"    judge_not_run: {judge.not_run_reason}")
    return lines


def render_experiment(result: ExperimentResult, *, verbose: bool) -> list[str]:
    """The terminal summary for one experiment.

    ``verbose`` adds one line per repetition (ids, counts, and scores
    only -- never a prompt, a payload, or a raw exception) plus the
    ``judge_not_run`` reason where present.
    """
    repetitions = _all_repetitions(result)
    cases_passed = sum(1 for case in result.cases if case.passed)
    reps_completed = sum(1 for rep in repetitions if rep.completed)
    gates_passed, gates_total = _gate_counts(repetitions)

    lines = [f"{_title(result.agent_name)} - {result.tier}"]
    lines.append(f"{_label('Cases:')}{cases_passed}/{len(result.cases)} passed")
    lines.append(
        f"{_label('Repetitions:')}{reps_completed}/{len(repetitions)} completed"
    )
    lines.append(f"{_label('Hard gates:')}{gates_passed}/{gates_total} passed")
    lines.append(f"{_label('Mean score:')}{format_score(result.mean_quality)}")
    lines.append(f"{_label('Status:')}{result.status}")
    lines.append("")
    lines.append(f"{_label('Experiment:')}{result.experiment_url or 'n/a'}")
    lines.extend(_review_lines(result.cases))
    lines.append(f"Results: {_results_path(result)}")

    if result.status == "FAILED":
        lines.extend(_failures_lines(result.cases))

    if verbose:
        lines.extend(_verbose_lines(result.cases))

    return lines


def _suite_summary_path(result: SuiteResult) -> str:
    return f"output/evaluations/suite/{result.suite_id}/summary.json"


def render_suite(result: SuiteResult, *, verbose: bool) -> list[str]:
    """The terminal summary for a six-agent suite.

    One compact line per agent (``format_score`` is the only rounding
    site, same as ``render_experiment``), then the suite's overall status,
    then the summary artifact path, then each agent's experiment url --
    the fixed order a human skimming the terminal needs before any
    per-agent detail. ``verbose=True`` appends each agent's full
    ``render_experiment`` block, in the same order the suite ran them.
    """
    lines: list[str] = []
    for experiment in result.experiments:
        agent_title = _title(experiment.agent_name)
        lines.append(
            f"{agent_title:<18} {experiment.status:<22} "
            f"mean {format_score(experiment.mean_quality)}"
        )
    lines.append(f"Suite status: {result.status}")
    lines.append(f"Summary: {_suite_summary_path(result)}")
    for experiment in result.experiments:
        lines.append(
            f"{cli_agent_name(experiment.agent_name)}: "
            f"{experiment.experiment_url or 'n/a'}"
        )
    if verbose:
        for experiment in result.experiments:
            lines.append("")
            lines.extend(render_experiment(experiment, verbose=True))
    return lines


def render_listing(
    *, dataset_version: int, repetitions: Mapping[str, int]
) -> list[str]:
    """Every agent's controlled and live datasets and case ids."""
    lines: list[str] = []
    for agent_name in AGENT_NAMES:
        lines.append(cli_agent_name(agent_name))
        for tier in ("controlled", "live"):
            name = dataset_name(agent_name, tier, dataset_version)
            count = repetitions.get(tier)
            unit = "repetition" if count == 1 else "repetitions"
            lines.append(f"  {tier}: {name} ({count} {unit})")
            for case in cases_for(agent_name, tier):
                lines.append(f"    {case.case_id}")
    return lines


def write_experiment_artifact(result: ExperimentResult, *, root: Path) -> Path:
    """Write the strict, round-trippable ``results.json`` artifact."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / "results.json"
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return path


def write_suite_artifact(result: SuiteResult, *, root: Path) -> Path:
    """Write the strict, round-trippable ``summary.json`` artifact."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / "summary.json"
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return path
