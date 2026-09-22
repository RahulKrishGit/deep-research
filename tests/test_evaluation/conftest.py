"""Offline fixtures shared by the evaluation tests."""

from __future__ import annotations

import functools
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

from deep_research.agents.identity import claim_fingerprint
from deep_research.agents.planner import (
    EvidenceTargetDraft,
    PlanReviewDraft,
    ResearchPlanDraft,
    SubTopicDraft,
    target_id_for,
)
from deep_research.agents.researcher import FindingDraft, SubTopicFindingsDraft
from deep_research.agents.steps import ReActDecision, ReActStep
from deep_research.evaluation.cases import (
    all_cases as _all_cases,
)
from deep_research.evaluation.cases import (
    case_by_id,
    cases_for,
)
from deep_research.evaluation.config import (
    GitMetadata,
    build_runtime_config,
    redact_secrets,
)
from deep_research.evaluation.datasets import example_payload
from deep_research.evaluation.dependencies import (
    bounded_url_fingerprints,
    build_controlled_dependencies,
    build_live_dependencies,
    read_url_fingerprints,
)
from deep_research.evaluation.models import (
    CaseResult,
    DependencyLedger,
    EvaluationCase,
    EvaluationFailure,
    EvidenceContext,
    ExperimentResult,
    GateReport,
    GateResult,
    JudgeFeedback,
    JudgeScores,
    JudgeVerdict,
    ReActSummary,
    RepetitionResult,
    SuiteResult,
    TargetOutput,
    TrajectoryStep,
)
from deep_research.evaluation.targets import RepetitionCounter, build_target
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.providers import OpenAIProviderError
from deep_research.utils.config import ConfigSettings
from tests.evaluation_fakes import (
    FakeEvaluateRunner,
    FakeLangSmithClient,
    FakeStructuredProvider,
)


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
def critic_live_case(live_case_for):
    return live_case_for("critic")


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
                    "coverage_id": "topic-01",
                    "title": "Solid-state electrolyte degradation",
                    "rationale": "Electrolyte stability dominates cycle life.",
                    "search_queries": [
                        "solid-state electrolyte degradation mechanism"
                    ],
                    "success_criteria": ["Crack propagation data"],
                    "priority": 1,
                },
                {
                    "coverage_id": "topic-02",
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
            stop_reason="finished",
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
def leaking_target_output(planner_case) -> TargetOutput:
    """A planner repetition whose raw fields carry real-looking secrets.

    Nothing here is pre-redacted: the point of this fixture is to prove
    that ``build_judge_input`` redacts every block itself rather than
    trusting the target output to already be clean.
    """
    return TargetOutput(
        case_id=planner_case.case_id,
        case_version=planner_case.version,
        agent_name=planner_case.agent_name,
        tier=planner_case.tier,
        repetition=1,
        session_id="evaluation-focused-decomposition",
        experiment_name="planner-controlled-20260816T101500Z-abc1234",
        trace_url="https://smith.langchain.com/o/x/r/planner-leak-1",
        completed=True,
        failure=None,
        result={
            "sub_topics": [
                {
                    "coverage_id": "topic-01",
                    "title": "Solid-state electrolyte degradation",
                    "rationale": (
                        "Electrolyte stability dominates cycle life. "
                        "key=sk-abcdefghijklmnop"
                    ),
                    "search_queries": [
                        "solid-state electrolyte degradation mechanism"
                    ],
                    "success_criteria": ["Crack propagation data"],
                    "priority": 1,
                },
            ]
        },
        state_update={
            "note": "planned one subtopic token=ls-abcdefghijklmnop"
        },
        errors=[],
        tracker_errors=[],
        react=ReActSummary(
            iterations=2,
            tool_calls=3,
            stop_reason="finished",
            max_iterations=planner_case.expectations.max_iterations,
            tool_budget=planner_case.expectations.max_tool_calls,
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
                    "Retrieved data using key tvly-abcdefghij for lookup."
                ),
            ),
        ],
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
            stop_reason="finished",
            max_iterations=researcher_case.expectations.max_iterations,
            tool_budget=researcher_case.expectations.max_tool_calls,
        ),
        dependencies=DependencyLedger(),
        evidence=EvidenceContext(scripted_search_urls=list(urls)),
        trajectory=[],
        target_model_requested="deepseek-v4-flash",
        target_model_returned="deepseek-v4-flash",
        target_reasoning_effort="high",
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


def _read_trajectory(urls: list[str]) -> list[TrajectoryStep]:
    """Trajectory steps of a run that READ each URL, not merely found it."""
    return [
        TrajectoryStep(
            iteration=index,
            thought="",
            tool_name="web_scraper",
            succeeded=True,
            observation_summary=f"Read {url}.",
        )
        for index, url in enumerate(urls)
    ]


def _ledger_with_reads(
    ledger: DependencyLedger,
    fingerprints: list[str],
    *,
    complete: bool,
) -> DependencyLedger:
    """Return ``ledger`` plus the bounded read identities of a run."""
    return ledger.model_copy(
        update={
            "read_url_fingerprints": list(fingerprints),
            "read_url_fingerprints_complete": complete,
        }
    )


def _read_ledger(urls: Sequence[str]) -> DependencyLedger:
    """A ledger proving the run READ exactly ``urls``."""
    fingerprints, complete = bounded_url_fingerprints(urls)
    return _ledger_with_reads(
        DependencyLedger(), fingerprints, complete=complete
    )


class PlannerOutput(TargetOutput):
    """A planner repetition with builder helpers for the agent gate tests.

    Each rebuilt sub-topic carries the ``topic-NN`` id the Planner stamps for
    its position, so a rebuilt repetition stays something the Planner's own
    ``valid_subtopics`` gate accepts.
    """

    def with_sub_topics(self, count: int) -> "PlannerOutput":
        return self.model_copy(
            update={
                "result": {
                    "sub_topics": [
                        {
                            "coverage_id": f"topic-{index + 1:02d}",
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
                            "coverage_id": f"topic-{index + 1:02d}",
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

    def with_evidence_targets(
        self, by_title: Mapping[str, Sequence[dict[str, object]]]
    ) -> "PlannerOutput":
        """Replace the evidence targets of the sub-topics named in ``by_title``."""
        result = dict(self.result or {})
        sub_topics = []
        for entry in result.get("sub_topics") or []:
            topic = dict(entry)
            replacement = by_title.get(str(topic.get("title")))
            if replacement is not None:
                topic["evidence_targets"] = [
                    dict(target) for target in replacement
                ]
            sub_topics.append(topic)
        return self.model_copy(
            update={"result": {**result, "sub_topics": sub_topics}}
        )

    def without_evidence_targets(self) -> "PlannerOutput":
        """Every sub-topic planned with no evidence target at all."""
        return self._map_sub_topics(
            lambda topic: {**topic, "evidence_targets": []}
        )

    def with_target_dimensions(
        self, dimensions: Sequence[str]
    ) -> "PlannerOutput":
        """Rewrite every planned target's required dimensions."""
        return self._map_targets(
            lambda target: {**target, "required_dimensions": list(dimensions)}
        )

    def with_target_policy(
        self,
        target_id: str,
        policy: str,
        *,
        question: str | None = None,
    ) -> "PlannerOutput":
        """Rewrite one target's support policy, and its question when given.

        The policy is set here rather than derived: these fixtures stage a
        plan the Planner has already stamped, so the helper writes what the
        Planner *would* have written for the replacement question.
        """

        def rewrite(target: dict[str, object]) -> dict[str, object]:
            updated = dict(target)
            if updated.get("target_id") != target_id:
                return updated
            updated["support_policy"] = policy
            if question is not None:
                updated["question"] = question
            return updated

        return self._map_targets(rewrite)

    def _map_sub_topics(
        self, transform: Callable[[dict[str, object]], dict[str, object]]
    ) -> "PlannerOutput":
        result = dict(self.result or {})
        return self.model_copy(
            update={
                "result": {
                    **result,
                    "sub_topics": [
                        transform(dict(entry))
                        for entry in result.get("sub_topics") or []
                    ],
                }
            }
        )

    def _map_targets(
        self, transform: Callable[[dict[str, object]], dict[str, object]]
    ) -> "PlannerOutput":
        return self._map_sub_topics(
            lambda topic: {
                **topic,
                "evidence_targets": [
                    transform(dict(target))
                    for target in topic.get("evidence_targets") or []
                ],
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

    def with_read_urls(
        self, urls: Sequence[str], *, complete: bool = True
    ) -> "ResearcherOutput":
        """Declare exactly which URLs this repetition READ.

        ``complete=False`` models an artifact that lost read identities, which
        the read-bearing metric must treat as unable to prove anything.
        """
        fingerprints, derived_complete = bounded_url_fingerprints(urls)
        return self.model_copy(
            update={
                "dependencies": _ledger_with_reads(
                    self.dependencies,
                    fingerprints,
                    complete=complete and derived_complete,
                )
            }
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

    def with_source_identity(
        self, url: str, **fields: object
    ) -> "SourceEvaluatorOutput":
        """Rewrite one scored source's read-derived identity fields.

        Publisher, work, transport relation, and role are the fields the
        work-identity metrics read, so a mutation has to be able to set one of
        them — including setting ``publisher_id`` to ``None`` — without
        rebuilding the whole repetition. A url the fixture does not carry is a
        typo, never a silent no-op.
        """
        result = dict(self.result or {})
        sources = [dict(item) for item in (result.get("evaluated_sources") or [])]
        for source in sources:
            if source.get("url") == url:
                source.update(fields)
                break
        else:
            raise AssertionError(f"no evaluated source for {url}")
        return self.model_copy(
            update={"result": {**result, "evaluated_sources": sources}}
        )


class FactCheckerOutput(TargetOutput):
    """A fact-checker repetition with builder helpers.

    Every helper that adds verification passages also records the read
    provenance those passages need. Task 5 review: a fixture that moved the
    claim alone could assert passages at URLs the run never read — or that no
    run could have read — which is exactly what the read-provenance gate
    exists to refuse.
    """

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

    def with_read_urls(
        self, urls: Sequence[str], *, complete: bool = True
    ) -> "FactCheckerOutput":
        """Declare exactly which URLs this repetition READ.

        ``complete=False`` models an artifact that lost read identities, which
        the gate must treat as unable to prove anything.
        """
        fingerprints, derived_complete = bounded_url_fingerprints(urls)
        return self.model_copy(
            update={
                "dependencies": _ledger_with_reads(
                    self.dependencies,
                    fingerprints,
                    complete=complete and derived_complete,
                )
            }
        )

    def with_read_trajectory(self, urls: Sequence[str]) -> "FactCheckerOutput":
        """Record one web_scraper step per URL: reads, not discovery."""
        return self.model_copy(
            update={"trajectory": _read_trajectory(list(urls))}
        )

    def with_read_steps(self, steps: Sequence[ReActStep]) -> "FactCheckerOutput":
        """Derive read provenance from typed steps, exactly as the target does.

        Uses the production ``read_url_fingerprints`` classifier, so a fixture
        can prove — rather than assert — that a search-only step set yields no
        read identity at all.
        """
        fingerprints, complete = read_url_fingerprints(steps)
        return self.model_copy(
            update={
                "dependencies": _ledger_with_reads(
                    self.dependencies, fingerprints, complete=complete
                )
            }
        )

    def with_verification_passage_urls(
        self, urls: Sequence[str]
    ) -> "FactCheckerOutput":
        result = dict(self.result or {})
        claims = [dict(item) for item in (result.get("verified_claims") or [])]
        claims[0]["verification_evidence"] = [
            {
                "source_url": url,
                "source_title": "Independent review",
                "locator": f"p. {index + 1}",
                "excerpt": "An independent source reports the same result.",
                "stance": "supports",
            }
            for index, url in enumerate(urls)
        ]
        return self.model_copy(
            update={"result": {**result, "verified_claims": claims}}
        ).with_read_urls(urls).with_read_trajectory(urls)

    def with_empty_evidence(self) -> "FactCheckerOutput":
        result = dict(self.result or {})
        claims = [dict(item) for item in (result.get("verified_claims") or [])]
        claims[0]["evidence"] = []
        claims[0]["verification_evidence"] = []
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

    def with_claim_fields(
        self, index: int, **fields: object
    ) -> "FactCheckerOutput":
        """Rewrite one claim record's fields.

        The helpers above patch ``claims[0]``; a fixture carrying more than
        one claim needs to name the record it mutates, or an assertion about
        the second claim is satisfied by a mutation that never touched it.
        """
        result = dict(self.result or {})
        claims = [dict(item) for item in (result.get("verified_claims") or [])]
        claims[index].update(fields)
        return self.model_copy(
            update={"result": {**result, "verified_claims": claims}}
        )

    def with_claim_passages(
        self, index: int, urls: Sequence[str]
    ) -> "FactCheckerOutput":
        """Replace one claim's verification passages with one per URL."""
        result = dict(self.result or {})
        claims = [dict(item) for item in (result.get("verified_claims") or [])]
        claims[index]["verification_evidence"] = [
            {
                "source_url": url,
                "source_title": "Second account of the same number",
                "locator": f"p. {position + 1}",
                "excerpt": "A second account states the same figure.",
                "stance": "supports",
            }
            for position, url in enumerate(urls)
        ]
        return self.model_copy(
            update={"result": {**result, "verified_claims": claims}}
        )


class SynthesizerOutput(TargetOutput):
    """A synthesizer repetition with builder helpers."""

    def with_report_citing(self, url: str) -> "SynthesizerOutput":
        result = dict(self.result or {})
        report = str(result.get("markdown") or "")
        state_update = dict(self.state_update)
        report = f"{report}\n\nSee {url} for details."
        return self.model_copy(
            update={
                "result": {
                    **result,
                    "markdown": report,
                },
                "state_update": {**state_update, "report": report},
            }
        )

    def without_limitations(self) -> "SynthesizerOutput":
        result = dict(self.result or {})
        report = str(result.get("markdown") or "")
        state_update = dict(self.state_update)
        report = report.split(
            "## Uncertainty and conflicting evidence"
        )[0].rstrip()
        return self.model_copy(
            update={
                "result": {
                    **result,
                    "markdown": report,
                },
                "state_update": {**state_update, "report": report},
            }
        )

    def with_report_text(self, text: str) -> "SynthesizerOutput":
        result = dict(self.result or {})
        state_update = dict(self.state_update)
        return self.model_copy(
            update={
                "result": {**result, "markdown": text},
                "state_update": {**state_update, "report": text},
            }
        )


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
def synthesizer_composition_case(controlled_case_for_id):
    return controlled_case_for_id("synthesizer", "composition-no-publication")


# Task 12's high-risk cases are looked up by id with ``case_by_id``, not
# through ``controlled_case_for_id``: that factory skips when a case is
# missing, and a test whose whole subject is one of these cases has to fail
# loudly when the case it measures is not registered.


@pytest.fixture
def scoped_targets_case() -> EvaluationCase:
    """The planner case whose contract polices evidence-target scoping."""
    return case_by_id("planner", "controlled", "scoped-evidence-targets")


@pytest.fixture
def read_bearing_case() -> EvaluationCase:
    """The researcher case whose contract polices read-bearing provenance."""
    return case_by_id("researcher", "controlled", "read-bearing-acquisition")


@pytest.fixture
def work_role_case() -> EvaluationCase:
    """The source-evaluator case whose contract polices work identity."""
    return case_by_id(
        "source_evaluator", "controlled", "work-role-independence"
    )


@pytest.fixture
def upstream_pair_case() -> EvaluationCase:
    """The fact-checker case whose contract polices upstream independence."""
    return case_by_id(
        "fact_checker", "controlled", "upstream-independent-pair"
    )


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
                    "coverage_id": "topic-01",
                    "title": "Solid-state electrolyte degradation",
                    "rationale": "Electrolyte stability dominates cycle life.",
                    "search_queries": [
                        "solid-state electrolyte degradation mechanisms"
                    ],
                    "success_criteria": ["Crack propagation data"],
                    "priority": 1,
                },
                {
                    "coverage_id": "topic-02",
                    "title": "Cathode interface resistance",
                    "rationale": "Interface resistance limits capacity retention.",
                    "search_queries": [
                        "cathode solid-state interface resistance"
                    ],
                    "success_criteria": ["Quantified resistance growth"],
                    "priority": 2,
                },
                {
                    "coverage_id": "topic-03",
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
            stop_reason="finished",
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


def _stamped_target(
    coverage_id: str,
    position: int,
    *,
    question: str,
    required_dimensions: Sequence[str],
    support_policy: str,
    critical: bool = True,
) -> dict[str, object]:
    """One evidence target as the Planner's artifact carries it, id stamped."""
    return {
        "target_id": target_id_for(coverage_id, position),
        "coverage_id": coverage_id,
        "question": question,
        "required_dimensions": list(required_dimensions),
        "required": True,
        "critical": critical,
        "support_policy": support_policy,
    }


@pytest.fixture
def scoped_target_output(scoped_targets_case) -> PlannerOutput:
    """A plan whose three obligations are bounded, creditable, and policed.

    One comparative obligation under the independent-pair policy and two
    official-instrument obligations under primary attribution: the declared
    policy set is covered only while the comparative obligation keeps the
    policy its own wording earns.
    """
    comparison = (
        "How do documented interconnection queue wait times for "
        "utility-scale solar compare with those for utility-scale wind in "
        "the United States?"
    )
    return PlannerOutput(
        case_id=scoped_targets_case.case_id,
        case_version=scoped_targets_case.version,
        agent_name=scoped_targets_case.agent_name,
        tier=scoped_targets_case.tier,
        repetition=1,
        session_id="evaluation-scoped-evidence-targets",
        experiment_name="planner-controlled-20260816T101500Z-abc1234",
        trace_url="https://smith.langchain.com/o/x/r/planner-agent-2",
        completed=True,
        failure=None,
        result={
            "sub_topics": [
                {
                    "coverage_id": "topic-01",
                    "title": "Documented queue wait times",
                    "rationale": "Queue duration is the compared dimension.",
                    "search_queries": [
                        "interconnection queue wait times solar and wind "
                        "United States"
                    ],
                    "success_criteria": ["A reported wait time per technology"],
                    "priority": 1,
                    "evidence_targets": [
                        _stamped_target(
                            "topic-01",
                            1,
                            question=comparison,
                            required_dimensions=[
                                "comparison: median queue wait time in months "
                                "for each technology",
                                "period: the most recent reported year",
                                "geography: the United States",
                            ],
                            support_policy="independent_pair",
                        )
                    ],
                },
                {
                    "coverage_id": "topic-02",
                    "title": "Federal interconnection rule requirements",
                    "rationale": (
                        "The question also asks what the current federal rule "
                        "requires."
                    ),
                    "search_queries": [
                        "current federal interconnection rule requirements "
                        "utility-scale generators"
                    ],
                    "success_criteria": ["The binding requirement, by issuer"],
                    "priority": 2,
                    "evidence_targets": [
                        _stamped_target(
                            "topic-02",
                            1,
                            question=(
                                "What does the current federal "
                                "interconnection rule require of "
                                "utility-scale solar projects?"
                            ),
                            required_dimensions=[
                                "instrument: the issuing federal rule and its "
                                "effective date",
                                "period: the rule in force as of the latest "
                                "revision",
                                "geography: the United States",
                            ],
                            support_policy="primary_attribution",
                        )
                    ],
                },
                {
                    "coverage_id": "topic-03",
                    "title": "Official study process",
                    "rationale": (
                        "Study timelines decide how much of the wait is "
                        "administrative."
                    ),
                    "search_queries": [
                        "official interconnection study process timeline"
                    ],
                    "success_criteria": ["An official timeline or fee schedule"],
                    "priority": 3,
                    "evidence_targets": [
                        _stamped_target(
                            "topic-03",
                            1,
                            question=(
                                "Which official instrument sets the "
                                "interconnection study fee schedule for "
                                "utility-scale generators?"
                            ),
                            required_dimensions=[
                                "measure: the study fee in dollars",
                                "instrument: the official fee schedule",
                                "geography: the United States",
                            ],
                            support_policy="primary_attribution",
                        )
                    ],
                },
            ]
        },
        state_update={"note": "planned three obligations"},
        errors=[],
        tracker_errors=[],
        react=ReActSummary(
            iterations=2,
            tool_calls=3,
            stop_reason="finished",
            max_iterations=scoped_targets_case.expectations.max_iterations,
            tool_budget=scoped_targets_case.expectations.max_tool_calls,
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
            stop_reason="finished",
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
def read_bearing_output(read_bearing_case) -> ResearcherOutput:
    """Two findings, each citing one of the two pages the run read.

    The read ledger is derived from the case's own ``readable_urls``, so the
    fixture cannot drift from the URLs the scenario scripts as readable.
    """
    readable = read_bearing_case.expectations.reference["readable_urls"]
    sub_topic = read_bearing_case.state.sub_topics[0].title
    return ResearcherOutput(
        case_id=read_bearing_case.case_id,
        case_version=read_bearing_case.version,
        agent_name=read_bearing_case.agent_name,
        tier=read_bearing_case.tier,
        repetition=1,
        session_id="evaluation-read-bearing-acquisition",
        experiment_name="researcher-controlled-20260816T101500Z-abc1234",
        trace_url="https://smith.langchain.com/o/x/r/researcher-agent-2",
        completed=True,
        failure=None,
        result={
            "findings": [
                {
                    "content": (
                        "Utility-scale battery storage additions in the "
                        "United States reached 14 GW in 2025."
                    ),
                    "source_url": readable[0],
                    "source_title": (
                        "NREL utility-scale storage deployment report"
                    ),
                    "related_sub_topic": sub_topic,
                },
                {
                    "content": (
                        "The department's deployment report states 14 GW of "
                        "utility-scale battery storage was added in 2025."
                    ),
                    "source_url": readable[1],
                    "source_title": "Department of Energy storage report",
                    "related_sub_topic": sub_topic,
                },
            ]
        },
        state_update={},
        errors=[],
        tracker_errors=[],
        react=ReActSummary(
            iterations=3,
            tool_calls=4,
            stop_reason="finished",
            max_iterations=read_bearing_case.expectations.max_iterations,
            tool_budget=read_bearing_case.expectations.max_tool_calls,
        ),
        dependencies=_read_ledger(list(readable)),
        evidence=EvidenceContext(
            scripted_search_urls=list(
                read_bearing_case.expectations.known_source_urls
            )
        ),
        trajectory=_read_trajectory(list(readable)),
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
                    "overall_score": 0.88,
                    "rationale": "Authoritative assessment with broad corroboration.",
                    "evaluation_status": "scored",
                    "low_confidence": False,
                },
                {
                    "url": "https://journals.ametsoc.org/regional-precip",
                    "title": "AMS regional precipitation study",
                    "authority_score": 0.85,
                    "recency_score": 0.80,
                    "relevance_score": 0.90,
                    "overall_score": 0.84,
                    "rationale": "Peer-reviewed regional analysis.",
                    "evaluation_status": "scored",
                    "low_confidence": False,
                },
                {
                    "url": "https://noaa.gov/precip-assessment",
                    "title": "NOAA precipitation assessment",
                    "authority_score": 0.88,
                    "recency_score": 0.82,
                    "relevance_score": 0.88,
                    "overall_score": 0.85,
                    "rationale": "Agency assessment with solid corroboration.",
                    "evaluation_status": "scored",
                    "low_confidence": False,
                },
                {
                    "url": "https://weatherblog.example.com/my-take",
                    "title": "Weather blog commentary",
                    "authority_score": 0.30,
                    "recency_score": 0.60,
                    "relevance_score": 0.50,
                    "overall_score": 0.42,
                    "rationale": "Opinion piece without independent verification.",
                    "evaluation_status": "scored",
                    "low_confidence": False,
                },
                {
                    "url": "https://forum.example.net/thread/1182",
                    "title": "Forum thread on precipitation",
                    "authority_score": 0.20,
                    "recency_score": 0.50,
                    "relevance_score": 0.45,
                    "overall_score": 0.35,
                    "rationale": "Anonymous forum discussion.",
                    "evaluation_status": "scored",
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
            stop_reason="finished",
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


# The survey's publisher as a read-backed assessment resolves it: the
# evidenced issuer's own words, which is what the identity code derives when a
# document says who published it. The mirror and the wire reprint name the same
# institute, so they resolve to the same identity — that is the whole point of
# the case — while the university's study keeps its own.
_SURVEY_PUBLISHER_ID = "national soil baseline institute"
_SURVEY_WORK_ID = "report:national soil baseline institute:sc-2026-04"


@pytest.fixture
def work_role_output(work_role_case) -> SourceEvaluatorOutput:
    """One evaluation per finding, with the survey's copies labelled.

    The identity fields are the ones the work-identity metrics read, carrying
    the shape a correct run produces: the repository copy and the wire reprint
    inherit the survey's publisher identity and a derivative transport
    relation, and the university's own study keeps its own identity. The
    original's work key is the one the shared issuer and report number
    resolve to.
    """
    reference = work_role_case.expectations.reference
    original, mirror, wire = reference["same_work_urls"]
    independent = reference["independent_work_urls"][0]
    return SourceEvaluatorOutput(
        case_id=work_role_case.case_id,
        case_version=work_role_case.version,
        agent_name=work_role_case.agent_name,
        tier=work_role_case.tier,
        repetition=1,
        session_id="evaluation-work-role-independence",
        experiment_name="source-evaluator-controlled-20260816T101500Z-abc1234",
        trace_url="https://smith.langchain.com/o/x/r/source-evaluator-2",
        completed=True,
        failure=None,
        result={
            "evaluated_sources": [
                {
                    "url": original,
                    "title": "National soil carbon baseline survey 2025",
                    "authority_score": 0.88,
                    "recency_score": 0.85,
                    "relevance_score": 0.90,
                    "overall_score": 0.87,
                    "rationale": "The institute's own survey report.",
                    "evaluation_status": "scored",
                    "low_confidence": False,
                    "source_role": "original_report",
                    "transport_relation": "original",
                    "publisher_id": _SURVEY_PUBLISHER_ID,
                    "work_id": _SURVEY_WORK_ID,
                },
                {
                    "url": mirror,
                    "title": "Repository record: soil carbon baseline survey",
                    "authority_score": 0.85,
                    "recency_score": 0.80,
                    "relevance_score": 0.90,
                    "overall_score": 0.85,
                    "rationale": "The archived copy of the same survey.",
                    "evaluation_status": "scored",
                    "low_confidence": False,
                    "source_role": "derivative",
                    "transport_relation": "mirror",
                    "publisher_id": _SURVEY_PUBLISHER_ID,
                    "work_id": _SURVEY_WORK_ID,
                },
                {
                    "url": wire,
                    "title": "Wire reprint: soil carbon baseline survey",
                    "authority_score": 0.60,
                    "recency_score": 0.80,
                    "relevance_score": 0.85,
                    "overall_score": 0.72,
                    "rationale": "A reprint that adds no new reporting.",
                    "evaluation_status": "scored",
                    "low_confidence": False,
                    "source_role": "derivative",
                    "transport_relation": "syndication",
                    "publisher_id": _SURVEY_PUBLISHER_ID,
                    "work_id": _SURVEY_WORK_ID,
                },
                {
                    "url": independent,
                    "title": "University soil study: regional carbon baseline",
                    "authority_score": 0.80,
                    "recency_score": 0.82,
                    "relevance_score": 0.78,
                    "overall_score": 0.80,
                    "rationale": "A separate group's own measurements.",
                    "evaluation_status": "scored",
                    "low_confidence": False,
                    "source_role": "independent_research",
                    "transport_relation": "original",
                    "publisher_id": "soilstudies.example.edu",
                    "work_id": "sha256:" + "b" * 64,
                },
            ]
        },
        state_update={},
        errors=[],
        tracker_errors=[],
        react=ReActSummary(
            iterations=2,
            tool_calls=4,
            stop_reason="finished",
            max_iterations=work_role_case.expectations.max_iterations,
            tool_budget=work_role_case.expectations.max_tool_calls,
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
                    "claim_id": claim_fingerprint(
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
                    "verification_evidence": [
                        {
                            "source_url": "https://syndication.news.example.com/c",
                            "source_title": "Independent safety review",
                            "locator": "p. 1",
                            "excerpt": (
                                "The IAEA framework covers SMR designs."
                            ),
                            "stance": "supports",
                        },
                        {
                            "source_url": (
                                "https://world-nuclear.org/smr-safety-standards"
                            ),
                            "source_title": "Independent safety review",
                            "locator": "p. 2",
                            "excerpt": "The NRC applies the same review.",
                            "stance": "supports",
                        },
                    ],
                }
            ]
        },
        state_update={},
        errors=[],
        tracker_errors=[],
        react=ReActSummary(
            iterations=2,
            tool_calls=4,
            stop_reason="finished",
            max_iterations=fact_checker_case.expectations.max_iterations,
            tool_budget=fact_checker_case.expectations.max_tool_calls,
        ),
        dependencies=_read_ledger(
            [
                "https://syndication.news.example.com/c",
                "https://world-nuclear.org/smr-safety-standards",
            ]
        ),
        evidence=EvidenceContext(),
        trajectory=_read_trajectory(
            [
                "https://syndication.news.example.com/c",
                "https://world-nuclear.org/smr-safety-standards",
            ]
        ),
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
                    "claim_id": claim_fingerprint(
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
                    "verification_evidence": [
                        {
                            "source_url": "https://news.example.com/outage-minutes-fall",
                            "source_title": "News follow-up",
                            "locator": "p. 1",
                            "excerpt": (
                                "A follow-up report confirms outage minutes fell."
                            ),
                            "stance": "supports",
                        },
                        {
                            "source_url": (
                                "https://syndication.news.example.com/"
                                "outage-minutes-fall"
                            ),
                            "source_title": "Syndicated report",
                            "locator": "p. 1",
                            "excerpt": "The same figure is repeated.",
                            "stance": "supports",
                        },
                    ],
                }
            ]
        },
        state_update={},
        errors=[],
        tracker_errors=[],
        react=ReActSummary(
            iterations=2,
            tool_calls=4,
            stop_reason="finished",
            max_iterations=fact_checker_dependent_case.expectations.max_iterations,
            tool_budget=fact_checker_dependent_case.expectations.max_tool_calls,
        ),
        dependencies=_read_ledger(
            [
                "https://news.example.com/outage-minutes-fall",
                "https://syndication.news.example.com/outage-minutes-fall",
            ]
        ),
        evidence=EvidenceContext(),
        trajectory=_read_trajectory(
            [
                "https://news.example.com/outage-minutes-fall",
                "https://syndication.news.example.com/outage-minutes-fall",
            ]
        ),
        target_model_requested="gpt-5.6-luna",
        target_model_returned="gpt-5.6-luna",
        target_reasoning_effort="low",
    )


@pytest.fixture
def upstream_pair_output(upstream_pair_case) -> FactCheckerOutput:
    """The case's two claims as a correct run records them.

    The trap claim is supported by the commission's own order and its own
    press release — one publisher, so ``insufficient_evidence`` with a
    ``source_supported`` badge, never a pair. The control claim rests on two
    separate works by two unrelated publishers, so it carries the pair badge.
    Both claim texts and every URL come from the case's reference, so a
    mutation cannot drift from what the case declares.
    """
    reference = upstream_pair_case.expectations.reference
    trap = reference["forbidden_verified_claims"][0]
    control = reference["required_verified_claims"][0]
    agency, press = reference["same_publisher_urls"]
    lab = reference["dimension_only_urls"][0]
    monitor, university = reference["corroborating_urls"]
    read_urls = [agency, press, lab, monitor, university]
    return FactCheckerOutput(
        case_id=upstream_pair_case.case_id,
        case_version=upstream_pair_case.version,
        agent_name=upstream_pair_case.agent_name,
        tier=upstream_pair_case.tier,
        repetition=1,
        session_id="evaluation-upstream-independent-pair",
        experiment_name="fact-checker-controlled-20260816T101500Z-abc1234",
        trace_url="https://smith.langchain.com/o/x/r/fact-checker-3",
        completed=True,
        failure=None,
        result={
            "verified_claims": [
                {
                    "text": trap,
                    "claim_id": claim_fingerprint(trap),
                    "source_urls": list(
                        reference["claim_origin_urls"][trap]
                    ),
                    "verdict": "insufficient_evidence",
                    "confidence": 0.40,
                    "evidence": [
                        "Order 2026-14 records non-revenue water at "
                        "12 percent of supply in 2025.",
                        "The commission's press release repeats the "
                        "12 percent of supply figure for 2025.",
                    ],
                    "contradictions": [],
                    "evidence_status": "source_supported",
                    "verification_evidence": [
                        {
                            "source_url": agency,
                            "source_title": "Commission order 2026-14",
                            "locator": "p. 3",
                            "excerpt": (
                                "Order 2026-14 requires the authority to "
                                "reduce non-revenue water to 8 percent of "
                                "supply by 2030, from the 12 percent of "
                                "supply measured in 2025."
                            ),
                            "stance": "supports",
                        },
                        {
                            "source_url": press,
                            "source_title": "Commission press release",
                            "locator": "p. 1",
                            "excerpt": (
                                "The commission confirmed that non-revenue "
                                "water in the authority's network measured "
                                "12 percent of supply in 2025."
                            ),
                            "stance": "supports",
                        },
                    ],
                },
                {
                    "text": control,
                    "claim_id": claim_fingerprint(control),
                    "source_urls": list(
                        reference["claim_origin_urls"][control]
                    ),
                    "verdict": "verified",
                    "confidence": 0.85,
                    "evidence": [
                        "The monitor's 2025 audit records 41 kilometres of "
                        "leaking mains replaced.",
                        "The university's regional study records 41 "
                        "kilometres of mains replaced.",
                    ],
                    "contradictions": [],
                    "evidence_status": "verified_pair",
                    "verification_evidence": [
                        {
                            "source_url": monitor,
                            "source_title": "Utility monitor: 2025 audit",
                            "locator": "p. 2",
                            "excerpt": (
                                "The 2025 network audit records 41 "
                                "kilometres of leaking mains replaced by "
                                "the authority."
                            ),
                            "stance": "supports",
                        },
                        {
                            "source_url": university,
                            "source_title": "University regional study",
                            "locator": "p. 4",
                            "excerpt": (
                                "The regional study records 41 kilometres "
                                "of leaking mains replaced in the "
                                "authority's network during 2025."
                            ),
                            "stance": "supports",
                        },
                    ],
                },
            ]
        },
        state_update={},
        errors=[],
        tracker_errors=[],
        react=ReActSummary(
            iterations=4,
            tool_calls=5,
            stop_reason="finished",
            max_iterations=upstream_pair_case.expectations.max_iterations,
            tool_budget=upstream_pair_case.expectations.max_tool_calls,
        ),
        dependencies=_read_ledger(read_urls),
        evidence=EvidenceContext(
            scripted_search_urls=list(
                upstream_pair_case.expectations.known_source_urls
            )
        ),
        trajectory=_read_trajectory(read_urls),
        target_model_requested="gpt-5.6-luna",
        target_model_returned="gpt-5.6-luna",
        target_reasoning_effort="low",
    )


@pytest.fixture
def synthesizer_output(synthesizer_case) -> SynthesizerOutput:
    """Both Task 6 Markdown artifacts, citing only known sources."""
    urls = synthesizer_case.expectations.known_source_urls
    report = (
        "# Research report: What is the evidence base for congestion pricing "
        "reducing urban travel times?\n\n"
        "**As of:** 2026-08-01T00:00:00+00:00\n\n"
        "**Scope:** the supplied urban congestion-pricing evidence.\n\n"
        "**Quality status:** not yet quality-gated\n\n"
        "## Executive summary\n\n"
        f"- London congestion charging evidence shows travel times fell "
        f"after the charge was introduced. [{1}]\n\n"
        "## Constraint ranking\n\n"
        "(no constraint was ranked for this pass)\n\n"
        "## Findings\n\n"
        "### London congestion charging evidence\n\n"
        f"- London results show travel-time reductions. [{1}]\n\n"
        "### New York congestion pricing evidence\n\n"
        f"- New York results point to similar reductions. [{2}]\n\n"
        "### International congestion-pricing evidence\n\n"
        f"- Evidence beyond London and New York is thinner. [{3}]\n\n"
        "## Uncertainty and conflicting evidence\n\n"
        "The evidence is limited to a few cities and short evaluation windows.\n\n"
        "## Methodology\n\n"
        "- Claims and citations were composed from the supplied evidence.\n\n"
        "## References\n\n"
        f"1. London monitoring — {urls[0]}\n"
        f"2. New York evaluation — {urls[1]}\n"
        f"3. Comparative review — {urls[2]}"
    )
    evidence = (
        "# Evidence ledger: evaluation-complete\n\n"
        "## Claim registry\n\n"
        "The controlled fixture's checked claims and source assessments."
    )
    state_update = {
        "report": report,
        "report_evidence": evidence,
        "evidence_path": "report-evaluation-complete-1-evidence.md",
        "unique_source_count": len(urls),
        "unique_claim_count": 4,
    }
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
        result={
            "markdown": report,
            "path": None,
            "evidence_markdown": evidence,
            "evidence_path": "report-evaluation-complete-1-evidence.md",
            "section_count": 3,
            "citation_count": 3,
            "unique_source_count": len(urls),
            "unique_claim_count": 4,
        },
        state_update=state_update,
        errors=[],
        tracker_errors=[],
        react=ReActSummary(
            iterations=2,
            tool_calls=3,
            stop_reason="finished",
            max_iterations=synthesizer_case.expectations.max_iterations,
            tool_budget=synthesizer_case.expectations.max_tool_calls,
        ),
        dependencies=DependencyLedger(),
        evidence=EvidenceContext(),
        trajectory=[],
        target_model_requested="gpt-5.6-luna",
        target_model_returned="gpt-5.6-luna",
        target_reasoning_effort="medium",
    )


@pytest.fixture
def synthesizer_composition_output(
    synthesizer_composition_case,
) -> SynthesizerOutput:
    """A composition-only output with no publication claim or side effect."""
    urls = synthesizer_composition_case.expectations.known_source_urls
    report = (
        "# Research report: How much does building retrofit depth affect "
        "realized energy savings?\n\n"
        "**As of:** 2026-08-01T00:00:00+00:00\n\n"
        "**Scope:** the supplied retrofit evidence.\n\n"
        "**Quality status:** not yet quality-gated\n\n"
        "## Executive summary\n\n"
        f"- Deep retrofit results are mixed across the supplied studies. [1]\n\n"
        "## Constraint ranking\n\n"
        "(no constraint was ranked for this pass)\n\n"
        "## Findings\n\n"
        "### Retrofit depth and realized savings\n\n"
        f"- Realized savings can fall below modeled values. [1]\n\n"
        "## Uncertainty and conflicting evidence\n\n"
        "The evidence base is still limited.\n\n"
        "## Methodology\n\n"
        "- Claims were composed from the supplied checked evidence.\n\n"
        "## References\n\n"
        f"1. Retrofit study — {urls[0]}"
    )
    evidence = (
        "# Evidence ledger: composition-no-publication\n\n"
        "## Claim registry\n\n"
        "The controlled fixture's checked claims and source assessments."
    )
    state_update = {
        "report": report,
        "report_evidence": evidence,
        "evidence_path": "report-composition-no-publication-1-evidence.md",
        "unique_source_count": len(urls),
        "unique_claim_count": 3,
    }
    return SynthesizerOutput(
        case_id=synthesizer_composition_case.case_id,
        case_version=synthesizer_composition_case.version,
        agent_name=synthesizer_composition_case.agent_name,
        tier=synthesizer_composition_case.tier,
        repetition=1,
        session_id="evaluation-composition-no-publication",
        experiment_name="synthesizer-controlled-20260816T101500Z-abc1234",
        trace_url="https://smith.langchain.com/o/x/r/synthesizer-composition-1",
        completed=True,
        failure=None,
        result={
            "markdown": report,
            "path": None,
            "evidence_markdown": evidence,
            "evidence_path": "report-composition-no-publication-1-evidence.md",
            "section_count": 1,
            "citation_count": 1,
            "unique_source_count": len(urls),
            "unique_claim_count": 3,
        },
        state_update=state_update,
        errors=[],
        tracker_errors=[],
        react=ReActSummary(
            iterations=2,
            tool_calls=3,
            stop_reason="finished",
            max_iterations=synthesizer_composition_case.expectations.max_iterations,
            tool_budget=synthesizer_composition_case.expectations.max_tool_calls,
        ),
        dependencies=DependencyLedger(),
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
            tool_calls=0,
            stop_reason="finished",
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
            tool_calls=0,
            stop_reason="finished",
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
    """Budget exhausted: the critique stops because no iteration remains.

    The error ledger is empty on purpose. This fixture used to record a
    ``search_unavailable`` failure from the spot-check loop Task 8 removed,
    while the same fixture declared ``tool_calls=0`` — an artifact no
    tool-free Critic can produce. The case no longer requires a recoverable
    error either, so the fabricated one is gone rather than left to be read as
    live coverage.
    """
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
        errors=[],
        tracker_errors=[],
        react=ReActSummary(
            iterations=3,
            tool_calls=0,
            stop_reason="finished",
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
                evidence_targets=[
                    EvidenceTargetDraft(
                        question=f"What does {title} measure, and per which "
                        "authority?",
                        required_dimensions=[f"measure: {title}"],
                        critical=index == 1,
                    )
                ],
            )
            for index, title in enumerate(titles, start=1)
        ]
    )


def _planner_responses(*, leak: str | None = None) -> list:
    """One finishing decision, a valid plan draft, and one sound review.

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
    return [_finish_decision(), draft, _sound_review()]


def _sound_review() -> PlanReviewDraft:
    """The planner's tool-free review verdict for a plan that is sound."""
    return PlanReviewDraft(
        sound=True,
        missing_dimensions=[],
        atomicity_defects=[],
        unsupported_premises=[],
        repair_instruction="",
    )


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
                        "title": (
                            "Sodium-ion energy density report "
                            + "x" * 240
                        ),
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
            "DEEPSEEK_API_KEY": "sk-deepseek-abcdefgh",
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


# --- Task 24: reporting fixtures --------------------------------------------

_REPORTING_METADATA = {
    "target_model": "deepseek-v4-flash",
    "target_model_returned": "deepseek-v4-flash",
    "target_reasoning_effort": "high",
    "target_profile_source": "production",
    "production_parity": True,
    "production_parity_source": "configuration",
    "release_evidence": True,
    "judge_reasoning_effort": "max",
    "thinking_mode": "enabled",
    "configuration_fingerprint": "abc123abc123",
    "judge_configuration_fingerprint": "def456def456",
}


def _scored_judge(score: float) -> JudgeFeedback:
    return JudgeFeedback(
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
            rationale="Scripted for the reporting tests.",
        ),
        judge_quality=score,
        prompt_id="individual-agent-judge",
        rubric_version=1,
        prompt_fingerprint="abc123abc123",
        judge_model="deepseek-v4-flash",
        judge_configuration_fingerprint="def456def456",
    )


@pytest.fixture
def researcher_experiment_result() -> ExperimentResult:
    """4 real researcher controlled cases x 3 repetitions, mean 0.86.

    Every repetition scores exactly 0.86 so the experiment-level mean is
    exactly 0.86 and ``format_score`` never has to hide rounding drift.
    """
    cases = cases_for("researcher", "controlled")
    assert [case.case_id for case in cases] == [
        "multi-source-coverage",
        "conflicting-evidence",
        "partial-search-failure",
        "read-bearing-acquisition",
    ]

    case_results: list[CaseResult] = []
    for case in cases:
        repetitions = [
            RepetitionResult(
                case_id=case.case_id,
                case_version=case.version,
                repetition=n,
                completed=True,
                gates=GateReport(
                    results=[
                        GateResult(
                            gate_id="run_completed", passed=True, detail=""
                        )
                    ]
                ),
                deterministic_quality=0.86,
                judge=_scored_judge(0.86),
                aggregate_quality=0.86,
                trace_url=(
                    f"https://smith.langchain.com/o/x/r/{case.case_id}-{n}"
                ),
                errors=[],
            )
            for n in range(1, 4)
        ]
        case_results.append(
            CaseResult(
                case_id=case.case_id,
                case_version=case.version,
                repetitions=repetitions,
                average_quality=0.86,
                passed=True,
                lowest_scoring_trace_url=repetitions[0].trace_url,
            )
        )

    return ExperimentResult(
        agent_name="researcher",
        tier="controlled",
        experiment_name="researcher-controlled-20260816T101500Z-abc1234",
        experiment_url=(
            "https://smith.langchain.com/o/x/experiments/researcher-1"
        ),
        dataset_name="deep-research-researcher-controlled-v1",
        dataset_url="https://smith.langchain.com/o/x/datasets/researcher-1",
        cases=case_results,
        status="REVIEW REQUIRED",
        metadata=dict(_REPORTING_METADATA),
    )


@pytest.fixture
def failing_experiment_result() -> ExperimentResult:
    """One case whose one repetition fails the ``citations_known`` gate."""
    repetition = RepetitionResult(
        case_id="unsupported-claim",
        case_version=1,
        repetition=1,
        completed=True,
        gates=GateReport(
            results=[
                GateResult(gate_id="run_completed", passed=True, detail=""),
                GateResult(
                    gate_id="citations_known",
                    passed=False,
                    detail="a cited url is not in known_source_urls",
                ),
            ]
        ),
        deterministic_quality=0.4,
        judge=_scored_judge(0.4),
        aggregate_quality=0.4,
        trace_url="https://smith.langchain.com/o/x/r/unsupported-claim-1",
        errors=[],
    )
    case_result = CaseResult(
        case_id="unsupported-claim",
        case_version=1,
        repetitions=[repetition],
        average_quality=0.4,
        passed=False,
        lowest_scoring_trace_url=repetition.trace_url,
    )
    return ExperimentResult(
        agent_name="synthesizer",
        tier="controlled",
        experiment_name="synthesizer-controlled-20260816T101500Z-abc1234",
        experiment_url=(
            "https://smith.langchain.com/o/x/experiments/synthesizer-1"
        ),
        dataset_name="deep-research-synthesizer-controlled-v1",
        dataset_url="https://smith.langchain.com/o/x/datasets/synthesizer-1",
        cases=[case_result],
        status="FAILED",
        metadata=dict(_REPORTING_METADATA),
    )


@pytest.fixture
def judge_not_run_experiment_result() -> ExperimentResult:
    """One repetition whose judge never ran (no evaluable output)."""
    not_run_judge = JudgeFeedback(
        status="judge_not_run",
        not_run_reason="no_evaluable_output",
        prompt_id="individual-agent-judge",
        rubric_version=1,
        prompt_fingerprint="abc123abc123",
        judge_model="gpt-5.6-luna",
        judge_configuration_fingerprint="def456def456",
    )
    repetition = RepetitionResult(
        case_id="focused-decomposition",
        case_version=1,
        repetition=1,
        completed=False,
        gates=GateReport(
            results=[
                GateResult(gate_id="run_completed", passed=False, detail="")
            ]
        ),
        deterministic_quality=None,
        judge=not_run_judge,
        aggregate_quality=None,
        trace_url=None,
        errors=[],
    )
    case_result = CaseResult(
        case_id="focused-decomposition",
        case_version=1,
        repetitions=[repetition],
        average_quality=None,
        passed=False,
        lowest_scoring_trace_url=None,
    )
    return ExperimentResult(
        agent_name="planner",
        tier="controlled",
        experiment_name="planner-controlled-20260816T101500Z-abc1234",
        experiment_url="https://smith.langchain.com/o/x/experiments/planner-1",
        dataset_name="deep-research-planner-controlled-v1",
        dataset_url="https://smith.langchain.com/o/x/datasets/planner-1",
        cases=[case_result],
        status="INFRASTRUCTURE FAILURE",
        metadata=dict(_REPORTING_METADATA),
    )


@pytest.fixture
def leaking_experiment_result() -> ExperimentResult:
    """Proves redaction holds even through this rendering path.

    The failure message is built from text that originally carried a
    real-looking secret and a traceback-shaped fragment, then run through
    the same ``redact_secrets`` helper Task 5 uses before anything is ever
    attached to a typed ``EvaluationFailure`` -- so by the time it is part
    of this ``ExperimentResult``, it is already clean, and this fixture
    exists to prove ``write_experiment_artifact`` never reintroduces the
    leak while serializing.
    """
    secret = "sk-abcdefghijklmnop"
    raw = f"provider call failed with key={secret}"
    clean_message = redact_secrets(raw, [secret])
    assert secret not in clean_message

    # A raw traceback is never stored on a typed EvaluationFailure -- only
    # a curated ``message`` and an ``exception_type`` name are. This
    # asserts that structural guarantee holds for the fixture itself, so
    # the artifact-write test below is proving the rendering path, not a
    # traceback that was never here to begin with.
    assert "Traceback" not in clean_message

    failure = EvaluationFailure(
        stage="provider",
        reason="provider_error",
        message=clean_message,
        exception_type="OpenAIProviderError",
    )
    repetition = RepetitionResult(
        case_id="focused-decomposition",
        case_version=1,
        repetition=1,
        completed=False,
        gates=GateReport(
            results=[
                GateResult(gate_id="run_completed", passed=False, detail="")
            ]
        ),
        deterministic_quality=None,
        judge=JudgeFeedback(
            status="judge_not_run",
            not_run_reason="setup_failure",
            prompt_id="individual-agent-judge",
            rubric_version=1,
            prompt_fingerprint="abc123abc123",
            judge_model="gpt-5.6-luna",
            judge_configuration_fingerprint="def456def456",
        ),
        aggregate_quality=None,
        trace_url=None,
        errors=[failure],
    )
    case_result = CaseResult(
        case_id="focused-decomposition",
        case_version=1,
        repetitions=[repetition],
        average_quality=None,
        passed=False,
        lowest_scoring_trace_url=None,
    )
    return ExperimentResult(
        agent_name="planner",
        tier="controlled",
        experiment_name="planner-controlled-20260816T101500Z-abc1234",
        experiment_url="https://smith.langchain.com/o/x/experiments/planner-1",
        dataset_name="deep-research-planner-controlled-v1",
        dataset_url="https://smith.langchain.com/o/x/datasets/planner-1",
        cases=[case_result],
        status="INFRASTRUCTURE FAILURE",
        metadata=dict(_REPORTING_METADATA),
        errors=[failure],
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
        "DEEPSEEK_API_KEY": "sk-deepseek-abcdefgh",
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
    """The planner controlled cases, with one repetition scripted to
    fail during the model call (a provider failure, the same shape
    ``failing_target_harness`` already proves the target captures as a
    typed, non-escaping failure) -- so the case owning that repetition
    fails while the other cases, and the other eleven repetitions,
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


# --- Task 25: CLI runner fixtures -------------------------------------------
#
# cli.main's `runner` keyword is injected with a callable matching the
# calling convention cli._dispatch uses for the `agent` command:
# keyword-only agent_name, tier, case_id, config, reasoning_effort,
# judge_reasoning_effort, output_directory, experiment_prefix, verbose --
# returning (or raising in place of) an ExperimentResult. Each fixture
# below is a fake standing in for the real production pipeline (preflight
# + run_agent_evaluation), scripted to one outcome the CLI's exit-code
# mapping must handle.


def _cli_repetition(
    case_id: str,
    version: int,
    repetition: int,
    *,
    quality: float = 0.9,
    gate_passed: bool = True,
    gate_id: str = "run_completed",
) -> RepetitionResult:
    return RepetitionResult(
        case_id=case_id,
        case_version=version,
        repetition=repetition,
        completed=True,
        gates=GateReport(
            results=[GateResult(gate_id=gate_id, passed=gate_passed, detail="")]
        ),
        deterministic_quality=quality,
        judge=_scored_judge(quality),
        aggregate_quality=quality,
        trace_url=f"https://smith.langchain.com/o/x/r/{case_id}-{repetition}",
        errors=[],
    )


def _cli_experiment_result(
    *,
    agent_name: str = "researcher",
    tier: str = "controlled",
    status: str = "REVIEW REQUIRED",
    cases: list[CaseResult] | None = None,
    errors: list[EvaluationFailure] | None = None,
) -> ExperimentResult:
    if cases is None:
        repetitions = [
            _cli_repetition("conflicting-evidence", 1, n) for n in range(1, 4)
        ]
        cases = [
            CaseResult(
                case_id="conflicting-evidence",
                case_version=1,
                repetitions=repetitions,
                average_quality=0.9,
                passed=status != "FAILED",
                lowest_scoring_trace_url=repetitions[0].trace_url,
            )
        ]
    kebab = agent_name.replace("_", "-")
    return ExperimentResult(
        agent_name=agent_name,
        tier=tier,
        experiment_name=f"{kebab}-{tier}-20260817T000000Z-abc1234",
        experiment_url=f"https://smith.langchain.com/o/x/experiments/{kebab}-1",
        dataset_name=f"deep-research-{kebab}-{tier}-v1",
        dataset_url=f"https://smith.langchain.com/o/x/datasets/{kebab}-1",
        cases=cases,
        status=status,
        metadata=dict(_REPORTING_METADATA),
        errors=errors or [],
    )


@pytest.fixture
def passing_runner():
    """A clean REVIEW REQUIRED run: every repetition and gate passes."""

    def runner(**kwargs):
        del kwargs
        return _cli_experiment_result(status="REVIEW REQUIRED")

    return runner


@pytest.fixture
def failing_runner():
    """One case fails its gate, so the overall status is FAILED."""

    def runner(**kwargs):
        del kwargs
        repetitions = [
            _cli_repetition("conflicting-evidence", 1, n, gate_passed=(n != 1))
            for n in range(1, 4)
        ]
        case = CaseResult(
            case_id="conflicting-evidence",
            case_version=1,
            repetitions=repetitions,
            average_quality=0.9,
            passed=False,
            lowest_scoring_trace_url=repetitions[0].trace_url,
        )
        return _cli_experiment_result(status="FAILED", cases=[case])

    return runner


@pytest.fixture
def preflight_failing_runner():
    """A missing-credentials preflight failure -- exit 3, not exit 2."""
    from deep_research.evaluation.runner import PreflightError

    def runner(**kwargs):
        del kwargs
        raise PreflightError(
            "missing_credentials",
            "missing required credentials: DEEPSEEK_API_KEY, LANGSMITH_API_KEY",
        )

    return runner


@pytest.fixture
def registry_failing_runner():
    """An invalid local case registry -- a local-input error, exit 2."""
    from deep_research.evaluation.runner import PreflightError

    def runner(**kwargs):
        del kwargs
        raise PreflightError(
            "invalid_registry", "duplicate case identities: focused-decomposition"
        )

    return runner


@pytest.fixture
def interrupting_runner():
    """The user cancels mid-run."""

    def runner(**kwargs):
        del kwargs
        raise KeyboardInterrupt

    return runner


@pytest.fixture
def partially_failing_runner():
    """9 repetitions across 3 cases, all completed, one case failing.

    Repetitions: 9/9 completed must still be printed even though the
    overall status is FAILED: a partial failure never hides the
    repetitions that did complete.
    """

    def runner(**kwargs):
        del kwargs
        case_specs = [
            ("multi-source-coverage", True),
            ("conflicting-evidence", True),
            ("partial-search-failure", False),
        ]
        cases = []
        for case_id, passes in case_specs:
            repetitions = [
                _cli_repetition(case_id, 1, n, gate_passed=(passes or n != 1))
                for n in range(1, 4)
            ]
            cases.append(
                CaseResult(
                    case_id=case_id,
                    case_version=1,
                    repetitions=repetitions,
                    average_quality=0.9,
                    passed=passes,
                    lowest_scoring_trace_url=repetitions[0].trace_url,
                )
            )
        return _cli_experiment_result(status="FAILED", cases=cases)

    return runner


@pytest.fixture
def live_runner():
    """The single live case, one repetition, a clean pass."""

    def runner(**kwargs):
        del kwargs
        repetition = _cli_repetition("researcher-live-case", 1, 1)
        case = CaseResult(
            case_id="researcher-live-case",
            case_version=1,
            repetitions=[repetition],
            average_quality=0.9,
            passed=True,
            lowest_scoring_trace_url=repetition.trace_url,
        )
        return _cli_experiment_result(
            agent_name="researcher",
            tier="live",
            status="REVIEW REQUIRED",
            cases=[case],
        )

    return runner


@pytest.fixture
def recording_runner():
    """Records every keyword argument main calls it with, then passes."""

    class _RecordingRunner:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def __call__(self, **kwargs):
            self.calls.append(kwargs)
            return _cli_experiment_result(status="REVIEW REQUIRED")

    return _RecordingRunner()


@pytest.fixture
def leaking_runner():
    """Embeds a real-looking secret in data the CLI's rendering path never
    surfaces (ExperimentResult.errors / RepetitionResult.errors), proving
    redaction holds through the CLI's own rendering path: it reads the
    secret from OPENAI_API_KEY at call time (not fixture-setup time), so
    it picks up whatever value the test's monkeypatch.setenv call sets
    before main ever invokes this runner.
    """

    def runner(**kwargs):
        del kwargs
        secret = os.environ.get("OPENAI_API_KEY", "sk-abcdefghijklmnop")
        failure = EvaluationFailure(
            stage="provider",
            reason="provider_error",
            message=f"unredacted for this test only: {secret}",
            exception_type="OpenAIProviderError",
        )
        repetition = _cli_repetition("conflicting-evidence", 1, 1)
        repetition = repetition.model_copy(update={"errors": [failure]})
        case = CaseResult(
            case_id="conflicting-evidence",
            case_version=1,
            repetitions=[repetition],
            average_quality=0.9,
            passed=True,
            lowest_scoring_trace_url=repetition.trace_url,
        )
        return _cli_experiment_result(
            status="REVIEW REQUIRED", cases=[case], errors=[failure]
        )

    return runner


# --- Task 26: suite harnesses and fixtures ----------------------------------
#
# ``run_suite_evaluation`` builds its own per-agent ``EvaluationRuntimeConfig``,
# real cases (via ``cases_for``), and real target/judge ``OpenAIChatProvider``
# instances internally -- there is no per-agent injection point in its public
# signature, mirroring the CLI's own production wiring. What *is* injectable
# is ``evaluate`` (the ``aevaluate``-shaped callable), so every harness below
# hands it a shared ``FakeEvaluateRunner`` with an empty example list: an
# empty example list means the fake never actually invokes the real target
# callable, so the real (but offline-safe -- object construction only, no
# network) providers ``run_suite_evaluation`` builds are constructed but
# never called, and each agent's run still completes and writes its own
# ``results.json``.


@dataclass
class _SuiteHarness:
    """A shared fake LangSmith runner plus ``run_suite_evaluation`` kwargs,
    scoped across all six agents -- the suite-level analogue of
    ``_EvaluationHarness`` (Task 23), which was scoped to one agent."""

    runner: FakeEvaluateRunner
    factory_kwargs: dict = field(default_factory=dict)

    def kwargs(self, tmp_path: Path) -> dict:
        return {
            **self.factory_kwargs,
            "evaluate": self.runner,
            "output_directory": str(tmp_path),
        }


def _suite_factory_kwargs() -> dict:
    return dict(
        judge_reasoning_effort=None,
        experiment_prefix=None,
        config_path="config.yaml",
        langsmith_client_factory=FakeLangSmithClient,
        now=datetime(2026, 8, 16, 10, 15, tzinfo=timezone.utc),
        git=GitMetadata(commit="abc1234def", short_sha="abc1234", dirty=False),
    )


@pytest.fixture
def suite_harness() -> _SuiteHarness:
    """All six agents build and run cleanly; nothing is scripted to fail."""
    return _SuiteHarness(
        runner=FakeEvaluateRunner(examples=()),
        factory_kwargs=_suite_factory_kwargs(),
    )


@pytest.fixture
def partially_failing_suite_harness(monkeypatch) -> _SuiteHarness:
    """The fact-checker's own runtime-config build fails; the other five
    agents build and run normally.

    ``agent_prompt_fingerprint`` is the seam ``build_runtime_config``
    always calls while resolving one agent's ``EvaluationRuntimeConfig`` --
    patching it to fail for exactly one agent name exercises
    ``run_suite_evaluation``'s own try/except (not
    ``run_agent_evaluation``'s internal ``evaluate()`` guard, which never
    escapes to its caller), the same "one repetition's failure doesn't
    abort the others" discipline scaled up to one *agent's* failure not
    stopping the rest of the suite.
    """
    from deep_research.evaluation import config as config_module

    real_fingerprint = config_module.agent_prompt_fingerprint

    def flaky_fingerprint(agent_name: str) -> str:
        if agent_name == "fact_checker":
            raise RuntimeError("scripted agent-prompt-fingerprint failure")
        return real_fingerprint(agent_name)

    monkeypatch.setattr(
        config_module, "agent_prompt_fingerprint", flaky_fingerprint
    )

    return _SuiteHarness(
        runner=FakeEvaluateRunner(examples=()),
        factory_kwargs=_suite_factory_kwargs(),
    )


@pytest.fixture
def critic_experiment_result(repetition_result) -> ExperimentResult:
    """A minimal, real critic ``ExperimentResult`` for the suite rendering
    tests -- same shape as the ``experiment_result`` (planner) fixture."""
    return ExperimentResult(
        agent_name="critic",
        tier="controlled",
        experiment_name="critic-controlled-20260816T101500Z-abc1234",
        experiment_url="https://smith.langchain.com/o/x/experiments/critic-1",
        dataset_name="deep-research-critic-controlled-v1",
        dataset_url="https://smith.langchain.com/o/x/datasets/critic-1",
        cases=[
            CaseResult(
                case_id="approve-strong-report",
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


@pytest.fixture
def suite_result(
    experiment_result, researcher_experiment_result, critic_experiment_result
) -> SuiteResult:
    """A three-agent ``SuiteResult`` (planner, researcher, critic), all
    ``REVIEW REQUIRED`` -- enough for the rendering test's assertions
    without needing all six agents represented."""
    return SuiteResult(
        suite_id="suite-20260816T101500Z-abc1234",
        experiments=[
            experiment_result,
            researcher_experiment_result,
            critic_experiment_result,
        ],
        status="REVIEW REQUIRED",
        metadata={"git_commit": "abc1234def", "git_dirty": False},
    )
