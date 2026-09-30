"""LangGraph node wrappers over the agents that already work.

A node is thin on purpose: read the channel, invoke one agent, merge what
it returned with ``merge_research_state``, and bracket the whole thing with
graph-level events. Nothing here imports LangGraph — a node is an async
function from channel to channel, which is what makes every rule below
testable by calling it directly.

Failure handling is a halt discipline rather than an exception escaping
``ainvoke``. Four exception types become enumerated graph errors that mark
the run dead; every later node sees the mark, records ``graph.node.skipped``
and returns without invoking its agent; the router sends the run to ``END``
with status ``failed``. The state collected before the failure survives —
which is the whole point of not raising.

Anything other than those four exception types propagates. An unhandled
exception is a defect, not a research outcome, and converting it into a
recorded error would hide it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypeAlias, runtime_checkable

from pydantic import JsonValue

from deep_research.agents.base import AgentRun
from deep_research.agents.errors import AgentConfigurationError, PlanningError
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.quality import (
    compute_report_quality,
    review_status_fields,
)
from deep_research.agents.reader_notes import board_notes
from deep_research.agents.report import (
    QUALITY_STATUS_ACCEPTED,
    evidence_report_filename,
    quality_report_filename,
    render_finding_log,
    render_quality_json,
    render_written_report,
    report_filename,
)
from deep_research.agents.report_reviewer import (
    ReportReviewInput,
    ScopedReportReviewInput,
    build_report_review_input,
    build_scoped_report_review_input,
    remap_review_for_redraft,
)
from deep_research.agents.report_writer import (
    finding_memory_payload,
)
from deep_research.graph.errors import (
    GraphConfigurationError,
    agent_configuration_error,
    invalid_agent_state_error,
    invalid_route_error,
    planning_failed_error,
    provider_configuration_error,
    publication_unavailable_error,
    publication_write_error,
    redraft_limit_error,
    report_review_unavailable_error,
    request_attempt_limit_error,
)
from deep_research.graph.events import (
    extra_pass_started_event,
    node_completed_event,
    node_skipped_event,
    node_started_event,
    quality_assessed_event,
    redraft_requested_event,
    report_published_event,
    report_review_completed_event,
    route_decided_event,
)
from deep_research.graph.live import publish_live
from deep_research.graph.state import (
    EXTRA_PASS_NODE,
    FINALIZE_NODE,
    MAX_WRITER_REDRAFTS,
    REDRAFT_NODE,
    REPORT_REVIEWER_NODE,
    REPORT_WRITER_NODE,
    ResearchGraphState,
    dump_state,
    extra_pass_target_ids,
    graph_quality_status,
    graph_route,
    graph_status,
    is_halted,
    load_state,
)
from deep_research.observability import RunTelemetryCollector
from deep_research.providers import ProviderConfigurationError, ProviderError
from deep_research.request_budget import RequestAttemptLimitError
from deep_research.tools.base import ToolResult
from deep_research.utils.types import (
    Finding,
    ReportComposition,
    ReportQualitySnapshot,
    ReportReview,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    advance_research_iteration,
    merge_research_state,
    with_board_notes,
)

GraphNode: TypeAlias = Callable[
    [ResearchGraphState], Awaitable[ResearchGraphState]
]


class ResearchAgent(Protocol):
    """The one agent capability a graph node needs.

    Every concrete agent satisfies it. Keeping the protocol to a name and a
    ``run`` is what lets graph tests use two-line doubles with no provider,
    tracker, scratchpad, or toolset.
    """

    name: str

    async def run(self, state: ResearchState) -> AgentRun[Any]:
        """Run one agent pass over research state."""
        raise NotImplementedError


@runtime_checkable
class ReportReviewerLike(Protocol):
    """The one capability the terminal review node needs.

    Structural, like ``ReportPublisher``: the production ``ReportReviewer``
    satisfies it, and so does a two-line double in a graph test — which is why
    a graph test can exercise "the review refused the report" without a
    provider, a prompt, or a tracker.

    ``review_records`` is what the review just made *cost*: the records the
    reviewer produced about its own calls, which the node publishes beside the
    judgement so an operational fact about the request — a truncated call that
    had to be re-asked — lands where the run's other warnings do instead of
    only in a trace. A double that makes no such calls reports none.
    """

    review_records: tuple[ResearchError, ...]

    async def review(
        self,
        packet: ReportReviewInput,
        *,
        previous: ReportReview | None = None,
    ) -> ReportReview:
        """Judge one report packet, reusing an identical earlier judgement."""
        raise NotImplementedError

    async def review_scoped(
        self,
        scoped: ScopedReportReviewInput,
    ) -> ReportReview:
        """Judge only a redraft's changed parts (T5 addendum), carrying the
        rest of the previous review forward."""
        raise NotImplementedError


def _with(
    state: ResearchState,
    update: ResearchStateUpdate,
) -> ResearchGraphState:
    return dump_state(merge_research_state(state, update))


def _skipped(state: ResearchState, node: str) -> ResearchGraphState:
    return _with(
        state,
        {"events": [node_skipped_event(node, iteration=state.iteration)]},
    )


def _halt(
    state: ResearchState,
    error: ResearchError,
) -> ResearchGraphState:
    return _with(state, {"errors": [error]})


def _board_notes_update(state: ResearchState) -> ResearchStateUpdate:
    """The run board's notes the state does not hold yet, as one update, or ``{}``.

    live-briefs spec §4.6: a node starts from every note received so far. A
    note the state already holds keeps the state's flags; ``{}`` when the
    board adds nothing, so a run without notes merges exactly what it did.
    """
    notes = with_board_notes(state.reader_notes, board_notes())
    if len(notes) == len(state.reader_notes):
        return {}
    return {"reader_notes": notes}


def agent_node(
    agent: ResearchAgent,
    *,
    node_name: str | None = None,
) -> GraphNode:
    """Wrap one agent as a LangGraph node.

    ``node_name`` defaults to the agent's own name so a LangSmith trace and
    the graph read the same; it is overridable so the same agent class can
    fill two slots if a later spec ever needs that.
    """
    name = (node_name or agent.name).strip()
    if not name:
        raise GraphConfigurationError("graph nodes need a non-blank name")

    async def node(channel: ResearchGraphState) -> ResearchGraphState:
        state = load_state(channel)
        if is_halted(state):
            return _skipped(state, name)

        started_event = node_started_event(name, iteration=state.iteration)
        # live-briefs spec §4.6: every node begins with the notes received so far.
        started = merge_research_state(
            state, {"events": [started_event], **_board_notes_update(state)}
        )
        # Published live (live-briefs spec E3): the object merged here is the one
        # this node's snapshot carries, so the orchestrator delivers it once.
        publish_live(started_event)
        try:
            outcome = await agent.run(started)
        except RequestAttemptLimitError as error:
            # The run's declared attempt budget is spent. It arrives here only
            # because the tool and provider layers deliberately re-raise it
            # instead of translating it, so this is the one place it can be
            # turned into a record: an enumerated halt whose details are the
            # refusal's own snapshot. It must never reach _from_exception(),
            # the provider retry policy, or a recoverable tool taxonomy — a
            # recoverable record here would let the run carry on spending past
            # a ceiling the user declared.
            return _halt(
                started, request_attempt_limit_error(error, node=name)
            )
        except PlanningError as error:
            return _halt(started, planning_failed_error(error, node=name))
        except AgentConfigurationError as error:
            return _halt(started, agent_configuration_error(error, node=name))
        except ProviderConfigurationError as error:
            return _halt(
                started, provider_configuration_error(error, node=name)
            )

        update = outcome.state_update
        try:
            merged = merge_research_state(started, update)
        except (TypeError, ValueError) as error:
            # merge_research_state already enforces the whole contract:
            # unknown fields, non-list appends, a direct iteration write, and
            # every Pydantic constraint. ValidationError subclasses ValueError.
            return _halt(started, invalid_agent_state_error(error, node=name))

        return _with(
            merged,
            {
                "events": [
                    node_completed_event(
                        name,
                        iteration=merged.iteration,
                        event_count=len(update.get("events", ())),
                        error_count=len(update.get("errors", ())),
                    )
                ]
            },
        )

    return node


def report_writer_node(
    agent: ResearchAgent,
    *,
    node_name: str = REPORT_WRITER_NODE,
) -> GraphNode:
    """Wrap the Report Writer and score the artifacts it just composed.

    The deterministic quality pass runs here rather than inside the agent for
    one reason: ``compute_report_quality`` reads the state a pass *produced*,
    and this wrapper is what merges the agent's update into the channel.
    Judging the composition against the state handed in would report every
    first pass as having no artifacts at all.

    A pass that composed no typed composition records no snapshot. The
    absence stays visible — no run is ever accepted without one — and a halted
    pass is not graded at all: ``agent_node`` skips a halted node without
    touching state, so any composition still in the channel belongs to an
    *earlier* pass, and re-scoring it would emit a verdict labelled with this
    pass's iteration for work this pass never did.

    T5 addendum: this is also the redraft-to-reviewer handoff. ``composition``
    changing invalidates the stored review by construction
    (``merge_research_state``), which is right for a genuinely new report but
    would throw away exactly what a scoped re-review needs. When this pass
    ran with a scored ``report_review`` already in hand *and* arrived here
    through the writer-redraft hop specifically (not an extra research pass
    that merely still has an old material defect on hand --
    :func:`_arrived_via_redraft_hop` reads the event log to tell the two
    apart), :func:`remap_review_for_redraft` is asked -- with both the
    composition that review judged and the one this pass just produced still
    local Python objects -- to carry it onto the new statement ids. It comes
    back ``None`` unless every part the new composition marks carried over is
    byte-identical to what the review judged, so a part that changed without
    a redraft request simply leaves the review dropped, and the reviewer node
    falls back to a full review.
    """
    inner = agent_node(agent, node_name=node_name)

    async def node(channel: ResearchGraphState) -> ResearchGraphState:
        pre_state = load_state(channel)
        composed = await inner(channel)
        state = load_state(composed)
        if is_halted(state) or state.composition is None:
            return composed
        quality = compute_report_quality(state, state.composition)
        update: dict[str, object] = {
            "quality": quality,
            "events": [
                quality_assessed_event(
                    iteration=state.iteration, quality=quality
                )
            ],
        }
        previous_review = pre_state.report_review
        if (
            previous_review is not None
            and previous_review.status == "scored"
            and pre_state.composition is not None
            and _arrived_via_redraft_hop(pre_state.events)
        ):
            remapped = remap_review_for_redraft(
                previous_review,
                previous_composition=pre_state.composition,
                composition=state.composition,
            )
            if remapped is not None:
                update["report_review"] = remapped
        return _with(state, update)

    return node


def _arrived_via_redraft_hop(events: Sequence[ResearchEvent]) -> bool:
    """Whether the writer-redraft hop, not an extra research pass, is what
    most recently ran before this writer call (T5 addendum, P2).

    ``state.report_review`` surviving with material defects on hand does not
    by itself say *why* the writer is running again: an extra pass bought by
    a missing required target loops back through the whole research chain
    with the same review (and its defects) still in place, and that pass's
    carried-over parts may now have new evidence and new fact rows behind
    them that a scoped re-review would never be asked to re-judge. The two
    hops are told apart by their own marker events: ``writer_redraft_node``
    always runs immediately before this node with nothing in between
    (``graph.report.redraft_requested``), while an extra pass re-enters
    through the researcher, the source evaluator and the evidence verifier
    first (``graph.extra_pass.started``). Scanning from the most recent event
    for whichever marker comes first settles it without a new state field.
    """
    for event in reversed(events):
        if event.event_type == "graph.report.redraft_requested":
            return True
        if event.event_type == "graph.extra_pass.started":
            return False
    return False


@runtime_checkable
class ReportPublisher(Protocol):
    """The one writer of the terminal artifacts and long-term memory.

    Structural on purpose. The Report Writer agent already owns the
    ``write_document`` and ``save_to_memory`` tools, so it satisfies this
    without the graph reaching into agent internals — and a test can pass a
    recording double instead of a filesystem. Every method returns the tool's
    own result, because a failed write is an outcome to record, not an
    exception to catch.
    """

    async def publish_document(
        self, *, filename: str, content: str
    ) -> ToolResult:
        """Write one Markdown artifact and report the path it landed on."""
        raise NotImplementedError

    async def publish_finding(
        self, *, content: str, metadata: Mapping[str, JsonValue]
    ) -> ToolResult:
        """Save one cited finding to long-term memory."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class _Publication:
    """What the terminal publication step actually achieved.

    ``document_writes`` is the truthful count of writes that succeeded, whether
    or not the set is complete. The three paths, by contrast, are all-or-
    nothing: a set missing any required artifact advertises no path at all.
    """

    report_path: str | None
    evidence_path: str | None
    quality_path: str | None
    document_writes: int
    memory_writes: int
    errors: tuple[ResearchError, ...]


def _terminal_artifacts(
    state: ResearchState,
    status: str,
) -> tuple[str, str, str, ReportComposition | None]:
    """The exact text of all three artifacts, from one frozen composition.

    Re-rendered from the composition in state when there is one: the reader
    must see the status the terminal gates decided, not the
    ``not yet quality-gated`` placeholder the pass composed it under. With no
    composition, the state's own Markdown is authoritative and is left alone —
    and the quality record, which describes a composition, is not invented for
    a pass that never had one; it is rendered as the empty record of a pass
    nothing composed.

    The reader report is the Report Writer's own rendering of the composition
    and the evidence artifact is the finding log of the same one, so the two
    documents a reader and a checker receive describe one statement set. The
    quality record is rendered from the *same* finalized composition and
    hashes those two exact strings. One composition in, three artifacts out:
    no renderer re-derives the fit, and no hash describes a document other
    than the one written beside it.

    The composition's ``errors`` are refreshed from the state as it is
    published. A composition is built by the Report Writer, so its own error
    list stops there — and a record made *after* the writing (the review's "no
    judgement of this report exists", for one) would otherwise be invisible in
    everything published beside it. Refreshing here is the smallest place to
    say it, because publication is the one point that knows the run is over;
    ``errors`` is also not part of ``composition_semantic_fingerprint``, so
    this cannot invalidate the stored review. With no composition there is
    nothing to re-render, and the state's own text is published as it stands.
    """
    composition = state.composition
    run_status = graph_status(state)
    if composition is None:
        reader = state.report or ""
        evidence = state.report_evidence or ""
        quality = render_quality_json(
            state,
            None,
            state.report_review,
            artifacts={
                "reader_markdown": reader,
                "evidence_markdown": evidence,
            },
            quality_status=status,
            session_status=run_status,
        )
        return reader, evidence, quality, None
    finalized = composition.model_copy(
        update={
            "quality_status": status,
            "errors": list(state.errors),
        }
    )
    reader = render_written_report(finalized).strip()
    evidence = render_finding_log(finalized).strip()
    quality = render_quality_json(
        state,
        finalized,
        state.report_review,
        artifacts={
            "reader_markdown": reader,
            "evidence_markdown": evidence,
        },
        session_status=run_status,
    )
    return reader, evidence, quality, finalized


def _cited_findings(state: ResearchState) -> list[Finding]:
    """Every verified finding the published composition cites, in label order.

    A finding is cited when a reader sentence rests on it or when it carries a
    key-facts row: those are the two places a reader meets a citation, and both
    are drawn from ``composition.findings`` — the verified snapshot — so the
    fingerprint lookup can only miss for a row whose finding was dropped after
    the fact, which is a row the renderer withholds anyway.
    """
    composition = state.composition
    if composition is None:
        return []
    by_id = {
        finding_fingerprint(finding): finding
        for finding in composition.findings
    }
    cited: list[Finding] = []
    seen: set[str] = set()
    for statement in composition.statements:
        finding_ids = list(statement.finding_ids)
        for finding_id in finding_ids:
            finding = by_id.get(finding_id)
            if finding is None or finding_id in seen:
                continue
            seen.add(finding_id)
            cited.append(finding)
    for row in composition.fact_rows:
        for finding_id in (row.finding_id, *row.duplicate_finding_ids):
            finding = by_id.get(finding_id)
            if finding is None or finding_id in seen:
                continue
            seen.add(finding_id)
            cited.append(finding)
    return cited


async def _publish(
    state: ResearchState,
    publisher: ReportPublisher | None,
    *,
    markdown: str,
    evidence: str,
    quality: str,
    status: str,
) -> _Publication:
    """Write the artifact set, then keep findings only for an accepted report.

    The three document writes are staged independently: each records its own
    error, so one failing never hides another and the failure record names
    every write that did not complete. What is *not* independent is what gets
    advertised. The paths are published only once the whole set — reader
    Markdown, finding log, quality record — has been written, because a
    front-end handed two paths out of three cannot tell from the paths which
    artifact is missing, and the two documents on their own cannot be checked
    against the IDs and hashes that describe them. Nothing here asserts the set
    is written atomically: the writes are separate operations, and this is a
    decision about what may be advertised rather than a statement about the
    filesystem.

    Memory is written last and only for ``accepted`` — a partial report is
    published, never remembered.
    """
    if publisher is None:
        return _Publication(
            None,
            None,
            None,
            0,
            0,
            (publication_unavailable_error(node=FINALIZE_NODE),),
        )

    errors: list[ResearchError] = []
    report_path, failure = await _write_document(
        publisher,
        filename=report_filename(
            session_id=state.session_id, iteration=state.iteration
        ),
        content=markdown,
        artifact="reader",
    )
    if failure is not None:
        errors.append(failure)
    evidence_path, failure = await _write_document(
        publisher,
        filename=evidence_report_filename(
            session_id=state.session_id, iteration=state.iteration
        ),
        content=evidence,
        artifact="evidence",
    )
    if failure is not None:
        errors.append(failure)
    quality_path, failure = await _write_document(
        publisher,
        filename=quality_report_filename(
            session_id=state.session_id, iteration=state.iteration
        ),
        content=quality,
        artifact="quality",
    )
    if failure is not None:
        errors.append(failure)

    written = (report_path, evidence_path, quality_path)
    document_writes = sum(path is not None for path in written)
    if document_writes != len(written):
        report_path = evidence_path = quality_path = None

    memory_writes = 0
    if status == QUALITY_STATUS_ACCEPTED:
        for finding in _cited_findings(state):
            content, metadata = finding_memory_payload(
                finding, session_id=state.session_id
            )
            result = await publisher.publish_finding(
                content=content, metadata=metadata
            )
            if result.success:
                memory_writes += 1
                continue
            errors.append(
                publication_write_error(
                    node=FINALIZE_NODE,
                    artifact="memory",
                    tool=result.tool_name,
                    failure_type=_failure_type(result),
                )
            )
    return _Publication(
        report_path,
        evidence_path,
        quality_path,
        document_writes,
        memory_writes,
        tuple(errors),
    )


def _failure_type(result: ToolResult) -> str:
    """The failed tool result's own error class name, never its message."""
    return result.error.type if result.error is not None else "unknown"


async def _write_document(
    publisher: ReportPublisher,
    *,
    filename: str,
    content: str,
    artifact: str,
) -> tuple[str | None, ResearchError | None]:
    """Write one artifact and report its published path or its failure."""
    result = await publisher.publish_document(filename=filename, content=content)
    if not result.success:
        return None, publication_write_error(
            node=FINALIZE_NODE,
            artifact=artifact,
            tool=result.tool_name,
            failure_type=_failure_type(result),
        )
    data = result.data
    path = data.get("path") if isinstance(data, dict) else None
    return (path if isinstance(path, str) and path else None), None


def finalize_report_node(
    publisher: ReportPublisher | None,
    *,
    run_telemetry: RunTelemetryCollector | None = None,
) -> GraphNode:
    """Publish the composed artifact set, once, at the one terminal node.

    This is the run's only writer — the writing pass composes and writes
    nothing — and it is reached only by a run that was not halted. It:

    * stamps the terminal quality status the router decided onto the report;
    * writes the reader report, the finding log and the quality record from
      one frozen composition, recording a separate error for each write that
      did not complete;
    * advertises no artifact path unless the whole set was written, so a
      front-end is never pointed at an earlier pass's file and never at an
      incomplete set;
    * keeps the cited verified findings in long-term memory only when the
      status is ``accepted``;
    * emits the terminal ``graph.report.published`` event carrying all three
      paths — each ``None`` when the set is incomplete — and the truthful
      count of writes that succeeded;
    * leaves the Markdown authoritative in state whatever the filesystem did.

    Nothing here halts the run: a failed write is a recorded recoverable
    error, because the report a reader receives is the Markdown, not the file.
    And nothing here asserts the writes are one atomic filesystem operation:
    they are three separate writes, and what the incomplete case guarantees is
    that none of them is advertised.

    ``run_telemetry`` is the run's §7.3 collector, stamped into the state
    before the record is rendered so the published JSON carries the run's own
    figures. It is optional in the same sense the budget is not: a graph built
    without one — a unit test, an injected double — publishes ``telemetry:
    None``, which says the run measured nothing rather than that it measured
    zeroes.
    """

    async def node(channel: ResearchGraphState) -> ResearchGraphState:
        state = load_state(channel)
        if is_halted(state):
            return _skipped(state, FINALIZE_NODE)

        started = merge_research_state(
            state,
            {
                # The run's §7.3 reading, taken here because this is the one
                # node that knows the run is over and the one whose renderer
                # publishes it: the quality record rendered below reads the
                # telemetry out of the state, so stamping it in the same merge
                # that starts the node is what keeps the published figures and
                # the stored ones the same reading. ``None`` when the run
                # carried no collector, never an empty snapshot.
                "run_telemetry": (
                    run_telemetry.snapshot()
                    if run_telemetry is not None
                    else None
                ),
                "events": [
                    node_started_event(
                        FINALIZE_NODE, iteration=state.iteration
                    )
                ],
            },
        )
        status = graph_quality_status(started)
        markdown, evidence, quality, composition = _terminal_artifacts(
            started, status
        )
        publication = await _publish(
            started,
            publisher,
            markdown=markdown,
            evidence=evidence,
            quality=quality,
            status=status,
        )
        return _with(
            started,
            {
                "report": markdown,
                "report_evidence": evidence,
                "composition": composition,
                "report_path": publication.report_path,
                "evidence_path": publication.evidence_path,
                "quality_path": publication.quality_path,
                "errors": list(publication.errors),
                "events": [
                    report_published_event(
                        quality_status=status,
                        report_path=publication.report_path,
                        evidence_path=publication.evidence_path,
                        quality_path=publication.quality_path,
                        document_writes=publication.document_writes,
                        memory_writes=publication.memory_writes,
                        error_count=len(publication.errors),
                    ),
                    node_completed_event(
                        FINALIZE_NODE,
                        iteration=started.iteration,
                        event_count=1,
                        error_count=len(publication.errors),
                    ),
                ],
            },
        )

    return node


def report_reviewer_node(reviewer: ReportReviewerLike | None) -> GraphNode:
    """Run the terminal review, stamp the routing record, and decide the edge.

    This node is where the report is judged rather than merely measured, and it
    is also the graph's decision point: the review it records is what
    ``graph_route`` reads, and ``route_after_review`` reads the same pure
    function, so the recorded route event and the taken edge agree by
    construction.

    Four outcomes, and the difference between them is the point:

    * a *scored* review is recorded, the quality snapshot carries its status
      and mean beside its structural diagnostics, and the route may read the
      missing required targets it is stamped with;
    * an *incomplete* or *provider_failed* review is recorded with no score at
      all, a recoverable error names it, and the route publishes the report as
      partial — never as accepted, and never as a graph failure;
    * a review that a previous pass already made over the identical semantic
      fingerprint is reused, at no provider cost, because the report it judged
      is the report this pass produced;
    * a reviewer that could not be asked at all — a spent request ceiling, or a
      provider this run cannot reach — is recorded as an unreviewed report
      rather than allowed to end the run: the report is already composed by the
      writer node, and publishing it is what makes a refusal a missing
      judgement instead of a lost report.

    PD-5: ``missing_required_target_ids`` is computed by code — the writer
    node's quality pass measures it — and stamped onto the record here
    whatever the review said, because a reviewer that named no missing target
    of its own cannot clear an obligation the deterministic pass measured. The
    AI reviewer never produces it.

    A reused or skipped call is recorded as such in the review event, so the
    trace says whether a model was asked this pass.
    """

    async def node(channel: ResearchGraphState) -> ResearchGraphState:
        state = load_state(channel)
        if is_halted(state):
            return _skipped(state, REPORT_REVIEWER_NODE)

        started = merge_research_state(
            state,
            {
                "events": [
                    node_started_event(
                        REPORT_REVIEWER_NODE, iteration=state.iteration
                    )
                ]
            },
        )
        review, errors, reused = await _review_report(started, reviewer)
        review = review.model_copy(
            update={
                "missing_required_target_ids": list(
                    started.quality.missing_required_target_ids
                )
                if started.quality
                else []
            }
        )
        merged = merge_research_state(
            started,
            {
                "report_review": review,
                "quality": _quality_with_review(started, review),
                "errors": list(errors),
            },
        )
        destination, reason = graph_route(merged)
        return _with(
            merged,
            {
                "events": [
                    report_review_completed_event(
                        iteration=started.iteration,
                        review_status=review.status,
                        mean_score=review.mean_score,
                        material_defects=len(review.material_defects),
                        reviewed_statements=len(review.reviewed_statement_ids),
                        fingerprint=review.input_fingerprint,
                        reused=reused,
                    ),
                    route_decided_event(
                        destination=destination,
                        reason=reason,
                        iteration=started.iteration,
                        max_extra_passes=started.max_extra_passes,
                        missing_required_target_ids=extra_pass_target_ids(merged),
                    ),
                    node_completed_event(
                        REPORT_REVIEWER_NODE,
                        iteration=started.iteration,
                        event_count=1,
                        error_count=len(errors),
                    ),
                ]
            },
        )

    return node


async def _review_report(
    state: ResearchState,
    reviewer: ReportReviewerLike | None,
) -> tuple[ReportReview, list[ResearchError], bool]:
    """Judge the candidate, or record honestly that nothing judged it.

    The packet is always built, even with no reviewer wired: it is the record
    of what a review *would* have judged, its fingerprint is what a later
    reuse is checked against, and a composition-less report is recorded as
    unreviewable rather than as reviewed-and-fine.
    """
    packet = build_report_review_input(state, state.composition)
    previous = state.report_review
    if (
        previous is not None
        and previous.status == "scored"
        and previous.input_fingerprint == packet.fingerprint
    ):
        return previous, [], True
    if not packet.reader_content.strip() or state.composition is None:
        # Nothing reviewable exists: no reader content, or prose with no typed
        # record behind it. Refused here rather than asked of the reviewer,
        # because "is there a report to judge at all" is not a judgement — and a
        # reviewer that answered anyway would be scoring prose that no
        # statement, target, or excerpt can be tied to.
        review = _unreviewed(
            packet,
            (
                "No reviewable report is recorded for this pass: the reader "
                "content or its typed composition is missing, so no judgement "
                "of the report exists."
            ),
            status="incomplete",
        )
        return (
            review,
            [
                report_review_unavailable_error(
                    node=REPORT_REVIEWER_NODE,
                    review_status=review.status,
                    reason="report_unreviewable",
                )
            ],
            False,
        )
    if reviewer is None:
        review = _unreviewed(
            packet,
            (
                "No terminal report reviewer is configured for this run, so "
                "no judgement of the report exists."
            ),
            status="incomplete",
        )
        return (
            review,
            [
                report_review_unavailable_error(
                    node=REPORT_REVIEWER_NODE,
                    review_status=review.status,
                    reason="report_reviewer_unconfigured",
                )
            ],
            False,
        )
    # T5 addendum: ``previous`` here is either the ordinary stored review (the
    # reuse check above already handled the identical-fingerprint case) or a
    # remapped carried-over review ``report_writer_node`` restored after a
    # redraft (:func:`remap_review_for_redraft`). Only the latter yields a
    # scoped packet, since only it carries a disposition for at least one
    # unchanged statement; a stale unrelated review yields ``None`` from
    # ``build_scoped_report_review_input`` just as safely, falling back to a
    # full review below.
    scoped = None
    if previous is not None and previous.status == "scored":
        scoped = build_scoped_report_review_input(state, previous_review=previous)
    scoped_failure: str | None = None
    scoped_records: tuple[ResearchError, ...] = ()
    try:
        if scoped is not None:
            try:
                scoped_status: str | None = None
                review = await reviewer.review_scoped(scoped)
                if review.status != "scored":
                    scoped_status = review.status
            except ProviderError:
                scoped_status = "provider_failed"
                review = None
            # The scoped attempt's own retry telemetry must be read before the
            # fallback call below: ``ReportReviewer.review``/``review_scoped``
            # both reset ``review_records`` to an empty tuple at the start of
            # their own request, so a truncation retry the scoped call paid
            # for would otherwise vanish from ``errors`` the moment the
            # fallback's own (possibly retry-free) call starts. When no
            # fallback runs, ``reviewer.review_records`` below is still this
            # very tuple, so ``scoped_records`` is prepended only when
            # ``scoped_failure`` is set -- never beside the identical tuple
            # it was captured from.
            scoped_records = reviewer.review_records
            if scoped_status is not None:
                # The addendum's own promise: a scoped call that could not be
                # made -- a provider failure, an invalid reply, or one that
                # judged an id this packet does not carry -- is not the final
                # word. One full, fresh review (never the stale ``previous``)
                # is tried before the run settles for an unjudged report.
                scoped_failure = scoped_status
                review = await reviewer.review(packet, previous=None)
        else:
            review = await reviewer.review(packet, previous=previous)
    except (RequestAttemptLimitError, ProviderConfigurationError) as error:
        # The one collaborator refusal that is not a judgement: a spent
        # request ceiling, or a provider this run cannot reach at all. Both are
        # raised before any review exists, and both arrive here only because
        # the provider and tool layers deliberately re-raise them instead of
        # translating them.
        #
        # They are recorded as a judgement that was not made, never as a run
        # failure. The report is already composed — the writer node before this
        # one had to succeed for there to be a packet at all — and publishing
        # it spends nothing, so a refusal here must not cost a finished report.
        # Recording the ceiling as its own enumerated halt would do exactly
        # that: ``graph_request_attempt_limit_exceeded`` is a halting type, so
        # ``graph_route`` would return ROUTE_END and the finalizer would skip
        # publication. The ceiling itself stays visible where it belongs, in
        # the budget's own snapshots, and the reason below names it.
        reason, rationale = _unavailable_review_reading(error)
        review = _unreviewed(packet, rationale, status="incomplete")
        return (
            review,
            [
                *(scoped_records if scoped_failure is not None else ()),
                *reviewer.review_records,
                report_review_unavailable_error(
                    node=REPORT_REVIEWER_NODE,
                    review_status=review.status,
                    reason=reason,
                ),
            ],
            False,
        )
    if scoped_failure is not None:
        # T5 addendum: the fallback happened -- recorded here rather than as
        # its own error, since (unlike every other entry in ``errors``) the
        # ordinary outcome is that this full review *did* produce a verdict.
        review = review.model_copy(
            update={
                "rationale": (
                    f"A scoped re-review after the redraft was {scoped_failure} "
                    "and could not be used; this is a full review of the "
                    "whole report instead. " + review.rationale
                ).strip()
            }
        )
    errors: list[ResearchError] = [
        *(scoped_records if scoped_failure is not None else ()),
        *reviewer.review_records,
    ]
    if review.status != "scored":
        errors.append(
            report_review_unavailable_error(
                node=REPORT_REVIEWER_NODE,
                review_status=review.status,
                reason=(
                    "report_review_provider_failed"
                    if review.status == "provider_failed"
                    else "report_review_incomplete"
                ),
            )
        )
    return review, errors, False


def _unreviewed(packet: ReportReviewInput, reason: str, *, status: str) -> ReportReview:
    """An explicit "nothing judged this report" record, with no score.

    Built through the review contract rather than by hand, so an unreviewed
    report cannot accidentally be recorded in a shape the acceptance rule would
    read as a pass: there are no dimensions, the status is not ``scored``, and
    the packet fingerprint still names what was not judged.

    ``composition_fingerprint`` travels too, and it is load-bearing: the state
    merge drops a stored judgement when the *composition* changes, matching on
    this value. Without it the record could never match the composition the
    terminal finalizer re-renders — the same material with only the quality
    badge stamped — so "no review was made" was dropped at exactly the node
    that publishes, and the state kept no record of the review that never
    happened. Changed content still invalidates it, which is the rule.
    """
    return ReportReview(
        status=status,  # type: ignore[arg-type]
        dimensions={},
        defects=[],
        per_statement_dispositions={},
        reviewed_statement_ids=[],
        unreviewed_statement_ids=list(packet.expected_statement_ids),
        input_fingerprint=packet.fingerprint,
        composition_fingerprint=packet.composition_fingerprint,
        rubric_version=packet.rubric_version,
        rationale=reason,
    )


def _unavailable_review_reading(
    error: RequestAttemptLimitError | ProviderConfigurationError,
) -> tuple[str, str]:
    """One unaskable review's enumerated reason, and the sentence it records.

    Two halves of one event: ``reason`` is the machine-readable value the error
    record carries beside the review status, and the rationale is the sentence
    the review record itself keeps. Both are project-generated — the ceiling
    refusal's own message is static project text and is still not copied,
    because the reason names the event rather than repeating its wording — and
    neither carries a provider name, a ceiling, or a count.
    """
    if isinstance(error, RequestAttemptLimitError):
        return (
            "report_review_request_attempt_limit",
            (
                "The request budget refused the terminal review: this run's "
                "declared attempt ceiling for the provider is spent, so no "
                "judgement of the report exists."
            ),
        )
    return (
        "report_review_provider_unconfigured",
        (
            "The provider the terminal review needs is not configured for "
            "this run, so no judgement of the report exists."
        ),
    )


def _quality_with_review(
    state: ResearchState,
    review: ReportReview,
) -> ReportQualitySnapshot | None:
    """The snapshot with the review's status and score recorded beside it.

    The review fields are written onto the existing structural snapshot rather
    than into ``hard_failures``: a review that was not made is an absent
    judgement, and the hard-failure list is a closed set of defects found *in
    the report*. Keeping them apart is what lets a reader see "the gates found
    nothing and nothing judged the report" as the distinct state it is.
    """
    quality = state.quality
    if quality is None:
        return None
    return quality.model_copy(update=review_status_fields(review))


async def extra_pass_node(channel: ResearchGraphState) -> ResearchGraphState:
    """Open the one extra pass the missing required targets justify (§6.5, D4).

    This exists as its own node because a LangGraph conditional edge routes
    but cannot write, and both the macro-iteration increment and the pass's
    job list have to happen somewhere the graph can see and a test can call.

    The job list is ``extra_pass_target_ids(state)`` (D10): the targets the
    code-stamped gate recorded as missing a verified finding, plus any
    required target a reviewer's own ``coverage`` defect named even though
    that gate already counted it answered. It is *replaced*, never merged:
    the pass that is about to run exists for those targets and no others, so
    a target an earlier pass owed cannot keep the run alive after the newest
    decision dropped it.

    The iteration bound is the graph's law and a second lock on the door: the
    router already refuses to reach this node once the ceiling is spent, and a
    run that somehow arrives anyway records ``graph_invalid_route`` — an
    enumerated halt — rather than paying for a pass its declared ceiling
    forbids.
    """
    state = load_state(channel)
    if is_halted(state):
        return _skipped(state, EXTRA_PASS_NODE)
    started = merge_research_state(
        state,
        {"events": [node_started_event(EXTRA_PASS_NODE, iteration=state.iteration)]},
    )
    if state.iteration >= state.max_extra_passes:
        return _halt(
            started,
            invalid_route_error(
                node=EXTRA_PASS_NODE,
                iteration=state.iteration,
                max_extra_passes=state.max_extra_passes,
            ),
        )

    targets = extra_pass_target_ids(state)
    advanced = advance_research_iteration(started)
    return _with(
        advanced,
        {
            "extra_pass_target_ids": targets,
            "events": [
                extra_pass_started_event(
                    iteration=advanced.iteration,
                    max_extra_passes=advanced.max_extra_passes,
                    targets=targets,
                ),
                node_completed_event(
                    EXTRA_PASS_NODE,
                    iteration=advanced.iteration,
                    event_count=1,
                    error_count=0,
                ),
            ],
        },
    )


async def writer_redraft_node(channel: ResearchGraphState) -> ResearchGraphState:
    """Hand a scored review's material defects back to the writer, once.

    ``semantic_review_passes`` refuses to accept a report whose review carries
    a material defect, and the graph could not act on that: the run published a
    report its own reviewer had just named as wrong, and the only continuation
    it had — an extra research pass — cannot fix a self-contradiction or an
    omission the evidence already supports. The defect list is addressed to the
    writer, so this hop buys one more draft: no research, no re-verification,
    the same evidence and a request that carries what the review said.

    It is its own node for the same reason ``extra_pass`` is: a conditional
    edge routes but cannot write, and the counter that bounds this re-run has
    to be written somewhere the graph and a test can both see. One re-run is
    the bound (``MAX_WRITER_REDRAFTS``) — a reviewer that keeps naming defects
    must not be able to loop the writer — and the guard is the second lock on
    that door: a run that somehow arrives with the re-run spent records
    ``graph_invalid_route`` rather than paying for a draft its bound forbids.
    """
    state = load_state(channel)
    if is_halted(state):
        return _skipped(state, REDRAFT_NODE)
    started = merge_research_state(
        state,
        {"events": [node_started_event(REDRAFT_NODE, iteration=state.iteration)]},
    )
    if state.writer_redrafts >= MAX_WRITER_REDRAFTS:
        return _halt(
            started,
            redraft_limit_error(
                node=REDRAFT_NODE,
                redrafts=state.writer_redrafts,
                max_redrafts=MAX_WRITER_REDRAFTS,
            ),
        )

    review = state.report_review
    defects = len(review.material_defects) if review is not None else 0
    return _with(
        started,
        {
            "writer_redrafts": state.writer_redrafts + 1,
            "events": [
                redraft_requested_event(
                    iteration=state.iteration,
                    redrafts=state.writer_redrafts + 1,
                    material_defects=defects,
                ),
                node_completed_event(
                    REDRAFT_NODE,
                    iteration=state.iteration,
                    event_count=1,
                    error_count=0,
                ),
            ],
        },
    )


def route_after_review(channel: ResearchGraphState) -> str:
    """The conditional edge out of the Report Reviewer. Pure read of state."""
    return graph_route(load_state(channel))[0]
