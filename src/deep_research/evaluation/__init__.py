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
from deep_research.evaluation.runner import run_agent_evaluation

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


def __getattr__(name: str):
    """Resolve ``run_suite_evaluation`` lazily (PEP 562).

    ``run_suite_evaluation`` is Task 26's deliverable ("The suite
    command") and does not exist in ``runner.py`` yet -- Task 26 comes
    after this task (Task 25, the CLI) in the plan's own numbering. A
    plain eager ``from deep_research.evaluation.runner import
    run_suite_evaluation`` at the top of this module would raise
    ``ImportError`` on every import of this package, including for
    everything Task 25 itself needs (``main``, the typed contracts above).
    Resolving it lazily here means importing this package never fails
    before Task 26 lands, and once it does, this branch starts returning
    the real function with no further change required -- the ``__all__``
    entry above is already in place.
    """
    if name == "run_suite_evaluation":
        from deep_research.evaluation.runner import run_suite_evaluation

        return run_suite_evaluation
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
