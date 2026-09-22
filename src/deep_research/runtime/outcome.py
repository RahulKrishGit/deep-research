"""What one finished research session produced, in one object.

``GraphRun`` already carries the session id, the state, the status, and the
trace URL. Three things every front-end needs are recorded somewhere less
convenient: where the two artifacts were published, whether the terminal
quality gates accepted the report, and token totals — which live only in the
tracker's metric records. Deriving them once here keeps the CLI, the API, and
the UI from re-implementing the same archaeology three times.

Every field is read from typed state or a typed terminal event. Nothing here
parses the report Markdown, and nothing here reads report prose: the quality
verdict is ``graph.state.graph_quality_status``, the same pure decision the
router used, and the artifact paths come from the finalizer's own record.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from deep_research.agents.report import (
    distinct_retention_counts,
    evidence_status_counts,
)
from deep_research.graph.errors import (
    PUBLICATION_DOCUMENT_ARTIFACTS,
    PUBLICATION_MEMORY_ARTIFACT,
)
from deep_research.graph.orchestrator import GraphRun
from deep_research.graph.state import graph_quality_status
from deep_research.observability import (
    MetricRecord,
    TokenUsage,
    TokenUsageMetric,
    ToolMetric,
)
from deep_research.request_budget import RequestBudgetSnapshot
from deep_research.utils.types import (
    LEGACY_QUALITY_CONTRACT_VERSION,
    QUALITY_STATUS_ACCEPTED,
    ReportComposition,
    ReportQualitySnapshot,
    ResearchError,
    ResearchEvent,
    ResearchState,
)

# The terminal event the finalizer emits, and the only record of where the
# session's final artifacts were written. It carries *all three* paths, each
# ``None`` unless the whole set was published, so a reader is never pointed at
# an earlier refinement pass's file and never at an incomplete set. Emitted by
# ``graph.events.report_published_event``.
REPORT_WRITTEN_EVENT = "graph.report.published"

REPORT_PATH_METADATA_KEY = "report_path"
EVIDENCE_PATH_METADATA_KEY = "evidence_path"
QUALITY_PATH_METADATA_KEY = "quality_path"

PUBLICATION_FAILURE_ERROR_TYPE = "graph_publication_failed"
"""The enumerated error type one failed terminal write records."""

RESEARCHER_SUB_TOPIC_EVENT = "researcher.sub_topic.completed"
"""The researcher's per-sub-topic completion record, whose metadata carries
the two drop counts and every other count this pass produced."""

DROPPED_DUPLICATE_METADATA_KEY = "findings_dropped_duplicate"
DROPPED_CAP_METADATA_KEY = "findings_dropped_cap"


@dataclass(frozen=True, slots=True)
class ToolCallSummary:
    """One tool's calls, failures and retries during a session.

    Three counts of three different things: a call is one tool invocation, a
    failure is one invocation that raised, and a retry is one extra transport
    attempt an invocation made. A retry is never folded into the call count —
    the tracker's spans record it separately, so the summary reports it
    separately.
    """

    tool_name: str
    calls: int
    failures: int
    retries: int = 0


@dataclass(frozen=True, slots=True)
class DroppedProposals:
    """What the researcher proposed and did not keep, by reason (ruling 7).

    Two reasons, counted apart because they are different events: a duplicate
    is a restatement folded into a finding the pass already held, and a cap
    drop is a distinct finding past the per-sub-topic limit. Neither entered
    research state and neither is a failure — which is why they are their own
    numbers rather than a subtraction from the findings that did.
    """

    duplicates: int
    beyond_cap: int

    @property
    def total(self) -> int:
        """Every proposal the pass dropped, whatever the reason."""
        return self.duplicates + self.beyond_cap


def _terminal_artifact_path(
    stamped: str | None,
    events: Sequence[ResearchEvent],
    *,
    metadata_key: str,
) -> str | None:
    """One terminal artifact's path, from the state stamp and its own event.

    ``stamped`` is the value the finalizer wrote into state from the write
    that actually succeeded, so a state with no events still names its file.
    The *last* terminal publication record wins over it: a resumed run can
    publish more than once, and a record that carries no path (a failed write)
    clears the previous one — a caller must never be pointed at an earlier
    refinement pass's artifact.
    """
    path = stamped if isinstance(stamped, str) and stamped else None
    for event in events:
        if event.event_type != REPORT_WRITTEN_EVENT:
            continue
        candidate = event.metadata.get(metadata_key)
        path = candidate if isinstance(candidate, str) and candidate else None
    return path


def report_path_from_state(state: ResearchState) -> str | None:
    """The path of the session's final reader report, if one was published.

    A refinement pass writes nothing: only the terminal finalizer publishes,
    so the newest ``graph.report.published`` record is the session's own. A
    None value means the Markdown in ``state.report`` was never written to
    disk, whatever an earlier pass managed to save.
    """
    return _terminal_artifact_path(
        state.report_path,
        state.events,
        metadata_key=REPORT_PATH_METADATA_KEY,
    )


def evidence_path_from_state(state: ResearchState) -> str | None:
    """The path of the session's final evidence ledger, if one was published.

    The ledger and the reader report are written by two independent terminal
    writes, so this is ``None`` when the ledger write failed — never the path
    of an earlier pass's ledger. The ledger Markdown in
    ``state.report_evidence`` is authoritative either way.
    """
    return _terminal_artifact_path(
        state.evidence_path,
        state.events,
        metadata_key=EVIDENCE_PATH_METADATA_KEY,
    )


def quality_path_from_state(state: ResearchState) -> str | None:
    """The path of the session's final quality record, if one was published.

    The third terminal write, and read exactly like the other two. An
    incomplete publication advertises none of the three, so ``None`` here
    means the session published no quality record — never that a caller should
    look for an earlier pass's file.
    """
    return _terminal_artifact_path(
        state.quality_path,
        state.events,
        metadata_key=QUALITY_PATH_METADATA_KEY,
    )


def recorded_session_span(events: Sequence[ResearchEvent]) -> float | None:
    """The seconds between the first and last recorded event, or ``None``.

    Read from the events' own timestamps rather than from a clock, so the same
    record always yields the same number and a replayed run reports the span it
    actually covered. One event is not a span, and an unparseable timestamp is
    no measurement at all — both are ``None``, never a zero.
    """
    stamps: list[datetime] = []
    for event in events:
        try:
            stamps.append(datetime.fromisoformat(event.timestamp))
        except ValueError:
            continue
    if len(stamps) < 2:
        return None
    span = (max(stamps) - min(stamps)).total_seconds()
    return span if span > 0 else None


def tool_call_summaries(
    metrics: Sequence[MetricRecord],
    *,
    session_id: str | None = None,
) -> list[ToolCallSummary]:
    """Group one session's tool spans by tool name, alphabetically.

    Each span contributes one call, a failure when it did not succeed, and the
    retries the span itself recorded — so a call that retried twice is still
    one call, with its two retries counted beside it rather than inside it.

    Pass ``session_id`` to exclude spans the tracker accumulated for other
    sessions — a resumed run shares its tracker with the run that made the
    checkpoint, and mixing the two would double-count.
    """
    counts: dict[str, list[int]] = {}
    for metric in metrics:
        if not isinstance(metric, ToolMetric):
            continue
        if session_id is not None and metric.session_id != session_id:
            continue
        entry = counts.setdefault(metric.tool_name, [0, 0, 0])
        entry[0] += 1
        if not metric.success:
            entry[1] += 1
        entry[2] += metric.retry_count
    return [
        ToolCallSummary(
            tool_name=name, calls=calls, failures=failures, retries=retries
        )
        for name, (calls, failures, retries) in sorted(counts.items())
    ]


def _recorded_count(event: ResearchEvent, key: str) -> int:
    """One non-negative integer a recorded event carries, else zero.

    Event metadata is public JSON: a count that is missing, negative, a float,
    a string or a bool is not a measurement this reader can add, and reading
    one as a count would print a number the producer never recorded.
    """
    value = event.metadata.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def total_token_usage(
    metrics: Sequence[MetricRecord],
    *,
    session_id: str | None = None,
) -> TokenUsage:
    """Sum one session's LLM spans' token usage.

    Zero totals mean "no provider reported usage", which is exactly what a
    fully mocked run produces — callers render that as "not available"
    rather than as "zero tokens". Pass ``session_id`` to exclude spans the
    tracker accumulated for other sessions.
    """
    input_tokens = 0
    output_tokens = 0
    for metric in metrics:
        if not isinstance(metric, TokenUsageMetric):
            continue
        if session_id is not None and metric.session_id != session_id:
            continue
        input_tokens += metric.input_tokens
        output_tokens += metric.output_tokens
    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)


@dataclass(frozen=True, slots=True)
class CoverageProgress:
    """Target and topic progress, kept apart (Section 2.3).

    Two denominators and two readings, because they fail differently: nine
    tenths of the targets can be answered while one critical topic is
    untouched, and one blended ratio hides exactly that. ``covered_topics``
    counts topics whose every counted obligation is answered; an unanswered
    critical target is listed whether or not its topic counted as covered.
    """

    planned_topics: int
    covered_topics: int
    substantive_topic_ratio: float
    planned_targets: int
    required_targets: int
    answered_targets: int
    critical_targets: int
    answered_critical_targets: int
    unanswered_critical_target_ids: tuple[str, ...]
    unaccounted_target_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceCounts:
    """Distinct quantities, each of a different thing (Section 2.5).

    Ten counts of ten different things plus the four corroboration readings.
    A read call is not a work; a work is not a publisher; a source URL is not a
    finding; "checked" is not "corroborated". Each field here answers a
    question the others cannot, which is why none of them is an alias of
    another and why a read-call count never stands in for unique works.
    """

    read_records: int = 0
    network_reads: int = 0
    cache_reads: int = 0
    unique_works: int = 0
    publishers: int = 0
    source_urls: int = 0
    findings: int = 0
    assessed_sources: int = 0
    cited_assessed_sources: int = 0
    checked_claims: int = 0
    corroborated: int = 0
    primary_attributed: int = 0
    contested: int = 0
    not_established: int = 0


@dataclass(frozen=True, slots=True)
class ResearchOutcome:
    """Everything one research session produced, ready to render."""

    session_id: str
    question: str
    status: str
    state: ResearchState
    trace_url: str | None
    report_path: str | None
    token_usage: TokenUsage
    tool_calls: tuple[ToolCallSummary, ...]
    evidence_path: str | None = None
    """The file the evidence ledger was published under, or ``None``.

    Derived with ``report_path`` from the terminal publication, so a failed
    ledger write is ``None`` rather than an earlier pass's file.
    """

    quality_path: str | None = None
    """The file the quality record was published under, or ``None``.

    The third path of the same publication: all three are advertised together
    or none is, so this is ``None`` exactly when the published set is
    incomplete.
    """

    duration_seconds: float | None = None
    """The span the session's recorded events cover, or ``None``.

    Read from the events' own timestamps, never from a clock: a replayed
    outcome reports the same span. ``None`` means the record carries fewer than
    two timestamps, which is not a duration of zero.
    """

    quality_contract_version: str = LEGACY_QUALITY_CONTRACT_VERSION
    """Which evidence/quality contract wrote this session's snapshot.

    The legacy version for a snapshot written before the versioned contract
    existed — the honest value, and the one a consumer refuses strict
    acceptance on.
    """

    request_budget_snapshots: tuple[RequestBudgetSnapshot, ...] = ()
    """The run's terminal per-provider attempt and token counts.

    The budget's own immutable snapshots, in its fixed ``deepseek``,
    ``openai``, ``tavily`` order, and empty when no budget was observed at
    all — an injected outcome, or a runtime that carries none. Never
    ``None``: a front-end renders "not recorded" rather than inventing a
    limit, and an absent ceiling stays absent instead of becoming a zero.
    """

    @property
    def report(self) -> str | None:
        """The report Markdown, authoritative whether or not it was written."""
        return self.state.report

    @property
    def errors(self) -> tuple[ResearchError, ...]:
        """Recoverable errors recorded during the session."""
        return tuple(self.state.errors)

    @property
    def failed(self) -> bool:
        """True when the run ended without a judged report.

        Two ways reach it: the graph halted on a non-recoverable failure, or
        the Critic's review never validated, so the report was never accepted.
        Both are ``failed`` rather than ``completed``-with-limitations, and
        ``accepted`` is ``False`` for both.
        """
        return self.status == "failed"

    @property
    def quality(self) -> ReportQualitySnapshot | None:
        """The quality snapshot the terminal gates judged, if one was taken."""
        return self.state.quality

    @property
    def quality_status(self) -> str:
        """The terminal quality verdict, from the decision the router used."""
        return graph_quality_status(self.state)

    @property
    def accepted(self) -> bool:
        """True when the terminal quality status accepted the report."""
        return self.quality_status == QUALITY_STATUS_ACCEPTED

    @property
    def composition(self) -> ReportComposition | None:
        """The typed composition the published artifacts render, if any."""
        return self.state.composition

    @property
    def semantic_review_status(self) -> str:
        """The terminal review's own status, or ``""`` when none was made.

        The quality snapshot's stamped field is the primary source — that is
        the record the gates judged. A snapshot that carries no status at all
        falls back to the stored review record, which is where Task 10 fills it
        from: reading the same vocabulary from its own source is not a second
        vocabulary, and answering "no review" while the state holds a scored
        one would be a false statement about the run.
        """
        quality = self.quality
        status = quality.semantic_review_status if quality is not None else ""
        if status:
            return status
        review = self.state.report_review
        return review.status if review is not None else ""

    @property
    def semantic_review_score(self) -> float | None:
        """The review's mean over the seven dimensions, or ``None``.

        ``None`` means no score was recorded, which is exactly the case for an
        incomplete or provider-failed review. It is never rendered as zero.
        """
        quality = self.quality
        if quality is not None and quality.semantic_review_status:
            return quality.semantic_review_score
        review = self.state.report_review
        return review.mean_score if review is not None else None

    @property
    def semantic_review_fingerprint(self) -> str:
        """The packet fingerprint the stored judgement was made over, or ``""``.

        Read the same way the status and the score are: from the snapshot's
        stamped field, falling back to the review record it was stamped from.
        An empty value means no judgement names a packet, which is what an
        unreviewed report has — never a fingerprint of something else.
        """
        quality = self.quality
        fingerprint = (
            quality.semantic_review_fingerprint if quality is not None else ""
        )
        if fingerprint:
            return fingerprint
        review = self.state.report_review
        return review.input_fingerprint if review is not None else ""

    @property
    def coverage(self) -> CoverageProgress | None:
        """Target and topic progress, or ``None`` when nothing judged it.

        ``covered_topics`` is the substantive count — topics whose every
        counted required target is answered — not the claimed one the snapshot
        also carries for historical artifacts. A field named on
        ``CoverageProgress`` as the measured reading must not publish a topic
        the report never answered. A snapshot written before the numerator
        existed falls back to the count it does carry
        (``measured_covered_topics``), so an old record never reads as zero
        beside its own nonzero ratio.
        """
        quality = self.quality
        if quality is None:
            return None
        answered_critical = max(
            0,
            quality.critical_targets
            - len(quality.unanswered_critical_target_ids),
        )
        return CoverageProgress(
            planned_topics=quality.planned_topics,
            covered_topics=quality.measured_covered_topics,
            substantive_topic_ratio=quality.substantive_topic_ratio,
            planned_targets=quality.planned_targets,
            required_targets=quality.required_targets,
            answered_targets=quality.answered_targets,
            critical_targets=quality.critical_targets,
            answered_critical_targets=answered_critical,
            unanswered_critical_target_ids=tuple(
                quality.unanswered_critical_target_ids
            ),
            unaccounted_target_ids=tuple(quality.unaccounted_target_ids),
        )

    @property
    def evidence_counts(self) -> EvidenceCounts | None:
        """The distinct counts, or ``None`` without a composition to count.

        ``None`` is the honest reading of a session whose Markdown predates the
        composition contract: with no reader report to index, "how many sources
        were cited" has no answer, and a zero would be a claim rather than an
        absence.
        """
        composition = self.state.composition
        if composition is None:
            return None
        return EvidenceCounts(
            **distinct_retention_counts(self.state, composition),
            # ``composition.claims`` is the canonical registry: the type
            # canonicalizes on construction, so re-merging here would only
            # re-derive the same rows.
            **evidence_status_counts(composition.claims),
        )

    @property
    def dropped_proposals(self) -> DroppedProposals | None:
        """The researcher's own drop counts, or ``None`` with no record.

        Read from the sub-topic completion events the researcher emits, which
        carry both counts as metadata — nothing here is re-derived from the
        findings that did enter state. A run whose researcher completed no
        sub-topic reports ``None``: no record is not a measured zero.
        """
        duplicates = 0
        beyond_cap = 0
        recorded = False
        for event in self.state.events:
            if event.event_type != RESEARCHER_SUB_TOPIC_EVENT:
                continue
            recorded = True
            duplicates += _recorded_count(event, DROPPED_DUPLICATE_METADATA_KEY)
            beyond_cap += _recorded_count(event, DROPPED_CAP_METADATA_KEY)
        if not recorded:
            return None
        return DroppedProposals(duplicates=duplicates, beyond_cap=beyond_cap)

    @property
    def failed_publication_artifacts(self) -> tuple[str, ...]:
        """The published set's artifacts whose terminal write did not complete.

        The set is the reader report, its evidence ledger and the quality
        record: the three files advertised together or not at all. A failed
        memory-claim write is recorded under the same error type but is not
        part of the set, so it never withholds a path — reading it as one
        printed "Publication: incomplete … No artifact path is advertised"
        above all three advertised paths.
        """
        return tuple(
            artifact
            for artifact in self._failed_write_artifacts()
            if artifact in PUBLICATION_DOCUMENT_ARTIFACTS
        )

    @property
    def failed_memory_writes(self) -> int:
        """How many writes of a claim to memory failed.

        Counted rather than listed: two failed claims are two lost memory
        records, and the artifact name repeated once per claim says nothing a
        count does not.
        """
        return sum(
            artifact == PUBLICATION_MEMORY_ARTIFACT
            for artifact in self._failed_write_artifacts()
        )

    def _failed_write_artifacts(self) -> tuple[str, ...]:
        """Every failed terminal write's artifact name, in recorded order."""
        return tuple(
            artifact
            for error in self.errors
            if error.error_type == PUBLICATION_FAILURE_ERROR_TYPE
            for artifact in [str(error.details.get("artifact", ""))]
            if artifact
        )


def build_outcome(
    run: GraphRun,
    *,
    metrics: Sequence[MetricRecord],
    request_budget_snapshots: Sequence[RequestBudgetSnapshot] = (),
) -> ResearchOutcome:
    """Fold one graph run and the tracker's metrics into an outcome.

    ``request_budget_snapshots`` defaults to the empty tuple so every existing
    injected and unit caller stays source-compatible: an outcome built without
    a budget simply records none. The same holds for every field Task 11 adds:
    an outcome assembled before them still reads, and the session span is read
    from the run's own recorded events rather than from a clock.
    """
    return ResearchOutcome(
        session_id=run.session_id,
        question=run.state.original_question,
        status=run.status,
        state=run.state,
        trace_url=run.trace_url,
        report_path=report_path_from_state(run.state),
        token_usage=total_token_usage(metrics, session_id=run.session_id),
        tool_calls=tuple(
            tool_call_summaries(metrics, session_id=run.session_id)
        ),
        evidence_path=evidence_path_from_state(run.state),
        quality_path=quality_path_from_state(run.state),
        duration_seconds=recorded_session_span(run.state.events),
        quality_contract_version=run.state.quality_contract_version,
        request_budget_snapshots=tuple(request_budget_snapshots),
    )
