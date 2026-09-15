"""The two Markdown artifacts — pure, offline rendering.

One synthesis pass composes two documents, and they answer different
questions:

* the **reader report** answers "what is settled, how strongly, and where is
  it uncertain". Every settled statement is a claim-linked point that ends in
  its own citation markers, and the reference list holds only the sources
  those points actually cite;
* the **evidence ledger** answers "what was checked, and what did the check
  find". It carries the whole checked-claim registry, every source assessment
  (including the ones no reader point cites), every verification passage, the
  drafted content this pass refused, the findings no claim consumed, and the
  run's error inventory.

Nothing here performs I/O, reads a clock, or calls a provider, so both
artifacts are deterministic functions of the composition handed to them.
``As of`` is therefore the newest timestamp the *recorded evidence* carries,
never a clock read.

Canonicalization is repeated here on purpose. ``state.evaluated_sources`` and
``state.verified_claims`` are canonical snapshots by contract, but a caller
may hand a renderer a snapshot assembled before that contract, or a fixture
built by hand. Both renderers therefore fold their inputs through
``identity``'s merge helpers first, so no canonical URL and no claim text can
appear twice and no identity is ever re-derived locally.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from deep_research.agents.identity import (
    finding_fingerprint,
    merge_claim_snapshot,
    merge_source_snapshot,
)
from deep_research.agents.sources import normalize_source_url
from deep_research.agents.steps import summarize_text
from deep_research.utils.types import (
    QUALITY_STATUS_ACCEPTED,
    QUALITY_STATUS_NOT_GATED,
    QUALITY_STATUS_PARTIAL,
    Citation,
    Claim,
    Finding,
    ReportComposition,
    ReportConstraint,
    ReportPoint,
    ReportSection,
    ResearchEvent,
    ScoredSource,
    SubTopic,
)

__all__ = [
    "EVIDENCE_SECTIONS",
    "EVIDENCE_TITLE_PREFIX",
    "LIMITATION_REASONS",
    "QUALITY_STATUS_ACCEPTED",
    "QUALITY_STATUS_NOT_GATED",
    "QUALITY_STATUS_PARTIAL",
    "REPORT_SECTIONS",
    "REPORT_SUMMARY_FALLBACK",
    "REPORT_TITLE_PREFIX",
    "Citation",
    "ReportComposition",
    "ReportConstraint",
    "ReportPoint",
    "ReportSection",
    "build_citation_index",
    "canonical_claims",
    "canonical_sources",
    "citation_markers",
    "reader_citations",
    "render_citations",
    "render_evidence_ledger",
    "render_limitations",
    "render_reader_report",
    "report_as_of",
    "report_scope",
]

REPORT_TITLE_PREFIX = "# Research report: "
EVIDENCE_TITLE_PREFIX = "# Evidence ledger: "

# The reader report's H2 headings, in the order a decision-maker meets them.
# Emitted unconditionally, with an explicit placeholder body when empty: a
# reader must never have to tell "nothing to report" apart from "this section
# was dropped".
REPORT_SECTIONS = (
    "## Executive summary",
    "## Constraint ranking",
    "## Findings",
    "## Uncertainty and conflicting evidence",
    "## Methodology",
    "## References",
)

# The evidence ledger's H2 headings. This is the verbose artifact: nothing is
# summarized away, and a block with no rows says so rather than disappearing.
EVIDENCE_SECTIONS = (
    "## Checked claim registry",
    "## Source assessment",
    "## Reviewed but not cited",
    "## Verification passages",
    "## Rejected draft content",
    "## Unchecked findings and open questions",
    "## Run errors",
)

# The quality status a composed report carries before the terminal quality
# gates judge it now lives with the composition itself, in
# ``utils.types.QUALITY_STATUS_NOT_GATED``, because ``ResearchState`` carries
# the composition the gates judged. It is re-exported here for the renderers
# and every existing caller.

REPORT_SUMMARY_FALLBACK = (
    "No executive summary was written for this pass. The claims, sources, "
    "and limitations recorded below are the whole of what this research "
    "established."
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

# Verdict groups for the uncertainty section, in the order a reader should
# meet them: the evidence that argues against the report comes first.
_UNCERTAIN_VERDICTS = (
    ("contradicted", "Contradicted by independent sources"),
    ("unverified", "Not addressed by independent sources"),
    ("insufficient_evidence", "Insufficient independent evidence"),
)

# Render bounds. Every one of them clamps a single cell or bullet, so a long
# model-written sentence cannot push a table off the page or turn the reader
# report back into the appendix-heavy document this design replaces.
_POINT_CHARS = 600
_RATIONALE_CHARS = 240
_CLAIM_TEXT_CHARS = 240
_EVIDENCE_CHARS = 200
_LOCATOR_CHARS = 120
_ERROR_MESSAGE_CHARS = 240
_NO_DATED_EVIDENCE = "no dated evidence was recorded"
_NO_SCOPE = "not stated"
_CELL_EMPTY = "—"
_BLOCK_SEPARATOR = " · "
# The registry joins several values into one cell with this separator; kept as
# a named constant so it cannot drift between the two artifacts.
_CELL_SEPARATOR_JOIN = ", "


# ``Citation``, ``ReportPoint``, ``ReportConstraint``, ``ReportSection`` and
# ``ReportComposition`` are re-exported from ``utils.types`` above: they are
# state records — ``ResearchState`` carries the composition the quality pass
# judged — and this module is the pure renderer over them.


def canonical_sources(sources: Sequence[ScoredSource]) -> list[ScoredSource]:
    """One record per canonical URL, in first-seen order."""
    return merge_source_snapshot([], sources)


def canonical_claims(claims: Sequence[Claim]) -> list[Claim]:
    """One record per claim identity, in first-seen order."""
    return merge_claim_snapshot([], claims)


def report_as_of(
    *,
    findings: Sequence[Finding],
    events: Sequence[ResearchEvent],
) -> str:
    """The newest timestamp the recorded evidence carries, or an empty string.

    A report's ``As of`` line must never be a clock read: it says how current
    the *evidence* is, not when the document was printed. Both inputs are
    already stamped by the pass that recorded them, so this is a pure
    function of state.
    """
    candidates: list[tuple[datetime, str]] = []
    stamps = [finding.extracted_at for finding in findings]
    stamps.extend(event.timestamp for event in events)
    for stamp in stamps:
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        candidates.append((parsed, stamp))
    if not candidates:
        return ""
    return max(candidates, key=lambda item: item[0])[1]


def report_scope(sub_topics: Sequence[SubTopic]) -> str:
    """State the scope this report assumes, from the plan alone.

    The plan's sub-topics are the only scope the system was given, so naming
    them is the honest declaration a reader needs before reading a ranking as
    advice. Everything else the report prints is bounded by the sources its
    points cite.
    """
    topics = list(sub_topics)
    assumed = (
        "No geography, population, or period beyond what the cited sources "
        "state is assumed."
    )
    if not topics:
        return f"No sub-topic plan was recorded. {assumed}"
    listed = "; ".join(f"{topic.coverage_id} {topic.title}" for topic in topics)
    return (
        f"{len(topics)} planned sub-topic(s), in priority order: {listed}. "
        f"{assumed}"
    )


def build_citation_index(
    sources: Sequence[ScoredSource],
    claims: Sequence[Claim],
) -> list[Citation]:
    """Number every canonical URL this *research* may cite, sources first.

    An evidence-level index: the ledger's assessment rows and the evaluation
    harness both need a number for a URL no reader point cites. The reader
    report numbers its own, narrower index with ``reader_citations``.
    """
    titles: dict[str, str] = {}
    for source in canonical_sources(sources):
        url = normalize_source_url(source.url)
        if url:
            titles.setdefault(url, source.title)
    for claim in canonical_claims(claims):
        for raw in claim.source_urls:
            url = normalize_source_url(raw)
            if url:
                titles.setdefault(url, url)
    return [
        Citation(number=number, url=url, title=title)
        for number, (url, title) in enumerate(titles.items(), start=1)
    ]


def _reader_points(
    composition: ReportComposition,
) -> list[ReportPoint]:
    """Every point the reader report prints, in the order it prints them."""
    points: list[ReportPoint] = [*composition.summary, *composition.constraints]
    for section in composition.sections:
        points.extend(section.points)
    return points


def reader_citations(composition: ReportComposition) -> list[Citation]:
    """Number the URLs the reader report cites, in first-use order.

    Deliberately not ``build_citation_index``: the reader's reference list
    holds only the sources its own points rely on, numbered 1..N without
    gaps, in the order a reader meets them.
    """
    titles: dict[str, str] = {}
    for source in composition.sources:
        url = normalize_source_url(source.url)
        if url:
            titles.setdefault(url, source.title)
    for claim in composition.claims:
        for raw in claim.source_urls:
            url = normalize_source_url(raw)
            if url:
                titles.setdefault(url, url)

    ordered: list[str] = []
    for point in _reader_points(composition):
        for raw in point.source_urls:
            url = normalize_source_url(raw)
            if url and url not in ordered:
                ordered.append(url)
    return [
        Citation(number=number, url=url, title=titles.get(url, url))
        for number, url in enumerate(ordered, start=1)
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
    """Render the numbered reference list the markers point at."""
    lines = [
        f"{citation.number}. {citation.title} — {citation.url}"
        for citation in index
    ]
    return "\n".join(lines) or "(no sources were cited)"


def _cell(text: str, *, limit: int = _POINT_CHARS) -> str:
    """Collapse a value onto one Markdown table cell.

    Pipes are escaped rather than dropped: a title containing ``|`` would
    otherwise silently split the row into extra columns.
    """
    return summarize_text(text, limit=limit).replace("|", "\\|")


def _clamped(text: str, *, limit: int) -> str:
    return summarize_text(text, limit=limit)


def _marker_suffix(markers: str) -> str:
    return f" {markers}" if markers else ""


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _bullets(lines: Sequence[str]) -> str:
    return "\n".join(f"- {line}" for line in lines)


def render_limitations(reasons: Sequence[str]) -> str:
    """Render enumerated limitation reasons as one sentence each."""
    lines: list[str] = []
    for reason in reasons:
        explanation = LIMITATION_REASONS.get(reason)
        if explanation is None:
            raise ValueError(f"unknown limitation reason: {reason}")
        lines.append(f"- {explanation}")
    return "\n".join(lines) or "No limitations were recorded for this pass."


# --- the reader report --------------------------------------------------------


def render_reader_report(composition: ReportComposition) -> str:
    """Render the whole reader report from validated, claim-linked content."""
    index = reader_citations(composition)
    bodies = (
        _reader_summary(composition, index),
        _reader_constraints(composition, index),
        _reader_findings(composition, index),
        _reader_uncertainty(composition, index),
        _reader_methodology(composition),
        render_citations(index),
    )
    blocks = [
        f"{REPORT_TITLE_PREFIX}{' '.join(composition.question.split())}",
        _reader_header(composition),
    ]
    for heading, body in zip(REPORT_SECTIONS, bodies, strict=True):
        blocks.append(f"{heading}\n\n{body}")
    return "\n\n".join(blocks) + "\n"


def _reader_header(composition: ReportComposition) -> str:
    return "\n\n".join(
        (
            f"**As of:** {composition.as_of.strip() or _NO_DATED_EVIDENCE}",
            f"**Scope:** {composition.scope.strip() or _NO_SCOPE}",
            f"**Quality status:** {composition.quality_status.strip() or _NO_SCOPE}",
        )
    )


def _reader_summary(
    composition: ReportComposition,
    index: Sequence[Citation],
) -> str:
    if not composition.summary:
        return REPORT_SUMMARY_FALLBACK
    return _bullets(
        [
            f"{_clamped(point.text, limit=_POINT_CHARS)}"
            f"{_marker_suffix(citation_markers(point.source_urls, index))}"
            for point in composition.summary
        ]
    )


def _claims_for(
    composition: ReportComposition,
    point: ReportPoint,
) -> list[Claim]:
    """The checked claims a rendered point cites, in registry order."""
    wanted = set(point.claim_ids)
    return [claim for claim in composition.claims if claim.claim_id in wanted]


def _evidence_strength(claims: Sequence[Claim]) -> str:
    if not claims:
        return "no checked claim"
    return _CELL_SEPARATOR_JOIN.join(
        f"{claim.verdict} {claim.confidence:.2f}" for claim in claims
    )


def _weakest_confidence(claims: Sequence[Claim]) -> str:
    if not claims:
        return _CELL_EMPTY
    return f"{min(claim.confidence for claim in claims):.2f}"


def _reader_constraints(
    composition: ReportComposition,
    index: Sequence[Citation],
) -> str:
    if not composition.constraints:
        return "(no constraint was ranked for this pass)"
    rows: list[list[str]] = []
    for row in composition.constraints:
        claims = _claims_for(composition, row)
        markers = citation_markers(row.source_urls, index)
        rows.append(
            [
                f"{_cell(row.text)}{_marker_suffix(markers)}",
                _cell(row.deployment_mechanism or "not stated", limit=120),
                _cell(row.geography or "not stated", limit=120),
                _cell(_evidence_strength(claims), limit=200),
                _weakest_confidence(claims),
            ]
        )
    return _table(
        (
            "Constraint",
            "Deployment mechanism",
            "Geography",
            "Evidence strength",
            "Confidence",
        ),
        rows,
    )


def _reader_findings(
    composition: ReportComposition,
    index: Sequence[Citation],
) -> str:
    blocks: list[str] = []
    for section in composition.sections:
        if not section.points:
            continue
        blocks.append(
            f"### {_clamped(section.title, limit=120)}\n\n"
            + _bullets(
                [
                    f"{_clamped(point.text, limit=_POINT_CHARS)}"
                    f"{_marker_suffix(citation_markers(point.source_urls, index))}"
                    for point in section.points
                ]
            )
        )
    return "\n\n".join(blocks) or "(no finding passed validation for this pass)"


def _reader_uncertainty(
    composition: ReportComposition,
    index: Sequence[Citation],
) -> str:
    blocks: list[str] = []
    if composition.uncertainty_notes:
        blocks.append(
            _bullets(
                [
                    _clamped(note, limit=_POINT_CHARS)
                    for note in composition.uncertainty_notes
                ]
            )
        )
    for verdict, heading in _UNCERTAIN_VERDICTS:
        lines: list[str] = []
        for claim in composition.claims:
            if claim.verdict != verdict:
                continue
            markers = citation_markers(claim.source_urls, index)
            note = (
                f" — {len(claim.contradictions)} contradicting passage(s)"
                if claim.contradictions
                else ""
            )
            lines.append(
                f"{_clamped(claim.text, limit=_CLAIM_TEXT_CHARS)}"
                f"{_marker_suffix(markers)}{note}"
            )
        if lines:
            blocks.append(f"### {heading}\n\n{_bullets(lines)}")
    if not blocks:
        blocks.append("(no unresolved claim was recorded for this pass)")
    blocks.append(
        "**Limitations recorded for this pass**\n\n"
        f"{render_limitations(composition.limitations)}"
    )
    return "\n\n".join(blocks)


def _reader_methodology(composition: ReportComposition) -> str:
    scored = sum(
        1 for source in composition.sources if source.evaluation_status == "scored"
    )
    unscored = len(composition.sources) - scored
    verdicts = {
        verdict: sum(1 for claim in composition.claims if claim.verdict == verdict)
        for verdict, _ in _UNCERTAIN_VERDICTS
    }
    verified = sum(
        1 for claim in composition.claims if claim.verdict == "verified"
    )
    passages = sum(
        len(claim.verification_evidence) for claim in composition.claims
    )
    cited_claims = {
        claim_id
        for point in _reader_points(composition)
        for claim_id in point.claim_ids
    }
    lines = [
        f"{len(composition.sources)} reviewed source(s): {scored} scored, "
        f"{unscored} unscored.",
        f"{len(composition.claims)} checked claim(s): {verified} verified, "
        f"{verdicts['contradicted']} contradicted, "
        f"{verdicts['unverified']} unverified, "
        f"{verdicts['insufficient_evidence']} insufficient; {passages} "
        "verification passage(s) recorded.",
        f"{len(composition.findings)} retrieved finding(s); "
        f"{len(cited_claims)} checked claim(s) support a statement above.",
        (
            f"{len(composition.sub_topics)} planned sub-topic(s): "
            + ", ".join(topic.coverage_id for topic in composition.sub_topics)
            if composition.sub_topics
            else "No sub-topic plan was recorded."
        ),
        (
            f"Iteration {composition.iteration} of {composition.max_iterations}."
            if composition.max_iterations
            else f"Iteration {composition.iteration}."
        ),
        (
            "Every statement above is a claim-linked point; its markers name "
            "the sources the checked claims behind it carry."
        ),
        (
            "The limitations this pass discloses are listed with the "
            "uncertainty above, so no limitation is stated twice."
        ),
        (
            "The full evidence ledger — every checked claim, every assessed "
            "source, every verification passage, and every recorded error — is "
            "a separate artifact, published only after the terminal quality "
            "gates."
        ),
    ]
    return _bullets(lines)


# --- the evidence ledger ------------------------------------------------------


def render_evidence_ledger(composition: ReportComposition) -> str:
    """Render the verbose evidence artifact for the same pass."""
    bodies = (
        _claim_registry(composition),
        _source_assessment(composition),
        _reviewed_not_cited(composition),
        _verification_passages(composition),
        _rejected_content(composition),
        _unchecked_findings(composition),
        _run_errors(composition),
    )
    blocks = [
        f"{EVIDENCE_TITLE_PREFIX}{' '.join(composition.question.split())}",
        _ledger_header(composition),
    ]
    for heading, body in zip(EVIDENCE_SECTIONS, bodies, strict=True):
        blocks.append(f"{heading}\n\n{body}")
    return "\n\n".join(blocks) + "\n"


def _ledger_header(composition: ReportComposition) -> str:
    passages = sum(
        len(claim.verification_evidence) for claim in composition.claims
    )
    return "\n\n".join(
        (
            f"**Session:** {composition.session_id} | "
            f"**Iteration:** {composition.iteration} | "
            f"**As of:** {composition.as_of.strip() or _NO_DATED_EVIDENCE}",
            f"**Scope:** {composition.scope.strip() or _NO_SCOPE}",
            f"**Quality status:** {composition.quality_status.strip() or _NO_SCOPE}",
            f"**Canonical counts:** {len(composition.sources)} source(s), "
            f"{len(composition.claims)} claim(s), "
            f"{len(composition.findings)} finding(s), {passages} passage(s).",
        )
    )


def _claim_registry(composition: ReportComposition) -> str:
    if not composition.claims:
        return "(no claim was checked for this pass)"
    rows = [
        [
            str(position),
            _cell(claim.claim_id, limit=80),
            claim.verdict,
            f"{claim.confidence:.2f}",
            _cell(claim.text, limit=_CLAIM_TEXT_CHARS),
            _cell(", ".join(claim.source_urls), limit=_RATIONALE_CHARS),
            _cell(", ".join(claim.consumed_coverage_ids) or _CELL_EMPTY, limit=200),
            _cell(_BLOCK_SEPARATOR.join(claim.contradictions) or _CELL_EMPTY),
        ]
        for position, claim in enumerate(composition.claims, start=1)
    ]
    return _table(
        (
            "#",
            "Claim ID",
            "Verdict",
            "Confidence",
            "Claim",
            "Sources",
            "Coverage",
            "Contradictions",
        ),
        rows,
    )


def _source_assessment(composition: ReportComposition) -> str:
    """One row per canonical source, printing numbers only when scored."""
    if not composition.sources:
        return "(no source was reviewed for this pass)"
    rows: list[list[str]] = []
    for position, source in enumerate(composition.sources, start=1):
        scored = source.evaluation_status == "scored"
        rows.append(
            [
                str(position),
                f"{_cell(source.title, limit=120)} ({source.url})",
                source.evaluation_status,
                _score_cell(source.authority_score if scored else None),
                _score_cell(source.recency_score if scored else None),
                _score_cell(source.relevance_score if scored else None),
                _score_cell(source.overall_score if scored else None),
                (
                    "low" if scored and source.low_confidence
                    else "normal" if scored
                    else "not assessed"
                ),
                _cell(source.rationale, limit=_RATIONALE_CHARS),
            ]
        )
    return _table(
        (
            "#",
            "Source",
            "Status",
            "Authority",
            "Recency",
            "Relevance",
            "Overall",
            "Confidence",
            "Assessment",
        ),
        rows,
    )


def _score_cell(value: float | None) -> str:
    """A numeric quality field, or an explicit absence of one.

    Only a ``scored`` source has a number to print. An unscored source's
    status and reason say why; printing anything numeric for it would invent
    a judgement nobody made.
    """
    return _CELL_EMPTY if value is None else f"{value:.2f}"


def _reviewed_not_cited(composition: ReportComposition) -> str:
    cited = {citation.url for citation in reader_citations(composition)}
    unused = [
        source
        for source in composition.sources
        if normalize_source_url(source.url) not in cited
    ]
    if not unused:
        return "Reviewed but not cited: every reviewed source is cited above."
    return "\n\n".join(
        (
            "Reviewed but not cited: these sources were assessed for this pass "
            "and no statement in the reader report relies on them.",
            _bullets(
                [
                    f"{source.url} ({_cell(source.title, limit=120)}) — "
                    f"reviewed, not cited"
                    for source in unused
                ]
            ),
        )
    )


def _verification_passages(composition: ReportComposition) -> str:
    rows: list[list[str]] = []
    for position, claim in enumerate(composition.claims, start=1):
        for passage in claim.verification_evidence:
            rows.append(
                [
                    str(position),
                    passage.stance,
                    f"{_cell(passage.source_title, limit=120)} "
                    f"({passage.source_url})",
                    _cell(passage.locator, limit=_LOCATOR_CHARS),
                    _cell(passage.excerpt, limit=_EVIDENCE_CHARS),
                ]
            )
    if not rows:
        return "(no verification passage was recorded for this pass)"
    return _table(
        ("Claim #", "Stance", "Source", "Locator", "Passage"),
        rows,
    )


def _rejected_content(composition: ReportComposition) -> str:
    if not composition.rejected:
        return "(no drafted content was refused for this pass)"
    return _bullets(
        [
            _clamped(reason, limit=_ERROR_MESSAGE_CHARS)
            for reason in composition.rejected
        ]
    )


def _unchecked_findings(composition: ReportComposition) -> str:
    consumed = {
        fingerprint
        for claim in composition.claims
        for fingerprint in claim.consumed_finding_fingerprints
    }
    unchecked = [
        finding
        for finding in composition.findings
        if finding_fingerprint(finding) not in consumed
    ]
    if not unchecked:
        return "(every retrieved finding supports a checked claim)"
    return "\n\n".join(
        (
            "These findings support no checked claim, so they are open "
            "questions rather than settled evidence.",
            _bullets(
                [
                    f"[{_clamped(finding.related_sub_topic, limit=120)}] "
                    f"{_clamped(finding.content, limit=_EVIDENCE_CHARS)} "
                    f"({finding.source_url})"
                    for finding in unchecked
                ]
            ),
        )
    )


def _run_errors(composition: ReportComposition) -> str:
    if not composition.errors:
        return "(no error was recorded for this pass)"
    rows = [
        [
            str(position),
            _cell(error.error_type, limit=120),
            _cell(error.source, limit=120),
            "recoverable" if error.recoverable else "fatal",
            _cell(error.message, limit=_ERROR_MESSAGE_CHARS),
        ]
        for position, error in enumerate(composition.errors, start=1)
    ]
    return _table(("#", "Type", "Source", "Severity", "Message"), rows)
