"""The source-of-truth case registry for individual agent evaluation.

Cases are code-backed typed fixtures rather than YAML because
``ResearchState`` and dependency behavior are richer than a safe
serializable representation. The LangSmith datasets hold a secret-free
mirror; this module is the truth they are synchronized from.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import JsonValue

from deep_research.evaluation.models import (
    AGENT_NAMES,
    AgentName,
    CaseExpectations,
    DeterministicMetric,
    EvaluationCase,
    EvaluationTier,
    JudgeRubric,
    RubricDimension,
    UnknownCaseError,
)
from deep_research.utils.types import (
    Claim,
    Critique,
    Finding,
    MemorySnapshot,
    ResearchState,
    ScoredSource,
    SubTopic,
)

CASE_REGISTRY_VERSION = 1

# A fixed timestamp so a case fixture is byte-identical between runs and a
# dataset example never changes just because the clock moved.
FIXED_TIMESTAMP = "2026-08-01T00:00:00+00:00"


class CaseRegistryError(ValueError):
    """The local case registry is invalid; nothing may be executed."""


def sub_topic(
    title: str,
    *,
    rationale: str,
    queries: Sequence[str],
    criteria: Sequence[str],
    priority: int,
) -> SubTopic:
    return SubTopic(
        title=title,
        rationale=rationale,
        search_queries=list(queries),
        success_criteria=list(criteria),
        priority=priority,
    )


def finding(
    content: str,
    *,
    url: str,
    title: str,
    sub_topic_title: str,
    confidence: float = 0.8,
) -> Finding:
    return Finding(
        content=content,
        source_url=url,
        source_title=title,
        extracted_at=FIXED_TIMESTAMP,
        confidence=confidence,
        related_sub_topic=sub_topic_title,
    )


def scored_source(
    url: str,
    *,
    title: str,
    authority: float,
    recency: float,
    relevance: float,
    corroboration: float,
    overall: float,
    rationale: str,
    low_confidence: bool = False,
) -> ScoredSource:
    return ScoredSource(
        url=url,
        title=title,
        authority_score=authority,
        recency_score=recency,
        relevance_score=relevance,
        corroboration_score=corroboration,
        overall_score=overall,
        rationale=rationale,
        low_confidence=low_confidence,
    )


def claim(
    text: str,
    *,
    urls: Sequence[str],
    verdict: str,
    confidence: float,
    evidence: Sequence[str] = (),
    contradictions: Sequence[str] = (),
) -> Claim:
    return Claim(
        text=text,
        source_urls=list(urls),
        verdict=verdict,
        confidence=confidence,
        evidence=list(evidence),
        contradictions=list(contradictions),
    )


def evaluation_state(
    *,
    case_id: str,
    question: str,
    sub_topics: Sequence[SubTopic] = (),
    findings: Sequence[Finding] = (),
    sources: Sequence[ScoredSource] = (),
    claims: Sequence[Claim] = (),
    report: str | None = None,
    critique: Critique | None = None,
    iteration: int = 0,
    max_iterations: int = 3,
    memory_context: MemorySnapshot | None = None,
) -> ResearchState:
    """One curated starting state.

    ``session_id`` is derived from the case id and always prefixed with
    ``evaluation-`` so no case can look like a production session in a
    trace or a memory namespace.

    Task 7 review: the controlled memory double drops a scripted
    ``"timestamp"`` field from a seeded entry and falls back to the real
    clock, so a memory seed can never be pinned to a fixed time. Keep any
    assertion on a scripted memory entry's timestamp out of the
    deterministic metrics.
    """
    return ResearchState(
        session_id=f"evaluation-{case_id}",
        original_question=question,
        sub_topics=list(sub_topics),
        raw_findings=list(findings),
        evaluated_sources=list(sources),
        verified_claims=list(claims),
        report=report,
        critique=critique,
        iteration=iteration,
        max_iterations=max_iterations,
        memory_context=memory_context or MemorySnapshot(),
    )


def metrics(*pairs: tuple[str, float, str]) -> list[DeterministicMetric]:
    """Build the weighted metric list, keeping the weights visible inline."""
    return [
        DeterministicMetric(
            metric_id=metric_id, weight=weight, description=description
        )
        for metric_id, weight, description in pairs
    ]


def rubric(
    rubric_id: str,
    *dimensions: tuple[str, str, str, str],
) -> JudgeRubric:
    """``(dimension_id, description, anchor_1_0, anchor_0_0)`` per dimension."""
    return JudgeRubric(
        rubric_id=rubric_id,
        version=1,
        agent_dimensions=[
            RubricDimension(
                dimension_id=dimension_id,
                description=description,
                anchors={"1.0": high, "0.0": low},
            )
            for dimension_id, description, high, low in dimensions
        ],
    )


# Failure-injection scenarios are scripted in dependencies.py (Tasks 10-15).
# Task 7 review: scripted failures typed as ``httpx.TimeoutException`` /
# ``httpx.HTTPStatusError`` are retried by the real tools (3 attempts each),
# so a scripted failure shows up triple-counted in call counts. Prefer a
# plain ``RuntimeError`` for failure-recovery scenarios, or assert the
# tripled count explicitly.
def build_case(
    *,
    case_id: str,
    agent_name: AgentName,
    tier: EvaluationTier,
    title: str,
    purpose: str,
    state: ResearchState,
    dependency_scenario: str,
    expectations: CaseExpectations,
    judge_rubric: JudgeRubric,
    metadata: dict[str, JsonValue] | None = None,
    version: int = 1,
) -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        version=version,
        agent_name=agent_name,
        tier=tier,
        title=title,
        purpose=purpose,
        state=state,
        dependency_scenario=dependency_scenario,
        expectations=expectations,
        judge_rubric=judge_rubric,
        metadata=metadata or {},
    )


from deep_research.evaluation.cases import (  # noqa: E402
    critic,
    fact_checker,
    planner,
    researcher,
    source_evaluator,
    synthesizer,
)

_MODULES = {
    "planner": planner,
    "researcher": researcher,
    "source_evaluator": source_evaluator,
    "fact_checker": fact_checker,
    "synthesizer": synthesizer,
    "critic": critic,
}


def all_cases() -> tuple[EvaluationCase, ...]:
    """Every case, in agent order then controlled-before-live order."""
    collected: list[EvaluationCase] = []
    for agent_name in AGENT_NAMES:
        module = _MODULES[agent_name]
        collected.extend(module.CONTROLLED_CASES)
        collected.extend(module.LIVE_CASES)
    return tuple(collected)


def cases_for(
    agent_name: AgentName, tier: EvaluationTier
) -> tuple[EvaluationCase, ...]:
    return tuple(
        case
        for case in all_cases()
        if case.agent_name == agent_name and case.tier == tier
    )


def case_by_id(
    agent_name: AgentName, tier: EvaluationTier, case_id: str
) -> EvaluationCase:
    available = cases_for(agent_name, tier)
    for case in available:
        if case.case_id == case_id:
            return case
    valid = ", ".join(item.case_id for item in available)
    raise UnknownCaseError(
        f"unknown {tier} case {case_id!r} for {agent_name}; "
        f"expected one of: {valid}"
    )


def case_by_identity(case_id: str, version: int) -> EvaluationCase:
    """Look a case up the way a dataset example identifies it."""
    for case in all_cases():
        if case.identity == (case_id, version):
            return case
    raise UnknownCaseError(
        f"no case {case_id!r} at version {version} is in the local registry"
    )


def validate_registry(
    cases: Sequence[EvaluationCase] | None = None,
) -> None:
    """Fail before any model call when the registry cannot be trusted.

    Checks the three things that would corrupt a dataset or an experiment:
    duplicate identities, one id at conflicting versions, and the wrong
    number of cases for an agent or tier.
    """
    catalog = list(all_cases() if cases is None else cases)

    seen: dict[tuple[str, int], int] = {}
    versions: dict[str, set[int]] = {}
    for case in catalog:
        seen[case.identity] = seen.get(case.identity, 0) + 1
        versions.setdefault(case.case_id, set()).add(case.version)

    duplicates = sorted(key[0] for key, count in seen.items() if count > 1)
    if duplicates:
        raise CaseRegistryError(
            f"duplicate case identities: {', '.join(duplicates)}"
        )

    conflicting = sorted(
        case_id for case_id, found in versions.items() if len(found) > 1
    )
    if conflicting:
        raise CaseRegistryError(
            f"conflicting versions for case ids: {', '.join(conflicting)}"
        )

    for agent_name in AGENT_NAMES:
        controlled = [
            case
            for case in catalog
            if case.agent_name == agent_name and case.tier == "controlled"
        ]
        live = [
            case
            for case in catalog
            if case.agent_name == agent_name and case.tier == "live"
        ]
        if len(controlled) != 3 or len(live) != 1:
            raise CaseRegistryError(
                f"{agent_name} must define exactly 3 controlled and 1 live "
                f"case; found {len(controlled)} controlled and {len(live)} live"
            )
