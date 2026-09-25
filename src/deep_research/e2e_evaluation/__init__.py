"""The offline real-agent replay matrix, kept separate from agent evaluation."""

from __future__ import annotations

from deep_research.e2e_evaluation.models import (
    ReplayCaseResult,
    ReplayRepetitionResult,
    ReplaySuiteResult,
)
from deep_research.e2e_evaluation.replay_matrix import (
    REPLAY_CASE_IDS,
    REPLAY_CASE_MANIFEST,
    REPLAY_CASE_MANIFEST_VERSION,
    REPLAY_CASE_VERSION,
    ReplayCaseEntry,
    manifest_entry,
    replay_scenarios,
    scenario_by_id,
)
from deep_research.e2e_evaluation.runner import main, run_replay_suite

__all__ = [
    "REPLAY_CASE_IDS",
    "REPLAY_CASE_MANIFEST",
    "REPLAY_CASE_MANIFEST_VERSION",
    "REPLAY_CASE_VERSION",
    "ReplayCaseEntry",
    "ReplayCaseResult",
    "ReplayRepetitionResult",
    "ReplaySuiteResult",
    "main",
    "manifest_entry",
    "replay_scenarios",
    "run_replay_suite",
    "scenario_by_id",
]
