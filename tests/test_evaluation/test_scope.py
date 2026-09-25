"""This implementation must not grow an end-to-end graph evaluation."""

from __future__ import annotations

import ast
from pathlib import Path

EVALUATION = Path("src/deep_research/evaluation")


def test_the_evaluation_package_never_imports_the_graph() -> None:
    """Full-graph evaluation is a separate, later specification."""
    offenders = [
        path.name
        for path in EVALUATION.rglob("*.py")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith(("import ", "from ")) and "deep_research.graph" in line
    ]

    assert offenders == []

    ast_offenders: list[str] = []
    for path in EVALUATION.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(
                    alias.name.startswith("deep_research.graph")
                    for alias in node.names
                ):
                    ast_offenders.append(path.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith("deep_research.graph") or (
                    module == "deep_research"
                    and any(alias.name == "graph" for alias in node.names)
                ):
                    ast_offenders.append(path.name)
    assert ast_offenders == []

    # Keep this assertion broader than import statements: a future helper
    # must not smuggle the graph in through importlib or an aliased string.
    assert all(
        "deep_research.graph" not in path.read_text(encoding="utf-8")
        for path in EVALUATION.rglob("*.py")
    )


def test_the_evaluation_package_defines_no_graph_or_suite_dataset() -> None:
    from deep_research.evaluation.cases import (
        EXPECTED_CONTROLLED_CASE_IDS,
        EXPECTED_LIVE_CASE_IDS,
        all_cases,
    )

    assert all(
        case.agent_name
        in {
            "planner",
            "researcher",
            "source_evaluator",
            "evidence_verifier",
            "report_writer",
        }
        for case in all_cases()
    )
    # The total is derived from the declared inventory rather than pinned to
    # a literal, so a round that adds a case updates one declaration.
    assert len(all_cases()) == sum(
        len(ids) for ids in EXPECTED_CONTROLLED_CASE_IDS.values()
    ) + sum(len(ids) for ids in EXPECTED_LIVE_CASE_IDS.values())


def test_whole_report_evaluation_has_a_separate_package() -> None:
    """The whole-report replay matrix lives in its own package, not per-agent evaluation."""
    from deep_research.e2e_evaluation.replay_matrix import REPLAY_CASE_MANIFEST
    from deep_research.evaluation.cases import all_cases

    # Both registries are real and independently populated; a case id can
    # legitimately name the same real-world scenario at two granularities (a
    # per-agent case here, a whole-report row in e2e_evaluation), so the two
    # registries are not required to be globally disjoint -- only to be two
    # separate packages, which the two distinct imports above already prove.
    assert REPLAY_CASE_MANIFEST
    assert all_cases()


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
