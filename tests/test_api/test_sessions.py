"""Tests for typed API models and the in-memory research session store."""

from __future__ import annotations

import asyncio
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
from deep_research.api.sessions import SessionStore, outcome_response_fields
from deep_research.graph.orchestrator import GraphRun
from deep_research.runtime.errors import configuration_error
from deep_research.runtime.outcome import ResearchOutcome, build_outcome
from deep_research.utils.types import (
    QUALITY_CONTRACT_VERSION,
    REVIEW_DIMENSIONS,
    ResearchError,
    ResearchEvent,
    ResearchState,
)
from tests.graph_fakes import (
    fake_claim,
    fake_finding,
    fake_quality,
    fake_reader_composition,
    fake_report_review,
    fake_research_state,
    fake_scored_source,
    fake_sub_topic,
)
from tests.test_api.fakes import GateRunner, ScriptedRunner

REPORT_PATH = "output/report-session-1-0.md"
EVIDENCE_PATH = "output/report-session-1-0-evidence.md"
QUALITY_PATH = "output/report-session-1-0-quality.json"


def judged_state() -> ResearchState:
    """One judged pass: a quality snapshot, a review, and a composition.

    The snapshot carries the critical-target reading the coverage block
    publishes and the composition carries the claims and sources the evidence
    block counts, so every additive field has a typed record behind it.
    """
    state = fake_research_state(
        raw_findings=[fake_finding()],
        evaluated_sources=[fake_scored_source()],
        verified_claims=[fake_claim()],
        sub_topics=[fake_sub_topic()],
        quality=fake_quality().model_copy(
            update={
                "covered_topics": 1,
                "substantive_covered_topics": 1,
                "critical_targets": 2,
                "unanswered_critical_target_ids": ["t2"],
            }
        ),
        report_review=fake_report_review(
            dimensions={name: 0.75 for name in REVIEW_DIMENSIONS}
        ),
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
    composition = fake_reader_composition(state)
    return state.model_copy(update={"composition": composition})


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
    max_iterations: int | None = None,
    config_overrides: dict[str, object] | None = None,
) -> None:
    store.start(
        session_id=session_id,
        query=query,
        max_iterations=max_iterations,
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


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"query": "   "},
        {"query": "Question", "max_iterations": 0},
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
            status=status,
            iteration=0,
            started_at=datetime.now(timezone.utc),
        )

        assert response.status == status


def test_session_response_accepts_a_complete_lifecycle() -> None:
    response = ResearchSessionResponse(
        session_id="session-1",
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

    The coverage block keeps the two denominators apart, the evidence block
    keeps ten distinct counts of ten different things, and the review fields
    carry the status and score the snapshot was stamped with. An empty review
    status is ``None`` here: "no review was recorded" is not a status.
    """
    state = judged_state()
    response = ResearchSessionResponse(
        session_id="session-1",
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
    assert response.coverage.planned_topics == 2
    assert response.coverage.covered_topics == 1
    assert response.coverage.unanswered_critical_target_ids == ["t2"]
    assert response.evidence_counts is not None
    assert response.evidence_counts.corroborated == 1
    assert response.evidence_counts.checked_claims == 1


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
        max_iterations=2,
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
