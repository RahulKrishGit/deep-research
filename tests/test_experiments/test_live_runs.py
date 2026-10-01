"""Latency plan Task 17: paired live runs, their metrics and the verdict."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from deep_research.experiments.live_runs import (
    QUESTIONS,
    compare,
    load_runs,
    lock_waits,
    main,
    output_speeds,
    peak_ahead,
    quality_metrics,
    run_one,
    stage_seconds,
    suite_verdict,
)
from deep_research.observability import capture_node_input
from deep_research.utils.types import ResearchEvent, ResearchState


@pytest.mark.parametrize(
    ("hour", "minute", "day", "peak"),
    [
        (0, 9, 1, False),
        (0, 10, 1, True),
        (3, 59, 1, True),
        (4, 0, 1, False),
        (5, 9, 1, False),
        (5, 10, 1, True),
        (9, 59, 1, True),
        (10, 0, 1, False),
        (2, 0, 3, True),
    ],
)
def test_peak_hours_are_looked_for_fifty_minutes_ahead_every_day(
    hour: int, minute: int, day: int, peak: bool
) -> None:
    # 2026-10-01 is a Thursday and 2026-10-03 a Saturday.
    assert peak_ahead(datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)) is peak


def _quality(**quality: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "answered_target_ids": ["t1", "t2", "t3", "extra"],
        "required_target_ids": ["t1", "t2", "t3", "t4"],
        "hard_failures": [],
        "unjudged_sentences": [],
        "unresolved_citations": 0,
        "verified_findings": 30,
        "corrected_findings": 6,
        "quoted_findings": 10,
        "dropped_findings": 4,
        "cited_sources": 20,
        "refused_sentences": 3,
    }
    values.update(quality)
    return {
        "session_status": "completed",
        "quality_status": "accepted",
        "quality": values,
        "review": {
            "status": "scored",
            "mean_score": 0.81,
            "defects": [
                {"severity": "major", "material": True, "resolution": None},
                {"severity": "critical", "material": False, "resolution": "resolved"},
                {"severity": "minor", "material": False, "resolution": None},
            ],
        },
        "not_found": [{"target_id": "t4"}],
        "sources": [{"publisher": "a.test"}, {"publisher": "a.test"}, {"publisher": "b.test"}],
        "telemetry": {
            "stages": [
                {
                    "agent": "researcher",
                    "call_records": [
                        {"label": "page_extraction", "seconds": 10.0, "output_tokens": 2000},
                        {"label": "page_extraction", "seconds": 20.0, "output_tokens": 3000},
                        {"label": "extraction", "seconds": 0.0, "output_tokens": 0},
                    ],
                },
                {"agent": "planner", "call_records": []},
            ]
        },
    }


def test_the_accuracy_metrics_are_read_from_the_quality_record() -> None:
    assert quality_metrics(_quality()) == {
        "session_status": "completed",
        "quality_status": "accepted",
        "hard_failures": 0,
        "unjudged_sentences": 0,
        "unresolved_citations": 0,
        "review_status": "scored",
        "review_mean_score": 0.81,
        "material_defects": 1,
        "required_coverage": 0.75,
        "not_found": 1,
        "verified_plus_corrected": 36,
        "dropped_findings": 4,
        "dropped_rate": 0.08,
        "cited_sources": 20,
        "publishers": 2,
        "refused_sentences": 3,
    }


def test_each_stages_median_output_speed_is_read_from_its_call_records() -> None:
    assert output_speeds(_quality()) == {"researcher": 175.0}
    assert output_speeds({"quality": {}}) == {}


def _event(event_type: str, at: str, **metadata: Any) -> dict[str, Any]:
    return {"event_type": event_type, "timestamp": f"2026-10-01T12:{at}+00:00", "metadata": metadata}


def test_stage_times_and_tool_waits_come_from_the_runs_events() -> None:
    events = [
        _event("graph.node.started", "00:00", node="planner"),
        _event("graph.node.completed", "05:00", node="planner"),
        _event("graph.node.started", "05:00", node="researcher"),
        *(
            _event("researcher.tool_call", "06:00", lock_wait_s=wait, duration_s=1.0)
            for wait in (0.0, 0.0, 0.5, 2.0)
        ),
        _event("researcher.tool_call", "06:00"),
        _event("graph.node.completed", "15:00", node="researcher"),
        _event("graph.node.started", "20:00", node="researcher"),
        _event("graph.node.completed", "22:30", node="researcher"),
    ]

    assert stage_seconds(events) == {"planner": 300.0, "researcher": 750.0}
    assert lock_waits(events) == {
        "timed_tool_calls": 4,
        "lock_wait_s_median": 0.25,
        "lock_wait_s_p95": 2.0,
        "lock_wait_s_max": 2.0,
    }


@pytest.mark.asyncio
async def test_a_run_has_its_own_memory_its_overrides_and_its_records(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}
    quality_file = tmp_path / "report-0-quality.json"
    quality_file.write_text(json.dumps(_quality()), encoding="utf-8")
    events = [
        ResearchEvent(event_type="graph.node.started", source="graph.planner", message="m",
                      timestamp="2026-10-01T12:00:00+00:00", metadata={"node": "planner"}),
        ResearchEvent(event_type="graph.node.completed", source="graph.planner", message="m",
                      timestamp="2026-10-01T12:01:00+00:00", metadata={"node": "planner"}),
    ]

    async def research(question: str, **kwargs: Any) -> Any:
        seen.update(kwargs, question=question)
        for event in events:
            kwargs["event_handler"](event)
        capture_node_input(
            "evidence_verifier",
            ResearchState.model_validate({"session_id": "s-1", "original_question": question}),
        )
        return SimpleNamespace(
            session_id="s-1", status="completed", report_path="output/report.md",
            quality_path=str(quality_file), duration_seconds=60.0,
            state=SimpleNamespace(events=events),
        )

    path = await run_one(
        question_id="rome", arm="x3", repetition=1,
        overrides={"llm": {"model_overrides": {"planner": {"reasoning_effort": "high"}}}},
        out=tmp_path / "live", capture=True, research=research,
        output_root=tmp_path / "output",
    )

    run_dir = tmp_path / "live" / "rome-x3-1"
    assert seen["question"] == QUESTIONS["rome"]
    assert seen["config_overrides"] == {
        "llm": {"model_overrides": {"planner": {"reasoning_effort": "high"}}},
        "memory": {
            "long_term": {"persist_directory": str(run_dir / "memory" / "chroma")},
            "procedural": {"strategies_path": str(run_dir / "memory" / "strategies.json")},
        },
    }
    recorded = json.loads(path.read_text(encoding="utf-8"))
    assert recorded["metrics"]["stage_seconds"] == {"planner": 60.0}
    assert recorded["metrics"]["required_coverage"] == 0.75
    assert recorded["metrics"]["seconds"] == 60.0
    assert recorded["metrics"]["output_tokens_per_s"] == {"researcher": 175.0}
    assert len((run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert (run_dir / "quality.json").is_file()
    assert (run_dir / "capture" / "evidence_verifier-01.json").is_file()

    with pytest.raises(FileExistsError):
        await run_one(question_id="rome", arm="x3", repetition=1, overrides={},
                      out=tmp_path / "live", capture=False, research=research,
                      output_root=tmp_path / "output")


@pytest.mark.asyncio
async def test_a_quality_record_that_cannot_be_read_keeps_the_runs_timing(tmp_path: Path) -> None:
    """Review P2-5: a run without a quality snapshot publishes ``"quality": {}``;
    the paid run's timing survives, and the reason is recorded."""
    quality_file = tmp_path / "report-0-quality.json"
    quality_file.write_text(json.dumps({"session_status": "completed", "quality": {}}), encoding="utf-8")
    events = [
        ResearchEvent(event_type="graph.node.started", source="graph.planner", message="m",
                      timestamp="2026-10-01T12:00:00+00:00", metadata={"node": "planner"}),
        ResearchEvent(event_type="graph.node.completed", source="graph.planner", message="m",
                      timestamp="2026-10-01T12:00:30+00:00", metadata={"node": "planner"}),
    ]

    async def research(question: str, **kwargs: Any) -> Any:
        return SimpleNamespace(
            session_id="s-2", status="completed", report_path=None,
            quality_path=str(quality_file), duration_seconds=30.0,
            state=SimpleNamespace(events=events),
        )

    path = await run_one(question_id="tamil", arm="x2", repetition=1, overrides={},
                         out=tmp_path / "live", capture=False, research=research,
                         output_root=tmp_path / "output")

    metrics = json.loads(path.read_text(encoding="utf-8"))["metrics"]
    assert metrics["seconds"] == 30.0
    assert metrics["stage_seconds"] == {"planner": 30.0}
    assert metrics["quality_error"].startswith("KeyError")
    assert "quality_status" not in metrics


def _metrics(**overrides: Any) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "session_status": "completed", "quality_status": "accepted", "hard_failures": 0,
        "unjudged_sentences": 0, "unresolved_citations": 0, "review_status": "scored",
        "review_mean_score": 0.80, "material_defects": 0, "required_coverage": 1.0,
        "not_found": 0, "verified_plus_corrected": 100, "dropped_findings": 5,
        "dropped_rate": 0.05, "cited_sources": 30, "publishers": 20, "refused_sentences": 4,
        "seconds": 1500.0, "stage_seconds": {"researcher": 700.0, "planner": 300.0},
    }
    metrics.update(overrides)
    return metrics


def _runs(treatment: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    runs = [
        {"question_id": "tamil", "arm": "baseline", "metrics": _metrics(review_mean_score=0.80)},
        {"question_id": "tamil", "arm": "baseline", "metrics": _metrics(review_mean_score=0.86)},
        {"question_id": "latte", "arm": "baseline", "metrics": _metrics()},
        {"question_id": "rome", "arm": "baseline", "metrics": _metrics()},
    ]
    for question in ("tamil", "latte", "rome"):
        runs.append({"question_id": question, "arm": "x2",
                     "metrics": _metrics(**treatment.get(question, {}))})
    return runs


def test_a_neutral_faster_treatment_passes() -> None:
    faster = {"seconds": 1300.0, "stage_seconds": {"researcher": 500.0, "planner": 300.0}}
    verdict = compare(
        _runs({"tamil": {**faster, "review_mean_score": 0.75}, "latte": faster, "rome": faster}),
        treatment="x2", stage="researcher",
    )

    # Tamil's two controls are 0.06 apart, so the margin is 0.06, not the floor.
    assert verdict["review_margin"] == 0.06
    assert verdict["accuracy_passed"] is True
    assert verdict["stage_faster_on"] == 3
    assert verdict["passed"] is True
    # The provider's speed sits beside the time verdict; these runs record none.
    assert verdict["questions"]["latte-1"]["stage_output_tokens_per_s"] is None
    assert verdict["questions"]["latte-1"]["control_stage_output_tokens_per_s"] is None


@pytest.mark.parametrize(
    ("change", "check"),
    [
        ({"hard_failures": 1}, "no_hard_failures"),
        ({"unjudged_sentences": 2}, "no_unjudged_sentences"),
        ({"review_status": "unavailable"}, "review_scored"),
        ({"quality_status": "partial"}, "accepted_if_controls_were"),
        ({"review_mean_score": 0.73}, "review_score"),
        ({"material_defects": 1}, "material_defects"),
        ({"required_coverage": 0.9}, "required_coverage"),
        ({"not_found": 1}, "not_found"),
        ({"verified_plus_corrected": 84}, "verified_plus_corrected"),
        ({"publishers": 16}, "publishers"),
        ({"refused_sentences": 7}, "refused_sentences"),
        ({"dropped_rate": 0.11}, "dropped_rate"),
    ],
)
def test_each_accuracy_check_can_fail_a_faster_treatment(change: dict[str, Any], check: str) -> None:
    faster = {"seconds": 1300.0, "stage_seconds": {"researcher": 500.0, "planner": 300.0}}
    verdict = compare(
        _runs({"tamil": faster, "latte": {**faster, **change}, "rome": faster}),
        treatment="x2", stage="researcher",
    )

    assert verdict["questions"]["latte-1"]["checks"][check] is False
    assert verdict["accuracy_passed"] is False
    assert verdict["passed"] is False


def test_an_accurate_treatment_that_is_not_faster_fails_on_time() -> None:
    slower = {"seconds": 1550.0, "stage_seconds": {"researcher": 750.0, "planner": 300.0}}
    verdict = compare(
        _runs({"tamil": slower, "latte": slower, "rome": {"seconds": 1500.0}}),
        treatment="x2", stage="researcher",
    )

    assert verdict["accuracy_passed"] is True
    assert verdict["stage_faster_on"] == 0
    assert verdict["time_passed"] is False
    assert verdict["passed"] is False


def test_the_run_command_refuses_to_start_inside_peak_hours(tmp_path: Path) -> None:
    async def research(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no run may start inside peak hours")

    code = main(
        ["run", "--question", "tamil", "--arm", "baseline", "--repetition", "1",
         "--out", str(tmp_path)],
        research=research,
        now=lambda: datetime(2026, 10, 1, 5, 30, tzinfo=timezone.utc),
    )

    assert code == 2
    assert list(tmp_path.iterdir()) == []


def _suite(status: str, **cases: tuple[float, ...]) -> dict[str, Any]:
    return {
        "status": status,
        "cases": [
            {
                "case_id": case_id,
                "repetitions": [{"aggregate_quality": score} for score in scores],
                "average_quality": sum(scores) / len(scores),
                "passed": min(scores) >= 0.65 and sum(scores) / len(scores) >= 0.80,
            }
            for case_id, scores in cases.items()
        ],
    }


def test_the_suite_gate_needs_the_harness_pass_and_each_case_near_its_control() -> None:
    control = _suite("REVIEW REQUIRED", a=(0.90, 0.92, 0.94), b=(0.80, 0.90, 0.85))

    near = suite_verdict(control, _suite("REVIEW REQUIRED", a=(0.88, 0.88, 0.88), b=(0.85, 0.80, 0.81)))
    assert near["cases"]["b"]["margin"] == 0.1
    assert near["passed"] is True

    dropped = suite_verdict(control, _suite("REVIEW REQUIRED", a=(0.85, 0.86, 0.84), b=(0.85, 0.85, 0.85)))
    assert dropped["cases"]["a"]["within_margin"] is False
    assert dropped["passed"] is False

    failed = suite_verdict(control, _suite("FAILED", a=(0.92, 0.92, 0.60), b=(0.85, 0.85, 0.85)))
    assert failed["harness_passed"] is False
    assert failed["passed"] is False


def test_a_run_that_published_nothing_fails_as_a_treatment_and_is_no_control() -> None:
    faster = {"seconds": 1300.0, "stage_seconds": {"researcher": 500.0, "planner": 300.0}}
    runs = _runs({"tamil": faster, "latte": faster, "rome": faster})
    runs.append({"question_id": "rome", "arm": "baseline",
                 "metrics": {"seconds": 900.0, "stage_seconds": {}}})
    runs.append({"question_id": "latte", "arm": "x2",
                 "metrics": {"seconds": None, "stage_seconds": {}}})

    verdict = compare(runs, treatment="x2", stage="researcher")

    assert verdict["questions"]["rome-1"]["checks"]["completed"] is True
    assert verdict["questions"]["latte-2"] == {"checks": {"completed": False}}
    assert verdict["passed"] is False


@pytest.mark.asyncio
async def test_a_crashed_run_still_writes_run_json_so_compare_fails_closed(tmp_path: Path) -> None:
    """Task 17 review: a paid run whose ``research`` raises used to leave no
    ``run.json``, so ``load_runs`` never saw it and its question silently
    dropped out of ``compare``, which could then pass on the other questions."""
    out = tmp_path / "live"
    events = [
        ResearchEvent(event_type="graph.node.started", source="graph.planner", message="m",
                      timestamp="2026-10-01T12:00:00+00:00", metadata={"node": "planner"}),
        ResearchEvent(event_type="graph.node.completed", source="graph.planner", message="m",
                      timestamp="2026-10-01T12:01:00+00:00", metadata={"node": "planner"}),
        ResearchEvent(event_type="graph.node.started", source="graph.researcher", message="m",
                      timestamp="2026-10-01T12:01:00+00:00", metadata={"node": "researcher"}),
    ]

    async def research(question: str, **kwargs: Any) -> Any:
        for event in events:
            kwargs["event_handler"](event)
        raise RuntimeError("provider went away")

    with pytest.raises(RuntimeError, match="provider went away"):
        await run_one(question_id="latte", arm="x2", repetition=1, overrides={"a": 1},
                      out=out, capture=False, research=research,
                      output_root=tmp_path / "output")

    crashed = json.loads((out / "latte-x2-1" / "run.json").read_text(encoding="utf-8"))
    assert crashed["status"] == "failed"
    assert crashed["error"] == "RuntimeError: provider went away"
    assert crashed["question_id"] == "latte"
    assert crashed["arm"] == "x2"
    assert crashed["repetition"] == 1
    assert crashed["overrides"] == {"a": 1}
    # The timing recorded before the crash survives; a node that never finished adds none.
    assert crashed["metrics"] == {"seconds": None, "stage_seconds": {"planner": 60.0}}
    assert load_runs(out) == [crashed]

    faster = {"seconds": 1300.0, "stage_seconds": {"researcher": 500.0, "planner": 300.0}}
    runs = [
        run for run in _runs({"tamil": faster, "latte": faster, "rome": faster})
        if not (run["question_id"] == "latte" and run["arm"] == "x2")
    ] + load_runs(out)

    # The other two questions are faster and accurate, yet the crashed one fails the verdict.
    verdict = compare(runs, treatment="x2", stage="researcher")
    assert verdict["questions"]["latte-1"] == {"checks": {"completed": False}}
    assert verdict["accuracy_passed"] is False
    assert verdict["passed"] is False

    # And alone it does not pass the time verdict on "1 of 1 question" either.
    alone = compare(
        [run for run in runs if run["question_id"] == "latte"], treatment="x2", stage="researcher"
    )
    assert alone["stage_faster_on"] == 0
    assert alone["passed"] is False


@pytest.mark.asyncio
async def test_a_cancelled_run_still_writes_run_json_and_the_cancellation_propagates(
    tmp_path: Path,
) -> None:
    """Final review: ``CancelledError`` is a ``BaseException``, so a cancelled
    paid run (a Ctrl-C, a timeout) used to leave no ``run.json``. It now leaves
    the timing-only record and the cancellation still propagates."""
    out = tmp_path / "live"
    events = [
        ResearchEvent(event_type="graph.node.started", source="graph.planner", message="m",
                      timestamp="2026-10-01T12:00:00+00:00", metadata={"node": "planner"}),
        ResearchEvent(event_type="graph.node.completed", source="graph.planner", message="m",
                      timestamp="2026-10-01T12:00:45+00:00", metadata={"node": "planner"}),
    ]

    async def research(question: str, **kwargs: Any) -> Any:
        for event in events:
            kwargs["event_handler"](event)
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await run_one(question_id="rome", arm="x2", repetition=2, overrides={},
                      out=out, capture=False, research=research,
                      output_root=tmp_path / "output")

    cancelled = json.loads((out / "rome-x2-2" / "run.json").read_text(encoding="utf-8"))
    assert cancelled["status"] == "failed"
    assert cancelled["error"].startswith("CancelledError")
    assert cancelled["metrics"] == {"seconds": None, "stage_seconds": {"planner": 45.0}}
    assert load_runs(out) == [cancelled]


def _with_repetitions(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``_runs`` has no repetition numbers; tamil's second baseline is repetition 2."""
    for number, run in enumerate(runs):
        run["repetition"] = 2 if number == 1 else 1
    return runs


def test_compare_lists_every_baseline_it_considered_and_whether_it_was_used() -> None:
    """Final review, then owner decision H10: the verdict names each baseline it
    considered per question with its session status and whether it was used,
    and the treatment runs it could not judge. Listing changes nothing: the
    verdict equals the one for the same runs without the excluded baseline."""
    faster = {"seconds": 1300.0, "stage_seconds": {"researcher": 500.0, "planner": 300.0}}
    built = _with_repetitions(_runs({"tamil": faster, "latte": faster, "rome": faster}))
    built[1]["metrics"]["session_status"] = "incomplete"
    # Rome's only baseline run crashed: it has no quality record and no duration.
    built[3]["metrics"] = {"seconds": None, "stage_seconds": {}}

    verdict = compare(built, treatment="x2", stage="researcher")

    assert verdict["controls_used"] == {
        "latte": {"count": 1, "baselines": [
            {"repetition": 1, "session_status": "completed", "used": True},
        ]},
        "rome": {"count": 0, "baselines": [
            {"repetition": 1, "session_status": None, "used": False},
        ]},
        "tamil": {"count": 1, "baselines": [
            {"repetition": 1, "session_status": "completed", "used": True},
            {"repetition": 2, "session_status": "incomplete", "used": False},
        ]},
    }
    assert verdict["unpaired"] == [{"question_id": "rome", "arm": "x2", "repetition": 1}]
    assert verdict["questions"]["rome-1"] == {"checks": {"has_controls": False}}
    assert verdict["passed"] is False

    # The excluded baseline is simply not a control: dropping it changes nothing but its listing.
    without = compare(
        [run for number, run in enumerate(built) if number != 1], treatment="x2", stage="researcher"
    )
    for key in verdict.keys() - {"controls_used"}:
        assert verdict[key] == without[key], key


def test_a_baseline_that_did_not_complete_is_no_control_and_does_not_widen_the_margins() -> None:
    """Owner decision H10 (2026-10-01): a control must have completed. An
    incomplete baseline at review 0.55 used to widen the review margin to its
    0.25 spread from the completed baseline, which let a weak 0.70 treatment
    through; now it is no control, the margin stays at the 0.03 floor and the
    treatment fails."""
    faster = {"seconds": 1300.0, "stage_seconds": {"researcher": 500.0, "planner": 300.0}}
    weak = {**faster, "review_mean_score": 0.70}

    def verdict_for(second_baseline_status: str) -> dict[str, Any]:
        built = _runs({"tamil": weak, "latte": faster, "rome": faster})
        built[0]["metrics"]["review_mean_score"] = 0.80
        built[1]["metrics"].update(session_status=second_baseline_status, review_mean_score=0.55)
        return compare(built, treatment="x2", stage="researcher")

    # Were the 0.55 baseline a completed one, it would be a control and the spread is the pre-registered margin.
    widened = verdict_for("completed")
    assert widened["review_margin"] == 0.25
    assert widened["passed"] is True

    excluded = verdict_for("incomplete")
    assert excluded["review_margin"] == 0.03
    assert excluded["questions"]["tamil-1"]["checks"]["review_score"] is False
    assert excluded["controls_used"]["tamil"]["count"] == 1
    assert excluded["accuracy_passed"] is False
    assert excluded["passed"] is False


def test_a_question_with_a_treatment_but_no_usable_control_fails_the_verdict() -> None:
    """Owner decision H10 (2026-10-01): it used to drop out of the verdict and
    let the other questions pass. A question with neither arm is still absent."""
    faster = {"seconds": 1300.0, "stage_seconds": {"researcher": 500.0, "planner": 300.0}}
    runs = _with_repetitions(_runs({"tamil": faster, "latte": faster, "rome": faster}))
    # Rome's only baseline run did not complete.
    runs[3]["metrics"]["session_status"] = "max_iterations"

    verdict = compare(runs, treatment="x2", stage="researcher")

    assert verdict["questions"]["rome-1"] == {"checks": {"has_controls": False}}
    assert verdict["unpaired"] == [{"question_id": "rome", "arm": "x2", "repetition": 1}]
    assert verdict["accuracy_passed"] is False
    assert verdict["time_passed"] is True
    assert verdict["passed"] is False

    neither = compare(
        [run for run in runs if run["question_id"] != "rome"], treatment="x2", stage="researcher"
    )
    assert "rome" not in neither["controls_used"]
    assert neither["unpaired"] == []
    assert neither["passed"] is True


def test_a_treatment_with_no_control_anywhere_fails_instead_of_raising() -> None:
    treatment_only = [
        {"question_id": "rome", "arm": "x2", "repetition": 1,
         "metrics": _metrics(seconds=1300.0, stage_seconds={"researcher": 500.0})},
    ]

    verdict = compare(treatment_only, treatment="x2", stage="researcher")

    assert verdict["questions"] == {"rome-1": {"checks": {"has_controls": False}}}
    assert verdict["mean_seconds_ratio"] is None
    assert verdict["time_passed"] is False
    assert verdict["passed"] is False

    with pytest.raises(ValueError, match="no question has both"):
        compare([{"question_id": "rome", "arm": "baseline", "metrics": _metrics()}],
                treatment="x2", stage="researcher")


QUALITY_FILE = "report-0-quality.json"
PLANNER_OVERRIDES = {
    "llm": {"model_overrides": {"planner": {"reasoning_effort": "high"}}}
}


def _published(quality_path: str | None) -> Any:
    """A fake ``research`` whose session publishes ``quality_path`` as given."""
    events = [
        ResearchEvent(event_type="graph.node.started", source="graph.planner",
                      message="m", timestamp="2026-10-01T12:00:00+00:00",
                      metadata={"node": "planner"}),
        ResearchEvent(event_type="graph.node.completed", source="graph.planner",
                      message="m", timestamp="2026-10-01T12:01:00+00:00",
                      metadata={"node": "planner"}),
    ]

    async def research(question: str, **kwargs: Any) -> Any:
        return SimpleNamespace(
            session_id="s-9", status="completed", report_path="report-0.md",
            quality_path=quality_path, duration_seconds=60.0,
            state=SimpleNamespace(events=events),
        )

    return research


def _settings_loader(output_root: Path, calls: list[tuple[str, Any]]) -> Any:
    """A ``load_settings`` stand-in: records its arguments, answers ``output_root``."""

    def load(config_path: str, *, config_overrides: Any = None) -> Any:
        calls.append((config_path, config_overrides))
        return SimpleNamespace(output=SimpleNamespace(directory=str(output_root)))

    return load


def _publish_quality(output_root: Path) -> None:
    """The quality record, where the ``write_document`` tool puts it."""
    output_root.mkdir(exist_ok=True)
    (output_root / QUALITY_FILE).write_text(json.dumps(_quality()), encoding="utf-8")


def _recorded(run_json: Path) -> dict[str, Any]:
    return json.loads(run_json.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_a_quality_path_relative_to_the_output_directory_is_merged(
    tmp_path: Path,
) -> None:
    """The ``write_document`` tool publishes ``quality_path`` relative to its root,
    ``settings.output.directory``. Every real run lost its accuracy metrics to a
    ``FileNotFoundError`` because the runner read it from the working directory."""
    _publish_quality(tmp_path / "output")

    path = await run_one(
        question_id="rome", arm="baseline", repetition=1, overrides={},
        out=tmp_path / "live", capture=False, research=_published(QUALITY_FILE),
        output_root=tmp_path / "output",
    )

    recorded = _recorded(path)
    assert recorded["quality_path"] == QUALITY_FILE
    assert "quality_error" not in recorded["metrics"]
    assert recorded["metrics"]["required_coverage"] == 0.75
    assert recorded["metrics"]["output_tokens_per_s"] == {"researcher": 175.0}
    assert _recorded(path.parent / "quality.json") == _quality()


@pytest.mark.asyncio
async def test_requality_repairs_a_finished_run_whose_quality_record_was_not_found(
    tmp_path: Path,
) -> None:
    """A run that lost its metrics to the relative-path defect is repaired in place,
    to exactly the ``run.json`` and ``quality.json`` a fixed run writes."""
    output_root = tmp_path / "output"
    output_root.mkdir()
    research = _published(QUALITY_FILE)
    # The run as the defect left it: its timing, and why it has no accuracy metrics.
    broken = await run_one(
        question_id="rome", arm="x3", repetition=1, overrides=PLANNER_OVERRIDES,
        out=tmp_path / "broken", capture=False, research=research,
        output_root=output_root,
    )
    assert _recorded(broken)["metrics"]["quality_error"].startswith("FileNotFoundError")
    _publish_quality(output_root)
    fixed = await run_one(
        question_id="rome", arm="x3", repetition=1, overrides=PLANNER_OVERRIDES,
        out=tmp_path / "fixed", capture=False, research=research,
        output_root=output_root,
    )
    calls: list[tuple[str, Any]] = []

    code = main(
        ["requality", "--run-dir", str(broken.parent), "--config", "other.yaml"],
        settings_loader=_settings_loader(output_root, calls),
    )

    assert code == 0
    assert calls == [("other.yaml", PLANNER_OVERRIDES)]
    repaired = _recorded(broken)
    assert "quality_error" not in repaired["metrics"]
    assert repaired["metrics"]["required_coverage"] == 0.75
    assert broken.read_bytes() == fixed.read_bytes()
    repaired_quality = (broken.parent / "quality.json").read_bytes()
    assert repaired_quality == (fixed.parent / "quality.json").read_bytes()


@pytest.mark.asyncio
async def test_requality_fails_and_says_why_when_the_quality_record_is_still_missing(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "output"
    output_root.mkdir()
    broken = await run_one(
        question_id="rome", arm="x3", repetition=1, overrides={},
        out=tmp_path / "live", capture=False, research=_published(QUALITY_FILE),
        output_root=output_root,
    )

    code = main(
        ["requality", "--run-dir", str(broken.parent)],
        settings_loader=_settings_loader(output_root, []),
    )

    assert code == 1
    metrics = _recorded(broken)["metrics"]
    assert metrics["quality_error"].startswith("FileNotFoundError")
    assert metrics["seconds"] == 60.0


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"status": "failed"}, "failed"),
        ({"quality_path": None}, "published no quality record"),
    ],
)
def test_requality_refuses_a_run_that_did_not_complete_or_has_no_quality_record(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    changes: dict[str, Any],
    reason: str,
) -> None:
    run_dir = tmp_path / "rome-baseline-1"
    run_dir.mkdir()
    record = {
        "question_id": "rome", "arm": "baseline", "repetition": 1, "overrides": {},
        "status": "completed", "quality_path": QUALITY_FILE,
        "metrics": {"seconds": 60.0, "stage_seconds": {}},
        **changes,
    }
    (run_dir / "run.json").write_text(json.dumps(record), encoding="utf-8")
    before = (run_dir / "run.json").read_bytes()

    def settings_loader(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a refused run must not load the configuration")

    code = main(["requality", "--run-dir", str(run_dir)],
                settings_loader=settings_loader)

    assert code == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert reason in lines[0]
    assert (run_dir / "run.json").read_bytes() == before
    assert not (run_dir / "quality.json").exists()


def test_the_run_command_reads_the_quality_record_from_the_configured_output_dir(
    tmp_path: Path,
) -> None:
    _publish_quality(tmp_path / "output")
    calls: list[tuple[str, Any]] = []

    code = main(
        ["run", "--question", "rome", "--arm", "x3", "--repetition", "1",
         "--out", str(tmp_path / "live"), "--config", "other.yaml",
         "--override", json.dumps(PLANNER_OVERRIDES)],
        research=_published(QUALITY_FILE),
        settings_loader=_settings_loader(tmp_path / "output", calls),
        now=lambda: datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
    )

    assert code == 0
    assert calls == [("other.yaml", PLANNER_OVERRIDES)]
    metrics = _recorded(tmp_path / "live" / "rome-x3-1" / "run.json")["metrics"]
    assert "quality_error" not in metrics
    assert metrics["required_coverage"] == 0.75


def test_a_configuration_error_spends_nothing_and_leaves_no_run_directory(
    tmp_path: Path,
) -> None:
    async def research(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no run may start when the configuration does not load")

    def settings_loader(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("config_invalid")

    with pytest.raises(RuntimeError, match="config_invalid"):
        main(
            ["run", "--question", "rome", "--arm", "baseline", "--repetition", "1",
             "--out", str(tmp_path / "live")],
            research=research,
            settings_loader=settings_loader,
            now=lambda: datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
        )

    assert not (tmp_path / "live").exists()
