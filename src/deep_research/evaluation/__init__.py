"""Evaluation harness for the individual research agents.

Public re-export surface: the typed contracts every CLI consumer needs,
plus the CLI's own ``main``.
"""

from __future__ import annotations

from deep_research.evaluation.cli import main
from deep_research.evaluation.models import (
    AgentName,
    EvaluationCase,
    EvaluationTier,
    ExperimentResult,
    SuiteResult,
)
from deep_research.evaluation.runner import run_agent_evaluation, run_suite_evaluation

__all__ = [
    "AgentName",
    "EvaluationTier",
    "EvaluationCase",
    "ExperimentResult",
    "SuiteResult",
    "run_agent_evaluation",
    "run_suite_evaluation",
    "main",
]
