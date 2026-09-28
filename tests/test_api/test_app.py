"""Tests for the FastAPI start/status routes and API observability.

The app is exercised through Starlette's ``TestClient`` with scripted
runners — no provider, no graph, no network. Every error response is
asserted to be safe: no rejected input values, no secret material, no
exception text.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from deep_research.api.app import create_app
from deep_research.observability import ApiMetric
from deep_research.runtime.errors import configuration_error
from tests.test_api.fakes import GateRunner, ScriptedRunner


def valid_preflight(**kwargs: Any) -> object:
    """Preflight double that never refuses a request."""
    return object()


def wait_until_terminal(
    client: TestClient, session_id: str
) -> dict[str, Any]:
    """Poll the status route until the session reaches a terminal state."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        body = client.get(f"/research/{session_id}/status").json()
        if body["status"] != "running":
            return body
        time.sleep(0.01)
    raise AssertionError("session did not reach a terminal status")


def test_post_starts_a_session_and_forwards_every_request_field() -> None:
    runner = ScriptedRunner()
    app = create_app(runner=runner, preflight=valid_preflight)

    with TestClient(app) as client:
        response = client.post(
            "/research",
            json={
                "query": "Quantum error correction",
                "max_iterations": 2,
                "output_format": "markdown",
                "config_overrides": {
                    "output": {"directory": "api-output/"}
                },
            },
        )

    assert response.status_code == 202
    body = response.json()
    assert body["session_id"]
    assert body["status"] == "running"
    assert body["iteration"] == 0
    call = runner.calls[0]
    assert call["session_id"] == body["session_id"]
    assert call["question"] == "Quantum error correction"
    assert call["max_extra_passes"] == 2
    assert call["output_format"] == "markdown"
    assert call["config_overrides"] == {
        "output": {"directory": "api-output/"}
    }
    metric = app.state.api_tracker.metrics[-1]
    assert isinstance(metric, ApiMetric)
    assert metric.session_id == body["session_id"]
    assert metric.route == "/research"
    assert metric.method == "POST"


def test_post_accepts_zero_extra_passes() -> None:
    """A request may ask for no extra pass at all (PD-15)."""
    runner = ScriptedRunner()
    app = create_app(runner=runner, preflight=valid_preflight)

    with TestClient(app) as client:
        response = client.post(
            "/research", json={"query": "Question", "max_iterations": 0}
        )

    assert response.status_code == 202
    assert runner.calls[0]["max_extra_passes"] == 0


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"query": "   "},
        {"query": "Question", "max_iterations": -1},
        {"query": "Question", "output_format": "pdf"},
        {
            "query": "Question",
            "config_overrides": {"graph": {"iteration_limit": 2}},
        },
    ],
)
def test_invalid_requests_return_safe_422(payload: dict[str, object]) -> None:
    app = create_app(
        runner=ScriptedRunner(),
        preflight=valid_preflight,
    )

    with TestClient(app) as client:
        response = client.post("/research", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "input" not in response.text


def test_missing_configuration_returns_safe_500_and_records_an_event() -> None:
    def failing_preflight(**_kwargs):
        raise configuration_error(
            reason="missing_secrets",
            message="secret value sk-never-return-this",
        )

    app = create_app(
        runner=ScriptedRunner(),
        preflight=failing_preflight,
    )

    with TestClient(app) as client:
        response = client.post(
            "/research",
            json={"query": "Question"},
        )

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "configuration_error",
        "message": "Research service configuration is unavailable.",
        "reason": "missing_secrets",
        "issues": [],
    }
    assert "sk-never-return-this" not in response.text
    error_event = next(
        event
        for event in app.state.api_tracker.events
        if event.event_type == "api.request.error"
    )
    assert error_event.metadata["status_code"] == 500
    assert error_event.metadata["reason"] == "missing_secrets"


def test_status_reports_running_then_completed_lifecycle() -> None:
    runner = GateRunner()
    app = create_app(runner=runner, preflight=valid_preflight)

    with TestClient(app) as client:
        session_id = client.post(
            "/research", json={"query": "Question"}
        ).json()["session_id"]

        running = client.get(f"/research/{session_id}/status")
        assert running.status_code == 200
        body = running.json()
        assert body["session_id"] == session_id
        assert body["status"] == "running"
        assert body["current_agent"] == "planner"
        assert body["iteration"] == 1

        client.portal.call(runner.release.set)
        completed = wait_until_terminal(client, session_id)

    assert completed["status"] == "completed"
    assert completed["session_id"] == session_id
    assert completed["report_path"] == "report-session-1.md"


@pytest.mark.parametrize(
    "path",
    [
        "/research/no-such-session/status",
        "/research/no-such-session/stream",
        "/research/no-such-session/report",
        "/research/no-such-session/trace",
    ],
)
def test_unknown_sessions_return_the_same_safe_404(path: str) -> None:
    app = create_app(
        runner=ScriptedRunner(),
        preflight=valid_preflight,
    )

    with TestClient(app) as client:
        response = client.get(path)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"
    assert "no-such-session" not in response.text


def test_lifespan_shutdown_cancels_running_sessions() -> None:
    runner = GateRunner()
    app = create_app(runner=runner, preflight=valid_preflight)

    with TestClient(app) as client:
        session_id = client.post(
            "/research", json={"query": "Question"}
        ).json()["session_id"]
        session = app.state.session_store.require(session_id)
        assert session.status == "running"

    assert session.task is not None
    assert session.task.cancelled()
    assert session.finished_at is not None


def test_every_session_response_echoes_the_stripped_query() -> None:
    app = create_app(runner=ScriptedRunner(), preflight=valid_preflight)
    with TestClient(app) as client:
        posted = client.post("/research", json={"query": "  How mature is quantum error correction?  "})
        assert posted.status_code == 202
        assert posted.json()["query"] == "How mature is quantum error correction?"
        session_id = posted.json()["session_id"]
        status = client.get(f"/research/{session_id}/status").json()
    assert status["query"] == "How mature is quantum error correction?"


def test_list_returns_sessions_newest_first_with_a_bounded_limit() -> None:
    app = create_app(runner=ScriptedRunner(), preflight=valid_preflight)
    with TestClient(app) as client:
        assert client.get("/research").json() == {"sessions": []}
        ids = [client.post("/research", json={"query": f"Question {n}"}).json()["session_id"] for n in range(3)]
        for session_id in ids:
            wait_until_terminal(client, session_id)
        listed = client.get("/research").json()["sessions"]
        assert [item["session_id"] for item in listed] == list(reversed(ids))
        assert [item["query"] for item in listed] == ["Question 2", "Question 1", "Question 0"]
        for item in listed:
            assert item == client.get(f"/research/{item['session_id']}/status").json()
        assert len(client.get("/research?limit=2").json()["sessions"]) == 2
        assert client.get("/research?limit=0").status_code == 422
        assert client.get("/research?limit=201").status_code == 422


@pytest.mark.parametrize("mode", ["live", "replay"])
def test_every_response_carries_the_mode_header(mode: str) -> None:
    app = create_app(runner=ScriptedRunner(report="# q\n\nbody\n"), preflight=valid_preflight, mode=mode)
    with TestClient(app) as client:
        posted = client.post("/research", json={"query": "Question"})
        assert posted.headers["x-deep-research-mode"] == mode
        session_id = posted.json()["session_id"]
        wait_until_terminal(client, session_id)
        for path, expected in (
            (f"/research/{session_id}/status", 200),
            (f"/research/{session_id}/report", 200),
            (f"/research/{session_id}/stream", 200),
            ("/research/nope/status", 404),
        ):
            response = client.get(path)
            assert response.status_code == expected
            assert response.headers["x-deep-research-mode"] == mode
        invalid = client.post("/research", json={})
        assert invalid.status_code == 422
        assert invalid.headers["x-deep-research-mode"] == mode
    assert create_app(runner=ScriptedRunner(), preflight=valid_preflight).state.mode == "live"
