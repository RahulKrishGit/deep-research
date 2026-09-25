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

from deep_research.agents.report import distinct_retention_counts
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
# an earlier pass's file and never at an incomplete set. Emitted by
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
    pass's artifact.

    A metadata key this reader does not find reads as ``None`` rather than
    raising, and that tolerance lives here, on the reading side: a default on
    an event field cannot make an already-written record readable, and this is
    where the record's own fields are read.
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
    """Required-target progress, and what the report could not answer.

    Two readings of one denominator, kept apart because they answer different
    questions: ``missing_required_target_ids`` is the gate's own reading —
    every required target no verified finding answers — while
    ``not_found_target_ids`` is the report's own account of what it searched
    for and did not find. A missing target the report lists under Not found is
    accounted for and published; a missing target no list names is the gate
    failure. Reading only one of the two would either hide an unaccounted
    obligation or report an accounted one as a defect.
    """

    required_targets: int
    answered_targets: int
    missing_required_target_ids: tuple[str, ...]
    not_found_target_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceCounts:
    """Distinct quantities, each of a different thing (Section 2.5).

    A read call is not a work; a work is not a publisher; a source URL is not a
    finding; "checked" is not "cited". Each field here answers a question the
    others cannot, which is why none of them is an alias of another and why a
    read-call count never stands in for unique works.

    The five findings-side readings are the Evidence Verifier's own: how many
    findings it confirmed as written, how many it kept with corrected context,
    how many it dropped, how many it kept with an unchecked context, and how
    many the reader report cites. A dropped finding is not a verified one, and
    a cited one is not a checked one.
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
    verified_findings: int = 0
    corrected_findings: int = 0
    dropped_findings: int = 0
    context_unchecked_findings: int = 0
    cited_findings: int = 0


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
        """True when the graph halted on a non-recoverable failure.

        A halted run publishes nothing, and every other ending does: an
        exhausted extra-pass budget and a report the reviewer did not accept
        are both published, honestly, as ``max_iterations`` or ``incomplete``.
        ``accepted`` is ``False`` for all of them, and ``failed`` says which
        one left no report at all.
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
        falls back to the stored review record: reading the same vocabulary
        from its own source is not a second vocabulary, and answering "no
        review" while the state holds a scored one would be a false statement
        about the run.
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
        """Required-target progress, or ``None`` when nothing judged it.

        The counts are lengths of the id lists the quality pass judged, not the
        ``required_targets``/``answered_targets`` scalars of the composition's
        terminal record: nothing fills those, so reading them would print
        "0/0 required targets answered" above a missing-target list with an
        entry in it, while the ids are what the gates and the extra-pass
        router both read.

        The numerator is answered *required* targets, which is not the length
        of ``answered_target_ids``: that list holds every target a verified
        finding answers, the plan's optional targets included. Counting it
        published more answers than the plan required — "3/2 answered" — and,
        where the answered targets were the optional ones, a satisfied count
        directly above the target still owed. The two readings printed together
        are therefore complements over one denominator: the required ids minus
        the ones the same gate recorded as missing, plus that missing list.

        The Not found reading is the composition's own — the report's account
        of what it searched for and did not find. It is empty when no
        composition was kept, and never re-derived from the missing ids: a
        target no search reported on belongs in no Not found section.
        """
        quality = self.quality
        if quality is None:
            return None
        composition = self.state.composition
        required = set(quality.required_target_ids)
        missing = set(quality.missing_required_target_ids)
        return CoverageProgress(
            required_targets=len(required),
            answered_targets=len(required - missing),
            missing_required_target_ids=tuple(
                quality.missing_required_target_ids
            ),
            not_found_target_ids=(
                ()
                if composition is None
                else tuple(row.target_id for row in composition.not_found)
            ),
        )

    @property
    def evidence_counts(self) -> EvidenceCounts | None:
        """The distinct counts, or ``None`` when the run measured neither.

        Two records are needed, and each absence is honest on its own: without
        a composition there is no reader report to index, and without a quality
        snapshot no pass judged this run at all — in which case "how many
        findings were verified" has no answer and a zero would be a claim
        rather than an absence. A run that collected sources and was never
        judged therefore reports both readings absent together, instead of
        publishing one half of them as zeroes.
        """
        composition = self.state.composition
        quality = self.quality
        if composition is None or quality is None:
            return None
        # The retention helper answers the read-side counts this contract
        # keeps; its fields are named here rather than splatted, so a new key
        # the helper grows is a decision about this dataclass rather than a
        # count that appears in it unannounced.
        retention = distinct_retention_counts(self.state, composition)
        return EvidenceCounts(
            read_records=retention["read_records"],
            network_reads=retention["network_reads"],
            cache_reads=retention["cache_reads"],
            unique_works=retention["unique_works"],
            publishers=retention["publishers"],
            source_urls=retention["source_urls"],
            findings=retention["findings"],
            assessed_sources=retention["assessed_sources"],
            cited_assessed_sources=retention["cited_assessed_sources"],
            verified_findings=quality.verified_findings,
            corrected_findings=quality.corrected_findings,
            dropped_findings=quality.dropped_findings,
            context_unchecked_findings=quality.context_unchecked_findings,
            cited_findings=quality.cited_findings,
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
        memory write of a cited finding is recorded under the same error type
        but is not part of the set, so it never withholds a path — reading it
        as one printed "Publication: incomplete … No artifact path is
        advertised" above all three advertised paths.
        """
        return tuple(
            artifact
            for artifact in self._failed_write_artifacts()
            if artifact in PUBLICATION_DOCUMENT_ARTIFACTS
        )

    @property
    def failed_memory_writes(self) -> int:
        """How many writes of a finding to memory failed.

        Counted rather than listed: two failed writes are two lost memory
        records, and the artifact name repeated once per finding says nothing
        a count does not.
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
    a budget simply records none. The same holds for each additive field:
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
