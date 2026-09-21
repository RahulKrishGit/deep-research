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
from deep_research.agents.identity import merge_claim_snapshot, normalize_source_url
from deep_research.agents.quality import compute_report_quality, review_status_fields
from deep_research.agents.report import (
    QUALITY_STATUS_ACCEPTED,
    render_evidence_ledger,
    render_reader_report,
)
from deep_research.agents.report_review import (
    ReportReviewInput,
    build_report_review_input,
    review_defects_as_refinement_jobs,
)
from deep_research.agents.synthesizer import (
    evidence_report_filename,
    high_confidence_claims,
    memory_payload,
    report_filename,
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
    report_review_unavailable_error,
    request_attempt_limit_error,
)
from deep_research.graph.events import (
    node_completed_event,
    node_skipped_event,
    node_started_event,
    quality_assessed_event,
    refinement_started_event,
    report_published_event,
    report_review_completed_event,
    route_decided_event,
)
from deep_research.graph.state import (
    CRITIC_NODE,
    FACT_CHECKER_NODE,
    FINALIZE_NODE,
    PLANNER_NODE,
    REFINE_NODE,
    REPORT_REVIEW_NODE,
    RESEARCHER_NODE,
    ROUTE_FINALIZE,
    SOURCE_EVALUATOR_NODE,
    SYNTHESIZER_NODE,
    ResearchGraphState,
    dump_state,
    graph_quality_status,
    graph_route,
    is_halted,
    load_state,
    progress_snapshot,
    repair_is_terminal,
    repair_stop_reason,
)
from deep_research.providers import ProviderConfigurationError
from deep_research.request_budget import RequestAttemptLimitError
from deep_research.tools.base import ToolResult
from deep_research.utils.types import (
    Claim,
    RefinementOrigin,
    RefinementTarget,
    RepairAction,
    ReportComposition,
    ReportQualitySnapshot,
    ReportReview,
    ResearchError,
    ResearchState,
    ResearchStateUpdate,
    advance_research_iteration,
    merge_research_state,
    unanswered_required_targets,
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
    """

    async def review(
        self,
        packet: ReportReviewInput,
        *,
        previous: ReportReview | None = None,
    ) -> ReportReview:
        """Judge one report packet, reusing an identical earlier judgement."""
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

        started = merge_research_state(
            state,
            {"events": [node_started_event(name, iteration=state.iteration)]},
        )
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


def synthesizer_node(
    agent: ResearchAgent,
    *,
    node_name: str = SYNTHESIZER_NODE,
) -> GraphNode:
    """Wrap the Synthesizer and score the artifacts it just composed.

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
    """
    inner = agent_node(agent, node_name=node_name)

    async def node(channel: ResearchGraphState) -> ResearchGraphState:
        composed = await inner(channel)
        state = load_state(composed)
        if is_halted(state) or state.composition is None:
            return composed
        quality = compute_report_quality(state, state.composition)
        return _with(
            state,
            {
                "quality": quality,
                "events": [
                    quality_assessed_event(
                        iteration=state.iteration, quality=quality
                    )
                ],
            },
        )

    return node


def critic_node(
    agent: ResearchAgent,
    *,
    node_name: str = CRITIC_NODE,
) -> GraphNode:
    """Wrap the Critic and record the route its critique produced.

    The route event is recorded here rather than in the conditional edge
    because a LangGraph router reads state and cannot write it.
    ``graph_route`` is pure and the state does not change between this call
    and ``route_after_critic``'s, so the recorded decision and the taken
    edge agree by construction.
    """
    inner = agent_node(agent, node_name=node_name)

    async def node(channel: ResearchGraphState) -> ResearchGraphState:
        reviewed = await inner(channel)
        state = load_state(reviewed)
        destination, reason = graph_route(state)
        critique = state.critique
        return _with(
            state,
            {
                "events": [
                    route_decided_event(
                        destination=destination,
                        reason=reason,
                        iteration=state.iteration,
                        max_iterations=state.max_iterations,
                        should_continue=(
                            critique is not None and critique.should_continue
                        ),
                    )
                ]
            },
        )

    return node


@runtime_checkable
class ReportPublisher(Protocol):
    """The one writer of the terminal artifacts and long-term memory.

    Structural on purpose. The Synthesizer agent already owns the
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

    async def publish_claim(
        self, *, content: str, metadata: Mapping[str, JsonValue]
    ) -> ToolResult:
        """Save one verified claim to long-term memory."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class _Publication:
    """What the terminal publication step actually achieved."""

    report_path: str | None
    evidence_path: str | None
    document_writes: int
    memory_writes: int
    errors: tuple[ResearchError, ...]


def _terminal_artifacts(
    state: ResearchState,
    status: str,
) -> tuple[str, str, ReportComposition | None]:
    """The exact Markdown both artifacts publish, stamped with the verdict.

    Re-rendered from the composition in state when there is one: the reader
    must see the status the terminal gates decided, not the
    ``not yet quality-gated`` placeholder the pass composed it under. With no
    composition, the state's own Markdown is authoritative and is left alone.
    Both strings are stripped to what ``ResearchState`` will hold, so the file
    and the state can never differ by a trailing newline.
    """
    composition = state.composition
    if composition is None:
        return state.report or "", state.report_evidence or "", None
    finalized = composition.model_copy(update={"quality_status": status})
    return (
        render_reader_report(finalized).strip(),
        render_evidence_ledger(finalized).strip(),
        finalized,
    )


async def _publish(
    state: ResearchState,
    publisher: ReportPublisher | None,
    *,
    markdown: str,
    evidence: str,
    status: str,
) -> _Publication:
    """Write both artifacts, then keep claims only for an accepted report.

    The two document writes are independent: each records its own error and
    its own path, so one failing never hides the other and a path is only
    ever set from a write that actually succeeded. Memory is written last and
    only for ``accepted`` — a partial report is published, never remembered.
    """
    if publisher is None:
        return _Publication(
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

    document_writes = sum(
        path is not None for path in (report_path, evidence_path)
    )
    memory_writes = 0
    if status == QUALITY_STATUS_ACCEPTED:
        for claim in high_confidence_claims(
            merge_claim_snapshot([], state.verified_claims)
        ):
            content, metadata = memory_payload(
                claim, session_id=state.session_id
            )
            result = await publisher.publish_claim(
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


def finalize_report_node(publisher: ReportPublisher | None) -> GraphNode:
    """Publish the composed artifacts, once, at the one terminal node.

    This is the run's only writer — synthesis composes and writes nothing —
    and it is reached only by a run that was not halted. It:

    * stamps the terminal quality status the router decided onto the report;
    * writes the reader report and the evidence ledger as two independent
      documents, recording a separate error and a separate path for each;
    * keeps high-confidence verified claims in long-term memory only when the
      status is ``accepted``;
    * emits the terminal ``graph.report.published`` event carrying both paths,
      ``None`` for any write that failed, so a front-end is never pointed at
      an earlier refinement pass's file;
    * leaves the Markdown authoritative in state whatever the filesystem did.

    Nothing here halts the run: a failed write is a recorded recoverable
    error, because the report a reader receives is the Markdown, not the file.
    """

    async def node(channel: ResearchGraphState) -> ResearchGraphState:
        state = load_state(channel)
        if is_halted(state):
            return _skipped(state, FINALIZE_NODE)

        started = merge_research_state(
            state,
            {
                "events": [
                    node_started_event(
                        FINALIZE_NODE, iteration=state.iteration
                    )
                ]
            },
        )
        status = graph_quality_status(started)
        markdown, evidence, composition = _terminal_artifacts(started, status)
        publication = await _publish(
            started,
            publisher,
            markdown=markdown,
            evidence=evidence,
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
                "errors": list(publication.errors),
                "events": [
                    report_published_event(
                        quality_status=status,
                        report_path=publication.report_path,
                        evidence_path=publication.evidence_path,
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


def report_review_node(reviewer: ReportReviewerLike | None) -> GraphNode:
    """Run the terminal semantic review, then record the route it produced.

    This node is where the report is judged rather than merely measured. It
    runs after the Critic and before the route, for one reason: a review that
    refuses the report is a defect with somewhere to go, so its finding has to
    exist before the edge is chosen. Nothing else about the graph changes —
    the review buys no pass of its own, and a review that was never made buys
    nothing at all.

    Three outcomes, and the difference between them is the point:

    * a *scored* review is recorded, the quality snapshot carries its status
      and mean beside its structural diagnostics, and the route may consume one
      refinement opportunity if it refused the report;
    * an *incomplete* or *provider_failed* review is recorded with no score at
      all, a recoverable error names it, and the route is left exactly as the
      Critic and the deterministic gates decided. Acceptance is then blocked by
      ``graph_quality_status``, so the report publishes as ``partial`` — never
      as accepted, and never as a graph failure;
    * a review that a previous pass already made over the identical semantic
      fingerprint is reused, at no provider cost, because the report it judged
      is the report this pass produced.

    A reused or skipped call is recorded as such in the review event, so the
    trace says whether a model was asked this pass.
    """
    async def node(channel: ResearchGraphState) -> ResearchGraphState:
        state = load_state(channel)
        if is_halted(state):
            return _skipped(state, REPORT_REVIEW_NODE)

        started = merge_research_state(
            state,
            {
                "events": [
                    node_started_event(
                        REPORT_REVIEW_NODE, iteration=state.iteration
                    )
                ]
            },
        )
        before = graph_route(started)[1]

        review, errors, reused = await _review_report(
            started, reviewer
        )
        merged = merge_research_state(
            started,
            {
                "report_review": review,
                "quality": _quality_with_review(started, review),
                "errors": list(errors),
            },
        )
        after, reason = graph_route(merged)
        events = [
            report_review_completed_event(
                iteration=started.iteration,
                review_status=review.status,
                mean_score=review.mean_score,
                material_defects=len(review.material_defects),
                reviewed_statements=len(review.reviewed_statement_ids),
                omitted_evidence=len(review.omitted_evidence_ids),
                fingerprint=review.input_fingerprint,
                reused=reused,
            ),
            node_completed_event(
                REPORT_REVIEW_NODE,
                iteration=started.iteration,
                event_count=1,
                error_count=len(errors),
            ),
        ]
        if reason != before:
            # The Critic's node already recorded the route as its own review
            # left it. Only a review that *changed* the destination records a
            # second decision, so the event stream shows one decision per run
            # unless the semantic review is the reason it moved.
            events.append(
                route_decided_event(
                    destination=after,
                    reason=reason,
                    iteration=started.iteration,
                    max_iterations=started.max_iterations,
                    should_continue=bool(
                        merged.critique is not None
                        and merged.critique.should_continue
                    ),
                )
            )
        return _with(merged, {"events": events})

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
                    node=REPORT_REVIEW_NODE,
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
                    node=REPORT_REVIEW_NODE,
                    review_status=review.status,
                    reason="report_reviewer_unconfigured",
                )
            ],
            False,
        )
    review = await reviewer.review(packet, previous=previous)
    errors: list[ResearchError] = []
    if review.status != "scored":
        errors.append(
            report_review_unavailable_error(
                node=REPORT_REVIEW_NODE,
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
        reviewed_evidence_ids=[],
        omitted_evidence_ids=list(packet.evidence_ids),
        reviewed_batch_ids=[],
        expected_batch_ids=list(packet.expected_batch_ids),
        reviewed_target_ids=[target.target_id for target in packet.targets],
        input_fingerprint=packet.fingerprint,
        composition_fingerprint=packet.composition_fingerprint,
        rubric_version=packet.rubric_version,
        rationale=reason,
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


async def refine_node(channel: ResearchGraphState) -> ResearchGraphState:
    """Open the next macro iteration, routing the repair it is about to run.

    This exists as its own node because a LangGraph conditional edge routes
    but cannot write, and both the macro-iteration increment and the repair
    plan have to happen somewhere the graph can see and a test can call.

    Two things are settled here, and both are about the pass that just
    finished rather than the one about to start:

    * the typed repair jobs the next pass owes — the Critic's defects unioned
      with the plan's mechanically unmet required targets — so a resumed run
      can name exactly what it was about to repair, and the hop's own edge can
      dispatch them;
    * whether the pass just finished changed anything substantive, compared
      against the snapshot the previous hop recorded. A whole repair job is
      one unit here: a plan extension and the acquisition it triggered finish
      together, so adding a target is never mistaken for answering one.

    The reviews a *typed* repair invalidates are dropped here too, because this
    is where the repair's inputs are declared to have changed. Mechanically
    unmet targets are excluded on purpose: they produce an ``acquire`` job on
    every pass, so invalidating on those would delete the claims of every
    still-open obligation each time the loop turned.

    The comparison is only made once a previous snapshot exists, so the first
    refinement can never be called a stall, and the stop reason is evaluated
    only while budget remains — the ceiling keeps its own reason.
    """
    state = load_state(channel)
    if is_halted(state):
        return _skipped(state, REFINE_NODE)
    if state.iteration >= state.max_iterations:
        return _halt(
            state,
            invalid_route_error(
                node=REFINE_NODE,
                iteration=state.iteration,
                max_iterations=state.max_iterations,
            ),
        )

    previous = state.progress_history[-1] if state.progress_history else None
    after = progress_snapshot(state, previous=previous)
    stopped = (
        None
        if previous is None
        else repair_stop_reason(state, before=previous, after=after)
    )
    targets = refinement_targets_for(state)
    recorded = merge_research_state(
        state,
        {
            "refinement_targets": targets,
            "progress_history": [after],
            "repair_stop_reason": stopped,
        },
    )
    if stopped is not None and repair_is_terminal(recorded):
        # The pass just finished changed nothing another pass would change, so
        # no further pass is opened: the iteration does not advance, no agent
        # runs again, and the run goes straight to publication with the reason
        # recorded on the same route vocabulary every other decision uses.
        #
        # Nothing is invalidated on this path. No pass follows to re-derive
        # what an invalidation drops, so the ledger the publication reads has
        # to stay the one the last review judged — otherwise the state behind
        # the report (its claim count, its quality snapshot, the checkpoint)
        # stops agreeing with the artifact, which is rendered from a
        # composition that still cites the dropped claim.
        return _with(
            recorded,
            {
                "events": [
                    route_decided_event(
                        destination=ROUTE_FINALIZE,
                        reason=stopped,
                        iteration=state.iteration,
                        max_iterations=state.max_iterations,
                        should_continue=bool(
                            recorded.critique is not None
                            and recorded.critique.should_continue
                        ),
                    )
                ]
            },
        )

    # A pass will run, so the reviews the typed jobs change are dropped here and
    # re-derived there. Mechanically unmet targets are excluded: their
    # ``acquire`` job exists on every pass, so invalidating on one would delete
    # the claims of every still-open obligation each time the loop turned.
    recorded = merge_research_state(
        recorded,
        invalidation_update(
            state,
            [job for job in targets if job.origin != "unanswered_target"],
        ),
    )
    advanced = advance_research_iteration(recorded)
    return _with(
        advanced,
        {
            "events": [
                refinement_started_event(
                    iteration=advanced.iteration,
                    max_iterations=advanced.max_iterations,
                )
            ]
        },
    )


def route_after_critic(channel: ResearchGraphState) -> str:
    """The conditional edge out of the Critic. Pure read of state."""
    return graph_route(load_state(channel))[0]


def route_after_refine(channel: ResearchGraphState) -> str:
    """The refinement hop's own edge: planner, researcher, or publication.

    ``refine`` is where the stop reason and the typed worklist are decided, so
    it is also where they take effect: a stall publishes without paying for one
    more pass, and an ``extend_plan`` job goes to the Planner — which is the
    only node that can add the obligation an original-question omission asks
    for. Everything else opens an ordinary research pass. The names are the
    three destinations the orchestrator wires.
    """
    state = load_state(channel)
    if repair_is_terminal(state):
        return "finalize"
    if any(job.action == "extend_plan" for job in state.refinement_targets):
        return "planner"
    return "researcher"


# --- Task 9: the typed repair route -----------------------------------------

REPAIR_NODES: dict[RepairAction, str] = {
    "extend_plan": PLANNER_NODE,
    "acquire": RESEARCHER_NODE,
    "assess_source": SOURCE_EVALUATOR_NODE,
    "adjudicate": FACT_CHECKER_NODE,
    "consolidate": FACT_CHECKER_NODE,
    "synthesize": SYNTHESIZER_NODE,
}
"""One node per typed repair action, keyed by ``CritiqueGap.repair_action``.

The keys are Task 8's normative ``REPAIR_ACTIONS`` literals and nothing else,
so a defect the Critic typed is routed by exactly the vocabulary it was typed
with. ``consolidate`` and ``adjudicate`` share the Fact Checker because
merging duplicate claims *is* adjudication of them; the two actions stay
distinct in the contract because the defect they repair is not the same.

A test asserts this table's keys equal ``REPAIR_ACTIONS``: an action added
without a node, or a node added without an action, is a routing hole rather
than a new capability.
"""

# Which origin wins when two defects become one job. A Critic-named defect is
# the most specific record of it — it carries the queries and the gap id — so
# it outranks a defect the terminal reviewer named, which carries the same
# typed shape from a different review; both outrank an assertion the
# Synthesizer returned, which in turn outranks the graph's own mechanical
# reading of an unmet target.
_ORIGIN_RANK: dict[RefinementOrigin, int] = {
    "critic_gap": 0,
    "review_defect": 1,
    "returned_assertion": 2,
    "unanswered_target": 3,
}

_SEVERITY_RANK: dict[str, int] = {"critical": 3, "major": 2, "minor": 1}


def route_refinement(target: RefinementTarget) -> str:
    """The node one repair job runs, from its typed action alone.

    An action this table does not know is an error, never a fallback: routing
    an unknown defect to synthesis would publish a repair nobody asked for,
    and would hide the contract drift that produced the unknown value.
    """
    node = REPAIR_NODES.get(target.action)
    if node is None:
        raise GraphConfigurationError(
            f"no repair node is wired for repair action {target.action!r}"
        )
    return node


def refinement_targets_for(state: ResearchState) -> list[RefinementTarget]:
    """Every repair job this pass owes: the Critic's defects and the plan's.

    The union is the point. A defect the Critic named is routed exactly as it
    was typed — its action, its resolved scope, and its queries, never
    re-resolved and never widened to the whole answer — and a mechanically
    unmet required target is routed to acquisition whether or not the Critic
    mentioned it. Critic silence therefore suppresses nothing a plan still
    owes: the reviewed baseline skipped topic-04, topic-02 and topic-05
    because one raw finding existed and no gap named them.

    There is one job per ``(action, scope)``. Two defects that route to the
    same node over the same records are one errand, and the Critic's version
    survives because it carries the queries and the gap id. Two jobs that
    differ in action stay apart even when their scope and their words are
    identical: they are two nodes' work, and collapsing them would adopt one
    node's repair for the other's defect.

    ``target_ids=["question"]`` is read as what it is — an original-question
    omission — and is carried through unchanged. It is never expanded into
    planned ids here: the token is only ever correct because the plan has no
    id for that obligation yet.
    """
    jobs: list[RefinementTarget] = []
    critique = state.critique
    if critique is not None and critique.review_status == "reviewed":
        for gap in critique.gaps:
            jobs.append(
                RefinementTarget(
                    gap_id=gap.gap_id,
                    coverage_id=gap.coverage_id,
                    target_ids=list(gap.target_ids),
                    claim_cluster_ids=list(gap.claim_cluster_ids),
                    statement_ids=list(gap.statement_ids),
                    action=gap.repair_action,
                    queries=list(gap.recommended_queries),
                    origin="critic_gap",
                    severity=gap.severity,
                    problem=gap.problem,
                )
            )

    for topic in state.sub_topics:
        for target in unanswered_required_targets(state, topic):
            jobs.append(
                RefinementTarget(
                    coverage_id=topic.coverage_id,
                    target_ids=[target.target_id],
                    action="acquire",
                    origin="unanswered_target",
                    severity="critical" if target.critical else "major",
                    problem=(
                        f"{topic.title}: the required obligation "
                        f"{target.target_id!r} is not answered by any reader "
                        "statement."
                    ),
                )
            )

    # The terminal semantic review's defects are jobs like any other: routed by
    # the action they were typed with, over the scope they named. They are read
    # only from a *scored* review, so a review that never happened contributes
    # no "no defects found" and no invented work — the report is simply
    # unreviewed, and `graph_quality_status` says so.
    for gap in review_defects_as_refinement_jobs(state.report_review):
        jobs.append(
            RefinementTarget(
                gap_id=gap.gap_id,
                coverage_id=gap.coverage_id,
                target_ids=list(gap.target_ids),
                claim_cluster_ids=list(gap.claim_cluster_ids),
                statement_ids=list(gap.statement_ids),
                action=gap.repair_action,
                queries=list(gap.recommended_queries),
                origin="review_defect",
                severity=gap.severity,
                problem=gap.problem,
            )
        )

    composition = state.composition
    if composition is not None:
        for assertion in composition.returned_to_fact_checker:
            jobs.append(
                RefinementTarget(
                    action="adjudicate",
                    origin="returned_assertion",
                    severity="major",
                    problem=assertion,
                )
            )
    return _merged_jobs(jobs)


def _merged_jobs(jobs: Sequence[RefinementTarget]) -> list[RefinementTarget]:
    """Fold jobs that route identically over the same scope into one.

    Order is first-seen, so a job's position is stable across passes. The
    surviving wording is the most severe one's, the queries are the union of
    every reading, and the gap id is kept from whichever job had one — a job
    that came from a Critic gap stays addressable by that gap.
    """
    merged: list[RefinementTarget] = []
    index: dict[tuple[object, ...], int] = {}
    for job in jobs:
        previous = index.get(job.identity)
        if previous is None:
            index[job.identity] = len(merged)
            merged.append(job)
            continue
        incumbent = merged[previous]
        survivor = (
            job
            if _ORIGIN_RANK[job.origin] < _ORIGIN_RANK[incumbent.origin]
            else incumbent
        )
        other = incumbent if survivor is job else job
        factual = max(
            (survivor, other), key=lambda item: _SEVERITY_RANK[item.severity]
        )
        merged[previous] = survivor.model_copy(
            update={
                "queries": list(
                    dict.fromkeys([*survivor.queries, *other.queries])
                ),
                "severity": factual.severity,
                "problem": factual.problem,
                "gap_id": survivor.gap_id or other.gap_id,
            }
        )
    return merged


def invalidation_update(
    state: ResearchState,
    targets: Sequence[RefinementTarget],
) -> ResearchStateUpdate:
    """The reviews a repair invalidates, and nothing else.

    A publication-changing repair drops the verified claims it touches — a
    claim whose target, cluster, or statement the repair names — along with
    the quality snapshot that judged a report those claims no longer support.
    Surviving claims keep their ``claim_id``, their cluster, and their
    citations, so unrelated verified claims and the target coverage they
    carry are preserved rather than re-derived.

    A source re-assessment additionally drops the *source* reviews its claims
    rested on: ``evaluated_sources`` rows for the URLs those claims cite, since
    a score for a body whose assessment is being redone is the same stale
    judgement the claims were just told to stop relying on. No other action
    touches source rows.

    A presentation-only repair invalidates nothing at all: ``synthesize``
    rewrites prose over evidence the run already holds, and dropping verified
    claims for a re-worded paragraph is exactly the evidence loss this
    distinction prevents.

    Nothing is *deleted* here: the claim cluster registry keeps every identity
    and its provenance, so the invalidation is visible as "not currently
    reviewed" rather than as evidence that never existed.
    """
    changing = [target for target in targets if target.publication_changing]
    if not changing:
        return {}

    target_scope = {
        target_id for target in changing for target_id in target.target_ids
    }
    cluster_scope = {
        cluster_id
        for target in changing
        for cluster_id in target.claim_cluster_ids
    }
    statement_scope = {
        statement_id
        for target in changing
        for statement_id in target.statement_ids
    }
    cluster_scope.update(_clusters_of_statements(state.composition, statement_scope))

    kept = [
        claim
        for claim in state.verified_claims
        if not _claim_is_invalidated(claim, target_scope, cluster_scope)
    ]
    update: ResearchStateUpdate = {"quality": None}
    if len(kept) != len(state.verified_claims):
        update["verified_claims"] = kept

    if any(target.action == "assess_source" for target in changing):
        # Normalized on both sides: a cited URL and a scored row can be written
        # in two spellings of one address, and comparing the raw strings would
        # leave a stale score standing for a source that was just invalidated.
        cited = {
            normalize_source_url(url)
            for claim in state.verified_claims
            if _claim_is_invalidated(claim, target_scope, cluster_scope)
            for url in claim.source_urls
        }
        kept_sources = [
            source
            for source in state.evaluated_sources
            if normalize_source_url(source.url) not in cited
        ]
        if len(kept_sources) != len(state.evaluated_sources):
            update["evaluated_sources"] = kept_sources
    return update


def _clusters_of_statements(
    composition: ReportComposition | None,
    statement_ids: set[str],
) -> set[str]:
    """The clusters the named reader statements rest on."""
    if composition is None or not statement_ids:
        return set()
    return {
        cluster_id
        for statement in composition.statements
        if statement.statement_id in statement_ids
        for cluster_id in statement.claim_cluster_ids
    }


def _claim_is_invalidated(
    claim: Claim,
    target_scope: set[str],
    cluster_scope: set[str],
) -> bool:
    """True when a repair touches this claim's obligation or cluster."""
    if target_scope.intersection(claim.target_ids):
        return True
    if claim.cluster_id is not None and claim.cluster_id in cluster_scope:
        return True
    if cluster_scope.intersection(claim.cluster_aliases):
        return True
    if claim.claim_id in cluster_scope:
        return True
    return any(
        coverage_id in cluster_scope
        for coverage_id in claim.consumed_coverage_ids
    )
