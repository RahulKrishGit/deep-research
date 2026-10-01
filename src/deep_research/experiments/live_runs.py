"""Paired live runs (latency audit §7 tier 4; latency plan X1 baseline, X2, X3).

``run`` makes one research session for one question and arm, through
``run_research`` with the arm's request-scoped config overrides and a fresh,
empty memory of its own, so no run reads what an earlier run stored. It writes
``events.jsonl`` as the run goes and, at the end, ``run.json`` (the metrics
below) and a copy of the run's quality record into
``<out>/<question>-<arm>-<repetition>/``. ``--capture`` also binds a stage
capture there, for the X1 stage replay.

``compare`` applies the latency plan's pre-registered criteria (Task 17) to a
treatment arm against the baseline arm: accuracy first, then time.

    python -m deep_research.experiments.live_runs run --question tamil \\
        --arm baseline --repetition 1 --out output/latency-experiments/live --capture
    python -m deep_research.experiments.live_runs compare \\
        --out output/latency-experiments/live --treatment x2 --stage researcher
    python -m deep_research.experiments.live_runs compare-suite \\
        --control CONTROL/results.json --treatment TREATMENT/results.json

Every run is paid. ``run`` refuses to start when any minute of the next 50
falls inside DeepSeek's peak hours, 01:00-04:00 or 06:00-10:00 UTC.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

QUESTIONS: dict[str, str] = {
    # Figure-heavy: the audit's Tamil run (box-office figures, one 16-figure finding).
    "tamil": "what are the best films in tamil?",
    # Local recommendations: the audit's Latte run.
    "latte": "Where can we get the best tasting Lattes in san Jose",
    # Prose: causes and interpretations, few figures.
    "rome": "Why did the Roman Republic fall?",
}

PEAK_HOURS_UTC: tuple[tuple[int, int], ...] = ((1, 4), (6, 10))
PEAK_LOOKAHEAD_MINUTES = 50

# Pre-registered tolerances (latency plan Task 17, "Pass criteria"). A
# question's control spread is used when it has two or more control runs;
# these floors stand in for it otherwise, and bound it from below.
REVIEW_MARGIN_FLOOR = 0.03
KEPT_RELATIVE_FLOOR = 0.15
REFUSED_ABSOLUTE_FLOOR = 2
REFUSED_RELATIVE = 0.25
DROPPED_RATE_MARGIN = 0.05
SUITE_CASE_MARGIN_FLOOR = 0.05


def peak_ahead(now: datetime, minutes: int = PEAK_LOOKAHEAD_MINUTES) -> bool:
    """Whether any minute from ``now`` to ``now + minutes`` is a peak minute."""
    moment = now.astimezone(timezone.utc)
    return any(
        start <= (moment + timedelta(minutes=offset)).hour < end
        for offset in range(minutes + 1)
        for start, end in PEAK_HOURS_UTC
    )


def _when(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def stage_seconds(events: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Each graph node's wall time, summed over its passes, from its events."""
    started: dict[str, datetime] = {}
    totals: dict[str, float] = {}
    for event in events:
        node = (event.get("metadata") or {}).get("node")
        if not isinstance(node, str):
            continue
        if event["event_type"] == "graph.node.started":
            started[node] = _when(event["timestamp"])
        elif event["event_type"] == "graph.node.completed" and node in started:
            elapsed = (_when(event["timestamp"]) - started.pop(node)).total_seconds()
            totals[node] = totals.get(node, 0.0) + elapsed
    return {node: round(seconds, 3) for node, seconds in totals.items()}


def lock_waits(events: Sequence[Mapping[str, Any]]) -> dict[str, float | int]:
    """How long tool calls waited for the run's tool gate (latency audit O4, O8)."""
    waits = sorted(
        float(event["metadata"]["lock_wait_s"])
        for event in events
        if event["event_type"] == "researcher.tool_call"
        and "lock_wait_s" in (event.get("metadata") or {})
    )
    if not waits:
        return {"timed_tool_calls": 0}
    return {
        "timed_tool_calls": len(waits),
        "lock_wait_s_median": round(statistics.median(waits), 3),
        "lock_wait_s_p95": waits[math.ceil(0.95 * len(waits)) - 1],
        "lock_wait_s_max": waits[-1],
    }


def quality_metrics(record: Mapping[str, Any]) -> dict[str, Any]:
    """The accuracy side of one run, read from its ``-quality.json``."""
    quality = record["quality"]
    review = record.get("review") or {}
    required = set(quality["required_target_ids"])
    answered = required & set(quality["answered_target_ids"])
    judged = (
        quality["verified_findings"] + quality["corrected_findings"]
        + quality["quoted_findings"] + quality["dropped_findings"]
    )
    return {
        "session_status": record["session_status"],
        "quality_status": record["quality_status"],
        "hard_failures": len(quality["hard_failures"]),
        "unjudged_sentences": len(quality["unjudged_sentences"]),
        "unresolved_citations": quality["unresolved_citations"],
        "review_status": review.get("status"),
        "review_mean_score": review.get("mean_score"),
        # The review's own flag (``ReviewDefect.material``, written by
        # ``agents/report.py``), not a re-derivation from the severity.
        "material_defects": sum(
            1
            for defect in review.get("defects") or ()
            if defect.get("material") and defect.get("resolution") != "resolved"
        ),
        "required_coverage": len(answered) / len(required) if required else 1.0,
        "not_found": len(record.get("not_found") or ()),
        "verified_plus_corrected": quality["verified_findings"] + quality["corrected_findings"],
        "dropped_findings": quality["dropped_findings"],
        "dropped_rate": quality["dropped_findings"] / judged if judged else 0.0,
        "cited_sources": quality["cited_sources"],
        "publishers": len({
            source["publisher"] for source in record.get("sources") or () if source.get("publisher")
        }),
        "refused_sentences": quality["refused_sentences"],
    }


def output_speeds(record: Mapping[str, Any]) -> dict[str, float]:
    """Each stage's median output tokens per second over its provider calls.

    Read from the quality record's ``telemetry.stages[].call_records`` (latency
    plan Task 4). It sits beside the time verdict: a stage that is slower only
    because the provider streamed more slowly that hour shows it here.
    """
    speeds: dict[str, float] = {}
    for stage in (record.get("telemetry") or {}).get("stages") or ():
        rates = [
            call["output_tokens"] / call["seconds"]
            for call in stage.get("call_records") or ()
            if call.get("seconds") and call.get("output_tokens")
        ]
        if rates and stage.get("agent"):
            speeds[stage["agent"]] = round(statistics.median(rates), 1)
    return speeds


def _merged(base: Mapping[str, Any], extra: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in extra.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _merged(merged[key], value)
        else:
            merged[key] = value
    return merged


Research = Callable[..., Awaitable[Any]]


def _write_crashed_run(
    directory: Path,
    *,
    question_id: str,
    arm: str,
    repetition: int,
    overrides: Mapping[str, Any],
    error: Exception,
) -> None:
    """Timing-only ``run.json`` for a run whose research raised (Task 17 review).

    It carries no quality record and no duration, so ``compare`` counts it as a
    failed treatment and never as a control: a crash fails closed instead of
    dropping its question from the verdict. The stage times are whatever
    ``events.jsonl`` recorded before the crash.
    """
    try:
        lines = (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()
        stages = stage_seconds([json.loads(line) for line in lines if line.strip()])
    except (OSError, ValueError, KeyError, TypeError):
        stages = {}
    crashed = {
        "question_id": question_id,
        "question": QUESTIONS[question_id],
        "arm": arm,
        "repetition": repetition,
        "overrides": dict(overrides),
        "session_id": None,
        "status": "failed",
        "report_path": None,
        "quality_path": None,
        "error": f"{type(error).__name__}: {error}",
        "metrics": {"seconds": None, "stage_seconds": stages},
    }
    try:
        (directory / "run.json").write_text(
            json.dumps(crashed, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except OSError as write_error:  # never mask the research error being re-raised
        print(f"could not record the crashed run: {write_error}", file=sys.stderr)


async def run_one(
    *,
    question_id: str,
    arm: str,
    repetition: int,
    overrides: Mapping[str, Any],
    out: Path,
    capture: bool,
    research: Research,
    config_path: str = "config.yaml",
) -> Path:
    """One paid run; returns its ``run.json``. Never reuses a run's directory."""
    from deep_research.observability import bind_stage_capture

    directory = out / f"{question_id}-{arm}-{repetition}"
    directory.mkdir(parents=True, exist_ok=False)
    memory = directory / "memory"
    run_overrides = _merged(
        overrides,
        {
            "memory": {
                "long_term": {"persist_directory": str(memory / "chroma")},
                "procedural": {"strategies_path": str(memory / "strategies.json")},
            }
        },
    )
    events_file = directory / "events.jsonl"

    def record(event: Any) -> None:
        with events_file.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n")

    async def go() -> Any:
        return await research(
            QUESTIONS[question_id],
            config_path=config_path,
            config_overrides=run_overrides,
            event_handler=record,
        )

    try:
        if capture:
            with bind_stage_capture(directory / "capture", nodes=["evidence_verifier"]):
                outcome = await go()
        else:
            outcome = await go()
    except Exception as error:
        # A crashed paid run must stay visible: without a run.json it would
        # silently drop out of ``compare``. The original error is re-raised.
        _write_crashed_run(
            directory,
            question_id=question_id,
            arm=arm,
            repetition=repetition,
            overrides=overrides,
            error=error,
        )
        raise

    events = [event.model_dump(mode="json") for event in outcome.state.events]
    metrics: dict[str, Any] = {
        "seconds": outcome.duration_seconds,
        "stage_seconds": stage_seconds(events),
        **lock_waits(events),
    }
    result = directory / "run.json"
    record: dict[str, Any] = {
        "question_id": question_id,
        "question": QUESTIONS[question_id],
        "arm": arm,
        "repetition": repetition,
        "overrides": dict(overrides),
        "session_id": outcome.session_id,
        "status": outcome.status,
        "report_path": outcome.report_path,
        "quality_path": outcome.quality_path,
        "metrics": metrics,
    }

    def write() -> None:
        result.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")

    # The timing is written first: a quality record that cannot be read (a run
    # that took no quality snapshot publishes ``"quality": {}``) must not lose
    # what a paid run measured. Its accuracy metrics are then merged in, or the
    # reason they could not be is recorded, and ``compare`` treats a run
    # without them as no control and a failed treatment.
    write()
    if outcome.quality_path is not None:
        try:
            quality = json.loads(Path(outcome.quality_path).read_text(encoding="utf-8"))
            (directory / "quality.json").write_text(
                json.dumps(quality, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            metrics["output_tokens_per_s"] = output_speeds(quality)
            metrics.update(quality_metrics(quality))
        except (OSError, ValueError, KeyError, TypeError) as error:
            metrics["quality_error"] = f"{type(error).__name__}: {error}"
        write()
    return result


def load_runs(out: Path) -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(out.glob("*/run.json"))
    ]


def _spread(values: Sequence[float]) -> float:
    return max(values) - min(values) if len(values) > 1 else 0.0


def _usable(metrics: Mapping[str, Any]) -> bool:
    """A run that published a quality record and has a duration."""
    return "quality_status" in metrics and metrics.get("seconds") is not None


def compare(
    runs: Sequence[Mapping[str, Any]],
    *,
    treatment: str,
    stage: str,
    control: str = "baseline",
) -> dict[str, Any]:
    """The pre-registered verdict for one treatment arm (latency plan Task 17).

    Accuracy: every check below on every question that has both arms. Time:
    the targeted stage is faster than the control mean on at least two of the
    questions, and the mean ratio of end-to-end seconds is below 1.
    """
    by_question: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for run in runs:
        metrics = run["metrics"]
        if run["arm"] == control and not _usable(metrics):
            continue  # a control that published nothing measures nothing
        by_question.setdefault(run["question_id"], {}).setdefault(run["arm"], []).append(metrics)
    paired = {
        question: arms for question, arms in by_question.items()
        if arms.get(control) and arms.get(treatment)
    }
    if not paired:
        raise ValueError(f"no question has both a {control} and a {treatment} run")
    multi = [arms[control] for arms in by_question.values() if len(arms.get(control, ())) > 1]
    review_margin = max([
        REVIEW_MARGIN_FLOOR,
        *(
            _spread([c["review_mean_score"] for c in cs if c["review_mean_score"] is not None] or [0.0])
            for cs in multi
        ),
    ])
    kept_margin = max([
        KEPT_RELATIVE_FLOOR,
        *(
            _spread([c["verified_plus_corrected"] for c in cs])
            / max(1, max(c["verified_plus_corrected"] for c in cs))
            for cs in multi
        ),
    ])
    verdict: dict[str, Any] = {
        "control": control, "treatment": treatment, "stage": stage,
        "review_margin": round(review_margin, 4), "kept_margin": round(kept_margin, 4),
        "questions": {},
    }
    accuracy_ok = True
    faster: list[bool] = []
    ratios: list[float] = []
    for question, arms in sorted(paired.items()):
        controls = arms[control]
        scores = [c["review_mean_score"] for c in controls if c["review_mean_score"] is not None]
        for number, run in enumerate(arms[treatment], 1):
            if not _usable(run):
                accuracy_ok = False
                faster.append(False)
                ratios.append(math.inf)
                verdict["questions"][f"{question}-{number}"] = {"checks": {"completed": False}}
                continue
            all_accepted = all(c["quality_status"] == "accepted" for c in controls)
            refused_cap = max(c["refused_sentences"] for c in controls)
            checks = {
                "completed": run["session_status"] == "completed",
                "no_hard_failures": run["hard_failures"] == 0,
                "no_unjudged_sentences": run["unjudged_sentences"] == 0,
                "no_unresolved_citations": run["unresolved_citations"] == 0,
                "review_scored": run["review_status"] == "scored",
                "accepted_if_controls_were": (not all_accepted) or run["quality_status"] == "accepted",
                "review_score": run["review_mean_score"] is not None
                and (not scores or run["review_mean_score"] >= min(scores) - review_margin),
                "material_defects": run["material_defects"] <= max(c["material_defects"] for c in controls),
                "required_coverage": run["required_coverage"] >= min(c["required_coverage"] for c in controls),
                "not_found": run["not_found"] <= max(c["not_found"] for c in controls),
                "verified_plus_corrected": run["verified_plus_corrected"]
                >= (1 - kept_margin) * min(c["verified_plus_corrected"] for c in controls),
                "cited_sources": run["cited_sources"]
                >= (1 - kept_margin) * min(c["cited_sources"] for c in controls),
                "publishers": run["publishers"]
                >= (1 - kept_margin) * min(c["publishers"] for c in controls),
                "refused_sentences": run["refused_sentences"]
                <= refused_cap + max(REFUSED_ABSOLUTE_FLOOR, math.ceil(REFUSED_RELATIVE * refused_cap)),
                "dropped_rate": run["dropped_rate"]
                <= max(c["dropped_rate"] for c in controls) + DROPPED_RATE_MARGIN,
            }
            control_stage = statistics.mean(c["stage_seconds"].get(stage, 0.0) for c in controls)
            stage_time = run["stage_seconds"].get(stage, 0.0)
            ratio = run["seconds"] / statistics.mean(c["seconds"] for c in controls)
            faster.append(stage_time < control_stage)
            ratios.append(ratio)
            accuracy_ok = accuracy_ok and all(checks.values())
            control_speeds = [
                c["output_tokens_per_s"][stage]
                for c in controls
                if stage in (c.get("output_tokens_per_s") or {})
            ]
            verdict["questions"][f"{question}-{number}"] = {
                "checks": checks,
                "stage_seconds": stage_time,
                "control_stage_seconds": round(control_stage, 3),
                "seconds_ratio": round(ratio, 4),
                # Beside the time verdict, not part of it (review P2-6): the
                # provider's own speed for this stage in each arm.
                "stage_output_tokens_per_s": (run.get("output_tokens_per_s") or {}).get(stage),
                "control_stage_output_tokens_per_s": (
                    round(statistics.mean(control_speeds), 1) if control_speeds else None
                ),
            }
    needed = min(2, len(faster))
    verdict["accuracy_passed"] = accuracy_ok
    verdict["stage_faster_on"] = sum(faster)
    verdict["mean_seconds_ratio"] = round(statistics.mean(ratios), 4)
    verdict["time_passed"] = sum(faster) >= needed and statistics.mean(ratios) < 1
    verdict["passed"] = accuracy_ok and verdict["time_passed"]
    return verdict


def suite_verdict(control: Mapping[str, Any], treatment: Mapping[str, Any]) -> dict[str, Any]:
    """The tier-3 gate (latency plan Task 17; run in Tasks 23 and 24): two
    ``results.json`` of one agent.

    The treatment must pass by the harness's own rule (status ``REVIEW
    REQUIRED``: every case's average at least 0.80, every repetition at least
    0.65, every hard gate passed), and no case's average may fall below the
    control's by more than the larger of 0.05 and the spread of that case's
    own control repetitions.
    """
    controls = {case["case_id"]: case for case in control["cases"]}
    cases: dict[str, Any] = {}
    within = True
    for case in treatment["cases"]:
        reference = controls.get(case["case_id"])
        if reference is None:
            continue
        scores = [
            repetition["aggregate_quality"]
            for repetition in reference["repetitions"]
            if repetition.get("aggregate_quality") is not None
        ]
        margin = max(SUITE_CASE_MARGIN_FLOOR, _spread(scores) if scores else 0.0)
        ok = (
            case["average_quality"] is not None
            and reference["average_quality"] is not None
            and case["average_quality"] >= reference["average_quality"] - margin
        )
        within = within and ok
        cases[case["case_id"]] = {
            "control_average": reference["average_quality"],
            "treatment_average": case["average_quality"],
            "margin": round(margin, 4),
            "within_margin": ok,
            "treatment_passed": case["passed"],
        }
    harness_passed = treatment["status"] == "REVIEW REQUIRED" and all(
        case["passed"] for case in treatment["cases"]
    )
    return {
        "cases": cases,
        "harness_passed": harness_passed,
        "within_control": within and bool(cases),
        "passed": harness_passed and within and bool(cases),
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    research: Research | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> int:
    parser = argparse.ArgumentParser(prog="python -m deep_research.experiments.live_runs")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="one paid research run")
    run.add_argument("--question", required=True, choices=sorted(QUESTIONS))
    run.add_argument("--arm", required=True)
    run.add_argument("--repetition", required=True, type=int)
    run.add_argument("--out", required=True)
    run.add_argument("--override", default="{}", help="config overrides, as a JSON object")
    run.add_argument("--capture", action="store_true")
    run.add_argument("--config", default="config.yaml")
    comparison = commands.add_parser("compare", help="judge a treatment arm against the baseline")
    comparison.add_argument("--out", required=True)
    comparison.add_argument("--treatment", required=True)
    comparison.add_argument("--stage", required=True)
    comparison.add_argument("--control", default="baseline")
    suite = commands.add_parser("compare-suite", help="the tier-3 gate on two results.json")
    suite.add_argument("--control", required=True)
    suite.add_argument("--treatment", required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "compare-suite":
        verdict = suite_verdict(
            json.loads(Path(arguments.control).read_text(encoding="utf-8")),
            json.loads(Path(arguments.treatment).read_text(encoding="utf-8")),
        )
        print(json.dumps(verdict, indent=1))
        return 0 if verdict["passed"] else 1
    if arguments.command == "compare":
        result = compare(
            load_runs(Path(arguments.out)),
            treatment=arguments.treatment,
            stage=arguments.stage,
            control=arguments.control,
        )
        print(json.dumps(result, indent=1))
        return 0 if result["passed"] else 1
    if peak_ahead(now()):
        print("REFUSE: DeepSeek peak hours start within 50 minutes; nothing was run.")
        return 2
    if research is None:
        from deep_research.main import run_research

        research = run_research
    path = asyncio.run(
        run_one(
            question_id=arguments.question,
            arm=arguments.arm,
            repetition=arguments.repetition,
            overrides=json.loads(arguments.override),
            out=Path(arguments.out),
            capture=arguments.capture,
            research=research,
            config_path=arguments.config,
        )
    )
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
