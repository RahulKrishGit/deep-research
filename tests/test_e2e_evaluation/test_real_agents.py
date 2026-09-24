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
    ReplayCompleter,
    ReplayRun,
    ReplayScenario,
    ReplaySource,
    ReplayTopic,
    expectation_failures,
    network_denied,
    run_replay_scenario,
)
from deep_research.e2e_evaluation.replay_matrix import (
    GRAPH_ONLY_HISTORICAL_MANIFEST,
    REPLAY_CASE_IDS,
    REPLAY_CASE_MANIFEST,
    REPLAY_CASE_MANIFEST_VERSION,
    ReplayCaseEntry,
    controlled_suite_inventory,
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


def _never_built() -> ReplayScenario:
    raise AssertionError("a rejected row must never be asked for its scenario")


def _probe_entry(
    *, build: object, graph_only_historical: bool
) -> ReplayCaseEntry:
    return ReplayCaseEntry(
        case_id="probe-row",
        version=1,
        title="probe",
        expected_product_result="accepted / 0",
        decisive_assertion="probe",
        build=build,  # type: ignore[arg-type]
        graph_only_historical=graph_only_historical,
    )


def test_a_graph_only_historical_row_cannot_contradict_its_own_builder() -> None:
    """The flag and the builder are one fact, and neither may be inert.

    ``graph_only_historical`` was assigned in ``__init__`` and read nowhere: a
    row could carry it, a row could drop it, and no reader could tell. The flag
    exists because a historical row has no scenario to build, so "no builder"
    and "graph-only historical" are two spellings of one fact, and the
    constructor refuses the pairings that would let them disagree — the
    historical row that quietly grew a builder, and the real-agent row that
    lost one and became unrunnable.
    """
    with pytest.raises(ValueError, match="graph_only_historical"):
        _probe_entry(build=None, graph_only_historical=False)
    with pytest.raises(ValueError, match="graph_only_historical"):
        _probe_entry(build=_never_built, graph_only_historical=True)

    historical = _probe_entry(build=None, graph_only_historical=True)
    assert historical.build is None
    assert historical.graph_only_historical is True
    real_agent = _probe_entry(build=_never_built, graph_only_historical=False)
    assert real_agent.build is _never_built
    assert real_agent.graph_only_historical is False


def test_the_controlled_inventory_is_the_matrix_plus_the_graph_only_rows() -> None:
    """One declared inventory per harness, and no row claimed by both.

    A controlled run names one of two harnesses, and each has its own declared
    inventory: the eighteen real-agent rows, and the scripted-double rows —
    the three whose product result was recorded when the only agents were the
    doubles, and the cases that declare counted obligations. The rows whose
    case ids are the *same strings* as matrix rows are the first three, so
    those ids are checked for collision rather than assumed distinct — a
    doubled id would let one harness's evidence answer for the other's. Which
    rows those are, and how many rows the inventory holds, is read from the
    manifest rather than restated here.
    """
    real_agent_ids = tuple(entry.case_id for entry in REPLAY_CASE_MANIFEST)
    historical_ids = tuple(
        entry.case_id for entry in GRAPH_ONLY_HISTORICAL_MANIFEST
    )
    combined = controlled_suite_inventory()

    assert combined == (*REPLAY_CASE_MANIFEST, *GRAPH_ONLY_HISTORICAL_MANIFEST)
    assert len({entry.case_id for entry in combined}) == len(combined)
    assert set(historical_ids).isdisjoint(real_agent_ids)
    # Every row of this half is a scripted-double row, and the ones whose base
    # id is also a matrix id are exactly the first three matrix rows.
    for entry in GRAPH_ONLY_HISTORICAL_MANIFEST:
        assert entry.graph_only_historical is True, entry.case_id
        assert entry.build is None, entry.case_id
        assert entry.version >= 1, entry.case_id
        assert entry.expected_product_result, entry.case_id
        assert entry.decisive_assertion, entry.case_id
    assert {
        entry.case_id
        for entry in GRAPH_ONLY_HISTORICAL_MANIFEST
        if entry.case_id.removesuffix("-graph") in real_agent_ids
    } == {f"{case_id}-graph" for case_id in real_agent_ids[:3]}


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


def test_claim_source_is_a_page_that_delivered_its_body() -> None:
    """A refused page never stated the claim, so it cannot carry its verdict.

    The landing page is declared first and answers 403: the run records the
    denial and reads the document behind it, and the verdict of the claim the
    documents state is the verdict the *readable* page carries. A provider
    that let the refusal decide would script the badge away for a fact two
    documents state, which is the case's subject rather than its premise.
    """
    claim = "the Acme widget adoption rate was 40 percent in 2024"
    scenario = ReplayScenario(
        case_id="claim-source-probe",
        question="What was the Acme widget adoption rate in 2024?",
        topics=(
            ReplayTopic(
                title="Adoption rate",
                question="What was the Acme widget adoption rate in 2024?",
                dimensions=("rate",),
                critical=True,
                query="Acme widget adoption rate 2024",
                sources=(
                    ReplaySource(
                        url="https://refused.test/report",
                        title="Adoption report landing page",
                        text=f"Adoption report landing page. {claim}.",
                        excerpt=claim,
                        claim=claim,
                        issuer="Acme Institute 4",
                        verdict="insufficient_evidence",
                        status_code=403,
                    ),
                    ReplaySource(
                        url="https://agency.test/report.pdf",
                        title="Adoption report",
                        text=f"Adoption report. {claim}.",
                        excerpt=claim,
                        claim=claim,
                        issuer="Acme Institute 4",
                        verdict="verified",
                    ),
                ),
            ),
        ),
        expectation=CaseExpectation(terminal_quality="accepted", exit_code=0),
    )
    completer = ReplayCompleter(scenario)

    source = completer.claim_source(f"Candidate passages\n{claim}\n")

    assert source.url == "https://agency.test/report.pdf"
    assert source.verdict == "verified"


def _claim_packet(claim: str, rows: tuple[tuple[str, ReplaySource], ...]) -> str:
    """One adjudication packet as the product renders one.

    The header states the claim under check, and each candidate is printed
    with its id, its page and the exact text of the read - the shape
    ``replay.py``'s own reply handler parses. Every candidate's own sentence is
    therefore *in* the packet text whether or not it has anything to say about
    the claim being judged.
    """
    blocks = [
        (
            f"- id: {evidence_id}\n"
            f"  source: {source.title} ({source.url})\n"
            f"  locator: chunk-0\n"
            f"  exact text: {source.text}"
        )
        for evidence_id, source in rows
    ]
    return (
        f"# Claim\n{claim}\n\n"
        "# Candidate passages (select by id)\n" + "\n".join(blocks) + "\n"
    )


def _note_then_record_scenario() -> tuple[ReplayScenario, ReplaySource, str]:
    """A topic whose first two pages state no value, and the record after them."""
    claim = (
        "the Acme widget adoption rate in the United States was 40 percent in 2024"
    )
    cover = ReplaySource(
        url="https://agency12.test/cover-note",
        title="Adoption cover note",
        text=(
            "Adoption cover note. Published by Acme Institute 12. The report "
            "states the Acme widget adoption rate cover note lists a title and "
            "a publication date and states no measured value."
        ),
        excerpt=(
            "the Acme widget adoption rate cover note lists a title and a "
            "publication date and states no measured value"
        ),
        claim=(
            "the Acme widget adoption rate cover note lists a title and a "
            "publication date and states no measured value"
        ),
        issuer="Acme Institute 12",
        verdict="insufficient_evidence",
    )
    method = ReplaySource(
        url="https://agency12.test/method-note",
        title="Adoption method note",
        text=(
            "Adoption method note. Published by Acme Institute 12. The report "
            "states the Acme widget adoption rate method note describes the "
            "method and states no measured value."
        ),
        excerpt=(
            "the Acme widget adoption rate method note describes the method "
            "and states no measured value"
        ),
        claim=(
            "the Acme widget adoption rate method note describes the method "
            "and states no measured value"
        ),
        issuer="Acme Institute 12",
        verdict="insufficient_evidence",
    )
    record = ReplaySource(
        url="https://agency12.test/adoption-2024",
        title="Adoption survey",
        text=(
            f"Adoption survey. Published by Acme Institute 12. The report "
            f"states {claim}."
        ),
        excerpt=claim,
        claim=claim,
        issuer="Acme Institute 12",
        verdict="verified",
    )
    scenario = ReplayScenario(
        case_id="claim-source-scope-probe",
        question=(
            "What was the Acme widget adoption rate in the United States in 2024?"
        ),
        topics=(
            ReplayTopic(
                title="Adoption rate",
                question=(
                    "What was the Acme widget adoption rate in the United "
                    "States in 2024?"
                ),
                dimensions=("rate",),
                critical=True,
                query="Acme widget adoption rate 2024",
                sources=(cover, method, record),
            ),
        ),
        expectation=CaseExpectation(terminal_quality="accepted", exit_code=0),
    )
    return scenario, record, claim


def test_claim_source_is_the_page_that_states_the_claim_under_check() -> None:
    """A page quoted for its own sake cannot carry another claim's verdict.

    The notes are read for the same sub-topic as the record, so a pool that
    selects by the sub-topic quotes them beside the pages that state the
    figure - and the packet carries each one's own sentence. The verdict of
    the claim being judged is the verdict of the page that states *that*
    claim: reading any candidate whose text the packet contains would let a
    page that states no value decide a figure.
    """
    scenario, record, claim = _note_then_record_scenario()
    completer = ReplayCompleter(scenario)
    cover, method = scenario.topics[0].sources[:2]

    source = completer.claim_source(
        _claim_packet(
            claim,
            (
                ("ev-1", cover),
                ("ev-2", method),
                ("ev-3", record),
            ),
        )
    )

    assert source.url == record.url
    assert source.verdict == "verified"


def test_claim_source_is_scoped_to_the_pages_the_packet_selected() -> None:
    """The packet's own page carries its verdict, not any page that states it.

    Both pages state the same claim, and this packet selected one of them: the
    verdict is the verdict of the page the pool chose for *this* packet. A
    search over the scenario's whole registry answers with the page that
    happens to be declared first.
    """
    scenario, record, claim = _note_then_record_scenario()
    completer = ReplayCompleter(scenario)
    other = ReplaySource(
        url="https://bureau12.test/adoption-2024",
        title="Adoption panel",
        text=(
            f"Adoption panel. Published by Independent Bureau 12. The report "
            f"states {claim}."
        ),
        excerpt=claim,
        claim=claim,
        issuer="Independent Bureau 12",
        verdict="verified",
    )
    scenario = ReplayScenario(
        case_id=scenario.case_id,
        question=scenario.question,
        topics=(
            ReplayTopic(
                title="Adoption rate",
                question=scenario.topics[0].question,
                dimensions=("rate",),
                critical=True,
                query="Acme widget adoption rate 2024",
                sources=(record, other),
            ),
        ),
        expectation=scenario.expectation,
    )
    completer = ReplayCompleter(scenario)

    source = completer.claim_source(
        _claim_packet(claim, (("ev-9", other),)),
    )

    assert source.url == other.url


def _mirror_run(
    *,
    supports: tuple[str, ...],
    references: tuple[str, ...],
) -> object:
    """A run with two hosts serving one body, judged by one filled-in checker.

    ``supports`` is the pages the claim's badge rests on and ``references`` the
    list the report published. Three reads are registered: the running copy,
    its mirror, and an account an independent publisher wrote. Each read has
    the assessed source the product records for it, identified by the
    product's own batch resolution — the identity the checker reads.
    """
    from deep_research.agents.evidence import resolve_source_identities
    from deep_research.utils.types import (
        Claim,
        EvidencePassage,
        ReadRecord,
        ScoredSource,
    )

    body = "the Acme widget adoption rate was 40 percent in 2024"
    reads = {
        "https://agency.test/report.pdf": "a" * 64,
        "https://mirror.test/report.pdf": "a" * 64,
        "https://bureau.test/panel.pdf": "b" * 64,
    }
    read_records = {
        f"read-{index}": ReadRecord(
            read_id=f"read-{index}",
            requested_url=url,
            resolved_url=url,
            title="Adoption report",
            reader="document_reader",
            retrieved_at="2024-12-31T00:00:00+00:00",
            content_sha256=digest,
            extraction_complete=True,
            passages={"page-1-chunk-0": body},
            origin_session_id="mirror-probe",
        )
        for index, (url, digest) in enumerate(reads.items())
    }
    claim = Claim(
        claim_id="claim-1",
        text=body,
        source_urls=list(reads),
        verdict="verified",
        confidence=0.85,
        evidence=[body],
        contradictions=[],
        verification_evidence=[
            EvidencePassage(
                source_url=url,
                source_title="Adoption report",
                locator="page-1-chunk-0",
                excerpt=body,
                stance="supports",
            )
            for url in supports
        ],
        cluster_id="cluster-1",
        evidence_status="verified_pair",
    )
    report = "\n".join(
        f"{number}. Adoption report — {url}"
        for number, url in enumerate(references, start=1)
    )

    evaluated_sources = resolve_source_identities(
        [
            ScoredSource(
                url=url,
                title="Adoption report",
                rationale="Recorded without a model judgement.",
                evaluation_status="unscored_missing",
            )
            for url in reads
        ],
        read_records.values(),
    )

    class _Run:
        state = type(
            "State",
            (),
            {
                "read_records": read_records,
                "verified_claims": [claim],
                "evaluated_sources": evaluated_sources,
            },
        )()
        statements: list[object] = []

    run = _Run()
    run.report = report
    return run


def test_mirror_checker_reads_the_references_the_reader_is_handed() -> None:
    """One work is one reference, and one body is not a second account.

    The badge half: a corroborated claim whose supporting reads are the two
    copies of one body is the badge the case exists to refuse, and a badge
    that also rested on a genuine second account is not. The reader half: the
    published reference list is what the reader was handed, so a list naming
    the running copy and its mirror is one work counted twice however the
    citation list behind it was assembled.
    """
    from deep_research.e2e_evaluation.replay import (
        _invariant_mirror_not_double_counted,
    )

    copied = _invariant_mirror_not_double_counted(
        _mirror_run(
            supports=(
                "https://agency.test/report.pdf",
                "https://mirror.test/report.pdf",
            ),
            references=("https://agency.test/report.pdf",),
        )
    )
    assert copied is not None, "a badge resting on one body twice passed"
    assert "one body" in copied

    corroborated = _invariant_mirror_not_double_counted(
        _mirror_run(
            supports=(
                "https://agency.test/report.pdf",
                "https://mirror.test/report.pdf",
                "https://bureau.test/panel.pdf",
            ),
            references=(
                "https://agency.test/report.pdf",
                "https://bureau.test/panel.pdf",
            ),
        )
    )
    assert corroborated is None, corroborated

    twice = _invariant_mirror_not_double_counted(
        _mirror_run(
            supports=(
                "https://agency.test/report.pdf",
                "https://bureau.test/panel.pdf",
            ),
            references=(
                "https://agency.test/report.pdf",
                "https://mirror.test/report.pdf",
                "https://bureau.test/panel.pdf",
            ),
        )
    )
    assert twice is not None, "one work reached the reader as two references"
    assert "reference" in twice


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
                dimensions=("rate",),
                critical=True,
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


def test_writer_discloses_a_claim_the_packet_badges_contradicted() -> None:
    """A row the run refused to settle is disclosed, not published as one.

    The synthesis packet tags every checked claim with the verdict the run's
    own adjudication gave it, so a row reading ``contradicted`` is a claim the
    run declined to settle. A scripted writer that published that row as an
    answer anyway is writing the false settlement the adversarial cases exist
    to catch, and it never makes the disclosure the reader is owed: it hands
    the composer no uncertainty at all. The settled row stays a point; the
    contradicted row becomes a note.
    """
    scenario = ReplayScenario(
        case_id="writer-badge-probe",
        question="What was the Acme widget adoption rate in 2024?",
        topics=(
            ReplayTopic(
                title="Adoption rate",
                question="What was the Acme widget adoption rate in 2024?",
                dimensions=("rate",),
                critical=True,
                query="Acme widget adoption rate 2024",
                sources=(),
            ),
        ),
        expectation=CaseExpectation(terminal_quality="accepted", exit_code=0),
    )
    completer = ReplayCompleter(scenario)

    draft = completer._reply_ReportDraft(
        "# Canonical evidence packet\n"
        "C001 [verified 0.85 | independently corroborated] the rate was 40 "
        "percent in 2024\n"
        "  cites: https://agency.test/report\n"
        "  coverage: topic-01\n"
        "C002 [contradicted 0.85 | contested] the rate was 30 percent in "
        "2024\n"
        "  cites: https://rival.test/report\n"
        "  coverage: topic-01\n"
    )

    settled = [
        point.text for section in draft.sections for point in section.points
    ]
    assert settled == ["the rate was 40 percent in 2024"]
    assert [row.finding for row in draft.answer_rows] == [
        "the rate was 40 percent in 2024"
    ]
    assert draft.uncertainty_notes, "the contradicted claim was never disclosed"
    assert any(
        "30 percent" in note for note in draft.uncertainty_notes
    ), draft.uncertainty_notes


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


def _omitting_b(real):
    """The brief's ``omit B`` mutation, as a drop-in for ``claim_evidence_pool``.

    ``claim_evidence_pool`` is the function that decides which registry units a
    claim is adjudicated against, and it is the only input the ``verified_pair``
    badge has: the badge is granted from the two independent accounts one claim
    row is adjudicated on. The mutation keeps the real pool's decision and then
    drops the last independent account in it, so B never reaches the packet -
    the shape the brief names, applied to whatever the case's pool happens to
    be rather than to a hardcoded id.
    """

    def pool(state, claim, *, target_ids=None):
        units = real(state, claim, target_ids=target_ids)
        accounts: dict[str, list[object]] = {}
        for unit in units:
            accounts.setdefault(unit.source_url, []).append(unit)
        if len(accounts) < 2:
            return units
        kept = {
            unit.evidence_id
            for url in list(accounts)[:-1]
            for unit in accounts[url]
        }
        return [unit for unit in units if unit.evidence_id in kept]

    return pool


_INVENTED_STATEMENT_URL = "https://invented.example.test/never-read"


def _inventing_a_statement_url(real):
    """The brief's invented-URL mutation, as a drop-in for ``statement_source_urls``.

    The real function derives a statement's citation URLs locally from the
    evidence registry, keyed by the selected ids, so a URL is whatever the
    recorded unit was actually served from. The mutation appends one URL that
    no read of the run produced: the citation the contract exists to make
    impossible.
    """

    def urls(evidence_ids, evidence):
        return [*real(evidence_ids, evidence), _INVENTED_STATEMENT_URL]

    return urls


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


def test_omitting_b_from_the_claim_pool_breaks_the_recovery_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B is what the recovery row's badge rests on, so omitting it fails the row.

    ``refinement-evidence-recovery`` is a positive row whose decisive assertion
    is that the *repair round's* late page is what turned the obligation into an
    answer, which its invariant reads off the badge: no corroborated claim
    resting on the page the repair acquired is a failed recovery. The pool is
    where the two accounts are joined, so a run that never showed the second
    account to the adjudication cannot reach the badge at all - and the case has
    to fail in exactly that way rather than falling over somewhere else.
    """
    from deep_research.agents import fact_checker

    monkeypatch.setattr(
        fact_checker,
        "claim_evidence_pool",
        _omitting_b(fact_checker.claim_evidence_pool),
    )
    with tempfile.TemporaryDirectory() as directory:
        outcomes = _repetitions_under_the_mutation(
            "refinement-evidence-recovery", Path(directory)
        )

    for run, failures in outcomes:
        assert failures, "the case passed with one of its two supports dropped"
        assert not [
            claim
            for claim in run.state.verified_claims
            if claim.evidence_status == "verified_pair"
        ], "a pair badge was granted from the pool the mutation narrowed"
        assert any(
            "invariant 'refinement_recovered_evidence' broken: "
            "no corroborated claim rested on the page the repair acquired"
            in failure
            for failure in failures
        ), failures
        assert run.report, "the case failed without publishing anything"


def test_an_invented_statement_url_breaks_a_positive_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A citation no read produced is refused, and the positive row records that.

    ``statement_source_urls`` derives every citation from the evidence registry
    so a URL the model typed can never reach the reader. The mutation appends
    one, and the case's own product contract is what catches it: the quality
    gate records the hard failure ``unscored_cited_sources`` because the reader
    was handed a source no assessment covers, so the run can no longer reach the
    ``accepted`` result its row declares.
    """
    from deep_research.agents import report

    monkeypatch.setattr(
        report,
        "statement_source_urls",
        _inventing_a_statement_url(report.statement_source_urls),
    )
    with tempfile.TemporaryDirectory() as directory:
        outcomes = _repetitions_under_the_mutation(
            "refinement-evidence-recovery", Path(directory)
        )

    for run, failures in outcomes:
        read_urls = {
            url
            for read in run.state.read_records.values()
            for url in (read.requested_url, read.resolved_url)
        }
        assert _INVENTED_STATEMENT_URL not in read_urls, (
            "the invented URL was a real read of this run"
        )
        assert _INVENTED_STATEMENT_URL in run.report, (
            "the reader was not handed the invented citation"
        )
        assert any(
            "unscored_cited_sources" in failure for failure in failures
        ), failures
        assert failures, "the case passed with an invented citation published"


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
