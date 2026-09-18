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

import re
from typing import get_args

import pytest

from deep_research.agents.identity import claim_fingerprint, finding_fingerprint
from deep_research.agents.report import (
    DEFAULT_READER_WORD_LIMIT,
    EVIDENCE_SECTIONS,
    EVIDENCE_TITLE_PREFIX,
    LIMITATION_REASONS,
    LIMITATION_TOPICS,
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
    reader_citations,
    reader_word_count,
    reader_word_limit,
    render_citations,
    render_evidence_ledger,
    render_limitations,
    render_reader_report,
    render_statement_map,
    report_as_of,
    report_scope,
    statement_source_urls,
    validate_report_statements,
)
from deep_research.utils.types import (
    AtomicProposition,
    Claim,
    ClaimCluster,
    EvidencePassage,
    EvidenceTarget,
    EvidenceUnit,
    Finding,
    ReportAnswerRow,
    ReportStatement,
    ResearchError,
    ResearchEvent,
    ScoredSource,
    SourceTemporal,
    StatementMode,
    SubTopic,
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
) -> Claim:
    return Claim(
        claim_id=claim_fingerprint(text),
        text=text,
        source_urls=urls or [SOURCE_URL],
        verdict=verdict,
        evidence_status=(
            "verified_pair"
            if verdict == "verified"
            else "source_supported"
            if verdict == "insufficient_evidence"
            else None
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


def test_the_constraint_table_carries_the_five_decision_columns() -> None:
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
        "| Constraint | Deployment mechanism | Geography | Evidence strength "
        "| Confidence |"
    ) in table
    assert "| Cordon tolling inside the central business district [1] " in table
    assert "| area licence with camera enforcement | London |" in table
    assert "| not stated | not stated |" in table
    # Confidence is the weakest confidence behind the row, rendered locally.
    assert "| 0.80 |" in table


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
    reader = render_reader_report(composition)

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
