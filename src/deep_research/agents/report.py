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

import re
from collections.abc import Mapping, Sequence
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
    EvidenceUnit,
    Finding,
    ReportAnswerRow,
    ReportComposition,
    ReportConstraint,
    ReportPoint,
    ReportSection,
    ReportStatement,
    ResearchError,
    ResearchEvent,
    ScoredSource,
    SubTopic,
)

__all__ = [
    "ANSWER_SECTION_HEADINGS",
    "DEFAULT_READER_WORD_LIMIT",
    "EVIDENCE_SECTIONS",
    "EVIDENCE_TITLE_PREFIX",
    "LIMITATION_REASONS",
    "LIMITATION_TOPICS",
    "MAX_BACKMATTER_RATIO",
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
    "StatementMappingError",
    "UnknownEvidenceError",
    "backmatter_ratio",
    "build_citation_index",
    "canonical_claims",
    "canonical_sources",
    "citation_markers",
    "collapse_mirror_urls",
    "composition_statements",
    "fit_report_composition",
    "reader_citations",
    "reader_sections",
    "reader_word_count",
    "reader_word_limit",
    "render_citations",
    "render_evidence_ledger",
    "render_limitations",
    "render_reader_report",
    "render_statement_map",
    "report_as_of",
    "report_scope",
    "statement_citation_urls",
    "statement_source_urls",
    "validate_report_statements",
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

# The second section is the answer's own shape. A constraint question is
# answered by a ranked constraint table; a comparison, a factual question, an
# explanation and a historical question are not. Printing an empty constraint
# table for every one of them is how a deployment-ranking template gets
# imposed on a question nobody asked it of.
ANSWER_SECTION_HEADINGS: dict[str, str] = {
    "constraints": "## Constraint ranking",
    "comparison": "## Comparison",
    "explanation": "## Explanation",
    "factual": "## Key facts",
    "historical": "## Chronology",
}

#: What each answer form's table is headed by, in column order. The last
#: column is always the row's evidence strength, rendered locally from the
#: checked claims behind it.
ANSWER_TABLE_COLUMNS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "constraints": (
        ("Constraint", "Constraint"),
        ("Deployment mechanism", ""),
        ("Geography", ""),
    ),
    "comparison": (("Option", "Option"), ("Dimension", "Dimension")),
    "factual": (("Subject", "Subject"), ("Dimension", "Dimension")),
    "historical": (("Period", "Period"), ("Subject", "Subject")),
}

DEFAULT_ANSWER_HEADING = "## Constraint ranking"

#: How long a reader report may be before the frozen contract says otherwise.
DEFAULT_READER_WORD_LIMIT = 8000
#: The share of the rendered reader that backmatter may occupy.
MAX_BACKMATTER_RATIO = 0.35

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
    "## Statement support map",
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

# The three kinds of uncertainty a reader must be able to tell apart. A gap
# caused by nothing being acquired is a different fact from a live
# disagreement, and both differ from a question this pass was never scoped to
# answer; collapsing them into one "uncertainty" list tells the reader none of
# the three.
UNCERTAINTY_GROUPS = (
    ("not acquired", "Not acquired", ("not acquired", "not_acquired")),
    (
        "uncertain/conflicting",
        "Uncertain or conflicting",
        ("uncertain", "conflicting", "contested"),
    ),
    ("outside scope", "Outside scope", ("outside scope", "out_of_scope")),
)

# Short, non-duplicating topic labels for the answer-first summary. The
# uncertainty section states each limitation in full; naming the topic here
# keeps the summary honest without printing the same sentence twice.
LIMITATION_TOPICS: dict[str, str] = {
    "errors_recorded": "some steps of this pass failed",
    "max_iterations_reached": "the refinement budget was exhausted",
    "no_sources_evaluated": "no source behind these findings was scored",
    "low_confidence_sources": "a source behind these findings is low confidence",
    "no_verified_claims": (
        "no claim was independently corroborated by a second source"
    ),
    "contradicted_claims": "an independent source contradicted a claim",
    "report_generation_failed": "the report-writing call failed",
}

# Render bounds. Every one of them clamps a single cell or bullet, so a long
# model-written sentence cannot push a table off the page or turn the reader
# report back into the appendix-heavy document this design replaces.
_POINT_CHARS = 600
_RATIONALE_CHARS = 240
_CLAIM_TEXT_CHARS = 240
_EVIDENCE_CHARS = 200
_LOCATOR_CHARS = 120
_ERROR_MESSAGE_CHARS = 240
_DETAILS_CHARS = 240
# The narrowest bound here, because this cell carries one enumerated token
# (``no_independent_source``) rather than prose: wide enough for the whole
# vocabulary with room for a longer name, and no wider, so a snapshot that
# carries something unexpected cannot widen the registry.
_REASON_CHARS = 60
#: The error types whose ``details`` may be published in the evidence ledger.
#: Membership requires evidence that every value is bounded — a projection that
#: revalidates what it copies, or an enumerated builder — because the ledger is
#: a public artifact and the default for an unvetted key is to withhold it.
#: See ``_published_details``.
#:
#: ``researcher_sub_topic_skipped`` joined after a run lost three planned
#: sub-topics and the ledger could not say why: its details are a locally
#: stamped ``coverage_id`` (``topic-01``), an integer ``priority``, one of three
#: enumerated ``reason`` strings, and a summarised sub-topic title — the same
#: kind of content this artifact already prints for claims and sources. The
#: reason is what distinguishes "truncated by the cap" from "a provider failure
#: stopped the pass" from "already satisfied on a refinement pass", which is
#: exactly the difference between a coverage gap and an acceptable skip.
_DETAILED_ERROR_TYPES = frozenset(
    {"agent_tool_failed", "researcher_sub_topic_skipped"}
)
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


class UnknownEvidenceError(ValueError):
    """A statement cited an evidence id this composition cannot resolve.

    Raised before anything renders: a marker that points at an evidence unit
    nobody recorded is a fabricated citation, and the one place it must never
    reach is the reader report.
    """


class StatementMappingError(ValueError):
    """A substantive statement carries no evidence link at all.

    Factual prose outside the statement map is invalid by contract: it cannot
    be audited, so it cannot be printed. The message names the statement ids,
    never the prose, so it is safe for a ledger or an error record.
    """


def statement_source_urls(
    evidence_ids: Sequence[str],
    evidence: Mapping[str, EvidenceUnit],
) -> list[str]:
    """The citation URLs a statement's *selected* evidence carries.

    Derived locally from the evidence registry, never from a URL the model
    supplied: the ids are the join key, and a URL is whatever the recorded
    unit was actually served from. An unknown id raises, so a citation can
    never be invented by a typo, and the caller learns before rendering.
    """
    urls: list[str] = []
    for evidence_id in evidence_ids:
        unit = evidence.get(evidence_id)
        if unit is None:
            raise UnknownEvidenceError(
                f"unknown evidence id in a reader statement: {evidence_id}"
            )
        url = normalize_source_url(unit.source_url)
        if url and url not in urls:
            urls.append(url)
    return urls


def collapse_mirror_urls(
    urls: Sequence[str],
    sources: Sequence[ScoredSource],
) -> list[str]:
    """One reference per known work, preferring the readable copy.

    A mirror is a transport relation, not a second source: two URLs that
    resolve to one recorded work are one reference, and the copy that is kept
    is the original when the assessments identify one. A URL with no recorded
    work is left exactly as it is — unknown identity cannot collapse two
    references into one.
    """
    by_url = {normalize_source_url(source.url): source for source in sources}
    chosen: dict[str, str] = {}
    collapsed: list[str] = []
    for url in urls:
        source = by_url.get(url)
        work = source.work_id if source is not None else None
        if not work:
            if url not in collapsed:
                collapsed.append(url)
            continue
        existing = chosen.get(work)
        if existing is None:
            chosen[work] = url
            collapsed.append(url)
            continue
        current = by_url.get(existing)
        if (
            source is not None
            and source.transport_relation == "original"
            and (current is None or current.transport_relation != "original")
        ):
            collapsed[collapsed.index(existing)] = url
            chosen[work] = url
    return collapsed


def _claim_urls(composition: ReportComposition, claim_ids: Sequence[str]) -> list[str]:
    """Every URL the named checked claims are cited by, in recorded order."""
    urls: list[str] = []
    wanted = set(claim_ids)
    for claim in composition.claims:
        if claim.claim_id not in wanted:
            continue
        for raw in claim.source_urls:
            url = normalize_source_url(raw)
            if url and url not in urls:
                urls.append(url)
        for passage in claim.verification_evidence:
            url = normalize_source_url(passage.source_url)
            if url and url not in urls:
                urls.append(url)
    return urls


def _cluster_urls(
    composition: ReportComposition,
    cluster_ids: Sequence[str],
) -> list[str]:
    """Every citation the named clusters actually recorded, in order.

    ``verdict_evidence`` is per verdict, so this is the union in the order the
    cluster's own verdict list records — the citations that carried the
    judgement, not only the URL of the finding that first raised it.
    """
    urls: list[str] = []
    for cluster_id in cluster_ids:
        cluster = composition.claim_clusters.get(cluster_id)
        if cluster is None:
            continue
        candidates = [
            *cluster.source_urls,
            *(
                url
                for verdict in cluster.verdicts
                for url in cluster.verdict_evidence.get(verdict, [])
            ),
        ]
        for raw in candidates:
            url = normalize_source_url(raw)
            if url and url not in urls:
                urls.append(url)
    return urls


def statement_citation_urls(
    statement: ReportStatement,
    composition: ReportComposition,
) -> list[str]:
    """The URLs one statement may cite, resolved locally from its evidence.

    Three recorded sources are unioned: the exact selected evidence ids, the
    verdict citations of the clusters it names, and the citations of the
    checked claims behind it — including the verification passages that
    actually carried the verdict, which is why a claim verified against an
    independent review cites that review and not only the URL of the finding
    that first raised it. The result is collapsed per work, so a mirrored copy
    contributes one reference. A model-supplied URL never enters this
    function.
    """
    urls = statement_source_urls(
        statement.evidence_ids, composition.evidence_units
    )
    claims = _statement_owner_claims(composition, statement)
    for url in (
        *_cluster_urls(composition, statement.claim_cluster_ids),
        *_claim_urls(composition, [claim.claim_id for claim in claims]),
    ):
        if url not in urls:
            urls.append(url)
    return collapse_mirror_urls(urls, composition.sources)


def composition_statements(
    composition: ReportComposition,
) -> list[ReportStatement]:
    """Every statement the reader report renders, in render order."""
    return list(composition.statements)


def _statement_owner_claims(
    composition: ReportComposition,
    statement: ReportStatement,
) -> list[Claim]:
    """The checked claims whose statement this is, by point link or cluster."""
    cluster_ids = set(statement.claim_cluster_ids)
    linked = [
        claim
        for claim in composition.claims
        if claim.cluster_id in cluster_ids
        or cluster_ids & set(claim.cluster_aliases)
    ]
    if linked:
        return linked
    point = _statement_points(composition).get(statement.statement_id)
    if point is None:
        return []
    wanted = set(point.claim_ids)
    return [claim for claim in composition.claims if claim.claim_id in wanted]


def _statement_points(
    composition: ReportComposition,
) -> dict[str, ReportPoint]:
    """The point each statement renders through, when it renders through one.

    A constraint row's mechanism and geography cells render inside the row, so
    they resolve their claim link through the same row the reader sees.
    """
    index: dict[str, ReportPoint] = {}
    for point in [*composition.summary, *composition.constraints]:
        if point.statement is not None:
            index[point.statement.statement_id] = point
    for row in composition.constraints:
        for cell in (row.mechanism_statement, row.geography_statement):
            if cell is not None:
                index[cell.statement_id] = row
    for section in composition.sections:
        for point in section.points:
            if point.statement is not None:
                index[point.statement.statement_id] = point
    return index


def statement_has_claim_link(
    composition: ReportComposition,
    statement: ReportStatement,
) -> bool:
    """True when a statement resolves to selected evidence or a checked claim.

    The link may come from the statement's own fields or from the point that
    renders it: a statement whose evidence registry is missing (a narrative
    path, or a snapshot written before the registry existed) is still linked
    when the point names a checked claim whose citations the reader sees.
    """
    if statement.evidence_ids:
        return True
    if any(
        cluster_id in composition.claim_clusters
        for cluster_id in statement.claim_cluster_ids
    ):
        return True
    return bool(_statement_owner_claims(composition, statement))


def validate_report_statements(
    composition: ReportComposition,
) -> list[str]:
    """Check the whole statement map before a renderer touches it.

    Two failures are fatal because they make a reader statement meaningless:
    an evidence id no unit answers (``UnknownEvidenceError``), and a
    substantive statement with no evidence link and no checked claim behind it
    (``StatementMappingError``). Everything else that is worth saying about
    the map is returned as a finding, so a caller can record it without
    refusing a report that is merely thin.
    """
    findings: list[str] = []
    for statement in composition.statements:
        statement_source_urls(
            statement.evidence_ids, composition.evidence_units
        )
        linked_clusters = [
            cluster_id
            for cluster_id in statement.claim_cluster_ids
            if cluster_id in composition.claim_clusters
        ]
        claims = _statement_owner_claims(composition, statement)
        if not statement.substantive:
            continue
        if not statement_has_claim_link(composition, statement):
            raise StatementMappingError(
                "a substantive reader statement carries no evidence link: "
                f"{statement.statement_id}"
            )
        if not (linked_clusters or claims):
            findings.append(
                f"{statement.statement_id} carries selected evidence but no "
                "checked claim link"
            )
    return findings


def _statement_urls_for_point(
    point: ReportPoint,
    composition: ReportComposition,
) -> list[str]:
    """The citation URLs one point renders, claim links included.

    A point's own ``source_urls`` are already validated against the claims it
    names; the statement's selected evidence is unioned onto them, so a
    statement backed by a verification passage cites the passage too.
    """
    urls = [
        normalize_source_url(url)
        for url in point.source_urls
        if normalize_source_url(url)
    ]
    if point.statement is not None:
        for url in statement_citation_urls(point.statement, composition):
            if url not in urls:
                urls.append(url)
    return collapse_mirror_urls(urls, composition.sources)


def _answer_row_urls(
    row: ReportAnswerRow,
    composition: ReportComposition,
) -> list[str]:
    urls: list[str] = []
    for cell in row.cells:
        for url in statement_citation_urls(cell, composition):
            if url not in urls:
                urls.append(url)
    return collapse_mirror_urls(urls, composition.sources)


def _statement_urls(
    statement: ReportStatement,
    composition: ReportComposition,
) -> list[str]:
    return collapse_mirror_urls(
        statement_citation_urls(statement, composition), composition.sources
    )



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


def _reader_url_groups(
    composition: ReportComposition,
) -> list[list[str]]:
    """The citation sets the reader meets, in the order it meets them.

    One group per rendered statement: the summary, the constraint or
    answer-kind rows, the findings, and the statement-backed uncertainty. The
    reference list is built from exactly these, so no URL reaches the reader
    that no statement resolved.
    """
    groups: list[list[str]] = [
        _statement_urls_for_point(point, composition)
        for point in [*composition.summary, *composition.constraints]
    ]
    for row in composition.answer_rows:
        groups.append(_answer_row_urls(row, composition))
    for section in composition.sections:
        for point in section.points:
            groups.append(_statement_urls_for_point(point, composition))
    for statement in composition.uncertainty_statements:
        groups.append(_statement_urls(statement, composition))
    return [group for group in groups if group]


def reader_citations(composition: ReportComposition) -> list[Citation]:
    """Number the URLs the reader report cites, in first-use order.

    Deliberately not ``build_citation_index``: the reader's reference list
    holds only the sources its own statements rely on, numbered 1..N without
    gaps, in the order a reader meets them. The URLs come from the statements'
    selected evidence and the checked claims' citations — never from a URL the
    model supplied on its own.
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
        for passage in claim.verification_evidence:
            url = normalize_source_url(passage.source_url)
            if url:
                titles.setdefault(url, passage.source_title)
    for unit in composition.evidence_units.values():
        url = normalize_source_url(unit.source_url)
        if url:
            titles.setdefault(url, unit.source_title)

    ordered: list[str] = []
    for group in _reader_url_groups(composition):
        for url in group:
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


# --- length and backmatter -----------------------------------------------------


def reader_word_count(markdown: str) -> int:
    """The reader report's word count, as a reader would count words."""
    return len(re.findall(r"\S+", markdown))


def reader_word_limit(composition: ReportComposition) -> int:
    """The length the frozen contract allows this reader report.

    ``requested_word_limit`` governs when it is set — Section 2.3 lets a
    question ask for more than the default, and a question that asks for less
    gets what it asked for. An unset contract gets the project default.
    """
    if composition.requested_word_limit is None:
        return DEFAULT_READER_WORD_LIMIT
    return max(1, composition.requested_word_limit)


def backmatter_ratio(markdown: str) -> float:
    """The share of the rendered reader that Methodology and References take.

    Counted over the rendered artifact, in characters, because that is the
    ceiling the contract states. Audit detail belongs in the ledger, so the
    fix for an over-long backmatter is to move detail out, never to delete a
    citation the statements rely on.
    """
    if not markdown:
        return 0.0
    index = markdown.find("## Methodology")
    if index < 0:
        return 0.0
    return len(markdown[index:]) / len(markdown)


# Statements are given up in this order when the report has to shrink: this
# pass's own framing first, then attribution, then derivations, then
# disagreements, and the settled findings last.
_SACRIFICE_MODE_ORDER = {
    "context": 0,
    "attributed": 1,
    "inference": 2,
    "contested": 3,
    "settled": 4,
}


def _statement_locations(
    composition: ReportComposition,
) -> list[tuple[str, int, str, int]]:
    """(statement_id, position, mode, rendered words) for droppable points.

    The word cost is the *point's* text, which is what the reader loses when
    the statement goes — not the statement record's own wording, which may be
    a shorter restatement of it.
    """
    locations: list[tuple[str, int, str, int]] = []
    position = 0
    for point in [*composition.summary, *composition.constraints]:
        position += 1
        if point.statement is not None:
            locations.append(
                (
                    point.statement.statement_id,
                    position,
                    point.mode,
                    reader_word_count(point.text) + 4,
                )
            )
    for section in composition.sections:
        for point in section.points:
            position += 1
            if point.statement is not None:
                locations.append(
                    (
                        point.statement.statement_id,
                        position,
                        point.mode,
                        reader_word_count(point.text) + 4,
                    )
                )
    return locations


def _restated_clusters(composition: ReportComposition) -> set[str]:
    """Clusters the summary restates that the findings also discuss."""
    summary_clusters = {
        cluster_id
        for point in composition.summary
        for cluster_id in point.claim_cluster_ids
    }
    body_clusters = {
        cluster_id
        for section in composition.sections
        for point in section.points
        for cluster_id in point.claim_cluster_ids
    }
    return summary_clusters & body_clusters


def _drop_statement(composition: ReportComposition, statement_id: str) -> bool:
    """Remove one statement from wherever it renders. True when it went."""
    kept_summary = [
        point
        for point in composition.summary
        if point.statement_id != statement_id
    ]
    if len(kept_summary) != len(composition.summary):
        composition.summary = kept_summary
        return True
    for section in composition.sections:
        kept = [
            point
            for point in section.points
            if point.statement_id != statement_id
        ]
        if len(kept) != len(section.points):
            section.points = kept
            return True
    return False


def fit_report_composition(
    composition: ReportComposition,
) -> tuple[ReportComposition, list[str]]:
    """Shrink the reader report to the frozen length ceiling, and say so.

    Statements are dropped in a fixed, documented order — this pass's framing
    before attribution, attribution before derivations, derivations before
    disagreements, settled findings last, and a summary restatement before the
    finding it restates — and every drop is reported as a disposition so the
    ledger can account for prose the reader does not see. Nothing is dropped
    from the composition's evidence: the claim registry, sources, clusters and
    statements stay in the ledger.
    """
    fitted = composition.model_copy(deep=True)
    reasons: list[str] = []
    limit = reader_word_limit(composition)
    if reader_word_count(_render_reader(fitted, compact_backmatter=False)) <= limit:
        return fitted, reasons
    restated = _restated_clusters(fitted)
    ordered = sorted(
        _statement_locations(fitted),
        key=lambda item: (
            0
            if any(
                cluster in restated
                for cluster in _clusters_of(fitted, item[0])
            )
            else 1,
            _SACRIFICE_MODE_ORDER.get(item[2], 1),
            -item[1],
        ),
    )
    pending = [(statement_id, words) for statement_id, _, _, words in ordered]
    while pending:
        rendered = _render_reader(fitted, compact_backmatter=0)
        over = reader_word_count(rendered) - limit
        if over <= 0:
            return fitted, reasons
        dropped = 0
        freed = 0
        while pending and freed < over:
            statement_id, words = pending.pop(0)
            if _drop_statement(fitted, statement_id):
                dropped += 1
                freed += words
        if not dropped:
            break
        reasons.append(f"length_budget_dropped:{dropped}")
    if reader_word_count(_render_reader(fitted, compact_backmatter=0)) > limit:
        reasons.append("length_budget_floor_reached")
    return fitted, reasons


def _clusters_of(
    composition: ReportComposition, statement_id: str
) -> list[str]:
    for statement in composition.statements:
        if statement.statement_id == statement_id:
            return list(statement.claim_cluster_ids)
    return []


def reader_sections(answer_kind: str | None) -> tuple[str, ...]:
    """The reader's H2 headings for one frozen answer form.

    The first and last three sections are the same for every question: a
    summary, the findings, the uncertainty, the method, and the references.
    The second section is the answer's own shape — a constraint ranking only
    where the question asked for constraints.
    """
    heading = ANSWER_SECTION_HEADINGS.get(
        answer_kind or "", DEFAULT_ANSWER_HEADING
    )
    return (REPORT_SECTIONS[0], heading, *REPORT_SECTIONS[2:])


def render_reader_report(composition: ReportComposition) -> str:
    """Render the whole reader report from validated, statement-mapped content.

    Fits the composition to the frozen length ceiling, then renders at the
    most informative backmatter level that satisfies the backmatter ceiling.
    Both are measured on the rendered text rather than estimated: the ceilings
    are stated over the artifact a reader receives, and the counts and
    provenance sentences the methodology carries are what keeps the reader
    able to check the report's own claims about itself.
    """
    fitted, _ = fit_report_composition(composition)
    rendered = _render_reader(fitted, compact_backmatter=0)
    for level in (1, 2):
        if backmatter_ratio(rendered) <= MAX_BACKMATTER_RATIO:
            break
        rendered = _render_reader(fitted, compact_backmatter=level)
    return rendered


def _render_reader(
    composition: ReportComposition,
    *,
    compact_backmatter: int,
) -> str:
    index = reader_citations(composition)
    summary = _reader_summary(composition, index)
    table = _reader_table(composition, index)
    findings = _reader_findings(composition, index)
    uncertainty = _reader_uncertainty(composition, index)
    rendered = "\n\n".join((summary, table, findings, uncertainty))
    bodies = (
        summary,
        table,
        findings,
        uncertainty,
        _reader_methodology(
            composition,
            rendered=rendered,
            compact=compact_backmatter,
        ),
        render_citations(index),
    )
    headings = reader_sections(composition.answer_kind)
    blocks = [
        f"{REPORT_TITLE_PREFIX}{' '.join(composition.question.split())}",
        _reader_header(composition),
    ]
    for heading, body in zip(headings, bodies, strict=True):
        blocks.append(f"{heading}\n\n{body}")
    return "\n\n".join(blocks) + "\n"


def _reader_header(composition: ReportComposition) -> str:
    lines = [
        f"**As of:** {composition.as_of.strip() or _NO_DATED_EVIDENCE}",
    ]
    if composition.generated_on.strip():
        lines.append(f"**Generated on:** {composition.generated_on.strip()}")
    if composition.date_basis.strip():
        lines.append(f"**Date basis:** {composition.date_basis.strip()}")
    lines.extend(
        (
            f"**Scope:** {composition.scope.strip() or _NO_SCOPE}",
            f"**Quality status:** {composition.quality_status.strip() or _NO_SCOPE}",
        )
    )
    return "\n\n".join(lines)


def _point_line(
    point: ReportPoint,
    composition: ReportComposition,
    index: Sequence[Citation],
) -> str:
    """One rendered statement: its text and its own resolved markers."""
    markers = citation_markers(
        _statement_urls_for_point(point, composition), index
    )
    return (
        f"{_clamped(point.text, limit=_POINT_CHARS)}"
        f"{_marker_suffix(markers)}"
    )


def _reader_summary(
    composition: ReportComposition,
    index: Sequence[Citation],
) -> str:
    """The answer first: what is established, what would change it, and the
    limitation that matters most — each part driven by statement modes.

    A contested or attributed statement is *not* what the evidence
    establishes, so it is grouped separately rather than printed as a settled
    finding; the summary's limitation line names the topic and leaves the
    sentence itself to the uncertainty section, which states it once.
    """
    if not composition.summary:
        return REPORT_SUMMARY_FALLBACK
    blocks: list[str] = []
    established = [
        point
        for point in composition.summary
        if point.mode in {"settled", "inference"}
    ]
    provisional = [
        point
        for point in composition.summary
        if point.mode in {"attributed", "contested"}
    ]
    context = [point for point in composition.summary if point.mode == "context"]
    if established:
        blocks.append(
            "**What the evidence establishes**\n\n"
            + _bullets(
                [
                    _point_line(point, composition, index)
                    for point in established
                ]
            )
        )
    if provisional:
        blocks.append(
            "**What would change the answer**\n\n"
            + _bullets(
                [
                    _point_line(point, composition, index)
                    for point in provisional
                ]
            )
        )
    if context:
        blocks.append(
            _bullets(
                [
                    _point_line(point, composition, index)
                    for point in context
                ]
            )
        )
    blocks.append(_summary_limitation_line(composition))
    return "\n\n".join(blocks)


def _summary_limitation_line(composition: ReportComposition) -> str:
    """Name the most important unresolved limitation without repeating it."""
    if not composition.limitations:
        return (
            "**The most important unresolved limitation** is that this pass "
            "recorded none."
        )
    topic = LIMITATION_TOPICS.get(
        composition.limitations[0], "see the limitations below"
    )
    return f"**The most important unresolved limitation** is {topic}."


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


def _reader_table(
    composition: ReportComposition,
    index: Sequence[Citation],
) -> str:
    """The answer's own second section: a table only where one is owed.

    A constraints question gets the ranked constraint table. Every other
    answer form gets its own columns when rows were drafted, an explanation
    gets prose instead of a table, and a form with no rows says so rather than
    printing an empty deployment-ranking grid.
    """
    kind = composition.answer_kind or "constraints"
    if kind == "constraints":
        return _reader_constraints(composition, index)
    if kind == "explanation":
        return _reader_explanation(composition, index)
    return _reader_answer_rows(composition, index, kind=kind)


def _reader_constraints(
    composition: ReportComposition,
    index: Sequence[Citation],
) -> str:
    if not composition.constraints:
        return "(no constraint was ranked for this pass)"
    rows: list[list[str]] = []
    for row in composition.constraints:
        claims = _claims_for(composition, row)
        markers = citation_markers(
            _statement_urls_for_point(row, composition), index
        )
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


def _reader_answer_rows(
    composition: ReportComposition,
    index: Sequence[Citation],
    *,
    kind: str,
) -> str:
    """The answer-kind table: labelled cells plus one evidenced finding."""
    if not composition.answer_rows:
        return f"(no {kind} row was drafted for this pass)"
    labels = [label for label, _ in ANSWER_TABLE_COLUMNS.get(kind, ())]
    header = (*labels, "Evidenced finding", "Evidence strength", "Confidence")
    rows: list[list[str]] = []
    for row in composition.answer_rows:
        cells = list(row.cells)
        statement = row.statement
        claims = (
            _statement_owner_claims(composition, statement)
            if statement is not None
            else []
        )
        markers = citation_markers(_answer_row_urls(row, composition), index)
        filled: list[str] = [
            _cell(cell.text, limit=120) for cell in cells[: len(labels)]
        ]
        while len(filled) < len(labels):
            filled.append(_CELL_EMPTY)
        finding = statement.text if statement is not None else _CELL_EMPTY
        rows.append(
            [
                *filled,
                f"{_cell(finding)}{_marker_suffix(markers)}",
                _cell(_evidence_strength(claims), limit=200),
                _weakest_confidence(claims),
            ]
        )
    return _table(header, rows)


def _reader_explanation(
    composition: ReportComposition,
    index: Sequence[Citation],
) -> str:
    """An explanation is prose: mechanisms, not a ranking grid."""
    if not composition.answer_rows:
        return "(no mechanism was drafted for this pass)"
    lines: list[str] = []
    for row in composition.answer_rows:
        statement = row.statement
        if statement is None:
            continue
        labelled = " — ".join(
            cell.text for cell in row.labels if cell.text.strip()
        )
        markers = citation_markers(_answer_row_urls(row, composition), index)
        prefix = f"**{_clamped(labelled, limit=120)}**: " if labelled else ""
        lines.append(
            f"{prefix}{_clamped(statement.text, limit=_POINT_CHARS)}"
            f"{_marker_suffix(markers)}"
        )
    return _bullets(lines) or "(no mechanism was drafted for this pass)"


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
                    _point_line(point, composition, index)
                    for point in section.points
                ]
            )
        )
    return "\n\n".join(blocks) or "(no finding passed validation for this pass)"


def _uncertainty_group(basis: str | None) -> str:
    """Which uncertainty group a context statement's basis names."""
    text = (basis or "").casefold()
    for _, heading, tokens in UNCERTAINTY_GROUPS:
        if any(token in text for token in tokens):
            return heading
    return ""


def _reader_uncertainty(
    composition: ReportComposition,
    index: Sequence[Citation],
) -> str:
    blocks: list[str] = []
    ungrouped: list[str] = []
    grouped: dict[str, list[str]] = {
        heading: [] for _, heading, _ in UNCERTAINTY_GROUPS
    }
    for statement in composition.uncertainty_statements:
        markers = citation_markers(
            _statement_urls(statement, composition), index
        )
        line = (
            f"{_clamped(statement.text, limit=_POINT_CHARS)}"
            f"{_marker_suffix(markers)}"
        )
        heading = _uncertainty_group(statement.basis)
        if heading:
            grouped[heading].append(line)
        else:
            ungrouped.append(line)
    if ungrouped:
        blocks.append(_bullets(ungrouped))
    for _, heading, _ in UNCERTAINTY_GROUPS:
        if grouped[heading]:
            blocks.append(f"### {heading}\n\n{_bullets(grouped[heading])}")
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


def _reader_methodology(
    composition: ReportComposition,
    *,
    rendered: str = "",
    compact: int = 0,
) -> str:
    """What this pass did, stated only as strongly as the report shows.

    The linkage, repetition and limitation sentences are measured before they
    are printed — against the rendered report where the claim is about what
    the reader can see, and against the statement map where the claim is about
    provenance. A pass whose prose does not demonstrate full linkage says how
    many statements are unlinked instead of asserting a completeness the
    reader can check and find missing. ``compact`` drops the narrative lines
    (and, at two, the sub-topic roster) so the backmatter ceiling is met by
    moving audit detail to the ledger rather than by deleting citations.
    """
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
    statements = composition.statements
    content_statements = [
        statement
        for statement in statements
        if statement not in composition.uncertainty_statements
    ]
    unlinked = [
        statement.statement_id
        for statement in content_statements
        if not statement_has_claim_link(composition, statement)
    ]
    substantive = [
        statement for statement in statements if statement.substantive
    ]
    with_evidence = sum(1 for statement in substantive if statement.evidence_ids)
    duplicated_limitations = [
        reason
        for reason in composition.limitations
        if rendered.count(LIMITATION_REASONS[reason]) > 1
    ]
    rendered_bullets = [
        " ".join(line.split())
        for line in rendered.splitlines()
        if line.strip().startswith("- ")
    ]
    repeated_bullets = len(rendered_bullets) - len(set(rendered_bullets))
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
    ]
    if compact < 2:
        lines.append(
            f"Iteration {composition.iteration} of {composition.max_iterations}."
            if composition.max_iterations
            else f"Iteration {composition.iteration}."
        )
    if compact < 1:
        lines.append(
            f"{len(statements)} reader statement(s) carry a statement record; "
            f"{with_evidence} of {len(substantive)} substantive statement(s) "
            "resolve to selected evidence."
        )
    # The three measured sentences below are the report's claims about
    # itself, so they are never dropped to meet a ratio: a reader who cannot
    # see them cannot check the report's own honesty.
    if unlinked:
        lines.append(
            f"{len(unlinked)} statement(s) above carry no checked claim "
            "link; they are this pass's own framing, not findings."
        )
    else:
        lines.append("Every statement above carries a checked claim link.")
    if repeated_bullets:
        lines.append(
            f"{repeated_bullets} bullet(s) above repeat another bullet "
            "verbatim."
        )
    else:
        lines.append("No statement above repeats another.")
    if duplicated_limitations:
        lines.append(
            f"{len(duplicated_limitations)} recorded limitation(s) are "
            "stated more than once above."
        )
    else:
        lines.append(
            "The limitations this pass discloses are listed with the "
            "uncertainty above, so no limitation is stated twice."
        )
    if compact < 1:
        lines.append(
            "The full evidence ledger — every checked claim, every assessed "
            "source, every verification passage, the statement map, and every "
            "recorded error — is a separate artifact, published only after the "
            "terminal quality gates."
        )
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
        render_statement_map(composition),
    )
    blocks = [
        f"{EVIDENCE_TITLE_PREFIX}{' '.join(composition.question.split())}",
        _ledger_header(composition),
    ]
    for heading, body in zip(EVIDENCE_SECTIONS, bodies, strict=True):
        blocks.append(f"{heading}\n\n{body}")
    return "\n\n".join(blocks) + "\n"


def render_statement_map(composition: ReportComposition) -> str:
    """Every reader statement, with the mapping that makes it auditable.

    This is the audit bulk the reader report deliberately does not carry: one
    row per statement, its reader mode, the claim clusters and exact evidence
    ids behind it, the targets and required dimensions it answers, any
    recorded derivation, and the citations it resolves to. The dispositions
    this pass recorded for refused and repaired prose are stated above the
    table, and the new factual assertions waiting for a fact check are named
    here rather than routed from here.
    """
    statements = composition.statements
    prelude = [
        (
            f"{len(statements)} reader statement(s) mapped; "
            f"{composition.distinct_statement_count} distinct fact(s) behind "
            "them."
        )
    ]
    _, fit_reasons = fit_report_composition(composition)
    dispositions = [*composition.statement_dispositions, *fit_reasons]
    if backmatter_ratio(_render_reader(composition, compact_backmatter=2)) > (
        MAX_BACKMATTER_RATIO
    ):
        # The ceiling is missed by the floor, not by choice: the references
        # every statement needs cannot be deleted to reach a ratio, so the
        # ledger records that the artifact is over it and why.
        dispositions.append("backmatter_floor_reached")
    prelude.append(
        "Dispositions recorded for this pass: "
        + (", ".join(dispositions) if dispositions else "none.")
    )
    prelude.append(
        "New factual assertions returned to the fact checker: "
        + (
            ", ".join(
                summarize_text(item, limit=_RATIONALE_CHARS)
                for item in composition.returned_to_fact_checker
            )
            if composition.returned_to_fact_checker
            else "none."
        )
    )
    if not statements:
        return "\n\n".join(
            [*prelude, "(no reader statement was composed for this pass)"]
        )
    rows = [
        [
            _cell(statement.statement_id, limit=40),
            statement.mode,
            _cell(statement.text, limit=_CLAIM_TEXT_CHARS),
            _cell(", ".join(statement.claim_cluster_ids) or _CELL_EMPTY, limit=200),
            _cell(", ".join(statement.evidence_ids) or _CELL_EMPTY, limit=200),
            _cell(", ".join(statement.target_ids) or _CELL_EMPTY, limit=160),
            _cell(
                ", ".join(statement.answered_dimensions) or _CELL_EMPTY,
                limit=160,
            ),
            _cell(statement.basis or _CELL_EMPTY, limit=_RATIONALE_CHARS),
            _cell(
                ", ".join(_statement_urls(statement, composition)) or _CELL_EMPTY,
                limit=_EVIDENCE_CHARS,
            ),
        ]
        for statement in statements
    ]
    table = _table(
        (
            "Statement",
            "Mode",
            "Text",
            "Claim clusters",
            "Evidence",
            "Targets",
            "Dimensions",
            "Basis",
            "Citations",
        ),
        rows,
    )
    return "\n\n".join([*prelude, table])


def _source_dates(source: ScoredSource) -> str:
    """The four dates a source can carry, each named separately.

    A publication date, the period the data cover, the horizon a projection
    refers to, and the date a rule took effect answer different questions;
    printing them as one value is how a 2035 projection becomes today's cost.
    """
    temporal = source.temporal
    parts = [
        f"{label}={value}"
        for label, value in (
            ("publication", temporal.publication_date),
            ("data_period", temporal.data_period),
            ("forecast", temporal.forecast_horizon),
            ("effective", temporal.effective_date),
        )
        if value
    ]
    return _CELL_SEPARATOR_JOIN.join(parts) or _CELL_EMPTY


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
            _reason_cell(claim),
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
            "Reason",
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
                _cell(_source_dates(source), limit=200),
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
            "Dates",
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


def _reason_cell(claim: Claim) -> str:
    """Why a claim could not be judged, or an explicit absence of a reason.

    Only an ``insufficient_evidence`` claim has a reason to print, and the
    gate is the same one ``_score_cell`` applies: a value the record's own
    verdict does not entitle it to is not published. An insufficient claim
    with no recorded reason stays empty too — that is a claim whose verdict
    was read and resolved to nothing usable, which is a different finding
    from one nothing independent was ever read for.
    """
    if claim.verdict != "insufficient_evidence":
        return _CELL_EMPTY
    return _cell(claim.insufficient_reason or _CELL_EMPTY, limit=_REASON_CHARS)


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


def _published_details(error: ResearchError) -> str:
    """Render ``error.details`` for the one error type whose details are bounded.

    ``agent_tool_failed`` is published because its details are produced by the
    ReAct projection, which revalidates every value it copies: the tool name the
    toolset resolved, the iteration, the enumerated error type, and — for
    ``web_scraper`` — the bounded diagnosis (``attempts``, ``retries``,
    ``status_code``, and a media type or the static ``unknown`` marker). Without
    those values a scraper failure is countable but not *classifiable*, which is
    the whole point of classifying it.

    Every other error type's details are withheld. They are not vetted by a
    projection that revalidates them, and this artifact is public, so an
    unvetted key could carry text this project never publishes. Withholding is
    the conservative default; a type is added here only with the same evidence
    that its details are bounded.
    """
    if error.error_type not in _DETAILED_ERROR_TYPES or not error.details:
        return _CELL_EMPTY
    ordered = sorted(error.details.items(), key=lambda item: item[0])
    return ", ".join(f"{key}={value}" for key, value in ordered)


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
            _cell(_published_details(error), limit=_DETAILS_CHARS),
        ]
        for position, error in enumerate(composition.errors, start=1)
    ]
    return _table(
        ("#", "Type", "Source", "Severity", "Message", "Details"), rows
    )
