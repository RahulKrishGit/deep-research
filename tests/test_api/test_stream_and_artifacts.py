"""Tests for SSE streaming, the Markdown report, and the trace endpoint.

The stream, report, and trace routes are thin adapters around the session
store, so every test drives a scripted or gated runner — no provider, no
graph, no network. SSE frames must round-trip through
``ResearchEvent.model_validate``, report bodies must be authoritative
Markdown or a safe 409, and trace responses must carry only session, route,
and status metadata.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from deep_research.api.app import create_app
from deep_research.api.events import encode_sse
from deep_research.utils.types import QUALITY_CONTRACT_VERSION, ResearchEvent
from tests.test_api.fakes import GateRunner, ScriptedRunner
from tests.test_api.test_app import valid_preflight, wait_until_terminal
from tests.test_api.test_sessions import (
    EVIDENCE_PATH,
    QUALITY_PATH,
    REPORT_PATH,
    judged_state,
)


def test_encode_sse_frames_one_event_with_its_id() -> None:
    event = ResearchEvent(
        event_type="graph.node.started",
        source="graph.planner",
        message="Node planner started.",
        metadata={"node": "planner", "iteration": 0},
    )

    frame = encode_sse(event, event_id=1)

    assert frame == (
        "id: 1\n"
        "event: graph.node.started\n"
        f"data: {event.model_dump_json()}\n\n"
    )


def test_encode_sse_rejects_event_ids_below_one() -> None:
    event = ResearchEvent(
        event_type="graph.node.started",
        source="graph.planner",
        message="Node planner started.",
    )

    with pytest.raises(ValueError, match="event_id"):
        encode_sse(event, event_id=0)


def test_stream_returns_typed_progress_as_sse() -> None:
    event = ResearchEvent(
        event_type="graph.node.started",
        source="graph.planner",
        message="Node planner started.",
        metadata={"node": "planner", "iteration": 0},
    )
    app = create_app(
        runner=ScriptedRunner(events=[event]),
        preflight=valid_preflight,
    )

    with TestClient(app) as client:
        created = client.post(
            "/research",
            json={"query": "Question"},
        ).json()
        with client.stream(
            "GET",
            f"/research/{created['session_id']}/stream",
        ) as response:
            body = "".join(response.iter_text())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "text/event-stream"
    )
    assert "id: 1\n" in body
    assert "event: graph.node.started\n" in body
    data_line = next(
        line for line in body.splitlines() if line.startswith("data: ")
    )
    payload = json.loads(data_line.removeprefix("data: "))
    assert ResearchEvent.model_validate(payload) == event


def test_report_returns_authoritative_markdown() -> None:
    app = create_app(
        runner=ScriptedRunner(report="# Final report\n\nEvidence."),
        preflight=valid_preflight,
    )

    with TestClient(app) as client:
        session_id = client.post(
            "/research",
            json={"query": "Question"},
        ).json()["session_id"]
        wait_until_terminal(client, session_id)
        response = client.get(f"/research/{session_id}/report")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.text == "# Final report\n\nEvidence."


def test_report_returns_409_while_session_is_unfinished() -> None:
    runner = GateRunner()
    app = create_app(runner=runner, preflight=valid_preflight)

    with TestClient(app) as client:
        session_id = client.post(
            "/research",
            json={"query": "Question"},
        ).json()["session_id"]
        response = client.get(f"/research/{session_id}/report")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_not_complete"


def test_report_returns_409_when_outcome_has_no_report() -> None:
    app = create_app(
        runner=ScriptedRunner(report=None),
        preflight=valid_preflight,
    )

    with TestClient(app) as client:
        session_id = client.post(
            "/research",
            json={"query": "Question"},
        ).json()["session_id"]
        wait_until_terminal(client, session_id)
        response = client.get(f"/research/{session_id}/report")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "report_unavailable"


def test_trace_returns_url_and_route_metadata() -> None:
    app = create_app(
        runner=ScriptedRunner(
            trace_url="https://smith.example/r/session-1"
        ),
        preflight=valid_preflight,
    )

    with TestClient(app) as client:
        session_id = client.post(
            "/research",
            json={"query": "Question"},
        ).json()["session_id"]
        wait_until_terminal(client, session_id)
        response = client.get(f"/research/{session_id}/trace")

    assert response.status_code == 200
    assert response.json() == {
        "session_id": session_id,
        "trace_url": "https://smith.example/r/session-1",
        "metadata": {
            "session_id": session_id,
            "route": "/research/{session_id}/trace",
            "status": "completed",
        },
    }


def test_trace_returns_metadata_for_running_sessions() -> None:
    runner = GateRunner()
    app = create_app(runner=runner, preflight=valid_preflight)

    with TestClient(app) as client:
        session_id = client.post(
            "/research",
            json={"query": "Question"},
        ).json()["session_id"]
        response = client.get(f"/research/{session_id}/trace")

    assert response.status_code == 200
    assert response.json()["trace_url"] is None
    assert response.json()["metadata"] == {
        "session_id": session_id,
        "route": "/research/{session_id}/trace",
        "status": "running",
    }


# --- the additive status fields --------------------------------------------


def test_status_carries_the_artifacts_and_measurements_of_a_finished_run() -> (
    None
):
    """The outcome's own readings reach the wire, not just the report path.

    A finished session's status reply names all three published artifacts,
    the contract version that wrote them, the review status and score, the
    span the events cover, both coverage denominators and the distinct
    evidence counts.
    """
    app = create_app(
        runner=ScriptedRunner(
            state=judged_state(),
            report_path=REPORT_PATH,
        ),
        preflight=valid_preflight,
    )

    with TestClient(app) as client:
        session_id = client.post(
            "/research",
            json={"query": "Question"},
        ).json()["session_id"]
        wait_until_terminal(client, session_id)
        body = client.get(f"/research/{session_id}/status").json()

    assert body["report_path"] == REPORT_PATH
    assert body["evidence_path"] == EVIDENCE_PATH
    assert body["quality_path"] == QUALITY_PATH
    assert body["quality_contract_version"] == QUALITY_CONTRACT_VERSION
    assert body["semantic_review_status"] == "scored"
    assert body["semantic_review_score"] == 0.75
    assert body["duration_seconds"] == 30.0
    assert body["coverage"]["planned_topics"] == 2
    assert body["coverage"]["covered_topics"] == 1
    assert body["coverage"]["unanswered_critical_target_ids"] == ["t2"]
    assert body["evidence_counts"]["findings"] == 1
    assert body["evidence_counts"]["checked_claims"] == 1
    assert body["evidence_counts"]["corroborated"] == 1


def test_status_says_nothing_it_has_not_measured_while_the_session_runs() -> (
    None
):
    """No outcome means no field: every additive value is ``None``, not zero."""
    runner = GateRunner()
    app = create_app(runner=runner, preflight=valid_preflight)

    with TestClient(app) as client:
        session_id = client.post(
            "/research",
            json={"query": "Question"},
        ).json()["session_id"]
        body = client.get(f"/research/{session_id}/status").json()

    assert body["report_path"] is None
    assert body["evidence_path"] is None
    assert body["quality_path"] is None
    assert body["quality_contract_version"] is None
    assert body["semantic_review_status"] is None
    assert body["semantic_review_score"] is None
    assert body["duration_seconds"] is None
    assert body["coverage"] is None
    assert body["evidence_counts"] is None
