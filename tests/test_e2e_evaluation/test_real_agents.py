"""The release proof: the real agents and CLI against adversarial evidence.

Every case here runs the six *production* agents through the real graph, the
real reviewer, renderer, publisher and ``deep_research.cli.main`` entrypoint.
The only scripted things are the external boundaries - the chat provider, the
search client, the HTTP client and long-term memory's vector store - and the
socket layer is denied for the whole run, so a case that reached the network
fails rather than passing quietly.

The inventory is the versioned manifest, not a hardcoded count: a case that is
added, removed or re-versioned changes this file's parametrization without
editing it.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

from deep_research.e2e_evaluation.replay import (
    CaseExpectation,
    expectation_failures,
    network_denied,
    run_replay_scenario,
)
from deep_research.e2e_evaluation.replay_matrix import (
    REPLAY_CASE_IDS,
    REPLAY_CASE_MANIFEST,
    REPLAY_CASE_MANIFEST_VERSION,
    manifest_entry,
    scenario_by_id,
)

# The plan's declared inventory. Named here as the *expected* set so a manifest
# that silently loses a row fails this test rather than shrinking the proof.
DECLARED_CASE_IDS: tuple[str, ...] = (
    "broad-constraints",
    "comparative-conflict",
    "refinement-evidence-recovery",
    "blocked-html-pdf-fallback",
    "same-work-mirror",
    "semantic-duplicate-claims",
    "stalled-refinement",
    "primary-attribution",
    "current-versus-forecast",
    "unsupported-mechanism",
    "judge-failure",
    "non-constraint-answer",
    "late-contradiction",
    "empty-but-clean",
    "memory-is-not-read",
    "validated-cache-reuse",
    "decision-context-late-candidate",
    "reopen-unanswered-target",
)

REPETITIONS = 3


def _run(case_id: str, *, repetition: int, root: Path):
    scenario = scenario_by_id(case_id)
    with network_denied() as attempts:
        run = run_replay_scenario(scenario, root=root, repetition=repetition)
    return run, attempts


_REFERENCE = re.compile(r"^(\d+)\. (.*)$")
_CITATION = re.compile(r"\[(\d+)\]")
_CITATION_RUN = re.compile(r"(?:\[\d+\]){2,}")


def _stable_report(report: str) -> str:
    """The published report without the two facts about the session that made it.

    ``As of`` is the moment the pass ran, and the numbering of the reference
    list is the order this session's reads were recorded in: read identity is
    scoped to a session by the product's own contract (``build_read_id``), so
    two runs of one fixture cite the same sources numbered in whichever order
    their own reads landed. So the report is canonicalized by what a reader
    would take from it: references renumbered by URL, every citation rewritten
    to the canonical number. What is compared is which sources the reader was
    shown against which sentences - not the numbering a session assigned. A
    citation set that gained, lost or moved a source still differs here.
    """
    body: list[str] = []
    references: list[tuple[str, str]] = []
    for line in report.splitlines():
        if line.startswith("**As of:**"):
            continue
        match = _REFERENCE.match(line) if references or line[:1].isdigit() else None
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
        return "".join(
            f"[{marker}]" for marker in sorted(markers, key=int)
        )

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
        (
            f"{canonical[number]}. {label}"
            for number, label in references
        ),
        key=lambda line: int(line.split(".", 1)[0]),
    )
    return "\n".join(rewritten + listing)


# --- the harness itself ------------------------------------------------------


def test_matrix_declares_every_case_the_plan_names() -> None:
    """The manifest is the inventory: every declared row, and no invented one."""
    assert REPLAY_CASE_MANIFEST_VERSION >= 1
    declared = set(DECLARED_CASE_IDS)
    listed = set(REPLAY_CASE_IDS)
    assert listed == declared, {
        "missing from the manifest": sorted(declared - listed),
        "not in the plan's inventory": sorted(listed - declared),
    }
    assert len(REPLAY_CASE_IDS) == len(set(REPLAY_CASE_IDS))
    for entry in REPLAY_CASE_MANIFEST:
        assert entry.version >= 1, entry.case_id
        assert entry.expected_product_result, entry.case_id
        assert entry.decisive_assertion, entry.case_id


def test_every_checker_is_asserted_by_a_case() -> None:
    """A checker no case declares is an assertion nothing makes.

    The registry is the matrix's whole vocabulary of properties, and a name
    that sits in it unwatched is worse than a missing one: it reads as
    coverage while asserting nothing. Both directions are checked, because a
    case naming a checker that does not exist is the same gap from the other
    side.
    """
    from deep_research.e2e_evaluation.replay import _REPLAY_INVARIANTS

    declared: set[str] = set()
    for case_id in REPLAY_CASE_IDS:
        declared.update(scenario_by_id(case_id).expectation.required_invariants)
    registered = set(_REPLAY_INVARIANTS)
    assert registered == declared, {
        "registered but no case declares it": sorted(registered - declared),
        "declared but not registered": sorted(declared - registered),
    }
    # And a registered checker is a callable the run is judged by, not a name
    # that happens to be spelled the same in two places.
    for name, checker in _REPLAY_INVARIANTS.items():
        assert callable(checker), name


def test_case_runs_the_production_agents_not_a_double() -> None:
    """The runtime under test holds the shipped agent classes, class for class."""
    from deep_research.agents.critic import CriticAgent
    from deep_research.agents.fact_checker import FactCheckerAgent
    from deep_research.agents.planner import PlannerAgent
    from deep_research.agents.researcher import ResearcherAgent
    from deep_research.agents.source_evaluator import SourceEvaluatorAgent
    from deep_research.agents.synthesizer import SynthesizerAgent

    expected = {
        "planner": PlannerAgent,
        "researcher": ResearcherAgent,
        "source_evaluator": SourceEvaluatorAgent,
        "fact_checker": FactCheckerAgent,
        "synthesizer": SynthesizerAgent,
        "critic": CriticAgent,
    }
    with tempfile.TemporaryDirectory() as directory:
        run, attempts = _run(
            "primary-attribution", repetition=1, root=Path(directory)
        )
    assert attempts == []

    agents = run.replay.agents
    for name, agent_class in expected.items():
        assert type(getattr(agents, name)) is agent_class, name
    # The terminal reviewer is wired, and the terminal writer resolves to the
    # production Synthesizer rather than to no writer at all: a graph with
    # neither would publish nothing and record an unreviewed report.
    from deep_research.graph.orchestrator import terminal_publisher

    assert type(agents.report_reviewer).__name__ == "ReportReviewer"
    assert terminal_publisher(agents) is agents.synthesizer

    # And nothing anywhere in the run is the scripted graph double the
    # historical regression cases use.
    from deep_research.e2e_evaluation.cases import ScriptedGraphAgent

    for name in expected:
        assert not isinstance(getattr(agents, name), ScriptedGraphAgent)


# --- the declared result and the test's own verdict are different facts ------


class _StubRun:
    """A run-shaped object whose only job is to be judged by the expectation."""

    def __init__(
        self,
        *,
        expectation: CaseExpectation,
        exit_code: int,
        quality_status: str,
        report: str,
        answered: tuple[str, ...],
        claims: tuple[object, ...],
        gaps: tuple[str, ...] = (),
    ) -> None:
        self.exit_code = exit_code
        self.quality_status = quality_status
        self.report = report
        self._answered = answered
        self._gaps = gaps
        self.scenario = type("Scenario", (), {"expectation": expectation})()
        self.state = type("State", (), {"verified_claims": claims})()

    def answered_target_ids(self) -> list[str]:
        return list(self._answered)

    def answerable_claims(self) -> list[object]:
        return [
            claim
            for claim in self.state.verified_claims
            if getattr(claim, "target_ids", ())
        ]

    def gap_kinds(self) -> tuple[str, ...]:
        return self._gaps


def test_vacuous_abstention_cannot_pass_a_positive_case() -> None:
    """A clean exit with no useful claim is a failed case, not a passed one."""
    expectation = CaseExpectation(
        terminal_quality="accepted",
        exit_code=0,
        required_target_ids=("topic-01-target-01",),
        minimum_answerable_claims=1,
    )
    failures = expectation_failures(
        _StubRun(
            expectation=expectation,
            exit_code=0,
            quality_status="accepted",
            report="The question cannot be answered from the evidence gathered.",
            answered=(),
            claims=(),
        )
    )
    assert failures, "a run with no claims and no answers passed a positive case"
    assert any("target" in failure for failure in failures)
    assert any("claim" in failure for failure in failures)


def test_negative_case_can_pass_without_product_acceptance() -> None:
    """The contract shape: a partial product result is an expectation met."""
    expectation = CaseExpectation(
        terminal_quality="partial",
        exit_code=4,
        # One obligation the case requires answered, and a gap it requires
        # recorded: the target that stays outstanding is named by the gap, not
        # by the required set, because a partial case that named it as required
        # would be asserting its own failure.
        required_target_ids=("topic-01-target-01",),
        required_gap_kinds=("unanswered_target",),
        minimum_answerable_claims=1,
    )
    assert expectation.terminal_quality == "partial"
    assert expectation.exit_code == 4
    assert expectation.required_gap_kinds == ("unanswered_target",)

    class _Claim:
        verdict = "verified"
        target_ids = ["topic-01-target-01"]

    failures = expectation_failures(
        _StubRun(
            expectation=expectation,
            exit_code=4,
            quality_status="partial",
            report="One obligation remains outstanding.",
            answered=("topic-01-target-01",),
            claims=(_Claim(),),
            gaps=("unanswered_target",),
        )
    )
    assert failures == []


# --- the matrix --------------------------------------------------------------


@pytest.mark.parametrize("case_id", REPLAY_CASE_IDS)
def test_matrix_case_holds_for_three_offline_repetitions(case_id: str) -> None:
    """One declared case, run three times, network-denied.

    Three repetitions verify deterministic *order, identity and isolation* -
    the same declarations produce the same result with nothing carried from
    one repetition into the next. They are not a claim about model
    reliability: every provider response here is scripted.
    """
    entry = manifest_entry(case_id)
    outcomes: list[tuple[int, str, tuple[str, ...], str, tuple[str, ...]]] = []
    sessions: list[str] = []
    with tempfile.TemporaryDirectory() as directory:
        for repetition in range(1, REPETITIONS + 1):
            run, attempts = _run(
                case_id,
                repetition=repetition,
                root=Path(directory) / f"repetition-{repetition}",
            )
            assert attempts == [], f"{case_id}: network attempted {attempts}"
            assert run.graph_run.state is run.state
            assert run.state.session_id == run.session_id
            sessions.append(run.session_id)
            failures = expectation_failures(run)
            assert failures == [], f"{case_id} r{repetition}: {failures}"
            outcomes.append(
                (
                    run.exit_code,
                    run.quality_status,
                    tuple(sorted(run.answered_target_ids())),
                    _stable_report(run.report),
                    tuple(
                        claim.claim_id for claim in run.state.verified_claims
                    ),
                )
            )
    assert entry.expected_product_result
    # Same result every time, produced in isolation: each repetition ran in
    # its own storage root under its own session, so a claim identity or an
    # answer that leaked across runs would differ here.
    assert len(outcomes) == REPETITIONS
    assert len(set(sessions)) == REPETITIONS
    assert len(set(outcomes)) == 1, {
        "repetitions disagree": outcomes,
    }
    assert outcomes[0][3], f"{case_id}: the run published no report"
