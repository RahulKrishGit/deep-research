"""Offline real-agent replay matrix runner and artifact writer."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import JsonValue

from deep_research.e2e_evaluation.models import (
    ReplayCaseResult,
    ReplayRepetitionResult,
    ReplaySuiteResult,
)
from deep_research.e2e_evaluation.replay import (
    expectation_failures,
    network_denied,
    run_replay_scenario,
)
from deep_research.e2e_evaluation.replay_matrix import (
    REPLAY_CASE_MANIFEST,
    REPLAY_CASE_MANIFEST_VERSION,
    REPLAY_CASE_VERSION,
    ReplayCaseEntry,
    scenario_by_id,
)
from deep_research.utils.types import QUALITY_STATUS_ACCEPTED

LIVE_TIER_NOT_RUN = (
    "live tier is declared only and has no runner; running it requires a "
    "separately authorized canary"
)
DEFAULT_OUTPUT_DIRECTORY = Path("output/evaluations/e2e")
CONTROLLED_REPETITIONS = 3

# The controlled suite runs one harness: the real agents over the replay
# matrix. PD-14 retires the six-agent scripted-double harness that used to
# replay a graph that no longer exists -- the real-agent matrix covers the
# new graph end to end, so the mode axis is gone rather than defaulted.
REAL_AGENT_MODE = "real-agent"
REPLAY_SUITE_FILENAME = "replay-suite.json"
_REPLAY_STORAGE_DIRECTORY = "replay"

# What the harness is, in the words its own output uses. A reader who sees
# only the terminal gets the same disclosure the artifact's `mode` carries:
# which agents produced the numbers.
AGENTS_PRODUCTION = "Agents: production classes through the real graph"

_AS_OF_PREFIX = "*As of "
_REFERENCE_LINE = re.compile(r"^(\d+)\. (.*)$")
_CITATION = re.compile(r"\[(\d+)\]")
_CITATION_RUN = re.compile(r"(?:\[\d+\]){2,}")


def graph_revision() -> str:
    """Return the local graph revision without contacting a remote service."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    revision = result.stdout.strip()
    return revision if result.returncode == 0 and revision else "unknown"


def graph_revision_value() -> str:
    """Named wrapper makes the metadata source easy to replace in tests."""
    return graph_revision()


def canonical_report_fingerprint(report: str) -> str:
    """The published report's hash, over the part of it the reader was shown.

    Two things about a published report are facts about the *session* that
    made it rather than about the report: the byline that opens with ``*As
    of``, which states the newest timestamp the recorded *evidence* carries
    and the source/finding counts of that pass rather than anything the
    reader authored, and the ordinal each source was given, which is the
    order that session's reads were recorded in. Read identity is
    session-scoped by the product's own contract, so two repetitions of one
    fixture cite the same sources numbered in whichever order their own reads
    landed. Hashing the rendered text as it stands would report a
    deterministic harness as non-deterministic whenever citation order moved.

    So the hash is taken over the canonical form: the byline dropped, and
    every reference renumbered by its own label. What remains comparable is
    which sources the reader was shown against which sentences, so a
    citation set that gained, lost or moved a source still differs here. A
    replay row is reproducible regardless, because the harness stamps every
    repetition from one pinned clock (``replay.replay_clock``): the byline
    the hash drops would be identical across repetitions even if it were
    kept.
    """
    body: list[str] = []
    references: list[tuple[str, str]] = []
    for line in report.splitlines():
        if line.startswith(_AS_OF_PREFIX):
            continue
        match = (
            _REFERENCE_LINE.match(line)
            if references or line[:1].isdigit()
            else None
        )
        if match is not None:
            references.append((match.group(1), match.group(2)))
            continue
        body.append(line)
    canonical = {
        number: index
        for index, (number, _label) in enumerate(
            sorted(references, key=lambda item: item[1]), start=1
        )
    }

    def _sort_run(run: re.Match[str]) -> str:
        markers = _CITATION.findall(run.group(0))
        return "".join(f"[{marker}]" for marker in sorted(markers, key=int))

    rewritten = [
        _CITATION_RUN.sub(
            _sort_run,
            _CITATION.sub(
                lambda hit: f"[{canonical.get(hit.group(1), int(hit.group(1)))}]",
                line,
            ),
        )
        for line in body
    ]
    listing = sorted(
        (f"{canonical[number]}. {label}" for number, label in references),
        key=lambda line: int(line.split(".", 1)[0]),
    )
    return hashlib.sha256("\n".join(rewritten + listing).encode("utf-8")).hexdigest()


def _replay_repetition(
    entry: ReplayCaseEntry, repetition: int, *, storage: Path
) -> ReplayRepetitionResult:
    """Run one declared row once, with the socket layer denied.

    The guard is not decoration: it is what turns "network-zero" from a claim
    about the fixture into a recorded fact about the run, and the attempts it
    records are carried into the result rather than asserted and dropped.

    The run's dates come from the harness's pinned clock, so the fingerprint
    below is a fact about the row rather than about the day it ran: three
    repetitions that straddle midnight UTC publish the same report, and the
    determinism requirement is met on the agents' behaviour alone.
    """
    session_id = f"replay-{entry.case_id}-r{repetition}"
    with network_denied() as attempts:
        run = run_replay_scenario(
            scenario_by_id(entry.case_id),
            root=storage,
            session_id=session_id,
            repetition=repetition,
        )
    return ReplayRepetitionResult(
        case_id=entry.case_id,
        repetition=repetition,
        session_id=run.session_id,
        terminal_quality=run.quality_status,
        exit_code=run.exit_code,
        expectation_failures=expectation_failures(run),
        answered_target_ids=run.answered_target_ids(),
        network_attempts=list(attempts),
        report_fingerprint=canonical_report_fingerprint(run.report),
    )


def _replay_case_result(
    entry: ReplayCaseEntry, repetitions: Sequence[ReplayRepetitionResult]
) -> ReplayCaseResult:
    """One row's verdict: what it produced, and whether that was its result.

    Determinism is asserted over the whole outcome — the exit code, the
    terminal quality, the answered targets and the report — not over the exit
    code alone, which two runs can agree on while publishing different
    reports. It is also *required*, not merely reported: the three repetitions
    exist to show order and identity are deterministic and that the runs are
    isolated, so a row whose repetitions disagree is not a row that passed,
    however clean each repetition's own result was.
    """
    outcomes = {
        (
            item.exit_code,
            item.terminal_quality,
            tuple(sorted(item.answered_target_ids)),
            item.report_fingerprint,
        )
        for item in repetitions
    }
    deterministic = len(outcomes) == 1
    return ReplayCaseResult(
        case_id=entry.case_id,
        version=entry.version,
        expected_product_result=entry.expected_product_result,
        decisive_assertion=entry.decisive_assertion,
        repetitions=list(repetitions),
        deterministic=deterministic,
        passed=deterministic
        and all(not item.expectation_failures for item in repetitions),
    )


def run_replay_suite(
    *,
    tier: str = "controlled",
    repetitions: int = CONTROLLED_REPETITIONS,
    output_directory: str | Path | None = None,
) -> ReplaySuiteResult:
    """Run every row of the real-agent matrix and write the suite artifact.

    This is the real thing: five production agents through the real compiled
    graph, the real reviewer, renderer and publisher, with only the external
    boundaries scripted and the socket layer denied for every repetition. The
    inventory is ``REPLAY_CASE_MANIFEST``, so the suite covers every row the
    versioned manifest declares rather than a hardcoded set.
    """
    if tier == "live":
        raise RuntimeError(LIVE_TIER_NOT_RUN)
    if tier != "controlled":
        raise ValueError("tier must be controlled or live")
    if repetitions != CONTROLLED_REPETITIONS:
        raise ValueError("controlled replay suite requires exactly 3 repetitions")
    root = Path(output_directory or DEFAULT_OUTPUT_DIRECTORY)
    results = [
        _replay_case_result(
            entry,
            [
                _replay_repetition(
                    entry,
                    repetition,
                    # Each repetition gets its own storage root, so the
                    # repetitions are isolated from one another.
                    storage=(
                        root
                        / _REPLAY_STORAGE_DIRECTORY
                        / entry.case_id
                        / f"repetition-{repetition}"
                    ),
                )
                for repetition in range(1, repetitions + 1)
            ],
        )
        for entry in REPLAY_CASE_MANIFEST
    ]
    attempts = sum(
        len(item.network_attempts)
        for case in results
        for item in case.repetitions
    )
    metadata: dict[str, JsonValue] = {
        "graph_revision": graph_revision_value(),
        "manifest_version": REPLAY_CASE_MANIFEST_VERSION,
        "case_version": REPLAY_CASE_VERSION,
        "case_ids": [case.case_id for case in results],
        # Written from the recorded attempts, not from the harness's intent:
        # an artifact claiming "zero" beside a non-zero attempt count would be
        # the same silent substitution this suite exists to prevent.
        "network": "zero" if not attempts else "attempted",
        "network_attempts": attempts,
    }
    suite = ReplaySuiteResult(
        campaign_id=(
            "controlled-replay-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + uuid4().hex[:8]
        ),
        tier="controlled",
        mode=REAL_AGENT_MODE,
        manifest_version=REPLAY_CASE_MANIFEST_VERSION,
        case_version=REPLAY_CASE_VERSION,
        repetitions=repetitions,
        cases=results,
        # A suite whose runs reached the network is not a suite that passed,
        # however clean every row's own result was.
        accepted=all(case.passed for case in results) and attempts == 0,
        # The stricter product-level fact, kept under its own name: a row can
        # produce the partial result it declares — which is what ``passed``
        # means here, together with a deterministic outcome — while the
        # product did not accept its report.
        rows_accepted=all(
            item.terminal_quality == QUALITY_STATUS_ACCEPTED
            for case in results
            for item in case.repetitions
        ),
        metadata=metadata,
    )
    root.mkdir(parents=True, exist_ok=True)
    artifact = root / REPLAY_SUITE_FILENAME
    suite = suite.model_copy(update={"artifact_path": str(artifact)})
    artifact.write_text(suite.model_dump_json(indent=2), encoding="utf-8")
    return suite


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m deep_research.e2e_evaluation",
        description="Run the network-zero real-agent replay matrix.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="list the matrix's case ids")
    suite_parser = subparsers.add_parser("suite", help="run the controlled suite")
    suite_parser.add_argument(
        "--tier", choices=("controlled", "live"), default="controlled"
    )
    suite_parser.add_argument("--repetitions", type=int, default=CONTROLLED_REPETITIONS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI for list and suite; report bodies never go to stdout."""
    options = build_parser().parse_args(argv)
    try:
        if options.command == "list":
            print(
                real_agent_mode_label(
                    len(REPLAY_CASE_MANIFEST),
                    manifest_version=REPLAY_CASE_MANIFEST_VERSION,
                    case_version=REPLAY_CASE_VERSION,
                )
            )
            print(AGENTS_PRODUCTION)
            for entry in REPLAY_CASE_MANIFEST:
                print(f"  {entry.case_id}: {entry.title}")
            return 0
        suite = run_replay_suite(
            tier=options.tier,
            repetitions=options.repetitions,
        )
        for line in real_agent_suite_lines(suite):
            print(line)
        return 0 if suite.accepted else 1
    except (KeyError, RuntimeError, ValueError) as error:
        print(f"error: {error}")
        return 2


def real_agent_mode_label(
    cases: int, *, manifest_version: int, case_version: int
) -> str:
    """The harness, the inventory it was read from, and its semantics version."""
    return (
        f"Mode: {REAL_AGENT_MODE} ({cases} cases from replay manifest "
        f"v{manifest_version}, case semantics v{case_version})"
    )


def real_agent_suite_lines(suite: ReplaySuiteResult) -> list[str]:
    """The real-agent suite's own output, one line each.

    The two header lines come first and are not optional: a per-case line
    reading "passed" is a claim about the five production agents, and a
    reader who was not told which harness ran cannot tell that claim from its
    opposite.
    """
    lines = [
        real_agent_mode_label(
            len(suite.cases),
            manifest_version=suite.manifest_version,
            case_version=suite.case_version,
        ),
        AGENTS_PRODUCTION,
    ]
    lines += [
        (
            f"{case.case_id}: {'passed' if case.passed else 'failed'} "
            f"({len(case.repetitions)} repetitions, "
            f"{'deterministic' if case.deterministic else 'NON-deterministic'})"
        )
        for case in suite.cases
    ]
    accepted = sum(1 for case in suite.cases if case.passed)
    lines.append(
        f"Suite: {'accepted' if suite.accepted else 'failed'} "
        f"({suite.repetitions} repetitions per case, "
        f"{accepted}/{len(suite.cases)} rows)"
    )
    lines.append(f"Artifact: {suite.artifact_path}")
    lines.append(network_line(suite))
    return lines


def network_line(suite: ReplaySuiteResult) -> str:
    """What the socket guard recorded, as the run's own evidence.

    Read from the repetitions rather than from the run's configuration: the
    number is a count of connections the product tried to open, so a suite
    that reached the network cannot print the zero that would have made it
    look acceptable.
    """
    attempts = sum(
        len(item.network_attempts)
        for case in suite.cases
        for item in case.repetitions
    )
    if attempts:
        return (
            f"Network: NOT zero (socket layer denied; "
            f"{attempts} attempts recorded)"
        )
    return "Network: zero (socket layer denied; 0 attempts recorded)"


__all__ = [
    "CONTROLLED_REPETITIONS",
    "DEFAULT_OUTPUT_DIRECTORY",
    "LIVE_TIER_NOT_RUN",
    "REAL_AGENT_MODE",
    "REPLAY_SUITE_FILENAME",
    "build_parser",
    "canonical_report_fingerprint",
    "main",
    "network_line",
    "real_agent_suite_lines",
    "run_replay_suite",
]
