"""Markdown report assembly — pure, offline rendering.

Nothing here performs I/O, reads a clock, or calls a provider, so a report
is a deterministic function of the evidence and prose handed to it. That is
what makes "the report always carries its required sections" and "every
verified claim is cited" testable without a provider, and true even when
the provider call failed.

Citation convention: one number per canonical source URL, assigned by
``build_citation_index`` — evaluated sources in order first, then claim
sources not already numbered. Every marker rendered into the report
resolves to a line in its Citations section; a URL with no number is never
marked.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field

from deep_research.agents.sources import normalize_source_url
from deep_research.agents.steps import summarize_text
from deep_research.utils.types import Claim, ContractModel, ScoredSource

REPORT_TITLE_PREFIX = "# Research report: "

# The H2 headings every report carries, in order. Emitted unconditionally,
# with an explicit placeholder body when empty: a reader must never have to
# tell "nothing to report" apart from "this section was dropped".
REPORT_SECTIONS = (
    "## Executive summary",
    "## Findings",
    "## Verified claims",
    "## Uncertainty and conflicting evidence",
    "## Limitations",
    "## Citations",
    "## Source appendix",
)

# Enumerated, project-generated limitation reasons. Never provider text:
# these strings reach the report body and ResearchEvent.metadata.
LIMITATION_REASONS = {
    "errors_recorded": (
        "Some steps of this research pass failed; sections of this report "
        "may be incomplete."
    ),
    "max_iterations_reached": (
        "The refinement budget was exhausted before the critic accepted "
        "the report."
    ),
    "no_sources_evaluated": (
        "No source behind these findings was scored, so source quality is "
        "unknown."
    ),
    "low_confidence_sources": (
        "Some sources behind these findings were flagged low confidence."
    ),
    "no_verified_claims": (
        "No claim was verified against a source independent of the one "
        "that made it."
    ),
    "contradicted_claims": (
        "At least one claim was contradicted by an independent source."
    ),
    "report_generation_failed": (
        "The model provider failed while this report was written; only the "
        "recorded evidence is included."
    ),
}

_APPENDIX_RATIONALE_CHARS = 200

# Verdict groups for the uncertainty section, in the order a reader should
# meet them: the evidence that argues against the report comes first.
_UNCERTAIN_VERDICTS = (
    ("contradicted", "Contradicted by independent sources"),
    ("unverified", "Not addressed by independent sources"),
    ("insufficient_evidence", "Insufficient independent evidence"),
)


class Citation(ContractModel):
    """One numbered source reference."""

    number: int = Field(ge=1)
    url: str = Field(min_length=1)
    title: str = Field(min_length=1)


class ReportSection(ContractModel):
    """One validated narrative section of the report body.

    ``source_urls`` has already been checked against the citation index by
    the time a section reaches here; rendering never validates.
    """

    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    source_urls: list[str] = Field(default_factory=list)


def build_citation_index(
    sources: Sequence[ScoredSource],
    claims: Sequence[Claim],
) -> list[Citation]:
    """Number every canonical URL this report may cite, sources first.

    A source's own title is preferred; a URL that only ever appeared on a
    claim is titled with the URL itself, because nothing scored it.
    """
    titles: dict[str, str] = {}
    for source in sources:
        url = normalize_source_url(source.url)
        if url:
            titles.setdefault(url, source.title)
    for claim in claims:
        for raw in claim.source_urls:
            url = normalize_source_url(raw)
            if url:
                titles.setdefault(url, url)
    return [
        Citation(number=number, url=url, title=title)
        for number, (url, title) in enumerate(titles.items(), start=1)
    ]


def _lookup(index: Sequence[Citation]) -> dict[str, int]:
    return {citation.url: citation.number for citation in index}


def citation_markers(
    urls: Sequence[str],
    index: Sequence[Citation],
) -> str:
    """Render ``[1][3]`` for the URLs that carry a citation number."""
    numbers = _lookup(index)
    found = {
        numbers[normalize_source_url(url)]
        for url in urls
        if normalize_source_url(url) in numbers
    }
    return "".join(f"[{number}]" for number in sorted(found))


def render_citations(index: Sequence[Citation]) -> str:
    """Render the numbered citation list the markers point at."""
    lines = [
        f"{citation.number}. {citation.title} — {citation.url}"
        for citation in index
    ]
    return "\n".join(lines) or "(no sources were cited)"


def _cell(text: str) -> str:
    """Collapse a value onto one Markdown table cell.

    Pipes are escaped rather than dropped: a title containing ``|`` would
    otherwise silently split the row into extra columns.
    """
    return " ".join(text.split()).replace("|", "\\|")


def render_source_appendix(
    sources: Sequence[ScoredSource],
    index: Sequence[Citation],
) -> str:
    """Render one appendix row per scored source, weak ones visible."""
    if not sources:
        return "(no sources were evaluated)"
    numbers = _lookup(index)
    lines = [
        "| # | Source | Score | Confidence | Assessment |",
        "| --- | --- | --- | --- | --- |",
    ]
    for source in sources:
        number = numbers.get(normalize_source_url(source.url))
        marker = str(number) if number is not None else "-"
        confidence = "low" if source.low_confidence else "normal"
        rationale = _cell(
            summarize_text(source.rationale, limit=_APPENDIX_RATIONALE_CHARS)
        )
        lines.append(
            f"| {marker} | {_cell(source.title)} ({source.url}) "
            f"| {source.overall_score:.2f} | {confidence} | {rationale} |"
        )
    return "\n".join(lines)


def render_findings(
    sections: Sequence[ReportSection],
    index: Sequence[Citation],
) -> str:
    """Render the narrative sections, each closing with its citations."""
    blocks: list[str] = []
    for section in sections:
        cited = citation_markers(section.source_urls, index) or "none cited"
        blocks.append(
            f"### {section.title}\n\n{section.body}\n\nSources: {cited}"
        )
    return "\n\n".join(blocks) or "(no findings were reported)"


def render_verified_claims(
    claims: Sequence[Claim],
    index: Sequence[Citation],
) -> str:
    """Render only the claims that reached ``verified``, each cited."""
    lines: list[str] = []
    for claim in claims:
        if claim.verdict != "verified":
            continue
        markers = citation_markers(claim.source_urls, index)
        suffix = f" {markers}" if markers else ""
        lines.append(
            f"- {claim.text}{suffix} (confidence {claim.confidence:.2f})"
        )
    return "\n".join(lines) or "(no claim reached a verified verdict)"


def render_uncertain_claims(
    claims: Sequence[Claim],
    index: Sequence[Citation],
) -> str:
    """Render every claim that did not reach ``verified``, by verdict.

    Kept structurally apart from ``render_verified_claims`` so a weak claim
    can never be read as a strong finding — the spec's "unverified claims
    are separated from strong findings" requirement.
    """
    blocks: list[str] = []
    for verdict, heading in _UNCERTAIN_VERDICTS:
        lines: list[str] = []
        for claim in claims:
            if claim.verdict != verdict:
                continue
            markers = citation_markers(claim.source_urls, index)
            suffix = f" {markers}" if markers else ""
            note = (
                f" — {len(claim.contradictions)} contradicting passage(s)"
                if claim.contradictions
                else ""
            )
            lines.append(f"- {claim.text}{suffix}{note}")
        if lines:
            blocks.append(f"### {heading}\n\n" + "\n".join(lines))
    return "\n\n".join(blocks) or "(no unresolved claims were recorded)"


def render_limitations(reasons: Sequence[str]) -> str:
    """Render enumerated limitation reasons as one sentence each."""
    lines: list[str] = []
    for reason in reasons:
        explanation = LIMITATION_REASONS.get(reason)
        if explanation is None:
            raise ValueError(f"unknown limitation reason: {reason}")
        lines.append(f"- {explanation}")
    return "\n".join(lines) or "No limitations were recorded for this pass."


def assemble_report(
    *,
    question: str,
    summary: str,
    sections: Sequence[ReportSection],
    claims: Sequence[Claim],
    sources: Sequence[ScoredSource],
    index: Sequence[Citation],
    limitations: Sequence[str],
    uncertainty_notes: str = "",
) -> str:
    """Assemble the whole Markdown report from prose and recorded evidence.

    ``zip(..., strict=True)`` pins the body list to ``REPORT_SECTIONS``: a
    heading added without a body (or the reverse) fails here rather than
    silently shortening every future report.
    """
    uncertain = render_uncertain_claims(claims, index)
    notes = " ".join(uncertainty_notes.split())
    bodies = (
        summary.strip() or "(no executive summary was produced)",
        render_findings(sections, index),
        render_verified_claims(claims, index),
        f"{notes}\n\n{uncertain}" if notes else uncertain,
        render_limitations(limitations),
        render_citations(index),
        render_source_appendix(sources, index),
    )
    blocks = [f"{REPORT_TITLE_PREFIX}{' '.join(question.split())}"]
    for heading, body in zip(REPORT_SECTIONS, bodies, strict=True):
        blocks.append(f"{heading}\n\n{body}")
    return "\n\n".join(blocks) + "\n"
