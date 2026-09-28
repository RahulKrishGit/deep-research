"""Tests for typed API models and the in-memory research session store."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from deep_research.api.models import (
    ApiErrorBody,
    ApiErrorResponse,
    ResearchRequest,
    ResearchSessionResponse,
    TraceMetadata,
    TraceResponse,
    ValidationIssue,
)
from deep_research.api.sessions import (
    ResearchSession,
    SessionStore,
    outcome_response_fields,
)
from deep_research.graph.orchestrator import GraphRun
from deep_research.runtime.errors import configuration_error
from deep_research.runtime.outcome import ResearchOutcome, build_outcome
from deep_research.utils.types import (
    QUALITY_CONTRACT_VERSION,
    REVIEW_DIMENSIONS,
    FigureContext,
    FigureResult,
    Finding,
    FindingVerification,
    NotFoundTarget,
    ReadRecord,
    ReportComposition,
    ReportPoint,
    ReportQualitySnapshot,
    ReportReview,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ScoredSource,
    SubTopic,
)
from tests.evidence_fakes import figure, make_finding, make_read
from tests.test_api.fakes import GateRunner, ScriptedRunner

REPORT_PATH = "output/report-session-1-0.md"
EVIDENCE_PATH = "output/report-session-1-0-evidence.md"
QUALITY_PATH = "output/report-session-1-0-quality.json"

QUESTION = "How mature is quantum error correction?"
ANSWERED_TARGET_IDS = ("topic-01-target-01", "topic-01-target-02")
MISSING_TARGET_ID = "topic-02-target-01"
SOURCE_URL = "https://www.eia.gov/todayinenergy/detail.php?id=64705"
OWNER = "U.S. Energy Information Administration"


def scored_review(**overrides: object) -> ReportReview:
    """A scored terminal review, which is what a judged pass carries."""
    payload: dict[str, object] = {
        "status": "scored",
        "dimensions": {name: 0.75 for name in REVIEW_DIMENSIONS},
        "reviewed_statement_ids": ["S001"],
        "per_statement_dispositions": {"S001": "supported"},
        "input_fingerprint": "packet-1",
        "composition_fingerprint": "composition-1",
    }
    payload.update(overrides)
    return ReportReview.model_validate(payload)


def quality_snapshot(**overrides: object) -> ReportQualitySnapshot:
    """The pass's snapshot: target ids and the verifier's finding readings.

    Only the readings this pipeline computes are named. The retired readings
    the type still carries are left at their defaults, so this fixture never
    depends on one.
    """
    payload: dict[str, object] = {
        "required_target_ids": [*ANSWERED_TARGET_IDS, MISSING_TARGET_ID],
        "answered_target_ids": list(ANSWERED_TARGET_IDS),
        "missing_required_target_ids": [MISSING_TARGET_ID],
        "verified_findings": 2,
        "dropped_findings": 1,
        "cited_findings": 2,
    }
    payload.update(overrides)
    return ReportQualitySnapshot.model_validate(payload)


def kept_finding(
    snippet: str, *, value: str, unit: str, target_ids: Sequence[str]
) -> Finding:
    """One finding the Evidence Verifier kept, figure and context included."""
    read = make_read()
    wanted = figure(value, unit, "2024", "actual")
    return make_finding(
        read,
        snippet,
        figures=[wanted],
        target_ids=list(target_ids),
        verification=FindingVerification(
            status="verified",
            figure_results=[
                FigureResult(
                    figure=wanted,
                    matched=True,
                    context=FigureContext(
                        period="2024",
                        attribution="own",
                        organisation=OWNER,
                        kind="actual",
                    ),
                    evidence_words=snippet,
                )
            ],
        ),
    )


def kept_findings() -> list[Finding]:
    """The two findings the pass kept, each answering one required target."""
    return [
        kept_finding(
            "Generators added 10.4 gigawatts (GW) of new battery storage "
            "capacity in 2024",
            value="10.4",
            unit="GW",
            target_ids=["topic-01-target-01"],
        ),
        kept_finding(
            "capacity growth from battery storage could set a record",
            value="19.6",
            unit="GW",
            target_ids=["topic-01-target-02"],
        ),
    ]


def scored_source() -> ScoredSource:
    return ScoredSource(
        url=SOURCE_URL,
        title="U.S. battery capacity increased 66% in 2024",
        authority_score=0.8,
        recency_score=0.8,
        relevance_score=0.8,
        overall_score=0.8,
        rationale="Read primary material with a stated date.",
        publisher_id="eia.gov",
    )


def judged_composition() -> ReportComposition:
    """The pass's composition: the kept findings, and what it could not find."""
    return ReportComposition(
        question=QUESTION,
        session_id="session-1",
        sources=[scored_source()],
        findings=kept_findings(),
        not_found=[
            NotFoundTarget(
                target_id=MISSING_TARGET_ID,
                question="How much battery storage is planned for 2025?",
                queries=["battery storage 2025 plans"],
                searched=True,
            )
        ],
        summary=[
            ReportPoint(
                text="Battery storage capacity grew by 66% in 2024.",
                source_urls=[SOURCE_URL],
            )
        ],
        sub_topics=[
            SubTopic(
                coverage_id="topic-01",
                title="Battery storage capacity",
                rationale="It answers the question.",
                search_queries=["battery storage capacity 2024"],
                success_criteria=["A read source answers it."],
                priority=1,
            )
        ],
    )


def judged_state() -> ResearchState:
    """One judged pass: a quality snapshot, a review, and a composition.

    The snapshot carries the required and answered target ids the coverage
    block publishes and the Evidence Verifier's own finding readings the
    evidence block counts; the composition carries the Not found list and the
    findings the read-side counts are taken from, so every additive field has
    a typed record behind it.
    """
    findings = kept_findings()
    reads: dict[str, ReadRecord] = {
        read.read_id: read for read in (make_read(),)
    }
    return ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        verified_findings=findings,
        evaluated_sources=[scored_source()],
        sub_topics=list(judged_composition().sub_topics),
        read_records=reads,
        composition=judged_composition(),
        quality=quality_snapshot(),
        report_review=scored_review(),
        quality_contract_version=QUALITY_CONTRACT_VERSION,
        evidence_path=EVIDENCE_PATH,
        quality_path=QUALITY_PATH,
        events=[
            ResearchEvent(
                event_type="graph.node.started",
                source="graph.planner",
                message="Node planner started.",
                timestamp="2026-08-03T12:00:00+00:00",
            ),
            ResearchEvent(
                event_type="graph.node.completed",
                source="graph.planner",
                message="Node planner completed.",
                timestamp="2026-08-03T12:00:30+00:00",
            ),
        ],
    )


def outcome_of(state: ResearchState) -> ResearchOutcome:
    """The outcome one finished session holds, paths and all."""
    return build_outcome(
        GraphRun(
            session_id="session-1",
            state=state,
            status="completed",
            trace_url=None,
        ),
        metrics=(),
    )


def start_session(
    store: SessionStore,
    *,
    session_id: str = "session-1",
    query: str = "Question",
    max_extra_passes: int | None = None,
    config_overrides: dict[str, object] | None = None,
) -> None:
    store.start(
        session_id=session_id,
        query=query,
        max_extra_passes=max_extra_passes,
        output_format="markdown",
        config_overrides=config_overrides or {},
        config_path="config.yaml",
    )


# --- strict request model -------------------------------------------------


def test_research_request_accepts_every_field_and_strips_whitespace() -> None:
    request = ResearchRequest.model_validate(
        {
            "query": "  How mature is quantum error correction?  ",
            "max_iterations": 2,
            "output_format": "markdown",
            "config_overrides": {"output": {"directory": "api-output/"}},
        }
    )

    assert request.query == "How mature is quantum error correction?"
    assert request.max_iterations == 2
    assert request.output_format == "markdown"
    assert request.config_overrides == {
        "output": {"directory": "api-output/"}
    }

def test_research_request_applies_safe_defaults() -> None:
    request = ResearchRequest.model_validate({"query": "Question"})

    assert request.max_iterations is None
    assert request.output_format == "markdown"
    assert request.config_overrides == {}


def test_research_request_accepts_zero_extra_passes() -> None:
    """PD-15: the field keeps its name and zero is a legitimate ceiling."""
    request = ResearchRequest.model_validate(
        {"query": "Question", "max_iterations": 0}
    )

    assert request.max_iterations == 0


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"query": "   "},
        {"query": "Question", "max_iterations": -1},
        {"query": "Question", "output_format": "pdf"},
        {"query": "Question", "unknown_field": "x"},
        {
            "query": "Question",
            "config_overrides": {"graph": {"iteration_limit": 2}},
        },
    ],
)
def test_research_request_rejects_invalid_payloads(payload) -> None:
    with pytest.raises(ValidationError):
        ResearchRequest.model_validate(payload)


# --- strict session, trace, and error models ------------------------------


def test_session_response_accepts_every_status() -> None:
    for status in (
        "running",
        "completed",
        "max_iterations",
        "incomplete",
        "failed",
    ):
        response = ResearchSessionResponse(
            session_id="session-1",
            query="Question",
            status=status,
            iteration=0,
            started_at=datetime.now(timezone.utc),
        )

        assert response.status == status


def test_session_response_accepts_a_complete_lifecycle() -> None:
    response = ResearchSessionResponse(
        session_id="session-1",
        query="Question",
        status="completed",
        current_agent=None,
        iteration=2,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        report_path="report-session-1.md",
        trace_url="https://smith.example/r/session-1",
        errors=[
            ResearchError(
                error_type="graph_invalid_agent_state",
                source="graph.researcher",
                message="An agent returned an invalid state update.",
                recoverable=False,
            )
        ],
    )

    assert response.iteration == 2
    assert response.report_path == "report-session-1.md"
    assert response.errors[0].recoverable is False


def test_session_response_defaults_the_outcome_fields_to_no_answer() -> None:
    """A running session answers ``None``, not a zero it never measured.

    The additive fields are the finished outcome's own readings, so a session
    that has none carries no path, no version, no score, no span and no
    counts — never a legacy version, an empty bucket set or a zero duration.
    """
    response = ResearchSessionResponse(
        session_id="session-1",
        query="Question",
        status="running",
        iteration=0,
        started_at=datetime.now(timezone.utc),
    )

    assert response.evidence_path is None
    assert response.quality_path is None
    assert response.quality_contract_version is None
    assert response.semantic_review_status is None
    assert response.semantic_review_score is None
    assert response.duration_seconds is None
    assert response.coverage is None
    assert response.evidence_counts is None


def test_session_response_reads_the_typed_measurements_a_pass_recorded() -> None:
    """Every additive field is the outcome property, not a re-derived number.

    The coverage block keeps the gate's missing-target reading apart from the
    report's Not found list, the evidence block keeps the read-side counts
    apart from the Evidence Verifier's own finding readings, and the review
    fields carry the status and score the snapshot was stamped with. An empty
    review status is ``None`` here: "no review was recorded" is not a status.
    """
    state = judged_state()
    response = ResearchSessionResponse(
        session_id="session-1",
        query="Question",
        status="completed",
        iteration=1,
        started_at=datetime.now(timezone.utc),
        report_path=REPORT_PATH,
        **outcome_response_fields(outcome_of(state)),
    )

    assert response.evidence_path == EVIDENCE_PATH
    assert response.quality_path == QUALITY_PATH
    assert response.quality_contract_version == QUALITY_CONTRACT_VERSION
    assert response.semantic_review_status == "scored"
    assert response.semantic_review_score == 0.75
    assert response.duration_seconds == 30.0
    assert response.coverage is not None
    assert (response.coverage.required_targets, response.coverage.answered_targets) == (
        3,
        2,
    )
    assert response.coverage.missing_required_target_ids == [MISSING_TARGET_ID]
    assert response.coverage.not_found_target_ids == [MISSING_TARGET_ID]
    assert response.evidence_counts is not None
    assert (
        response.evidence_counts.verified_findings,
        response.evidence_counts.dropped_findings,
    ) == (2, 1)
    assert response.evidence_counts.cited_findings == 2
    assert response.evidence_counts.findings == 2
    assert response.evidence_counts.assessed_sources == 1
    assert response.evidence_counts.cited_assessed_sources == 1


def test_session_response_counts_quoted_findings_apart() -> None:
    """D21: a quoted finding is neither verified nor dropped; the API's
    evidence counts must carry it as its own reading, not silently drop it."""
    state = judged_state().model_copy(
        update={"quality": quality_snapshot(quoted_findings=5)}
    )
    response = ResearchSessionResponse(
        session_id="session-1",
        query="Question",
        status="completed",
        iteration=1,
        started_at=datetime.now(timezone.utc),
        report_path=REPORT_PATH,
        **outcome_response_fields(outcome_of(state)),
    )

    assert response.evidence_counts is not None
    assert response.evidence_counts.quoted_findings == 5


def test_session_response_fields_are_empty_without_an_outcome() -> None:
    """No outcome contributes nothing: no field is defaulted into the reply."""
    assert outcome_response_fields(None) == {}


@pytest.mark.parametrize(
    "payload",
    [
        {
            "session_id": "session-1",
            "status": "cancelled",
            "iteration": 0,
            "started_at": "2026-08-03T12:00:00+00:00",
        },
        {
            "session_id": "session-1",
            "status": "completed",
            "iteration": -1,
            "started_at": "2026-08-03T12:00:00+00:00",
        },
        {
            "session_id": "session-1",
            "status": "completed",
            "iteration": 0,
            "started_at": "2026-08-03T12:00:00+00:00",
            "extra": 1,
        },
    ],
)
def test_session_response_rejects_invalid_payloads(payload) -> None:
    with pytest.raises(ValidationError):
        ResearchSessionResponse.model_validate(payload)


def test_trace_models_carry_route_metadata() -> None:
    trace = TraceResponse(
        session_id="session-1",
        trace_url="https://smith.example/r/session-1",
        metadata=TraceMetadata(
            session_id="session-1",
            route="/research/{session_id}/trace",
            status="completed",
        ),
    )

    assert trace.metadata.route == "/research/{session_id}/trace"
    assert trace.metadata.status == "completed"


def test_trace_metadata_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        TraceMetadata.model_validate(
            {
                "session_id": "session-1",
                "route": "/research/{session_id}/trace",
                "status": "completed",
                "extra": 1,
            }
        )


def test_api_error_response_exposes_only_locations_and_types() -> None:
    response = ApiErrorResponse(
        error=ApiErrorBody(
            code="validation_error",
            message="Request validation failed.",
            issues=[
                ValidationIssue(location="body.query", type="missing"),
                ValidationIssue(
                    location="body.config_overrides.graph.iteration_limit",
                    type="value_error",
                ),
            ],
        )
    )

    assert response.error.code == "validation_error"
    assert response.error.issues[0].location == "body.query"
    assert response.error.issues[0].type == "missing"


def test_api_error_body_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ApiErrorBody.model_validate(
            {
                "code": "validation_error",
                "message": "Request validation failed.",
                "extra": 1,
            }
        )


# --- non-blocking lifecycle -------------------------------------------------


@pytest.mark.asyncio
async def test_start_is_non_blocking_and_progress_updates_status() -> None:
    runner = GateRunner()
    store = SessionStore(runner=runner)

    session = store.start(
        session_id="session-1",
        query="How mature is quantum error correction?",
        max_extra_passes=2,
        output_format="markdown",
        config_overrides={"output": {"directory": "api-output/"}},
        config_path="config.yaml",
    )
    await runner.started.wait()

    assert session.status == "running"
    assert session.current_agent == "planner"
    assert session.iteration == 1
    assert runner.calls[0]["config_overrides"] == {
        "output": {"directory": "api-output/"}
    }
    # The request's ``max_iterations`` reaches the runner as the extra-pass
    # ceiling, in the graph's own vocabulary (PD-15).
    assert runner.calls[0]["max_extra_passes"] == 2

    runner.release.set()
    assert session.task is not None
    await session.task

    assert session.status == "completed"
    assert session.current_agent is None
    assert session.finished_at is not None
    assert session.report_path == "report-session-1.md"


@pytest.mark.asyncio
async def test_event_iterator_replays_events_and_stops_at_terminal_status() -> None:
    runner = ScriptedRunner(
        events=[
            ResearchEvent(
                event_type="graph.node.started",
                source="graph.planner",
                message="Node planner started.",
                metadata={"node": "planner", "iteration": 0},
            )
        ]
    )
    store = SessionStore(runner=runner)
    start_session(store)

    received = [
        event async for event in store.iter_events("session-1")
    ]

    assert [event.event_type for event in received] == [
        "graph.node.started"
    ]
    assert store.require("session-1").status == "completed"


# --- safe failures -----------------------------------------------------------


@pytest.mark.asyncio
async def test_configuration_failure_becomes_a_safe_failed_session() -> None:
    runner = ScriptedRunner(
        error=configuration_error(
            reason="missing_secrets",
            message="secret value sk-never-return-this",
        )
    )
    store = SessionStore(runner=runner)
    start_session(store)
    session = store.require("session-1")
    await session.task

    assert session.status == "failed"
    assert session.finished_at is not None
    error = session.errors[0]
    assert error.error_type == "api.research.configuration_error"
    assert error.recoverable is False
    assert error.details == {"reason": "missing_secrets"}
    assert "sk-never-return-this" not in error.message
    event = session.events[-1]
    assert event.event_type == "api.research.configuration_error"
    assert event.source == "api"
    assert event.metadata == {"reason": "missing_secrets"}
    assert "sk-never-return-this" not in event.message


@pytest.mark.asyncio
async def test_unexpected_failure_records_only_the_exception_type() -> None:
    runner = ScriptedRunner(error=RuntimeError("boom: sk-never-return-this"))
    store = SessionStore(runner=runner)
    start_session(store)
    session = store.require("session-1")
    await session.task

    assert session.status == "failed"
    error = session.errors[0]
    assert error.error_type == "api.research.failed"
    assert error.recoverable is False
    assert error.details == {"exception_type": "RuntimeError"}
    assert "boom" not in error.message
    event = session.events[-1]
    assert event.event_type == "api.research.failed"
    assert event.metadata == {"exception_type": "RuntimeError"}
    assert "boom" not in event.message


# --- isolation, cancellation, and unknown sessions --------------------------


@pytest.mark.asyncio
async def test_published_events_are_deep_copied_away_from_the_caller() -> None:
    event = ResearchEvent(
        event_type="graph.node.started",
        source="graph.planner",
        message="Node planner started.",
        metadata={"node": "planner", "iteration": 1, "nested": {"x": 1}},
    )
    store = SessionStore(runner=ScriptedRunner(events=[event]))
    start_session(store)
    session = store.require("session-1")
    await session.task

    event.metadata["node"] = "mutated"
    event.metadata["nested"]["x"] = 999

    stored = session.events[0]
    assert stored.metadata["node"] == "planner"
    assert stored.metadata["nested"]["x"] == 1


@pytest.mark.asyncio
async def test_close_cancels_running_tasks_without_failing_the_session() -> None:
    runner = GateRunner()
    store = SessionStore(runner=runner)
    start_session(store)
    await runner.started.wait()
    session = store.require("session-1")

    assert session.status == "running"
    await store.close()

    assert session.task is not None
    assert session.task.cancelled()
    assert session.status == "running"
    assert session.finished_at is not None
    assert session.current_agent is None


@pytest.mark.asyncio
async def test_iter_events_terminates_after_a_running_task_is_cancelled() -> None:
    """A cancelled finished session must close a live event stream.

    Cancellation has no public status value, so the session still reads
    ``running``; the iterator must still drain every stored event and stop
    instead of waiting on ``changed`` forever after ``close()``.
    """
    runner = GateRunner()
    store = SessionStore(runner=runner)
    start_session(store)
    await runner.started.wait()
    session = store.require("session-1")

    received: list[ResearchEvent] = []

    async def consume() -> None:
        async for event in store.iter_events("session-1"):
            received.append(event)

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    await store.close()
    await asyncio.wait_for(consumer, timeout=5)

    assert session.status == "running"
    assert session.finished_at is not None
    assert session.task is not None
    assert session.task.cancelled()
    assert received == session.events


@pytest.mark.asyncio
async def test_iter_events_terminates_after_immediate_start_to_close() -> None:
    """A stream must close when a session is closed before it ever ran.

    ``close()`` cancels the task before the event loop has stepped it, so
    the task's ``finally`` cleanup never executes and the session is left
    with no ``finished_at``; the iterator must still terminate instead of
    waiting on ``changed`` forever.
    """
    store = SessionStore(runner=GateRunner())
    start_session(store)
    session = store.require("session-1")

    await store.close()

    received: list[ResearchEvent] = []

    async def consume() -> None:
        async for event in store.iter_events("session-1"):
            received.append(event)

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(consumer, timeout=5)

    assert received == []
    assert session.finished_at is not None
    assert session.task is not None
    assert session.task.cancelled()


@pytest.mark.asyncio
async def test_require_rejects_unknown_session_ids() -> None:
    store = SessionStore(runner=ScriptedRunner())

    with pytest.raises(KeyError, match="no-such-session"):
        store.require("no-such-session")


@pytest.mark.asyncio
async def test_iter_events_rejects_unknown_session_ids() -> None:
    store = SessionStore(runner=ScriptedRunner())

    with pytest.raises(KeyError, match="no-such-session"):
        async for _event in store.iter_events("no-such-session"):
            pass


@pytest.mark.asyncio
async def test_start_rejects_duplicate_session_ids() -> None:
    store = SessionStore(runner=ScriptedRunner())
    start_session(store)

    with pytest.raises(ValueError, match="session-1"):
        start_session(store)


# --- iteration follows graph events only (spec A6) -------------------------


def _graph_or_tool_event(event_type: str, **metadata: object) -> ResearchEvent:
    return ResearchEvent(
        event_type=event_type, source="graph", message="event", metadata=metadata
    )


def test_publish_moves_iteration_only_for_graph_events() -> None:
    session = ResearchSession(
        session_id="session-1",
        query="Question",
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    session.publish(_graph_or_tool_event("graph.node.started", node="planner", iteration=0))
    assert session.iteration == 0
    # the researcher's ReAct step index must never read as the pass
    session.publish(_graph_or_tool_event("researcher.tool_call", tool="web_search", iteration=7))
    assert session.iteration == 0
    session.publish(_graph_or_tool_event("graph.extra_pass.started", iteration=1, max_extra_passes=1, targets=[]))
    assert session.iteration == 1
    session.publish(_graph_or_tool_event("graph.node.completed", node="researcher"))
    assert session.iteration == 1


def test_session_response_requires_query() -> None:
    with pytest.raises(ValidationError):
        ResearchSessionResponse(
            session_id="session-1",
            status="running",
            iteration=0,
            started_at=datetime.now(timezone.utc),
        )
