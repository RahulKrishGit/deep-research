"""The two published Markdown artifacts and the quality JSON — pure, offline rendering.

One writer pass composes ``ReportComposition``, and this module turns it into
the reader's three artifacts, all pure functions of that composition (and, for
the quality record, the state and the review):

* :func:`render_written_report` — spec §3: the answer-first skeleton --
  title, evidence line, bottom line, the question-shaped table, part
  sections, what could not be confirmed, and sources -- every citation
  numbered in the order a reader meets it (bottom line, then the table, then
  the sections);
* :func:`render_finding_log` — spec §9: the audit trail -- an "About this
  report" block (counts, scope, exact as-of, the parts, the table's shape),
  every verified figure, every recorded finding with its snippet and its
  Evidence Verifier result, every drop, every refusal, every dropped option
  mark and every unplaced finding;
* :func:`render_quality_json` (built on :func:`render_quality_record`) — the
  replay surface: every finding's verification, every fact row, the table,
  the sources, the parts, every statement's marks, every refusal, and the
  review's own recorded judgement, stored whole (no field here is clamped:
  a report and its audit trail never truncate what the reader or an auditor
  reads).

Nothing here performs I/O, reads a clock, or calls a provider, so every
artifact is a deterministic function of the composition handed to it. ``As
of`` is therefore the newest timestamp the *recorded evidence* carries — a
read's retrieval time or a finding's extraction time — never a graph event
and never a clock read.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from pydantic import JsonValue

from deep_research.agents.evidence import cosmetic_text
from deep_research.agents.figures import parse_figure, quantities_in, same_quantity
from deep_research.agents.identity import (
    deduplicate_findings,
    finding_fingerprint,
    merge_source_snapshot,
)
from deep_research.agents.planner import answer_form_requirement
from deep_research.agents.report_table import _who_text
from deep_research.agents.sources import normalize_source_url, publisher_identity
from deep_research.agents.verified_facts import (
    _period_key,
    answered_target_ids,
    release_text,
    same_organisation,
    subject_names_row,
)
from deep_research.agents.wording import title_segments
from deep_research.utils.types import (
    QUALITY_STATUS_ACCEPTED,
    QUALITY_STATUS_NOT_GATED,
    QUALITY_STATUS_PARTIAL,
    Citation,
    EvidenceTarget,
    FactRow,
    FigureContext,
    FigureKind,
    Finding,
    FindingFigure,
    PageCredit,
    ReadRecord,
    RejectedDraftPoint,
    ReportComposition,
    ReportPart,
    ReportPoint,
    ReportReview,
    ReportSection,
    ReportTable,
    ResearchError,
    ResearchState,
    ScoredSource,
    SubTopic,
    TableCell,
    UnreachablePage,
)

__all__ = [
    "QUALITY_STATUS_ACCEPTED",
    "QUALITY_STATUS_NOT_GATED",
    "QUALITY_STATUS_PARTIAL",
    "SUB_TOPIC_SKIP_MESSAGES",
    "Citation",
    "ReportComposition",
    "ReportPoint",
    "ReportSection",
    "answered_not_stated_targets",
    "artifact_content_hashes",
    "canonical_sources",
    "citation_markers",
    "collapse_mirror_urls",
    "distinct_retention_counts",
    "error_reading",
    "evidence_report_filename",
    "figure_label",
    "quality_report_filename",
    "render_finding_log",
    "render_quality_json",
    "render_quality_record",
    "render_written_report",
    "report_as_of",
    "report_filename",
    "report_scope",
    "written_citations",
]

_CELL_EMPTY = "—"

#: What ends a sentence in prose the renderer builds itself (§10 fallbacks,
#: ``report_scope``'s own sentence joins). A part that already ends with one
#: keeps it, so a sentence the plan supplies is never given two stops.
_SENTENCE_ENDS = (".", "!", "?", "…")

#: What a drafted point's own final character may be, for the purpose of
#: moving its citation markers to just before it (§3.1 rule 3 and rule 5): a
#: full stop most of the time, but a compound sentence may end a clause on a
#: semicolon, colon or comma before its citations and continue.
_STOP_CHARS = ".!?;:,…"

_SUB_TOPIC_SKIP_ERROR_TYPE = "researcher_sub_topic_skipped"

#: The enumerated reasons ``agents.researcher.sub_topic_skipped_error`` stamps
#: on a skip, and the reading each one gets. The producer writes one message
#: per cause: a sub-topic beyond the pass's cap, or a provider failure that
#: stopped the pass before its turn came up. A reason outside this map falls
#: back to the producer's own message rather than guessing.
SUB_TOPIC_SKIP_MESSAGES: dict[str, str] = {
    "cap": (
        "This planned sub-topic was deferred: the pass reached its sub-topic "
        "limit before its turn came up."
    ),
    "provider_failure_stopped_processing": (
        "A planned sub-topic was never researched; a provider failure stopped "
        "the pass before it could run."
    ),
}

_DETAILED_ERROR_TYPES = frozenset(
    {
        "agent_tool_failed",
        "agent_tool_policy_rejected",
        _SUB_TOPIC_SKIP_ERROR_TYPE,
    }
)

#: Characters kept verbatim in a report filename. Narrow on purpose:
#: WriteDocumentTool rejects absolute paths and traversal segments, and a
#: rejected write would lose the artifact.
_FILENAME_SAFE = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")

#: §10: the plain-words reading of one denied candidate's enumerated reason
#: (I2). A reason outside this map, other than the ``unusable_*`` family,
#: prints no reason at all rather than guessing at one.
_UNREACHABLE_REASON_TEXT: dict[str, str] = {
    "access_denied": "access was denied",
    "not_found": "the page could not be found",
    "http_error": "the site returned an error",
    "transport_failure": "the page could not be reached",
    "malformed": "the page's content could not be read",
}

#: §10 Q5: up to this many unreachable pages print in the reader report; the
#: rest (and every one of them, uncapped) are in the evidence log.
_MAX_UNREACHABLE_LINES = 5


def canonical_sources(sources: Sequence[ScoredSource]) -> list[ScoredSource]:
    """One record per canonical URL, in first-seen order."""
    return merge_source_snapshot([], sources)


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
    them is the honest declaration an auditor needs before reading a ranking
    as advice (spec §9's evidence-log "About this report" block; the reader
    report no longer prints this line at all — spec §3.1 rule 9 cuts it).
    They are named by their titles alone: a coverage id ("topic-01") and the
    plan's own count of its sub-topics are the run's bookkeeping, which
    nothing here ever prints. Everything else the report prints is bounded by
    the sources its points cite.
    """
    topics = list(sub_topics)
    assumed = (
        "No geography, population, or period beyond what the cited sources "
        "state is assumed."
    )
    if not topics:
        return assumed
    return f"{_sentence('; '.join(topic.title for topic in topics))} {assumed}"


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


def _published_details(error: ResearchError) -> str:
    """Render ``error.details`` for the error types whose details are bounded.

    ``agent_tool_failed`` is published because its details are produced by the
    ReAct projection, which revalidates every value it copies: the tool name the
    toolset resolved, the iteration, the enumerated error type, and — for
    ``web_scraper`` — the bounded diagnosis (``attempts``, ``retries``,
    ``status_code``, and a media type or the static ``unknown`` marker). Without
    those values a scraper failure is countable but not *classifiable*, which is
    the whole point of classifying it.

    ``agent_tool_policy_rejected`` is published for the same reason, from the
    same projection: the name the toolset resolved, the iteration, and the
    refusing policy's own sentence clamped by the loop's summary bound. A
    refusal that keeps only its tool and iteration can be counted and nothing
    else — a session whose searches were blocked by the acquisition policy
    reads exactly like one whose guessed URLs were rejected, which is a
    diagnosis the record made impossible rather than merely harder.

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


def error_reading(error: ResearchError) -> str:
    """The sentence to print for one error record, reason-aware where needed.

    Every record carries its own message except ``researcher_sub_topic_skipped``:
    that type has one message per cause and the message describes the
    unattempted case, so a deferred skip would otherwise be published as work
    that was never researched. The reason is enumerated, so the reading
    follows it.
    """
    return (
        SUB_TOPIC_SKIP_MESSAGES.get(_sub_topic_skip_reason(error))
        or error.message
    )


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
    zeroes. ``cited_assessed_sources`` reads the pages the *written* report
    actually cites (:func:`written_citations`); a session with no composition
    has no reader report to index, so it is reported as zero cited sources,
    not as an absent field.
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
    sources = canonical_sources(rows)
    cited = (
        {citation.url for citation in written_citations(composition)}
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
    }


def _review_record(review: ReportReview | None) -> dict[str, JsonValue] | None:
    """The semantic judgement, or ``None`` when this pass made none.

    ``mean_score`` is the review's own property — the mean over the seven
    dimensions, and ``None`` without a full set — so the record cannot
    disagree with the acceptance helper that judged the same review. A defect
    keeps its recorded id, kind, severity, materiality and its *whole*
    problem text (D19: never clamped -- no field this module publishes is),
    along with the ``resolution`` a scoped re-review recorded for it
    (``"resolved"``, ``"unresolved"``, or ``None`` for one no scoped review
    has judged yet) and the coverage ids it carries (T5 addendum item 4)
    — the record is the surface a replay checks it on. A review nobody made
    is ``None`` rather than an empty object that reads as a judgement with
    nothing to report.
    """
    if review is None:
        return None
    return {
        "status": review.status,
        "mean_score": review.mean_score,
        "dimensions": dict(review.dimensions),
        "defects": [
            {
                "defect_id": defect.defect_id,
                "kind": defect.kind,
                "severity": defect.severity,
                "material": defect.material,
                "target_ids": list(defect.target_ids),
                "statement_ids": list(defect.statement_ids),
                "problem": defect.problem,
                "resolution": defect.resolution,
                "coverage_ids": list(defect.coverage_ids),
            }
            for defect in review.defects
        ],
        "dispositions": dict(review.per_statement_dispositions),
        "missing_required_target_ids": list(review.missing_required_target_ids),
    }


def _table_record(table: ReportTable | None) -> dict[str, JsonValue] | None:
    """The question-shaped table, cell by cell, with the evidence each carries."""
    if table is None:
        return None
    return {
        "shape": table.shape,
        "columns": list(table.columns),
        "caption": table.caption,
        "rows": [
            [
                {
                    "text": cell.text,
                    "entries": [entry.model_dump(mode="json") for entry in cell.entries],
                    "statement_ids": list(cell.statement_ids),
                    "finding_ids": list(cell.finding_ids),
                    "row_ids": list(cell.row_ids),
                }
                for cell in row
            ]
            for row in table.rows
        ],
    }


def _sources_record(
    composition: ReportComposition, index: Sequence[Citation]
) -> list[dict[str, JsonValue]]:
    """Every printed source, as the Sources line prints it (§8, §9)."""
    rows: list[dict[str, JsonValue]] = []
    for citation in index:
        credit = composition.page_credits.get(normalize_source_url(citation.url))
        publisher = credit.publisher if credit is not None else publisher_identity(citation.url)
        rows.append(
            {
                "number": citation.number,
                "url": citation.url,
                "publisher": publisher,
                "title": _printed_title(citation.title, publisher),
                "date": credit.date if credit is not None else None,
                "date_kind": credit.date_kind if credit is not None else None,
            }
        )
    return rows


def _part_record(part: ReportPart) -> dict[str, JsonValue]:
    return {
        "coverage_id": part.coverage_id,
        "sub_topic_title": part.sub_topic_title,
        "finding_ids": list(part.finding_ids),
        "context_finding_ids": list(part.context_finding_ids),
        "status": part.status,
    }


def _statement_part_map(composition: ReportComposition) -> dict[str, str]:
    """Each statement id to the part that renders it: ``"bottom_line"`` or a
    coverage id (spec §9)."""
    mapping: dict[str, str] = {}
    for point in composition.summary:
        if point.statement is not None:
            mapping.setdefault(point.statement.statement_id, "bottom_line")
    for section in composition.sections:
        for point in section.points:
            if point.statement is not None:
                mapping.setdefault(point.statement.statement_id, section.coverage_id)
    return mapping


def render_quality_record(
    state: ResearchState,
    composition: ReportComposition | None,
    review: ReportReview | None,
    *,
    artifacts: Mapping[str, str] | None = None,
    quality_status: str | None = None,
    session_status: str = "",
) -> dict[str, JsonValue]:
    """The quality JSON: one pass's verified findings, judgements and hashes.

    The record is the replay surface for the other two artifacts. Every kept
    statement is serialized with the findings it cites and its option marks,
    every finding carries the verification the Evidence Verifier recorded for
    it, every fact row and every target under Not found is published as the
    writer composed it, the question-shaped table and every printed source
    are published cell by cell and row by row, and every refused sentence is
    published with the labels it cited and the reason it was refused. So
    "which source supports this sentence, and what did the verifier say about
    it" is answerable from the record alone, with no prose parsed anywhere.

    Three rules shape what is here, and each is a defect this artifact exists
    to prevent:

    * **no self-reference.** ``artifacts`` hashes the two published Markdown
      documents and never this JSON, and no field here hashes itself.
    * **the judgement is the one recorded.** ``review`` publishes the
      reviewer's own status, dimension scores, per-statement dispositions and
      defects; ``mean_score`` is the review's own property, so two artifacts
      cannot disagree about it, and a review nobody made is ``None`` rather
      than a clean bill of health.
    * **nothing here is clamped (D19; no strong limits).** A refused sentence
      carries the drafted text whole, a review defect's ``problem`` prints
      whole, and every recorded error prints its message and source whole: a
      replay has to be able to tell which drafted sentence tripped which
      reason and read every recorded defect and error in full, and a cut text
      would name words nobody wrote or hide words the run did write.

    Every text here is the one the pass recorded, and each is already bounded
    where it was produced — the snippet at ``MAX_SNIPPET_CHARS`` and a drafted
    sentence at the writer's own point limit — never clipped a second time.
    Page bodies, prompts and provider payloads never enter.

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
    findings = (
        _finding_registry_pairs(composition)
        if composition is not None
        else []
    )
    statements = composition.statements if composition is not None else []
    fact_rows = composition.fact_rows if composition is not None else []
    not_found = composition.not_found if composition is not None else []
    refused = (
        composition.rejected_points if composition is not None else []
    )
    index = written_citations(composition) if composition is not None else []
    statement_parts = (
        _statement_part_map(composition) if composition is not None else {}
    )
    from deep_research.agents.report_reviewer import (  # noqa: PLC0415
        composition_semantic_fingerprint,
    )

    if quality_status is not None:
        status = quality_status
    elif composition is not None:
        status = composition.quality_status
    else:
        status = ""

    return {
        # The contract the state carries: new runs stamp
        # ``utils.types.QUALITY_CONTRACT_VERSION``, and a legacy snapshot keeps
        # the version it was written under rather than being re-labelled here.
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
        "answer_kind": composition.answer_kind if composition is not None else None,
        "quality_status": status,
        "session_status": session_status,
        "artifacts": artifact_content_hashes(artifacts or {}),
        # The snapshot the gates judged, as the type records it: a session
        # that took none publishes an empty object rather than zeros, which
        # would read as a measurement nobody made.
        "quality": (
            state.quality.model_dump(mode="json")
            if state.quality is not None
            else {}
        ),
        # The run's own §7.3 reading, as the terminal finalizer stamped it: the
        # run's collector, not this pass's, so the record describes the run the
        # report belongs to. Every number here came from a provider seam, and
        # the config key beside each operation is the knob an operator would
        # raise. ``None`` for a run with no collector, because a row of zeroes
        # would publish a measurement nobody took -- and zeros here would read
        # as an idle run rather than as an unmeasured one. Nothing reads this
        # block back: the run never tunes itself (§12).
        "telemetry": (
            state.run_telemetry.model_dump(mode="json")
            if state.run_telemetry is not None
            else None
        ),
        "review": _review_record(review),
        "findings": [
            _quality_finding_row(label, finding)
            for label, finding in findings
        ],
        "fact_rows": [row.model_dump(mode="json") for row in fact_rows],
        "not_found": [target.model_dump(mode="json") for target in not_found],
        "table": _table_record(composition.table if composition is not None else None),
        "sources": _sources_record(composition, index) if composition is not None else [],
        "parts": (
            [_part_record(part) for part in composition.parts]
            if composition is not None else []
        ),
        "unreachable": (
            [page.model_dump(mode="json") for page in composition.unreachable]
            if composition is not None else []
        ),
        "dropped_marks": (
            list(composition.dropped_marks) if composition is not None else []
        ),
        "statements": [
            {
                "statement_id": statement.statement_id,
                "text": statement.text,
                "finding_ids": list(statement.finding_ids),
                "target_ids": list(statement.target_ids),
                "part": statement_parts.get(statement.statement_id, ""),
                "items": [item.model_dump(mode="json") for item in statement.items],
            }
            for statement in statements
        ],
        "refused_sentences": [
            _quality_rejected_point_row(point) for point in refused
        ],
        # The pass's own recorded errors, published as the ledger publishes
        # them: a replay reads them from here, and the planner's own tests pin
        # the row's shape on this surface.
        "errors": [_quality_error_row(error) for error in state.errors],
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
    }


def _quality_finding_row(
    label: str | None, finding: Finding
) -> dict[str, JsonValue]:
    """One finding the pass checked, with its verification exactly as recorded.

    The verification is published as the Evidence Verifier recorded it, never
    reshaped: its status decides whether the finding is citable, its figure
    results carry the evidence words, the corrections and the drop reasons a
    replay checks the reader's labels against, and a finding recorded under
    two revision editions shares this fingerprint with its twin — which is why
    the *label* is passed in beside it rather than re-derived from
    ``finding_labels``. A finding the registry did not label (a dropped one,
    or a duplicate) publishes no label rather than an invented one.
    """
    verification = finding.verification
    return {
        "id": finding_fingerprint(finding),
        "label": label or "",
        "source_url": finding.source_url,
        "snippet": finding.snippet or "",
        "verification": (
            verification.model_dump(mode="json")
            if verification is not None
            else None
        ),
    }


def _quality_rejected_point_row(
    point: RejectedDraftPoint,
) -> dict[str, JsonValue]:
    """One refused drafted sentence, in full: the quality record's companion
    to the evidence ledger's 'Refused sentences' section — where the sentence
    was drafted, its whole text, the labels it cited and the reason the
    Statement Check or a mechanical rule refused it, so a replay can tell
    which drafted sentence tripped which reason.
    """
    return {
        "where": point.where,
        "text": point.text,
        "finding_labels": list(point.finding_labels),
        "reason": point.reason,
    }


def _quality_error_row(error: ResearchError) -> dict[str, JsonValue]:
    """One error the pass recorded, published no further than the ledger.

    A record the run continued past — a planning defect, a degraded phase, a
    tool failure — is part of what a replay has to be able to verify, so it
    belongs in this artifact beside the findings the gates judged. What is
    published is exactly what the evidence ledger publishes for the same
    record: the type, the source, the severity, and the producer's own
    reading, each printed whole (no strong limits). ``details`` go through
    ``_published_details``, so the two artifacts cannot disagree about which
    details may be published at all, and page bodies, prompts and provider
    payloads never enter.
    """
    return {
        "error_type": error.error_type,
        "source": error.source,
        "severity": "recoverable" if error.recoverable else "fatal",
        "message": error_reading(error),
        "details": _published_details(error),
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


_FACTS_HEADER = "| Organisation | Measure | Period | Value | Kind | Scope | Release or edition | Source |"
_FACTS_HEADER_WITH_SUBJECT = "| Organisation | Subject | Measure | Period | Value | Kind | Scope | Release or edition | Source |"


def figure_label(
    *,
    organisation: str,
    attribution: FigureAttribution,
    relay_host: str | None,
    kind: FigureKind,
    release: str | None,
    unchecked: bool,
    period_resolved_from: str | None = None,
) -> str:
    """The evidence ledger's per-finding label: who, kind (with a forecast's
    release), edition, unchecked.

    Cut from the reader-facing report (spec §3.1 rule 8): kept here because
    the evidence log's per-finding blocks still print it (spec §9) and
    ``report_reviewer.py`` still reads it through ``_row_label``/
    ``_figure_label_for`` for the packet it shows the reviewer (R1).
    """
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
    if period_resolved_from:
        parts.append(f"period resolved from the page date {period_resolved_from}")
    if unchecked:
        parts.append("unchecked context")
    return "; ".join(parts)


def _figure_label_for(finding: Finding, context: FigureContext) -> str:
    """One kept figure's label: ``figure_label`` over the finding that states it.

    The one assembler of a label from a finding (the Report Writer's registry
    lines, and the reviewer's packet, both read this), so a label computed for
    a sentence and a label computed for a finding can never disagree about who
    the figure is credited to.
    """
    return figure_label(
        organisation=context.organisation, attribution=context.attribution,
        relay_host=publisher_identity(finding.source_url) if context.attribution == "relayed" else None,
        kind=context.kind, release=release_text(finding),
        unchecked=bool(finding.verification and finding.verification.context_unchecked),
        period_resolved_from=context.period_resolved_from,
    )


def _row_label(row: FactRow) -> str:
    return figure_label(organisation=row.organisation, attribution=row.attribution,
                        relay_host=row.relay_host, kind=row.kind, release=row.release,
                        unchecked=row.context_unchecked,
                        period_resolved_from=row.period_resolved_from)


def _row_finding_ids(row: FactRow) -> list[str]:
    """Every finding whose page a number in this row comes from, the row's own first.

    A row prints its own value and each earlier edition's, so every printed
    number is traceable only when both pages are cited (PD-9).
    """
    return list(dict.fromkeys([row.finding_id, *(edition.finding_id for edition in row.earlier)]))


def _states_period(text: str, period: str | None) -> bool:
    """Whether ``text`` states ``period``: every word of the period, not only its year.

    The period test the writer's restatement guard needs to tell two rows of
    one value apart (PD-9). A period this test cannot read in the sentence
    ("FY2024" for a row's "2024") is simply not stated, which leaves the row
    in play rather than ruling it out.
    """
    key = _period_key(period)
    if key is None:
        return False
    words = set(re.findall(r"[a-z0-9]+", cosmetic_text(text)))
    return all(word in words for word in key.split())


def _cited_figure_context(finding: Finding, row: FactRow) -> FigureContext | None:
    """The context of ``finding``'s kept figure that states ``row``'s own value."""
    if finding.verification is None:
        return None
    stated = quantities_in(row.value)
    for result in finding.verification.figure_results:
        if not result.kept or result.context is None:
            continue
        quantity = parse_figure(result.figure.value, result.figure.unit)
        if quantity is not None and any(same_quantity(quantity, other) for other in stated):
            return result.context
        if cosmetic_text(f"{result.figure.value} {result.figure.unit}") == cosmetic_text(row.value):
            return result.context
    return None


def _row_label_for(row: FactRow, cited_ids: set[str], by_id: Mapping[str, Finding]) -> str:
    """The row's label as the sentence's own citations read it (D13).

    A sentence that cites a duplicate of a row's fact -- the relayed copy of it,
    say -- carries *that* page's label, not the row primary's: the reader's
    marker points at the page the sentence cites, so a relay is never presented
    as the issuer, and the label is the one the Statement Check read for the
    same sentence.
    """
    if row.finding_id in cited_ids:
        return _row_label(row)
    for fingerprint in row.duplicate_finding_ids:
        finding = by_id.get(fingerprint)
        if fingerprint not in cited_ids or finding is None:
            continue
        context = _cited_figure_context(finding, row)
        if context is not None:
            return _figure_label_for(finding, context)
    return _row_label(row)


def _carried_rows(
    text: str,
    cited_ids: set[str],
    rows: Sequence[FactRow],
    targets: Sequence[EvidenceTarget],
) -> list[FactRow]:
    """The fact rows a sentence carries: cited, same value, its subject, its period.

    The one rule the writer's restatement guard and the unchecked-sentence
    provenance suffix (§3.1 rule 8) both ask of a sentence (Task 5.6c's seam,
    kept as one rule). A row is carried when the sentence cites it or a
    duplicate of it, states its quantity, is about its subject rather than a
    rival's, and -- where the sentence states a period one of those rows
    carries -- states that row's period: two periods of one value are two
    facts (PD-9), so "grew 12 percent in 2024" carries the 2024 row and never
    the 2025 one. A sentence that states no such period leaves every row in
    play, as before.
    """
    stated = quantities_in(text)
    candidates = [
        row for row in rows
        if (row.finding_id in cited_ids or cited_ids & set(row.duplicate_finding_ids))
        and any(same_quantity(r, s) for r in quantities_in(row.value) for s in stated)
    ]
    named = [row for row in candidates
             if subject_names_row(text, row, candidates, targets)]
    dated = [row for row in named if _states_period(text, row.period)]
    return dated or named


def _table_cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _findings_by_id(composition: ReportComposition) -> dict[str, Finding]:
    return {finding_fingerprint(finding): finding for finding in composition.findings}


def written_citations(composition: ReportComposition) -> list[Citation]:
    """Only the pages the report cites, numbered as a reader meets them.

    Spec §5: bottom line, then the table, then the sections. Nothing here
    walks ``fact_rows``: the Key Facts table that used to draw citations from
    every fact row is gone from the reader report (moved, unfiltered, to the
    evidence log, spec §9); the only fact rows the reader report cites are the
    ones the question-shaped table prints, through its cells' page entries and
    finding ids.
    """
    by_id = _findings_by_id(composition)
    ordered: list[str] = []

    def add(url: str) -> None:
        normalized = normalize_source_url(url)
        if normalized and normalized not in ordered:
            ordered.append(normalized)

    for point in composition.summary:
        for url in point.source_urls:
            add(url)
    table = composition.table
    if table is not None:
        for row in table.rows:
            for cell in row:
                for entry in cell.entries:
                    add(entry.source_url)
                for fingerprint in cell.finding_ids:
                    finding = by_id.get(fingerprint)
                    if finding is not None:
                        add(finding.source_url)
    for section in composition.sections:
        for point in section.points:
            for url in point.source_urls:
                add(url)
    titles = {normalize_source_url(s.url): s.title for s in composition.sources}
    for finding in composition.findings:
        titles.setdefault(normalize_source_url(finding.source_url), finding.source_title)
    return [Citation(number=n, url=url, title=titles.get(url, url)) for n, url in enumerate(ordered, start=1)]


def _counted(count: int, singular: str, plural: str) -> str:
    """``count`` with the noun it counts, in the form that count takes."""
    return f"{count} {singular if count == 1 else plural}"


def _sentence(text: str) -> str:
    """``text`` ended with exactly one stop: one it already ends with is kept.

    Used by ``report_scope``, whose own sentence is built from plan text that
    may already end with a stop ("Capacity in the U.S."): appending a second
    one would print two in a row.
    """
    text = text.rstrip()
    if not text or text.endswith(_SENTENCE_ENDS):
        return text
    return f"{text}."


def _reader_as_of(value: str) -> str:
    """A recorded timestamp as a reader meets it, or the value as recorded.

    The record keeps its own precision -- the quality JSON prints ``as_of``
    exactly as recorded -- and only the evidence log's "About this report"
    block loses it, printing the instant to the minute in UTC. The minute is
    truncated, never rounded, so no stamp moves to another day: the last second
    of a day prints as 23:59 of that day. A value that is not a zone-carrying
    timestamp is printed exactly as it was recorded: a date-only stamp has no
    time to name, and a zone-less one names no zone the report may claim.
    """
    if not value.strip():
        return "not recorded"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return value
    return f"{parsed.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC"


def _evidence_date(as_of: str) -> str | None:
    """The UTC date of ``as_of``, or ``None`` when it is empty or unparseable
    (spec §3.1 rule 2)."""
    if not as_of.strip():
        return None
    try:
        parsed = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    except ValueError:
        return None
    return f"{parsed.astimezone(timezone.utc):%Y-%m-%d}"


def _evidence_line(composition: ReportComposition, index: Sequence[Citation]) -> str:
    """§3.1 rule 2: ``Evidence as of {date} · {n} source(s)``, or the honest
    fallback when no evidence date can be read."""
    date = _evidence_date(composition.as_of)
    if date is None:
        return "No source could be checked."
    return f"Evidence as of {date} · {_counted(len(index), 'source', 'sources')}"


#: A trailing closing quote or paren the sentence's own stop can sit inside
#: of ("the best.\"", "5 GW.)"): markers land before the stop, and this run
#: stays after it, unchanged (§3.1 rule 3 P3).
_CLOSING_RUN = re.compile(r'["”’)]*$')

#: An ASCII ellipsis is one unit, not three stops in a row: the markers land
#: before the whole run, never splitting it.
_ELLIPSIS = "..."


def _sentence_with_markers(text: str, markers: str) -> str:
    """Move ``markers`` to just before ``text``'s own final stop (§3.1 rule 3).

    A deterministic move of trailing punctuation that leaves the verified
    words unchanged: whatever punctuation the drafted point already ends with
    -- a period, an ellipsis, or an internal clause's semicolon when the
    point continues -- the markers land immediately before it, never after,
    and never adding a second stop. A trailing closing quote or parenthesis
    is not itself the stop; the markers land before whatever stop it closes
    over, and the quote or parenthesis stays after it. A point with no
    citation prints as written.
    """
    stripped = " ".join(text.split())
    if not markers:
        return stripped
    if not stripped:
        return stripped
    closing = _CLOSING_RUN.search(stripped).group()
    core = stripped[: len(stripped) - len(closing)] if closing else stripped
    if core.endswith(_ELLIPSIS):
        return f"{core[: -len(_ELLIPSIS)]} {markers}{_ELLIPSIS}{closing}"
    if core and core[-1] in _STOP_CHARS:
        return f"{core[:-1]} {markers}{core[-1]}{closing}"
    return f"{stripped} {markers}."


def _who_text_for(
    row: FactRow, cited_ids: set[str], by_id: Mapping[str, Finding],
    page_credits: Mapping[str, PageCredit],
) -> str:
    """The row's Who/when text as the sentence's own citation reads it (D13).

    Mirrors ``_row_label_for``, but builds the findings table's own Who
    wording (``_who_text``) instead of the old ``figure_label`` style: a
    sentence that cites a duplicate of a row's fact -- the relayed copy of
    it, say -- gets that page's own attribution, organisation and dates, not
    the row primary's. A synthetic row-with-the-duplicate's-context is built
    (rather than reimplementing ``_who_text``) because ``_who_text`` reads
    its wording from ``row.attribution``/``row.organisation``/``row.
    relay_host``, which the *cited* page's own context supplies here, and its
    dates from the finding, which ``by_id`` supplies.
    """
    if row.finding_id in cited_ids:
        return _who_text(row, by_id.get(row.finding_id), page_credits)
    for fingerprint in row.duplicate_finding_ids:
        finding = by_id.get(fingerprint)
        if fingerprint not in cited_ids or finding is None:
            continue
        context = _cited_figure_context(finding, row)
        if context is None:
            continue
        relay_host = publisher_identity(finding.source_url) if context.attribution == "relayed" else None
        cited_row = row.model_copy(update={
            "attribution": context.attribution,
            "organisation": context.organisation,
            "relay_host": relay_host,
            "kind": context.kind,
        })
        return _who_text(cited_row, finding, page_credits)
    return _who_text(row, by_id.get(row.finding_id), page_credits)


def _unchecked_provenance(point: ReportPoint, composition: ReportComposition) -> str:
    """§3.1 rule 8's one exception: an ``unchecked`` sentence that carries a
    fact row ends with a deterministic provenance line, in the findings
    table's own Who wording (§4.3) -- the only sentence printed without an
    independent check keeps a provenance line, so the exception is visible.
    The Who text is built from the finding the sentence actually cites
    (D13), never a duplicate's primary the report may not even cite.
    """
    if point.statement is None:
        return ""
    verdict = composition.statement_verdicts.get(point.statement.statement_id)
    if verdict != "unchecked":
        return ""
    cited = set(point.statement.finding_ids)
    targets = [t for topic in composition.sub_topics for t in topic.evidence_targets]
    rows = _carried_rows(point.text, cited, composition.fact_rows, targets)
    if not rows:
        return ""
    by_id = _findings_by_id(composition)
    row = rows[0]
    who = _who_text_for(row, cited, by_id, composition.page_credits)
    return f" (figure: {who})"


def _point_labels(point: ReportPoint, composition: ReportComposition) -> list[str]:
    """The rows a sentence carries: its cited row's quantity, subject and period (D11).

    No longer printed beside a reader-facing sentence (spec §3.1 rule 8 cuts
    the suffix ``_written_point`` used to append); kept because
    ``report_reviewer.py`` reads it to show the reviewer model the same
    provenance words the reader used to see (R1). Only the row whose own
    subject and period the sentence states carries its label, and where the
    candidates' subjects are different things -- a target that names both
    options, say -- the sentence must also name what distinguishes that row
    from each rival, so "Kettle K1 scored 4.5" never takes Kettle K2's label
    (Task 5.6c). Each label is the label of the page the sentence cites, so a
    sentence that cites the relay of a fact carries the relay's label rather
    than the own page's (D13).
    """
    cited = set(point.statement.finding_ids) if point.statement is not None else set()
    targets = [t for topic in composition.sub_topics for t in topic.evidence_targets]
    by_id = _findings_by_id(composition)
    labels: list[str] = []
    for row in _carried_rows(point.text, cited, composition.fact_rows, targets):
        label = _row_label_for(row, cited, by_id)
        if label not in labels:
            labels.append(label)
    return labels


def _rendered_point(
    point: ReportPoint, composition: ReportComposition, index: Sequence[Citation]
) -> str:
    """One printed sentence: its markers before its final stop, plus the one
    unchecked-sentence provenance exception (§3.1 rules 3, 5, 8)."""
    markers = citation_markers(point.source_urls, index)
    return _sentence_with_markers(point.text, markers) + _unchecked_provenance(point, composition)


def _written_bullet(
    point: ReportPoint, composition: ReportComposition, index: Sequence[Citation]
) -> str:
    return f"- {_rendered_point(point, composition, index)}"


def _has_citable_finding(composition: ReportComposition) -> bool:
    """Whether any recorded finding could be cited (spec §4: every status
    except ``dropped``) -- keeps the "nothing answered" sentence honest when
    a vanished part (a redraft carry-over, or every drafted point refused)
    leaves a verified finding unstated rather than truly absent (P1-3).
    """
    return any(
        finding.verification is not None and finding.verification.status != "dropped"
        for finding in composition.findings
    )


def _bottom_line_block(composition: ReportComposition, index: Sequence[Citation]) -> str:
    """§3.1 rule 3 and §10: one paragraph, or a fallback when nothing was
    written.

    The "nothing answered" sentence is reserved for a pass that cites
    nothing at all and holds no citable finding either: an empty ``index``,
    no table, no section with kept points, and no citable finding recorded.
    P1-3's belt and braces -- a required part whose points all vanished,
    through a redraft carry-over or an all-refused draft, must never read as
    "no source answers this" while a verified finding for it exists. An
    empty bottom line over a pass that does cite or hold something gets its
    own honest sentence instead: the every-part-failed wording only when a
    part actually failed (R-7: an ``empty`` part is not a failure -- its
    findings were simply never placed there, spec §6.1 -- so that wording
    would be inaccurate); when nothing failed but no section kept a point
    either, a plain unplaced-findings sentence; else the summary-missing
    sentence (some section was written, only the bottom line was not).
    """
    if composition.summary:
        return " ".join(
            _rendered_point(point, composition, index) for point in composition.summary
        )
    non_empty_parts = [part for part in composition.parts if part.status != "empty"]
    any_part_failed = any(part.status == "failed" for part in composition.parts)
    every_part_failed_line = (
        "This report's sections could not be written this time; "
        f"{'the table and ' if composition.table is not None else ''}"
        "the evidence log shows what was verified."
    )
    unplaced_line = (
        "No finding could be placed in a section this time; the evidence "
        "log lists them."
    )
    if non_empty_parts and all(part.status == "failed" for part in non_empty_parts):
        return every_part_failed_line
    has_content = (
        bool(index)
        or composition.table is not None
        or any(section.points for section in composition.sections)
    )
    if has_content or _has_citable_finding(composition):
        if not any(section.points for section in composition.sections):
            return every_part_failed_line if any_part_failed else unplaced_line
        return (
            "A summary could not be written this time; the sections below "
            "give what was found."
        )
    return "No source we could check answers this question."


def _publisher_for(url: str, composition: ReportComposition) -> str:
    credit = composition.page_credits.get(normalize_source_url(url))
    return credit.publisher if credit is not None else publisher_identity(url)


def _date_suffix(credit: PageCredit | None) -> str:
    """§8's date rule, rendered: a published date as ``(<date>)``, an
    updated-only date as ``(updated <date>)``, and nothing when there is none.
    """
    if credit is None or not credit.date:
        return ""
    return f" (updated {credit.date})" if credit.date_kind == "updated" else f" ({credit.date})"


def _entry_date_suffix(url: str, composition: ReportComposition) -> str:
    return _date_suffix(composition.page_credits.get(normalize_source_url(url)))


def _option_part_cell_text(
    cell: TableCell, composition: ReportComposition, index: Sequence[Citation]
) -> str:
    """§4.2: a part cell's page entries, verdict text then the page's marker,
    a bare marker for a page beyond the cap."""
    if not cell.entries:
        return _CELL_EMPTY
    parts: list[str] = []
    for entry in cell.entries:
        marker = citation_markers([entry.source_url], index)
        if entry.text:
            publisher = _publisher_for(entry.source_url, composition)
            parts.append(f"{_table_cell(entry.text)} — {publisher} {marker}".strip())
        else:
            parts.append(marker)
    return "; ".join(parts)


def _recommended_by_text(
    cell: TableCell, composition: ReportComposition, index: Sequence[Citation]
) -> str:
    """§4.2: the distinct picking pages, ``{Publisher} ({date}) [n]`` -- or,
    when the mark's own entry text credits a relay (R-1: a page reporting
    another body's pick), that credit instead of the bare publisher, so a
    relayed pick is never printed as if the relaying page made it itself.
    """
    if not cell.entries:
        return _CELL_EMPTY
    parts: list[str] = []
    for entry in cell.entries:
        marker = citation_markers([entry.source_url], index)
        date_part = _entry_date_suffix(entry.source_url, composition)
        who = _table_cell(entry.text) if entry.text else _publisher_for(entry.source_url, composition)
        parts.append(f"{who}{date_part} {marker}".strip())
    return "; ".join(parts)


def _finding_ids_markers(
    finding_ids: Sequence[str], by_id: Mapping[str, Finding], index: Sequence[Citation]
) -> str:
    urls = [by_id[fid].source_url for fid in finding_ids if fid in by_id]
    return citation_markers(urls, index)


def _table_cell_text(
    shape: str,
    position: int,
    last: int,
    cell: TableCell,
    composition: ReportComposition,
    index: Sequence[Citation],
    by_id: Mapping[str, Finding],
) -> str:
    """One printed cell, by column position and table shape (§4.2, §4.3)."""
    if shape == "findings":
        if position == last:
            return _finding_ids_markers(cell.finding_ids, by_id, index) or _CELL_EMPTY
        return _table_cell(cell.text) if cell.text else _CELL_EMPTY
    # options
    if position == 0:
        return _table_cell(cell.text) if cell.text else _CELL_EMPTY
    if position == last:
        return _recommended_by_text(cell, composition, index)
    return _option_part_cell_text(cell, composition, index)


def _table_lines(
    table: ReportTable, composition: ReportComposition, index: Sequence[Citation]
) -> list[str]:
    """§3.1 rule 4, §4: the question-shaped table, then its italicised caption."""
    by_id = _findings_by_id(composition)
    last = len(table.columns) - 1
    lines = [
        "| " + " | ".join(_table_cell(column) for column in table.columns) + " |",
        "|" + "---|" * len(table.columns),
    ]
    for row in table.rows:
        cells = [
            _table_cell_text(table.shape, position, last, cell, composition, index, by_id)
            for position, cell in enumerate(row)
        ]
        lines.append("| " + " | ".join(cells) + " |")
    if table.caption:
        lines += ["", f"*{table.caption}*"]
    return lines


#: Characters that would splice an attacker's own destination into the
#: printed link if a page's own (untrusted) title carried them raw: a
#: backslash (to keep the escape itself literal), then the two brackets that
#: could prematurely close ``[title]`` and open ``(url)``.
_TITLE_ESCAPES = (("\\", "\\\\"), ("[", "\\["), ("]", "\\]"))

#: Characters that would break the ``(url)`` destination or the evaluator's
#: own single-token parse of it if a scraped URL carried them raw: a space
#: (not `\S`), and parens/angle brackets (which either close the destination
#: early or require ``<...>`` wrapping that cannot itself hold a space).
#: Percent-encoding keeps the destination one token and the same resource.
_URL_ESCAPES = {" ": "%20", "(": "%28", ")": "%29", "<": "%3C", ">": "%3E"}


def _markdown_safe_title(title: str) -> str:
    """A page title made safe as literal ``[...]`` link text (spec §8)."""
    for character, escaped in _TITLE_ESCAPES:
        title = title.replace(character, escaped)
    return title


def _markdown_safe_url(url: str) -> str:
    """``url`` as a safe, single-token Markdown link destination."""
    return "".join(_URL_ESCAPES.get(character, character) for character in url)


def _stripped_title(title: str, publisher: str) -> str:
    """§8: the page's title minus a first or last segment naming the publisher."""
    segments = title_segments(title)
    if len(segments) < 2:
        return title

    def _names_publisher(segment: str) -> bool:
        return same_organisation(segment, publisher) or cosmetic_text(segment) == cosmetic_text(publisher)

    if _names_publisher(segments[-1]):
        remaining = segments[:-1]
    elif _names_publisher(segments[0]):
        remaining = segments[1:]
    else:
        return title
    return " | ".join(remaining) if remaining else title


def _printed_title(citation_title: str, publisher: str) -> str:
    """The title exactly as it prints: publisher-segment stripped (§8), then
    made Markdown-safe for the ``[...]`` position it prints in -- the one
    title text both the Sources line and the quality JSON's "title as
    printed" field use, so the two can never disagree about what was
    published.
    """
    return _markdown_safe_title(_stripped_title(citation_title, publisher))


def _source_line(citation: Citation, composition: ReportComposition) -> str:
    """§8: ``n. Publisher — [Title](url) (date)``."""
    credit = composition.page_credits.get(normalize_source_url(citation.url))
    publisher = credit.publisher if credit is not None else publisher_identity(citation.url)
    title = _printed_title(citation.title, publisher)
    url = _markdown_safe_url(citation.url)
    return f"{citation.number}. {publisher} — [{title}]({url}){_date_suffix(credit)}"


def _unreachable_reason_text(reason: str) -> str:
    """§10: a denial reason (I2) in plain words, or ``""`` when none is recorded."""
    if not reason:
        return ""
    if reason in _UNREACHABLE_REASON_TEXT:
        return _UNREACHABLE_REASON_TEXT[reason]
    if reason.startswith("unusable_"):
        return "the page's content could not be used"
    return ""


def _unreachable_line(page: UnreachablePage) -> str:
    host = publisher_identity(page.url)
    label = page.title or host
    reason = _unreachable_reason_text(page.reason)
    suffix = f" — {reason}" if reason else ""
    return f"- {label} ({host}){suffix}"


def answered_not_stated_targets(composition: ReportComposition) -> list[str]:
    """Required targets a verified finding answers -- an explicit binding or
    a fallback through the sub-topic -- but that no printed statement states
    and that are not already listed under Not found (spec §10's disclosure
    group). The one place this set is computed: :func:`_could_not_confirm_groups`
    and ``quality.compute_report_quality``'s ``unaccounted_required_targets``
    gate both call this, so the reader's disclosure and the gate that
    exempts a disclosed target from being a hard failure can never disagree
    about which targets these are.
    """
    targets = [t for topic in composition.sub_topics for t in topic.evidence_targets]
    required = [t.target_id for t in targets if t.required]
    answered = answered_target_ids(composition.findings, targets, sub_topics=composition.sub_topics)
    listed = {row.target_id for row in composition.not_found}
    points = [*composition.summary, *(p for section in composition.sections for p in section.points)]
    stated = {identifier for p in points if p.statement is not None
              for identifier in p.statement.finding_ids}
    return [
        t for t in required
        if t in answered and t not in listed and not set(answered[t]) & stated
    ]


def _could_not_confirm_groups(composition: ReportComposition) -> list[list[str]]:
    """§10: each present group, in order -- searched targets, unsearched
    targets, answered-but-unstated targets, failed parts, unreachable pages
    (capped at 5, Q5)."""
    groups: list[list[str]] = []
    searched = [target for target in composition.not_found if target.searched]
    unsearched = [target for target in composition.not_found if not target.searched]
    if searched:
        groups.append([
            "We found no source we could check that answers:",
            *[f"- {target.question}" for target in searched],
        ])
    if unsearched:
        groups.append([
            "This run did not research:",
            *[f"- {target.question}" for target in unsearched],
        ])
    unstated_ids = answered_not_stated_targets(composition)
    if unstated_ids:
        targets_by_id = {
            target.target_id: target
            for topic in composition.sub_topics for target in topic.evidence_targets
        }
        groups.append([
            "We found sources on these but could not state a checked answer:",
            *[f"- {targets_by_id[t].question}" for t in unstated_ids if t in targets_by_id],
        ])
    failed = [part for part in composition.parts if part.status == "failed"]
    if failed:
        groups.append([
            f"We could not write up {part.sub_topic_title}; its sources are "
            "listed in the evidence log."
            for part in failed
        ])
    if composition.unreachable:
        shown = composition.unreachable[:_MAX_UNREACHABLE_LINES]
        lines = [
            "These pages could not be opened:",
            *[_unreachable_line(page) for page in shown],
        ]
        if len(composition.unreachable) > _MAX_UNREACHABLE_LINES:
            lines.append("- and others, listed in the evidence log")
        groups.append(lines)
    return groups


def report_filename(*, session_id: str, iteration: int) -> str:
    """Return a traversal-free ``.md`` filename for one reader report.

    ``session_id`` reaches this from state and may hold anything, so it is
    slugged rather than trusted. Moved here from ``report_writer.py`` (spec
    §3.1 rule 8): the renderer links the artifact family it renders, and
    ``report_writer`` imports ``report``, not the reverse.
    """
    if iteration < 0:
        raise ValueError("iteration must not be negative")
    slug = "".join(
        character if character in _FILENAME_SAFE else "-"
        for character in session_id.strip().casefold()
    ).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return f"report-{slug or 'session'}-{iteration}.md"


def evidence_report_filename(*, session_id: str, iteration: int) -> str:
    """Return the evidence ledger's filename for the same pass.

    Deliberately derived from the reader report's name rather than slugged a
    second time, so the two artifacts of one pass can never disagree about
    which session and iteration they belong to.
    """
    stem = report_filename(session_id=session_id, iteration=iteration)
    return f"{stem.removesuffix('.md')}-evidence.md"


def quality_report_filename(*, session_id: str, iteration: int) -> str:
    """Return the quality record's filename for the same pass.

    Derived the same way the ledger's name is, so the three artifacts of one
    publication are one name family: nothing about which set a file belongs to
    depends on a caller remembering a second slug.
    """
    stem = report_filename(session_id=session_id, iteration=iteration)
    return f"{stem.removesuffix('.md')}-quality.json"


def render_written_report(composition: ReportComposition) -> str:
    """Spec §3: the answer-first skeleton.

    Title, evidence line, bottom line, the question-shaped table, one section
    per non-empty part, what could not be confirmed, and sources -- every
    citation numbered in the order a reader meets it. Cut entirely: the old
    Executive summary, Key facts, Not found, header counts and scope (spec
    §3.1 rule 9); those move to the evidence log (§9).
    """
    index = written_citations(composition)
    lines = [f"# {composition.question}", "", _evidence_line(composition, index)]
    lines += ["", "## Bottom line", "", _bottom_line_block(composition, index)]

    table = composition.table
    if table is not None:
        lines.append("")
        lines.extend(_table_lines(table, composition, index))

    for section in composition.sections:
        if not section.points:
            continue
        lines += ["", f"## {section.title}", ""]
        lines += [_written_bullet(point, composition, index) for point in section.points]

    groups = _could_not_confirm_groups(composition)
    if groups:
        lines += ["", "## What we couldn't confirm", ""]
        lines.append("\n\n".join("\n".join(group) for group in groups))

    if index:
        lines += ["", "## Sources", ""]
        lines.extend(_source_line(citation, composition) for citation in index)

    lines += [
        "",
        "How this was researched: [evidence log]"
        f"({evidence_report_filename(session_id=composition.session_id, iteration=composition.iteration)})",
    ]
    return "\n".join(lines) + "\n"


def _finding_registry_pairs(composition: ReportComposition) -> list[tuple[str | None, Finding]]:
    """Pair each recorded finding with its own registered label, in order.

    R2: ``composition.finding_labels`` maps label -> finding id, and two
    distinct revision editions of one page (an unchanged URL, sub-topic and
    content; only the structured figure or its release differ) share a
    ``finding_fingerprint``. Inverting the dict to id -> label collapses
    that collision: one label overwrites the other, and the log prints one
    label twice while the other never appears. ``finding_registry``'s
    stable sort always gives two same-fingerprint findings the identical
    sort key, so their labels are inserted into ``finding_labels`` in the
    same relative order their ``Finding`` objects already have in
    ``composition.findings`` (the unfiltered snapshot ``citable_findings``
    filters from) -- walking a label queue per id against the findings
    sharing that id, both in list order, always recovers the right pairing.
    """
    queues: dict[str, list[str]] = {}
    for label, finding_id in composition.finding_labels.items():
        queues.setdefault(finding_id, []).append(label)
    pairs: list[tuple[str | None, Finding]] = []
    for finding in composition.findings:
        queue = queues.get(finding_fingerprint(finding))
        pairs.append((queue.pop(0) if queue else None, finding))
    return pairs


# D15: a spelled duration value ("a century", "sixteen years or more")
# already states its own unit; appending "years" beside it, or a fact
# row's own already-combined value string repeating it, restates what the
# value already says ("a century years"). Both shapes -- a figure's
# separate value/unit pair, and a fact row's already-fused value string --
# are checked the same way: whether a duration word appears more than
# once across value and unit together.
_DURATION_WORDS = frozenset({
    "day", "days", "week", "weeks", "month", "months",
    "decade", "decades", "century", "centuries", "year", "years",
})


def _is_duration_word(word: str) -> bool:
    return word.casefold() in _DURATION_WORDS


def _value_already_spells_a_unit(value: str, unit: str) -> bool:
    """Whether ``value`` already spells a duration, making a duration
    ``unit`` appended after it redundant."""
    if not _is_duration_word(unit.strip()):
        return False
    return any(_is_duration_word(word) for word in value.split())


def _figure_value_text(figure: FindingFigure) -> str:
    """``figure``'s value with its unit appended, unless the value already
    spells the same kind of unit (D15)."""
    if _value_already_spells_a_unit(figure.value, figure.unit):
        return figure.value
    return f"{figure.value} {figure.unit}"


def _deduplicated_fact_value(value: str) -> str:
    """``value`` -- a fact row's already-combined "value unit" string -- with
    a trailing unit word dropped when the value it follows already spells
    that same kind of unit (D15): 'a century years' -> 'a century'.
    """
    words = value.split()
    if len(words) < 2:
        return value
    unit_candidate = words[-1]
    value_part = " ".join(words[:-1])
    if _value_already_spells_a_unit(value_part, unit_candidate):
        return value_part
    return value


def _finding_label_map(composition: ReportComposition) -> dict[str, str]:
    """Each finding id to its first-registered label (§9's Verified figures
    Source column prints labels, not citation markers)."""
    mapping: dict[str, str] = {}
    for label, finding_id in composition.finding_labels.items():
        mapping.setdefault(finding_id, label)
    return mapping


def _verified_figures_lines(composition: ReportComposition) -> list[str]:
    """§9: today's Key Facts table, unchanged, moved to the evidence log and
    unfiltered -- every verified figure, including one that answers no
    planned target (decision #4's "full fact-row table"), with finding labels
    in the Source column."""
    rows = composition.fact_rows
    if not rows:
        return ["No figure passed the Evidence Verifier."]
    with_subjects = any(row.subject for row in rows)
    header = _FACTS_HEADER_WITH_SUBJECT if with_subjects else _FACTS_HEADER
    width = header.count("|") - 1
    lines = [header, "|" + "---|" * width]
    id_to_label = _finding_label_map(composition)
    for row in rows:
        organisation = {
            "own": row.organisation,
            "relayed": f"{row.organisation} (relayed by {row.relay_host})",
            "unattributed": f"{row.organisation} (source does not attribute it)",
        }[row.attribution]
        release = "; ".join([row.release or "not stated", *(
            f"earlier edition {edition.value}" + (f" ({edition.release})" if edition.release else "")
            for edition in row.earlier
        )])
        subject = [row.subject or "not stated"] if with_subjects else []
        source = ", ".join(
            id_to_label[fingerprint] for fingerprint in _row_finding_ids(row) if fingerprint in id_to_label
        ) or "not stated"
        cells = [organisation, *subject, row.measure, row.period or "not stated",
                 _deduplicated_fact_value(row.value),
                 row.kind + (" (unchecked context)" if row.context_unchecked else ""),
                 row.scope or "not stated", release, source]
        lines.append("| " + " | ".join(_table_cell(cell) for cell in cells) + " |")
    return lines


def _findings_counts_line(composition: ReportComposition) -> str:
    """§9: the findings counts, moved from the old header and reworded."""
    statuses = [f.verification for f in composition.findings if f.verification is not None]
    verified = sum(1 for v in statuses if v.status == "verified")
    corrected = sum(1 for v in statuses if v.status == "verified_corrected")
    quoted = sum(1 for v in statuses if v.status == "quoted")
    dropped = sum(1 for v in statuses if v.status == "dropped")
    unchecked = sum(1 for v in statuses if v.status != "dropped" and v.context_unchecked)
    return (
        f"{verified} verified, {corrected} verified with corrections, "
        f"{quoted} quoted (snippet found on the page; not checked for context), "
        f"{dropped} dropped; {unchecked} with unchecked context; "
        f"{len(composition.not_found)} required questions unanswered"
    )


def _question_form_line(composition: ReportComposition) -> str:
    if composition.answer_kind is None:
        return "not classified"
    return f"{composition.answer_kind} — {answer_form_requirement(composition.answer_kind)}"


def _part_lines(composition: ReportComposition) -> list[str]:
    """§9: coverage id, sub-topic title, printed title, status, finding count,
    context-only count."""
    printed_titles = {
        section.coverage_id: section.title
        for section in composition.sections if section.coverage_id
    }
    lines = []
    for part in composition.parts:
        printed = printed_titles.get(part.coverage_id, "(not written)")
        lines.append(
            f'- {part.coverage_id}: "{part.sub_topic_title}" '
            f'(printed as "{printed}") — {part.status}; '
            f"{len(part.finding_ids)} findings, {len(part.context_finding_ids)} context-only."
        )
    return lines


def _table_summary_line(table: ReportTable | None) -> str:
    if table is None:
        return "none"
    return f"{table.shape}" + (f" — {table.caption}" if table.caption else "")


def _about_this_report_lines(composition: ReportComposition) -> list[str]:
    """§9's new "About this report" block: evidence as of, printed on, scope,
    the question form, the findings counts, the parts and the table."""
    lines = [
        "## About this report", "",
        f"- Evidence as of: {_reader_as_of(composition.as_of)}",
        f"- Printed on: {composition.generated_on or 'not recorded'}",
        f"- Scope: {composition.scope or 'not recorded'}",
        f"- Question form: {_question_form_line(composition)}",
        f"- Findings: {_findings_counts_line(composition)}",
        "- Parts:",
    ]
    lines.extend(f"  {line}" for line in _part_lines(composition))
    lines.append(f"- Table: {_table_summary_line(composition.table)}")
    return lines


def _unplaced_findings_lines(composition: ReportComposition) -> list[str]:
    """§9's new Unplaced findings: findings no part's partition placed --
    recorded, not written."""
    placed = {
        fingerprint
        for part in composition.parts
        for fingerprint in (*part.finding_ids, *part.context_finding_ids)
    }
    id_to_label = _finding_label_map(composition)
    unplaced = [
        finding for finding in composition.findings
        if finding_fingerprint(finding) not in placed
    ]
    if not unplaced:
        return []
    lines = [
        "## Unplaced findings", "",
        "Findings the researcher recorded that no part's partition placed; "
        "not written, but kept here for audit.", "",
    ]
    for finding in unplaced:
        label = id_to_label.get(finding_fingerprint(finding), "")
        prefix = f"{label} — " if label else ""
        lines.append(f"- {prefix}{finding.source_title} ({finding.source_url})")
    return lines


def _dropped_marks_lines(composition: ReportComposition) -> list[str]:
    """§9's new Dropped option marks: each already a project-generated
    sentence naming the statement and the span that failed §6.4 rule 8."""
    if not composition.dropped_marks:
        return []
    return ["## Dropped option marks", "", *[f"- {mark}" for mark in composition.dropped_marks]]


def _pages_could_not_be_opened_lines(composition: ReportComposition) -> list[str]:
    """§9's new Pages that could not be opened: every one of them, uncapped
    (the reader report caps at 5, Q5; the ledger never does)."""
    if not composition.unreachable:
        return []
    return [
        "## Pages that could not be opened", "",
        *[_unreachable_line(page) for page in composition.unreachable],
    ]


def render_finding_log(composition: ReportComposition) -> str:
    """Spec §9: the audit trail.

    "About this report" (counts, scope, exact as-of, the parts, the table's
    shape and caption), "Verified figures" (today's Key Facts table, moved
    here unfiltered), every recorded finding with its snippet and its
    Evidence Verifier result, "Not found" (unchanged), every page that could
    not be opened (uncapped), every refused sentence, every dropped option
    mark, and every unplaced finding.
    """
    row_release = {fid: row.release for row in composition.fact_rows
                    for fid in (row.finding_id, *row.duplicate_finding_ids)}
    lines = [f"# Evidence log: {composition.question}", "",
             f"Session {composition.session_id}, pass {composition.iteration}. Every finding the "
             "researcher recorded, with its snippet and its verification result.", ""]
    lines.extend(_about_this_report_lines(composition))
    lines += ["", "## Verified figures", ""]
    lines.extend(_verified_figures_lines(composition))
    lines += ["", "## Findings", ""]
    unlabelled = 0
    for label, finding in _finding_registry_pairs(composition):
        finding_id = finding_fingerprint(finding)
        if label is None:
            unlabelled += 1
            label = f"X{unlabelled:02d}"
        verification = finding.verification
        if verification is None:
            status = "not checked"
        else:
            status = {"verified": "verified", "verified_corrected": "verified with corrections",
                      "quoted": "quoted (snippet found on the page; not checked for context)",
                      "dropped": f"dropped ({verification.dropped_reason})"}[verification.status]
            if verification.context_unchecked:
                status += "; context unchecked"
        lines += [f"### {label} — {finding.source_title}", "", f"- Source: {finding.source_url}",
                  f"- Read: {finding.read_id or 'none'}, locator {finding.locator or 'none'}",
                  f'- Snippet: "{finding.snippet or ""}"']
        passage = composition.statement_passages.get(finding_id)
        if passage:
            # Review F5: a snippet is cut at its passage's boundary, so a
            # verdict can rest on the sentence just past the cut. The ledger
            # prints the same bounded passage the Statement Check read.
            lines.append(f'- Passage: "{passage}"')
        lines.append(f"- Verification: {status}")
        for result in verification.figure_results if verification else []:
            text = _figure_value_text(result.figure)
            if result.kept and result.context is not None:
                context = result.context
                line = (f"  - {text}: kept; period {context.period or 'not stated'}; scope "
                        f"{context.scope or 'not stated'}; "
                        + figure_label(organisation=context.organisation, attribution=context.attribution,
                                       relay_host=publisher_identity(finding.source_url), kind=context.kind,
                                       release=release_text(finding) or row_release.get(finding_id),
                                       unchecked=verification.context_unchecked,
                                       period_resolved_from=context.period_resolved_from))
                if result.evidence_words:
                    line += f'; evidence words: "{result.evidence_words}"'
                if result.corrected:
                    line += "; corrected"
            else:
                line = f"  - {text}: dropped ({result.dropped_reason})" + (f": {result.reason}" if result.reason else "")
            lines.append(line)
        lines.append("")
    if composition.not_found:
        # The reader report states each unanswered question and how the search
        # went, without the log; the log is where the log belongs (ev-1 audit A6).
        lines += ["## Not found", "",
                  "Each planned question no checked finding answers, with every query made and "
                  "every page read for it.", ""]
        for target in composition.not_found:
            lines.append(f"### {target.question}")
            if target.searched:
                lines.append("- Searched: "
                             + ("; ".join(f'"{query}"' for query in target.queries) or "(none)"))
                lines.append("- Pages read: "
                             + (", ".join(target.pages_read) or "(none)"))
            else:
                lines.append("- Not searched in this run.")
            lines.append("")
    pages_lines = _pages_could_not_be_opened_lines(composition)
    if pages_lines:
        lines += pages_lines + [""]
    if composition.rejected_points:
        lines += ["## Refused sentences", ""]
        for rejected in composition.rejected_points:
            lines.append(f'- "{rejected.text}" (cited {", ".join(rejected.finding_labels) or "nothing"}): {rejected.reason}')
        lines.append("")
    dropped_lines = _dropped_marks_lines(composition)
    if dropped_lines:
        lines += dropped_lines + [""]
    unplaced_lines = _unplaced_findings_lines(composition)
    if unplaced_lines:
        lines += unplaced_lines
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"
