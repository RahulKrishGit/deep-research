"""Tests for the pure Markdown report skeleton."""

from __future__ import annotations

import pytest

from deep_research.agents.report import (
    LIMITATION_REASONS,
    REPORT_SECTIONS,
    REPORT_TITLE_PREFIX,
    Citation,
    ReportSection,
    assemble_report,
    build_citation_index,
    citation_markers,
    render_citations,
    render_findings,
    render_limitations,
    render_source_appendix,
    render_uncertain_claims,
    render_verified_claims,
)
from deep_research.utils.types import Claim, ScoredSource


def _source(
    *,
    url: str = "https://example.org/a",
    title: str = "QEC 2025",
    overall: float = 0.76,
    low_confidence: bool = False,
    rationale: str = "Peer-reviewed and corroborated.",
) -> ScoredSource:
    return ScoredSource(
        url=url,
        title=title,
        authority_score=0.8,
        recency_score=0.7,
        relevance_score=0.9,
        corroboration_score=0.5,
        overall_score=overall,
        rationale=rationale,
        low_confidence=low_confidence,
    )


def _claim(
    *,
    text: str = "Logical error rates fell below break-even in 2025.",
    urls: list[str] | None = None,
    verdict: str = "verified",
    confidence: float = 0.8,
    contradictions: list[str] | None = None,
) -> Claim:
    return Claim(
        text=text,
        source_urls=urls or ["https://example.org/a"],
        verdict=verdict,
        confidence=confidence,
        evidence=["An independent review states the same figure."],
        contradictions=contradictions or [],
    )


def test_citation_numbers_run_sources_first_then_claim_sources() -> None:
    index = build_citation_index(
        [_source(url="https://example.org/a"), _source(url="https://other.test/b")],
        [_claim(urls=["https://third.test/c", "https://example.org/a"])],
    )

    assert [(citation.number, citation.url) for citation in index] == [
        (1, "https://example.org/a"),
        (2, "https://other.test/b"),
        (3, "https://third.test/c"),
    ]


def test_citation_numbers_are_assigned_to_canonical_urls() -> None:
    index = build_citation_index(
        [_source(url="https://WWW.Example.ORG/a/")],
        [_claim(urls=["https://example.org/a"])],
    )

    assert len(index) == 1
    assert index[0].url == "https://example.org/a"


def test_markers_render_sorted_and_deduplicated() -> None:
    index = build_citation_index(
        [_source(url="https://example.org/a"), _source(url="https://other.test/b")],
        [],
    )

    assert (
        citation_markers(
            ["https://other.test/b", "https://example.org/a", "https://other.test/b"],
            index,
        )
        == "[1][2]"
    )


def test_an_uncited_url_is_never_marked() -> None:
    index = build_citation_index([_source()], [])

    assert citation_markers(["https://invented.test/x"], index) == ""


def test_citations_render_one_numbered_line_each() -> None:
    index = build_citation_index([_source(title="QEC 2025")], [])

    assert "1. QEC 2025 — https://example.org/a" in render_citations(index)
    assert render_citations([]) == "(no sources were cited)"


def test_the_appendix_marks_low_confidence_sources_and_escapes_pipes() -> None:
    index = build_citation_index(
        [_source(), _source(url="https://weak.test/b", title="A | B")], []
    )
    rendered = render_source_appendix(
        [
            _source(),
            _source(
                url="https://weak.test/b",
                title="A | B",
                overall=0.08,
                low_confidence=True,
                rationale="Anonymous blog.",
            ),
        ],
        index,
    )

    assert "| # | Source | Score | Confidence | Assessment |" in rendered
    assert "| 1 | QEC 2025 (https://example.org/a) | 0.76 | normal |" in rendered
    assert "| 2 | A \\| B (https://weak.test/b) | 0.08 | low |" in rendered
    assert render_source_appendix([], index) == "(no sources were evaluated)"


def test_findings_render_their_citation_line() -> None:
    index = build_citation_index([_source()], [])
    rendered = render_findings(
        [
            ReportSection(
                title="Error correction",
                body="Break-even was reached.",
                source_urls=["https://example.org/a"],
            ),
            ReportSection(title="Outlook", body="Scaling remains open."),
        ],
        index,
    )

    assert "### Error correction" in rendered
    assert "Sources: [1]" in rendered
    assert "Sources: none cited" in rendered
    assert render_findings([], index) == "(no findings were reported)"


def test_verified_claims_are_always_cited() -> None:
    index = build_citation_index([_source()], [])
    rendered = render_verified_claims([_claim(), _claim(verdict="unverified")], index)

    assert rendered.count("- ") == 1
    assert "[1] (confidence 0.80)" in rendered
    assert render_verified_claims([], index) == "(no claim reached a verified verdict)"


def test_unverified_claims_are_grouped_away_from_strong_findings() -> None:
    index = build_citation_index([_source()], [])
    rendered = render_uncertain_claims(
        [
            _claim(),
            _claim(text="Cost fell tenfold.", verdict="contradicted",
                   contradictions=["A vendor report disagrees."]),
            _claim(text="Adoption is broad.", verdict="unverified"),
            _claim(text="Latency improved.", verdict="insufficient_evidence"),
        ],
        index,
    )

    assert "### Contradicted by independent sources" in rendered
    assert "1 contradicting passage(s)" in rendered
    assert "### Not addressed by independent sources" in rendered
    assert "### Insufficient independent evidence" in rendered
    assert "Logical error rates" not in rendered
    assert render_uncertain_claims([], index) == (
        "(no unresolved claims were recorded)"
    )


def test_limitations_render_enumerated_reasons_only() -> None:
    rendered = render_limitations(["errors_recorded", "no_verified_claims"])

    assert rendered.count("- ") == 2
    assert LIMITATION_REASONS["errors_recorded"] in rendered
    assert render_limitations([]) == "No limitations were recorded for this pass."
    with pytest.raises(ValueError, match="limitation reason"):
        render_limitations(["because"])


def test_a_report_always_carries_every_required_section_in_order() -> None:
    index = build_citation_index([_source()], [_claim()])
    markdown = assemble_report(
        question="  How mature is quantum error correction?  ",
        summary="Break-even was reached in 2025.",
        sections=[
            ReportSection(
                title="Error correction",
                body="Break-even was reached.",
                source_urls=["https://example.org/a"],
            )
        ],
        claims=[_claim()],
        sources=[_source()],
        index=index,
        limitations=["errors_recorded"],
        uncertainty_notes="Vendor numbers remain unaudited.",
    )

    positions = [markdown.index(heading) for heading in REPORT_SECTIONS]
    assert positions == sorted(positions)
    assert markdown.startswith(
        f"{REPORT_TITLE_PREFIX}How mature is quantum error correction?"
    )
    assert "Vendor numbers remain unaudited." in markdown
    assert "1. QEC 2025 — https://example.org/a" in markdown
    assert markdown.endswith("\n")


def test_an_evidence_free_report_still_carries_every_section() -> None:
    markdown = assemble_report(
        question="What is known?",
        summary="   ",
        sections=[],
        claims=[],
        sources=[],
        index=[],
        limitations=[],
    )

    for heading in REPORT_SECTIONS:
        assert heading in markdown
    assert "(no executive summary was produced)" in markdown
    assert "No limitations were recorded for this pass." in markdown


def test_a_citation_object_rejects_a_zero_number() -> None:
    with pytest.raises(ValueError):
        Citation(number=0, url="https://example.org/a", title="A")
