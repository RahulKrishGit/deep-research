"""The six-agent controlled suite."""

from __future__ import annotations

import json

import pytest

from deep_research.evaluation.models import AGENT_NAMES, SuiteResult
from deep_research.evaluation.reporting import render_suite, write_suite_artifact
from deep_research.evaluation.runner import run_suite_evaluation


@pytest.mark.asyncio
async def test_the_suite_runs_all_six_agents_controlled(
    settings, tmp_path, suite_harness
) -> None:
    result = await run_suite_evaluation(settings, **suite_harness.kwargs(tmp_path))

    assert [item.agent_name for item in result.experiments] == list(AGENT_NAMES)
    assert all(item.tier == "controlled" for item in result.experiments)


@pytest.mark.asyncio
async def test_the_suite_never_launches_a_live_experiment(
    settings, tmp_path, suite_harness
) -> None:
    """Live runs are manually invoked after controlled review."""
    result = await run_suite_evaluation(settings, **suite_harness.kwargs(tmp_path))

    assert all(item.tier != "live" for item in result.experiments)
    assert all(
        call["num_repetitions"] == 3 for call in suite_harness.runner.calls
    )


@pytest.mark.asyncio
async def test_a_judge_override_applies_uniformly_to_every_agent(
    settings, tmp_path, suite_harness
) -> None:
    await run_suite_evaluation(
        settings,
        **{**suite_harness.kwargs(tmp_path), "judge_reasoning_effort": "max"},
    )

    efforts = {
        call["metadata"]["judge_reasoning_effort"]
        for call in suite_harness.runner.calls
    }
    assert efforts == {"max"}


@pytest.mark.asyncio
async def test_each_agent_keeps_its_own_target_effort_in_the_suite(
    settings, tmp_path, suite_harness
) -> None:
    """A suite must not flatten the approved per-agent profile."""
    await run_suite_evaluation(settings, **suite_harness.kwargs(tmp_path))

    efforts = {
        call["metadata"]["agent"]: call["metadata"]["target_reasoning_effort"]
        for call in suite_harness.runner.calls
    }
    assert efforts == {
        "planner": "medium",
        "researcher": "low",
        "source_evaluator": "low",
        "fact_checker": "medium",
        "synthesizer": "medium",
        "critic": "medium",
    }


@pytest.mark.asyncio
async def test_one_failing_agent_does_not_stop_the_others(
    settings, tmp_path, partially_failing_suite_harness
) -> None:
    result = await run_suite_evaluation(
        settings, **partially_failing_suite_harness.kwargs(tmp_path)
    )

    assert len(result.experiments) == 6
    assert result.status == "FAILED"
    assert any(item.status == "REVIEW REQUIRED" for item in result.experiments)


@pytest.mark.asyncio
async def test_the_suite_writes_a_summary_artifact(
    settings, tmp_path, suite_harness
) -> None:
    result = await run_suite_evaluation(settings, **suite_harness.kwargs(tmp_path))
    path = write_suite_artifact(
        result, root=tmp_path / "suite" / result.suite_id
    )

    assert path.name == "summary.json"
    restored = SuiteResult.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )
    assert restored == result


@pytest.mark.asyncio
async def test_each_agent_still_writes_its_own_results_artifact(
    settings, tmp_path, suite_harness
) -> None:
    await run_suite_evaluation(settings, **suite_harness.kwargs(tmp_path))

    written = sorted(path.parent.parent.name
                     for path in tmp_path.rglob("results.json"))
    assert written == [
        "critic",
        "fact-checker",
        "planner",
        "researcher",
        "source-evaluator",
        "synthesizer",
    ]


def test_the_suite_summary_lists_every_agent_and_its_status(
    suite_result,
) -> None:
    body = "\n".join(render_suite(suite_result, verbose=False))

    for agent_name in ("planner", "researcher", "critic"):
        assert agent_name in body
    assert "REVIEW REQUIRED" in body
    assert "summary.json" in body
