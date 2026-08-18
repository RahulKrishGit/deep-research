"""This implementation must not grow an end-to-end graph evaluation."""

from __future__ import annotations

from pathlib import Path

EVALUATION = Path("src/deep_research/evaluation")


def test_the_evaluation_package_never_imports_the_graph() -> None:
    """Full-graph evaluation is a separate, later specification."""
    offenders = [
        path.name
        for path in EVALUATION.rglob("*.py")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith(("import ", "from "))
        and "deep_research.graph" in line
    ]

    assert offenders == []


def test_the_evaluation_package_defines_no_graph_or_suite_dataset() -> None:
    from deep_research.evaluation.cases import all_cases

    assert all(
        case.agent_name in {
            "planner",
            "researcher",
            "source_evaluator",
            "fact_checker",
            "synthesizer",
            "critic",
        }
        for case in all_cases()
    )
    assert len(all_cases()) == 24


def test_the_evaluation_cli_exposes_exactly_three_commands() -> None:
    from deep_research.evaluation.cli import build_parser

    actions = [
        action
        for action in build_parser()._subparsers._group_actions
        if hasattr(action, "choices")
    ]
    assert set(actions[0].choices) == {"list", "agent", "suite"}


def test_no_automatic_human_approval_status_exists() -> None:
    """The harness reports the review gate; it never grants approval."""
    from deep_research.evaluation.models import ExperimentResult

    statuses = ExperimentResult.model_fields["status"].annotation
    assert "APPROVED" not in str(statuses)
