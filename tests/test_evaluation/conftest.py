"""Offline fixtures shared by the evaluation tests."""

from __future__ import annotations

import functools
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

from deep_research.agents.planner import ResearchPlanDraft, SubTopicDraft
from deep_research.agents.researcher import FindingDraft, SubTopicFindingsDraft
from deep_research.agents.steps import ReActDecision
from deep_research.evaluation.cases import (
    all_cases as _all_cases,
)
from deep_research.evaluation.cases import (
    cases_for,
)
from deep_research.evaluation.config import GitMetadata, build_runtime_config
from deep_research.evaluation.datasets import example_payload
from deep_research.evaluation.dependencies import (
    build_controlled_dependencies,
    build_live_dependencies,
)
from deep_research.evaluation.models import (
    CaseResult,
    DependencyLedger,
    EvaluationCase,
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
    TrajectoryStep,
)
from deep_research.evaluation.targets import RepetitionCounter, build_target
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.providers import OpenAIProviderError
from deep_research.utils.config import ConfigSettings
from tests.evaluation_fakes import FakeStructuredProvider


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


# --- Task 18: per-agent output builders for the agent-specific gate tests ---


class PlannerOutput(TargetOutput):
    """A planner repetition with builder helpers for the agent gate tests."""

    def with_sub_topics(self, count: int) -> "PlannerOutput":
        return self.model_copy(
            update={
                "result": {
                    "sub_topics": [
                        {
                            "title": f"Sub-topic {index}",
                            "rationale": f"Rationale {index}",
                            "search_queries": [f"query {index}"],
                            "success_criteria": [f"criterion {index}"],
                            "priority": index + 1,
                        }
                        for index in range(count)
                    ]
                }
            }
        )

    def with_titles(self, titles: list[str]) -> "PlannerOutput":
        return self.model_copy(
            update={
                "result": {
                    "sub_topics": [
                        {
                            "title": title,
                            "rationale": f"Rationale {index}",
                            "search_queries": [f"query {index}"],
                            "success_criteria": [f"criterion {index}"],
                            "priority": index + 1,
                        }
                        for index, title in enumerate(titles)
                    ]
                }
            }
        )


class ResearcherOutput(TargetOutput):
    """A researcher repetition with builder helpers for the agent gate tests.

    ``with_findings_for`` adds findings for the given subtopic titles,
    reusing a known source url from the existing findings, so callers grow
    the fixture's two default findings instead of replacing them.
    """

    def with_findings_for(self, titles: Sequence[str]) -> "ResearcherOutput":
        result = dict(self.result or {})
        findings = list(result.get("findings") or [])
        if not findings:
            raise AssertionError(
                "no existing finding to borrow a source url from"
            )
        template_url = findings[0]["source_url"]
        for title in titles:
            findings.append(
                {
                    "content": f"Evidence for {title}.",
                    "source_url": template_url,
                    "source_title": f"Source for {title}",
                    "related_sub_topic": title,
                }
            )
        return self.model_copy(
            update={"result": {**result, "findings": findings}}
        )

    def with_finding_url(self, url: str) -> "ResearcherOutput":
        result = dict(self.result or {})
        findings = [dict(item) for item in (result.get("findings") or [])]
        findings[0]["source_url"] = url
        return self.model_copy(
            update={"result": {**result, "findings": findings}}
        )


class SourceEvaluatorOutput(TargetOutput):
    """A source-evaluator repetition with builder helpers."""

    def drop_one_source(self) -> "SourceEvaluatorOutput":
        result = dict(self.result or {})
        sources = list(result.get("evaluated_sources") or [])
        return self.model_copy(
            update={"result": {**result, "evaluated_sources": sources[:-1]}}
        )

    def with_score(self, field: str, value: float) -> "SourceEvaluatorOutput":
        result = dict(self.result or {})
        sources = [dict(item) for item in (result.get("evaluated_sources") or [])]
        sources[0][field] = value
        return self.model_copy(
            update={"result": {**result, "evaluated_sources": sources}}
        )

    def clear_low_confidence(self) -> "SourceEvaluatorOutput":
        result = dict(self.result or {})
        sources = [dict(item) for item in (result.get("evaluated_sources") or [])]
        for source in sources:
            source["low_confidence"] = False
        return self.model_copy(
            update={"result": {**result, "evaluated_sources": sources}}
        )


class FactCheckerOutput(TargetOutput):
    """A fact-checker repetition with builder helpers."""

    def with_verified_claim_sources(
        self, urls: Sequence[str]
    ) -> "FactCheckerOutput":
        result = dict(self.result or {})
        claims = [dict(item) for item in (result.get("verified_claims") or [])]
        claims[0]["source_urls"] = list(urls)
        return self.model_copy(
            update={"result": {**result, "verified_claims": claims}}
        )

    def with_evidence_texts(self, texts: Sequence[str]) -> "FactCheckerOutput":
        result = dict(self.result or {})
        claims = [dict(item) for item in (result.get("verified_claims") or [])]
        claims[0]["evidence"] = list(texts)
        return self.model_copy(
            update={"result": {**result, "verified_claims": claims}}
        )

    def with_trajectory_urls(self, urls: Sequence[str]) -> "FactCheckerOutput":
        """Record one web_search step per URL, the shape a verification
        loop's tool calls take in the repetition's trajectory."""
        trajectory = [
            TrajectoryStep(
                iteration=index,
                thought="",
                tool_name="web_search",
                succeeded=True,
                observation_summary=f"Retrieved {url}.",
            )
            for index, url in enumerate(urls)
        ]
        return self.model_copy(update={"trajectory": trajectory})

    def with_empty_evidence(self) -> "FactCheckerOutput":
        result = dict(self.result or {})
        claims = [dict(item) for item in (result.get("verified_claims") or [])]
        claims[0]["evidence"] = []
        return self.model_copy(
            update={"result": {**result, "verified_claims": claims}}
        )

    def with_claim_verdict(
        self, verdict: str, *, confidence: float
    ) -> "FactCheckerOutput":
        result = dict(self.result or {})
        claims = [dict(item) for item in (result.get("verified_claims") or [])]
        claims[0]["verdict"] = verdict
        claims[0]["confidence"] = confidence
        return self.model_copy(
            update={"result": {**result, "verified_claims": claims}}
        )


class SynthesizerOutput(TargetOutput):
    """A synthesizer repetition with builder helpers."""

    def with_report_citing(self, url: str) -> "SynthesizerOutput":
        result = dict(self.result or {})
        report = str(result.get("report") or "")
        return self.model_copy(
            update={
                "result": {
                    **result,
                    "report": f"{report}\n\nSee {url} for details.",
                }
            }
        )

    def without_limitations(self) -> "SynthesizerOutput":
        result = dict(self.result or {})
        report = str(result.get("report") or "")
        return self.model_copy(
            update={
                "result": {
                    **result,
                    "report": report.split("## Limitations")[0].rstrip(),
                }
            }
        )

    def with_report_text(self, text: str) -> "SynthesizerOutput":
        result = dict(self.result or {})
        return self.model_copy(update={"result": {**result, "report": text}})


class CriticOutput(TargetOutput):
    """A critic repetition with builder helpers."""

    def _critique(self, **updates: object) -> "CriticOutput":
        result = dict(self.result or {})
        critique = dict(result.get("critique") or {})
        critique.update(updates)
        return self.model_copy(
            update={"result": {**result, "critique": critique}}
        )

    def with_score(self, score: int) -> "CriticOutput":
        return self._critique(score=score)

    def with_should_continue(self, value: bool) -> "CriticOutput":
        return self._critique(should_continue=value)

    def with_gaps(self, gaps: Sequence[str]) -> "CriticOutput":
        return self._critique(gaps=list(gaps))

    def with_recommended_queries(
        self, queries: Sequence[str]
    ) -> "CriticOutput":
        return self._critique(recommended_queries=list(queries))


@pytest.fixture
def controlled_case_for_id():
    """The controlled case with a specific case_id, or a skip."""

    def factory(agent_name, case_id):
        for case in cases_for(agent_name, "controlled"):
            if case.case_id == case_id:
                return case
        pytest.skip(
            f"no controlled case {case_id!r} registered for {agent_name} yet; "
            "cases land in Tasks 10-15"
        )

    return factory


@pytest.fixture
def source_evaluator_case(controlled_case_for):
    return controlled_case_for("source_evaluator")


@pytest.fixture
def fact_checker_case(controlled_case_for):
    return controlled_case_for("fact_checker")


@pytest.fixture
def fact_checker_dependent_case(controlled_case_for_id):
    """The case that genuinely declares minimum_independent_domains."""
    return controlled_case_for_id("fact_checker", "independent-domain-evidence")


@pytest.fixture
def critic_case(controlled_case_for):
    return controlled_case_for("critic")


@pytest.fixture
def critic_gap_case(controlled_case_for_id):
    return controlled_case_for_id("critic", "request-more-research")


@pytest.fixture
def critic_budget_case(controlled_case_for_id):
    return controlled_case_for_id("critic", "missing-evidence-or-budget-exhausted")


@pytest.fixture
def synthesizer_failure_case(controlled_case_for_id):
    return controlled_case_for_id("synthesizer", "write-or-memory-failure")


@pytest.fixture
def planner_output(planner_case) -> PlannerOutput:
    """Three distinct, prioritized subtopics for the focused case."""
    return PlannerOutput(
        case_id=planner_case.case_id,
        case_version=planner_case.version,
        agent_name=planner_case.agent_name,
        tier=planner_case.tier,
        repetition=1,
        session_id="evaluation-focused-decomposition",
        experiment_name="planner-controlled-20260816T101500Z-abc1234",
        trace_url="https://smith.langchain.com/o/x/r/planner-agent-1",
        completed=True,
        failure=None,
        result={
            "sub_topics": [
                {
                    "title": "Solid-state electrolyte degradation",
                    "rationale": "Electrolyte stability dominates cycle life.",
                    "search_queries": [
                        "solid-state electrolyte degradation mechanisms"
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
                {
                    "title": "Mechanical stress and cracking",
                    "rationale": "Stress from cycling drives crack formation.",
                    "search_queries": [
                        "solid-state battery cracking stress analysis"
                    ],
                    "success_criteria": ["Stress thresholds measured"],
                    "priority": 3,
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
def researcher_output(researcher_case) -> ResearcherOutput:
    """Findings for two of the three subtopics of the multi-source case.

    ``Cold-climate field trial outcomes`` is deliberately left uncovered so
    the ``sub_topic_covered`` tests can exercise the skip-error path.
    """
    urls = researcher_case.expectations.known_source_urls
    return ResearcherOutput(
        case_id=researcher_case.case_id,
        case_version=researcher_case.version,
        agent_name=researcher_case.agent_name,
        tier=researcher_case.tier,
        repetition=1,
        session_id="evaluation-multi-source-coverage",
        experiment_name="researcher-controlled-20260816T101500Z-abc1234",
        trace_url="https://smith.langchain.com/o/x/r/researcher-agent-1",
        completed=True,
        failure=None,
        result={
            "findings": [
                {
                    "content": "COP stays above 2.0 at -15C in field trials.",
                    "source_url": urls[0],
                    "source_title": "NREL cold-climate heat pump study",
                    "related_sub_topic": (
                        "Coefficient of performance below freezing"
                    ),
                },
                {
                    "content": "Backup heating adds 15% annual energy use.",
                    "source_url": urls[6],
                    "source_title": "NREL backup heating guidance",
                    "related_sub_topic": "Backup heating requirements",
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
def source_evaluator_output(
    source_evaluator_case,
) -> SourceEvaluatorOutput:
    """One evaluation per canonical source of the mixed case.

    The urls are the normalized forms of the case's raw findings, the way
    the production evaluator records them.
    """
    return SourceEvaluatorOutput(
        case_id=source_evaluator_case.case_id,
        case_version=source_evaluator_case.version,
        agent_name=source_evaluator_case.agent_name,
        tier=source_evaluator_case.tier,
        repetition=1,
        session_id="evaluation-strong-and-weak-sources",
        experiment_name="source-evaluator-controlled-20260816T101500Z-abc1234",
        trace_url="https://smith.langchain.com/o/x/r/source-evaluator-1",
        completed=True,
        failure=None,
        result={
            "evaluated_sources": [
                {
                    "url": "https://ipcc.ch/ar6-wg1",
                    "title": "IPCC AR6 Working Group I",
                    "authority_score": 0.90,
                    "recency_score": 0.85,
                    "relevance_score": 0.92,
                    "corroboration_score": 0.80,
                    "overall_score": 0.88,
                    "rationale": "Authoritative assessment with broad corroboration.",
                    "low_confidence": False,
                },
                {
                    "url": "https://journals.ametsoc.org/regional-precip",
                    "title": "AMS regional precipitation study",
                    "authority_score": 0.85,
                    "recency_score": 0.80,
                    "relevance_score": 0.90,
                    "corroboration_score": 0.78,
                    "overall_score": 0.84,
                    "rationale": "Peer-reviewed regional analysis.",
                    "low_confidence": False,
                },
                {
                    "url": "https://noaa.gov/precip-assessment",
                    "title": "NOAA precipitation assessment",
                    "authority_score": 0.88,
                    "recency_score": 0.82,
                    "relevance_score": 0.88,
                    "corroboration_score": 0.75,
                    "overall_score": 0.85,
                    "rationale": "Agency assessment with solid corroboration.",
                    "low_confidence": False,
                },
                {
                    "url": "https://weatherblog.example.com/my-take",
                    "title": "Weather blog commentary",
                    "authority_score": 0.30,
                    "recency_score": 0.60,
                    "relevance_score": 0.50,
                    "corroboration_score": 0.30,
                    "overall_score": 0.42,
                    "rationale": "Opinion piece without independent verification.",
                    "low_confidence": False,
                },
                {
                    "url": "https://forum.example.net/thread/1182",
                    "title": "Forum thread on precipitation",
                    "authority_score": 0.20,
                    "recency_score": 0.50,
                    "relevance_score": 0.45,
                    "corroboration_score": 0.25,
                    "overall_score": 0.35,
                    "rationale": "Anonymous forum discussion.",
                    "low_confidence": True,
                },
            ]
        },
        state_update={},
        errors=[],
        tracker_errors=[],
        react=ReActSummary(
            iterations=2,
            tool_calls=4,
            stop_reason="completed",
            max_iterations=source_evaluator_case.expectations.max_iterations,
            tool_budget=source_evaluator_case.expectations.max_tool_calls,
        ),
        dependencies=DependencyLedger(),
        evidence=EvidenceContext(),
        trajectory=[],
        target_model_requested="gpt-5.6-luna",
        target_model_returned="gpt-5.6-luna",
        target_reasoning_effort="low",
    )


@pytest.fixture
def fact_checker_output(fact_checker_case) -> FactCheckerOutput:
    """One verified claim whose evidence spans two independent domains."""
    return FactCheckerOutput(
        case_id=fact_checker_case.case_id,
        case_version=fact_checker_case.version,
        agent_name=fact_checker_case.agent_name,
        tier=fact_checker_case.tier,
        repetition=1,
        session_id="evaluation-mixed-verdicts",
        experiment_name="fact-checker-controlled-20260816T101500Z-abc1234",
        trace_url="https://smith.langchain.com/o/x/r/fact-checker-1",
        completed=True,
        failure=None,
        result={
            "verified_claims": [
                {
                    "text": (
                        "Small modular reactor designs must satisfy the same "
                        "international safety standards as large reactors."
                    ),
                    "source_urls": ["https://iaea.org/smr-safety-assessment"],
                    "verdict": "verified",
                    "confidence": 0.85,
                    "evidence": [
                        "The IAEA framework covers SMR designs "
                        "(https://syndication.news.example.com/c).",
                        "The NRC applies the same review "
                        "(https://world-nuclear.org/smr-safety-standards).",
                    ],
                    "contradictions": [],
                }
            ]
        },
        state_update={},
        errors=[],
        tracker_errors=[],
        react=ReActSummary(
            iterations=2,
            tool_calls=4,
            stop_reason="completed",
            max_iterations=fact_checker_case.expectations.max_iterations,
            tool_budget=fact_checker_case.expectations.max_tool_calls,
        ),
        dependencies=DependencyLedger(),
        evidence=EvidenceContext(),
        trajectory=[],
        target_model_requested="gpt-5.6-luna",
        target_model_returned="gpt-5.6-luna",
        target_reasoning_effort="low",
    )


@pytest.fixture
def fact_checker_dependent_output(
    fact_checker_dependent_case,
) -> FactCheckerOutput:
    """One verified claim whose corroboration is only same-family: the
    claim's own sources are the four outage findings (three news.example.com
    hats plus the regulator), the evidence strings paraphrase without
    pasting URLs, and the scripted verification search's two results —
    both news-family — are the only URLs the trajectory recorded."""
    return FactCheckerOutput(
        case_id=fact_checker_dependent_case.case_id,
        case_version=fact_checker_dependent_case.version,
        agent_name=fact_checker_dependent_case.agent_name,
        tier=fact_checker_dependent_case.tier,
        repetition=1,
        session_id="evaluation-independent-domain-evidence",
        experiment_name="fact-checker-controlled-20260816T101500Z-abc1234",
        trace_url="https://smith.langchain.com/o/x/r/fact-checker-2",
        completed=True,
        failure=None,
        result={
            "verified_claims": [
                {
                    "text": (
                        "The 2025 grid upgrade reduced outage minutes by "
                        "40 percent."
                    ),
                    "source_urls": [
                        "https://news.example.com/outage-coverage",
                        "https://news.example.com/outage-verification",
                        (
                            "https://syndication.news.example.com/"
                            "outage-syndication"
                        ),
                        "https://regulator.example.gov/outage-report",
                    ],
                    "verdict": "verified",
                    "confidence": 0.80,
                    "evidence": [
                        "A follow-up report confirms outage minutes fell "
                        "40 percent after the 2025 grid upgrade.",
                        "Syndicated outage statistics repeat the same "
                        "40 percent figure.",
                    ],
                    "contradictions": [],
                }
            ]
        },
        state_update={},
        errors=[],
        tracker_errors=[],
        react=ReActSummary(
            iterations=2,
            tool_calls=4,
            stop_reason="completed",
            max_iterations=fact_checker_dependent_case.expectations.max_iterations,
            tool_budget=fact_checker_dependent_case.expectations.max_tool_calls,
        ),
        dependencies=DependencyLedger(),
        evidence=EvidenceContext(),
        trajectory=[
            TrajectoryStep(
                iteration=0,
                thought="",
                tool_name="web_search",
                succeeded=True,
                observation_summary=(
                    "Retrieved https://news.example.com/outage-minutes-fall "
                    "which confirms the 40 percent figure."
                ),
            ),
            TrajectoryStep(
                iteration=1,
                thought="",
                tool_name="web_search",
                succeeded=True,
                observation_summary=(
                    "Retrieved https://syndication.news.example.com/"
                    "outage-minutes-fall, the same 40 percent figure."
                ),
            ),
        ],
        target_model_requested="gpt-5.6-luna",
        target_model_returned="gpt-5.6-luna",
        target_reasoning_effort="low",
    )


@pytest.fixture
def synthesizer_output(synthesizer_case) -> SynthesizerOutput:
    """A complete report citing only known sources, with limitations."""
    urls = synthesizer_case.expectations.known_source_urls
    report = (
        "## Summary\n\n"
        f"London congestion charging evidence shows travel times fell after "
        f"the charge was introduced ({urls[0]}).\n\n"
        "## Findings\n\n"
        f"New York congestion pricing results point to similar reductions "
        f"({urls[1]}). Evidence beyond London and New York is thinner "
        f"({urls[2]}).\n\n"
        "## Limitations\n\n"
        "The evidence is limited to a few cities and short evaluation windows."
    )
    return SynthesizerOutput(
        case_id=synthesizer_case.case_id,
        case_version=synthesizer_case.version,
        agent_name=synthesizer_case.agent_name,
        tier=synthesizer_case.tier,
        repetition=1,
        session_id="evaluation-complete-cited-report",
        experiment_name="synthesizer-controlled-20260816T101500Z-abc1234",
        trace_url="https://smith.langchain.com/o/x/r/synthesizer-1",
        completed=True,
        failure=None,
        result={"report": report},
        state_update={"report": report},
        errors=[],
        tracker_errors=[],
        react=ReActSummary(
            iterations=2,
            tool_calls=3,
            stop_reason="completed",
            max_iterations=synthesizer_case.expectations.max_iterations,
            tool_budget=synthesizer_case.expectations.max_tool_calls,
        ),
        dependencies=DependencyLedger(document_writes=1),
        evidence=EvidenceContext(),
        trajectory=[],
        target_model_requested="gpt-5.6-luna",
        target_model_returned="gpt-5.6-luna",
        target_reasoning_effort="medium",
    )


@pytest.fixture
def synthesizer_failure_output(
    synthesizer_failure_case,
) -> SynthesizerOutput:
    """The write failed: zero ledger writes, no output_path, no saved-claim."""
    urls = synthesizer_failure_case.expectations.known_source_urls
    report = (
        "## Summary\n\n"
        f"Deep retrofit field studies show realized savings below modeled "
        f"savings ({urls[0]}).\n\n"
        "## Limitations\n\n"
        "The evidence base is still small."
    )
    return SynthesizerOutput(
        case_id=synthesizer_failure_case.case_id,
        case_version=synthesizer_failure_case.version,
        agent_name=synthesizer_failure_case.agent_name,
        tier=synthesizer_failure_case.tier,
        repetition=1,
        session_id="evaluation-write-or-memory-failure",
        experiment_name="synthesizer-controlled-20260816T101500Z-abc1234",
        trace_url="https://smith.langchain.com/o/x/r/synthesizer-failure-1",
        completed=True,
        failure=None,
        result={"report": report},
        state_update={"report": report},
        errors=[
            {
                "error_type": "write_failed",
                "source": "write_document",
                "message": "output directory was not writable",
                "timestamp": "2026-08-01T00:00:00+00:00",
                "recoverable": True,
            }
        ],
        tracker_errors=[],
        react=ReActSummary(
            iterations=2,
            tool_calls=3,
            stop_reason="completed",
            max_iterations=synthesizer_failure_case.expectations.max_iterations,
            tool_budget=synthesizer_failure_case.expectations.max_tool_calls,
        ),
        dependencies=DependencyLedger(document_writes=0),
        evidence=EvidenceContext(),
        trajectory=[],
        target_model_requested="gpt-5.6-luna",
        target_model_returned="gpt-5.6-luna",
        target_reasoning_effort="medium",
    )


@pytest.fixture
def critic_output(critic_case) -> CriticOutput:
    """A strong-report approval: score 9, end routing, no gaps."""
    return CriticOutput(
        case_id=critic_case.case_id,
        case_version=critic_case.version,
        agent_name=critic_case.agent_name,
        tier=critic_case.tier,
        repetition=1,
        session_id="evaluation-approve-strong-report",
        experiment_name="critic-controlled-20260816T101500Z-abc1234",
        trace_url="https://smith.langchain.com/o/x/r/critic-1",
        completed=True,
        failure=None,
        result={
            "critique": {
                "score": 9,
                "gaps": [],
                "unsupported_claims": [],
                "recommended_queries": [],
                "should_continue": False,
                "rationale": (
                    "The report covers the measured surface temperature "
                    "reductions and states its limitations."
                ),
            }
        },
        state_update={},
        errors=[],
        tracker_errors=[],
        react=ReActSummary(
            iterations=1,
            tool_calls=1,
            stop_reason="completed",
            max_iterations=critic_case.expectations.max_iterations,
            tool_budget=critic_case.expectations.max_tool_calls,
        ),
        dependencies=DependencyLedger(),
        evidence=EvidenceContext(),
        trajectory=[],
        target_model_requested="gpt-5.6-luna",
        target_model_returned="gpt-5.6-luna",
        target_reasoning_effort="medium",
    )


@pytest.fixture
def critic_gap_output(critic_gap_case) -> CriticOutput:
    """A request for more research: low score with actionable gaps."""
    return CriticOutput(
        case_id=critic_gap_case.case_id,
        case_version=critic_gap_case.version,
        agent_name=critic_gap_case.agent_name,
        tier=critic_gap_case.tier,
        repetition=1,
        session_id="evaluation-request-more-research",
        experiment_name="critic-controlled-20260816T101500Z-abc1234",
        trace_url="https://smith.langchain.com/o/x/r/critic-gap-1",
        completed=True,
        failure=None,
        result={
            "critique": {
                "score": 5,
                "gaps": [
                    "participation rates",
                    "methane measurement methodology",
                ],
                "unsupported_claims": [],
                "recommended_queries": [
                    "municipal composting mandates participation rates"
                ],
                "should_continue": True,
                "rationale": (
                    "Participation rates and measurement methodology are "
                    "missing from the evidence."
                ),
            }
        },
        state_update={},
        errors=[],
        tracker_errors=[],
        react=ReActSummary(
            iterations=1,
            tool_calls=1,
            stop_reason="completed",
            max_iterations=critic_gap_case.expectations.max_iterations,
            tool_budget=critic_gap_case.expectations.max_tool_calls,
        ),
        dependencies=DependencyLedger(),
        evidence=EvidenceContext(),
        trajectory=[],
        target_model_requested="gpt-5.6-luna",
        target_model_returned="gpt-5.6-luna",
        target_reasoning_effort="medium",
    )


@pytest.fixture
def critic_budget_output(critic_budget_case) -> CriticOutput:
    """Budget exhausted: the critique stops and records the failure."""
    return CriticOutput(
        case_id=critic_budget_case.case_id,
        case_version=critic_budget_case.version,
        agent_name=critic_budget_case.agent_name,
        tier=critic_budget_case.tier,
        repetition=1,
        session_id="evaluation-missing-evidence-or-budget-exhausted",
        experiment_name="critic-controlled-20260816T101500Z-abc1234",
        trace_url="https://smith.langchain.com/o/x/r/critic-budget-1",
        completed=True,
        failure=None,
        result={
            "critique": {
                "score": 4,
                "gaps": [],
                "unsupported_claims": [],
                "recommended_queries": [],
                "should_continue": False,
                "rationale": "The evidence is thin and no iteration budget remains.",
            }
        },
        state_update={},
        errors=[
            {
                "error_type": "search_unavailable",
                "source": "web_search",
                "message": "the scripted provider failure recurred",
                "timestamp": "2026-08-01T00:00:00+00:00",
                "recoverable": True,
            }
        ],
        tracker_errors=[],
        react=ReActSummary(
            iterations=3,
            tool_calls=6,
            stop_reason="completed",
            max_iterations=critic_budget_case.expectations.max_iterations,
            tool_budget=critic_budget_case.expectations.max_tool_calls,
        ),
        dependencies=DependencyLedger(),
        evidence=EvidenceContext(),
        trajectory=[],
        target_model_requested="gpt-5.6-luna",
        target_model_returned="gpt-5.6-luna",
        target_reasoning_effort="medium",
    )


# --- Task 19: judge fixtures ---


@pytest.fixture
def clean_gate_report() -> GateReport:
    """Every deterministic gate passed; the judge must still run."""
    return GateReport(
        results=[
            GateResult(gate_id="run_completed", passed=True, detail=""),
            GateResult(
                gate_id="required_output_fields", passed=True, detail=""
            ),
        ]
    )


@pytest.fixture
def failing_gate_report() -> GateReport:
    """A hard gate failed, but the run still produced evaluable output.

    A gate failure is information for the judge, never a zero score.
    """
    return GateReport(
        results=[
            GateResult(gate_id="run_completed", passed=True, detail=""),
            GateResult(
                gate_id="required_output_fields",
                passed=False,
                detail="sub_topics missing",
            ),
        ]
    )


@pytest.fixture
def failed_target_output(planner_case) -> TargetOutput:
    """A repetition with no evaluable output: the judge must not run."""
    return TargetOutput(
        case_id=planner_case.case_id,
        case_version=planner_case.version,
        agent_name=planner_case.agent_name,
        tier=planner_case.tier,
        repetition=1,
        session_id="evaluation-focused-decomposition",
        experiment_name="planner-controlled-20260816T101500Z-abc1234",
        trace_url=None,
        completed=False,
        failure=None,
        result=None,
        state_update={},
        errors=[],
        tracker_errors=[],
        react=None,
        dependencies=DependencyLedger(),
        evidence=EvidenceContext(),
        trajectory=[],
        target_model_requested="gpt-5.6-luna",
        target_model_returned=None,
        target_reasoning_effort="medium",
    )


# --- Task 21: build_target harnesses ----------------------------------------


def _finish_decision(answer: str = "Scoping complete.") -> ReActDecision:
    return ReActDecision(
        thought="I have enough context to proceed.",
        action="finish",
        tool_input_json="{}",
        final_answer=answer,
    )


def _planner_draft(*titles: str) -> ResearchPlanDraft:
    return ResearchPlanDraft(
        sub_topics=[
            SubTopicDraft(
                title=title,
                rationale=f"Rationale for {title}.",
                search_queries=[f"query about {title}"],
                success_criteria=[f"evidence about {title}"],
                priority=index,
            )
            for index, title in enumerate(titles, start=1)
        ]
    )


def _planner_responses(*, leak: str | None = None) -> list:
    """One finishing decision, then a valid three-subtopic plan draft.

    When ``leak`` is given, it is embedded in the first subtopic's
    rationale so the target's redaction step has a real secret to catch.
    """
    draft = _planner_draft(
        "Solid-state electrolyte degradation",
        "Cathode interface resistance",
        "Mechanical stress and cracking",
    )
    if leak is not None:
        first = draft.sub_topics[0].model_copy(
            update={"rationale": f"{draft.sub_topics[0].rationale} key={leak}"}
        )
        draft = draft.model_copy(
            update={"sub_topics": [first, *draft.sub_topics[1:]]}
        )
    return [_finish_decision(), draft]


@pytest.fixture
def target_harness(tracker, settings, tmp_path):
    """A real ``build_target`` over a controlled planner case.

    The provider is a ``FakeStructuredProvider`` scripted with the
    planner's real schemas (``ReActDecision``, then ``ResearchPlanDraft``);
    nothing here is a hand-built fixture.
    """

    def factory(case, runtime, *, secrets: Sequence[str] = ()):
        del case
        counter = RepetitionCounter(max_concurrency=1)
        return build_target(
            runtime,
            settings,
            tracker_factory=lambda: tracker,
            dependency_factory=build_controlled_dependencies,
            provider_factory=lambda: FakeStructuredProvider(
                _planner_responses()
            ),
            counter=counter,
            secrets=secrets,
            root=tmp_path,
        )

    return factory


@pytest.fixture
def failing_target_harness(tracker, settings, tmp_path):
    """A ``build_target`` whose provider fails while the plan is requested.

    The ReAct loop itself finishes cleanly (the decide call succeeds), but
    the finalize-stage plan request raises ``OpenAIProviderError``, which
    the planner re-raises chained as ``PlanningError`` — exercising the
    target's cause-chain classification, not just a bare isinstance check.
    """

    def factory(case, runtime, *, secrets: Sequence[str] = ()):
        del case
        counter = RepetitionCounter(max_concurrency=1)

        def provider_factory():
            return FakeStructuredProvider(
                [
                    _finish_decision(),
                    OpenAIProviderError(
                        "the model provider is unavailable"
                    ),
                ]
            )

        return build_target(
            runtime,
            settings,
            tracker_factory=lambda: tracker,
            dependency_factory=build_controlled_dependencies,
            provider_factory=provider_factory,
            counter=counter,
            secrets=secrets,
            root=tmp_path,
        )

    return factory


@pytest.fixture
def leaking_target_harness(tracker, settings, tmp_path):
    """A ``build_target`` whose scripted plan carries a real secret value."""

    def factory(case, runtime, *, secrets: Sequence[str] = ()):
        del case
        counter = RepetitionCounter(max_concurrency=1)
        leak = secrets[0] if secrets else None
        return build_target(
            runtime,
            settings,
            tracker_factory=lambda: tracker,
            dependency_factory=build_controlled_dependencies,
            provider_factory=lambda: FakeStructuredProvider(
                _planner_responses(leak=leak)
            ),
            counter=counter,
            secrets=secrets,
            root=tmp_path,
        )

    return factory


_LIVE_FINDING_URL = "https://example.com/sodium-ion-energy-density"


@pytest.fixture
def live_target_harness(tracker, settings, tmp_path):
    """A ``build_target`` over a live researcher case.

    ``search_client`` is a fake so no network call is ever made;
    ``embeddings=object()`` mirrors Task 8's own live-dependency tests
    (memory is never queried by this scripted run, so the placeholder is
    never called).
    """

    class _FakeSearchClient:
        def search(self, *, query, search_depth, max_results):
            del query, search_depth, max_results
            return {
                "results": [
                    {
                        "url": _LIVE_FINDING_URL,
                        "title": "Sodium-ion energy density report",
                        "content": (
                            "Cell-level energy density reported at "
                            "160 Wh/kg."
                        ),
                        "score": 0.9,
                    }
                ]
            }

    def factory(case, runtime, *, secrets: Sequence[str] = ()):
        del case
        counter = RepetitionCounter(max_concurrency=1)
        environ = {
            "OPENAI_API_KEY": "sk-abcdefghijklmnop",
            "LANGSMITH_API_KEY": "ls-abcdefghijklmnop",
            "TAVILY_API_KEY": "tvly-abcdefghijklmnop",
        }
        dependency_factory = functools.partial(
            build_live_dependencies,
            environ=environ,
            search_client=_FakeSearchClient(),
            embeddings=object(),
        )

        def provider_factory():
            return FakeStructuredProvider(
                [
                    ReActDecision(
                        thought="Search for the reported energy density.",
                        action="use_tool",
                        tool_name="web_search",
                        tool_input_json=(
                            '{"query": '
                            '"sodium-ion battery energy density Wh/kg 2026"}'
                        ),
                    ),
                    _finish_decision("Found relevant data."),
                    SubTopicFindingsDraft(
                        findings=[
                            FindingDraft(
                                content=(
                                    "Cell-level energy density reported at "
                                    "160 Wh/kg."
                                ),
                                source_url=_LIVE_FINDING_URL,
                                source_title="Sodium-ion energy density report",
                                confidence=0.8,
                            )
                        ]
                    ),
                ]
            )

        return build_target(
            runtime,
            settings,
            tracker_factory=lambda: tracker,
            dependency_factory=dependency_factory,
            provider_factory=provider_factory,
            counter=counter,
            secrets=secrets,
            root=tmp_path,
        )

    return factory


# --- Task 23: runner harnesses -----------------------------------------------


@pytest.fixture
def repetitions_at():
    """Build scored, gate-passing ``RepetitionResult``s at given scores.

    Every repetition uses the same fixed case identity and a fresh trace
    url ``.../r{n}`` (1-based), so the lowest-scoring-trace test can assert
    on the exact suffix.
    """

    def factory(scores: Sequence[float]) -> list[RepetitionResult]:
        results = []
        for index, score in enumerate(scores):
            repetition = index + 1
            results.append(
                RepetitionResult(
                    case_id="focused-decomposition",
                    case_version=1,
                    repetition=repetition,
                    completed=True,
                    gates=GateReport(
                        results=[
                            GateResult(
                                gate_id="run_completed", passed=True, detail=""
                            )
                        ]
                    ),
                    deterministic_quality=score,
                    judge=JudgeFeedback(
                        status="scored",
                        verdict=JudgeVerdict(
                            scores=JudgeScores(
                                role_adherence=score,
                                completeness=score,
                                groundedness=score,
                                reasoning_quality=score,
                                usefulness=score,
                                uncertainty_calibration=score,
                            ),
                            rationale="Scripted for aggregation tests.",
                        ),
                        judge_quality=score,
                        prompt_id="individual-agent-judge",
                        rubric_version=1,
                        prompt_fingerprint="abc123abc123",
                        judge_model="gpt-5.6-luna",
                        judge_configuration_fingerprint="def456def456",
                    ),
                    aggregate_quality=score,
                    trace_url=f"https://smith.langchain.com/o/x/r/r{repetition}",
                    errors=[],
                )
            )
        return results

    return factory


@pytest.fixture
def repetition_without_judge() -> RepetitionResult:
    """A completed, gate-passing repetition whose judge never ran.

    ``aggregate_quality`` must be ``None``: a repetition with no aggregate
    score never passes, regardless of a strong deterministic score.
    """
    return RepetitionResult(
        case_id="focused-decomposition",
        case_version=1,
        repetition=99,
        completed=True,
        gates=GateReport(
            results=[GateResult(gate_id="run_completed", passed=True, detail="")]
        ),
        deterministic_quality=0.9,
        judge=JudgeFeedback(
            status="judge_not_run",
            not_run_reason="no_evaluable_output",
            prompt_id="individual-agent-judge",
            rubric_version=1,
            prompt_fingerprint="abc123abc123",
            judge_model="gpt-5.6-luna",
            judge_configuration_fingerprint="def456def456",
        ),
        aggregate_quality=None,
        trace_url="https://smith.langchain.com/o/x/r/no-judge",
        errors=[],
    )


@pytest.fixture
def repetition_with_failed_gate() -> RepetitionResult:
    """A repetition with a very high score but one failed hard gate.

    ``passed`` on the enclosing case must never be offset by the score: a
    failed hard gate is an independent, non-negotiable AND-condition.
    """
    return RepetitionResult(
        case_id="focused-decomposition",
        case_version=1,
        repetition=98,
        completed=True,
        gates=GateReport(
            results=[
                GateResult(
                    gate_id="no_prohibited_calls",
                    passed=False,
                    detail="a prohibited call was recorded",
                )
            ]
        ),
        deterministic_quality=1.0,
        judge=JudgeFeedback(
            status="scored",
            verdict=JudgeVerdict(
                scores=JudgeScores(
                    role_adherence=1.0,
                    completeness=1.0,
                    groundedness=1.0,
                    reasoning_quality=1.0,
                    usefulness=1.0,
                    uncertainty_calibration=1.0,
                ),
                rationale="Scripted: a high score that must not offset a gate.",
            ),
            judge_quality=1.0,
            prompt_id="individual-agent-judge",
            rubric_version=1,
            prompt_fingerprint="abc123abc123",
            judge_model="gpt-5.6-luna",
            judge_configuration_fingerprint="def456def456",
        ),
        aggregate_quality=1.0,
        trace_url="https://smith.langchain.com/o/x/r/failed-gate",
        errors=[],
    )


@pytest.fixture
def passing_cases(repetitions_at):
    """Two ``CaseResult``s, both passing under a 0.80 threshold."""
    from deep_research.evaluation.runner import build_case_result

    return [
        build_case_result(
            None, repetitions_at([0.90, 0.90, 0.90]), threshold=0.80
        ),
        build_case_result(
            None, repetitions_at([0.85, 0.85, 0.85]), threshold=0.80
        ),
    ]


@pytest.fixture
def failing_case(repetitions_at):
    """One ``CaseResult`` that fails under a 0.80 threshold."""
    from deep_research.evaluation.runner import build_case_result

    return build_case_result(
        None, repetitions_at([0.10, 0.10, 0.10]), threshold=0.80
    )


def _judge_verdict(value: float = 0.85) -> JudgeVerdict:
    """A generic, schema-valid judge verdict for the async harness tests.

    The exact score is not asserted by any Task 23 test; only
    ``status == "scored"`` is. Kept high and uniform on purpose so nothing
    here accidentally trips a threshold test elsewhere.
    """
    return JudgeVerdict(
        scores=JudgeScores(
            role_adherence=value,
            completeness=value,
            groundedness=value,
            reasoning_quality=value,
            usefulness=value,
            uncertainty_calibration=value,
        ),
        agent_specific={"decomposition_quality": value},
        rationale="Scripted judge verdict for the runner harness tests.",
    )


class _FakePlannerSearchClient:
    """A live-tier search double the planner's scripted flow never calls.

    The scripted decision always finishes immediately, so this only needs
    to exist to satisfy ``build_live_dependencies``' constructive guard.
    """

    def search(self, *, query, search_depth, max_results):
        del query, search_depth, max_results
        return {"results": []}


@dataclass
class _EvaluationHarness:
    """A bundle of cases, LangSmith-shaped examples, and ``run_agent_evaluation``
    kwargs, consistent enough with each other that ``FakeEvaluateRunner``
    driving them through the real ``build_target`` chain produces one
    scored repetition per case per repetition.
    """

    cases: list[EvaluationCase]
    examples: list[dict]
    factory_kwargs: dict = field(default_factory=dict)

    def kwargs(self, tmp_path: Path) -> dict:
        return {**self.factory_kwargs, "root": tmp_path / "runs"}

    def for_case(self, case_id: str) -> "_EvaluationHarness":
        case = next(item for item in self.cases if item.case_id == case_id)
        examples = [
            example
            for example in self.examples
            if example["inputs"]["case_id"] == case_id
        ]
        return _EvaluationHarness(
            cases=[case], examples=examples, factory_kwargs=self.factory_kwargs
        )


@pytest.fixture
def evaluation_harness(tracker, settings):
    """The three planner controlled cases, ready for a real controlled run.

    The target provider is scripted identically for every repetition (one
    "finish" decision, then a valid three-subtopic plan draft) -- the same
    script ``target_harness`` already proves works end to end for the
    focused-decomposition case; the fake provider never inspects the
    prompt, so the same script is agent-flow-shape-valid for the other two
    controlled cases too. The judge provider is a fresh, three-verdict
    queue per case, matching the controlled tier's three repetitions.
    """
    cases = list(cases_for("planner", "controlled"))
    examples = [example_payload(case, rubric_version=1) for case in cases]

    def target_provider_factory():
        return FakeStructuredProvider(_planner_responses())

    def judge_provider_factory():
        return FakeStructuredProvider([_judge_verdict() for _ in range(3)])

    return _EvaluationHarness(
        cases=cases,
        examples=examples,
        factory_kwargs=dict(
            target_provider_factory=target_provider_factory,
            judge_provider_factory=judge_provider_factory,
            tracker_factory=lambda: tracker,
            dependency_factory=build_controlled_dependencies,
            secrets=(),
        ),
    )


@pytest.fixture
def live_evaluation_harness(tracker, settings):
    """The planner's one live case, ready for a real live-tier run."""
    case = next(
        item
        for item in cases_for("planner", "live")
        if item.case_id == "planner-live-scope"
    )
    examples = [example_payload(case, rubric_version=1)]

    def target_provider_factory():
        return FakeStructuredProvider(_planner_responses())

    def judge_provider_factory():
        return FakeStructuredProvider([_judge_verdict()])

    environ = {
        "OPENAI_API_KEY": "sk-abcdefghijklmnop",
        "LANGSMITH_API_KEY": "ls-abcdefghijklmnop",
        "TAVILY_API_KEY": "tvly-abcdefghijklmnop",
    }
    dependency_factory = functools.partial(
        build_live_dependencies,
        environ=environ,
        search_client=_FakePlannerSearchClient(),
        embeddings=object(),
    )

    return _EvaluationHarness(
        cases=[case],
        examples=examples,
        factory_kwargs=dict(
            target_provider_factory=target_provider_factory,
            judge_provider_factory=judge_provider_factory,
            tracker_factory=lambda: tracker,
            dependency_factory=dependency_factory,
            secrets=(),
        ),
    )


@pytest.fixture
def partially_failing_harness(tracker, settings):
    """The three planner controlled cases, with one repetition scripted to
    fail during the model call (a provider failure, the same shape
    ``failing_target_harness`` already proves the target captures as a
    typed, non-escaping failure) -- so the case owning that repetition
    fails while the other two cases, and the other eight repetitions,
    complete normally.
    """
    cases = list(cases_for("planner", "controlled"))
    examples = [example_payload(case, rubric_version=1) for case in cases]

    call_count = {"n": 0}

    def target_provider_factory():
        call_count["n"] += 1
        if call_count["n"] == 4:
            return FakeStructuredProvider(
                [
                    _finish_decision(),
                    OpenAIProviderError(
                        "the model provider is unavailable"
                    ),
                ]
            )
        return FakeStructuredProvider(_planner_responses())

    def judge_provider_factory():
        return FakeStructuredProvider([_judge_verdict() for _ in range(3)])

    return _EvaluationHarness(
        cases=cases,
        examples=examples,
        factory_kwargs=dict(
            target_provider_factory=target_provider_factory,
            judge_provider_factory=judge_provider_factory,
            tracker_factory=lambda: tracker,
            dependency_factory=build_controlled_dependencies,
            secrets=(),
        ),
    )
