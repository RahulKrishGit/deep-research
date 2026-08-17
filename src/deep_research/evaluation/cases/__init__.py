"""The source-of-truth case registry for individual agent evaluation.

Task 7 scaffolds only the registry mechanics: the six case modules below
start empty and Tasks 10–15 fill them, and Task 9 adds the shared fixture
builders, ``case_by_id``/``case_by_identity`` lookup, and
``validate_registry``.
"""

from __future__ import annotations

from deep_research.evaluation.models import (
    AGENT_NAMES,
    AgentName,
    EvaluationCase,
    EvaluationTier,
)

CASE_REGISTRY_VERSION = 1

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
