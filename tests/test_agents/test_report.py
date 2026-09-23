"""Tests for the two pure Markdown artifacts: reader report and ledger.

The reader report is what a decision-maker reads: a claim-linked summary, a
constraint ranking, findings whose every bullet ends in its own citation
markers, the uncertainty the pass recorded, a compact methodology note, and a
reference list holding only the sources the points actually cite. Everything
verbose — the checked-claim registry, the complete source assessment, the
verification passages, the rejected draft content, and the run's errors —
belongs to the evidence ledger instead.

Nothing here performs I/O, so both artifacts are asserted directly.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import get_args

import pytest

from deep_research.agents.evidence import build_read_record, resolve_read_works
from deep_research.agents.identity import claim_fingerprint, finding_fingerprint
from deep_research.agents.quality import compute_report_quality
from deep_research.agents.report import (
    DEFAULT_ANSWER_HEADING,
    DEFAULT_READER_WORD_LIMIT,
    EVIDENCE_SECTIONS,
    EVIDENCE_STATUS_LABELS,
    EVIDENCE_TITLE_PREFIX,
    LIMITATION_REASONS,
    LIMITATION_TOPICS,
    QUALITY_RECORD_EXCERPT_CHARS,
    QUALITY_RECORD_TEXT_CHARS,
    QUALITY_STATUS_NOT_GATED,
    REPORT_SECTIONS,
    REPORT_SUMMARY_FALLBACK,
    REPORT_TITLE_PREFIX,
    Citation,
    ReportComposition,
    ReportConstraint,
    ReportPoint,
    ReportSection,
    StatementMappingError,
    UnknownEvidenceError,
    backmatter_ratio,
    canonical_claims,
    canonical_sources,
    citation_markers,
    composition_statements,
    evidence_status_bucket,
    evidence_status_counts,
    fit_report_composition,
    reader_citations,
    reader_sections,
    reader_word_count,
    reader_word_limit,
    render_citations,
    render_evidence_ledger,
    render_limitations,
    render_quality_record,
    render_reader_report,
    render_statement_map,
    report_as_of,
    report_scope,
    statement_citation_urls,
    statement_source_urls,
    validate_report_statements,
)
from deep_research.agents.researcher import sub_topic_skipped_error
from deep_research.utils.types import (
    EVIDENCE_BADGE_LABELS,
    QUALITY_CONTRACT_VERSION,
    AtomicProposition,
    Claim,
    ClaimCluster,
    EvidenceDisposition,
    EvidencePassage,
    EvidenceTarget,
    EvidenceUnit,
    Finding,
    ReadRecord,
    ReportAnswerRow,
    ReportStatement,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ScoredSource,
    SourceTemporal,
    StatementMode,
    SubTopic,
    answered_atom_dimensions,
    statement_mode_for_claims,
)

EXTRACTED_AT = "2026-08-01T12:00:00+00:00"
SOURCE_URL = "https://example.org/a"
OTHER_URL = "https://other.test/b"
THIRD_URL = "https://third.test/c"


def render_reports(composition: ReportComposition) -> tuple[str, str]:
    """Both artifacts, exactly as a caller sees them.

    Test-local on purpose: the two renderers are the production interface.
    """
    return (
        render_reader_report(composition),
        render_evidence_ledger(composition),
    )


def _source(
    *,
    url: str = SOURCE_URL,
    title: str = "QEC 2025",
    overall: float | None = 0.76,
    status: str = "scored",
    low_confidence: bool = False,
    rationale: str = "Peer-reviewed and corroborated.",
    work_id: str | None = None,
    transport: str = "unknown",
    temporal: SourceTemporal | None = None,
) -> ScoredSource:
    extra: dict[str, object] = {
        "work_id": work_id,
        "transport_relation": transport,
    }
    if temporal is not None:
        extra["temporal"] = temporal
    if status == "scored":
        return ScoredSource(
            url=url,
            title=title,
            authority_score=0.8,
            recency_score=0.7,
            relevance_score=0.9,
            overall_score=overall,
            rationale=rationale,
            low_confidence=low_confidence,
            **extra,  # type: ignore[arg-type]
        )
    return ScoredSource(
        url=url,
        title=title,
        rationale=rationale,
        evaluation_status=status,
        **extra,  # type: ignore[arg-type]
    )


def _claim(
    *,
    text: str = "Logical error rates fell below break-even in 2025.",
    urls: list[str] | None = None,
    verdict: str = "verified",
    confidence: float = 0.8,
    contradictions: list[str] | None = None,
    passages: list[EvidencePassage] | None = None,
    coverage_ids: list[str] | None = None,
    finding_fingerprints: list[str] | None = None,
    insufficient_reason: str | None = None,
    badge: str | None = None,
) -> Claim:
    """One checked claim. ``badge`` overrides the verdict-derived badge."""
    return Claim(
        claim_id=claim_fingerprint(text),
        text=text,
        source_urls=urls or [SOURCE_URL],
        verdict=verdict,
        evidence_status=(
            badge
            if badge is not None
            else (
                "verified_pair"
                if verdict == "verified"
                else "source_supported"
                if verdict == "insufficient_evidence"
                else None
            )
        ),
        confidence=confidence,
        evidence=["An independent review states the same figure."],
        contradictions=contradictions or [],
        verification_evidence=passages or [],
        # The reason is set on the insufficient path only, so a fixture that
        # is not an insufficient claim does not carry the field at all —
        # the same shape the fact checker emits.
        **(
            {"insufficient_reason": insufficient_reason}
            if insufficient_reason is not None
            else {}
        ),
        consumed_finding_fingerprints=finding_fingerprints or [],
        consumed_coverage_ids=coverage_ids or [],
    )


def _passage(url: str = THIRD_URL) -> EvidencePassage:
    return EvidencePassage(
        source_url=url,
        source_title="Independent review",
        locator="p. 1",
        excerpt="An independent review states the same figure.",
        stance="supports",
    )


def _point(
    text: str = "Break-even was reached.",
    *,
    claim_ids: list[str] | None = None,
    source_urls: list[str] | None = None,
) -> ReportPoint:
    return ReportPoint(
        text=text,
        claim_ids=claim_ids if claim_ids is not None else [_claim().claim_id],
        source_urls=source_urls if source_urls is not None else [SOURCE_URL],
    )


def _composition(**overrides: object) -> ReportComposition:
    claim = _claim()
    payload: dict[str, object] = {
        "question": "How mature is quantum error correction?",
        "session_id": "session-1",
        "iteration": 0,
        "as_of": EXTRACTED_AT,
        "scope": "1 planned sub-topic, as recorded below.",
        "claims": [claim],
        "sources": [_source()],
        "findings": [
            Finding(
                content="Logical error rates fell below break-even.",
                source_url=SOURCE_URL,
                source_title="QEC 2025",
                extracted_at=EXTRACTED_AT,
                confidence=0.8,
                related_sub_topic="Alpha",
            )
        ],
        "limitations": [],
        "summary": [_point(claim_ids=[claim.claim_id])],
        "sections": [
            ReportSection(
                title="Error correction",
                points=[_point(claim_ids=[claim.claim_id])],
            )
        ],
    }
    payload.update(overrides)
    return ReportComposition.model_validate(payload)


# --- the pathological fixture -------------------------------------------------

PATHOLOGICAL_CANONICAL_SOURCES = 101
PATHOLOGICAL_SOURCE_RECORDS = 257
PATHOLOGICAL_CANONICAL_CLAIMS = 40
PATHOLOGICAL_CLAIM_RECORDS = 257


def _pathological_sources() -> list[ScoredSource]:
    """101 canonical URLs behind 257 records, exactly the observed shape."""
    return [
        _source(
            url=f"https://example.test/source-{record % 101 + 1:03d}",
            title=f"Source {record % 101 + 1:03d}",
            overall=0.60 + (record % 20) / 100,
        )
        for record in range(PATHOLOGICAL_SOURCE_RECORDS)
    ]


def _pathological_claims() -> list[Claim]:
    """40 canonical claims repeated across 257 records, at three verdicts."""
    claims: list[Claim] = []
    for record in range(PATHOLOGICAL_CLAIM_RECORDS):
        index = record % 40 + 1
        verdict = (
            "contradicted"
            if index % 5 == 0
            else "unverified"
            if index % 7 == 0
            else "verified"
        )
        claims.append(
            _claim(
                text=f"Claim {index:03d} states a measured result.",
                urls=[f"https://example.test/source-{index:03d}"],
                verdict=verdict,
                confidence=0.4 + (record % 6) / 10,
                contradictions=(
                    ["An independent source disagrees."]
                    if verdict == "contradicted"
                    else None
                ),
            )
        )
    return claims


def pathological_composition() -> ReportComposition:
    """The pathological state's composition, with one cited settled point."""
    claim_id = claim_fingerprint("Claim 001 states a measured result.")
    source_url = "https://example.test/source-001"
    return ReportComposition.model_validate(
        {
            "question": "How much of the evidence is load-bearing?",
            "session_id": "pathological",
            "iteration": 1,
            "as_of": EXTRACTED_AT,
            "scope": "3 planned sub-topics.",
            "claims": _pathological_claims(),
            "sources": _pathological_sources(),
            "findings": [],
            "limitations": ["no_verified_claims"],
            "summary": [
                _point(
                    "One measured result is load-bearing.",
                    claim_ids=[claim_id],
                    source_urls=[source_url],
                )
            ],
            "sections": [
                ReportSection(
                    title="Load-bearing evidence",
                    points=[
                        _point(
                            "The first source carries the result.",
                            claim_ids=[claim_id],
                            source_urls=[source_url],
                        )
                    ],
                )
            ],
        }
    )


def duplicate_claim_text(markdown: str) -> bool:
    """True when any bullet of ``markdown`` repeats another one verbatim.

    Repeated claims with different confidences are the observed pathology:
    every settled statement, and every conflicting claim the report prints,
    must appear exactly once.
    """
    bullets = [
        " ".join(line.split())
        for line in markdown.splitlines()
        if line.strip().startswith(("- ", "* "))
    ]
    return len(bullets) != len(set(bullets))


def test_the_pathological_state_renders_each_canonical_record_once() -> None:
    reader, ledger = render_reports(pathological_composition())

    assert reader.count("https://example.test/source-001") == 1
    assert "Reviewed but not cited" not in reader
    assert "Reviewed but not cited" in ledger
    assert duplicate_claim_text(reader) is False


def test_the_pathological_ledger_carries_one_row_per_canonical_source() -> None:
    _, ledger = render_reports(pathological_composition())
    rows = _table_rows(_section_body(ledger, "## Source assessment"))[2:]

    urls = [
        re.search(r"https://[^\s)]+", row).group(0)  # type: ignore[union-attr]
        for row in rows
    ]
    assert len(rows) == PATHOLOGICAL_CANONICAL_SOURCES
    assert len(set(urls)) == PATHOLOGICAL_CANONICAL_SOURCES


def test_the_pathological_ledger_registers_each_canonical_claim_once() -> None:
    _, ledger = render_reports(pathological_composition())
    registry = _section_body(ledger, "## Checked claim registry")

    rows = _table_rows(registry)[2:]
    assert len(rows) == PATHOLOGICAL_CANONICAL_CLAIMS
    assert ledger.count("Claim 001 states a measured result.") == 1


def test_the_claim_registry_keeps_its_columns_and_ends_with_reason() -> None:
    """The audit column is additive: nothing the ledger already showed moves.

    A reader who learned the registry's eight columns must find all eight in
    the same order, with the new ``Reason`` column appended rather than
    inserted, and every row — header, separator and data — as wide as the
    header it belongs to.
    """
    _, ledger = render_reports(pathological_composition())
    rows = _table_rows(_section_body(ledger, "## Checked claim registry"))

    assert _cells(rows[0]) == [
        "#",
        "Claim ID",
        "Verdict",
        "Confidence",
        "Claim",
        "Sources",
        "Coverage",
        "Contradictions",
        "Reason",
    ]
    assert {len(_cells(row)) for row in rows} == {9}


def test_an_insufficient_claim_registers_its_reason_in_the_ledger() -> None:
    """The classification a reviewer asked for, read off the artifact.

    ``verdict`` alone cannot say whether a claim went unjudged because nothing
    independent was ever read or because the verdict came back thin; the
    enumerated reason is what separates them, and the ledger is where a
    reviewer who never sees the event log reads it.
    """
    _, ledger = render_reports(
        _composition(
            claims=[
                _claim(
                    verdict="insufficient_evidence",
                    confidence=0.0,
                    insufficient_reason="no_independent_source",
                )
            ]
        )
    )
    cells = _cells(
        _table_rows(_section_body(ledger, "## Checked claim registry"))[2]
    )

    assert len(cells) == 9
    assert cells[-1] == "no_independent_source"


def test_a_verified_claim_registers_no_reason() -> None:
    """The reason is an admission, not a verdict.

    A claim that was judged against independent evidence carries no reason —
    on the record or in the ledger — so an empty ``Reason`` cell stays
    distinguishable from a populated one.
    """
    claim = _claim()
    _, ledger = render_reports(_composition(claims=[claim]))
    cells = _cells(
        _table_rows(_section_body(ledger, "## Checked claim registry"))[2]
    )

    # Nine cells: the eight the registry always had, plus Reason, which is
    # empty for a claim nothing was wrong with.
    assert len(cells) == 9
    assert cells[-1] == "—"
    assert claim.insufficient_reason is None


def test_reviewed_but_unused_sources_reach_the_ledger_only() -> None:
    composition = _composition(
        sources=[_source(), _source(url=OTHER_URL, title="Other study")],
    )
    reader, ledger = render_reports(composition)

    assert OTHER_URL not in reader
    assert "Reviewed but not cited" in ledger
    unused = _section_body(ledger, "## Reviewed but not cited")
    assert OTHER_URL in unused
    assert SOURCE_URL not in unused


# --- the reader report's shape ------------------------------------------------


def test_the_reader_report_carries_every_section_in_order() -> None:
    reader = render_reader_report(_composition())

    positions = [reader.index(heading) for heading in REPORT_SECTIONS]
    assert positions == sorted(positions)
    assert reader.startswith(
        f"{REPORT_TITLE_PREFIX}How mature is quantum error correction?"
    )
    assert reader.endswith("\n")


def test_the_reader_report_declares_as_of_scope_and_quality_status() -> None:
    reader = render_reader_report(_composition())

    assert f"**As of:** {EXTRACTED_AT}" in reader
    assert "**Scope:** 1 planned sub-topic, as recorded below." in reader
    assert f"**Quality status:** {QUALITY_STATUS_NOT_GATED}" in reader


def test_an_undated_pass_says_so_instead_of_reading_a_clock() -> None:
    reader = render_reader_report(_composition(as_of=""))

    assert "**As of:** no dated evidence was recorded" in reader


def test_every_point_carries_its_own_inline_markers() -> None:
    composition = _composition(
        claims=[
            _claim(),
            _claim(
                text="Cost fell tenfold.",
                urls=[OTHER_URL],
                verdict="unverified",
            ),
        ],
        sources=[_source(), _source(url=OTHER_URL, title="Other study")],
        summary=[],
        sections=[
            ReportSection(
                title="Error correction",
                points=[
                    _point(
                        "Break-even was reached.",
                        claim_ids=[claim_fingerprint(
                            "Logical error rates fell below break-even in 2025."
                        )],
                        source_urls=[SOURCE_URL],
                    ),
                    _point(
                        "Costs fell.",
                        claim_ids=[claim_fingerprint("Cost fell tenfold.")],
                        source_urls=[OTHER_URL],
                    ),
                ],
            )
        ],
    )
    reader = render_reader_report(composition)
    findings = _section_body(reader, "## Findings")

    assert "- Break-even was reached. [1]" in findings
    assert "- Costs fell. [2]" in findings
    # The old renderer closed every section with one pile of markers.
    assert "Sources: [" not in reader
    assert "Sources: none cited" not in reader


def test_an_empty_findings_section_renders_no_heading_at_all() -> None:
    composition = _composition(
        sections=[
            ReportSection(title="Kept", points=[_point()]),
            ReportSection(title="Dropped", points=[]),
        ]
    )
    reader = render_reader_report(composition)

    assert "### Kept" in reader
    assert "### Dropped" not in reader


def test_the_constraint_table_carries_the_four_decision_columns() -> None:
    """Evidence strength is a label, not a two-decimal probability.

    The confidence number is a model judgement; printing it invited a reader
    to weigh "0.80" as a frequency it never was. It stays in the ledger's
    claim registry, beside the caveat.
    """
    composition = _composition(
        summary=[],
        sections=[],
        constraints=[
            ReportConstraint(
                text="Cordon tolling inside the central business district",
                deployment_mechanism="area licence with camera enforcement",
                geography="London",
                claim_ids=[_claim().claim_id],
                source_urls=[SOURCE_URL],
            ),
            ReportConstraint(
                text="Distance-based charging",
                deployment_mechanism="not stated",
                geography="not stated",
                claim_ids=[_claim().claim_id],
                source_urls=[SOURCE_URL],
            ),
        ],
    )
    table = _section_body(render_reader_report(composition), "## Constraint ranking")

    assert (
        "| Constraint | Deployment mechanism | Geography | Evidence strength |"
    ) in table
    assert "Confidence" not in table
    assert "0.80" not in table
    assert "| Cordon tolling inside the central business district [1] " in table
    assert "| area licence with camera enforcement | London |" in table
    assert "| not stated | not stated |" in table
    # The evidence strength is the qualitative reading of the badge behind the
    # row, resolved locally from the claim the row cites.
    assert "| independently corroborated |" in table


def test_uncertainty_prints_gaps_conflicts_and_limitations_once_each() -> None:
    contradicted = _claim(
        text="Cost fell tenfold.",
        verdict="contradicted",
        confidence=0.4,
        contradictions=["A vendor report disagrees."],
    )
    composition = _composition(
        claims=[_claim(), contradicted],
        uncertainty_notes=["Vendor numbers remain unaudited."],
        limitations=["errors_recorded"],
    )
    section = _section_body(
        render_reader_report(composition), "## Uncertainty and conflicting evidence"
    )

    assert "Vendor numbers remain unaudited." in section
    assert "- Cost fell tenfold." in section
    assert "1 contradicting passage(s)" in section
    assert LIMITATION_REASONS["errors_recorded"] in section
    # A verified claim is not uncertain; it stays out of this section.
    assert "Logical error rates fell below break-even" not in section


def test_methodology_is_a_compact_locally_generated_run_summary() -> None:
    section = _section_body(
        render_reader_report(
            _composition(
                sub_topics=[
                    SubTopic(
                        coverage_id="topic-01",
                        title="Alpha",
                        rationale="First.",
                        search_queries=["alpha"],
                        success_criteria=["alpha evidence"],
                        priority=1,
                    )
                ],
                sources=[
                    _source(),
                    _source(
                        url=OTHER_URL,
                        title="Unscored study",
                        status="unscored_provider",
                        rationale=(
                            "The provider was unavailable, so this source "
                            "carries no quality judgement."
                        ),
                    ),
                ],
                limitations=["low_confidence_sources"],
            )
        ),
        "## Methodology",
    )

    assert "2 reviewed source(s)" in section
    assert "1 scored" in section
    assert "1 unscored" in section
    assert "topic-01" in section
    # No persistence claim: synthesis writes nothing.
    for phrase in ("saved to", "written to", "stored at"):
        assert phrase not in section
    # Limitations are disclosed once, in the uncertainty section.
    assert LIMITATION_REASONS["low_confidence_sources"] not in section


def test_an_evidence_free_reader_report_still_carries_every_section() -> None:
    reader = render_reader_report(
        ReportComposition(question="What is known?", session_id="session-1")
    )

    for heading in REPORT_SECTIONS:
        assert heading in reader
    assert REPORT_SUMMARY_FALLBACK in reader
    assert "(no sources were cited)" in reader
    assert render_reader_report(
        ReportComposition(question="What is known?", session_id="session-1")
    ) == reader


def test_limitations_render_enumerated_reasons_only() -> None:
    rendered = render_limitations(["errors_recorded", "no_verified_claims"])

    assert rendered.count("- ") == 2
    assert LIMITATION_REASONS["errors_recorded"] in rendered
    assert render_limitations([]) == "No limitations were recorded for this pass."
    with pytest.raises(ValueError, match="limitation reason"):
        render_limitations(["because"])


# --- Step 7: the structural bounds -------------------------------------------


def test_no_reader_section_repeats_a_canonical_url_row() -> None:
    """Cited URLs exactly once in the reader; uncited ones not at all.

    ``<= 1`` was unfalsifiable for the second URL: ``source-042`` is never
    cited, so its count is 0 and the bound permitted 0 for ``source-001`` too.
    The reader's cited set comes from the composition's own citation index, so
    "exactly once" is asserted where a citation exists and "never" where none
    does.
    """
    composition = pathological_composition()
    reader, ledger = render_reports(composition)
    cited = {citation.url for citation in reader_citations(composition)}
    uncited = "https://example.test/source-042"
    assessment = _section_body(ledger, "## Source assessment")

    assert cited == {"https://example.test/source-001"}
    assert uncited not in cited
    for source_url in cited:
        assert reader.count(source_url) == 1
    assert reader.count(uncited) == 0
    for source_url in (*sorted(cited), uncited):
        assert assessment.count(source_url) == 1


def test_no_claim_id_is_registered_twice() -> None:
    _, ledger = render_reports(pathological_composition())
    registry = _section_body(ledger, "## Checked claim registry")
    ids = [
        row.split("|")[2].strip()
        for row in _table_rows(registry)[2:]
    ]

    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[0-9a-f]{64}", claim_id) for claim_id in ids)


def test_every_reader_citation_resolves_and_every_reference_is_used() -> None:
    reader = render_reader_report(
        _composition(
            claims=[
                _claim(),
                _claim(
                    text="Cost fell tenfold.",
                    urls=[OTHER_URL],
                    verdict="unverified",
                ),
            ],
            sources=[_source(), _source(url=OTHER_URL, title="Other study")],
            sections=[
                ReportSection(
                    title="Both",
                    points=[
                        _point(),
                        _point(
                            "Costs fell.",
                            claim_ids=[claim_fingerprint("Cost fell tenfold.")],
                            source_urls=[OTHER_URL],
                        ),
                    ],
                )
            ],
        )
    )

    markers = {int(number) for number in re.findall(r"\[(\d+)\]", reader)}
    references = {
        int(match.group(1))
        for match in re.finditer(r"(?m)^(\d+)\. ", reader)
    }
    assert markers
    assert markers == references


def test_every_settled_point_is_cited_and_carries_a_checked_claim() -> None:
    composition = pathological_composition()
    reader = render_reader_report(composition)
    cited = {citation.url for citation in reader_citations(composition)}

    for point in _reader_points(composition):
        assert point.claim_ids
        assert point.source_urls
        assert cited.issuperset(point.source_urls)
    # Every settled statement the reader prints ends in its own markers.
    for heading in ("## Executive summary", "## Findings"):
        bullets = [
            line
            for line in _section_body(reader, heading).splitlines()
            if line.startswith("- ")
        ]
        assert bullets
        assert all(re.search(r"\[\d+\]", line) for line in bullets)


def test_references_equal_the_urls_the_reader_points_use() -> None:
    composition = _composition(
        claims=[
            _claim(),
            _claim(text="Cost fell tenfold.", urls=[OTHER_URL], verdict="unverified"),
        ],
        sources=[_source(), _source(url=OTHER_URL, title="Other study")],
        sections=[
            ReportSection(
                title="Both",
                points=[
                    _point(),
                    _point(
                        "Costs fell.",
                        claim_ids=[claim_fingerprint("Cost fell tenfold.")],
                        source_urls=[OTHER_URL],
                    ),
                ],
            )
        ],
    )
    reader = render_reader_report(composition)
    index = reader_citations(composition)
    references = _section_body(reader, "## References")

    assert [citation.url for citation in index] == [SOURCE_URL, OTHER_URL]
    assert references.strip() == render_citations(index)


def test_no_finding_section_says_sources_none_cited() -> None:
    reader = render_reader_report(
        _composition(
            sections=[ReportSection(title="Uncited", points=[])],
        )
    )

    assert "Sources: none cited" not in reader
    assert "### Uncited" not in reader


def test_the_reference_material_stays_below_a_third_of_the_reader_report() -> None:
    claims = [
        _claim(
            text=f"Measured result {index} was reported.",
            urls=[f"https://example.test/study-{index}"],
        )
        for index in range(6)
    ]
    sources = [
        _source(
            url=f"https://example.test/study-{index}",
            title=f"Study {index}",
        )
        for index in range(6)
    ]
    composition = _composition(
        claims=claims,
        sources=sources,
        summary=[
            _point(
                "Six measured results were reported.",
                claim_ids=[claims[0].claim_id],
                source_urls=[claims[0].source_urls[0]],
            )
        ],
        constraints=[
            ReportConstraint(
                text=f"Constraint {index}",
                deployment_mechanism="licence",
                geography="a city",
                claim_ids=[claim.claim_id],
                source_urls=list(claim.source_urls),
            )
            for index, claim in enumerate(claims)
        ],
        sections=[
            ReportSection(
                title=f"Theme {index}",
                points=[
                    _point(
                        f"Result {index} was measured.",
                        claim_ids=[claim.claim_id],
                        source_urls=list(claim.source_urls),
                    )
                ],
            )
            for index, claim in enumerate(claims)
        ],
    )
    reader = render_reader_report(composition)
    references = _section_body(reader, "## References")

    assert len(references) / len(reader) < 0.35
    assert reader.count(claims[0].source_urls[0]) == 1


# --- Step 6: honest statuses --------------------------------------------------


def test_scored_sources_print_numbers_and_unscored_ones_print_status() -> None:
    _, ledger = render_reports(
        _composition(
            sources=[
                _source(),
                _source(
                    url=OTHER_URL,
                    title="Capped study",
                    status="unscored_cap",
                    rationale=(
                        "The per-run source cap was reached before this "
                        "source was scored."
                    ),
                ),
            ]
        )
    )
    assessment = _section_body(ledger, "## Source assessment")
    scored_row, unscored_row = _table_rows(assessment)[2:]

    assert "0.76" in scored_row
    assert "scored" in scored_row
    assert "unscored_cap" in unscored_row
    assert "per-run source cap was reached" in unscored_row
    # No numeric quality field is printed for a source nobody scored.
    assert "0.76" not in unscored_row
    assert "0.80" not in unscored_row


def test_low_confidence_means_a_real_score_below_the_threshold() -> None:
    _, ledger = render_reports(
        _composition(
            sources=[
                _source(
                    url=OTHER_URL,
                    title="Anonymous blog",
                    overall=0.08,
                    low_confidence=True,
                    rationale="Anonymous blog.",
                )
            ]
        )
    )
    assessment = _section_body(ledger, "## Source assessment")
    row = _table_rows(assessment)[2]

    assert "0.08" in row
    assert "low" in row


def test_an_unscored_source_is_never_reported_as_low_confidence() -> None:
    _, ledger = render_reports(
        _composition(
            sources=[
                _source(
                    url=OTHER_URL,
                    title="Unscored study",
                    status="unscored_missing",
                    rationale="The model returned no row for this source.",
                )
            ]
        )
    )
    row = _table_rows(_section_body(ledger, "## Source assessment"))[2]

    assert "unscored_missing" in row
    assert "low" not in row


# --- the evidence ledger ------------------------------------------------------


def test_the_ledger_carries_every_verbose_block() -> None:
    _, ledger = render_reports(
        _composition(
            claims=[
                _claim(passages=[_passage()], coverage_ids=["topic-01"]),
                _claim(
                    text="Cost fell tenfold.",
                    urls=[OTHER_URL],
                    verdict="contradicted",
                    contradictions=["A vendor report disagrees."],
                ),
            ],
            sources=[_source(), _source(url=OTHER_URL, title="Other study")],
            errors=[
                ResearchError(
                    error_type="synthesizer_invalid_section",
                    source="agent.synthesizer",
                    message="Some drafted content was refused.",
                    recoverable=True,
                    details={"rejected": ["section 2: no known checked claim"]},
                )
            ],
            rejected=["section 2: no known checked claim"],
        )
    )

    assert ledger.startswith(
        f"{EVIDENCE_TITLE_PREFIX}How mature is quantum error correction?"
    )
    for heading in EVIDENCE_SECTIONS:
        assert heading in ledger
    passages = _section_body(ledger, "## Verification passages")
    assert THIRD_URL in passages
    assert "An independent review states the same figure." in passages
    assert "A vendor report disagrees." in _section_body(
        ledger, "## Checked claim registry"
    )
    assert "section 2: no known checked claim" in _section_body(
        ledger, "## Rejected draft content"
    )
    assert "synthesizer_invalid_section" in _section_body(ledger, "## Run errors")


def test_the_ledger_records_the_coverage_a_claim_consumed() -> None:
    _, ledger = render_reports(
        _composition(claims=[_claim(coverage_ids=["topic-01", "topic-02"])])
    )

    assert "topic-01, topic-02" in ledger


def test_same_url_findings_only_hide_the_consumed_finding() -> None:
    consumed = Finding(
        content="The consumed finding is checked.",
        source_url=SOURCE_URL,
        source_title="QEC 2025",
        extracted_at=EXTRACTED_AT,
        confidence=0.8,
        related_sub_topic="Alpha",
    )
    untouched = Finding(
        content="The untouched finding remains an open question.",
        source_url=SOURCE_URL,
        source_title="QEC 2025",
        extracted_at=EXTRACTED_AT,
        confidence=0.7,
        related_sub_topic="Alpha",
    )
    ledger = render_evidence_ledger(
        _composition(
            claims=[
                _claim(
                    finding_fingerprints=[finding_fingerprint(consumed)]
                )
            ],
            findings=[consumed, untouched],
        )
    )

    open_questions = _section_body(
        ledger, "## Unchecked findings and open questions"
    )
    assert "The consumed finding is checked." not in open_questions
    assert "The untouched finding remains an open question." in open_questions


def test_an_empty_ledger_still_carries_every_block() -> None:
    ledger = render_evidence_ledger(
        ReportComposition(question="What is known?", session_id="session-1")
    )

    for heading in EVIDENCE_SECTIONS:
        assert heading in ledger
    assert ledger.endswith("\n")


def test_the_run_errors_block_publishes_bounded_tool_failure_details() -> None:
    """A classified tool failure reaches the ledger; an unvetted one does not.

    The bounded scraper diagnosis is the only reason a ``web_scraper`` failure
    can be counted *by class* rather than merely counted, so the evidence
    artifact has to carry it — otherwise classifying the failure buys nothing
    for the reader of this artifact. Every other error type's details are
    withheld: this ledger is public, and those values are not produced by a
    projection that revalidates them.
    """
    ledger = render_evidence_ledger(
        _composition(
            errors=[
                ResearchError(
                    error_type="agent_tool_failed",
                    source="agent.researcher",
                    message="web_scraper failed; the agent continued.",
                    recoverable=True,
                    details={
                        "tool": "web_scraper",
                        "iteration": 3,
                        "tool_error_type": "HTTPStatusError",
                        "attempts": 2,
                        "retries": 1,
                        "status_code": 503,
                        "content_type": "text/html",
                    },
                ),
                ResearchError(
                    error_type="synthesizer_invalid_section",
                    source="agent.synthesizer",
                    message="Some drafted content was refused.",
                    recoverable=True,
                    details={"unvetted": "https://internal.example/secret"},
                ),
            ]
        )
    )

    errors = _section_body(ledger, "## Run errors")

    assert "tool_error_type=HTTPStatusError" in errors
    assert "status_code=503" in errors
    assert "content_type=text/html" in errors
    assert "unvetted" not in errors
    assert "internal.example" not in errors


def test_the_run_errors_block_names_why_a_sub_topic_was_skipped() -> None:
    """A skipped sub-topic must publish *which* one and *why*.

    It is the difference between a coverage gap and an acceptable skip: "cap"
    and "provider_failure_stopped_processing" are losses, while
    "interim_satisfaction" is a refinement pass correctly reusing a prior
    finding. A run lost three planned sub-topics and the artifact could not say
    which reason applied, so the reason is now published alongside the
    locally-stamped coverage id.
    """
    ledger = render_evidence_ledger(
        _composition(
            errors=[
                ResearchError(
                    error_type="researcher_sub_topic_skipped",
                    source="agent.researcher",
                    message="A planned sub-topic was never researched.",
                    recoverable=True,
                    details={
                        "sub_topic": "Interconnection queue reform",
                        "coverage_id": "topic-04",
                        "priority": 4,
                        "reason": "provider_failure_stopped_processing",
                    },
                )
            ]
        )
    )

    errors = _section_body(ledger, "## Run errors")

    assert "coverage_id=topic-04" in errors
    assert "reason=provider_failure_stopped_processing" in errors


def test_the_run_errors_block_reads_each_skip_reason_distinctly() -> None:
    """Prior completion is not a stopped pass, and neither reads "never researched".

    ``sub_topic_skipped_error`` writes one message for all four of its
    reasons, and that message says the topic was never researched — which the
    ledger printed for every reason, including a topic an earlier pass had
    already answered. Ruling 5 keeps prior completion, deferred work and
    never-attempted work distinct, so the reading follows the enumerated
    reason and the never-researched sentence is printed only where it is true.
    """
    ledger = render_evidence_ledger(
        _composition(
            errors=[
                sub_topic_skipped_error(
                    _sub_topic("topic-01"), reason="required_targets_completed"
                ),
                sub_topic_skipped_error(_sub_topic("topic-04"), reason="cap"),
                sub_topic_skipped_error(
                    _sub_topic("topic-06"),
                    reason="provider_failure_stopped_processing",
                ),
            ]
        )
    )

    errors = _section_body(ledger, "## Run errors")

    assert "already met its required targets" in errors
    assert "was deferred" in errors
    assert "a provider failure stopped the pass" in errors
    assert errors.count("never researched") == 1


def _sub_topic(coverage_id: str) -> SubTopic:
    return SubTopic(
        coverage_id=coverage_id,
        title="Alpha",
        rationale="First.",
        search_queries=["alpha"],
        success_criteria=["alpha evidence"],
        priority=1,
    )


# --- identity and citation helpers -------------------------------------------


def test_canonicalization_collapses_repeated_records_in_first_seen_order() -> None:
    sources = _pathological_sources()
    claims = _pathological_claims()

    canonical = canonical_sources(sources)
    canonical_claim_list = canonical_claims(claims)

    assert len(canonical) == PATHOLOGICAL_CANONICAL_SOURCES
    assert len({source.url for source in canonical}) == len(canonical)
    assert len(canonical_claim_list) == PATHOLOGICAL_CANONICAL_CLAIMS
    assert len({claim.claim_id for claim in canonical_claim_list}) == len(
        canonical_claim_list
    )
    # The latest record for a repeated claim wins, as the state contract says.
    assert canonical_claims(claims)[0] == claims[240]


def test_the_latest_recorded_timestamp_is_the_as_of_value() -> None:
    earlier = Finding(
        content="Earlier.",
        source_url=SOURCE_URL,
        source_title="QEC 2025",
        extracted_at="2026-07-01T09:00:00+00:00",
        confidence=0.5,
        related_sub_topic="Alpha",
    )
    later = earlier.model_copy(update={"extracted_at": EXTRACTED_AT})

    assert report_as_of(findings=[earlier, later], events=[]) == EXTRACTED_AT
    assert (
        report_as_of(
            findings=[earlier],
            events=[
                ResearchEvent(
                    event_type="researcher.sub_topic.completed",
                    source="agent.researcher",
                    message="Done.",
                    timestamp="2026-09-01T00:00:00+00:00",
                )
            ],
        )
        == "2026-09-01T00:00:00+00:00"
    )
    assert report_as_of(findings=[], events=[]) == ""


def test_scope_is_stated_from_the_plan_alone() -> None:
    topic = SubTopic(
        coverage_id="topic-01",
        title="Alpha",
        rationale="First.",
        search_queries=["alpha"],
        success_criteria=["alpha evidence"],
        priority=1,
    )

    rendered = report_scope([topic])

    assert "topic-01" in rendered
    assert "Alpha" in rendered
    assert "no geography" in rendered.lower()
    assert report_scope([]) != ""


def test_citation_numbers_follow_first_use_in_the_reader_report() -> None:
    composition = _composition(
        claims=[
            _claim(),
            _claim(text="Cost fell tenfold.", urls=[OTHER_URL], verdict="unverified"),
        ],
        sources=[_source(), _source(url=OTHER_URL, title="Other study")],
        summary=[
            _point(
                "Costs fell.",
                claim_ids=[claim_fingerprint("Cost fell tenfold.")],
                source_urls=[OTHER_URL],
            )
        ],
        sections=[
            ReportSection(
                title="Both",
                points=[
                    _point(
                        "Break-even was reached.",
                        claim_ids=[
                            claim_fingerprint(
                                "Logical error rates fell below break-even in 2025."
                            )
                        ],
                        source_urls=[SOURCE_URL],
                    )
                ],
            )
        ],
    )

    assert [citation.url for citation in reader_citations(composition)] == [
        OTHER_URL,
        SOURCE_URL,
    ]


def test_markers_render_sorted_and_deduplicated() -> None:
    index = [
        Citation(number=1, url=SOURCE_URL, title="QEC 2025"),
        Citation(number=2, url=OTHER_URL, title="Other study"),
    ]

    assert (
        citation_markers([OTHER_URL, SOURCE_URL, OTHER_URL], index) == "[1][2]"
    )
    assert citation_markers(["https://invented.test/x"], index) == ""


def test_citations_render_one_numbered_line_each() -> None:
    index = [Citation(number=1, url=SOURCE_URL, title="QEC 2025")]

    assert render_citations(index) == f"1. QEC 2025 — {SOURCE_URL}"
    assert render_citations([]) == "(no sources were cited)"


def test_a_citation_object_rejects_a_zero_number() -> None:
    with pytest.raises(ValueError):
        Citation(number=0, url=SOURCE_URL, title="A")


# --- Task 7: the statement map ------------------------------------------------


def _unit(
    url: str = THIRD_URL,
    *,
    evidence_id: str = "e1",
    excerpt: str = "An independent review states the same figure.",
    target_ids: list[str] | None = None,
    origin: str = "fact_checker",
) -> EvidenceUnit:
    return EvidenceUnit(
        evidence_id=evidence_id,
        read_id=f"read-{evidence_id}",
        source_url=url,
        source_title="Independent review",
        locator="p. 1",
        excerpt=excerpt,
        target_ids=target_ids if target_ids is not None else ["t1"],
        origin=origin,
    )


def _cluster(
    *,
    cluster_id: str = "cluster-1",
    claim_ids: list[str] | None = None,
    evidence_ids: list[str] | None = None,
    source_urls: list[str] | None = None,
    verdicts: list[str] | None = None,
    target_ids: list[str] | None = None,
    verdict_evidence: dict[str, list[str]] | None = None,
) -> ClaimCluster:
    claim = _claim()
    return ClaimCluster(
        cluster_id=cluster_id,
        proposition=AtomicProposition(text=claim.text),
        evidence_ids=evidence_ids if evidence_ids is not None else ["e1"],
        member_claim_ids=claim_ids if claim_ids is not None else [claim.claim_id],
        target_ids=target_ids if target_ids is not None else ["t1"],
        source_urls=source_urls if source_urls is not None else [THIRD_URL],
        verdicts=verdicts if verdicts is not None else ["verified"],
        verdict_evidence=(
            verdict_evidence
            if verdict_evidence is not None
            else {"verified": [THIRD_URL]}
        ),
        verdict_evidence_status={"verified": "verified_pair"},
    )


def _statement(
    text: str = "Break-even was reached.",
    *,
    statement_id: str = "S1",
    mode: str = "attributed",
    cluster_ids: list[str] | None = None,
    evidence_ids: list[str] | None = None,
    target_ids: list[str] | None = None,
    dimensions: list[str] | None = None,
    basis: str | None = None,
) -> ReportStatement:
    return ReportStatement(
        statement_id=statement_id,
        text=text,
        mode=mode,
        claim_cluster_ids=(
            cluster_ids if cluster_ids is not None else ["cluster-1"]
        ),
        evidence_ids=evidence_ids if evidence_ids is not None else ["e1"],
        target_ids=target_ids if target_ids is not None else ["t1"],
        answered_dimensions=dimensions if dimensions is not None else [],
        basis=basis,
    )


def _stated(
    text: str = "Break-even was reached.",
    *,
    statement: ReportStatement | None = None,
    claim_ids: list[str] | None = None,
    source_urls: list[str] | None = None,
) -> ReportPoint:
    return ReportPoint(
        text=text,
        claim_ids=claim_ids if claim_ids is not None else [_claim().claim_id],
        source_urls=source_urls if source_urls is not None else [SOURCE_URL],
        statement=statement if statement is not None else _statement(text),
    )


def _answer_row(
    subject: str = "Option A",
    dimension: str = "cost",
    finding: str = "Option A cost less.",
) -> ReportAnswerRow:
    return ReportAnswerRow(
        cells=[
            ReportStatement(
                statement_id="row-label-1", text=subject, mode="context"
            ),
            ReportStatement(
                statement_id="row-label-2", text=dimension, mode="context"
            ),
            _statement(
                finding, statement_id="row-finding", mode="settled"
            ),
        ]
    )


def _evidence_composition(**overrides: object) -> ReportComposition:
    """A composition whose statements cite the evidence it actually carries."""
    payload: dict[str, object] = {
        "evidence_units": {"e1": _unit()},
        "claim_clusters": {"cluster-1": _cluster()},
    }
    payload.update(overrides)
    return _composition(**payload)


def test_citation_urls_come_from_selected_support() -> None:
    evidence = {
        "e1": EvidenceUnit(
            evidence_id="e1",
            read_id="r1",
            source_url="https://independent.example/study",
            source_title="Independent study",
            locator="p3",
            excerpt="Study finding.",
            target_ids=["t1"],
            origin="fact_checker",
        )
    }

    assert statement_source_urls(["e1"], evidence) == [
        "https://independent.example/study"
    ]
    assert statement_source_urls([], evidence) == []


def test_statement_source_urls_rejects_an_unknown_id() -> None:
    with pytest.raises(UnknownEvidenceError, match="missing"):
        statement_source_urls(["missing"], {})


def test_a_statement_mode_is_not_a_claim_evidence_status() -> None:
    assert set(get_args(StatementMode)) == {
        "settled",
        "attributed",
        "inference",
        "contested",
        "context",
    }
    assert "verified_pair" not in get_args(StatementMode)
    assert "verified_pair" not in set(get_args(StatementMode))


def test_an_inference_statement_records_the_derivation_it_rests_on() -> None:
    statement = _statement(
        "The converted value is 100 units.",
        mode="inference",
        basis="unit conversion: 1 GW = 1000 MW",
    )

    assert statement.mode == "inference"
    assert statement.basis == "unit conversion: 1 GW = 1000 MW"
    with pytest.raises(ValueError, match="basis"):
        _statement("A derived figure.", mode="inference", basis=None)


def test_reader_citations_carry_the_statements_selected_evidence() -> None:
    """The verified cluster's own selected sources reach the references.

    A statement that resolved its support to an independent verification
    passage must cite that passage, not only the URL of the finding that first
    raised the claim.
    """
    composition = _composition(
        summary=[],
        evidence_units={"e1": _unit()},
        claim_clusters={"cluster-1": _cluster()},
        constraints=[],
        sections=[
            ReportSection(
                title="Error correction",
                points=[_stated()],
            )
        ],
    )
    index = reader_citations(composition)

    assert [citation.url for citation in index] == [SOURCE_URL, THIRD_URL]
    body = _section_body(render_reader_report(composition), "## Findings")
    assert "- Break-even was reached. [1][2]" in body


def test_a_mirror_pair_collapses_to_one_reader_reference() -> None:
    """One work served twice is one reference, and the copy is readable.

    The mirror is cited first and the original second, so collapsing cannot be
    an accident of the order the statement happened to select them in.
    """
    mirror = "https://mirror.test/qec"
    composition = _composition(
        summary=[],
        constraints=[],
        sources=[
            _source(
                url=mirror,
                title="QEC 2025 (mirror)",
                work_id="work-qec-2025",
                transport="mirror",
            ),
            _source(
                url=SOURCE_URL,
                title="QEC 2025",
                work_id="work-qec-2025",
                transport="original",
            ),
        ],
        evidence_units={
            "e1": _unit(mirror, evidence_id="e1"),
            "e2": _unit(SOURCE_URL, evidence_id="e2"),
        },
        claim_clusters={
            "cluster-1": _cluster(
                evidence_ids=["e1", "e2"],
                source_urls=[],
                verdict_evidence={},
            )
        },
        sections=[
            ReportSection(
                title="Error correction",
                points=[
                    _stated(
                        statement=_statement(evidence_ids=["e1", "e2"])
                    )
                ],
            )
        ],
    )

    index = reader_citations(composition)

    assert [citation.url for citation in index] == [SOURCE_URL]
    assert mirror not in render_reader_report(composition)


def test_every_reader_statement_is_mapped_in_the_ledger() -> None:
    composition = _evidence_composition(
        evidence_units={"e1": _unit()},
        claim_clusters={"cluster-1": _cluster()},
        summary=[
            _stated(
                "Break-even was reached in 2025.",
                statement=_statement(
                    "Break-even was reached in 2025.", statement_id="S1"
                ),
            )
        ],
        sections=[
            ReportSection(
                title="Error correction",
                points=[
                    _stated(statement=_statement(statement_id="S2"))
                ],
            )
        ],
    )
    statements = composition_statements(composition)
    mapping = render_statement_map(composition)
    ledger = render_evidence_ledger(composition)

    assert [statement.statement_id for statement in statements] == ["S1", "S2"]
    assert "| Statement | Mode |" in mapping
    assert "cluster-1" in mapping
    assert "e1" in mapping
    assert mapping in ledger


def test_an_unknown_evidence_id_fails_validation_before_rendering() -> None:
    composition = _evidence_composition(
        evidence_units={},
        claim_clusters={},
        summary=[],
        sections=[
            ReportSection(
                title="Error correction",
                points=[
                    _stated(
                        statement=_statement(evidence_ids=["missing"])
                    )
                ],
            )
        ],
    )

    with pytest.raises(UnknownEvidenceError, match="missing"):
        validate_report_statements(composition)
    with pytest.raises(UnknownEvidenceError, match="missing"):
        render_reader_report(composition)


def test_factual_prose_outside_the_statement_map_is_invalid() -> None:
    composition = _evidence_composition(
        evidence_units={},
        claim_clusters={},
        summary=[],
        sections=[
            ReportSection(
                title="Error correction",
                points=[
                    ReportPoint(
                        text="An unmapped factual sentence.",
                        statement=ReportStatement(
                            statement_id="S9",
                            text="An unmapped factual sentence.",
                            mode="settled",
                        ),
                    )
                ],
            )
        ],
    )

    with pytest.raises(StatementMappingError, match="S9"):
        validate_report_statements(composition)
    # The renderer is the only guard a rehydrated composition meets, so it
    # runs the same check before it prints anything.
    with pytest.raises(StatementMappingError, match="S9"):
        render_reader_report(composition)


def test_a_context_statement_may_be_source_free_and_is_still_mapped() -> None:
    composition = _evidence_composition(
        evidence_units={},
        claim_clusters={},
        constraints=[],
        summary=[],
        sections=[
            ReportSection(
                title="Error correction",
                points=[
                    ReportPoint(
                        text="No read was acquired for this topic.",
                        statement=ReportStatement(
                            statement_id="S9",
                            text="No read was acquired for this topic.",
                            mode="context",
                            basis="not acquired: no read was retrieved",
                        ),
                    )
                ],
            )
        ],
    )

    assert validate_report_statements(composition) == []
    assert "No read was acquired" in render_reader_report(composition)


def test_the_summary_leads_with_the_answer_before_the_details() -> None:
    composition = _evidence_composition(
        constraints=[],
        summary=[
            _stated(
                "Break-even was reached in 2025.",
                statement=_statement(
                    "Break-even was reached in 2025.",
                    statement_id="S1",
                    mode="settled",
                ),
            )
        ],
        sections=[ReportSection(title="Error correction", points=[_stated()])],
        limitations=["no_verified_claims"],
    )
    summary = _section_body(
        render_reader_report(composition), "## Executive summary"
    )

    assert "**What the evidence establishes**" in summary
    assert "**What would change the answer**" not in summary
    assert "**The most important unresolved limitation**" in summary
    assert LIMITATION_TOPICS["no_verified_claims"] in summary


@pytest.mark.parametrize(
    ("kind", "heading", "column"),
    [
        ("constraints", "## Constraint ranking", "| Constraint |"),
        ("comparison", "## Comparison", "| Option |"),
        ("factual", "## Key facts", "| Subject |"),
        ("historical", "## Chronology", "| Period |"),
        ("explanation", "## Explanation", None),
    ],
)
def test_each_answer_kind_generates_its_own_second_section(
    kind: str, heading: str, column: str | None
) -> None:
    composition = _evidence_composition(
        answer_kind=kind,
        constraints=(
            [
                ReportConstraint(
                    text="Cordon tolling",
                    deployment_mechanism="licence",
                    geography="London",
                    claim_ids=[_claim().claim_id],
                    source_urls=[SOURCE_URL],
                )
            ]
            if kind == "constraints"
            else []
        ),
        answer_rows=[] if kind == "constraints" else [_answer_row()],
    )
    reader = render_reader_report(composition)
    body = _section_body(reader, heading)

    assert heading in reader
    if column is None:
        assert "| " not in body
        assert "Option A cost less." in body
    else:
        assert column in body
    if kind == "constraints":
        assert "## Constraint ranking" in reader
    else:
        assert "## Constraint ranking" not in reader


def test_a_question_that_is_not_about_constraints_gets_no_empty_table() -> None:
    reader = render_reader_report(
        _evidence_composition(
            answer_kind="comparison", constraints=[], answer_rows=[]
        )
    )
    body = _section_body(reader, "## Comparison")

    assert "| " not in body
    assert body.strip() != ""


def test_the_reader_word_ceiling_follows_the_frozen_contract() -> None:
    assert reader_word_limit(_composition()) == DEFAULT_READER_WORD_LIMIT
    assert (
        reader_word_limit(_composition(requested_word_limit=12000)) == 12000
    )

    points = [
        _stated(
            f"Statement {index} reports a measured result from the study that "
            "was read for this topic and nothing beyond it.",
            statement=_statement(
                f"Statement {index} reports a measured result.",
                statement_id=f"S{index}",
            ),
        )
        for index in range(12)
    ]
    composition = _evidence_composition(
        requested_word_limit=250,
        summary=points[:6],
        sections=[ReportSection(title="Error correction", points=points[6:])],
    )
    # The ceiling is enforced where the composition is built; the renderer
    # shows what it is given.
    fitted, _ = fit_report_composition(composition)
    reader = render_reader_report(fitted)

    assert reader_word_count(reader) <= 250
    assert "Statement 11" not in reader
    assert "Statement 0 reports" in reader


def test_the_backmatter_stays_within_the_reader_ceiling() -> None:
    claims = [
        _claim(
            text=f"Measured result {index} was reported.",
            urls=[f"https://example.test/study-{index}"],
        )
        for index in range(10)
    ]
    composition = _evidence_composition(
        claims=claims,
        sources=[
            _source(
                url=f"https://example.test/study-{index}",
                title=f"Study {index}",
            )
            for index in range(10)
        ],
        constraints=[],
        summary=[
            _stated(
                "One measured result was reported.",
                claim_ids=[claims[0].claim_id],
                source_urls=[claims[0].source_urls[0]],
                statement=_statement("One measured result was reported."),
            )
        ],
        sections=[
            ReportSection(
                title="Load-bearing evidence",
                points=[
                    _stated(
                        f"Study {index} reports that its own measurement was "
                        "taken over the recorded period, that the measurement "
                        "was independently reviewed before publication, and "
                        "that the review found no material disagreement with "
                        "the reported figure or with the method used to "
                        "obtain it.",
                        claim_ids=[claim.claim_id],
                        source_urls=[claim.source_urls[0]],
                        statement=_statement(
                            f"Study {index} reports a reviewed measurement.",
                            statement_id=f"F{index}",
                        ),
                    )
                    for index, claim in enumerate(claims[:8])
                ],
            )
        ],
    )
    reader = render_reader_report(composition)

    assert backmatter_ratio(reader) <= 0.35
    # The ratio is not met by deleting citations: every cited URL survives.
    for claim in claims[:2]:
        assert reader.count(claim.source_urls[0]) == 1


def test_citations_survive_when_the_backmatter_floor_cannot_be_met() -> None:
    """A reference-heavy, prose-light report keeps its citations and says so.

    Deleting a citation to reach a ratio is forbidden, so the honest outcome
    is an over-ratio artifact with the reason recorded in the ledger.
    """
    claims = [
        _claim(
            text=f"Measured result {index} was reported.",
            urls=[f"https://example.test/study-{index}"],
        )
        for index in range(10)
    ]
    composition = _evidence_composition(
        claims=claims,
        sources=[
            _source(
                url=f"https://example.test/study-{index}",
                title=f"Study {index}",
            )
            for index in range(10)
        ],
        constraints=[],
        sections=[
            ReportSection(
                title="Load-bearing evidence",
                points=[
                    _stated(
                        "One measured result was reported.",
                        claim_ids=[claims[0].claim_id],
                        source_urls=[claims[0].source_urls[0]],
                        statement=_statement(
                            "One measured result was reported."
                        ),
                    )
                ],
            )
        ],
    )

    reader = render_reader_report(composition)
    ledger = render_evidence_ledger(composition)
    references = [
        line
        for line in _section_body(reader, "## References").splitlines()
        if re.match(r"^\d+\. ", line)
    ]

    assert backmatter_ratio(reader) > 0.35
    # No citation was deleted to reach a ratio that cannot be reached.
    assert len(references) == len(reader_citations(composition))
    assert "backmatter_floor_reached" in ledger


def test_methodology_does_not_claim_more_linkage_than_the_report_shows() -> None:
    unlinked = ReportStatement(
        statement_id="S7",
        text="No cost evidence was acquired for this topic.",
        mode="context",
        basis="not acquired: no read was retrieved",
    )
    composition = _evidence_composition(
        constraints=[],
        summary=[_stated("Break-even was reached in 2025.")],
        sections=[
            ReportSection(
                title="Error correction",
                points=[
                    _stated(),
                    ReportPoint(text=unlinked.text, statement=unlinked),
                ],
            )
        ],
    )
    section = _section_body(
        render_reader_report(composition), "## Methodology"
    )

    assert "Every statement above is a claim-linked point" not in section
    assert "1 statement(s) above carry no checked claim link" in section


def test_methodology_claims_full_linkage_when_the_report_shows_it() -> None:
    composition = _evidence_composition(
        constraints=[],
        summary=[_stated("Break-even was reached in 2025.")],
        sections=[ReportSection(title="Error correction", points=[_stated()])],
    )
    section = _section_body(
        render_reader_report(composition), "## Methodology"
    )

    assert "Every statement above carries a checked claim link" in section


def test_the_header_separates_generation_from_the_evidence_date() -> None:
    reader = render_reader_report(
        _composition(
            generated_on="2026-09-16",
            date_basis="the question asks for current installed capacity",
        )
    )

    assert f"**As of:** {EXTRACTED_AT}" in reader
    assert "**Generated on:** 2026-09-16" in reader
    assert "**Date basis:** the question asks for current installed capacity" in (
        reader
    )


def test_source_dates_stay_distinct_in_the_ledger() -> None:
    ledger = render_evidence_ledger(
        _composition(
            sources=[
                _source(
                    temporal=SourceTemporal(
                        publication_date="2026-01-01",
                        data_period="2024",
                        forecast_horizon="2035",
                        effective_date="2026-06-01",
                        status="current",
                    )
                )
            ]
        )
    )
    row = _table_rows(_section_body(ledger, "## Source assessment"))[2]

    assert "publication=2026-01-01" in row
    assert "data_period=2024" in row
    assert "forecast=2035" in row
    assert "effective=2026-06-01" in row


def test_uncertainty_context_is_grouped_by_what_it_is() -> None:
    composition = _composition(
        constraints=[],
        summary=[],
        sections=[],
        uncertainty_notes=[],
        uncertainty_statements=[
            ReportStatement(
                statement_id="S1",
                text="No read was acquired for the cost topic.",
                mode="context",
                basis="not acquired: no read was retrieved",
            ),
            ReportStatement(
                statement_id="S2",
                text="Two reads disagree about the measured rate.",
                mode="context",
                basis="uncertain/conflicting: same period, different result",
            ),
            ReportStatement(
                statement_id="S3",
                text="The 2035 horizon is outside this pass's scope.",
                mode="context",
                basis="outside scope: the contract froze an earlier period",
            ),
        ],
    )
    uncertainty = _section_body(
        render_reader_report(composition), "## Uncertainty and conflicting evidence"
    )

    assert "### Not acquired" in uncertainty
    assert "### Uncertain or conflicting" in uncertainty
    assert "### Outside scope" in uncertainty
    assert "No read was acquired for the cost topic." in uncertainty


def test_an_answered_dimension_requires_the_evidence_to_carry_it() -> None:
    """A statement answers a required dimension only when its atom states it.

    The proposition behind the cluster fills a place and a quantity, so a
    target asking for geography and scale is answered on both; a target asking
    for a mechanism is not, because nothing in the recorded evidence states
    one.
    """
    claim = _claim()
    cluster = _cluster(
        claim_ids=[claim.claim_id],
        evidence_ids=["e1"],
    ).model_copy(
        update={
            "proposition": AtomicProposition(
                text=claim.text,
                subject="London pilot",
                value="12",
                unit="GW",
                geography="London",
            )
        }
    )
    composition = _evidence_composition(
        claims=[claim],
        claim_clusters={"cluster-1": cluster},
        sub_topics=[
            SubTopic(
                coverage_id="topic-01",
                title="Alpha",
                rationale="First.",
                search_queries=["alpha"],
                success_criteria=["alpha evidence"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="t1",
                        coverage_id="topic-01",
                        question="Where, how much, and how?",
                        required_dimensions=[
                            "geography",
                            "scale",
                            "mechanism",
                        ],
                        required=True,
                        critical=True,
                        support_policy="independent_pair",
                    )
                ],
            )
        ],
        constraints=[],
        summary=[
            ReportPoint(
                text="The London pilot added 12 GW.",
                claim_ids=[claim.claim_id],
                source_urls=[SOURCE_URL],
            )
        ],
        sections=[],
    )

    # The composition derives the record from the evidence it actually holds,
    # which is where the answered dimensions come from.
    statement = composition.summary[0].statement
    assert statement is not None
    assert statement.target_ids == ["t1"]
    assert statement.answered_dimensions == ["geography", "scale"]


def test_the_word_budget_never_drops_the_ranked_answer() -> None:
    """A row the fit cannot remove must not be listed as droppable.

    The ranked answer is the last thing a report should lose, and a row that
    was popped but could not be dropped would free nothing while the loop ran
    to its floor.
    """
    constraint = ReportConstraint(
        text="Cordon tolling inside the central business district",
        deployment_mechanism="not stated",
        geography="not stated",
        claim_ids=[_claim().claim_id],
        source_urls=[SOURCE_URL],
    )
    findings = [
        _stated(
            f"Finding {index} reports a measured result from the study that "
            "was read for this topic and nothing beyond it.",
            statement=_statement(
                f"Finding {index} reports a measured result.",
                statement_id=f"S{index}",
            ),
        )
        for index in range(12)
    ]
    composition = _evidence_composition(
        requested_word_limit=330,
        summary=[],
        constraints=[constraint],
        sections=[ReportSection(title="Findings", points=findings)],
    )

    # The fit is a build-time step now, so the test performs it explicitly.
    fitted, _ = fit_report_composition(composition)
    reader = render_reader_report(fitted)

    assert "Cordon tolling inside the central business district" in reader
    assert "Finding 11" not in reader
    assert "Finding 0 reports" in reader
    assert reader_word_count(reader) <= 330


def test_the_ledger_describes_the_reader_the_reader_rendered() -> None:
    """Both artifacts are rendered from one fit of one composition.

    A statement dropped to meet the word ceiling takes its citation with it,
    so a ledger that listed the source as cited would contradict the reader.
    """
    dropped_source = _source(url=OTHER_URL, title="Other study")
    findings = [
        _stated(
            f"Finding {index} reports a measured result from the study that "
            "was read for this topic and nothing beyond it.",
            statement=_statement(
                f"Finding {index} reports a measured result.",
                statement_id=f"S{index}",
            ),
        )
        for index in range(12)
    ]
    findings[-1] = _stated(
        "Only this finding cites the other study, and it is the one the "
        "ceiling drops because it is the longest statement in the report by "
        "a wide margin.",
        claim_ids=[_claim().claim_id],
        source_urls=[OTHER_URL],
        statement=_statement(
            "Only this finding cites the other study.",
            statement_id="S11",
        ),
    )
    composition = _evidence_composition(
        requested_word_limit=330,
        sources=[_source(), dropped_source],
        summary=[],
        constraints=[],
        sections=[ReportSection(title="Findings", points=findings)],
    )

    # One fit, at build time; both artifacts render the composition it
    # produced. The fit reasons travel on the composition now, which is what
    # lets the ledger describe a drop the reader never rendered.
    fitted, _ = fit_report_composition(composition)
    reader = render_reader_report(fitted)
    ledger = render_evidence_ledger(fitted)
    references = [
        line
        for line in _section_body(reader, "## References").splitlines()
        if re.match(r"^\d+\. ", line)
    ]
    not_cited = _section_body(ledger, "## Reviewed but not cited")

    assert references
    assert not any(OTHER_URL in line for line in references)
    assert OTHER_URL in not_cited
    assert "length_budget_dropped" in _section_body(
        ledger, "## Statement support map"
    )


def test_the_renderer_shows_the_composition_it_is_given() -> None:
    """One statement set: the fit belongs to the build, not to each renderer.

    A renderer that fits on its own renders a report the composition does not
    describe, and every gate that reads the composition then judges a document
    nobody was shown. The renderer's contract is now the composition it is
    handed — which the build path has already fitted — so an over-long
    composition renders whole rather than being silently trimmed here.
    """
    findings = [
        _stated(
            f"Finding {index} reports a measured result from the study that "
            "was read for this topic and nothing beyond it.",
            statement=_statement(
                f"Finding {index} reports a measured result.",
                statement_id=f"S{index}",
            ),
        )
        for index in range(12)
    ]
    composition = _evidence_composition(
        requested_word_limit=330,
        summary=[],
        constraints=[],
        sections=[ReportSection(title="Findings", points=findings)],
    )

    reader = render_reader_report(composition)

    assert "Finding 11" in reader
    assert reader_word_count(reader) > 330


def test_a_contradicted_claim_is_not_printed_as_corroborated() -> None:
    """The reader's strength column reads the verdict, as the counts do.

    The badge is written before adjudication finishes, so a claim an
    independent source contradicted can still carry ``verified_pair``.
    ``evidence_status_bucket`` — which the quality JSON's counts and the CLI
    line already read through — maps that combination to contested, but the
    reader's own column read the raw badge, so an accepted report could print
    a contradicted fact as "independently corroborated" in the table a reader
    is most likely to trust.
    """
    contradicted = _claim(verdict="contradicted", badge="verified_pair")
    composition = _evidence_composition(
        claims=[contradicted],
        summary=[],
        sections=[],
        constraints=[
            ReportConstraint(
                text="Charge for driving inside the measured zone.",
                deployment_mechanism="area licence with camera enforcement",
                geography="not stated",
                claim_ids=[contradicted.claim_id],
                source_urls=[SOURCE_URL],
            )
        ],
        answer_rows=[_answer_row()],
    )

    reader = render_reader_report(composition)

    assert "contested; both sides recorded" in reader
    assert "independently corroborated" not in reader
    # The counts the quality record and the CLI publish read the same way.
    assert evidence_status_counts([contradicted])["contested"] == 1


def test_a_contradicting_passage_is_not_a_supporting_citation() -> None:
    """A citation after a statement is its support, not its rebuttal.

    ``_claim_urls`` added every recorded verification passage whatever its
    stance, so a passage filed as contradicting the claim was printed as a
    numbered citation supporting the statement it disputes — and counted by
    ``reader_citations`` as a cited source, which is what the quality record's
    ``cited`` field and ``cited_sources`` report.
    """
    claim = _claim(
        verdict="insufficient_evidence",
        passages=[
            EvidencePassage(
                source_url=SOURCE_URL,
                source_title="QEC 2025",
                locator="p. 1",
                excerpt="The study reports the fall.",
                stance="supports",
            ),
            EvidencePassage(
                source_url=OTHER_URL,
                source_title="Rebuttal",
                locator="p. 2",
                excerpt="The rebuttal disputes the fall.",
                stance="contradicts",
            ),
        ],
    )
    composition = _evidence_composition(
        claims=[claim],
        sources=[_source(), _source(url=OTHER_URL, title="Rebuttal")],
        summary=[_stated(claim_ids=[claim.claim_id], source_urls=[SOURCE_URL])],
        sections=[],
        constraints=[],
    )
    statement = composition.summary[0].statement
    assert statement is not None

    urls = statement_citation_urls(statement, composition)

    assert SOURCE_URL in urls
    assert OTHER_URL not in urls
    assert OTHER_URL not in {
        citation.url for citation in reader_citations(composition)
    }


def test_a_contradicted_verdicts_evidence_is_not_a_supporting_citation() -> None:
    """A cluster's rebuttal citations do not support the statement either.

    ``_cluster_urls`` unioned ``verdict_evidence`` across every recorded
    verdict, so the URL filed under a ``contradicted`` verdict was printed
    after the statement as one of its citations.
    """
    claim = _claim()
    cluster = _cluster(
        verdicts=["verified", "contradicted"],
        verdict_evidence={"verified": [THIRD_URL], "contradicted": [OTHER_URL]},
    )
    composition = _evidence_composition(
        claims=[claim],
        claim_clusters={"cluster-1": cluster},
        sources=[
            _source(),
            _source(url=THIRD_URL, title="Independent review"),
            _source(url=OTHER_URL, title="Rebuttal"),
        ],
        summary=[_stated(statement=_statement(cluster_ids=["cluster-1"]))],
        sections=[],
        constraints=[],
    )
    statement = composition.summary[0].statement
    assert statement is not None

    urls = statement_citation_urls(statement, composition)

    assert THIRD_URL in urls
    assert OTHER_URL not in urls
    assert OTHER_URL not in {
        citation.url for citation in reader_citations(composition)
    }


def test_a_contested_statement_cites_the_source_that_disputes_it() -> None:
    """§2.4: an attributed or contested point cites its contradiction.

    The stance filter belongs to statements the report presents as
    *supporting*. A statement presented as contested must carry the source
    that disputes it — that is the citation a reader checks the disagreement
    against — and a cluster's ``contradicted`` verdict evidence is that
    record. Excluding both unconditionally left a contested bullet whose every
    citation supported it, with the refuting source recorded and unpublished.
    """
    claim = _claim(
        verdict="contradicted",
        badge="contested",
        passages=[
            EvidencePassage(
                source_url=SOURCE_URL,
                source_title="QEC 2025",
                locator="p. 1",
                excerpt="The study reports the fall.",
                stance="supports",
            ),
            EvidencePassage(
                source_url=OTHER_URL,
                source_title="Rebuttal",
                locator="p. 2",
                excerpt="The rebuttal disputes the fall.",
                stance="contradicts",
            ),
        ],
    )
    composition = _evidence_composition(
        claims=[claim],
        sources=[_source(), _source(url=OTHER_URL, title="Rebuttal")],
        summary=[
            _stated(
                claim_ids=[claim.claim_id],
                source_urls=[SOURCE_URL],
                statement=_statement(
                    "Break-even was reached, and disputed.",
                    mode="contested",
                    evidence_ids=[],
                ),
            )
        ],
        sections=[],
        constraints=[],
    )
    statement = composition.summary[0].statement
    assert statement is not None

    urls = statement_citation_urls(statement, composition)

    assert SOURCE_URL in urls
    assert OTHER_URL in urls
    assert OTHER_URL in {
        citation.url for citation in reader_citations(composition)
    }


def test_a_contested_statement_cites_a_contradicted_verdicts_evidence() -> None:
    """The cluster half of the same rule: a contested point keeps its rebuttal."""
    claim = _claim(verdict="contradicted", badge="contested")
    cluster = _cluster(
        verdicts=["contradicted"],
        verdict_evidence={"contradicted": [OTHER_URL]},
        source_urls=[SOURCE_URL],
    )
    composition = _evidence_composition(
        claims=[claim],
        claim_clusters={"cluster-1": cluster},
        sources=[_source(), _source(url=OTHER_URL, title="Rebuttal")],
        summary=[
            _stated(
                claim_ids=[claim.claim_id],
                source_urls=[SOURCE_URL],
                statement=_statement(
                    "Break-even was reached, and disputed.",
                    mode="contested",
                    cluster_ids=["cluster-1"],
                    evidence_ids=[],
                ),
            )
        ],
        sections=[],
        constraints=[],
    )
    statement = composition.summary[0].statement
    assert statement is not None

    urls = statement_citation_urls(statement, composition)

    assert OTHER_URL in urls
    assert OTHER_URL in {
        citation.url for citation in reader_citations(composition)
    }


def test_the_backmatter_boundary_is_the_last_methodology_heading() -> None:
    """A statement naming the heading must not move the measured boundary."""
    body = "# Report\n\n- The source's own ## Methodology section is quoted.\n\n" + (
        "x" * 400
    )
    backmatter = "\n\n## Methodology\n\n- counts\n\n## References\n\n1. Source"
    rendered = body + backmatter
    boundary = rendered.rfind("## Methodology")

    # The body names the heading, so the first occurrence is inside the body:
    # measuring from it would count the body as backmatter.
    assert rendered.find("## Methodology") < boundary
    assert backmatter_ratio(rendered) == pytest.approx(
        len(rendered[boundary:]) / len(rendered)
    )


def test_an_atom_dimension_record_lists_only_the_fields_that_are_filled() -> None:
    """A period is not evidence of a forecast status."""
    period_only = AtomicProposition(text="x", observation_period="2026")
    both = AtomicProposition(
        text="x", observation_period="2026", forecast_status="observed"
    )

    assert answered_atom_dimensions([period_only]) == ["observation_period"]
    assert answered_atom_dimensions([both]) == [
        "observation_period",
        "forecast_status",
    ]


def test_an_unknown_answer_form_falls_back_to_the_form_its_heading_names() -> None:
    """The heading and the table cannot disagree about what the section is."""
    composition = _composition().model_copy(update={"answer_kind": "estimate"})
    reader = render_reader_report(composition)

    assert reader_sections("estimate")[1] == DEFAULT_ANSWER_HEADING
    assert DEFAULT_ANSWER_HEADING in reader
    assert "(no constraint was ranked for this pass)" in reader


def test_every_evidence_badge_has_a_reader_label() -> None:
    """A badge without a label would print its raw enum string to the reader.

    ``_evidence_strength`` falls back to the badge itself, so adding a value
    to ``Claim.evidence_status`` without adding it to ``EVIDENCE_BADGE_LABELS``
    is the one way this surface can regress silently.
    """
    union = get_args(Claim.model_fields["evidence_status"].annotation)
    badges = {value for member in union for value in get_args(member)}

    assert badges
    assert badges <= set(EVIDENCE_BADGE_LABELS)
    # The no-badge case is a label too: a claim judged with nothing to
    # classify is not the same as a claim with no row.
    assert "" in EVIDENCE_BADGE_LABELS


# --- helpers ------------------------------------------------------------------


def _section_body(markdown: str, heading: str) -> str:
    """The text between ``heading`` and the next H2 heading (or the end)."""
    start = markdown.index(heading)
    tail = markdown[start + len(heading) :]
    match = re.search(r"(?m)^## ", tail)
    return tail[: match.start()] if match else tail


def _table_rows(body: str) -> list[str]:
    return [line for line in body.splitlines() if line.startswith("| ")]


def _cells(row: str) -> list[str]:
    """One Markdown table row as its stripped cells.

    Test-local: every row asserted through it is built from fixture text with
    no escaped pipe, so splitting on the delimiter is exact.
    """
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _reader_points(composition: ReportComposition) -> list[ReportPoint]:
    points: list[ReportPoint] = [*composition.summary, *composition.constraints]
    for section in composition.sections:
        points.extend(section.points)
    return points


# --- the quality record -------------------------------------------------------
#
# The third published artifact: one JSON document that makes the other two
# auditable. It carries IDs rather than prose, hashes the bytes it describes
# without describing itself, and must serialize everything a replay needs to
# resolve a cited statement back to the exact evidence it rests on.


def _record_state(
    composition: ReportComposition, **fields: object
) -> ResearchState:
    """The state the terminal finalizer holds when it publishes the set."""
    state = ResearchState(
        session_id=composition.session_id,
        original_question=composition.question,
        composition=composition,
        report=render_reader_report(composition),
        report_evidence=render_evidence_ledger(composition),
        quality_contract_version=QUALITY_CONTRACT_VERSION,
        **fields,  # type: ignore[arg-type]
    )
    return state.model_copy(
        update={"quality": compute_report_quality(state, composition)}
    )


def _read(url: str, text: str, title: str = "Grid Storage Outlook 2024") -> ReadRecord:
    """One complete read of a page, built through the shared read producer."""
    return build_read_record(
        session_id="session-1",
        reader="web_scraper",
        requested_url=url,
        resolved_url=url,
        title=title,
        retrieved_at=EXTRACTED_AT,
        text=text,
        passages={"p-1": text},
    )


def _artifact_texts(composition: ReportComposition) -> dict[str, str]:
    return {
        "reader_markdown": render_reader_report(composition),
        "evidence_markdown": render_evidence_ledger(composition),
    }


def test_the_quality_record_replays_every_statement_from_its_own_ids() -> None:
    """Every cited statement resolves inside the record, with no prose parsing.

    A replay reads the record alone: the statement rows it publishes, the claim
    clusters and claims those rows name, the evidence units behind them, and the
    source rows the reader's citations point at. Every link is asserted on the
    serialized rows rather than on the composition that produced them — a record
    whose statement rows carry no evidence ids is exactly the regression this
    test exists to catch, and reading the composition would not see it.
    """
    composition = _evidence_composition(
        sources=[
            _source(),
            _source(url=THIRD_URL, title="Independent review"),
        ]
    )
    state = _record_state(composition)

    record = render_quality_record(
        state, composition, None, artifacts=_artifact_texts(composition)
    )

    statements = {row["statement_id"]: row for row in record["statements"]}
    evidence_rows = {row["evidence_id"]: row for row in record["evidence"]}
    cluster_rows = {row["cluster_id"]: row for row in record["claim_clusters"]}
    claim_rows = {row["claim_id"] for row in record["claims"]}
    source_rows = {row["url"] for row in record["sources"]}

    assert set(statements) == {
        statement.statement_id for statement in composition.statements
    }
    assert evidence_rows
    assert cluster_rows
    # At least one statement row carries links: an emptied list on every row
    # would otherwise satisfy every "is a subset" assertion below.
    assert any(row["evidence_ids"] for row in record["statements"])
    for row in record["statements"]:
        assert set(row["evidence_ids"]) <= set(evidence_rows), row["statement_id"]
        assert set(row["claim_cluster_ids"]) <= set(cluster_rows), (
            row["statement_id"]
        )
        for cluster_id in row["claim_cluster_ids"]:
            cluster = cluster_rows[cluster_id]
            assert set(cluster["member_claim_ids"]) <= claim_rows
            assert set(cluster["evidence_ids"]) <= set(evidence_rows)
        for evidence_id in row["evidence_ids"]:
            assert evidence_rows[evidence_id]["source_url"] in source_rows
    cited = {citation.url for citation in reader_citations(composition)}
    assert cited
    # The URLs reached through those rows are source rows the reader cites.
    cited_through_statements = {
        evidence_rows[evidence_id]["source_url"]
        for row in record["statements"]
        for evidence_id in row["evidence_ids"]
    }
    assert cited_through_statements
    assert cited_through_statements <= source_rows
    assert cited_through_statements <= {
        row["url"] for row in record["sources"] if row["cited"]
    }
    # The reader's own reference numbers resolve to the same source rows.
    assert {
        citation.url for citation in reader_citations(composition)
    } == {row["url"] for row in record["sources"] if row["cited"]}


def test_the_quality_record_derives_the_coverage_id_lists_the_snapshot_lacks() -> (
    None
):
    """The record's target id lists are derived on the snapshot path too.

    The record prefers the snapshot the gates judged, which is where the
    denominators come from — but that snapshot carries no ``accounted_target_ids``
    and no list of every unanswered required target, only the unanswered
    critical ones and the unaccounted ones. Re-deriving them from those two
    fields published an empty "accounted" list and omitted an obligation that
    ended with a recorded denial, so a replay reading the record saw a target
    nobody could account for, or did not see it at all. The id lists come from
    the same pure function that produced the snapshot's own counts.
    """
    claim = _claim()
    sub_topics = [
        SubTopic(
            coverage_id="topic-01",
            title="Alpha",
            rationale="First.",
            search_queries=["alpha"],
            success_criteria=["alpha evidence"],
            priority=1,
            evidence_targets=[
                EvidenceTarget(
                    target_id="t1",
                    coverage_id="topic-01",
                    question="What did the pilot add?",
                    required_dimensions=["finding"],
                    required=True,
                    critical=False,
                    support_policy="primary_attribution",
                ),
                EvidenceTarget(
                    target_id="t-deferred",
                    coverage_id="topic-01",
                    question="What mechanism did the denied source report?",
                    required_dimensions=["mechanism"],
                    required=True,
                    critical=False,
                    support_policy="primary_attribution",
                ),
            ],
        )
    ]
    composition = _evidence_composition(
        claims=[claim],
        claim_clusters={"cluster-1": _cluster(claim_ids=[claim.claim_id])},
        sub_topics=sub_topics,
        summary=[
            ReportPoint(
                text="The pilot added 12 GW.",
                claim_ids=[claim.claim_id],
                source_urls=[SOURCE_URL],
                statement=ReportStatement(
                    statement_id="S001",
                    text="The pilot added 12 GW.",
                    mode="settled",
                    claim_cluster_ids=[claim.claim_id],
                    evidence_ids=["e1"],
                    target_ids=["t1"],
                    answered_dimensions=["finding"],
                ),
            )
        ],
        sections=[],
    )
    state = _record_state(
        composition,
        sub_topics=sub_topics,
        evidence_dispositions=[
            EvidenceDisposition(
                item_id="t-deferred",
                stage="access_denied",
                reason="every candidate for this target was denied",
                target_ids=["t-deferred"],
            )
        ],
    )
    assert state.quality is not None
    assert state.quality.unaccounted_target_ids == []
    assert state.quality.unanswered_critical_target_ids == []

    record = render_quality_record(state, composition, None)
    coverage = record["counts"]

    assert coverage["unanswered_required_target_ids"] == ["t-deferred"]
    assert coverage["accounted_target_ids"] == ["t-deferred"]
    assert coverage["unaccounted_target_ids"] == []
    # The scalars stay the snapshot's own measurement.
    assert coverage["required_targets"] == state.quality.required_targets
    assert coverage["answered_targets"] == state.quality.answered_targets


def test_the_works_count_is_the_identity_the_work_map_publishes() -> None:
    """The count and the map inside one record cannot disagree about a work.

    Section 2.3: an assessed source's ``work_id`` is resolved once per snapshot,
    with the anchors the Source Evaluator validated, and this record publishes
    it in ``work_keys``. Counting the works a second way — from the reads
    alone, with no anchors — resolves *less*: the DOI that joined a copy to its
    original was validated as an anchor on the source, and a re-typeset mirror
    never shared the original's bytes. The record then named one work in its map
    and reported two in its count.
    """
    work_id = "doi:10.1234/grid.2025"
    original = _read(SOURCE_URL, "Grid Storage Outlook 2024: 10 GW in 2024.")
    mirror = _read(
        OTHER_URL,
        "Grid Storage Outlook 2024, re-typeset: 10 GW in 2024.",
        title="Grid Storage Outlook 2024 (repository copy)",
    )
    composition = _evidence_composition(
        sources=[
            _source(url=SOURCE_URL, work_id=work_id),
            _source(
                url=OTHER_URL,
                title="Grid Storage Outlook 2024 (repository copy)",
                work_id=work_id,
                transport="mirror",
            ),
        ]
    )
    state = _record_state(
        composition,
        read_records={original.read_id: original, mirror.read_id: mirror},
    )

    # The reads alone cannot see the join, which is why the count may not be
    # re-derived from them: two rows, two keys.
    assert len(set(resolve_read_works([original, mirror]).values())) == 2

    record = render_quality_record(state, composition, None)

    assert set(record["work_keys"].values()) == {work_id}
    assert record["counts"]["unique_works"] == 1


def test_a_source_no_assessment_covers_is_its_own_work_entry() -> None:
    """An unresolved work stays its own entry; it is never folded into a peer.

    The map publishes the identity that was established, so an unscored source
    has no row in it. The count must not invent one for it either — by joining
    it to whatever source sits beside it, or by dropping it — because a source
    the run read is a work it retains whether or not anyone assessed it.
    """
    work_id = "doi:10.1234/grid.2025"
    reads = [
        _read(SOURCE_URL, "Grid Storage Outlook 2024: 10 GW in 2024."),
        _read(
            OTHER_URL,
            "Grid Storage Outlook 2024, re-typeset: 10 GW in 2024.",
            title="Grid Storage Outlook 2024 (repository copy)",
        ),
        _read(
            THIRD_URL,
            "Interconnection Queue 2024: 800 MW.",
            title="Interconnection Queue 2024",
        ),
    ]
    composition = _evidence_composition(
        sources=[
            _source(url=SOURCE_URL, work_id=work_id),
            _source(
                url=OTHER_URL,
                title="Grid Storage Outlook 2024 (repository copy)",
                work_id=work_id,
                transport="mirror",
            ),
            _source(
                url=THIRD_URL,
                title="Interconnection Queue 2024",
                status="unscored_cap",
                rationale="The per-run source cap was reached first.",
            ),
        ]
    )
    state = _record_state(
        composition, read_records={read.read_id: read for read in reads}
    )

    record = render_quality_record(state, composition, None)

    assert set(record["work_keys"]) == {SOURCE_URL, OTHER_URL}
    # One joined work, plus the unassessed source's own unresolved entry.
    assert record["counts"]["unique_works"] == 2


def test_the_quality_record_hashes_the_published_bytes_and_never_itself() -> None:
    """Artefact hashes are of the final bytes, and the JSON has no self-hash.

    A document cannot carry the digest of the bytes that contain that digest,
    so the quality JSON is deliberately outside its own ``artifacts`` map. The
    two Markdown digests are recomputed here from the exact strings published.
    """
    composition = _evidence_composition()
    state = _record_state(composition)
    texts = _artifact_texts(composition)

    record = render_quality_record(
        state, composition, None, artifacts=texts
    )
    encoded = json.dumps(record, sort_keys=True, ensure_ascii=False)

    assert set(record["artifacts"]) == {
        "reader_markdown",
        "evidence_markdown",
    }
    for name, text in texts.items():
        assert record["artifacts"][name] == hashlib.sha256(
            text.encode("utf-8")
        ).hexdigest()
    own_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    assert own_digest not in encoded
    assert "quality_json" not in record["artifacts"]


def test_the_semantic_fingerprint_moves_on_content_and_not_on_the_badge() -> None:
    """The record's fingerprint excludes exactly the generated badge.

    ``quality_status`` is presentation: the terminal finalizer rewrites it on
    the way out, and a judgement must survive that. Everything a review judges
    — content, references, targets — moves the fingerprint, so the record and
    the review cannot disagree about which report was judged.
    """
    composition = _evidence_composition()
    state = _record_state(composition)

    base = render_quality_record(state, composition, None)
    restamped = composition.model_copy(update={"quality_status": "accepted"})
    assert render_quality_record(state, restamped, None)["configuration"][
        "composition_fingerprint"
    ] == base["configuration"]["composition_fingerprint"]

    for field, value in (
        ("scope", "A materially different scope."),
        ("as_of", "2027-01-01T00:00:00+00:00"),
        ("question", "A different question entirely?"),
    ):
        changed = composition.model_copy(update={field: value})
        assert render_quality_record(state, changed, None)["configuration"][
            "composition_fingerprint"
        ] != base["configuration"]["composition_fingerprint"], field

    retargeted = composition.model_copy(
        update={
            "sub_topics": [
                *composition.sub_topics,
                SubTopic(
                    coverage_id="topic-extra",
                    title="A target the review never saw",
                    rationale="Added after the review.",
                    search_queries=["new query"],
                    success_criteria=["A read source answers it."],
                    priority=1,
                ),
            ]
        }
    )
    assert render_quality_record(state, retargeted, None)["configuration"][
        "composition_fingerprint"
    ] != base["configuration"]["composition_fingerprint"]


def test_the_quality_record_is_bounded_json_without_page_payloads() -> None:
    """It is JSON, it is bounded, and a whole extracted page never enters it."""
    page = "the complete extracted page text " * 200
    composition = _evidence_composition(
        evidence_units={"e1": _unit(excerpt=page)}
    )
    state = _record_state(composition)

    record = render_quality_record(state, composition, None)
    encoded = json.dumps(record, sort_keys=True)

    assert page not in encoded
    for row in record["evidence"]:
        assert len(row["excerpt"]) <= QUALITY_RECORD_EXCERPT_CHARS
    for row in record["claims"]:
        assert len(row["text"]) <= QUALITY_RECORD_TEXT_CHARS
    assert json.loads(encoded) == record


@pytest.mark.asyncio
async def test_the_quality_record_exports_the_identity_the_sources_carry() -> None:
    """A replay reads work identity from the record, never re-derives it.

    The sources come from the real assessment service: a DOI original, its
    byte-identical DOI-less mirror, and a story stating the report's DOI as
    its data source. The record must carry each source's aliases, basis,
    status, issuer, lineage, and validated anchors — and its ``work_keys``
    must be the sources' own work ids, not an anchor-less re-resolution of the
    reads that would split the original from its mirror.
    """
    from deep_research.agents.evidence import build_read_record
    from deep_research.agents.source_evaluator import (
        SourceScoreDraft,
        SourceScoresDraft,
        assess_new_sources,
    )
    from tests.agent_fakes import ScriptedCompleter

    report_text = (
        "Grid Storage Outlook. Published by Example Lab on 2026-01-15. "
        "Example Lab measured that 1,200 MW of interconnection capacity was "
        "withheld during 2024. doi:10.1234/grid.2025"
    )
    story_text = (
        "Queue backlog, by the numbers. Published by News Daily on "
        "2026-02-02. Our analysis of the Example Lab report "
        "(doi:10.1234/grid.2025) finds 1,200 MW was withheld during 2024."
    )
    original_url = "https://lab.example/report"
    mirror_url = "https://mirror.example/grid-outlook"
    story_url = "https://news.example/queue-story"

    def read(url: str, title: str, text: str):  # type: ignore[no-untyped-def]
        return build_read_record(
            session_id="session-1",
            reader="web_scraper",
            requested_url=url,
            resolved_url=url,
            title=title,
            retrieved_at=EXTRACTED_AT,
            text=text,
            passages={"chunk-0": text},
            extraction_complete=True,
        )

    def draft(url: str, **fields: object) -> SourceScoreDraft:
        return SourceScoreDraft(
            url=url,
            authority_score=0.9,
            recency_score=0.8,
            relevance_score=0.9,
            rationale="Assessed from the read.",
            **fields,  # type: ignore[arg-type]
        )

    reads = [
        read(original_url, "Grid Storage Outlook", report_text),
        read(mirror_url, "Grid Storage Outlook", report_text),
        read(story_url, "Queue backlog, by the numbers", story_text),
    ]
    lab = {"source_role": "original_report", "issuer": "Example Lab"}
    sources = await assess_new_sources(
        ScriptedCompleter(
            outputs=[
                SourceScoresDraft(
                    sources=[
                        draft(
                            original_url,
                            transport_relation="original",
                            doi="10.1234/grid.2025",
                            **lab,
                        ),
                        draft(mirror_url, transport_relation="mirror", **lab),
                        draft(
                            story_url,
                            source_role="independent_research",
                            transport_relation="original",
                            issuer="News Daily",
                            derived_from=["10.1234/grid.2025"],
                        ),
                    ]
                )
            ]
        ),
        reads,
    )
    composition = _evidence_composition(sources=[_source(), *sources])
    state = _record_state(
        composition, read_records={item.read_id: item for item in reads}
    )

    record = json.loads(
        json.dumps(render_quality_record(state, composition, None), sort_keys=True)
    )

    rows = {row["url"]: row for row in record["sources"]}
    original, mirror, story = (
        rows[original_url],
        rows[mirror_url],
        rows[story_url],
    )
    report_hash = f"sha256:{reads[0].content_sha256}"
    assert original["work_id"] == mirror["work_id"] == "doi:10.1234/grid.2025"
    for row in (original, mirror):
        assert row["identity_status"] == "known"
        assert row["identity_basis"]
        assert set(row["work_aliases"]) == {"doi:10.1234/grid.2025", report_hash}
        assert row["issuer_id"] == "example lab"
        assert row["derives_from_work_ids"] == []
    assert original["identity_anchors"]["doi"] == "10.1234/grid.2025"
    assert original["identity_anchors"]["issuer"] == "Example Lab"
    assert "doi" not in mirror["identity_anchors"]
    assert story["identity_status"] == "known"
    assert story["derives_from_work_ids"] == ["doi:10.1234/grid.2025"]
    # The anchor is the cited work's id, the form a lineage comparison reads.
    assert story["identity_anchors"]["derived_from"] == ["doi:10.1234/grid.2025"]
    # One identity: the record's url -> work map is the sources' own.
    identified = {row["url"]: row["work_id"] for row in record["sources"] if row["work_id"]}
    assert identified
    assert {
        url: key for url, key in record["work_keys"].items() if url in identified
    } == identified


def test_every_corroboration_badge_has_its_own_distinct_count() -> None:
    """Four labels, four counts, and they add up to the claims that were checked.

    "Sixteen claims were checked" is not "sixteen claims are verified": the
    counts below are the reader-facing reading of the badge each canonical
    claim actually recorded, and a claim with no recorded classification is
    counted as not established rather than as corroborated.
    """
    claims = [
        _claim(),
        _claim(
            text="A claim with primary-source attribution.",
            verdict="insufficient_evidence",
        ),
        _claim(text="A contested claim.", verdict="contradicted", badge="contested"),
        _claim(text="A claim with no recorded badge.", verdict="unverified"),
    ]

    counts = evidence_status_counts(claims)

    assert counts == {
        "corroborated": 1,
        "primary_attributed": 1,
        "contested": 1,
        "not_established": 1,
    }
    assert sum(counts.values()) == len(claims)
    assert set(counts) == set(EVIDENCE_STATUS_LABELS)


def test_a_contradicted_claim_is_counted_the_way_the_reader_reads_it() -> None:
    """One claim, one reading: the counts follow the reader's own contract.

    The badge is stamped before adjudication finishes, so the claim an
    independent source contradicted can still carry ``verified_pair``. Counting
    the badge alone published it as "independently corroborated" while the
    reader's statement contract called the same claim contested — two surfaces
    disagreeing about one claim. Section 2.1: verified requires that no
    unresolved material contradiction defeats settlement.
    """
    contradicted = _claim(
        text="An independent source contradicted this.",
        verdict="contradicted",
        badge="verified_pair",
    )
    settled = _claim()

    assert evidence_status_counts([contradicted, settled]) == {
        "corroborated": 1,
        "primary_attributed": 0,
        "contested": 1,
        "not_established": 0,
    }
    assert statement_mode_for_claims([contradicted]) == "contested"
    assert statement_mode_for_claims([settled]) == "settled"
    assert (
        evidence_status_bucket(
            contradicted.evidence_status, verdict=contradicted.verdict
        )
        == "contested"
    )
    assert (
        evidence_status_bucket(settled.evidence_status, verdict=settled.verdict)
        == "corroborated"
    )
