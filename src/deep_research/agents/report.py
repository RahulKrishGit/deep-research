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
``As of`` is therefore the newest timestamp the *recorded evidence* carries —
a read's retrieval time or a finding's extraction time — never a graph event
and never a clock read.

Canonicalization is repeated here on purpose. ``state.evaluated_sources`` and
``state.verified_claims`` are canonical snapshots by contract, but a caller
may hand a renderer a snapshot assembled before that contract, or a fixture
built by hand. Both renderers therefore fold their inputs through
``identity``'s merge helpers first, so no canonical URL and no claim text can
appear twice and no identity is ever re-derived locally.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Mapping, Sequence
from datetime import datetime

from pydantic import JsonValue

from deep_research.agents.figures import quantities_in, same_quantity
from deep_research.agents.identity import (
    deduplicate_findings,
    finding_fingerprint,
    merge_claim_snapshot,
    merge_source_snapshot,
)
from deep_research.agents.sources import normalize_source_url, publisher_identity
from deep_research.agents.steps import summarize_text
from deep_research.utils.types import (
    ANSWERING_STATEMENT_MODES,
    EVIDENCE_BADGE_LABELS,
    QUALITY_STATUS_ACCEPTED,
    QUALITY_STATUS_NOT_GATED,
    QUALITY_STATUS_PARTIAL,
    Citation,
    Claim,
    EvidenceUnit,
    FactRow,
    FigureAttribution,
    FigureKind,
    Finding,
    ReadRecord,
    ReportAnswerRow,
    RejectedDraftPoint,
    ReportComposition,
    ReportConstraint,
    ReportPoint,
    ReportReview,
    ReportSection,
    ReportStatement,
    ReportTerminalState,
    ResearchError,
    ResearchState,
    ScoredSource,
    SubstantiveCoverage,
    SubTopic,
)

__all__ = [
    "ATTRIBUTED_ANSWER_HEADING",
    "ANSWER_SECTION_HEADINGS",
    "COUNTERFACTUAL_ANSWER_HEADING",
    "DEFAULT_READER_WORD_LIMIT",
    "ESTABLISHED_ANSWER_HEADING",
    "EVIDENCE_SECTIONS",
    "EVIDENCE_STATUS_LABELS",
    "EVIDENCE_TITLE_PREFIX",
    "LIMITATION_CONSEQUENCE",
    "LIMITATION_REASONS",
    "LIMITATION_TOPICS",
    "MAX_BACKMATTER_RATIO",
    "QUALITY_RECORD_ARTIFACT_NAMES",
    "QUALITY_RECORD_EXCERPT_CHARS",
    "QUALITY_RECORD_TEXT_CHARS",
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
    "artifact_content_hashes",
    "asks_for_the_latest",
    "backmatter_ratio",
    "build_citation_index",
    "canonical_claims",
    "canonical_sources",
    "citation_markers",
    "collapse_mirror_urls",
    "composition_statements",
    "disclosed_limitations",
    "distinct_retention_counts",
    "evidence_status_bucket",
    "evidence_status_counts",
    "fit_report_composition",
    "most_consequential_limitation",
    "point_vintage",
    "reader_citations",
    "reader_sections",
    "reader_word_count",
    "reader_word_limit",
    "render_citations",
    "render_evidence_ledger",
    "render_limitations",
    "render_quality_json",
    "render_quality_record",
    "render_reader_report",
    "render_statement_map",
    "render_terminal_status",
    "report_as_of",
    "report_scope",
    "statement_citation_urls",
    "statement_source_urls",
    "terminal_report_state",
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

#: The label columns of each answer form's table, in column order. The
#: evidenced finding and the evidence strength are appended by the renderer,
#: because they are the same two columns for every form. A constraints
#: question is not here: its table is the ranked constraint table, with its
#: own header, and ``_reader_table`` routes to it directly.
ANSWER_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "comparison": ("Option", "Dimension"),
    "factual": ("Subject", "Dimension"),
    "historical": ("Period", "Subject"),
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
    ("contradicted", "Contradicted or revised"),
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

LIMITATION_CONSEQUENCE: tuple[str, ...] = (
    "report_generation_failed",
    "errors_recorded",
    "max_iterations_reached",
    "contradicted_claims",
    "no_verified_claims",
    "no_sources_evaluated",
    "low_confidence_sources",
)
"""The recorded limitation reasons, in the order of what they cost a reader.

``limitation_reasons`` records them in producer order, which is an
implementation detail; this is the order a reader should meet them in. A
report nobody wrote is the most consequential thing a pass can disclose, an
independent source contradicting a claim outranks nothing being corroborated,
and a low-confidence source matters less than any of them. A reason this table
does not know keeps its recorded position after these and is never dropped.
"""

ESTABLISHED_ANSWER_HEADING = "**What the evidence establishes**"
ATTRIBUTED_ANSWER_HEADING = "**The attributed answer**"
COUNTERFACTUAL_ANSWER_HEADING = "**What would change the answer**"
"""The three answer blocks, one per kind of statement a summary can carry.

An *attributed* statement is an answer whose provenance is a named source, so
it is headed as one: filing it under "what would change the answer" told the
audited run's reader that its whole answer — both figures and both forecasts,
every point of them attributed — was a counterfactual. Only a *contested*
statement is that, because an unresolved disagreement is exactly what a reader
would need settled before the answer holds.
"""

_FAILED_RUN_STATUS = "failed"

# What a question asks for when it asks for recency. The summary's order is
# otherwise the writer's, and this is the only reading that overrides it.
_LATEST_MARKERS = (
    "latest",
    "most recent",
    "newest",
    "up to date",
    "up-to-date",
    "current",
)

# A recorded date value as it may be written: ``YYYY``, ``YYYY-MM``, or
# ``YYYY-MM-DD``, anywhere inside the value's own words. The month is bounded
# to a real month, so a *range* ("2011-2025") reads as two years rather than as
# 2011 with a month of 20.
_VINTAGE_PATTERN = re.compile(r"(\d{4})(?:-(0[1-9]|1[0-2])(?:-(\d{2}))?)?")

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
#: stamped ``coverage_id`` (``topic-01``), an integer ``priority``, one of four
#: enumerated ``reason`` strings, and a summarised sub-topic title — the same
#: kind of content this artifact already prints for claims and sources. The
#: reason is what distinguishes "truncated by the cap" from "a provider failure
#: stopped the pass" from "already satisfied on a refinement pass", which is
#: exactly the difference between a coverage gap and an acceptable skip.
_SUB_TOPIC_SKIP_ERROR_TYPE = "researcher_sub_topic_skipped"

# The enumerated reasons ``agents.researcher.sub_topic_skipped_error`` stamps on
# a skip, and the reading each one gets. The producer writes one message for all
# four, and that message says the topic was never researched — true of a cap
# truncation and of a stopped pass, false for a topic an earlier pass already
# answered. Ruling 5 keeps prior completion, deferred work and never-attempted
# work distinct, so the reading follows the enumerated reason and the
# never-researched sentence is printed only where it is true. A reason outside
# this map falls back to the producer's own message rather than guessing.
SUB_TOPIC_SKIP_MESSAGES: dict[str, str] = {
    "required_targets_completed": (
        "This planned sub-topic already met its required targets on an earlier "
        "pass, so no new research was owed for it."
    ),
    "interim_satisfaction": (
        "This planned sub-topic already carried a finding from an earlier pass, "
        "so the refinement did not research it again."
    ),
    "cap": (
        "This planned sub-topic was deferred: the pass reached its sub-topic "
        "limit before its turn came up."
    ),
    "provider_failure_stopped_processing": (
        "A planned sub-topic was never researched; a provider failure stopped "
        "the pass before it could run."
    ),
}

#: The skip reasons that are prior completion rather than lost coverage: the
#: topic owed nothing, so nothing was lost by omitting it. Both mean the topic
#: *was* researched — on an earlier pass — or, in the legacy target-less case,
#: already carried a finding. Every other reason is a real gap and stays a
#: warning.
SATISFIED_SKIP_REASONS = frozenset(
    {"required_targets_completed", "interim_satisfaction"}
)

_DETAILED_ERROR_TYPES = frozenset(
    {"agent_tool_failed", _SUB_TOPIC_SKIP_ERROR_TYPE}
)
_NO_DATED_EVIDENCE = "no dated evidence was recorded"
#: Why the ``As of`` stamp is not the figures' date. It is the newest timestamp
#: the recorded *evidence* carries — a read's retrieval or a finding's
#: extraction — and a reader who takes it for the data's own date would read a
#: retrieval clock as a measurement vintage. Every figure states the edition it
#: rests on beside it; these two words say which of the two the stamp is, and
#: the reader's word ceiling is charged for every one of them.
_AS_OF_MEANING = " (evidence retrieved)"
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


def _claim_urls(
    composition: ReportComposition,
    claim_ids: Sequence[str],
    *,
    supporting: bool,
) -> list[str]:
    """Every URL the named checked claims are cited by, in recorded order.

    Only the passages recorded as *supporting* count for a statement presented
    as supporting: a verification passage carries the stance it was selected
    with, so a passage filed as contradicting the claim is the rebuttal, and
    printing it after a supporting statement as one of that statement's
    citations credits the thing the statement disputes. A statement presented
    as contested cites both, because there the rebuttal is the point
    (``statement_citation_urls``).
    """
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
            if supporting and passage.stance != "supports":
                continue
            url = normalize_source_url(passage.source_url)
            if url and url not in urls:
                urls.append(url)
    return urls


def _cluster_urls(
    composition: ReportComposition,
    cluster_ids: Sequence[str],
    *,
    supporting: bool,
) -> list[str]:
    """Every citation the named clusters recorded for this statement.

    ``verdict_evidence`` is per verdict, so this is the union in the order the
    cluster's own verdict list records — the citations that carried the
    judgement, not only the URL of the finding that first raised it. For a
    statement presented as supporting, a ``contradicted`` verdict is excluded:
    its evidence is what disputed the proposition, and a statement asserting
    that proposition must not list its rebuttal among the citations that carry
    it. A statement presented as contested keeps it, for the same reason.
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
                if not (supporting and verdict == "contradicted")
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

    Which of those citations count depends on how the statement is presented
    (Section 2.4: "attributed/contested points cite the appropriate
    source/contradiction"). A statement the reader is shown as supporting —
    ``settled``, ``attributed`` or ``inference`` — cites its support and never
    its rebuttal. A statement presented as ``contested`` cites the source that
    disputes it as well: that is the citation a reader checks the recorded
    disagreement against, and withholding it published a contested bullet
    whose every citation agreed with it.
    """
    supporting = statement.mode in ANSWERING_STATEMENT_MODES
    urls = statement_source_urls(
        statement.evidence_ids, composition.evidence_units
    )
    claims = _statement_owner_claims(composition, statement)
    for url in (
        *_cluster_urls(
            composition, statement.claim_cluster_ids, supporting=supporting
        ),
        *_claim_urls(
            composition,
            [claim.claim_id for claim in claims],
            supporting=supporting,
        ),
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
    """The checked claims whose statement this is, by point link or cluster.

    Resolved through the point that renders it, through the clusters it
    names, and through cluster membership — a cluster records the claims it
    absorbed, so a fixture or a refinement that persisted only one side of
    the link still resolves.
    """
    cluster_ids = set(statement.claim_cluster_ids)
    linked = [
        claim
        for claim in composition.claims
        if claim.cluster_id in cluster_ids
        or cluster_ids & set(claim.cluster_aliases)
        or any(
            claim.claim_id
            in composition.claim_clusters[cluster_id].member_claim_ids
            for cluster_id in cluster_ids
            if cluster_id in composition.claim_clusters
        )
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
        if not statement.substantive:
            continue
        linked_clusters = [
            cluster_id
            for cluster_id in statement.claim_cluster_ids
            if cluster_id in composition.claim_clusters
        ]
        claims = _statement_owner_claims(composition, statement)
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
    """The URLs one statement cites, already collapsed per work.

    ``statement_citation_urls`` collapses mirrors itself, so this is a name
    for the single source of a statement's citations rather than a second
    pass over them.
    """
    return statement_citation_urls(statement, composition)


def report_as_of(
    *,
    findings: Sequence[Finding],
    reads: Sequence[ReadRecord],
) -> str:
    """The newest timestamp the recorded evidence carries, or an empty string.

    A report's ``As of`` line must never be a clock read: it says how current
    the *evidence* is, not when the document was printed. The two evidence
    sources are exactly the timestamps the recording pass stamped — each
    finding's ``extracted_at`` and each read's ``retrieved_at``. A graph event
    timestamp is not evidence: it says when a node ran, not how current the
    material was, so no event timestamp is a candidate here.
    """
    candidates: list[tuple[datetime, str]] = []
    stamps = [finding.extracted_at for finding in findings]
    stamps.extend(read.retrieved_at for read in reads)
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
    points: list[ReportPoint] = [
        *_ordered_summary(composition),
        *composition.constraints,
    ]
    for section in composition.sections:
        points.extend(section.points)
    return points


def _stated_claim_ids(composition: ReportComposition) -> set[str]:
    """The checked claims some reader point already states."""
    return {
        claim_id
        for point in _reader_points(composition)
        for claim_id in point.claim_ids
    }


def _reader_url_groups(
    composition: ReportComposition,
) -> list[list[str]]:
    """The citation sets the reader meets, in the order it meets them.

    One group per rendered statement: the summary, the constraint or
    answer-kind rows, the findings, and the statement-backed uncertainty. The
    reference list is built from exactly these, so no URL reaches the reader
    that no statement resolved — and the summary is walked in the order the
    reader meets it, so a re-ordered answer cannot put a marker on a bullet
    that is numbered after one below it.
    """
    groups: list[list[str]] = [
        _statement_urls_for_point(point, composition)
        for point in [*_ordered_summary(composition), *composition.constraints]
    ]
    for row in composition.answer_rows:
        groups.append(_answer_row_urls(row, composition))
    for section in composition.sections:
        for point in section.points:
            groups.append(_statement_urls_for_point(point, composition))
    for statement in composition.uncertainty_statements:
        groups.append(_statement_urls(statement, composition))
    # Checked claims printed under "Insufficient independent evidence" are
    # reader bullets too, even when no substantive point used them. Without
    # their URLs the list silently loses its markers and its references. A
    # claim a point already states is counted there, not reprinted, so it
    # cites nothing new; one that is printed cites one copy per work, like
    # every other statement.
    stated = _stated_claim_ids(composition)
    for claim in composition.claims:
        if claim.verdict in dict(_UNCERTAIN_VERDICTS) and claim.claim_id not in stated:
            groups.append(
                collapse_mirror_urls(
                    [
                        url
                        for raw in claim.source_urls
                        if (url := normalize_source_url(raw))
                    ],
                    composition.sources,
                )
            )
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


#: What a display bound leaves where it cut. A cut that lands mid-word reads as
#: the source's own wording — the audited report carried "…at the end of 2026,
#: c..." and "…with Texas and California expect..." — so the cut falls between
#: words and the marker says a cut was made, rather than trailing off into an
#: ellipsis a reader cannot tell from the text itself. The synthesizer shares
#: this bound: its own clamps feed the same artifacts.
_DISPLAY_CUT = " […] (cut)"


def _display_clamp(text: str, *, limit: int) -> str:
    """Collapse whitespace and clamp to ``limit``, cutting between words.

    This is the bound a *published* value goes through, which is why it is not
    ``summarize_text``: that one cuts on the character count, which is right
    for a prompt or a span output and produces a fragment of a word in an
    artifact. The bound still bounds — the marker is counted inside ``limit``,
    so a clamped value is never wider than an unclamped one would have been —
    and a text with no space to cut on is cut on the count, because a single
    longer word would otherwise overflow the cell it is printed in.
    """
    collapsed = " ".join(text.split())
    if not collapsed:
        # ``summarize_text``'s own placeholder, reached through it rather than
        # restated here: a blank value says so in one place, not two.
        return summarize_text(text, limit=limit)
    if len(collapsed) <= limit:
        return collapsed
    if limit <= len(_DISPLAY_CUT):
        return collapsed[:limit]
    budget = limit - len(_DISPLAY_CUT)
    boundary = collapsed[: budget + 1].rfind(" ")
    cut = budget if boundary < 0 else boundary
    return collapsed[:cut] + _DISPLAY_CUT


def _cell(text: str, *, limit: int = _POINT_CHARS) -> str:
    """Collapse a value onto one Markdown table cell.

    Pipes are escaped rather than dropped: a title containing ``|`` would
    otherwise silently split the row into extra columns.
    """
    return _display_clamp(text, limit=limit).replace("|", "\\|")


def _clamped(text: str, *, limit: int) -> str:
    return _display_clamp(text, limit=limit)


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


def _bullet_lines(markdown: str) -> list[str]:
    """Every bullet ``markdown`` prints, whitespace-collapsed.

    The one reader of "what bullets does this text carry", used both by the
    methodology's repetition count and by the uncertainty section's own
    suppression: measured one way and enforced another, the disclosed count
    and the rendered list could disagree about the same report.
    """
    return [
        " ".join(line.split())
        for line in markdown.splitlines()
        if line.strip().startswith("- ")
    ]


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
    # The *last* Methodology heading, not the first: a statement's own text
    # may name the heading, and taking the first occurrence would move the
    # boundary into the body and inflate the ratio it measures.
    index = markdown.rfind("## Methodology")
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
    a shorter restatement of it. Only the summary and the findings are listed:
    the ranked answer is the last thing a report should lose, and a row this
    loop cannot remove would be popped, free nothing, and still reach a floor
    while droppable prose remained.
    """
    locations: list[tuple[str, int, str, int]] = []
    position = 0
    for point in composition.summary:
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

    The reasons travel on the fitted composition itself, because the fit runs
    once and every later reader of the composition — the gates, the reviewer,
    the quality record, the ledger — has to see what was dropped. A caller
    that re-runs this on an already-fitted composition gets it back unchanged
    with no reasons, so the dispositions cannot be duplicated or drifted.
    """
    fitted = composition.model_copy(deep=True)
    reasons: list[str] = []
    limit = reader_word_limit(composition)
    if reader_word_count(_render_reader(fitted, compact_backmatter=0)) <= limit:
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
            return _with_fit_reasons(fitted, reasons), reasons
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
    return _with_fit_reasons(fitted, reasons), reasons


def _with_fit_reasons(
    fitted: ReportComposition, reasons: Sequence[str]
) -> ReportComposition:
    """The fitted composition, carrying its own fit reasons as dispositions."""
    if not reasons:
        return fitted
    return fitted.model_copy(
        update={
            "statement_dispositions": [
                *fitted.statement_dispositions,
                *reasons,
            ]
        }
    )


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

    Validates first: the production path checks the map in
    ``build_report_composition``, but a composition rehydrated from state and
    re-rendered would otherwise reach the reader with a substantive statement
    that carries no evidence link at all. The reader renders exactly the
    composition it is given: the word-limit fit belongs to the build, so the
    published Markdown, the acceptance gates, the reviewer and the quality
    record all describe one statement set. It renders at the most informative
    backmatter level that satisfies the backmatter ceiling, measured on the
    rendered text rather than estimated, because the ceilings are stated over
    the artifact a reader receives and the counts and provenance sentences the
    methodology carries are what keeps the reader able to check the report's
    own claims about itself.
    """
    validate_report_statements(composition)
    rendered = _render_reader(composition, compact_backmatter=0)
    for level in (1, 2):
        if backmatter_ratio(rendered) <= MAX_BACKMATTER_RATIO:
            break
        rendered = _render_reader(composition, compact_backmatter=level)
    return rendered


def _render_reader(
    composition: ReportComposition,
    *,
    compact_backmatter: int,
) -> str:
    index = reader_citations(composition)
    table = _reader_table(composition, index)
    # The lines the reader meets before the uncertainty section, rendered
    # without the verdict notes. This is what the section compares against
    # when it decides a claim is already stated above, and a note is part of
    # the line: marking a bullet before that comparison would un-suppress the
    # very claim the note explains, and the report would state it twice.
    earlier = "\n\n".join(
        (
            _reader_summary(composition, index, notes={}),
            table,
            _reader_findings(composition, index, notes={}),
        )
    )
    notes = _unestablished_notes(composition, index, earlier)
    summary = _reader_summary(composition, index, notes=notes)
    findings = _reader_findings(composition, index, notes=notes)
    uncertainty = _reader_uncertainty(
        composition,
        index,
        # What the reader has already met when this section renders, so a claim
        # that repeats a finding above is not printed a second time.
        already=earlier,
    )
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


def _critic_reading(terminal: ReportTerminalState) -> str:
    """The Critic's outcome, in the words its own ``review_status`` supports.

    A failed review is printed as a review that never happened, never as its
    floor score: the floor exists so an outage cannot read as a low score, and
    printing the number beside "failed" puts the judgement back.
    """
    if terminal.critic_status == "reviewed":
        return (
            f"scored {terminal.critic_score}/10"
            if terminal.critic_score is not None
            else "reviewed, with no score recorded"
        )
    if terminal.critic_status == "failed":
        return "never judged (the review did not validate)"
    return "no critique was recorded"


def _review_reading(terminal: ReportTerminalState) -> str:
    """The terminal semantic review's outcome, or that there was none.

    The recorded mean is printed when the review was scored, and only then.
    The review contract refuses a score beside ``incomplete`` or
    ``provider_failed`` — a missing judgement must not be averageable into an
    acceptance — so a number beside one of those statuses is not a judgement
    and a renderer must not print it as one. A ``scored`` review whose mean is
    absent says so, the way the Critic's own reading does.
    """
    if terminal.review_status == "scored":
        if terminal.review_score is None:
            return "scored, with no score recorded"
        return f"scored {terminal.review_score:.2f}"
    if terminal.review_status:
        return f"unscored ({terminal.review_status})"
    return "unscored (no semantic review was recorded)"


def render_terminal_status(terminal: ReportTerminalState) -> list[str]:
    """What the run's own terminal checks decided, in the run's own terms.

    Read from the record the finalizer stamped and never inferred from the
    composition: a pass nobody finalized states nothing here, because an
    unstated check is not a passed one. Every value is enumerated or counted —
    a router's status name, a reviewer's own status, the gate names that
    rejected the report — so no provider text can reach the reader through it.
    """
    if not terminal.status:
        return []
    lines = [
        f"**Run status:** {terminal.status}",
        f"**Critic:** {_critic_reading(terminal)}",
        f"**Report review:** {_review_reading(terminal)}",
    ]
    if terminal.required_targets or terminal.critical_targets:
        lines.append(
            f"**Coverage:** {terminal.answered_targets} of "
            f"{terminal.required_targets} required targets answered; "
            f"{terminal.answered_critical_targets} of "
            f"{terminal.critical_targets} critical targets answered"
        )
    if terminal.gate_failures:
        lines.append(
            f"**Gate failures:** {', '.join(terminal.gate_failures)}"
        )
    return lines


def _reader_header(composition: ReportComposition) -> str:
    stamp = composition.as_of.strip()
    lines = [
        (
            f"**As of:** {stamp}{_AS_OF_MEANING}"
            if stamp
            else f"**As of:** {_NO_DATED_EVIDENCE}"
        ),
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
    lines.extend(render_terminal_status(composition.terminal))
    return "\n\n".join(lines)


def _note_key(text: str) -> str:
    """A statement's words, as both the note map and a bullet key them.

    Clamped at the bound the uncertainty section prints *claims* at, because
    that is the line the suppression compares: a claim longer than that is
    never suppressed — the section's own line is cut and cannot match a bullet
    — so a longer statement needs no note and gets none.
    """
    return " ".join(_clamped(text, limit=_CLAIM_TEXT_CHARS).split())


def _point_line(
    point: ReportPoint,
    composition: ReportComposition,
    index: Sequence[Citation],
    *,
    revision: str = "",
    notes: Mapping[str, str] | None = None,
) -> str:
    """One rendered statement: its text, its notes and its markers.

    A bullet whose words are a claim this pass could not establish carries that
    reading as a parenthetical, between the figure's own attribution and the
    citation markers. The uncertainty section does not reprint a claim the
    reader has already met above it, so without the note the bullet would be
    the report's only word on that claim and would say nothing about its
    status: a reader could not tell an established finding from one no
    independent source supported.

    The attribution comes from the claim's recorded provenance, so a figure
    reaches the reader with the issuer, the edition and the release date it
    was read with — never with a date picked off the page it was found on.
    """
    markers = citation_markers(
        _statement_urls_for_point(point, composition), index
    )
    note = (notes or {}).get(_note_key(point.text), "")
    return (
        f"{_clamped(point.text, limit=_POINT_CHARS)}"
        f"{_point_note(point, composition, revision=revision)}"
        f"{note}"
        f"{_marker_suffix(markers)}"
    )


def _claim_target_policy(
    claim: Claim, composition: ReportComposition
) -> str | None:
    """The support policy this claim's own recorded targets declare.

    ``None`` when the claim names no target, or none of its target ids
    resolves to a target this pass's own plan recorded — the caller then
    falls back to the verdict-derived reading, because no plan obligation
    ever certified what this claim's evidence would need to satisfy. A claim
    that answers targets under disagreeing policies is read by the strictest
    one recorded: meeting a weaker obligation does not discharge a stricter
    one the same claim also names.
    """
    if not claim.target_ids:
        return None
    policies = {
        target.target_id: target.support_policy
        for topic in composition.sub_topics
        for target in topic.evidence_targets
    }
    matched = [
        policies[target_id]
        for target_id in claim.target_ids
        if target_id in policies
    ]
    if not matched:
        return None
    if "independent_pair" in matched:
        return "independent_pair"
    if "derivation" in matched:
        return "derivation"
    return "primary_attribution"


_NOT_CORROBORATED_NOTE = " (not independently corroborated)"


def _unestablished_note_text(
    claim: Claim, composition: ReportComposition, heading: str
) -> str:
    """The parenthetical one bullet earns for the claim already stated above.

    A claim whose own badge is ``source_supported`` or ``verified_pair`` has
    already met a ``primary_attribution`` or ``derivation`` target exactly as
    the plan asked for it, so no parenthetical disagrees with the "attributed
    answer" heading it renders under. The same badge answering an
    ``independent_pair`` target has not met that stricter obligation, and
    says so in the badge's own words rather than the legacy verdict's. A
    claim that names no target, or none this plan recorded, keeps reading its
    recorded verdict — no obligation ever certified what it would need to
    satisfy, so nothing here may say it already did.
    """
    if claim.evidence_status in ("source_supported", "verified_pair"):
        policy = _claim_target_policy(claim, composition)
        if policy in ("primary_attribution", "derivation"):
            return ""
        if policy == "independent_pair":
            return _NOT_CORROBORATED_NOTE
    return f" ({heading})"


def _unestablished_notes(
    composition: ReportComposition,
    index: Sequence[Citation],
    already: str,
) -> dict[str, str]:
    """The reading to print on each bullet whose claim is already stated above.

    Keyed by the claim's words, so the note lands on the bullet that states
    them — in the summary or in the findings, whichever the pass put them in,
    and on both when it restated them in both.

    The test is the uncertainty section's own: only a claim whose line the
    reader has already met is suppressed there, and that suppression is
    exactly what this note replaces — a note on a claim the section still
    lists would state the same reading twice. ``already`` is therefore the
    rendering *without* notes: the note is part of the line, and a marked
    bullet would no longer match the claim it marks.

    ``_unestablished_note_text`` decides the note itself: a ``source_supported``
    claim answering the ``primary_attribution`` or ``derivation`` target it
    was checked for earns no parenthetical at all, since the heading it
    renders under already states that reading.
    """
    seen = set(_bullet_lines(already))
    points = _reader_points(composition)
    notes: dict[str, str] = {}
    for verdict, heading in _UNCERTAIN_VERDICTS:
        for claim in composition.claims:
            if claim.verdict != verdict:
                continue
            note_text = _unestablished_note_text(claim, composition, heading)
            linked = [
                point for point in points if claim.claim_id in point.claim_ids
            ]
            if linked:
                # The reader already met this checked claim, however reworded;
                # the uncertainty list will count it rather than reprint it.
                if not note_text:
                    continue
                for point in linked:
                    notes.setdefault(_note_key(point.text), note_text)
                continue
            if not note_text:
                continue
            markers = citation_markers(claim.source_urls, index)
            text = " ".join(claim.text.split())
            line = f"- {text}{_marker_suffix(markers)}"
            if " ".join(line.split()) not in seen:
                continue
            notes.setdefault(_note_key(claim.text), note_text)
    return notes


def asks_for_the_latest(question: str) -> bool:
    """True when the question asks for the most recent figure available."""
    folded = " ".join(question.casefold().split())
    return any(marker in folded for marker in _LATEST_MARKERS)


def point_provenance(
    point: ReportPoint,
    composition: ReportComposition,
) -> str:
    """The attribution this point's own claims record for its figures.

    Read from the claim and never from the source: a page can state several
    figures — EIA's March 2025 release carries both the 2024 additions and the
    2025 forecast — and a source's own publication date is not the edition its
    data rest on. Every distinct record the point's claims carry is stated, in
    the point's claim order, so a statement resting on two claims from two
    editions names both rather than one of them.
    """
    claims = {claim.claim_id: claim for claim in composition.claims}
    selected = [
        claims[claim_id] for claim_id in point.claim_ids if claim_id in claims
    ]
    parts: list[str] = []
    for claim in selected:
        provenance = claim.provenance
        if provenance.statement_date and not provenance.release_date and not (
            provenance.vintage or provenance.attributed_issuer
        ):
            # A relay's publication day is not the date of a measurement
            # whose own issuer release is also cited by this very point.
            # Require the same period, unit-bearing value and at least one
            # shared cited source before suppressing it; independent works
            # with the same numerical result must keep their own dates.
            figures = {
                (match.group(0).casefold())
                for match in _MEASURE_UNIT.finditer(claim.text)
                if match.group(1).casefold() in _MEASURE_UNITS
            }
            if any(
                other is not claim
                and other.provenance.release_date
                and other.provenance.data_period == provenance.data_period
                and set(other.source_urls).intersection(claim.source_urls)
                and figures.intersection(
                    match.group(0).casefold()
                    for match in _MEASURE_UNIT.finditer(other.text)
                    if match.group(1).casefold() in _MEASURE_UNITS
                )
                for other in selected
            ):
                continue
        text = provenance.as_text()
        if text and text not in parts:
            parts.append(text)
    return "; ".join(parts)


#: How a recorded figure's own status reads to a reader. ``observed`` is a
#: count of what happened; the rest are somebody's projection of what has not.
_FIGURE_KINDS = {
    "observed": "actual",
    "projected": "forecast",
    "forecast": "forecast",
    "estimated": "estimate",
}


def _figure_kind(point: ReportPoint, composition: ReportComposition) -> str:
    """Whether this point's figure is a projection or an observation, or ``""``.

    Read from the recorded proposition, not from the sentence: "19.6 GW in
    2025" is a forecast and "10.4 GW in 2024" counts what happened, and a
    reader must be able to tell which of the two an answer rests on.
    """
    claims = {claim.claim_id: claim for claim in composition.claims}
    kinds: list[str] = []
    for claim_id in point.claim_ids:
        claim = claims.get(claim_id)
        if claim is None:
            continue
        cluster = composition.claim_clusters.get(claim.cluster_id or "")
        if cluster is None:
            continue
        kind = _FIGURE_KINDS.get(
            cluster.proposition.forecast_status.strip().casefold(), ""
        )
        if kind and kind not in kinds:
            kinds.append(kind)
    return ", ".join(kinds)


def _point_note(
    point: ReportPoint,
    composition: ReportComposition,
    *,
    revision: str = "",
) -> str:
    """The reading a bullet carries beside its text, from the record alone.

    The attribution per figure — issuer, edition, release date — the revision
    label when two editions of one measurement stand on the page, and whether
    the figure is a projection or an observation. Nothing is printed that the
    recorded provenance does not carry.
    """
    labels = [
        label
        for label in (revision, _figure_kind(point, composition))
        if label
    ]
    provenance = point_provenance(point, composition)
    if not labels and not provenance:
        return ""
    if labels and provenance:
        body = f"{', '.join(labels)}: {provenance}"
    else:
        body = provenance or ", ".join(labels)
    return f" ({body})"


def _vintage_key(value: str) -> tuple[int, int, int] | None:
    """A recorded date value as a sortable key, or ``None`` when undated.

    A range is keyed by its **end** year: "2011-2025" describes data through
    2025, and keying it by 2011 made a current cumulative figure read as the
    oldest vintage on the page.
    """
    matches = list(_VINTAGE_PATTERN.finditer(value))
    if not matches:
        return None
    year, month, day = matches[-1].groups()
    return (int(year), int(month or 0), int(day or 0))


def point_vintage(
    point: ReportPoint,
    composition: ReportComposition,
) -> tuple[tuple[int, int, int], str] | None:
    """The newest release one point's own claims record, and what records it.

    Read from the claim's provenance: the date the attributed issuer released
    the figure, and the dated edition of the data when no release date was
    recorded. A page's own publication date and the run's retrieval clock are
    deliberately not candidates — a date this system did not read as *this
    figure's* edition is not a vintage it may print — and a point whose claims
    record no date has none.
    """
    claims = {claim.claim_id: claim for claim in composition.claims}
    newest: tuple[tuple[int, int, int], str] | None = None
    for claim_id in point.claim_ids:
        claim = claims.get(claim_id)
        if claim is None:
            continue
        provenance = claim.provenance
        for value in (provenance.release, provenance.vintage):
            if not value:
                continue
            key = _vintage_key(value)
            if key is None:
                continue
            if newest is None or key > newest[0]:
                newest = (
                    key,
                    provenance.as_text() or value,
                )
            break
    return newest


_MONTH_WORDS = frozenset(
    {
        "january", "february", "march", "april", "may", "june", "july",
        "august", "september", "october", "november", "december",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
        "oct", "nov", "dec",
    }
)
_WORD = re.compile(r"[a-z0-9]+")


def _series_words(vintage: str) -> str:
    """The series a vintage names, with its edition taken out.

    "December 2024 Preliminary Monthly Electric Generator Inventory" and
    "January 2025 Preliminary Monthly Electric Generator Inventory" are two
    editions of one series, and the edition is what orders them: it is not
    part of the identity that decides whether two figures are comparable.
    """
    return " ".join(
        word
        for word in _WORD.findall(vintage.casefold())
        if word not in _MONTH_WORDS and not any(char.isdigit() for char in word)
    )


def _revision_key(
    point: ReportPoint,
    composition: ReportComposition,
) -> tuple[object, ...]:
    """The comparable identity of one dated measurement, from its own claims.

    Who published it, in what series, of what measurand, over what scope, in
    what units and years. Two figures are revisions of one measurement only
    when all of those agree — a solar total, a cumulative stock and an
    all-segment count are another issuer, series, measurand or scope, however
    alike their units and years look.
    """
    claims = {claim.claim_id: claim for claim in composition.claims}
    issuers: list[str] = []
    series: list[str] = []
    scopes: list[str] = []
    measurands: list[str] = []
    for claim_id in point.claim_ids:
        claim = claims.get(claim_id)
        if claim is None:
            continue
        provenance = claim.provenance
        for target, value in (
            (issuers, provenance.attributed_issuer),
            (series, provenance.vintage),
            (scopes, provenance.measure_scope),
        ):
            text = " ".join((value or "").casefold().split())
            if text and text not in target:
                target.append(text)
        cluster = composition.claim_clusters.get(claim.cluster_id or "")
        if cluster is None:
            continue
        proposition = cluster.proposition
        measurand = " ".join(
            (
                proposition.change_kind,
                proposition.quantity_noun,
                proposition.unit,
            )
        ).casefold()
        if measurand.strip() and measurand not in measurands:
            measurands.append(measurand)
    return (
        tuple(issuers),
        tuple(sorted({_series_words(value) for value in series})),
        tuple(scopes),
        tuple(measurands),
        *_measure_signature(point),
    )


_MEASURE_UNIT = re.compile(
    r"\d[\d,.'\u2019]*\s*([A-Za-z][A-Za-z/-]*)",
)
_MEASURE_UNITS = frozenset(
    {
        "gw", "gws", "mw", "mws", "kw", "kws", "tw", "tws",
        "gwh", "mwh", "kwh", "twh", "gigawatt", "gigawatts", "megawatt",
        "megawatts", "kilowatt", "kilowatts", "terawatt", "terawatts",
        "percent", "pct", "%",
    }
)
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")


def _measure_signature(point: ReportPoint) -> tuple[frozenset[str], frozenset[str]]:
    """The quantities a point states, as (units, years).

    The fallback grouping when a point records no target: two statements
    measure the same thing when they carry the same units over the same years.
    A 2024 addition in GW and a 2025 forecast in GW are two different
    measurements, and comparing their dates called the actual superseded.
    """
    units = {
        match.group(1).casefold()
        for match in _MEASURE_UNIT.finditer(point.text)
        if match.group(1).casefold() in _MEASURE_UNITS
    }
    return (frozenset(units), frozenset(_YEAR.findall(point.text)))


def _vintage_group(
    point: ReportPoint,
    composition: ReportComposition,
) -> tuple[object, ...]:
    """What makes two dated statements versions of one measurement.

    The recorded target when the point names one — that is the system's own
    statement that these statements answer one obligation — and otherwise the
    units and years the point itself states. Two points that share neither are
    never compared, so no figure is called an older vintage of a different
    quantity.
    """
    statement = point.statement
    if statement is not None and statement.target_ids:
        return ("target", tuple(sorted(statement.target_ids)), *_revision_key(point, composition))
    return ("measure", *_revision_key(point, composition))


def _ordered_summary(
    composition: ReportComposition,
) -> list[ReportPoint]:
    """The summary in the order the reader meets it.

    A question that asks for the latest gets the newest recorded vintage
    first *within one measurement*: the audited report led with an 18.2 GW
    forecast two of its own citations already superseded with 19.6 GW, so the
    reader met the older vintage as the answer. Points that measure different
    things keep their written order — reordering across quantities is how a
    2024 actual came to sit below a 2025 forecast and to be labelled its older
    vintage. Every other question keeps the writer's order exactly.
    """
    points = list(composition.summary)
    if len(points) < 2 or not asks_for_the_latest(composition.question):
        return points
    vintages = [point_vintage(point, composition) for point in points]
    if sum(vintage is not None for vintage in vintages) < 2:
        return points
    ordered = list(points)
    groups: dict[tuple[object, ...], list[int]] = {}
    for position, point in enumerate(points):
        if vintages[position] is None:
            continue
        groups.setdefault(_vintage_group(point, composition), []).append(position)
    for positions in groups.values():
        if len(positions) < 2:
            continue
        ranked = sorted(
            positions,
            key=lambda position: tuple(-part for part in vintages[position][0]),
        )
        for slot, position in zip(sorted(positions), ranked):
            ordered[slot] = points[position]
    return ordered


def _summary_entries(
    composition: ReportComposition,
) -> list[tuple[ReportPoint, str]]:
    """Every summary point in reader order, each with the release label it carries.

    Empty for a question that did not ask for recency. Otherwise every dated
    statement whose own measurement appears at more than one release is told
    which release it is — which is what lets a reader see why two figures for
    one year differ — and the older ones are told that they are older. The
    labels are ordered by release date, never by the order the writer happened
    to put them in.
    """
    ordered = _ordered_summary(composition)
    if not asks_for_the_latest(composition.question):
        return [(point, "") for point in ordered]
    releases = [point_vintage(point, composition) for point in ordered]
    groups: dict[tuple[object, ...], list[int]] = {}
    for position, point in enumerate(ordered):
        if releases[position] is None:
            continue
        groups.setdefault(_vintage_group(point, composition), []).append(position)
    labels: dict[int, str] = {}
    for positions in groups.values():
        if len(positions) < 2:
            continue
        newest = max(releases[position][0] for position in positions)
        oldest = min(releases[position][0] for position in positions)
        for position in positions:
            key, _ = releases[position]
            labels[position] = (
                "older release"
                if key < newest
                else "newer release"
                if key > oldest
                else "release"
            )
    return [
        (point, labels.get(position, ""))
        for position, point in enumerate(ordered)
    ]


_UNMATCHED_LABEL = "not matched to a planned question"


def _unmatched_label(point: ReportPoint, composition: ReportComposition) -> str:
    """Say so when a summary figure answers none of the plan's questions.

    A pass whose claim binding failed keeps its evidenced answer in the
    summary rather than emptying it, and this label is how the reader learns
    that the figure was matched to no planned target.
    """
    planned = {
        target.target_id
        for topic in composition.sub_topics
        for target in topic.evidence_targets
    }
    if not planned or not any(
        match.group(1).casefold() in _MEASURE_UNITS
        for match in _MEASURE_UNIT.finditer(point.text)
    ):
        return ""
    claims = {claim.claim_id: claim for claim in composition.claims}
    bound = any(
        planned.intersection(claims[claim_id].target_ids)
        for claim_id in point.claim_ids
        if claim_id in claims
    )
    return "" if bound else _UNMATCHED_LABEL


def _reader_summary(
    composition: ReportComposition,
    index: Sequence[Citation],
    *,
    notes: Mapping[str, str],
) -> str:
    """The answer first: what is established, what would change it, and the
    limitation that matters most — each part driven by statement modes.

    A contested statement is *not* what the evidence establishes, so it is
    grouped separately rather than printed as a settled finding; the summary's
    limitation line names the topic and leaves the sentence itself to the
    uncertainty section, which states it once. ``notes`` carries the reading a
    bullet states about its own claim when the uncertainty section does not
    reprint it; see ``_unestablished_notes``.
    """
    if not composition.summary:
        return REPORT_SUMMARY_FALLBACK
    ordered = [
        (point, ", ".join(part for part in (revision, _unmatched_label(point, composition)) if part))
        for point, revision in _summary_entries(composition)
    ]
    blocks: list[str] = []
    established = [
        item for item in ordered if item[0].mode in {"settled", "inference"}
    ]
    attributed = [item for item in ordered if item[0].mode == "attributed"]
    contested = [item for item in ordered if item[0].mode == "contested"]
    context = [item for item in ordered if item[0].mode == "context"]
    if established:
        blocks.append(
            f"{ESTABLISHED_ANSWER_HEADING}\n\n"
            + _bullets(
                [
                    _point_line(
                        point, composition, index, revision=revision, notes=notes
                    )
                    for point, revision in established
                ]
            )
        )
    if attributed:
        blocks.append(
            f"{ATTRIBUTED_ANSWER_HEADING}\n\n"
            + _bullets(
                [
                    _point_line(
                        point, composition, index, revision=revision, notes=notes
                    )
                    for point, revision in attributed
                ]
            )
        )
    if contested:
        blocks.append(
            f"{COUNTERFACTUAL_ANSWER_HEADING}\n\n"
            + _bullets(
                [
                    _point_line(
                        point, composition, index, revision=revision, notes=notes
                    )
                    for point, revision in contested
                ]
            )
        )
    if context:
        blocks.append(
            _bullets(
                [
                    _point_line(
                        point, composition, index, revision=revision, notes=notes
                    )
                    for point, revision in context
                ]
            )
        )
    blocks.append(_summary_limitation_line(composition, index))
    return "\n\n".join(blocks)


def disclosed_limitations(
    composition: ReportComposition,
    cited: Collection[str] | None = None,
) -> list[str]:
    """The recorded limitations this report's own content makes true.

    ``limitation_reasons`` describes the pass; this describes the report in
    hand. The low-confidence reason's own sentence is about "the sources
    behind these findings", so it is disclosed only when a source the report
    cites carries that score — the audited report told its reader that sources
    behind its findings were low confidence when the flagged source supported
    no finding and appeared in no reference.

    The budget reason is keyed on the route outcome rather than on the
    iteration count: a final allowed pass that cleared the gates and satisfied
    both reviewers is the acceptance it is, and telling its reader the critic
    never accepted the report would be false about the one run whose verdict
    the reader can already see in the status line. Only a ceiling the report
    did not earn discloses it.
    """
    if cited is None:
        cited = {citation.url for citation in reader_citations(composition)}
    low_confidence = {
        normalize_source_url(source.url)
        for source in composition.sources
        if source.evaluation_status == "scored" and source.low_confidence
    }
    exhausted_without_acceptance = (
        composition.iteration >= composition.max_iterations
        and composition.quality_status != QUALITY_STATUS_ACCEPTED
    )
    return [
        reason
        for reason in composition.limitations
        if (
            reason != "low_confidence_sources"
            or bool(low_confidence.intersection(cited))
        )
        and (reason != "max_iterations_reached" or exhausted_without_acceptance)
    ]


def most_consequential_limitation(
    composition: ReportComposition,
    cited: Collection[str] | None = None,
) -> str:
    """The limitation a reader should meet first, or ``""`` when there is none.

    Ranked by consequence rather than by list position. A run the terminal
    checks failed outranks everything the pass recorded; an unanswered
    critical target outranks every recorded reason except that, because a plan
    obligation nobody answered is a hole in the answer itself; and the
    recorded reasons follow in ``LIMITATION_CONSEQUENCE`` order.
    """
    terminal = composition.terminal
    if terminal.status == _FAILED_RUN_STATUS:
        return "the run's own checks failed it"
    unanswered = terminal.critical_targets - terminal.answered_critical_targets
    if unanswered > 0:
        noun = "target" if terminal.critical_targets == 1 else "targets"
        verb = "has" if unanswered == 1 else "have"
        return (
            f"{unanswered} of {terminal.critical_targets} critical {noun} "
            f"{verb} no answer"
        )
    recorded = disclosed_limitations(composition, cited)
    for reason in LIMITATION_CONSEQUENCE:
        if reason in recorded:
            return LIMITATION_TOPICS.get(reason, "")
    for reason in recorded:
        if reason not in LIMITATION_CONSEQUENCE:
            return LIMITATION_TOPICS.get(reason, "")
    return ""


def _summary_limitation_line(
    composition: ReportComposition,
    index: Sequence[Citation],
) -> str:
    """Name the most important unresolved limitation without repeating it."""
    topic = most_consequential_limitation(
        composition, {citation.url for citation in index}
    )
    if not topic:
        return (
            "**The most important unresolved limitation** is that this pass "
            "recorded none."
        )
    return f"**The most important unresolved limitation** is {topic}."


def _claims_for(
    composition: ReportComposition,
    point: ReportPoint,
) -> list[Claim]:
    """The checked claims a rendered point cites, in registry order."""
    wanted = set(point.claim_ids)
    return [claim for claim in composition.claims if claim.claim_id in wanted]


def evidence_badge_label(badge: str | None, *, verdict: str | None = None) -> str:
    """The reader's label for one recorded badge, read through its verdict.

    One place decides what a badge reads as, so the reader table, the review
    packet and the quality counts cannot disagree about one claim. The reading
    is ``evidence_status_bucket`` — the same four buckets the counts use — and
    the verdict is consulted exactly as the counts consult it: the badge is
    stamped before adjudication finishes, so a claim an independent source
    contradicted can still carry ``verified_pair``, and labelling it from the
    raw badge publishes a contradicted fact as "independently corroborated".
    """
    bucket = evidence_status_bucket(badge, verdict=verdict)
    return EVIDENCE_STATUS_LABELS.get(
        bucket, EVIDENCE_STATUS_LABELS["not_established"]
    )


def _evidence_strength(claims: Sequence[Claim]) -> str:
    """The qualitative reading of the badges behind a row, not a probability.

    "verified 0.80" reads as a calibrated probability and is not one: the
    confidence is a model judgement, and a reader who weighs it as a frequency
    has been misled by the artifact. The badge says what a reader actually
    needs — independently corroborated, attribution only, contested, or never
    classified — and the number stays in the ledger's claim registry. Each
    label is read through the claim's verdict, so a contradicted claim prints
    as contested here exactly as it counts in the quality record.
    """
    if not claims:
        return _CELL_EMPTY
    labels: list[str] = []
    for claim in claims:
        label = evidence_badge_label(claim.evidence_status, verdict=claim.verdict)
        if label not in labels:
            labels.append(label)
    return _CELL_SEPARATOR_JOIN.join(labels)


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
    kind = composition.answer_kind or ""
    if kind == "constraints":
        return _reader_constraints(composition, index)
    if kind == "explanation":
        return _reader_explanation(composition, index)
    if kind in ANSWER_TABLE_COLUMNS:
        return _reader_answer_rows(composition, index, kind=kind)
    # An answer form this renderer does not know is rendered as the form its
    # own heading names, which is what ``reader_sections`` fell back to: the
    # heading and the table cannot disagree about what the section is.
    return _reader_constraints(composition, index)


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
            ]
        )
    return _table(
        (
            "Constraint",
            "Deployment mechanism",
            "Geography",
            "Evidence strength",
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
    labels = list(ANSWER_TABLE_COLUMNS.get(kind, ()))
    header = (*labels, "Evidenced finding", "Evidence strength")
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
    *,
    notes: Mapping[str, str],
) -> str:
    blocks: list[str] = []
    for section in composition.sections:
        if not section.points:
            continue
        blocks.append(
            f"### {_clamped(section.title, limit=120)}\n\n"
            + _bullets(
                [
                    _point_line(point, composition, index, notes=notes)
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


def _planned_target_ids(composition: ReportComposition) -> set[str]:
    """Every evidence target this pass's own plan recorded, by id."""
    return {
        target.target_id
        for topic in composition.sub_topics
        for target in topic.evidence_targets
    }


def _claim_identity_keys(
    claim: Claim,
) -> tuple[frozenset[str], frozenset[str]]:
    """A claim's dedup identity: its cluster set, and its own figure + period.

    Two claims that never joined one cluster can still restate one fact —
    consolidation is a separate pass's own job, and this section must not
    assume it always ran — so a claim's own unit-bearing figures, each paired
    with the one year the claim states, are a second identity: either match
    makes two claims one bullet.

    The pairing is withheld whenever the claim states more than one year: a
    claim naming both the period it measures and a release vintage — "10.4 GW
    ... in 2024, per EIA's January 2025 inventory" — would otherwise pair its
    figure with the vintage too, and silently merge with an unrelated claim
    for that other year ("10.4 GW planned for 2025"). One year is the only
    case this text-only heuristic can attribute to the figure with any
    confidence; cluster identity still catches everything else.
    """
    cluster_ids = frozenset(
        cluster_id
        for cluster_id in (claim.cluster_id, *claim.cluster_aliases)
        if cluster_id
    )
    units = [
        match.group(0).casefold()
        for match in _MEASURE_UNIT.finditer(claim.text)
        if match.group(1).casefold() in _MEASURE_UNITS
    ]
    years = set(_YEAR.findall(claim.text))
    figures = (
        frozenset(f"{unit}|{next(iter(years))}" for unit in units)
        if units and len(years) == 1
        else frozenset()
    )
    return cluster_ids, figures


def _uncertain_claim_eligible(
    claim: Claim, planned_targets: set[str]
) -> bool:
    """Whether one checked claim belongs in the uncertainty section at all.

    A contradicted claim is listed unconditionally: a live disagreement is
    never suppressed by which obligation it happens to name. Every other
    verdict is listed only for a claim with no recorded corroboration badge
    at all — ``evidence_status is None`` is exactly "a relay, or an identity
    this pass never resolved" — and only when it touches a target the plan
    actually asked for; a claim answering nothing planned would only clutter
    the section, and a ``source_supported`` issuer figure has already met its
    own obligation and is never printed here as if it had not.
    """
    if claim.verdict == "contradicted":
        return True
    return claim.evidence_status is None and bool(
        planned_targets.intersection(claim.target_ids)
    )


def _claims_considered_in_uncertainty(
    composition: ReportComposition,
) -> set[str]:
    """The checked claims the uncertainty section accounts for, by claim id.

    Read by ``_reader_methodology`` to name the claims that carry no reading
    anywhere in the reader report — not a rendered point, not an uncertainty
    bullet. Those are exactly the claims the evidence ledger, not the reader
    report, is the honest place to find; see the "not used by a statement"
    methodology line.
    """
    planned_targets = _planned_target_ids(composition)
    uncertain_verdicts = {verdict for verdict, _ in _UNCERTAIN_VERDICTS}
    return {
        claim.claim_id
        for claim in composition.claims
        if claim.verdict in uncertain_verdicts
        and _uncertain_claim_eligible(claim, planned_targets)
    }


def _reader_uncertainty(
    composition: ReportComposition,
    index: Sequence[Citation],
    *,
    already: str = "",
) -> str:
    """The uncertainty section, with each fact stated once.

    Every insufficient claim is listed by its own text, and a claim a finding
    above already states arrives here word for word — the audited report
    repeated four of its sixteen claim bullets verbatim, which reads as a
    second fact about the same figure rather than as a recap. ``already`` is
    the text the reader has met above this section; a bullet that repeats one
    of those lines is not printed again, and the group states how many claims
    it did not reprint, so its heading still accounts for every claim under it.
    The same rule holds inside the section: no bullet repeats one already
    printed there either.

    A claim is listed here at all only when its own recorded evidence earns
    it a place: a live contradiction always does, and every other claim only
    when it carries no corroboration badge (a relay, or an identity this pass
    never resolved) *and* touches a target the plan actually asked for. A
    ``source_supported`` issuer claim is never listed here — it has already
    met its own obligation, and its reading is the note on the bullet that
    states it (``_unestablished_notes``), not a second entry in this list.
    Two claims that name one cluster, or state one unit-bearing figure for
    one period, are one bullet: the second is counted, not reprinted, exactly
    as an already-stated claim is.
    """
    blocks: list[str] = []
    cited = {citation.url for citation in index}
    seen = set(_bullet_lines(already))
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
        seen.update(_bullet_lines(_bullets(ungrouped)))
        blocks.append(_bullets(ungrouped))
    for _, heading, _ in UNCERTAINTY_GROUPS:
        if grouped[heading]:
            seen.update(_bullet_lines(_bullets(grouped[heading])))
            blocks.append(f"### {heading}\n\n{_bullets(grouped[heading])}")
    stated_claim_ids = _stated_claim_ids(composition)
    planned_targets = _planned_target_ids(composition)
    seen_cluster_ids: set[str] = set()
    seen_figure_keys: set[str] = set()
    for verdict, heading in _UNCERTAIN_VERDICTS:
        lines: list[str] = []
        repeated = 0
        for claim in composition.claims:
            if claim.verdict != verdict:
                continue
            if not _uncertain_claim_eligible(claim, planned_targets):
                continue
            markers = citation_markers(claim.source_urls, index)
            note = (
                f" — {len(claim.contradictions)} contradicting passage(s)"
                if claim.contradictions
                else ""
            )
            line = f"{' '.join(claim.text.split())}{_marker_suffix(markers)}{note}"
            # Keyed the way ``_bullet_lines`` reads a rendered report, so the
            # line compared here is the line the reader would meet there.
            key = " ".join(f"- {line}".split())
            cluster_ids, figure_keys = _claim_identity_keys(claim)
            is_duplicate = (
                key in seen
                or claim.claim_id in stated_claim_ids
                or bool(cluster_ids & seen_cluster_ids)
                or bool(figure_keys & seen_figure_keys)
            )
            seen_cluster_ids |= cluster_ids
            seen_figure_keys |= figure_keys
            if is_duplicate:
                repeated += 1
                continue
            seen.add(key)
            lines.append(line)
        if lines or repeated:
            body = _bullets(lines)
            if repeated:
                # A sentence, not a bullet: it is the list's own note about
                # itself, and two verdict groups that each withheld one claim
                # would otherwise print the same bullet twice — the defect
                # this suppression exists to remove.
                body += (
                    f"\n\n{repeated} checked claim(s) for this heading are "
                    "already stated above and are not reprinted."
                )
            blocks.append(f"### {heading}\n\n{body}")
    if not blocks:
        blocks.append("(no unresolved claim was recorded for this pass)")
    blocks.append(
        "**Limitations recorded for this pass**\n\n"
        f"{render_limitations(disclosed_limitations(composition, cited))}"
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
    rendered_bullets = _bullet_lines(rendered)
    repeated_bullets = len(rendered_bullets) - len(set(rendered_bullets))
    # A reworded bullet resting only on claims the reader already met is a
    # restatement too; verbatim comparison alone certified the audited report
    # (one 10.4 GW claim stated five times) as free of repeats.
    met: set[str] = set()
    restated = 0
    for point in _reader_points(composition):
        claim_ids = set(point.claim_ids)
        if claim_ids and claim_ids <= met:
            restated += 1
        met.update(claim_ids)
    accounted_claim_ids = cited_claims | _claims_considered_in_uncertainty(
        composition
    )
    unused_claims = len(composition.claims) - len(accounted_claim_ids)
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
        f"{unused_claims} checked claim(s) not used by a statement are "
        "listed in the evidence ledger.",
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
    if restated:
        lines.append(
            f"{restated} statement(s) above restate a checked claim already "
            "stated earlier in the report."
        )
    if not (repeated_bullets or restated):
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
            "recorded error — is a separate artifact, published after the "
            "run's terminal checks had run."
        )
    return _bullets(lines)


# --- the evidence ledger ------------------------------------------------------


def render_evidence_ledger(composition: ReportComposition) -> str:
    """Render the verbose evidence artifact for the same pass.

    Renders exactly the composition it is given, which the build path has
    already fitted: the ledger's "reviewed but not cited" list, its statement
    map and its dispositions describe the same reader report the other
    renderer produces. Fitting here instead — as this used to — let the two
    artifacts disagree about one pass, and let the ledger's own statement count
    describe prose the reader never received.
    """
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
    this pass recorded — including the reasons the build-time fit dropped
    prose the reader does not see — are stated above the table, and the new
    factual assertions waiting for a fact check are named here rather than
    routed from here.

    The dispositions are read from the composition alone, never re-derived:
    the fit ran once, where the composition was built, so a renderer that
    fitted again could report a different statement set than the one it
    renders.
    """
    statements = composition.statements
    prelude = [
        (
            f"{len(statements)} reader statement(s) mapped; "
            f"{composition.distinct_statement_count} distinct fact(s) behind "
            "them."
        )
    ]
    dispositions = list(composition.statement_dispositions)
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
            f"**As of:** {composition.as_of.strip() or _NO_DATED_EVIDENCE}"
            f"{_AS_OF_MEANING if composition.as_of.strip() else ''}",
            f"**Scope:** {composition.scope.strip() or _NO_SCOPE}",
            f"**Quality status:** {composition.quality_status.strip() or _NO_SCOPE}",
            f"**Canonical counts:** {len(composition.sources)} source(s), "
            f"{len(composition.claims)} claim(s), "
            f"{len(composition.findings)} finding(s), {passages} passage(s).",
            # The same terminal record the reader report carries, so the two
            # documents cannot disagree about how the run ended or whether
            # anything judged the report.
            *render_terminal_status(composition.terminal),
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
    """The terse reasons, plus every refused point in full.

    ``composition.rejected`` alone covers every refusal, including the ones
    this artifact has no drafted text for (an unknown label, a narrowed
    citation); ``rejected_points`` names the drafted points that carry one
    — full text, claim ids and urls, never truncated — so a reader can see
    exactly what was drafted and why it did not survive, without replaying
    the provider call that wrote it.
    """
    if not composition.rejected and not composition.rejected_points:
        return "(no drafted content was refused for this pass)"
    bullets = [
        _clamped(reason, limit=_ERROR_MESSAGE_CHARS)
        for reason in composition.rejected
    ]
    bullets.extend(
        f"{point.where}: {point.reason} "
        f"(drafted text: {point.text!r}; claim_ids: {point.claim_ids!r}; "
        f"source_urls: {point.source_urls!r})"
        for point in composition.rejected_points
    )
    return _bullets(bullets)


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


def _sub_topic_skip_reason(error: ResearchError) -> str:
    """The enumerated skip reason one record carries, or ``""`` for anything else.

    Read from the typed ``reason`` detail the producer stamps, never inferred
    from a message: the message is the same sentence for every reason.
    """
    if error.error_type != _SUB_TOPIC_SKIP_ERROR_TYPE:
        return ""
    reason = error.details.get("reason")
    return reason.strip() if isinstance(reason, str) else ""


def is_prior_completion(error: ResearchError) -> bool:
    """True when a skipped sub-topic owed nothing rather than lost coverage.

    ``required_targets_completed`` and ``interim_satisfaction`` are recorded
    when every required target the topic carries is already answered (or, in
    the legacy target-less case, a finding already exists) and the Critic asked
    for no new searches. Omission cost the pass nothing, so a caller must not
    count such a record as a coverage error — and must never print the
    never-researched message for it.
    """
    return _sub_topic_skip_reason(error) in SATISFIED_SKIP_REASONS


def error_reading(error: ResearchError) -> str:
    """The sentence to print for one error record, reason-aware where needed.

    Every record carries its own message except ``researcher_sub_topic_skipped``:
    that type has one message for four reasons and the message describes the
    unattempted case, so a deferred or prior-completion skip would otherwise be
    published as work that was never researched. The reason is enumerated, so
    the reading follows it.
    """
    return (
        SUB_TOPIC_SKIP_MESSAGES.get(_sub_topic_skip_reason(error))
        or error.message
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
            _cell(error_reading(error), limit=_ERROR_MESSAGE_CHARS),
            _cell(_published_details(error), limit=_DETAILS_CHARS),
        ]
        for position, error in enumerate(composition.errors, start=1)
    ]
    return _table(
        ("#", "Type", "Source", "Severity", "Message", "Details"), rows
    )


# --- the quality record -------------------------------------------------------
#
# The third artifact of one pass. The reader report answers "what is settled";
# the evidence ledger answers "what was checked"; this one answers "how would a
# replay verify either of those claims" — from IDs rather than prose, and from
# counts that keep distinct quantities distinct.
#
# It is JSON because a consumer reads it by key, not by eye, and its content is
# bounded by construction: excerpts are clipped to the ledger's own excerpt
# bound, claim and statement text to its claim-text bound, and nothing here
# carries a whole extracted page, a provider payload, or a model prompt.

QUALITY_RECORD_ARTIFACT_NAMES = ("reader_markdown", "evidence_markdown")
"""The artifacts the quality record hashes, and never the quality JSON itself.

A document cannot carry the digest of the bytes that contain that digest, so
the record hashes the two Markdown artifacts and leaves itself out. Both
digests are taken from the exact final bytes published — after the terminal
status stamp — so a reader holding the pair can prove it is the pair the record
describes.
"""

QUALITY_RECORD_EXCERPT_CHARS = _EVIDENCE_CHARS
QUALITY_RECORD_TEXT_CHARS = _CLAIM_TEXT_CHARS

EVIDENCE_STATUS_LABELS: dict[str, str] = {
    "corroborated": "independently corroborated",
    "primary_attributed": (
        "primary-source attribution; independent corroboration not established"
    ),
    "contested": "contested; both sides recorded",
    "not_established": "no corroboration classification recorded",
}
"""The four reader-facing readings of a claim's recorded corroboration.

These are the *counted* buckets, and the only four. "Checked" is not one of
them: how many claims were examined and how many are corroborated are two
different quantities, and a report that prints the first under the second's
name is making a claim its evidence does not support.
"""

_BADGE_BUCKETS: dict[str, str] = {
    "verified_pair": "corroborated",
    "source_supported": "primary_attributed",
    "contested": "contested",
}

# The verdict that overrides the badge: an unresolved material contradiction
# defeats settlement whatever the claim's badge recorded.
_CONTRADICTED_VERDICT = "contradicted"


def evidence_status_bucket(
    badge: str | None, *, verdict: str | None = None
) -> str:
    """One recorded badge and verdict as one of the four counted readings.

    The verdict is consulted exactly as the reader's own statement contract
    consults it (``statement_mode_for_claims``): a contradicted verdict is
    ``contested`` whatever badge was stamped. The badge is written before
    adjudication finishes, so a claim an independent source contradicted can
    still carry ``verified_pair``; reading the badge alone published that claim
    as "independently corroborated" while the reader called the same claim
    contested, and Section 2.1 settles the disagreement the reader's way.

    An absent or unrecognized badge is ``not_established`` — never
    corroborated. A claim nobody classified has not been shown to stand on
    independent support, which is exactly what the bucket name says.
    """
    if verdict == _CONTRADICTED_VERDICT:
        return "contested"
    return _BADGE_BUCKETS.get(badge or "", "not_established")


def evidence_status_counts(claims: Sequence[Claim]) -> dict[str, int]:
    """How many checked claims recorded each corroboration reading."""
    counts = dict.fromkeys(EVIDENCE_STATUS_LABELS, 0)
    for claim in claims:
        counts[
            evidence_status_bucket(claim.evidence_status, verdict=claim.verdict)
        ] += 1
    return counts


def artifact_content_hashes(
    artifacts: Mapping[str, str],
) -> dict[str, JsonValue]:
    """The SHA-256 of each artifact's exact final text, in name order.

    Computed from the bytes as published, never from a re-render: a hash that
    described a document nobody holds would prove nothing about the document
    that was written.
    """
    return {
        name: hashlib.sha256(text.encode("utf-8")).hexdigest()
        for name, text in sorted(artifacts.items())
    }


def distinct_retention_counts(
    state: ResearchState,
    composition: ReportComposition | None,
) -> dict[str, int]:
    """Distinct counts, each of a different thing (Sections 2.3 and 2.5).

    Kept apart on purpose. A physical read call is not a unique validated work
    (two reads can serve one document), a source URL is not a work (a mirror is
    a transport relation), a finding is not a source, and "every assessed
    source" is not "every cited assessed source" — the last run assessed ten
    and cited eight, and one number cannot report both.

    ``unique_works`` is ``retained_work_count``: the identity-resolved count
    over already-retained sources, which resolves an unknown read to its own
    unresolved entry rather than folding it into a neighbour it was never shown
    to match. ``publishers`` counts only sources whose publisher identity is
    established; an unresolved issuer is not a publisher, and nothing here is
    ever derived from memory recall.

    A session with no composition is counted from the state's own canonical
    snapshots — the same records the quality pass reads when a composition
    carries none — so the read-side counts stay truthful rather than becoming
    zeroes. Nothing is cited without a reader report, and that is reported as
    zero cited sources, not as an absent field.
    """
    from deep_research.agents.evidence import (  # noqa: PLC0415
        retained_work_count,
    )

    reads = list(state.read_records.values())
    rows = (
        composition.sources if composition is not None else state.evaluated_sources
    )
    findings = (
        composition.findings if composition is not None else state.raw_findings
    )
    claims = (
        composition.claims if composition is not None else state.verified_claims
    )
    sources = canonical_sources(rows)
    cited = (
        {citation.url for citation in reader_citations(composition)}
        if composition is not None
        else set()
    )
    urls = [normalize_source_url(source.url) for source in sources]
    return {
        "read_records": len(reads),
        "network_reads": sum(
            read.acquisition_kind == "network" for read in reads
        ),
        "cache_reads": sum(read.acquisition_kind == "cache" for read in reads),
        "unique_works": retained_work_count(urls, reads, sources=sources),
        "publishers": len(
            {source.publisher_id for source in sources if source.publisher_id}
        ),
        "source_urls": len(set(urls)),
        "findings": len(deduplicate_findings(findings)),
        "assessed_sources": len(sources),
        "cited_assessed_sources": len(set(urls) & cited),
        "checked_claims": len(canonical_claims(claims)),
    }


def _coverage_counts(
    state: ResearchState,
    composition: ReportComposition | None,
) -> dict[str, JsonValue]:
    """Target and topic progress, read from the snapshot the gates judged.

    The snapshot is preferred because it is the measurement acceptance was
    decided on, and its scalars are published as it recorded them. It does not
    carry every id list, though: it names the unanswered *critical* targets and
    the unaccounted ones and no others, so re-deriving the rest from those two
    fields dropped every accounted non-critical obligation — the "deferred with
    a recorded reason" class — double-listed an unaccounted critical target,
    and published an empty ``accounted_target_ids`` as a positive claim. The
    lists come from ``compute_substantive_coverage`` instead: the same pure
    function that measured the scalars, run against the composition this record
    embeds, so every id it names resolves in the artifact beside it.

    A state holding no snapshot is measured here at the same denominator —
    never a smaller one: an absent record must not read as a completed
    obligation. ``covered_topics`` is the substantive count in both branches,
    because that is what the field meant to a reader of this record; the
    claimed count the snapshot also carries is published beside it as
    ``claimed_covered_topics``, and is omitted — not zeroed — when no snapshot
    recorded one. A snapshot written before the substantive numerator existed
    publishes the count it does carry (``measured_covered_topics``), so a
    legacy record never shows a zero count beside its own nonzero ratio.
    """
    from deep_research.agents.quality import (  # noqa: PLC0415
        compute_substantive_coverage,
    )

    measured = compute_substantive_coverage(state, composition)
    quality = state.quality
    if quality is None:
        return {
            "planned_topics": measured.planned_topics,
            "covered_topics": measured.covered_topics,
            "substantive_topic_ratio": measured.topic_ratio,
            "planned_targets": measured.planned_targets,
            "required_targets": measured.required_targets,
            "answered_targets": measured.answered_targets,
            "critical_targets": measured.critical_targets,
            "answered_critical_targets": measured.answered_critical_targets,
            **_coverage_target_id_lists(measured),
        }
    answered_critical = max(
        0,
        quality.critical_targets - len(quality.unanswered_critical_target_ids),
    )
    return {
        "planned_topics": quality.planned_topics,
        "covered_topics": quality.measured_covered_topics,
        "claimed_covered_topics": quality.covered_topics,
        "substantive_topic_ratio": quality.substantive_topic_ratio,
        "planned_targets": quality.planned_targets,
        "required_targets": quality.required_targets,
        "answered_targets": quality.answered_targets,
        "critical_targets": quality.critical_targets,
        "answered_critical_targets": answered_critical,
        **_coverage_target_id_lists(measured),
    }


def _coverage_target_id_lists(
    measured: SubstantiveCoverage,
) -> dict[str, JsonValue]:
    """The target id lists one measurement publishes, in a fixed order.

    ``accounted`` and ``unaccounted`` partition ``unanswered_required``, and
    each list is the measurement's own — never re-derived from a subset, and
    never emitted empty for a quantity nobody measured.
    """
    return {
        "unanswered_required_target_ids": list(
            measured.unanswered_required_target_ids
        ),
        "unanswered_critical_target_ids": list(
            measured.unanswered_critical_target_ids
        ),
        "accounted_target_ids": list(measured.accounted_target_ids),
        "unaccounted_target_ids": list(measured.unaccounted_target_ids),
        "initial_target_ids": list(measured.initial_target_ids),
        "expanded_target_ids": list(measured.expanded_target_ids),
    }


def _review_record(review: ReportReview | None) -> dict[str, JsonValue]:
    """The semantic judgement, or the honest record that none was made."""
    if review is None:
        return {
            "status": "",
            "mean_score": None,
            "rubric_version": None,
            "input_fingerprint": "",
            "composition_fingerprint": "",
            "coverage_complete": False,
            "unreviewed_statement_ids": [],
            "omitted_evidence_ids": [],
            "unsettled_statement_ids": [],
            "dimensions": {},
            "per_statement_dispositions": {},
            "defects": [],
            "rationale": "",
        }
    return {
        "status": review.status,
        "mean_score": review.mean_score,
        "rubric_version": review.rubric_version,
        "input_fingerprint": review.input_fingerprint,
        "composition_fingerprint": review.composition_fingerprint,
        "coverage_complete": review.coverage_complete,
        "unreviewed_statement_ids": list(review.unreviewed_statement_ids),
        "omitted_evidence_ids": list(review.omitted_evidence_ids),
        "unsettled_statement_ids": review.unsettled_statement_ids,
        "dimensions": dict(review.dimensions),
        "per_statement_dispositions": dict(review.per_statement_dispositions),
        "defects": [
            {
                "gap_id": gap.gap_id,
                "kind": gap.kind,
                "severity": gap.severity,
                "repair_action": gap.repair_action,
                "coverage_id": gap.coverage_id or "",
                "target_ids": list(gap.target_ids),
                "statement_ids": list(gap.statement_ids),
                "claim_cluster_ids": list(gap.claim_cluster_ids),
                "problem": _clamped(gap.problem, limit=QUALITY_RECORD_TEXT_CHARS),
            }
            for gap in review.defects
        ],
        "rationale": _clamped(review.rationale, limit=_RATIONALE_CHARS),
    }


def terminal_report_state(
    state: ResearchState,
    composition: ReportComposition | None,
    *,
    run_status: str,
) -> ReportTerminalState:
    """The terminal record the finalizer stamps onto the published report.

    Every value is read from a record the run already made and none is
    re-derived: the status the router ended on, the Critic's own
    ``review_status``, the semantic review's status, the target counts the
    acceptance gates measured (through ``_coverage_counts``, the same
    measurement the quality record publishes), and the gate names that
    rejected the report. A state with no critique, no review or no quality
    snapshot stamps the absence rather than a clean bill of health.
    """
    coverage = _coverage_counts(state, composition)
    critique = state.critique
    review = state.report_review
    quality = state.quality
    return ReportTerminalState(
        status=run_status,
        critic_status=critique.review_status if critique is not None else "",
        critic_score=(
            critique.score
            if critique is not None and critique.review_status != "failed"
            else None
        ),
        review_status=review.status if review is not None else "",
        review_score=(
            # The review's own recorded mean, never re-derived. It is ``None``
            # unless the review holds all seven dimensions, which the type
            # refuses on a review that did not score — so a score exists
            # exactly when a judgement was made.
            review.mean_score if review is not None else None
        ),
        required_targets=_counted(coverage["required_targets"]),
        answered_targets=_counted(coverage["answered_targets"]),
        critical_targets=_counted(coverage["critical_targets"]),
        answered_critical_targets=_counted(coverage["answered_critical_targets"]),
        gate_failures=list(quality.hard_failures) if quality is not None else [],
    )


def _counted(value: JsonValue) -> int:
    """One measured count as an integer, and never as something else."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _status_record(
    state: ResearchState,
    review: ReportReview | None,
    *,
    session_status: str,
) -> dict[str, JsonValue]:
    """The three terminal statuses, in one place a consumer can address.

    Each answers a different question and none of them is the quality badge:
    ``session`` is how the run ended, ``critic`` is whether the report was
    ever judged and by what, and ``review`` is the semantic review's own
    status. The audited record carried the badge alone, so an operator read
    ``partial`` beside a floor critic score and could not tell that the critic
    never produced a judgement at all. A status nobody recorded stays empty:
    an absent stamp is not a clean one.
    """
    critique = state.critique
    return {
        "session": session_status,
        "critic": critique.review_status if critique is not None else "",
        # A floor score beside a failed review is not a judgement. The CLI and
        # the reader report both refuse to print it; a record that kept it made
        # the three artifacts disagree about whether a critic score exists.
        "critic_score": (
            critique.score
            if critique is not None and critique.review_status != "failed"
            else None
        ),
        "review": review.status if review is not None else "",
    }


def _supporting_spans(
    claims: Sequence[Claim],
) -> dict[tuple[str, str], str]:
    """Every verification passage a claim rests on, by (url, locator).

    The key is the pair the evidence registry is addressed by, so a claim's
    supporting passage resolves to the unit it was selected from without
    either side being re-derived. A claim with several passages keeps the last
    one recorded for a key: they are the same passage of the same source, and
    a later adjudication supersedes an earlier reading of it.
    """
    spans: dict[tuple[str, str], str] = {}
    for claim in claims:
        for passage in claim.verification_evidence:
            key = (normalize_source_url(passage.source_url), passage.locator)
            spans[key] = passage.excerpt
    return spans


def render_quality_record(
    state: ResearchState,
    composition: ReportComposition | None,
    review: ReportReview | None,
    *,
    artifacts: Mapping[str, str] | None = None,
    quality_status: str | None = None,
    session_status: str = "",
) -> dict[str, JsonValue]:
    """The quality JSON: one pass's evidence, decisions and hashes, by ID.

    The record is the replay surface for the other two artifacts. Every reader
    statement is serialized with the claim clusters and evidence units it
    rests on, each of those names the read it came from, and each read names
    its content digest and how it was acquired — so "which source supports
    this sentence, and was it read or remembered" is answerable from the
    record alone, with no prose parsed anywhere.

    Four rules shape what is here, and each is a defect this artifact exists to
    prevent:

    * **distinct quantities stay distinct** (Section 2.5). Read calls, network
      reads, cache reuses, unique works, publishers, source URLs, findings,
      assessed sources, cited assessed sources and checked claims are ten
      different numbers, not one.
    * **counted is not corroborated.** ``evidence_status`` counts the four
      badges a claim actually recorded; a claim nobody classified is counted as
      not established.
    * **no self-reference.** ``artifacts`` hashes the two published Markdown
      documents and never this JSON, and no field here hashes itself.
    * **bounded content.** Excerpts and text are clipped to the ledger's own
      bounds; page bodies, prompts and provider payloads never enter.

    ``artifacts`` maps an artifact name to the exact final text published under
    it. A caller that supplies none gets no hashes rather than invented ones:
    a digest of text nobody published would be a fabricated provenance claim.

    ``composition`` is ``None`` for a session whose Markdown predates the
    composition contract. That is recorded as ``composition_present: false``
    with empty registries and an empty composition fingerprint — never as an
    empty composition's own digest, which would name a report nobody composed.
    ``quality_status`` states the terminal verdict for such a session; without
    it, the record carries the composition's own badge or nothing at all.
    """
    sources = (
        canonical_sources(composition.sources) if composition is not None else []
    )
    claims = (
        canonical_claims(composition.claims) if composition is not None else []
    )
    cited = (
        {citation.url for citation in reader_citations(composition)}
        if composition is not None
        else set()
    )
    reads = sorted(state.read_records.values(), key=lambda read: read.read_id)
    units = (
        sorted(
            composition.evidence_units.values(),
            key=lambda unit: unit.evidence_id,
        )
        if composition is not None
        else []
    )
    clusters = (
        sorted(
            composition.claim_clusters.values(),
            key=lambda cluster: cluster.cluster_id,
        )
        if composition is not None
        else []
    )
    statements = composition.statements if composition is not None else []
    spans = _supporting_spans(claims)
    from deep_research.agents.evidence import (  # noqa: PLC0415
        resolve_retained_work_keys,
    )
    from deep_research.agents.report_review import (  # noqa: PLC0415
        composition_semantic_fingerprint,
    )

    if quality_status is not None:
        status = quality_status
    elif composition is not None:
        status = composition.quality_status
    else:
        status = ""

    record: dict[str, JsonValue] = {
        "quality_contract_version": state.quality_contract_version,
        "composition_present": composition is not None,
        "session_id": state.session_id,
        "iteration": (
            composition.iteration if composition is not None else state.iteration
        ),
        "question": (
            composition.question
            if composition is not None
            else state.original_question
        ),
        "scope": composition.scope if composition is not None else "",
        "as_of": composition.as_of if composition is not None else "",
        "generated_on": (
            composition.generated_on if composition is not None else ""
        ),
        "date_basis": composition.date_basis if composition is not None else "",
        "answer_kind": (
            (composition.answer_kind or "") if composition is not None else ""
        ),
        "quality_status": status,
        "statuses": _status_record(state, review, session_status=session_status),
        "artifacts": artifact_content_hashes(artifacts or {}),
        "rejected_points": (
            [
                _quality_rejected_point_row(point)
                for point in composition.rejected_points
            ]
            if composition is not None
            else []
        ),
        "configuration": {
            "quality_contract_version": state.quality_contract_version,
            "composition_fingerprint": (
                composition_semantic_fingerprint(composition)
                if composition is not None
                else ""
            ),
            "review_input_fingerprint": (
                review.input_fingerprint if review is not None else ""
            ),
            "review_composition_fingerprint": (
                review.composition_fingerprint if review is not None else ""
            ),
            "review_rubric_version": (
                review.rubric_version if review is not None else None
            ),
        },
        "counts": {
            **_coverage_counts(state, composition),
            **distinct_retention_counts(state, composition),
            "verified_claims": sum(
                claim.verdict == "verified" for claim in claims
            ),
            "contradicted_claims": sum(
                claim.verdict == "contradicted" for claim in claims
            ),
            "reader_statements": len(statements),
        },
        "evidence_status": evidence_status_counts(claims),
        "sources": [_quality_source_row(source, cited) for source in sources],
        "reads": [
            {
                "read_id": read.read_id,
                "requested_url": read.requested_url,
                "resolved_url": read.resolved_url,
                "content_sha256": read.content_sha256,
                "extraction_complete": read.extraction_complete,
                "acquisition_kind": read.acquisition_kind,
                "origin_session_id": read.origin_session_id,
                "retrieved_at": read.retrieved_at,
                "locator_count": len(read.passages),
                "target_ids": list(read.target_ids),
            }
            for read in reads
        ],
        # Every retained source URL's work key, from the function the works
        # count counts over — so ``unique_works`` is exactly this map's
        # distinct values, and a source whose identity was never established
        # keeps the key its own read supports instead of vanishing from the
        # account while the count still holds it. Where identity *was*
        # established the key is the persisted one: a re-resolution of the
        # reads without the anchors the Source Evaluator validated would split
        # an original from its mirror.
        "work_keys": dict(
            sorted(
                resolve_retained_work_keys(
                    [source.url for source in sources],
                    reads,
                    sources=sources,
                ).items()
            )
        ),
        "evidence": [
            {
                "evidence_id": unit.evidence_id,
                "read_id": unit.read_id,
                "source_url": unit.source_url,
                "locator": unit.locator,
                "target_ids": list(unit.target_ids),
                "origin": unit.origin,
                # The span a claim rests on, when one was recorded: the page
                # head this used to publish was site navigation, so the row
                # could not be used to check the claim it describes. A unit no
                # claim selected has no supporting span, and publishes its own
                # excerpt under the ledger's bound.
                "excerpt": spans.get(
                    (normalize_source_url(unit.source_url), unit.locator),
                    _clamped(unit.excerpt, limit=QUALITY_RECORD_EXCERPT_CHARS),
                ),
            }
            for unit in units
        ],
        "dispositions": [
            {
                "item_id": disposition.item_id,
                "stage": disposition.stage,
                "reason": _clamped(
                    disposition.reason, limit=QUALITY_RECORD_TEXT_CHARS
                ),
                "target_ids": list(disposition.target_ids),
                "retained_equivalent_id": (
                    disposition.retained_equivalent_id or ""
                ),
            }
            for disposition in state.evidence_dispositions
        ],
        "boundary_audits": [
            {
                "audit_id": audit.audit_id,
                "job_id": audit.job_id,
                "agent_name": audit.agent_name,
                "operation": audit.operation,
                "status": audit.status,
                "target_ids": list(audit.target_ids),
                "claim_cluster_ids": list(audit.claim_cluster_ids),
                "input_ids": list(audit.input_ids),
                "selected_ids": list(audit.selected_ids),
                "returned_ids": list(audit.returned_ids),
                "accepted_ids": list(audit.accepted_ids),
                "deferred_ids": list(audit.deferred_ids),
                "disposition_ids": list(audit.disposition_ids),
                "packet_fingerprint": audit.packet_fingerprint,
                "schema_version": audit.schema_version,
                "configuration_fingerprint": audit.configuration_fingerprint,
            }
            for audit in sorted(
                state.boundary_audits.values(),
                key=lambda audit: audit.audit_id,
            )
        ],
        "claim_clusters": [
            {
                "cluster_id": cluster.cluster_id,
                "status": cluster.status,
                "member_claim_ids": list(cluster.member_claim_ids),
                "evidence_ids": list(cluster.evidence_ids),
                "target_ids": list(cluster.target_ids),
                "source_urls": list(cluster.source_urls),
                "verdicts": list(cluster.verdicts),
                "verdict_evidence_status": dict(
                    cluster.verdict_evidence_status
                ),
                "cluster_aliases": list(cluster.cluster_aliases),
                "consumed_coverage_ids": list(
                    cluster.consumed_coverage_ids
                ),
            }
            for cluster in clusters
        ],
        "claims": [
            {
                "claim_id": claim.claim_id,
                "cluster_id": claim.cluster_id or "",
                "cluster_aliases": list(claim.cluster_aliases),
                # The claim's own text, whole: a display bound is not a
                # publication bound, and publishing a cut claim made the
                # record describe a claim the run never checked — the audit's
                # "recorded only in part" uncertainty is one reader of that
                # cut text.
                "text": claim.text,
                "verdict": claim.verdict,
                "evidence_status": claim.evidence_status or "",
                "confidence": claim.confidence,
                "source_urls": sorted(claim.source_urls),
                "consumed_coverage_ids": list(claim.consumed_coverage_ids),
                "target_ids": list(claim.target_ids),
                "insufficient_reason": claim.insufficient_reason or "",
            }
            for claim in claims
        ],
        "statements": [
            {
                "statement_id": statement.statement_id,
                "mode": statement.mode,
                "text": _clamped(
                    statement.text, limit=QUALITY_RECORD_TEXT_CHARS
                ),
                "claim_cluster_ids": list(statement.claim_cluster_ids),
                "evidence_ids": list(statement.evidence_ids),
                "target_ids": list(statement.target_ids),
                "answered_dimensions": list(statement.answered_dimensions),
                "basis": (
                    _clamped(statement.basis, limit=_RATIONALE_CHARS)
                    if statement.basis
                    else ""
                ),
            }
            for statement in statements
        ],
        "statement_dispositions": (
            list(composition.statement_dispositions)
            if composition is not None
            else []
        ),
        "returned_to_fact_checker": (
            list(composition.returned_to_fact_checker)
            if composition is not None
            else []
        ),
        "errors": [_quality_error_row(error) for error in state.errors],
        "review": _review_record(review),
    }
    return record


def _quality_rejected_point_row(
    point: RejectedDraftPoint,
) -> dict[str, JsonValue]:
    """One refused drafted point, in full: the quality record's companion
    to the evidence ledger's 'Rejected draft content' section — the exact
    drafted text, claim ids and urls, never truncated, so a replay can tell
    which drafted point tripped which reason.
    """
    return {
        "where": point.where,
        "text": point.text,
        "claim_ids": list(point.claim_ids),
        "source_urls": list(point.source_urls),
        "reason": point.reason,
    }


def _quality_error_row(error: ResearchError) -> dict[str, JsonValue]:
    """One error the pass recorded, published no further than the ledger.

    A record the run continued past — a planning defect, a degraded phase, a
    tool failure — is part of what a replay has to be able to verify, so it
    belongs in this artifact beside the claims the gates judged. What is
    published is exactly what the evidence ledger publishes for the same
    record: the type, the source, the severity, and the producer's own reading.
    ``details`` go through ``_published_details``, so the two artifacts cannot
    disagree about which details may be published at all; the sentence is
    clamped like every other text cell here, and page bodies, prompts and
    provider payloads never enter.
    """
    return {
        "error_type": _clamped(error.error_type, limit=_ERROR_MESSAGE_CHARS),
        "source": _clamped(error.source, limit=_ERROR_MESSAGE_CHARS),
        "severity": "recoverable" if error.recoverable else "fatal",
        "message": _clamped(error_reading(error), limit=_ERROR_MESSAGE_CHARS),
        "details": _published_details(error),
    }


def _quality_source_row(
    source: ScoredSource, cited: set[str]
) -> dict[str, JsonValue]:
    """One assessed source with the identity it was resolved to, for replay.

    The aliases, basis, status, issuer, lineage, and validated anchors are
    what let a replay tell a mirror from a second source without re-deriving
    anything; a record written before batch resolution carries none of them
    and says so with an empty status.
    """
    identity = source.work_identity
    url = normalize_source_url(source.url)
    return {
        "url": url,
        "title": source.title,
        "publisher_id": source.publisher_id or "",
        "work_id": source.work_id or "",
        "identity_status": identity.identity_status if identity is not None else "",
        "identity_basis": identity.basis if identity is not None else "",
        "work_aliases": list(identity.aliases) if identity is not None else [],
        "issuer_id": (identity.issuer_id or "") if identity is not None else "",
        "derives_from_work_ids": (
            list(identity.derives_from_work_ids) if identity is not None else []
        ),
        "identity_anchors": {
            name: list(value) if isinstance(value, list) else value
            for name, value in sorted(source.identity_anchors.items())
        },
        "serving_host": source.serving_host or "",
        "transport_relation": source.transport_relation,
        "source_role": source.source_role,
        "evaluation_status": source.evaluation_status,
        "assessment_revision": source.assessment_revision,
        "target_ids": list(source.target_ids),
        "cited": url in cited,
    }


def render_quality_json(
    state: ResearchState,
    composition: ReportComposition | None,
    review: ReportReview | None,
    *,
    artifacts: Mapping[str, str] | None = None,
    quality_status: str | None = None,
    session_status: str = "",
) -> str:
    """The quality record as the bytes that are published.

    Sorted keys and a trailing newline, so the file is stable across runs that
    produced the same record and a diff of two records is readable. Nothing
    here is re-derived from the record: the text returned is what
    ``render_quality_record`` produced, serialized once.
    """
    record = render_quality_record(
        state,
        composition,
        review,
        artifacts=artifacts,
        quality_status=quality_status,
        session_status=session_status,
    )
    return json.dumps(record, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


# --- the evidence-verifier report layout (spec §6.1) --------------------------


_FACTS_HEADER = "| Organisation | Measure | Period | Value | Kind | Scope | Release or edition | Source |"


def figure_label(
    *,
    organisation: str,
    attribution: FigureAttribution,
    relay_host: str | None,
    kind: FigureKind,
    release: str | None,
    unchecked: bool,
) -> str:
    """§6.1's reader label: who, kind (with a forecast's release), edition, unchecked."""
    if attribution == "own":
        who = f"{organisation}'s own figure"
    elif attribution == "relayed":
        who = f"relayed by {relay_host or 'another site'} from {organisation}"
    else:
        who = "source does not attribute it"
    if kind == "forecast":
        # PD-24 (F5): a forecast with no release says so, never silently "forecast"
        parts = [who, f"forecast ({release})" if release else "forecast (release not stated on the page)"]
    else:
        parts = [who, kind]
    if kind == "actual" and release:
        parts.append(release)
    if unchecked:
        parts.append("unchecked context")
    return "; ".join(parts)


def _row_label(row: FactRow) -> str:
    return figure_label(organisation=row.organisation, attribution=row.attribution,
                        relay_host=row.relay_host, kind=row.kind, release=row.release,
                        unchecked=row.context_unchecked)


def _table_cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _findings_by_id(composition: ReportComposition) -> dict[str, Finding]:
    return {finding_fingerprint(finding): finding for finding in composition.findings}


def written_citations(composition: ReportComposition) -> list[Citation]:
    """Only the pages the report cites, numbered in the order a reader meets them."""
    by_id = _findings_by_id(composition)
    ordered: list[str] = []

    def add(url: str) -> None:
        normalized = normalize_source_url(url)
        if normalized and normalized not in ordered:
            ordered.append(normalized)

    for point in composition.summary:
        for url in point.source_urls:
            add(url)
    for row in composition.fact_rows:
        if row.finding_id in by_id:
            add(by_id[row.finding_id].source_url)
    for section in composition.sections:
        for point in section.points:
            for url in point.source_urls:
                add(url)
    titles = {normalize_source_url(s.url): s.title for s in composition.sources}
    for finding in composition.findings:
        titles.setdefault(normalize_source_url(finding.source_url), finding.source_title)
    return [Citation(number=n, url=url, title=titles.get(url, url)) for n, url in enumerate(ordered, start=1)]


def _point_labels(point: ReportPoint, composition: ReportComposition) -> list[str]:
    cited = set(point.statement.finding_ids) if point.statement is not None else set()
    stated = quantities_in(point.text)
    labels: list[str] = []
    for row in composition.fact_rows:
        if row.finding_id not in cited and not cited & set(row.duplicate_finding_ids):
            continue
        if any(same_quantity(r, s) for r in quantities_in(row.value) for s in stated):
            label = _row_label(row)
            if label not in labels:
                labels.append(label)
    return labels


def _written_point(point: ReportPoint, composition: ReportComposition, index: Sequence[Citation]) -> str:
    markers = citation_markers(point.source_urls, index)
    labels = _point_labels(point, composition)
    suffix = f" — *{' | '.join(labels)}*" if labels else ""
    return f"- {point.text} {markers}{suffix}".rstrip()


def _header_counts(composition: ReportComposition) -> str:
    statuses = [f.verification for f in composition.findings if f.verification is not None]
    checked = sum(1 for v in statuses if v.status != "dropped")
    corrected = sum(1 for v in statuses if v.status == "verified_corrected")
    unchecked = sum(1 for v in statuses if v.status != "dropped" and v.context_unchecked)
    dropped = sum(1 for v in statuses if v.status == "dropped")
    return (f"{len(written_citations(composition))} sources cited; {checked} findings checked against "
            f"their pages ({corrected} with corrected context, {unchecked} with unchecked context), "
            f"{dropped} dropped; {len(composition.not_found)} required targets not found.")


def render_written_report(composition: ReportComposition) -> str:
    """§6.1 items 1-6: header, summary, key facts, findings sections, Not found, sources."""
    index = written_citations(composition)
    by_id = _findings_by_id(composition)
    lines = [
        f"# {composition.question}", "",
        f"*As of {composition.as_of or 'not recorded'}. Scope: {composition.scope or 'not recorded'}. "
        f"{_header_counts(composition)}*", "",
        "## Executive summary", "",
    ]
    lines += [_written_point(p, composition, index) for p in composition.summary] or [
        "No summary statement could be printed from the checked findings; the key facts follow."
    ]
    lines += ["", "## Key facts", ""]
    if composition.fact_rows:
        lines += [_FACTS_HEADER, "|---|---|---|---|---|---|---|---|"]
        for row in composition.fact_rows:
            finding = by_id.get(row.finding_id)
            organisation = {
                "own": row.organisation,
                "relayed": f"{row.organisation} (relayed by {row.relay_host})",
                "unattributed": f"{row.organisation} (source does not attribute it)",
            }[row.attribution]
            release = "; ".join([row.release or "not stated", *(
                f"earlier edition {e.value}" + (f" ({e.release})" if e.release else "") for e in row.earlier
            )])
            cells = [organisation, row.measure, row.period or "not stated", row.value,
                     row.kind + (" (unchecked context)" if row.context_unchecked else ""),
                     row.scope or "not stated", release,
                     citation_markers([finding.source_url], index) if finding else ""]
            lines.append("| " + " | ".join(_table_cell(c) for c in cells) + " |")
    else:
        lines.append("No figure passed the Evidence Verifier.")
    for section in composition.sections:
        lines += ["", f"## {section.title}", ""]
        lines += [_written_point(p, composition, index) for p in section.points]
    if composition.not_found:
        lines += ["", "## Not found", ""]
        for target in composition.not_found:
            if target.searched:
                trail = "Searched: " + "; ".join(f'"{q}"' for q in target.queries) + f". Pages read: {len(target.pages_read)}"
                trail += (" (" + ", ".join(target.pages_read[:5]) + ")." if target.pages_read else ".")
            else:
                trail = "Not searched in this run."
            lines.append(f"- **{target.question}** No checked finding answers it. {trail}")
    lines += ["", "## Sources", "", render_citations(index)]
    return "\n".join(lines) + "\n"


def render_finding_log(composition: ReportComposition) -> str:
    """§6.1 item 7: every finding with its snippet and verification, every drop and refusal."""
    labels = {finding_id: label for label, finding_id in composition.finding_labels.items()}
    row_release = {fid: row.release for row in composition.fact_rows
                    for fid in (row.finding_id, *row.duplicate_finding_ids)}
    lines = [f"# Evidence log: {composition.question}", "",
             f"Session {composition.session_id}, pass {composition.iteration}. Every finding the "
             "researcher recorded, with its snippet and its verification result.", "", "## Findings", ""]
    unlabelled = 0
    for finding in composition.findings:
        finding_id = finding_fingerprint(finding)
        label = labels.get(finding_id)
        if label is None:
            unlabelled += 1
            label = f"X{unlabelled:02d}"
        verification = finding.verification
        if verification is None:
            status = "not checked"
        else:
            status = {"verified": "verified", "verified_corrected": "verified with corrections",
                      "dropped": f"dropped ({verification.dropped_reason})"}[verification.status]
            if verification.context_unchecked:
                status += "; context unchecked"
        lines += [f"### {label} — {finding.source_title}", "", f"- Source: {finding.source_url}",
                  f"- Read: {finding.read_id or 'none'}, locator {finding.locator or 'none'}",
                  f'- Snippet: "{finding.snippet or ""}"', f"- Verification: {status}"]
        for result in verification.figure_results if verification else []:
            text = f"{result.figure.value} {result.figure.unit}"
            if result.kept and result.context is not None:
                context = result.context
                line = (f"  - {text}: kept; period {context.period or 'not stated'}; scope "
                        f"{context.scope or 'not stated'}; "
                        + figure_label(organisation=context.organisation, attribution=context.attribution,
                                       relay_host=publisher_identity(finding.source_url), kind=context.kind,
                                       release=row_release.get(finding_id), unchecked=verification.context_unchecked))
                if result.evidence_words:
                    line += f'; evidence words: "{result.evidence_words}"'
                if result.corrected:
                    line += "; corrected"
            else:
                line = f"  - {text}: dropped ({result.dropped_reason})" + (f": {result.reason}" if result.reason else "")
            lines.append(line)
        lines.append("")
    if composition.rejected_points:
        lines += ["## Refused sentences", ""]
        for rejected in composition.rejected_points:
            lines.append(f'- "{rejected.text}" (cited {", ".join(rejected.finding_labels) or "nothing"}): {rejected.reason}')
    return "\n".join(lines) + "\n"
