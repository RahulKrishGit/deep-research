"""Typed contracts for the offline real-agent replay matrix.

A run is judged by its own declared result and invariants
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
    verified_findings: int | None = None
    """The seven counts, read from the run's own final state.

    ``verified_findings``, ``dropped_findings``,
    ``context_unchecked_findings``, ``duplicate_fact_rows``,
    ``unjudged_sentences`` and ``missing_required_targets`` come from
    ``state.quality`` -- the snapshot ``compute_report_quality`` stamped on the
    finished pass -- and ``extra_passes`` from ``state.iteration``. They are
    read rather than counted again here: a second count made by the harness
    could only disagree with the run it is judging.

    ``None`` is the honest value for the six the snapshot holds when no
    snapshot was stamped: a pass that composed no report judged nothing, and a
    recorded zero would read as a judgement it never made.

    ``duplicate_fact_rows`` and ``unjudged_sentences`` are recorded, not
    re-enforced. Both are already gates of the quality pass, and a row fails on
    any ``hard:<gate>`` gap its case does not allow, so the suite does not
    check them a second time.
    """
    dropped_findings: int | None = None
    context_unchecked_findings: int | None = None
    duplicate_fact_rows: int | None = None
    unjudged_sentences: int | None = None
    """Kept sentences with no verdict and no batch failure to blame."""
    missing_required_targets: int | None = None
    """Required targets the run's own accounting left unanswered."""
    network_attempts: list[str] = Field(default_factory=list)
    """Every socket connect this repetition attempted. Empty is the evidence.

    A suite that cannot show this empty on every repetition is not accepted:
    the harness exists to be network-zero, so an attempt is a failure of the
    proof rather than a warning about it.
    """
    report_fingerprint: str
    """The published report's hash, over its canonical form.

    Canonicalized because one thing about a report is a fact about the
    *session* that made it: which ordinal each reference was assigned. The
    byline is hashed with the rest of the report, so two repetitions that
    told the reader different counts are two different outcomes. The
    fingerprint is what makes "the same result every time" a measurable
    claim rather than a hope.
    """
    extra_passes: int = Field(ge=0)
    """The extra passes the graph spent (``state.iteration``).

    Required rather than defaulted: the iteration count exists for every
    finished run, so a repetition that does not state it is a repetition that
    recorded nothing about the repair round.
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
