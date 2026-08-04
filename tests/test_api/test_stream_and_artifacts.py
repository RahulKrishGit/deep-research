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
from deep_research.utils.types import ResearchEvent
from tests.test_api.fakes import GateRunner, ScriptedRunner
from tests.test_api.test_app import valid_preflight, wait_until_terminal


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
