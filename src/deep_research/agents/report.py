"""The two published Markdown artifacts and the quality JSON — pure, offline rendering.

One writer pass composes ``ReportComposition``, and this module turns it into
the reader's three artifacts, all pure functions of that composition (and, for
the quality record, the state and the review):

* :func:`render_written_report` — §6.1 items 1-6: header, executive summary,
  Key facts table, findings sections and Not found, all cited by code-built
  labels;
* :func:`render_finding_log` — §6.1 item 7: every recorded finding with its
  snippet and its Evidence Verifier result, every drop and every refusal;
* :func:`render_quality_json` (built on :func:`render_quality_record`) — the
  replay surface: every finding's verification, every fact row, every
  refusal, and the review's own recorded judgement.

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
from deep_research.agents.sources import normalize_source_url, publisher_identity
from deep_research.agents.steps import summarize_text
from deep_research.agents.verified_facts import (
    _period_key,
    release_text,
    subject_names_row,
)
from deep_research.utils.types import (
    QUALITY_STATUS_ACCEPTED,
    QUALITY_STATUS_NOT_GATED,
    QUALITY_STATUS_PARTIAL,
    Citation,
    EvidenceTarget,
    FactRow,
    FigureAttribution,
    FigureContext,
    FigureKind,
    Finding,
    ReadRecord,
    RejectedDraftPoint,
    ReportComposition,
    ReportPoint,
    ReportReview,
    ReportSection,
    ResearchError,
    ResearchState,
    ScoredSource,
    SubTopic,
)

__all__ = [
    "QUALITY_RECORD_TEXT_CHARS",
    "QUALITY_STATUS_ACCEPTED",
    "QUALITY_STATUS_NOT_GATED",
    "QUALITY_STATUS_PARTIAL",
    "SUB_TOPIC_SKIP_MESSAGES",
    "Citation",
    "ReportComposition",
    "ReportPoint",
    "ReportSection",
    "artifact_content_hashes",
    "canonical_sources",
    "citation_markers",
    "collapse_mirror_urls",
    "distinct_retention_counts",
    "error_reading",
    "figure_label",
    "render_citations",
    "render_finding_log",
    "render_quality_json",
    "render_quality_record",
    "render_written_report",
    "report_as_of",
    "report_scope",
    "written_citations",
]

# Render bounds. Every one of them clamps a single cell or bullet, so a long
# model-written sentence cannot push a table off the page.
_CLAIM_TEXT_CHARS = 240
_ERROR_MESSAGE_CHARS = 240

_CELL_EMPTY = "—"

#: What ends a sentence in the header. A part that already ends with one keeps
#: it: the scope text is a sentence of its own ("... state is assumed."), so the
#: header adds no second stop of its own (the third pre-flight run printed
#: "is assumed.. 5 sources cited").
_SENTENCE_ENDS = (".", "!", "?", "…")

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
    them is the honest declaration a reader needs before reading a ranking as
    advice. They are named by their titles alone: a coverage id ("topic-01") and
    the plan's own count of its sub-topics are the run's bookkeeping, which the
    reader never needs and the reader report never prints (ev-1 audit A6).
    Everything else the report prints is bounded by the sources its points cite.
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


def render_citations(index: Sequence[Citation]) -> str:
    """Render the numbered reference list the markers point at."""
    lines = [
        f"{citation.number}. {citation.title} — {citation.url}"
        for citation in index
    ]
    return "\n".join(lines) or "(no sources were cited)"


#: What a display bound leaves where it cut. A cut that lands mid-word reads as
#: the source's own wording, so the cut falls between words and the marker
#: says a cut was made, rather than trailing off into an ellipsis a reader
#: cannot tell from the text itself.
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


def _clamped(text: str, *, limit: int) -> str:
    return _display_clamp(text, limit=limit)


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


QUALITY_RECORD_TEXT_CHARS = _CLAIM_TEXT_CHARS


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
    keeps its recorded id, kind, severity and materiality: the gate reads
    materiality, not severity, and the record is the surface a replay checks
    it on. A review nobody made is ``None`` rather than an empty object that
    reads as a judgement with nothing to report.
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
                "problem": _clamped(
                    defect.problem, limit=QUALITY_RECORD_TEXT_CHARS
                ),
            }
            for defect in review.defects
        ],
        "dispositions": dict(review.per_statement_dispositions),
        "missing_required_target_ids": list(review.missing_required_target_ids),
    }


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
    statement is serialized with the findings it cites, every finding carries
    the verification the Evidence Verifier recorded for it — its status, its
    figure results with the evidence words and drop reasons, whether its
    context was unchecked — every fact row and every target under Not found is
    published as the writer composed it, and every refused sentence is
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
    * **a refusal is published, not summarised.** A refused sentence carries
      the drafted text whole: a replay has to be able to tell which drafted
      sentence tripped which reason, and a cut text names a sentence nobody
      wrote.

    Nothing here is clipped a second time: every text is the one the pass
    recorded, and each is already bounded where it was produced — the snippet
    at ``MAX_SNIPPET_CHARS`` and a drafted sentence at the writer's own point
    limit. Page bodies, prompts and provider payloads never enter.

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
        "statements": [
            {
                "statement_id": statement.statement_id,
                "text": statement.text,
                "finding_ids": list(statement.finding_ids),
                "target_ids": list(statement.target_ids),
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
    if period_resolved_from:
        parts.append(f"period resolved from the page date {period_resolved_from}")
    if unchecked:
        parts.append("unchecked context")
    return "; ".join(parts)


def _figure_label_for(finding: Finding, context: FigureContext) -> str:
    """One kept figure's label: ``figure_label`` over the finding that states it.

    The one assembler of a label from a finding (the Report Writer's registry
    lines and figure labels, and the reviewer's packet, all read this), so a
    label computed for a sentence and a label computed for a finding can never
    disagree about who the figure is credited to.
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

    The period test the reader's labels and the writer's restatement guard need
    to tell two rows of one value apart (PD-9). A period this test cannot read
    in the sentence ("FY2024" for a row's "2024") is simply not stated, which
    leaves the row in play rather than ruling it out.
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

    The one rule the reader's labels (:func:`_point_labels`) and the writer's
    restatement guard both ask of a sentence (Task 5.6c's seam, kept as one
    rule). A row is carried when the sentence cites it or a duplicate of it,
    states its quantity, is about its subject rather than a rival's, and -- where
    the sentence states a period one of those rows carries -- states that row's
    period: two periods of one value are two facts (PD-9), so "grew 12 percent
    in 2024" carries the 2024 row and never the 2025 one. A sentence that states
    no such period leaves every row in play, as before.
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
        for fingerprint in _row_finding_ids(row):
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


def _point_labels(point: ReportPoint, composition: ReportComposition) -> list[str]:
    """The rows a sentence carries: its cited row's quantity, subject and period (D11).

    Only the row whose own subject and period the sentence states carries its
    label, and where the candidates' subjects are different things -- a target
    that names both options, say -- the sentence must also name what
    distinguishes that row from each rival, so "Kettle K1 scored 4.5" never
    takes Kettle K2's label (Task 5.6c). Each label is the label of the page the
    sentence cites, so a sentence that cites the relay of a fact carries the
    relay's label rather than the own page's (D13).
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
    # ``not_found`` holds the *required* targets no finding answered
    # (``verified_facts.not_found_targets``), so an empty list proves the
    # required questions were answered and says nothing about optional ones:
    # the sentence claims no more than that (F7).
    unanswered = (
        _counted(len(composition.not_found), "required question", "required questions")
        + " unanswered, listed under Not found."
        if composition.not_found else "every required question is answered."
    )
    return (f"{len(written_citations(composition))} sources cited; {checked} findings checked against "
            f"their pages ({corrected} with corrected context, {unchecked} with unchecked context), "
            f"{dropped} dropped; {unanswered}")


def _counted(count: int, singular: str, plural: str) -> str:
    """``count`` with the noun it counts, in the form that count takes."""
    return f"{count} {singular if count == 1 else plural}"


def _sentence(text: str) -> str:
    """``text`` ended with exactly one stop: one it already ends with is kept.

    Every part of the header is a sentence, and two of them are written from
    strings the plan supplies: the scope's assumed note ends with its own full
    stop, and a sub-topic title may ("Capacity in the U.S."), so
    ``report_scope`` asks this of the list it names too. Appending a stop to a
    part that already ends with one prints two in a row, so a part that does is
    printed as written.
    """
    text = text.rstrip()
    if not text or text.endswith(_SENTENCE_ENDS):
        return text
    return f"{text}."


def _reader_as_of(value: str) -> str:
    """A recorded timestamp as the reader meets it, or the value as recorded.

    The record keeps its own precision -- the quality JSON prints ``as_of``
    exactly as recorded -- and the header prints the instant to the minute in
    UTC, which is the shape a date and time take in prose. The minute is
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


def render_written_report(composition: ReportComposition) -> str:
    """§6.1 items 1-6: header, summary, key facts, findings sections, Not found, sources."""
    index = written_citations(composition)
    by_id = _findings_by_id(composition)
    scope = composition.scope or "not recorded"
    header = (
        f"{_sentence(f'As of {_reader_as_of(composition.as_of)}')} "
        f"{_sentence(f'Scope: {scope}')} "
        f"{_header_counts(composition)}"
    )
    lines = [
        f"# {composition.question}", "",
        f"*{header}*", "",
        "## Executive summary", "",
    ]
    lines += [_written_point(p, composition, index) for p in composition.summary] or [
        "No summary statement could be printed from the checked findings; the key facts follow."
    ]
    # The table is the answer's own facts, so only a row that answers a planned
    # question prints (ev-1 audit A6: its five rows were four copies of a
    # site-wide page counter that answered nothing). A figure that answers no
    # question stays citable in prose and whole in the evidence log, and when no
    # verified figure exists at all the section says so rather than vanishing.
    answered = [row for row in composition.fact_rows if row.target_ids]
    if answered:
        lines += ["", "## Key facts", ""]
        with_subjects = any(row.subject for row in answered)
        lines += (
            [_FACTS_HEADER_WITH_SUBJECT, "|---|---|---|---|---|---|---|---|---|"]
            if with_subjects else [_FACTS_HEADER, "|---|---|---|---|---|---|---|---|"]
        )
        for row in answered:
            source = citation_markers(
                [by_id[fingerprint].source_url for fingerprint in _row_finding_ids(row)
                 if fingerprint in by_id],
                index,
            )
            organisation = {
                "own": row.organisation,
                "relayed": f"{row.organisation} (relayed by {row.relay_host})",
                "unattributed": f"{row.organisation} (source does not attribute it)",
            }[row.attribution]
            release = "; ".join([row.release or "not stated", *(
                f"earlier edition {e.value}" + (f" ({e.release})" if e.release else "") for e in row.earlier
            )])
            subject = [row.subject or "not stated"] if with_subjects else []
            cells = [organisation, *subject, row.measure, row.period or "not stated", row.value,
                     row.kind + (" (unchecked context)" if row.context_unchecked else ""),
                     row.scope or "not stated", release, source]
            lines.append("| " + " | ".join(_table_cell(c) for c in cells) + " |")
    elif not composition.fact_rows:
        lines += ["", "## Key facts", "", "No figure passed the Evidence Verifier."]
    for section in composition.sections:
        lines += ["", f"## {section.title}", ""]
        lines += [_written_point(p, composition, index) for p in section.points]
    if composition.not_found:
        lines += [
            "", "## Not found", "",
            "Each entry is a planned question that no checked finding answers. What the search "
            "did for it is recorded in full in the evidence log.",
        ]
        for target in composition.not_found:
            if target.searched:
                trail = (f"{_counted(len(target.queries), 'search', 'searches')} made, "
                         f"{_counted(len(target.pages_read), 'page', 'pages')} read.")
            else:
                trail = "not searched in this run."
            lines.append(f"- **{target.question}** No checked finding answers it: {trail}")
    lines += ["", "## Sources", "", render_citations(index)]
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


def render_finding_log(composition: ReportComposition) -> str:
    """§6.1 item 7: every finding with its snippet and verification, every drop and refusal."""
    row_release = {fid: row.release for row in composition.fact_rows
                    for fid in (row.finding_id, *row.duplicate_finding_ids)}
    lines = [f"# Evidence log: {composition.question}", "",
             f"Session {composition.session_id}, pass {composition.iteration}. Every finding the "
             "researcher recorded, with its snippet and its verification result.", "", "## Findings", ""]
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
            text = f"{result.figure.value} {result.figure.unit}"
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
    if composition.rejected_points:
        lines += ["## Refused sentences", ""]
        for rejected in composition.rejected_points:
            lines.append(f'- "{rejected.text}" (cited {", ".join(rejected.finding_labels) or "nothing"}): {rejected.reason}')
    return "\n".join(lines) + "\n"
