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

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, TypeAlias, runtime_checkable

from pydantic import JsonValue

from deep_research.agents.base import AgentRun
from deep_research.agents.errors import AgentConfigurationError, PlanningError
from deep_research.agents.identity import merge_claim_snapshot
from deep_research.agents.quality import compute_report_quality
from deep_research.agents.report import (
    QUALITY_STATUS_ACCEPTED,
    render_evidence_ledger,
    render_reader_report,
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
    request_attempt_limit_error,
)
from deep_research.graph.events import (
    node_completed_event,
    node_skipped_event,
    node_started_event,
    quality_assessed_event,
    refinement_started_event,
    report_published_event,
    route_decided_event,
)
from deep_research.graph.state import (
    CRITIC_NODE,
    FINALIZE_NODE,
    REFINE_NODE,
    SYNTHESIZER_NODE,
    ResearchGraphState,
    dump_state,
    graph_quality_status,
    graph_route,
    is_halted,
    load_state,
)
from deep_research.providers import ProviderConfigurationError
from deep_research.request_budget import RequestAttemptLimitError
from deep_research.tools.base import ToolResult
from deep_research.utils.types import (
    ReportComposition,
    ResearchError,
    ResearchState,
    ResearchStateUpdate,
    advance_research_iteration,
    merge_research_state,
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


async def refine_node(channel: ResearchGraphState) -> ResearchGraphState:
    """Open the next macro iteration before research runs again.

    This exists as its own node because a LangGraph conditional edge routes
    but cannot write, and the macro-iteration increment has to happen
    somewhere the graph can see and a test can call.
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

    advanced = advance_research_iteration(state)
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
