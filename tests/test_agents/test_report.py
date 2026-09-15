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

import pytest

from deep_research.agents.identity import claim_fingerprint, finding_fingerprint
from deep_research.agents.report import (
    EVIDENCE_SECTIONS,
    EVIDENCE_TITLE_PREFIX,
    LIMITATION_REASONS,
    QUALITY_STATUS_NOT_GATED,
    REPORT_SECTIONS,
    REPORT_SUMMARY_FALLBACK,
    REPORT_TITLE_PREFIX,
    Citation,
    ReportComposition,
    ReportConstraint,
    ReportPoint,
    ReportSection,
    canonical_claims,
    canonical_sources,
    citation_markers,
    reader_citations,
    render_citations,
    render_evidence_ledger,
    render_limitations,
    render_reader_report,
    report_as_of,
    report_scope,
)
from deep_research.utils.types import (
    Claim,
    EvidencePassage,
    Finding,
    ResearchError,
    ResearchEvent,
    ScoredSource,
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
) -> ScoredSource:
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
        )
    return ScoredSource(
        url=url,
        title=title,
        rationale=rationale,
        evaluation_status=status,
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
) -> Claim:
    return Claim(
        claim_id=claim_fingerprint(text),
        text=text,
        source_urls=urls or [SOURCE_URL],
        verdict=verdict,
        confidence=confidence,
        evidence=["An independent review states the same figure."],
        contradictions=contradictions or [],
        verification_evidence=passages or [],
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
    reader, ledger = render_reports(pathological_composition())

    for source_url in (
        "https://example.test/source-001",
        "https://example.test/source-042",
    ):
        assert reader.count(source_url) <= 1
        assessment = _section_body(ledger, "## Source assessment")
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


# --- helpers ------------------------------------------------------------------


def _section_body(markdown: str, heading: str) -> str:
    """The text between ``heading`` and the next H2 heading (or the end)."""
    start = markdown.index(heading)
    tail = markdown[start + len(heading) :]
    match = re.search(r"(?m)^## ", tail)
    return tail[: match.start()] if match else tail


def _table_rows(body: str) -> list[str]:
    return [line for line in body.splitlines() if line.startswith("| ")]


def _reader_points(composition: ReportComposition) -> list[ReportPoint]:
    points: list[ReportPoint] = [*composition.summary, *composition.constraints]
    for section in composition.sections:
        points.extend(section.points)
    return points
