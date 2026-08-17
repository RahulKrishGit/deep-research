"""Offline fixtures shared by the evaluation tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from deep_research.evaluation.cases import (
    all_cases as _all_cases,
)
from deep_research.evaluation.cases import (
    cases_for,
)
from deep_research.evaluation.config import GitMetadata, build_runtime_config
from deep_research.evaluation.models import (
    CaseResult,
    DependencyLedger,
    EvidenceContext,
    ExperimentResult,
    GateReport,
    GateResult,
    JudgeFeedback,
    JudgeScores,
    JudgeVerdict,
    ReActSummary,
    RepetitionResult,
    TargetOutput,
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
def all_cases():
    return _all_cases()


@pytest.fixture
def controlled_case_for():
    """The registry's first controlled case for an agent.

    The registry is empty until Tasks 10–15 land the case files, so an
    empty lookup skips rather than failing: the tests that need a case
    start running the moment that agent's cases exist.
    """

    def factory(agent_name):
        available = cases_for(agent_name, "controlled")
        if not available:
            pytest.skip(
                f"no controlled cases registered for {agent_name} yet; "
                "cases land in Tasks 10-15"
            )
        return available[0]

    return factory


@pytest.fixture
def live_case_for():
    """The registry's first live case for an agent.

    Same empty-registry contract as ``controlled_case_for``: an empty
    lookup skips rather than failing, so the tests that need a case start
    running the moment that agent's live cases exist.
    """

    def factory(agent_name):
        available = cases_for(agent_name, "live")
        if not available:
            pytest.skip(
                f"no live cases registered for {agent_name} yet; "
                "cases land in Tasks 10-15"
            )
        return available[0]

    return factory


@pytest.fixture
def planner_case(controlled_case_for):
    return controlled_case_for("planner")


@pytest.fixture
def researcher_case(controlled_case_for):
    return controlled_case_for("researcher")


@pytest.fixture
def synthesizer_case(controlled_case_for):
    return controlled_case_for("synthesizer")


@pytest.fixture
def clean_target_output(planner_case) -> TargetOutput:
    """A fully populated planner repetition that passes every general gate.

    The result carries the required ``sub_topics`` field and no URL-looking
    strings (the planner's controlled cases declare no known source urls),
    the ReAct summary stays within the case budgets, the ledger is empty,
    and a non-blank trace url is present.
    """
    return TargetOutput(
        case_id=planner_case.case_id,
        case_version=planner_case.version,
        agent_name=planner_case.agent_name,
        tier=planner_case.tier,
        repetition=1,
        session_id="evaluation-focused-decomposition",
        experiment_name="planner-controlled-20260816T101500Z-abc1234",
        trace_url="https://smith.langchain.com/o/x/r/planner-1",
        completed=True,
        failure=None,
        result={
            "sub_topics": [
                {
                    "title": "Solid-state electrolyte degradation",
                    "rationale": "Electrolyte stability dominates cycle life.",
                    "search_queries": [
                        "solid-state electrolyte degradation mechanism"
                    ],
                    "success_criteria": ["Crack propagation data"],
                    "priority": 1,
                },
                {
                    "title": "Cathode interface resistance",
                    "rationale": "Interface resistance limits capacity retention.",
                    "search_queries": [
                        "cathode solid-state interface resistance"
                    ],
                    "success_criteria": ["Quantified resistance growth"],
                    "priority": 2,
                },
            ]
        },
        state_update={"note": "planned three subtopics"},
        errors=[],
        tracker_errors=[],
        react=ReActSummary(
            iterations=2,
            tool_calls=3,
            stop_reason="completed",
            max_iterations=planner_case.expectations.max_iterations,
            tool_budget=planner_case.expectations.max_tool_calls,
        ),
        dependencies=DependencyLedger(),
        evidence=EvidenceContext(),
        trajectory=[],
        target_model_requested="gpt-5.6-luna",
        target_model_returned="gpt-5.6-luna",
        target_reasoning_effort="medium",
    )


@pytest.fixture
def researcher_target_output(researcher_case) -> TargetOutput:
    """A fully populated researcher repetition citing only known sources."""
    urls = researcher_case.expectations.known_source_urls
    return TargetOutput(
        case_id=researcher_case.case_id,
        case_version=researcher_case.version,
        agent_name=researcher_case.agent_name,
        tier=researcher_case.tier,
        repetition=1,
        session_id="evaluation-multi-source-coverage",
        experiment_name="researcher-controlled-20260816T101500Z-abc1234",
        trace_url="https://smith.langchain.com/o/x/r/researcher-1",
        completed=True,
        failure=None,
        result={
            "findings": [
                {
                    "content": "COP stays above 2.0 at -15C in field trials.",
                    "source_url": urls[0],
                    "source_title": "NREL cold-climate heat pump study",
                },
                {
                    "content": "IEA reports broad cold-climate uptake.",
                    "source_url": urls[1],
                    "source_title": "IEA heat pump report",
                },
                {
                    "content": "Backup heating adds 15% annual energy use.",
                    "source_url": urls[2],
                    "source_title": (
                        "ScienceDirect backup heating analysis"
                    ),
                },
            ]
        },
        state_update={},
        errors=[],
        tracker_errors=[],
        react=ReActSummary(
            iterations=4,
            tool_calls=6,
            stop_reason="completed",
            max_iterations=researcher_case.expectations.max_iterations,
            tool_budget=researcher_case.expectations.max_tool_calls,
        ),
        dependencies=DependencyLedger(),
        evidence=EvidenceContext(scripted_search_urls=list(urls)),
        trajectory=[],
        target_model_requested="gpt-5.6-luna",
        target_model_returned="gpt-5.6-luna",
        target_reasoning_effort="low",
    )


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
