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

import json
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path

import pytest

from deep_research.agents.evidence import normalized_content_sha256
from deep_research.e2e_evaluation.replay import (
    OBSERVATION_SUMMARY_CHARS,
    CaseExpectation,
    ReplayRun,
    ReplayScenario,
    ReplaySource,
    ReplayTopic,
    expectation_failures,
    network_denied,
    run_replay_scenario,
)
from deep_research.e2e_evaluation.replay_matrix import (
    REPLAY_CASE_IDS,
    REPLAY_CASE_MANIFEST,
    REPLAY_CASE_MANIFEST_VERSION,
    ReplayCaseEntry,
    manifest_entry,
    scenario_by_id,
)

# The plan's declared inventory. Named here as the *expected* set so a manifest
# that silently loses a row fails this test rather than shrinking the proof.
DECLARED_CASE_IDS: tuple[str, ...] = (
    "broad-constraints",
    "comparative-conflict",
    "extra-pass-recovers-missing-target",
    "blocked-html-pdf-fallback",
    "same-work-mirror",
    "extra-pass-finds-nothing",
    "report-relay-labelled-as-relay",
    "forecast-versus-actual-kept-apart",
    "unsupported-mechanism",
    "review-unavailable",
    "non-constraint-answer",
    "empty-but-clean",
    "memory-is-not-read",
    "validated-cache-reuse",
    "decision-context-late-candidate",
    "missing-target-triggers-one-extra-pass",
    "figure-not-on-page-dropped",
    "evidence-words-not-on-page-rejected",
    "report-scope-corrected-to-all-segments",
    "revision-noted",
    "statement-check-failure-keeps-sentences",
    "two-subjects-one-value",
    "two-versions-one-target",
    "single-subject-spellings",
    "prose-only-question",
    "count-unit-period",
    "purchase-year-empty-period",
    "relative-period-resolved",
    "unattributed-relay-prose",
    "one-part-question",
    "maker-notes-vs-relay",
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


def test_the_manifest_ids_are_unique_and_have_a_scenario() -> None:
    """Every declared row is a real-agent row: one id, one builder, no gap."""
    for entry in REPLAY_CASE_MANIFEST:
        assert entry.build is not None, entry.case_id
        assert scenario_by_id(entry.case_id).case_id == entry.case_id
    assert len({entry.case_id for entry in REPLAY_CASE_MANIFEST}) == len(
        REPLAY_CASE_MANIFEST
    )


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


def test_mirror_checker_reads_the_fact_rows_its_composition_recorded() -> None:
    """One body served twice is one fact row, however many hosts serve it.

    PD-9 merges figures by field key, so the checker reads the composition's
    own fact rows rather than the reads themselves: a badge that cites both
    the running copy and its mirror is a mirror counted twice, and a badge
    that cites the mirrored pair once and a genuine second account once is
    real corroboration.
    """
    from deep_research.agents.identity import finding_fingerprint
    from deep_research.e2e_evaluation.replay import (
        _invariant_mirror_not_double_counted,
    )
    from deep_research.utils.types import FactRow, ReportComposition
    from tests.evidence_fakes import make_finding, make_read

    body = "the Acme widget adoption rate was 40 percent in 2024"
    bureau_text = (
        "An independent panel found the Acme widget adoption rate was 40 "
        "percent in 2024."
    )
    running = make_read(
        body, url="https://agency.test/report.pdf", title="Adoption report"
    )
    mirror = make_read(
        body,
        url="https://mirror.test/report.pdf",
        title="Adoption report (mirror)",
    )
    bureau = make_read(
        bureau_text, url="https://bureau.test/panel.pdf", title="Adoption panel"
    )
    texts = {
        running.resolved_url: body,
        mirror.resolved_url: body,
        bureau.resolved_url: bureau_text,
    }

    def _run(*, reads: tuple, cited: tuple[str, ...]) -> object:
        findings = {
            read.resolved_url: make_finding(
                read, texts[read.resolved_url]
            )
            for read in reads
        }
        fact_rows = [
            FactRow(
                row_id=f"K{index:03d}",
                organisation="Acme Institute",
                attribution="own",
                measure="adoption rate",
                value="40 percent",
                kind="actual",
                finding_id=finding_fingerprint(findings[url]),
            )
            for index, url in enumerate(cited, start=1)
        ]
        composition = ReportComposition(
            question="What was the Acme widget adoption rate in 2024?",
            session_id="mirror-probe",
            findings=list(findings.values()),
            fact_rows=fact_rows,
        )
        state = type(
            "State",
            (),
            {
                "read_records": {read.read_id: read for read in reads},
                "composition": composition,
            },
        )()
        return type("Run", (), {"state": state})()

    copied = _invariant_mirror_not_double_counted(
        _run(
            reads=(running, mirror),
            cited=(running.resolved_url, mirror.resolved_url),
        )
    )
    assert copied is not None, "a fact row resting on one body twice passed"
    assert "one body" in copied

    corroborated = _invariant_mirror_not_double_counted(
        _run(
            reads=(running, mirror, bureau),
            cited=(running.resolved_url, bureau.resolved_url),
        )
    )
    assert corroborated is None, corroborated


def _cache_probe(declared: str) -> tuple[ReplayScenario, object]:
    """A one-page scenario carrying a stored artifact, and that artifact.

    The page is authored the way every case authors one, and the artifact is
    what the harness would seed for it: a valid one stores the page's own
    text, a forged one stores a body and declares the digest of this one.
    """
    from deep_research.e2e_evaluation.replay import prior_session_reads

    claim = "the Acme widget adoption rate was 40 percent in 2024"
    page = ReplaySource(
        url="https://bureau.test/adoption-panel",
        title="Adoption panel",
        text=f"Adoption panel. Published by Independent Bureau 11. {claim}.",
        excerpt=claim,
        claim=claim,
        issuer="Independent Bureau 11",
        cache_artifact=declared,
        cached_text="Panel notes. Published by Independent Bureau 11." * 3
        if declared == "forged"
        else "",
    )
    scenario = ReplayScenario(
        case_id="cache-provenance-probe",
        question="What was the Acme widget adoption rate in 2024?",
        topics=(
            ReplayTopic(
                title="Adoption rate",
                question="What was the Acme widget adoption rate in 2024?",
                measure="the Acme widget adoption rate",
                unit_dimension="percent",
                period="2024",
                kind="actual",
                query="Acme widget adoption rate 2024",
                sources=(page,),
            ),
        ),
        expectation=CaseExpectation(terminal_quality="accepted", exit_code=0),
    )
    return scenario, prior_session_reads(scenario)[page.url]


def _cache_run(
    scenario: ReplayScenario,
    *,
    kept: object | None,
    fetched: bool,
) -> object:
    """A run handed ``scenario``'s stored artifact, and what it kept for it."""
    from deep_research.e2e_evaluation.replay import prior_session_reads

    url = next(iter(scenario.sources))
    http = type(
        "HTTP", (), {"fetched": [url] if fetched else [], "requests": []}
    )()
    state = type(
        "State",
        (),
        {"read_records": {} if kept is None else {kept.read_id: kept}},
    )()
    return type(
        "Run",
        (),
        {
            "state": state,
            "scenario": scenario,
            "session_id": "cache-probe-run",
            "replay": type(
                "Replay",
                (),
                {"seeded_reads": prior_session_reads(scenario), "http": http},
            )(),
        },
    )()


def test_cache_provenance_checker_holds_each_artifact_to_what_it_claims() -> None:
    """The checker fails the four ways a stored body can be misused.

    It is the row's decisive assertion, so it cannot be a checker that only
    ever passes: a validated import has to be the run's record, that record
    has to keep the reading session and the local stamp, a stored body has to
    be reused rather than downloaded again, and a forgery has to end in a
    fetch rather than in the record it claims.
    """
    from deep_research.agents.evidence import build_read_record
    from deep_research.e2e_evaluation.replay import (
        _invariant_cache_provenance_is_validated,
    )

    scenario, artifact = _cache_probe("valid")
    url = artifact.resolved_url

    def imported(**updates: object) -> object:
        return artifact.model_copy(
            update={
                "acquisition_kind": "cache",
                "version_validated_at": "2026-09-22T00:00:00+00:00",
                **updates,
            }
        )

    assert (
        _invariant_cache_provenance_is_validated(
            _cache_run(scenario, kept=imported(), fetched=False)
        )
        is None
    )
    assert "downloaded again" in (
        _invariant_cache_provenance_is_validated(
            _cache_run(scenario, kept=imported(), fetched=True)
        )
        or ""
    )
    assert "filed as" in (
        _invariant_cache_provenance_is_validated(
            _cache_run(scenario, kept=artifact, fetched=False)
        )
        or ""
    )
    assert "lost the session" in (
        _invariant_cache_provenance_is_validated(
            _cache_run(
                scenario,
                kept=imported(origin_session_id="some-other-session"),
                fetched=False,
            )
        )
        or ""
    )

    forged_scenario, forged = _cache_probe("forged")
    assert forged.content_sha256 != normalized_content_sha256(
        "".join(forged.passages.values())
    ), "a forged record that validates itself is not forged"
    assert "did not produce a fetch" in (
        _invariant_cache_provenance_is_validated(
            _cache_run(forged_scenario, kept=imported(), fetched=False)
        )
        or ""
    )
    own_text = (
        "Adoption panel. Published by Independent Bureau 11. The rate was "
        "40 percent."
    )
    own_read = build_read_record(
        session_id="cache-probe-run",
        reader="web_scraper",
        requested_url=url,
        resolved_url=url,
        title="Adoption panel",
        retrieved_at="2026-09-22T00:00:00+00:00",
        text=own_text,
        passages={"chunk-0": own_text},
    )
    assert (
        _invariant_cache_provenance_is_validated(
            _cache_run(forged_scenario, kept=own_read, fetched=True)
        )
        is None
    )


def test_case_runs_the_production_agents_not_a_double() -> None:
    """The runtime under test holds the shipped agent classes, class for class."""
    from deep_research.agents.evidence_verifier import EvidenceVerifierAgent
    from deep_research.agents.planner import PlannerAgent
    from deep_research.agents.report_writer import ReportWriterAgent
    from deep_research.agents.researcher import ResearcherAgent
    from deep_research.agents.source_evaluator import SourceEvaluatorAgent

    expected = {
        "planner": PlannerAgent,
        "researcher": ResearcherAgent,
        "source_evaluator": SourceEvaluatorAgent,
        "evidence_verifier": EvidenceVerifierAgent,
        "report_writer": ReportWriterAgent,
    }
    with tempfile.TemporaryDirectory() as directory:
        run, attempts = _run(
            "broad-constraints", repetition=1, root=Path(directory)
        )
    assert attempts == []

    agents = run.replay.agents
    for name, agent_class in expected.items():
        assert type(getattr(agents, name)) is agent_class, name
    # The terminal reviewer is wired, and the terminal writer resolves to the
    # production Report Writer rather than to no writer at all: a graph with
    # neither would publish nothing and record an unreviewed report. Each
    # check above is exact-class identity (``is``, not ``isinstance``), which
    # already rules out any double, scripted or otherwise.
    from deep_research.graph.orchestrator import terminal_publisher

    assert type(agents.report_reviewer).__name__ == "ReportReviewer"
    assert terminal_publisher(agents) is agents.report_writer


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
        gaps: tuple[str, ...] = (),
    ) -> None:
        self.exit_code = exit_code
        self.quality_status = quality_status
        self.report = report
        self._answered = answered
        self._gaps = gaps
        self.scenario = type("Scenario", (), {"expectation": expectation})()

    def answered_target_ids(self) -> list[str]:
        return list(self._answered)

    def gap_kinds(self) -> tuple[str, ...]:
        return self._gaps


def test_a_clean_exit_with_nothing_answered_cannot_pass_a_positive_case() -> None:
    """A clean exit that answered no required target is a failed case.

    There is no separate "useful claim" count any more (D2: no corroboration
    step, so nothing pools claims for adjudication): a positive case's own
    ``required_target_ids`` is what a vacuous abstention fails.
    """
    expectation = CaseExpectation(
        terminal_quality="accepted",
        exit_code=0,
        required_target_ids=("topic-01-target-01",),
    )
    failures = expectation_failures(
        _StubRun(
            expectation=expectation,
            exit_code=0,
            quality_status="accepted",
            report="The question cannot be answered from the evidence gathered.",
            answered=(),
        )
    )
    assert failures, "a run that answered nothing passed a positive case"
    assert any("target" in failure for failure in failures)


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
    )
    assert expectation.terminal_quality == "partial"
    assert expectation.exit_code == 4
    assert expectation.required_gap_kinds == ("unanswered_target",)

    failures = expectation_failures(
        _StubRun(
            expectation=expectation,
            exit_code=4,
            quality_status="partial",
            report="One obligation remains outstanding.",
            answered=("topic-01-target-01",),
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
                        row.row_id
                        for row in (
                            run.state.composition.fact_rows
                            if run.state.composition is not None
                            else []
                        )
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


# --- the named mutations: a check that cannot fail is not a check -------------
#
# The brief names four mutations, and a mutation test is the only thing that
# tells a matrix row apart from a decoration: the row's own green says the
# property holds, and running the *same* row against the forbidden shape says
# the row's assertion is what holds it. Each mutation below is a drop-in for
# one real production function, active only for that test, and each is scoped
# to the behaviour the row asserts - never to the fixture that would fail
# whatever the product did.


def _repetitions_under_the_mutation(
    case_id: str, root: Path
) -> list[tuple[ReplayRun, list[str]]]:
    """One case's three repetitions, and how each fell short, mutation active.

    The same harness ``test_matrix_case_holds_for_three_offline_repetitions``
    runs: the declared case, the same storage isolation, the network denied.
    The only difference is the forbidden shape the caller has patched in, so a
    repetition that passed here is a repetition the mutation did not reach.
    """
    outcomes: list[tuple[ReplayRun, list[str]]] = []
    for repetition in range(1, REPETITIONS + 1):
        run, attempts = _run(
            case_id,
            repetition=repetition,
            root=root / f"repetition-{repetition}",
        )
        assert attempts == [], f"{case_id}: network attempted {attempts}"
        outcomes.append((run, expectation_failures(run)))
    return outcomes


def _memory_recall_admitted_as_read(real):
    """The pre-fix ``query_memory``-as-read shape, as an ``AcquisitionPolicy`` hook.

    A recall is a lead: the policy queues the remembered URL as a candidate and
    owes an original-source read before anything may rest on it. The mutation
    restores the behaviour Task 1 removed - a matched entry's ``source_url``
    counted as content this session had *read* - by handing each match's own
    text to the real read-admission path, so the read record it produces is the
    product's own and not a fixture's.
    """

    def recalled(result: object):
        data = result.data if isinstance(result.data, Mapping) else None
        matches = data.get("matches") if data is not None else None
        for match in matches if isinstance(matches, list) else []:
            if not isinstance(match, Mapping):
                continue
            content = match.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            metadata = match.get("metadata")
            metadata_map = metadata if isinstance(metadata, Mapping) else {}
            url = match.get("source_url") or metadata_map.get("source_url")
            if isinstance(url, str) and url.strip():
                yield url, content, str(match.get("title") or url)

    def observed(policy, result):
        real(policy, result)
        if not result.success:
            return
        for url, content, title in recalled(result):
            policy._read_observed(
                result.model_copy(
                    update={
                        "tool_name": "web_scraper",
                        "data": {"url": url, "text": content, "title": title},
                    }
                ),
                {"url": url},
            )

    return observed


def _manifest_from_the_public_summary(real):
    """The pre-fix prefix-only decision context, as a ``build_acquisition_context``.

    Before Task 3 the next-decision packet carried no acquisition context at
    all: the model saw the public observation summary - every search and read
    payload clamped to ``observation_summary_chars`` - and the decision was made
    from that prefix. The mutation restores that shape for the candidate
    manifest the row is about: the queued candidates are re-derived from the
    prefix of their own serialization the public summary length allows, so a
    candidate that fell past the clamp is a candidate no later request names.
    """
    from deep_research.agents.steps import summarize_text

    urls_in_line = re.compile(r"https?://\S+")

    def context(state, reads, evidence, *, limit, target_id=None, dispositions=()):
        text = real(
            state,
            reads,
            evidence,
            limit=limit,
            target_id=target_id,
            dispositions=dispositions,
        )
        carried = summarize_text(
            json.dumps(
                [
                    {"title": record.title, "url": record.url}
                    for record in state.candidate_records.values()
                ]
            ),
            limit=OBSERVATION_SUMMARY_CHARS,
        )
        kept: list[str] = []
        for line in text.splitlines():
            manifest = line.startswith(
                ("- candidate_id=", "- candidate_urls=")
            )
            if manifest and any(
                url not in carried for url in urls_in_line.findall(line)
            ):
                continue
            kept.append(line)
        return "\n".join(kept)

    return context


def test_restoring_query_memory_as_read_breaks_the_memory_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recalled entry is a lead, so admitting it as a read fails the row.

    ``memory-is-not-read`` is the row whose decisive assertion is that two
    remembered generated claims with different URLs establish neither a read nor
    independent support. The mutation restores the forbidden shape at the seam
    that decides it in this path - the acquisition policy's own admission of a
    recall - and the row's invariant names the fabrication: the remembered lead
    is recorded as a read of this run. The lead still reaches a decision packet
    under the mutation, so what fails is the read, not the recall.
    """
    from deep_research.agents.acquisition import AcquisitionPolicy

    monkeypatch.setattr(
        AcquisitionPolicy,
        "_memory_observed",
        _memory_recall_admitted_as_read(AcquisitionPolicy._memory_observed),
    )
    with tempfile.TemporaryDirectory() as directory:
        outcomes = _repetitions_under_the_mutation(
            "memory-is-not-read", Path(directory)
        )

    for run, failures in outcomes:
        leads = [
            entry.source_url
            for entry in run.scenario.memory_entries
            if entry.source_url
        ]
        assert leads, "the case seeded no remembered lead"
        recorded = " ".join(
            f"{read.requested_url} {read.resolved_url}"
            for read in run.state.read_records.values()
        )
        for lead in leads:
            assert lead in recorded, (
                f"the mutation did not admit the lead {lead} as a read"
            )
        # The invariant stops at the first lead it finds recorded as a read, so
        # the failure names one of them rather than each.
        assert any(
            "invariant 'memory_leads_are_not_reads' broken" in failure
            and "was recorded as a read of this run" in failure
            and any(lead in failure for lead in leads)
            for failure in failures
        ), failures


def test_a_prefix_only_decision_context_breaks_the_late_candidate_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The late candidate is the row's subject, so a prefix-only packet fails it.

    ``decision-context-late-candidate`` is a positive row whose decisive
    assertion is that the last result of a search and its document section reach
    the requests that decide and extract from it, despite the 200-character
    public summaries. The mutation makes the acquisition context fall back to
    what those summaries could carry, and the row's invariant names the loss:
    the candidate reached no request, so no later pass could ever read the
    figure the question asked for.
    """
    from deep_research.agents import acquisition

    monkeypatch.setattr(
        acquisition,
        "build_acquisition_context",
        _manifest_from_the_public_summary(acquisition.build_acquisition_context),
    )
    with tempfile.TemporaryDirectory() as directory:
        outcomes = _repetitions_under_the_mutation(
            "decision-context-late-candidate", Path(directory)
        )

    for run, failures in outcomes:
        late = run.scenario.topics[0].sources[-1]
        assert any(
            "invariant 'late_candidate_reached_decision' broken: the candidate "
            f"{late.url} reached 0 request(s), so a later pass never saw it again"
            in failure
            for failure in failures
        ), failures
        assert run.report, "the case failed without publishing anything"
