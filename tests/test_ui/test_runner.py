"""Offline tests for the non-blocking local research controller."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from time import sleep
from typing import Any

import pytest
import yaml

from deep_research.observability import TokenUsage
from deep_research.runtime.errors import (
    ResearchConfigurationError,
    configuration_error,
)
from deep_research.runtime.outcome import ToolCallSummary
from deep_research.ui.history import SessionHistoryStore
from deep_research.ui.runner import LocalResearchController
from deep_research.utils.types import Claim, Finding, ResearchEvent, ScoredSource
from tests.test_ui.fakes import FailingSyncRunner, GatedSyncRunner


def _event(event_type: str, *, metadata: dict[str, Any]) -> ResearchEvent:
    return ResearchEvent(
        event_type=event_type,
        source="tests",
        message="Typed test event.",
        metadata=metadata,
    )


def _config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "graph": {"max_iterations": 4},
                "output": {"directory": str(tmp_path / "output")},
            }
        ),
        encoding="utf-8",
    )
    return path


def _controller(
    tmp_path: Path,
    runner: Any,
    *,
    preflight: Any | None = None,
) -> LocalResearchController:
    return LocalResearchController(
        config_path=str(_config_file(tmp_path)),
        runner=runner,
        preflight=preflight or (lambda **_: object()),
    )


def _wait_for(predicate: Any) -> None:
    for _ in range(100):
        if predicate():
            return
        sleep(0.01)
    raise AssertionError("condition was not reached")


def test_start_returns_running_before_gated_runner_finishes(tmp_path: Path) -> None:
    runner = GatedSyncRunner()
    controller = _controller(tmp_path, runner)

    snapshot = controller.start(question="Question", max_iterations=2)

    assert snapshot.status == "running"
    persisted = controller.history_entry(snapshot.session_id)
    assert persisted is not None
    assert persisted.status == "running"
    assert runner.started.wait(1)
    assert len(runner.calls) == 1
    runner.release.set()
    _wait_for(lambda: controller.snapshot(snapshot.session_id).status == "completed")
    terminal = controller.history_entry(snapshot.session_id)
    assert terminal is not None
    assert terminal.status == "completed"


def test_valid_start_creates_and_starts_exactly_one_worker(tmp_path: Path) -> None:
    runner = GatedSyncRunner()
    created_workers: list[Thread] = []

    def thread_factory(**kwargs: Any) -> Thread:
        worker = Thread(**kwargs)
        created_workers.append(worker)
        return worker

    controller = LocalResearchController(
        config_path=str(_config_file(tmp_path)),
        runner=runner,
        preflight=lambda **_: object(),
        thread_factory=thread_factory,
    )

    snapshot = controller.start(question="Question", max_iterations=2)
    try:
        assert runner.started.wait(1)
        assert len(created_workers) == 1
        assert created_workers[0].is_alive()
        assert len(runner.calls) == 1
        assert runner.calls[0]["session_id"] == snapshot.session_id
    finally:
        runner.release.set()
        for worker in created_workers:
            worker.join(timeout=5)
        assert all(not worker.is_alive() for worker in created_workers)

    _wait_for(lambda: controller.snapshot(snapshot.session_id).status == "completed")


@pytest.mark.parametrize(
    ("status", "report", "report_path"),
    [
        ("failed", None, None),
        ("failed", "# Failed partial report", "reports/failed-partial.md"),
        ("incomplete", None, None),
        ("max_iterations", "# Max-iteration report", "reports/max.md"),
        ("completed", "# Completed report", "reports/completed.md"),
    ],
)
def test_terminal_presentation_metadata_survives_controller_restart(
    tmp_path: Path,
    status: str,
    report: str | None,
    report_path: str | None,
) -> None:
    events = [
        _event("planner.planning.completed", metadata={"sub_topic_count": 5}),
        _event(
            "graph.node.started",
            metadata={"node": "researcher", "iteration": 2},
        ),
        _event(
            "researcher.sub_topic.started",
            metadata={"index": 1, "sub_topic": "Finished topic"},
        ),
        _event(
            "researcher.sub_topic.completed",
            metadata={"index": 1, "sub_topic": "Finished topic", "findings": 2},
        ),
        _event(
            "researcher.sub_topic.started",
            metadata={"index": 2, "sub_topic": "Last active topic"},
        ),
        _event(
            "researcher.tool_call",
            metadata={"tool": "web_search", "success": True},
        ),
        _event(
            "graph.session.completed",
            metadata={"iteration": 2, "status": status},
        ),
    ]
    runner = GatedSyncRunner(
        events=events,
        outcome_kwargs={
            "status": status,
            "iteration": 2,
            "report": report,
            "report_path": report_path,
        },
    )
    store = SessionHistoryStore(output_directory=tmp_path / "output")
    controller = LocalResearchController(
        config_path=str(_config_file(tmp_path)),
        runner=runner,
        preflight=lambda **_: object(),
        history_store=store,
    )

    snapshot = controller.start(question="Question", max_iterations=2)
    assert runner.started.wait(1)
    persisted_live = store.get(snapshot.session_id)
    assert persisted_live is not None
    assert persisted_live.current_agent == "researcher"
    assert persisted_live.last_agent == "researcher"
    assert persisted_live.planned_sub_topic_count == 5
    assert persisted_live.last_sub_topic is not None
    assert persisted_live.last_sub_topic.title == "Last active topic"
    assert [topic.title for topic in persisted_live.sub_topics] == [
        "Finished topic",
        "Last active topic",
    ]
    assert len(persisted_live.recent_activity) <= 3

    if report_path is not None and report is not None:
        report_file = (tmp_path / "output" / report_path).resolve()
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(report, encoding="utf-8")

    runner.release.set()
    _wait_for(lambda: controller.snapshot(snapshot.session_id).status == status)

    restarted = LocalResearchController(
        config_path=str(_config_file(tmp_path)),
        runner=GatedSyncRunner(),
        preflight=lambda **_: object(),
        history_store=store,
    )
    restored = restarted.history_entry(snapshot.session_id)

    assert restored is not None
    assert restored.status == status
    assert restored.current_agent is None
    assert restored.last_agent == "researcher"
    assert restored.planned_sub_topic_count == 5
    assert restored.last_sub_topic is not None
    assert restored.last_sub_topic.title == "Last active topic"
    assert len(restored.recent_activity) <= 3
    if report_path is not None and report is not None:
        assert restarted.read_history_report(restored) == report


def test_multiple_running_sessions_remain_running_in_history(tmp_path: Path) -> None:
    runner = GatedSyncRunner()
    controller = _controller(tmp_path, runner)

    first = controller.start(question="First question", max_iterations=2)
    second = controller.start(question="Second question", max_iterations=2)

    assert runner.started.wait(1)
    entries = {entry.session_id: entry for entry in controller.list_history()}
    assert entries[first.session_id].status == "running"
    assert entries[second.session_id].status == "running"
    assert controller.history_entry(first.session_id).status == "running"
    assert controller.history_entry(second.session_id).status == "running"

    runner.release.set()
    _wait_for(lambda: controller.snapshot(first.session_id).status == "completed")
    _wait_for(lambda: controller.snapshot(second.session_id).status == "completed")


@pytest.mark.parametrize(
    ("config_name", "config_contents", "reason"),
    [
        ("missing.yaml", None, "config_file_missing"),
        ("invalid.yaml", "graph: [", "config_invalid"),
        (
            "invalid-output.yaml",
            yaml.safe_dump({"output": {"directory": 123}}),
            "config_invalid",
        ),
    ],
)
def test_bootstrap_config_failures_are_safe_and_block_start(
    tmp_path: Path,
    config_name: str,
    config_contents: str | None,
    reason: str,
) -> None:
    config_path = tmp_path / config_name
    if config_contents is not None:
        config_path.write_text(config_contents, encoding="utf-8")
    controller = LocalResearchController(
        config_path=str(config_path),
        runner=GatedSyncRunner(),
        preflight=lambda **_: object(),
        history_store=SessionHistoryStore(output_directory=tmp_path / "output"),
    )

    with pytest.raises(ResearchConfigurationError) as raised:
        controller.start(question="Question", max_iterations=2)

    error = raised.value
    assert error.reason == reason
    assert str(error) == "Research service configuration is unavailable."
    assert "graph: [" not in str(error)
    assert "config.yaml" not in str(error)
    assert controller.list_history() == []


def test_initial_history_failure_blocks_start_with_safe_actionable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SessionHistoryStore(output_directory=tmp_path / "output")
    secret = "private history payload"

    def fail_upsert(_: Any) -> None:
        raise OSError(secret)

    monkeypatch.setattr(store, "upsert", fail_upsert)
    controller = LocalResearchController(
        config_path=str(_config_file(tmp_path)),
        runner=GatedSyncRunner(),
        preflight=lambda **_: object(),
        history_store=store,
    )

    with pytest.raises(ResearchConfigurationError) as raised:
        controller.start(question="Question", max_iterations=2)

    error = raised.value
    assert error.reason == "history_unavailable"
    assert str(error) == (
        "Research session could not be started because local history is unavailable."
    )
    assert secret not in str(error)
    assert controller.list_history() == []


def test_strict_preflight_happens_before_session_registration(tmp_path: Path) -> None:
    runner = GatedSyncRunner()
    observed: list[bool] = []

    def preflight(**_: Any) -> object:
        observed.append(controller.history_entry("a" * 32) is not None)
        return object()

    controller = _controller(tmp_path, runner, preflight=preflight)
    snapshot = controller.start(question="Question", max_iterations=2)

    assert observed == [False]
    runner.release.set()
    _wait_for(lambda: controller.snapshot(snapshot.session_id).status == "completed")


def test_published_events_project_live_progress_and_snapshots_are_copies(
    tmp_path: Path,
) -> None:
    events = [
        _event(
            "graph.node.started",
            metadata={"node": "researcher", "iteration": 2},
        ),
        _event(
            "researcher.sub_topic.started",
            metadata={"index": 1, "sub_topic": "Market size", "priority": 1},
        ),
        _event(
            "researcher.tool_call",
            metadata={"tool": "web_search", "success": True},
        ),
    ]
    runner = GatedSyncRunner(events=events)
    controller = _controller(tmp_path, runner)

    first = controller.start(question="Question", max_iterations=2)
    assert runner.started.wait(1)
    live = controller.snapshot(first.session_id)
    assert live.current_agent == "researcher"
    assert live.iteration == 2
    assert live.sub_topics[0].title == "Market size"
    assert live.recent_activity[-1].summary == (
        "Evaluated new evidence for the research question"
    )

    live.sub_topics[0].title = "mutated"
    second = controller.snapshot(first.session_id)
    assert second.sub_topics[0].title == "Market size"
    runner.release.set()
    _wait_for(lambda: controller.snapshot(first.session_id).status == "completed")


def test_live_snapshot_projects_recoverable_graph_issue_count(
    tmp_path: Path,
) -> None:
    runner = GatedSyncRunner(
        events=[
            _event(
                "graph.node.completed",
                metadata={"node": "researcher", "iteration": 1, "error_count": 1},
            )
        ]
    )
    controller = _controller(tmp_path, runner)

    snapshot = controller.start(question="Question", max_iterations=2)
    assert runner.started.wait(1)
    assert controller.snapshot(snapshot.session_id).issue_count == 1
    runner.release.set()
    _wait_for(lambda: controller.snapshot(snapshot.session_id).status == "completed")


def test_terminal_snapshot_projects_recoverable_graph_issue_count(
    tmp_path: Path,
) -> None:
    event = _event(
        "graph.node.completed",
        metadata={"node": "researcher", "iteration": 1, "error_count": 1},
    )
    runner = GatedSyncRunner(events=[event])
    controller = _controller(tmp_path, runner)

    snapshot = controller.start(question="Question", max_iterations=2)
    assert runner.started.wait(1)
    runner.release.set()
    _wait_for(lambda: controller.snapshot(snapshot.session_id).status == "completed")

    assert controller.snapshot(snapshot.session_id).issue_count == 1


def test_live_event_path_separates_planned_total_from_actual_subtopics(
    tmp_path: Path,
) -> None:
    events = [
        _event(
            "planner.planning.completed",
            metadata={"sub_topic_count": 5},
        ),
        _event(
            "graph.node.started",
            metadata={"node": "researcher", "iteration": 2},
        ),
        _event(
            "researcher.sub_topic.started",
            metadata={"index": 2, "sub_topic": "Grid adoption"},
        ),
    ]
    runner = GatedSyncRunner(events=events)
    controller = _controller(tmp_path, runner)

    snapshot = controller.start(question="Question", max_iterations=2)
    assert runner.started.wait(1)
    live = controller.snapshot(snapshot.session_id)

    assert live.current_agent == "researcher"
    assert live.planned_sub_topic_count == 5
    assert len(live.sub_topics) == 1
    assert live.sub_topics[0].title == "Grid adoption"
    assert live.sub_topics[0].status == "running"
    assert all("Queued subtopic" not in topic.title for topic in live.sub_topics)
    runner.release.set()
    _wait_for(lambda: controller.snapshot(snapshot.session_id).status == "completed")


def test_later_agent_transition_clears_unvisited_research_rows(
    tmp_path: Path,
) -> None:
    events = [
        _event(
            "planner.planning.completed",
            metadata={"sub_topic_count": 5},
        ),
        _event(
            "graph.node.started",
            metadata={"node": "researcher", "iteration": 1},
        ),
        _event(
            "researcher.sub_topic.started",
            metadata={"index": 1, "sub_topic": "Grid adoption"},
        ),
        _event(
            "graph.node.completed",
            metadata={"node": "researcher", "iteration": 1},
        ),
        _event(
            "graph.node.started",
            metadata={"node": "source_evaluator", "iteration": 1},
        ),
    ]
    runner = GatedSyncRunner(events=events)
    controller = _controller(tmp_path, runner)

    snapshot = controller.start(question="Question", max_iterations=2)
    assert runner.started.wait(1)
    live = controller.snapshot(snapshot.session_id)

    assert live.current_agent == "source_evaluator"
    assert live.planned_sub_topic_count == 5
    assert live.sub_topics == []
    assert live.research_phase_complete is True
    runner.release.set()
    _wait_for(lambda: controller.snapshot(snapshot.session_id).status == "completed")


def test_failed_event_path_preserves_last_agent_and_recoverable_tool_issue(
    tmp_path: Path,
) -> None:
    events = [
        _event(
            "planner.planning.completed",
            metadata={"sub_topic_count": 5},
        ),
        _event(
            "graph.node.started",
            metadata={"node": "researcher", "iteration": 2},
        ),
        _event(
            "researcher.sub_topic.started",
            metadata={"index": 2, "sub_topic": "Grid adoption"},
        ),
        _event(
            "researcher.tool_call",
            metadata={
                "tool": "web_search",
                "success": False,
                "provider_payload": "must not render",
            },
        ),
    ]
    runner = FailingSyncRunner(RuntimeError("private provider failure"), events=events)
    controller = _controller(tmp_path, runner)

    snapshot = controller.start(question="Question", max_iterations=2)
    _wait_for(lambda: controller.snapshot(snapshot.session_id).status == "failed")
    failed = controller.snapshot(snapshot.session_id)

    assert failed.current_agent == "researcher"
    assert failed.planned_sub_topic_count == 5
    assert len(failed.sub_topics) == 1
    assert failed.sub_topics[0].title == "Grid adoption"
    assert failed.tool_calls[0].failures == 1
    assert "private provider failure" not in failed.model_dump_json()
    assert "provider_payload" not in failed.model_dump_json()


def test_terminal_snapshot_uses_authoritative_outcome_quality_fields(
    tmp_path: Path,
) -> None:
    source = ScoredSource(
        url="https://example.org/source",
        title="Typed source",
        authority_score=0.9,
        recency_score=0.9,
        relevance_score=0.9,
        corroboration_score=0.8,
        overall_score=0.9,
        rationale="Typed source rationale.",
    )
    finding = Finding(
        content="Typed finding.",
        source_url=source.url,
        source_title=source.title,
        extracted_at=datetime.now(timezone.utc).isoformat(),
        confidence=0.9,
        related_sub_topic="Typed topic",
    )
    claim = Claim(
        text="Typed verified claim.",
        source_urls=[source.url],
        verdict="verified",
        confidence=0.9,
        evidence=["Typed evidence."],
        contradictions=[],
    )
    runner = GatedSyncRunner(
        outcome_kwargs={
            "iteration": 2,
            "report": "authoritative report",
            "raw_findings": (finding,),
            "evaluated_sources": (source,),
            "verified_claims": (claim,),
            "token_usage": TokenUsage(input_tokens=12, output_tokens=8),
            "tool_calls": (
                ToolCallSummary(tool_name="web_search", calls=4, failures=1),
            ),
        }
    )
    controller = _controller(tmp_path, runner)
    snapshot = controller.start(question="Question", max_iterations=2)
    assert runner.started.wait(1)
    runner.release.set()
    _wait_for(lambda: controller.snapshot(snapshot.session_id).status == "completed")

    finished = controller.snapshot(snapshot.session_id)
    assert finished.iteration == 2
    assert finished.report == "authoritative report"
    assert finished.token_usage is not None
    assert finished.token_usage.model_dump() == {"input_tokens": 12, "output_tokens": 8}
    assert finished.tool_calls[0].calls == 4
    assert finished.source_summary.total == 1
    assert finished.source_summary.details[0].related_sub_topics == [
        "Typed topic"
    ]
    assert finished.fact_check_summary.verified == 1
    assert finished.fact_check_summary.details[0].text == "Typed verified claim."


def test_zero_token_outcome_publishes_none_token_usage(tmp_path: Path) -> None:
    runner = GatedSyncRunner(
        outcome_kwargs={"token_usage": TokenUsage(input_tokens=0, output_tokens=0)}
    )
    controller = _controller(tmp_path, runner)
    snapshot = controller.start(question="Question", max_iterations=2)
    assert runner.started.wait(1)
    runner.release.set()
    _wait_for(lambda: controller.snapshot(snapshot.session_id).status == "completed")

    assert controller.snapshot(snapshot.session_id).token_usage is None


def test_unexpected_failure_uses_safe_project_owned_text(tmp_path: Path) -> None:
    secret = "provider secret exception text"
    runner = FailingSyncRunner(RuntimeError(secret))
    controller = _controller(tmp_path, runner)

    snapshot = controller.start(question="Question", max_iterations=2)
    _wait_for(lambda: controller.snapshot(snapshot.session_id).status == "failed")
    finished = controller.snapshot(snapshot.session_id)
    history = controller.history_entry(snapshot.session_id)

    assert secret not in finished.model_dump_json()
    assert history is not None
    assert secret not in history.model_dump_json()
    assert finished.errors[0].message == "Research run failed unexpectedly."


def test_late_configuration_failure_stores_only_reason_and_hint(tmp_path: Path) -> None:
    runner = FailingSyncRunner(
        configuration_error(
            reason="missing_secrets",
            message="private configuration details",
        )
    )
    controller = _controller(tmp_path, runner)

    snapshot = controller.start(question="Question", max_iterations=2)
    _wait_for(lambda: controller.snapshot(snapshot.session_id).status == "failed")
    finished = controller.snapshot(snapshot.session_id)
    history = controller.history_entry(snapshot.session_id)

    assert history is not None
    assert "private configuration details" not in finished.model_dump_json()
    assert "private configuration details" not in history.model_dump_json()
    assert finished.errors[0].details["reason"] == "missing_secrets"
    assert finished.errors[0].details["hint"] == (
        "Set the selected chat provider's API key (DEEPSEEK_API_KEY by default) "
        "and TAVILY_API_KEY in the environment or in a .env file next to "
        "config.yaml. OPENAI_API_KEY is required only when a provider or "
        "embedding_provider of 'openai' is configured."
    )


def test_terminal_history_write_failure_does_not_leave_outcome_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = GatedSyncRunner(
        outcome_kwargs={"report": "authoritative report"}
    )
    store = SessionHistoryStore(output_directory=tmp_path / "output")
    original_upsert = store.upsert
    calls = 0
    secret = "private history write failure"

    def fail_terminal_upsert(entry: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(secret)
        original_upsert(entry)

    monkeypatch.setattr(store, "upsert", fail_terminal_upsert)
    controller = LocalResearchController(
        config_path=str(_config_file(tmp_path)),
        runner=runner,
        preflight=lambda **_: object(),
        history_store=store,
    )

    snapshot = controller.start(question="Question", max_iterations=2)
    assert runner.started.wait(1)
    runner.release.set()
    _wait_for(lambda: controller.snapshot(snapshot.session_id).status == "completed")

    finished = controller.snapshot(snapshot.session_id)
    assert finished.status == "completed"
    assert finished.report == "authoritative report"
    assert secret not in finished.model_dump_json()
    persisted = controller.history_entry(snapshot.session_id)
    assert persisted is not None
    assert persisted.status == "completed"
    listed = next(
        entry
        for entry in controller.list_history()
        if entry.session_id == snapshot.session_id
    )
    assert listed.status == "completed"
    assert not controller.is_session_active(snapshot.session_id)


def test_worker_start_failure_transitions_session_to_safe_failed_state(
    tmp_path: Path,
) -> None:
    secret = "private worker construction failure"

    def failing_thread_factory(**_: Any) -> Any:
        raise RuntimeError(secret)

    controller = LocalResearchController(
        config_path=str(_config_file(tmp_path)),
        runner=GatedSyncRunner(),
        preflight=lambda **_: object(),
        history_store=SessionHistoryStore(output_directory=tmp_path / "output"),
        thread_factory=failing_thread_factory,
    )

    with pytest.raises(RuntimeError, match=secret):
        controller.start(question="Question", max_iterations=2)

    entries = controller.list_history()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.status == "failed"
    assert not controller.is_session_active(entry.session_id)
    assert controller.snapshot(entry.session_id).status == "failed"
    assert secret not in entry.model_dump_json()


def test_worker_start_method_failure_transitions_session_to_safe_failed_state(
    tmp_path: Path,
) -> None:
    secret = "private worker start failure"

    class StartFailingWorker:
        def start(self) -> None:
            raise RuntimeError(secret)

    controller = LocalResearchController(
        config_path=str(_config_file(tmp_path)),
        runner=GatedSyncRunner(),
        preflight=lambda **_: object(),
        history_store=SessionHistoryStore(output_directory=tmp_path / "output"),
        thread_factory=lambda **_: StartFailingWorker(),
    )

    with pytest.raises(RuntimeError, match=secret):
        controller.start(question="Question", max_iterations=2)

    entries = controller.list_history()
    assert len(entries) == 1
    assert entries[0].status == "failed"
    assert not controller.is_session_active(entries[0].session_id)


def test_unknown_configuration_failure_details_are_allowlisted(
    tmp_path: Path,
) -> None:
    secret_reason = "private reason"
    secret_hint = "private hint"
    secret_message = "private configuration message"
    runner = FailingSyncRunner(
        ResearchConfigurationError(
            secret_message,
            reason=secret_reason,
            hint=secret_hint,
        )
    )
    controller = _controller(tmp_path, runner)

    snapshot = controller.start(question="Question", max_iterations=2)
    _wait_for(lambda: controller.snapshot(snapshot.session_id).status == "failed")

    finished = controller.snapshot(snapshot.session_id)
    history = controller.history_entry(snapshot.session_id)
    assert history is not None
    rendered = finished.model_dump_json() + history.model_dump_json()
    assert secret_reason not in rendered
    assert secret_hint not in rendered
    assert secret_message not in rendered
    assert finished.errors[0].details == {
        "reason": "configuration_error",
        "hint": "Review the research configuration and try again.",
    }


def test_history_running_entries_are_incomplete_when_not_active(tmp_path: Path) -> None:
    store = SessionHistoryStore(output_directory=tmp_path / "output")
    from deep_research.ui.models import (
        UiFactCheckSummary,
        UiSessionSnapshot,
        UiSourceSummary,
        history_entry_from_snapshot,
    )

    persisted = history_entry_from_snapshot(
        UiSessionSnapshot(
            session_id="b" * 32,
            question="Question",
            status="running",
            started_at=datetime.now(timezone.utc),
            iteration=0,
            max_iterations=2,
            source_summary=UiSourceSummary(
                total=0, high=0, moderate=0, low=0, unrated=0
            ),
            fact_check_summary=UiFactCheckSummary(
                verified=0,
                unverified=0,
                contradicted=0,
                insufficient_evidence=0,
            ),
        )
    )
    store.upsert(persisted)
    restored = LocalResearchController(
        config_path=str(_config_file(tmp_path)),
        runner=GatedSyncRunner(),
        preflight=lambda **_: object(),
        history_store=store,
    ).history_entry("b" * 32)
    assert restored is not None
    assert restored.status == "incomplete"
