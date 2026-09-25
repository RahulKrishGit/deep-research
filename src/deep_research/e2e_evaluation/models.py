"""Typed contracts for the offline real-agent replay matrix.

The retired scripted-double harness's contracts (the six-agent campaign,
its whole-report judge, and its deterministic evaluation of claim-era state)
are gone: PD-14 replaces that harness entirely with the real-agent replay
matrix, which judges a run by its own declared result and invariants
(``e2e_evaluation.replay.expectation_failures``), not by a bounded judge
adapter over a scripted double's state.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from deep_research.utils.types import ContractModel

REPLAY_SUITE_SCHEMA_VERSION = 1


class ReplayRepetitionResult(ContractModel):
    """One repetition of one real-agent replay row.

    The real-agent harness's repetition record: a replay runs the five
    production agents through the real graph, the real reviewer, renderer
    and publisher, and judges nothing with a whole-report judge, so a
    repetition carries what the run actually produced — the terminal
    quality, the exit code, the ways it fell short of its case's declared
    result — plus the two pieces of harness evidence a declared result has
    no place for: the socket connections the run attempted, and the
    fingerprint of the report it published.
    """

    case_id: str
    repetition: int = Field(ge=1)
    session_id: str
    terminal_quality: str
    exit_code: int
    expectation_failures: list[str] = Field(default_factory=list)
    answered_target_ids: list[str] = Field(default_factory=list)
    network_attempts: list[str] = Field(default_factory=list)
    """Every socket connect this repetition attempted. Empty is the evidence.

    A suite that cannot show this empty on every repetition is not accepted:
    the harness exists to be network-zero, so an attempt is a failure of the
    proof rather than a warning about it.
    """
    report_fingerprint: str
    """The published report's hash, over its canonical form.

    Canonicalized because two things about a report are facts about the
    *session* that made it: the ``As of`` clock read, and which ordinal each
    reference was assigned. The fingerprint is what makes "the same result
    every time" a measurable claim rather than a hope.
    """


class ReplayCaseResult(ContractModel):
    """One declared replay row, and the repetitions that ran it."""

    case_id: str
    version: int = Field(ge=1)
    expected_product_result: str
    decisive_assertion: str
    repetitions: list[ReplayRepetitionResult] = Field(min_length=1)
    deterministic: bool
    """Whether every repetition produced one identical outcome."""
    passed: bool
    """Whether every repetition met its case's declared result, deterministically.

    Determinism is required, not merely reported: the repetitions exist to
    show order and identity are deterministic and that the runs are
    isolated, so a row whose repetitions disagree did not pass however clean
    each repetition's own result was.
    """
    artifact_path: str | None = None


class ReplaySuiteResult(ContractModel):
    """Round-trippable real-agent suite artifact."""

    campaign_id: str = Field(min_length=1)
    tier: Literal["controlled"]
    mode: Literal["real-agent"]
    manifest_version: int = Field(ge=1)
    case_version: int = Field(ge=1)
    repetitions: int = Field(ge=1)
    cases: list[ReplayCaseResult] = Field(min_length=1)
    accepted: bool
    """The suite's verdict: every row produced the result it declares.

    A real-agent row can declare a partial result — ``same-work-mirror`` does
    — and a suite that read that as a failure could not hold the negative
    half of its own matrix. Determinism is part of the verdict too: a row
    that passed is a row whose repetitions agreed, so a suite holding a row
    whose runs disagreed is not accepted. The stricter, product-level fact is
    ``rows_accepted``.
    """
    rows_accepted: bool = True
    """Every repetition's own product result was an accepted one."""
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    artifact_path: str | None = None


__all__ = [
    "REPLAY_SUITE_SCHEMA_VERSION",
    "ReplayCaseResult",
    "ReplayRepetitionResult",
    "ReplaySuiteResult",
]
