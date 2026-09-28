"""Offline replay outcomes for API tests: the real graph, scripted, network denied."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from deep_research.e2e_evaluation.replay import (
    network_denied,
    offline_credentials,
    run_replay_scenario,
)
from deep_research.e2e_evaluation.replay_matrix import scenario_by_id
from deep_research.runtime.outcome import ResearchOutcome

EXTRA_PASS_CASE = "missing-target-triggers-one-extra-pass"
DROPPED_FINDING_CASE = "extra-pass-finds-nothing"
REDRAFT_CASE = "scoped-redraft-after-a-named-defect"
REVIEW_UNAVAILABLE_CASE = "review-unavailable"


@contextmanager
def guarded() -> Iterator[list[str]]:
    """Both harness guards, exactly as the replay-mode server holds them."""
    with offline_credentials(), network_denied() as attempts:
        yield attempts


def replay_outcome(case_id: str, root: Path) -> ResearchOutcome:
    """Run one case through the CLI harness and return its ``ResearchOutcome``."""
    with guarded() as attempts:
        run = run_replay_scenario(scenario_by_id(case_id), root=root)
    assert attempts == [], attempts
    return run.graph_run
