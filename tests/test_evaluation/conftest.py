"""Offline fixtures shared by the evaluation tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from deep_research.evaluation.config import GitMetadata, build_runtime_config
from deep_research.evaluation.models import (
    CaseResult,
    ExperimentResult,
    GateReport,
    GateResult,
    JudgeFeedback,
    JudgeScores,
    JudgeVerdict,
    RepetitionResult,
)
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.utils.config import ConfigSettings


@pytest.fixture
def tracker() -> Tracker:
    """Records locally and never opens a LangSmith client."""
    return Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False,
            project="evaluation-tests",
            api_key=None,
        )
    )


@pytest.fixture
def settings() -> ConfigSettings:
    return ConfigSettings()


@pytest.fixture
def runtime_config_for(settings):
    """Build an ``EvaluationRuntimeConfig`` with a frozen clock and SHA."""

    def factory(agent_name, *, tier="controlled", case_id=None, **kwargs):
        return build_runtime_config(
            settings,
            agent_name=agent_name,
            tier=tier,
            case_id=case_id,
            reasoning_effort=kwargs.get("reasoning_effort"),
            judge_reasoning_effort=kwargs.get("judge_reasoning_effort"),
            output_directory=kwargs.get("output_directory"),
            experiment_prefix=kwargs.get("experiment_prefix"),
            now=datetime(2026, 8, 16, 10, 15, tzinfo=timezone.utc),
            git=GitMetadata(
                commit="abc1234def", short_sha="abc1234", dirty=False
            ),
        )

    return factory


@pytest.fixture
def judge_feedback() -> JudgeFeedback:
    return JudgeFeedback(
        status="scored",
        verdict=JudgeVerdict(
            scores=JudgeScores(
                role_adherence=0.9,
                completeness=0.9,
                groundedness=0.9,
                reasoning_quality=0.9,
                usefulness=0.9,
                uncertainty_calibration=0.9,
            ),
            agent_specific={"decomposition_quality": 0.9},
            rationale="Clear, distinct, prioritized subtopics.",
        ),
        judge_quality=0.9,
        prompt_id="individual-agent-judge",
        rubric_version=1,
        prompt_fingerprint="abc123abc123",
        judge_model="gpt-5.6-luna",
        judge_configuration_fingerprint="def456def456",
        evaluator_trace_url="https://smith.langchain.com/o/x/r/judge-1",
        evaluator_source_url="https://smith.langchain.com/o/x/evaluators/1",
    )


@pytest.fixture
def repetition_result(judge_feedback) -> RepetitionResult:
    return RepetitionResult(
        case_id="focused-decomposition",
        case_version=1,
        repetition=1,
        completed=True,
        gates=GateReport(
            results=[
                GateResult(gate_id="run_completed", passed=True, detail="")
            ]
        ),
        deterministic_quality=0.9,
        judge=judge_feedback,
        aggregate_quality=0.9,
        trace_url="https://smith.langchain.com/o/x/r/1",
        errors=[],
    )


@pytest.fixture
def experiment_result(repetition_result) -> ExperimentResult:
    return ExperimentResult(
        agent_name="planner",
        tier="controlled",
        experiment_name="planner-controlled-20260816T101500Z-abc1234",
        experiment_url="https://smith.langchain.com/o/x/experiments/1",
        dataset_name="deep-research-planner-controlled-v1",
        dataset_url="https://smith.langchain.com/o/x/datasets/1",
        cases=[
            CaseResult(
                case_id="focused-decomposition",
                case_version=1,
                repetitions=[repetition_result],
                average_quality=0.9,
                passed=True,
                lowest_scoring_trace_url="https://smith.langchain.com/o/x/r/1",
            )
        ],
        status="REVIEW REQUIRED",
        metadata={"git_sha": "abc1234"},
    )
