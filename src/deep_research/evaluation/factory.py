"""Construct exactly one production-configured agent for evaluation.

There is no second wiring path here: this module calls the same
``runtime.assembly.build_agent`` mapping that production's
``build_agents`` calls, so evaluation cannot drift from production.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from deep_research.agents.base import StructuredCompleter
from deep_research.agents.errors import AgentConfigurationError
from deep_research.agents.source_evaluator import ReputationSource
from deep_research.evaluation.config import EvaluationRuntimeConfig
from deep_research.observability import Tracker
from deep_research.runtime.assembly import build_agent
from deep_research.tools.base import BaseTool
from deep_research.utils.config import ConfigSettings


class AgentConstructionError(RuntimeError):
    """Production-parity construction failed before any model call."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class EvaluationAgentBuild:
    """One constructed agent and the collaborators it was handed."""

    agent: Any
    tools: tuple[BaseTool, ...]
    provider: StructuredCompleter
    session_id: str
    settings: ConfigSettings


def evaluation_session_id(
    runtime: EvaluationRuntimeConfig,
    *,
    case_id: str,
    repetition: int,
) -> str:
    """A unique, greppable session id for one repetition.

    Unique per repetition so no two runs share a scratchpad namespace or a
    trace, and prefixed with ``evaluation-`` so an evaluation session can
    never be mistaken for a research session in LangSmith.
    """
    return f"evaluation-{runtime.experiment_name}-{case_id}-r{repetition}"


def build_evaluation_agent(
    runtime: EvaluationRuntimeConfig,
    settings: ConfigSettings,
    *,
    tracker: Tracker,
    provider: StructuredCompleter,
    tools: Sequence[BaseTool],
    session_id: str,
    reputation: ReputationSource | None,
) -> EvaluationAgentBuild:
    """Build the one agent this evaluation is about.

    ``AgentConfigurationError`` — including the declared-but-uninjected
    tool failure ``AgentToolset`` raises at construction — becomes a typed
    ``AgentConstructionError`` so preflight reports it and exits 3 without
    a traceback.
    """
    try:
        agent = build_agent(
            runtime.agent_name,
            settings,
            tracker=tracker,
            provider=provider,
            tools=tools,
            session_id=session_id,
            reputation=reputation,
        )
    except AgentConfigurationError as error:
        raise AgentConstructionError(
            f"{runtime.agent_name} could not be built with production-parity "
            f"wiring: {error}",
            reason="agent_unbuildable",
        ) from error
    return EvaluationAgentBuild(
        agent=agent,
        tools=tuple(tools),
        provider=provider,
        session_id=session_id,
        settings=settings,
    )
