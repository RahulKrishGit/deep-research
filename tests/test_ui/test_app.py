"""Offline AppTests for the persistent Streamlit shell and router."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import sleep

import pytest
import yaml
from streamlit.testing.v1 import AppTest

from deep_research.runtime.errors import configuration_error
from deep_research.ui import components
from deep_research.ui.app import (
    _ACTIVE_SESSION_KEY,
    _CONTROLLER_KEY,
    _HISTORY_FILTER_KEY,
    _HISTORY_SEARCH_KEY,
    _SELECTED_SESSION_KEY,
    _START_ERROR_KEY,
    _START_IN_FLIGHT_KEY,
    _VIEW_KEY,
    render_app,
)
from deep_research.ui.components import (
    _start_research,
    execution_error_presentation,
)
from deep_research.ui.history import SessionHistoryStore
from deep_research.ui.models import (
    SessionHistoryEntry,
    UiClaimDetail,
    UiFactCheckSummary,
    UiRecentActivity,
    UiSessionSnapshot,
    UiSourceDetail,
    UiSourceSummary,
    UiSubTopicProgress,
    UiTokenUsage,
    UiToolCallSummary,
)
from deep_research.ui.progress import project_progress
from deep_research.ui.runner import LocalResearchController
from deep_research.ui.styles import STATIC_CSS
from deep_research.utils.types import ResearchEvent
from tests.test_ui.fakes import DemoController, FailingSyncRunner, GatedSyncRunner

_LIVE_SESSION_STATE_KEY = "_deep_research_live_session_id"
_PENDING_START_STATE_KEY = "_deep_research_pending_start_request"


def _entry(
    session_id: str,
    question: str,
    status: str,
    offset: int,
) -> SessionHistoryEntry:
    return SessionHistoryEntry(
        session_id=session_id,
        question=question,
        status=status,
        started_at=datetime(2026, 9, 9, tzinfo=timezone.utc)
        - timedelta(minutes=offset),
        iteration=1,
        max_iterations=4,
        source_summary=UiSourceSummary(
            total=0,
            high=0,
            moderate=0,
            low=0,
            unrated=0,
        ),
        fact_check_summary=UiFactCheckSummary(
            verified=0,
            unverified=0,
            contradicted=0,
            insufficient_evidence=0,
        ),
    )


class FakeController:
    def __init__(
        self,
        entries: list[SessionHistoryEntry],
        *,
        start_error: Exception | None = None,
    ) -> None:
        self.entries = entries
        self.start_error = start_error
        self.start_calls: list[dict[str, object]] = []
        self.started_snapshots: dict[str, UiSessionSnapshot] = {}
        self.history_reports: dict[str, str] = {}

    @property
    def default_max_iterations(self) -> int:
        return 4

    def start(
        self,
        *,
        question: str,
        max_iterations: int,
        output_format: str = "markdown",
    ) -> UiSessionSnapshot:
        self.start_calls.append(
            {
                "question": question,
                "max_iterations": max_iterations,
                "output_format": output_format,
            }
        )
        if self.start_error is not None:
            raise self.start_error
        session_id = "r" * 32
        snapshot = UiSessionSnapshot(
            session_id=session_id,
            question=question,
            status="running",
            started_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
            iteration=0,
            max_iterations=max_iterations,
            source_summary=UiSourceSummary(
                total=0,
                high=0,
                moderate=0,
                low=0,
                unrated=0,
            ),
            fact_check_summary=UiFactCheckSummary(
                verified=0,
                unverified=0,
                contradicted=0,
                insufficient_evidence=0,
            ),
        )
        self.started_snapshots[session_id] = snapshot
        return snapshot

    def list_history(self, *, limit: int | None = None) -> list[SessionHistoryEntry]:
        return self.entries if limit is None else self.entries[:limit]

    def history_entry(self, session_id: str) -> SessionHistoryEntry | None:
        return next(
            (entry for entry in self.entries if entry.session_id == session_id),
            None,
        )

    def snapshot(self, session_id: str) -> UiSessionSnapshot:
        if session_id in self.started_snapshots:
            return self.started_snapshots[session_id]
        entry = self.history_entry(session_id)
        assert entry is not None
        return UiSessionSnapshot(
            session_id=entry.session_id,
            question=entry.question,
            status=entry.status,
            started_at=entry.started_at,
            finished_at=entry.finished_at,
            iteration=entry.iteration,
            max_iterations=entry.max_iterations,
            source_summary=entry.source_summary,
            fact_check_summary=entry.fact_check_summary,
        )

    def read_history_report(self, entry: SessionHistoryEntry) -> str | None:
        return self.history_reports.get(entry.session_id)


class MultiActiveFakeController(FakeController):
    def __init__(
        self,
        entries: list[SessionHistoryEntry],
        active_session_ids: set[str],
    ) -> None:
        super().__init__(entries)
        self.active_session_ids = active_session_ids

    def is_session_active(self, session_id: str) -> bool:
        return session_id in self.active_session_ids


class RestartedFakeController(FakeController):
    def snapshot(self, session_id: str) -> UiSessionSnapshot:
        raise KeyError(session_id)


def _app(
    entries: list[SessionHistoryEntry],
    *,
    start_error: Exception | None = None,
) -> AppTest:
    return AppTest.from_function(
        render_app,
        kwargs={
            "controller": FakeController(entries, start_error=start_error),
        },
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


def _button_values(app: AppTest) -> list[str]:
    return [button.label for button in app.button]


def _snapshot(
    *,
    status: str = "running",
    current_agent: str | None = "researcher",
    token_usage: UiTokenUsage | None = None,
    trace_url: str | None = None,
    sub_topics: list[UiSubTopicProgress] | None = None,
    planned_sub_topic_count: int | None = None,
    recent_activity: list[UiRecentActivity] | None = None,
    errors: list[object] | None = None,
    tool_calls: list[UiToolCallSummary] | None = None,
    report: str | None = None,
    report_path: str | None = None,
    issue_count: int = 0,
) -> UiSessionSnapshot:
    from deep_research.utils.types import ResearchError

    actual_sub_topics = sub_topics or []
    return UiSessionSnapshot(
        session_id="s" * 32,
        question="How will grid-scale batteries reshape energy markets by 2030?",
        status=status,
        started_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
        finished_at=(
            datetime(2026, 9, 9, 0, 10, tzinfo=timezone.utc)
            if status != "running"
            else None
        ),
        current_agent=current_agent,
        iteration=2,
        max_iterations=4,
        planned_sub_topic_count=(
            len(actual_sub_topics)
            if planned_sub_topic_count is None
            else planned_sub_topic_count
        ),
        sub_topics=actual_sub_topics,
        recent_activity=recent_activity or [],
        tool_calls=tool_calls
        or [
            UiToolCallSummary(
                tool_name="web_search",
                display_label="Web search",
                calls=2,
                failures=0,
            )
        ],
        token_usage=token_usage,
        trace_url=trace_url,
        report_path=report_path,
        report=report,
        source_summary=UiSourceSummary(
            total=0,
            high=0,
            moderate=0,
            low=0,
            unrated=0,
        ),
        fact_check_summary=UiFactCheckSummary(
            verified=0,
            unverified=0,
            contradicted=0,
            insufficient_evidence=0,
        ),
        errors=[ResearchError.model_validate(error) for error in (errors or [])],
        issue_count=issue_count,
    )


def _running_app(snapshot: UiSessionSnapshot) -> AppTest:
    app = _app(
        [_entry(snapshot.session_id, snapshot.question, snapshot.status, 0)]
    ).run()
    controller = app.session_state[_CONTROLLER_KEY]
    controller.started_snapshots[snapshot.session_id] = snapshot
    if snapshot.status == "running":
        app.session_state[_ACTIVE_SESSION_KEY] = snapshot.session_id
    app.session_state[_SELECTED_SESSION_KEY] = snapshot.session_id
    app.session_state[_VIEW_KEY] = "current"
    app.run()
    return app


def _completed_report_snapshot(
    *,
    status: str = "completed",
    token_usage: UiTokenUsage | None = UiTokenUsage(
        input_tokens=18_000,
        output_tokens=6_800,
    ),
    trace_url: str | None = "https://smith.langchain.com/o/example/r/report",
    report: str | None = (
        "# Research answer\n\n"
        "The report answer leads with the research question.\n\n"
        "## Cost trajectory\n\n"
        "- Evidence-backed finding [1].\n\n"
        "| Scenario | Capacity |\n| --- | --- |\n| Base | 1,020 GW |"
    ),
    limitations: list[str] | None = None,
    errors: list[object] | None = None,
) -> UiSessionSnapshot:
    snapshot = _snapshot(
        status=status,
        token_usage=token_usage,
        trace_url=trace_url,
        report=report,
        errors=errors,
    )
    return snapshot.model_copy(
        update={
            "report_path": "reports/grid-scale-batteries-2030.md",
            "limitations": limitations or [],
            "source_summary": UiSourceSummary(
                total=4,
                high=1,
                moderate=1,
                low=1,
                unrated=1,
                details=[
                    UiSourceDetail(
                        title="Grid Storage Outlook",
                        url="https://example.com/grid-storage",
                        tier="high",
                        overall_score=0.91,
                        rationale=(
                            "Primary market dataset with transparent methodology."
                        ),
                        corroboration_score=0.88,
                        related_sub_topics=["Battery cost curves"],
                    ),
                    UiSourceDetail(
                        title="Policy filing",
                        url="https://example.com/policy",
                        tier="moderate",
                        overall_score=0.64,
                        rationale="Useful regulatory context with narrower coverage.",
                        corroboration_score=0.55,
                        related_sub_topics=["Grid operator adoption"],
                    ),
                    UiSourceDetail(
                        title="Industry analysis",
                        url="https://example.com/industry",
                        tier="low",
                        overall_score=0.42,
                        rationale="Directional estimate requiring corroboration.",
                        corroboration_score=0.31,
                        related_sub_topics=["Market structure impacts"],
                    ),
                    UiSourceDetail(
                        title="Unrated source",
                        url="https://example.com/unrated",
                        tier="unrated",
                        overall_score=0.0,
                        rationale="The source could not be scored reliably.",
                        corroboration_score=0.12,
                        related_sub_topics=[],
                    ),
                ],
            ),
            "fact_check_summary": UiFactCheckSummary(
                verified=1,
                unverified=1,
                contradicted=1,
                insufficient_evidence=1,
                details=[
                    UiClaimDetail(
                        text="Deployment expands substantially.",
                        verdict="verified",
                        confidence=0.94,
                        source_urls=["https://example.com/grid-storage"],
                        evidence=["Capacity data supports the estimate."],
                    ),
                    UiClaimDetail(
                        text="Financing costs fall every year.",
                        verdict="unverified",
                        confidence=0.51,
                        source_urls=["https://example.com/industry"],
                        evidence=["The available series is incomplete."],
                    ),
                    UiClaimDetail(
                        text="Interconnection is never a constraint.",
                        verdict="contradicted",
                        confidence=0.89,
                        source_urls=["https://example.com/policy"],
                        evidence=["Regional filings report queue delays."],
                        contradictions=["The claim conflicts with operator filings."],
                    ),
                    UiClaimDetail(
                        text="Sodium-ion reaches mass scale by 2028.",
                        verdict="insufficient_evidence",
                        confidence=0.29,
                        source_urls=[],
                        evidence=["Only pilot-scale data was available."],
                    ),
                ],
            ),
        }
    )


def _visible_main_text(app: AppTest) -> str:
    return "\n".join(
        [item.value for item in app.main.markdown if "<style>" not in item.value]
        + [item.value for item in app.main.caption]
        + [item.value for item in app.main.error]
        + [item.value for item in app.main.warning]
    )


def test_new_research_screen_has_question_form_and_ready_state() -> None:
    app = _app([]).run()

    assert app.container(key="dr-new-research-column") is not None
    assert app.text_area(key="research_question").label == "Research question"
    assert app.number_input(key="max_iterations").label == "Maximum iterations"
    assert any("Markdown" in item.value for item in app.main.markdown)
    assert any("Start Research" in value for value in _button_values(app))
    assert any("Ready to start" in item.value for item in app.main.markdown)
    assert not any("Session history" in item.value for item in app.main.markdown)
    assert not any("Current session" in item.value for item in app.main.markdown)


def test_new_research_screen_accepts_configured_default_above_twenty() -> None:
    class HighIterationController(FakeController):
        @property
        def default_max_iterations(self) -> int:
            return 25

    app = AppTest.from_function(
        render_app,
        kwargs={"controller": HighIterationController([])},
    ).run()

    assert app.number_input(key="max_iterations").value == 25


def test_start_research_is_the_only_filled_primary_action() -> None:
    app = _app([]).run()

    assert app.sidebar.button(key="new_research").proto.type == "secondary"
    assert app.button(key="start_research").proto.type == "primary"


def test_new_research_blank_question_disables_submit_and_preserves_draft_values(
) -> None:
    app = _app([]).run()

    app.text_area(key="research_question").set_value("   ").run()
    app.number_input(key="max_iterations").set_value(6).run()

    assert app.text_area(key="research_question").value == "   "
    assert app.number_input(key="max_iterations").value == 6
    assert app.button(key="start_research").disabled is True
    assert app.session_state[_CONTROLLER_KEY].start_calls == []


def test_valid_question_can_submit_and_forwards_markdown_configuration() -> None:
    app = _app([]).run()

    app.text_area(key="research_question").set_value(
        "How will grid-scale batteries reshape energy markets by 2030?"
    ).run()
    app.number_input(key="max_iterations").set_value(6).run()
    app.button(key="start_research").click().run()

    controller = app.session_state[_CONTROLLER_KEY]
    assert len(controller.start_calls) == 1
    assert controller.start_calls[0] == {
        "question": "How will grid-scale batteries reshape energy markets by 2030?",
        "max_iterations": 6,
        "output_format": "markdown",
    }
    assert app.session_state[_PENDING_START_STATE_KEY] is None


def test_configuration_error_preserves_draft_values() -> None:
    app = _app(
        [],
        start_error=configuration_error(
            reason="missing_secrets",
            message="secret=TOP-SECRET-CONFIGURATION-DIAGNOSTIC",
        ),
    ).run()

    app.text_area(key="research_question").set_value("Keep this question").run()
    app.number_input(key="max_iterations").set_value(7).run()
    app.button(key="start_research").click().run()

    assert app.session_state[_VIEW_KEY] == "new"
    assert app.text_area(key="research_question").value == "Keep this question"
    assert app.number_input(key="max_iterations").value == 7


def test_configuration_error_renders_only_safe_project_message() -> None:
    sensitive_message = "provider-key=TOP-SECRET-CONFIGURATION-DIAGNOSTIC"
    app = _app(
        [],
        start_error=configuration_error(
            reason="missing_secrets",
            message=sensitive_message,
        ),
    ).run()

    app.text_area(key="research_question").set_value("A valid question").run()
    app.button(key="start_research").click().run()

    visible_text = "\n".join(
        [item.value for item in app.main.markdown]
        + [item.value for item in app.main.error]
        + [item.value for item in app.main.caption]
    )
    assert "Research service configuration is unavailable." in visible_text
    assert "Set the selected chat provider's API key" in visible_text
    assert sensitive_message not in visible_text


@pytest.mark.parametrize(
    ("start_error", "safe_message", "safe_hint", "raw_error"),
    [
        (
            configuration_error(
                reason="missing_secrets",
                message="secret=TOP-SECRET-PENDING-CONFIGURATION",
            ),
            "Research service configuration is unavailable.",
            "Set the selected chat provider's API key",
            "secret=TOP-SECRET-PENDING-CONFIGURATION",
        ),
        (
            configuration_error(
                reason="history_unavailable",
                message="history-path=TOP-SECRET-PENDING-HISTORY",
            ),
            "Research session could not be started because "
            "local history is unavailable.",
            "Check that the local output directory is writable and try again.",
            "history-path=TOP-SECRET-PENDING-HISTORY",
        ),
        (
            RuntimeError("TOP-SECRET-PENDING-START-FAILURE"),
            "Research session could not be started.",
            "Check the local output directory and try again.",
            "TOP-SECRET-PENDING-START-FAILURE",
        ),
    ],
    ids=["configuration", "history", "unexpected"],
)
def test_failed_pending_start_rebuilds_an_enabled_form(
    start_error: Exception,
    safe_message: str,
    safe_hint: str,
    raw_error: str,
) -> None:
    app = _app([], start_error=start_error).run()

    app.text_area(key="research_question").set_value("Keep this question").run()
    app.number_input(key="max_iterations").set_value(7).run()
    app.button(key="start_research").click().run()

    controller = app.session_state[_CONTROLLER_KEY]
    visible_text = _visible_main_text(app)
    assert app.text_area(key="research_question").disabled is False
    assert app.number_input(key="max_iterations").disabled is False
    assert app.button(key="start_research").disabled is False
    assert app.session_state[_START_IN_FLIGHT_KEY] is False
    assert app.session_state[_PENDING_START_STATE_KEY] is None
    assert len(controller.start_calls) == 1
    assert safe_message in visible_text
    assert safe_hint in visible_text
    assert raw_error not in visible_text
    assert app.text_area(key="research_question").value == "Keep this question"
    assert app.number_input(key="max_iterations").value == 7


def test_unexpected_start_error_surfaces_without_form_guidance() -> None:
    controller = FakeController(
        [],
        start_error=RuntimeError("unexpected persistence failure"),
    )
    state: dict[str, object] = {_START_ERROR_KEY: None}

    _start_research(
        controller,
        question="A valid question",
        max_iterations=4,
        state=state,
    )

    assert state[_START_ERROR_KEY] == (
        "Research session could not be started.",
        "Check the local output directory and try again.",
    )


def test_start_submission_persists_pending_request_before_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state: dict[str, object] = {}
    rerun_calls: list[bool] = []
    monkeypatch.setattr(components.st, "rerun", lambda: rerun_calls.append(True))

    components._queue_research_start(
        question="  A valid question  ",
        max_iterations=7,
        state=state,
    )

    assert state[_START_IN_FLIGHT_KEY] is True
    assert state[_PENDING_START_STATE_KEY] == {
        "question": "A valid question",
        "max_iterations": 7,
    }
    assert rerun_calls == [True]


def test_streamlit_container_uses_the_declared_keyed_container_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def current_container(
        *,
        key: str | None = None,
        gap: str | None = None,
        border: bool | None = None,
    ) -> object:
        captured["key"] = key
        captured["gap"] = gap
        captured["border"] = border
        return sentinel

    monkeypatch.setattr(components.st, "container", current_container)

    result = components._st_container(
        key="newer-key",
        gap="small",
        border=True,
    )

    assert result is sentinel
    assert captured == {"key": "newer-key", "gap": "small", "border": True}


def test_streamlit_dependency_floor_matches_keyed_container_contract() -> None:
    project_file = Path(__file__).parents[2] / "pyproject.toml"

    assert '"streamlit>=1.49"' in project_file.read_text(encoding="utf-8")


def test_plan_streamlit_floor_matches_keyed_container_contract() -> None:
    plan_file = (
        Path(__file__).parents[2]
        / "docs/superpowers/plans/2026-09-08-streamlit-ui.md"
    )
    plan_text = plan_file.read_text(encoding="utf-8")

    assert "streamlit>=1.37" not in plan_text
    assert "Streamlit `>=1.49`" in plan_text
    assert plan_text.count("streamlit>=1.49") >= 2
    assert "keyed `st.container` contract" in plan_text


def test_invalid_controller_bootstrap_keeps_new_form_and_safe_error(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "invalid-config.yaml"
    config_path.write_text("graph: [", encoding="utf-8")
    controller = LocalResearchController(
        config_path=str(config_path),
        history_store=SessionHistoryStore(output_directory=tmp_path / "output"),
    )
    app = AppTest.from_function(
        render_app,
        kwargs={"controller": controller},
    ).run()

    app.text_area(key="research_question").set_value("A valid question").run()
    app.button(key="start_research").click().run()

    visible_text = "\n".join(
        [item.value for item in app.main.markdown]
        + [item.value for item in app.main.error]
        + [item.value for item in app.main.caption]
    )
    assert "Research service configuration is unavailable." in visible_text
    assert "Check config.yaml against the settings documented in README.md." in (
        visible_text
    )
    assert "graph: [" not in visible_text
    assert app.text_area(key="research_question").value == "A valid question"


def test_invalid_output_path_bootstrap_keeps_form_and_safe_error(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "invalid-output-path.yaml"
    config_path.write_text(
        yaml.safe_dump({"output": {"directory": "\x00"}}),
        encoding="utf-8",
    )

    controller = LocalResearchController(config_path=str(config_path))
    app = AppTest.from_function(
        render_app,
        kwargs={"controller": controller},
    ).run()

    app.text_area(key="research_question").set_value("Keep this question").run()
    app.button(key="start_research").click().run()

    visible_text = "\n".join(
        [item.value for item in app.main.markdown]
        + [item.value for item in app.main.error]
        + [item.value for item in app.main.caption]
    )
    assert "Research service configuration is unavailable." in visible_text
    assert "Check config.yaml against the settings documented in README.md." in (
        visible_text
    )
    assert "embedded null" not in visible_text
    assert app.text_area(key="research_question").value == "Keep this question"


@pytest.mark.parametrize(
    ("config_name", "config_contents", "raw_value", "safe_hint"),
    [
        (
            "missing.yaml",
            None,
            "Config file not found",
            "Pass --config with the path to a config.yaml file.",
        ),
        (
            "malformed.yaml",
            "graph: [",
            "graph: [",
            "Check config.yaml against the settings documented in README.md.",
        ),
        (
            "invalid-output.yaml",
            yaml.safe_dump({"output": {"directory": 123}}),
            "directory: 123",
            "Check config.yaml against the settings documented in README.md.",
        ),
    ],
)
def test_bootstrap_failures_keep_form_and_show_sanitized_guidance(
    tmp_path: Path,
    config_name: str,
    config_contents: str | None,
    raw_value: str,
    safe_hint: str,
) -> None:
    config_path = tmp_path / config_name
    if config_contents is not None:
        config_path.write_text(config_contents, encoding="utf-8")
    controller = LocalResearchController(
        config_path=str(config_path),
        runner=GatedSyncRunner(),
        history_store=SessionHistoryStore(output_directory=tmp_path / "output"),
    )

    app = AppTest.from_function(
        render_app,
        kwargs={"controller": controller},
    ).run()

    app.text_area(key="research_question").set_value("Keep this question").run()
    app.button(key="start_research").click().run()

    visible_text = "\n".join(
        [item.value for item in app.main.markdown]
        + [item.value for item in app.main.error]
        + [item.value for item in app.main.caption]
    )
    assert "Research service configuration is unavailable." in visible_text
    assert safe_hint in visible_text
    assert raw_value not in visible_text
    assert app.text_area(key="research_question").value == "Keep this question"
    assert app.session_state[_VIEW_KEY] == "new"


def test_missing_secrets_are_sanitized_at_start_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "DEEPSEEK_API_KEY",
        "TAVILY_API_KEY",
        "OPENAI_API_KEY",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
    ):
        monkeypatch.delenv(name, raising=False)
    controller = LocalResearchController(
        config_path=str(_config_file(tmp_path)),
        runner=GatedSyncRunner(),
        history_store=SessionHistoryStore(output_directory=tmp_path / "output"),
    )
    app = AppTest.from_function(
        render_app,
        kwargs={"controller": controller},
    ).run()

    app.text_area(key="research_question").set_value("A valid question").run()
    app.button(key="start_research").click().run()

    visible_text = "\n".join(
        [item.value for item in app.main.markdown]
        + [item.value for item in app.main.error]
        + [item.value for item in app.main.caption]
    )
    assert "Research service configuration is unavailable." in visible_text
    assert "Set the selected chat provider's API key" in visible_text
    assert "Missing required environment variables" not in visible_text
    assert app.text_area(key="research_question").value == "A valid question"
    assert app.session_state[_VIEW_KEY] == "new"


def test_initial_history_failure_is_blocked_with_safe_app_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SessionHistoryStore(output_directory=tmp_path / "output")
    secret = "raw permission denied details"

    def fail_upsert(_: object) -> None:
        raise OSError(secret)

    monkeypatch.setattr(store, "upsert", fail_upsert)
    controller = LocalResearchController(
        config_path=str(_config_file(tmp_path)),
        runner=GatedSyncRunner(),
        preflight=lambda **_: object(),
        history_store=store,
    )
    app = AppTest.from_function(
        render_app,
        kwargs={"controller": controller},
    ).run()

    app.text_area(key="research_question").set_value("Keep this question").run()
    app.button(key="start_research").click().run()

    visible_text = "\n".join(
        [item.value for item in app.main.markdown]
        + [item.value for item in app.main.error]
        + [item.value for item in app.main.caption]
    )
    assert (
        "Research session could not be started because local history is unavailable."
        in visible_text
    )
    assert "Check that the local output directory is writable and try again." in (
        visible_text
    )
    assert secret not in visible_text
    assert app.text_area(key="research_question").value == "Keep this question"
    assert app.session_state[_VIEW_KEY] == "new"


def test_start_stores_session_and_immediately_renders_running_view() -> None:
    app = _app([]).run()

    app.text_area(key="research_question").set_value("A valid question").run()
    app.button(key="start_research").click().run()

    assert app.session_state[_ACTIVE_SESSION_KEY] == "r" * 32
    assert app.session_state[_VIEW_KEY] == "current"
    assert any("Running" in item.value for item in app.main.markdown)
    assert any("A valid question" in item.value for item in app.main.markdown)


def test_initial_shell_has_identity_navigation_and_at_most_five_recent_rows() -> None:
    entries = [
        _entry(f"{index:032x}", f"Question {index}", "completed", index)
        for index in range(6)
    ]

    app = _app(entries).run()

    assert any("Deep Research" in item.value for item in app.markdown)
    assert any("New research" in value for value in _button_values(app))
    assert any("RECENT SESSIONS" in item.value for item in app.markdown)
    assert "Session history" in _button_values(app)
    question_buttons = [
        value
        for value in _button_values(app)
        if value == "Open"
    ]
    assert question_buttons == ["Open"] * 5
    assert not any("Question 5" in item.value for item in app.markdown)
    assert len(app.tabs) == 0


def test_recent_questions_are_not_data_destructively_truncated() -> None:
    question = "A very long research question " + " ".join(
        ["with useful context"] * 20
    )

    app = _app([_entry("a" * 32, question, "running", 0)]).run()

    assert any(question in item.value for item in app.markdown)


def test_new_research_navigation_sets_new_view() -> None:
    app = _app([_entry("a" * 32, "Question", "completed", 0)]).run()

    app.button(key="session_history").click().run()
    app.button(key="new_research").click().run()

    assert app.session_state[_VIEW_KEY] == "new"


def test_history_navigation_sets_history_view() -> None:
    app = _app([_entry("a" * 32, "Question", "completed", 0)]).run()

    app.button(key="session_history").click().run()

    assert app.session_state[_VIEW_KEY] == "history"


def test_recent_session_navigation_selects_id_and_current_view() -> None:
    session_id = "b" * 32
    app = _app([_entry(session_id, "Question", "running", 0)]).run()

    app.button(key=f"session_{session_id}").click().run()

    assert app.session_state[_SELECTED_SESSION_KEY] == session_id
    assert app.session_state[_VIEW_KEY] == "current"
    assert app.session_state[_ACTIVE_SESSION_KEY] is None
    assert any("Question" in item.value for item in app.main.markdown)
    assert any("Incomplete" in item.value for item in app.markdown)


def test_recent_row_is_one_native_container_with_its_open_action() -> None:
    session_id = "c" * 32
    question = "Question in a native row"
    app = _app([_entry(session_id, question, "completed", 0)]).run()

    row = app.sidebar.container(key=f"dr-session-row-{session_id}")

    assert any(question in item.value for item in row.markdown)
    assert row.button(key=f"session_{session_id}").label == "Open"


def test_selected_recent_row_uses_a_native_selected_container() -> None:
    session_id = "d" * 32
    app = _app([_entry(session_id, "Question", "completed", 0)]).run()

    app.button(key=f"session_{session_id}").click().run()

    selected_row = app.sidebar.container(
        key=f"dr-session-row-selected-{session_id}"
    )
    assert selected_row.button(key=f"session_{session_id}").label == "Open"


def test_sidebar_spacer_is_a_native_container_before_history_action() -> None:
    app = _app([_entry("e" * 32, "Question", "completed", 0)]).run()

    spacer = app.sidebar.container(key="dr-sidebar-spacer")

    assert spacer is not None
    assert app.sidebar.button(key="session_history").label == "Session history"


def test_active_session_is_promoted_to_the_first_recent_row() -> None:
    active_id = "f" * 32
    older_id = "1" * 32
    app = _app(
        [
            _entry(older_id, "Older question", "completed", 10),
            _entry(active_id, "Active question", "running", 20),
        ]
    ).run()
    app.session_state[_ACTIVE_SESSION_KEY] = active_id

    app.run()

    row_keys = [container.key for container in app.sidebar.container]
    assert row_keys.index(f"dr-session-row-{active_id}") < row_keys.index(
        f"dr-session-row-{older_id}"
    )
    active_row = app.sidebar.container(key=f"dr-session-row-{active_id}")
    assert any("Active question" in item.value for item in active_row.markdown)
    assert any("Running" in item.value for item in active_row.markdown)


def test_selected_older_active_session_is_promoted_into_recent_rows() -> None:
    selected_id = "2" * 32
    entries = [
        _entry(selected_id, "Older selected active question", "running", 10),
        *[
            _entry(
                f"{index + 10:032x}",
                f"Newer completed question {index}",
                "completed",
                index,
            )
            for index in range(5)
        ],
    ]
    controller = MultiActiveFakeController(entries, {selected_id})
    app = AppTest.from_function(
        render_app,
        kwargs={"controller": controller},
    ).run()
    app.session_state[_SELECTED_SESSION_KEY] = selected_id
    app.run()

    row_keys = [container.key for container in app.sidebar.container]
    assert row_keys[0] == f"dr-session-row-selected-{selected_id}"
    selected_row = app.sidebar.container(
        key=f"dr-session-row-selected-{selected_id}"
    )
    assert any(
        "Older selected active question" in item.value
        for item in selected_row.markdown
    )
    assert any("Running" in item.value for item in selected_row.markdown)


def test_running_screen_preserves_narrative_sequence_and_three_recent_rows() -> None:
    snapshot = _snapshot(
        sub_topics=[
            UiSubTopicProgress(
                index=1,
                title="Battery cost curves",
                status="completed",
            ),
            UiSubTopicProgress(
                index=2,
                title="Grid operator adoption patterns",
                status="running",
            ),
            UiSubTopicProgress(
                index=3,
                title="Competing storage technologies",
                status="queued",
            ),
            UiSubTopicProgress(index=4, title="Investment flows", status="queued"),
            UiSubTopicProgress(
                index=5,
                title="Market structure impacts",
                status="queued",
            ),
        ],
        recent_activity=[
            UiRecentActivity(event_type="one", summary="Old activity"),
            UiRecentActivity(event_type="two", summary="Started subtopic 2"),
            UiRecentActivity(event_type="three", summary="Evaluated new evidence"),
            UiRecentActivity(event_type="four", summary="Newest meaningful activity"),
        ],
    )

    app = _running_app(snapshot)
    visible = _visible_main_text(app)

    assert "How will grid-scale batteries reshape energy markets by 2030?" in visible
    assert "Researcher" in visible
    assert "Subtopic 2 of 5" in visible
    assert "Searching and evaluating sources" in visible
    assert "Macro iteration 2 of 4" in visible
    assert "No issues detected" in visible
    assert "Battery cost curves" in visible
    assert "Grid operator adoption patterns" in visible
    assert "Queued" in visible
    assert "Old activity" not in visible
    activity_summaries = (
        "Started subtopic 2",
        "Evaluated new evidence",
        "Newest meaningful activity",
    )
    assert sum(summary in visible for summary in activity_summaries) == 3
    assert "Started subtopic 2" in visible
    assert "Evaluated new evidence" in visible
    assert "Newest meaningful activity" in visible


def test_running_screen_omits_unavailable_observability() -> None:
    app = _running_app(_snapshot())
    visible = _visible_main_text(app)

    assert "Not available" not in visible
    assert "0 tokens" not in visible
    assert "Open LangSmith trace" not in _button_values(app)
    assert len(app.metric) == 0


def test_running_screen_renders_available_observability_once() -> None:
    app = _running_app(
        _snapshot(
            token_usage=UiTokenUsage(input_tokens=8_000, output_tokens=4_400),
            trace_url="https://smith.langchain.com/o/example/r/session",
        )
    )
    assert app.metric[0].value == "12.4k"
    trace_links = app.main.get("link_button")
    assert len(trace_links) == 1
    assert trace_links[0].label == "Open LangSmith trace"
    assert trace_links[0].url.endswith("/session")


def test_running_screen_does_not_claim_progress_without_a_defensible_fraction() -> None:
    app = _running_app(
        _snapshot(
            sub_topics=[
                UiSubTopicProgress(index=1, title="Active topic", status="running"),
                UiSubTopicProgress(index=2, title="Queued topic", status="queued"),
            ]
        )
    )
    visible = _visible_main_text(app)

    assert "Macro iteration 2 of 4" in visible
    assert "%" not in visible
    assert "ETA" not in visible.replace("SESSION DETAILS", "")


def test_recoverable_issue_is_concise_and_safe() -> None:
    app = _running_app(
        _snapshot(
            errors=[
                {
                    "error_type": "researcher.tool_failed",
                    "source": "researcher",
                    "message": "safe internal message",
                    "recoverable": True,
                    "details": {"provider_response": "SECRET-DETAIL"},
                }
            ]
        )
    )
    visible = _visible_main_text(app)

    assert "1 issue; continuing" in visible
    assert "safe internal message" not in visible
    assert "SECRET-DETAIL" not in visible


def test_recoverable_failed_tool_event_is_reflected_in_health() -> None:
    progress = project_progress(
        [
            ResearchEvent(
                event_type="researcher.tool_call",
                source="test",
                message="private provider payload",
                metadata={
                    "tool": "web_search",
                    "success": False,
                    "payload": "SECRET-PAYLOAD",
                },
            )
        ]
    )
    app = _running_app(
        _snapshot(
            tool_calls=progress.tool_calls,
            recent_activity=progress.recent_activity,
        )
    )
    visible = _visible_main_text(app)

    assert "1 issue; continuing" in visible
    assert "SECRET-PAYLOAD" not in visible


def test_recoverable_non_tool_graph_error_is_reflected_in_health() -> None:
    progress = project_progress(
        [
            ResearchEvent(
                event_type="graph.node.completed",
                source="graph.source_evaluator",
                message="Node completed.",
                metadata={"error_count": 1, "iteration": 1},
            )
        ]
    )
    app = _running_app(_snapshot(issue_count=progress.issue_count))

    assert "1 issue; continuing" in _visible_main_text(app)
    assert "No issues detected" not in _visible_main_text(app)


def test_running_rerun_does_not_start_a_duplicate_worker() -> None:
    app = _app([]).run()
    app.text_area(key="research_question").set_value("A valid question").run()
    app.button(key="start_research").click().run()

    controller = app.session_state[_CONTROLLER_KEY]
    app.run()

    assert len(controller.start_calls) == 1


def test_live_fragment_updates_sidebar_status_after_terminal_snapshot() -> None:
    session_id = "g" * 32
    running = _snapshot(status="running").model_copy(
        update={"session_id": session_id}
    )
    completed = running.model_copy(
        update={"status": "completed", "report": "Done"}
    )

    class SequencedController(FakeController):
        def __init__(self) -> None:
            super().__init__([_entry(session_id, "Question", "running", 0)])
            self.snapshot_calls = 0

        def snapshot(self, requested_id: str) -> UiSessionSnapshot:
            assert requested_id == session_id
            self.snapshot_calls += 1
            return running if self.snapshot_calls == 1 else completed

    controller = SequencedController()

    def fragment_script(active_controller) -> None:
        import streamlit as st

        from deep_research.ui.app import (
            _ACTIVE_SESSION_KEY,
            _LIVE_SESSION_KEY,
            render_live_progress,
        )
        from deep_research.ui.components import render_sidebar

        st.session_state.setdefault(_ACTIVE_SESSION_KEY, "g" * 32)
        st.session_state.setdefault(_LIVE_SESSION_KEY, "g" * 32)

        render_sidebar(active_controller)
        render_live_progress(active_controller)

    app = AppTest.from_function(
        fragment_script,
        kwargs={"active_controller": controller},
    ).run()
    sidebar_text = "\n".join(item.value for item in app.sidebar.markdown)

    assert "Completed" in sidebar_text
    assert "Running" not in sidebar_text


def test_terminal_live_fragment_clears_poll_target_and_preserves_selection() -> None:
    session_id = "h" * 32
    running = _snapshot(status="running").model_copy(
        update={"session_id": session_id}
    )
    completed = running.model_copy(
        update={"status": "completed", "report": "Done"}
    )

    class SequencedController(FakeController):
        def __init__(self) -> None:
            super().__init__([_entry(session_id, "Question", "running", 0)])
            self.snapshot_calls = 0

        def snapshot(self, requested_id: str) -> UiSessionSnapshot:
            assert requested_id == session_id
            self.snapshot_calls += 1
            return running if self.snapshot_calls == 1 else completed

    controller = SequencedController()

    def fragment_script(active_controller) -> None:
        import streamlit as st

        from deep_research.ui.app import (
            _ACTIVE_SESSION_KEY,
            _LIVE_SESSION_KEY,
            _SELECTED_SESSION_KEY,
            render_live_progress,
        )
        from deep_research.ui.components import render_sidebar

        st.session_state.setdefault(_ACTIVE_SESSION_KEY, "h" * 32)
        st.session_state.setdefault(_LIVE_SESSION_KEY, "h" * 32)
        st.session_state.setdefault(_SELECTED_SESSION_KEY, "h" * 32)

        render_sidebar(active_controller)
        render_live_progress(active_controller)

    app = AppTest.from_function(
        fragment_script,
        kwargs={"active_controller": controller},
    ).run()

    assert app.session_state[_LIVE_SESSION_STATE_KEY] is None
    assert app.session_state[_SELECTED_SESSION_KEY] == session_id


def test_incomplete_without_report_hides_quality_rail() -> None:
    app = _running_app(
        _snapshot(
            status="incomplete",
            report=None,
            sub_topics=[
                UiSubTopicProgress(
                    index=2,
                    title="Last active topic",
                    status="running",
                )
            ],
            recent_activity=[
                UiRecentActivity(
                    event_type="researcher.sub_topic.started",
                    summary="Last known activity",
                )
            ],
        )
    )
    visible = _visible_main_text(app)

    assert "RESEARCH INCOMPLETE" in visible
    assert "Last active topic" in visible
    assert "Last known activity" in visible
    assert "No report was available for this session." in visible
    assert "SOURCE CREDIBILITY" not in visible
    assert "FACT-CHECK SUMMARY" not in visible


def test_starting_state_is_visible_and_prevents_duplicate_submission() -> None:
    app = _app([]).run()
    app.text_area(key="research_question").set_value("A valid question").run()
    app.session_state[_START_IN_FLIGHT_KEY] = True
    app.run()

    visible = _visible_main_text(app)
    assert "Preparing research plan" in visible
    assert "Ready to start" not in visible
    assert app.text_area(key="research_question").disabled is True
    assert app.number_input(key="max_iterations").disabled is True
    assert app.button(key="start_research").disabled is True


def test_running_screen_labels_derived_fraction_as_phase_progress() -> None:
    app = _running_app(
        _snapshot(
            sub_topics=[
                UiSubTopicProgress(
                    index=1,
                    title="Completed topic",
                    status="completed",
                ),
                UiSubTopicProgress(index=2, title="Active topic", status="running"),
                UiSubTopicProgress(index=3, title="Queued topic", status="queued"),
                UiSubTopicProgress(index=4, title="Queued topic two", status="queued"),
                UiSubTopicProgress(
                    index=5,
                    title="Queued topic three",
                    status="queued",
                ),
            ]
        )
    )
    visible = _visible_main_text(app)

    assert "Phase progress" in visible
    assert "20%" in visible
    assert "Overall progress" not in visible
    assert "ETA" not in visible.replace("SESSION DETAILS", "")


def test_running_screen_omits_indefensible_partial_phase_percentage() -> None:
    app = _running_app(
        _snapshot(
            current_agent="source_evaluator",
            planned_sub_topic_count=5,
            sub_topics=[
                UiSubTopicProgress(
                    index=1,
                    title="Completed topic",
                    status="completed",
                )
            ],
        )
    )
    visible = _visible_main_text(app)

    assert "Source evaluator" in visible
    assert "Evaluating source credibility" in visible
    assert "Searching and evaluating sources" not in visible
    assert "1 known of 5 planned subtopics" in visible
    assert "Phase progress" not in visible
    assert "Queued" not in visible
    assert "Queued subtopic" not in visible


def test_running_screen_uses_planned_total_for_known_active_subtopic() -> None:
    app = _running_app(
        _snapshot(
            planned_sub_topic_count=5,
            sub_topics=[
                UiSubTopicProgress(
                    index=2,
                    title="Known active topic",
                    status="running",
                )
            ],
        )
    )
    visible = _visible_main_text(app)

    assert "Researcher" in visible
    assert "Subtopic 2 of 5" in visible
    assert "Known active topic" in visible
    assert "Phase progress" not in visible
    assert "Queued subtopic" not in visible


def test_running_screen_keeps_tool_and_agent_details_collapsed_and_safe() -> None:
    app = _running_app(_snapshot())
    visible = _visible_main_text(app)

    expanders = {item.label: item for item in app.expander}
    assert expanders["Tool activity"].proto.expanded is False
    assert expanders["Agent details"].proto.expanded is False
    assert "raw event" not in visible.casefold()
    assert "web_search" not in visible
    assert "s" * 32 not in visible
    assert "provider" not in visible.casefold()
    assert "stack trace" not in visible.casefold()


def test_completed_snapshot_transitions_to_report_view_in_same_canvas() -> None:
    report = "# Research answer\n\nReport body."
    app = _running_app(_snapshot(status="completed", report=report))
    visible = _visible_main_text(app)

    assert "Completed" in visible
    assert report in visible
    assert "Research answer" in visible
    assert "Report body." in visible
    assert "Session detail content will appear here." not in visible


def test_completed_report_screen_leads_with_markdown_and_quality_summaries() -> None:
    app = _running_app(_completed_report_snapshot())
    visible = _visible_main_text(app)

    assert "RESEARCH COMPLETED" in visible
    assert "How will grid-scale batteries reshape energy markets by 2030?" in visible
    assert "Completed in 2 of 4 iterations" in visible
    assert "reports/grid-scale-batteries-2030.md" in visible
    assert "# Research answer" in visible
    assert "The report answer leads with the research question." in visible
    assert "SOURCE CREDIBILITY" in visible
    assert "FACT-CHECK SUMMARY" in visible
    assert "High" in visible and "Moderate" in visible
    assert "Low" in visible and "Unrated" in visible
    assert "Verified" in visible and "Unverified" in visible
    assert "Contradicted" in visible and "Insufficient evidence" in visible
    assert app.metric[0].label == "Tokens used"
    assert any(
        button.label == "Open LangSmith trace"
        for button in app.main.get("link_button")
    )


def test_completed_report_is_on_base_canvas_with_secondary_quality_details() -> None:
    app = _running_app(_completed_report_snapshot())
    visible = _visible_main_text(app)

    assert "The report answer leads with the research question." in visible
    assert not any(item.label == "Report" for item in app.expander)
    assert len(app.tabs) == 0
    assert any(item.label == "Source details" for item in app.expander)
    assert any(item.label == "Claim details" for item in app.expander)
    assert any(item.label == "Session metadata" for item in app.expander)
    assert "Average source score" not in visible
    assert "Gauge" not in visible


def test_empty_reopened_quality_details_explain_local_history_retention() -> None:
    snapshot = _completed_report_snapshot().model_copy(
        update={
            "source_summary": UiSourceSummary(
                total=0,
                high=0,
                moderate=0,
                low=0,
                unrated=0,
            ),
            "fact_check_summary": UiFactCheckSummary(
                verified=0,
                unverified=0,
                contradicted=0,
                insufficient_evidence=0,
            ),
        }
    )
    app = _running_app(snapshot)
    visible = _visible_main_text(app)

    assert "Source details were not retained in local session history." in visible
    assert "Claim details were not retained in local session history." in visible
    assert "No source details recorded." not in visible
    assert "No claim details recorded." not in visible


def test_report_quality_rows_and_path() -> None:
    app = _running_app(_completed_report_snapshot())
    visible = _visible_main_text(app)

    assert app.container(key="dr-report-column") is not None
    assert "dr-quality-row" in visible
    assert "dr-quality-icon" in visible
    assert "dr-quality-row--source-high" in visible
    assert "dr-quality-row--fact-contradicted" in visible
    assert app.code
    assert app.code[0].value == "reports/grid-scale-batteries-2030.md"
    assert any(
        item.value == "Report path" for item in app.main.caption
    )


def test_completed_without_execution_errors_keeps_neutral_issue_state() -> None:
    app = _running_app(_completed_report_snapshot(errors=[]))
    visible = _visible_main_text(app)

    assert "No errors reported." in visible
    assert "EXECUTION ERRORS" not in visible
    assert not any(
        item.label == "Safe diagnostic details" for item in app.expander
    )


def test_execution_error_mapping_uses_safe_project_owned_copy_and_details() -> None:
    from deep_research.utils.types import ResearchError

    error = ResearchError(
        error_type="source_evaluator_reputation_unavailable",
        source="agent.source_evaluator",
        message="provider response contains TOP-SECRET-RAW-MESSAGE",
        recoverable=True,
        details={
            "failures": 3,
            "exception_type": "ProviderTimeoutError",
            "provider_config": "SECRET-CONFIG",
            "raw_payload": "SECRET-PAYLOAD",
        },
    )

    presentation = execution_error_presentation(error)
    rendered = presentation.model_dump_json()

    assert presentation.category == "Source evaluation issue"
    assert "The source credibility pass continued" in presentation.effect
    assert presentation.recovery_hint
    assert presentation.diagnostic_context["Stage"] == "Source evaluator"
    assert presentation.diagnostic_context["Failures"] == "3"
    assert presentation.diagnostic_context["Exception type"] == (
        "ProviderTimeoutError"
    )
    assert "TOP-SECRET-RAW-MESSAGE" not in rendered
    assert "SECRET-CONFIG" not in rendered
    assert "SECRET-PAYLOAD" not in rendered


def test_nonfatal_error_copy_and_details() -> None:
    app = _running_app(
        _completed_report_snapshot(
            errors=[
                {
                    "error_type": "source_evaluator_reputation_unavailable",
                    "source": "agent.source_evaluator",
                    "message": "private raw provider response",
                    "recoverable": True,
                    "details": {
                        "failures": 2,
                        "raw_payload": "SECRET-COMPLETED-PAYLOAD",
                    },
                }
            ]
        )
    )
    visible = _visible_main_text(app)

    assert "EXECUTION ERRORS" in visible
    assert "Source evaluation issue" in visible
    assert "The source credibility pass continued" in visible
    assert "Recovery" in visible
    assert "private raw provider response" not in visible
    assert "SECRET-COMPLETED-PAYLOAD" not in visible
    details = next(
        item for item in app.expander if item.label == "Safe diagnostic details"
    )
    assert details.proto.expanded is False


def test_terminal_error_copy_and_recovery() -> None:
    app = _running_app(
        _snapshot(
            status="failed",
            report=None,
            errors=[
                {
                    "error_type": "researcher_extraction_provider_error",
                    "source": "agent.researcher",
                    "message": "raw provider trace SECRET-TERMINAL-DETAIL",
                    "recoverable": False,
                    "details": {
                        "exception_type": "ProviderTimeoutError",
                        "iterations": 2,
                        "trace": "SECRET-TRACE",
                    },
                }
            ],
        )
    )
    visible = _visible_main_text(app)

    assert "Finding extraction issue" in visible
    assert "The research pass stopped" in visible
    assert "Recovery" in visible
    assert "SECRET-TERMINAL-DETAIL" not in visible
    assert "SECRET-TRACE" not in visible


def test_failed_partial_report_keeps_report_and_sanitized_execution_effect() -> None:
    app = _running_app(
        _completed_report_snapshot(
            status="failed",
            report="# Partial result\n\nRetained findings.",
            errors=[
                {
                    "error_type": "ui.research.failed",
                    "source": "ui",
                    "message": "raw terminal exception SECRET-PARTIAL-DETAIL",
                    "recoverable": False,
                    "details": {"traceback": "SECRET-TRACEBACK"},
                }
            ],
        )
    )
    visible = _visible_main_text(app)

    assert "# Partial result" in visible
    assert "Retained findings." in visible
    assert "Execution stopped" in visible
    assert "The run stopped; any retained report is partial." in visible
    assert "SECRET-PARTIAL-DETAIL" not in visible
    assert "SECRET-TRACEBACK" not in visible


def test_completed_source_details_disclose_provenance_without_an_opaque_score() -> None:
    app = _running_app(_completed_report_snapshot())
    source_details = next(
        item for item in app.expander if item.label == "Source details"
    )
    assert source_details.proto.expanded is False
    visible = _visible_main_text(app)

    assert "Grid Storage Outlook" in visible
    assert "https://example.com/grid-storage" in visible
    assert "High" in visible
    assert "Credibility is mixed across the evaluated sources." in visible
    assert "Primary market dataset with transparent methodology." in visible
    assert "Used in research topics" in visible
    assert "Battery cost curves" in visible
    assert "Corroboration" in visible
    assert "91%" not in visible
    assert "Average" not in visible


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        (
            UiSourceSummary(total=0, high=0, moderate=0, low=0, unrated=0),
            "No evaluated sources are available to summarize.",
        ),
        (
            UiSourceSummary(total=6, high=3, moderate=2, low=1, unrated=0),
            "Most evaluated sources are high or moderate credibility; "
            "1 is low credibility.",
        ),
        (
            UiSourceSummary(total=4, high=1, moderate=1, low=1, unrated=1),
            "Credibility is mixed across the evaluated sources.",
        ),
        (
            UiSourceSummary(total=3, high=0, moderate=0, low=0, unrated=3),
            "All evaluated sources are unrated.",
        ),
        (
            UiSourceSummary(total=4, high=1, moderate=0, low=3, unrated=0),
            "Most evaluated sources are low credibility.",
        ),
    ],
)
def test_source_credibility_distribution_explanation_is_deterministic(
    summary: UiSourceSummary,
    expected: str,
) -> None:
    assert components._source_distribution_summary(summary) == expected


def test_completed_claim_details_preserve_verdict_and_structured_evidence() -> None:
    app = _running_app(_completed_report_snapshot())
    claim_details = next(
        item for item in app.expander if item.label == "Claim details"
    )
    assert claim_details.proto.expanded is False
    visible = _visible_main_text(app)

    assert "Deployment expands substantially." in visible
    assert "Verified" in visible
    assert "https://example.com/grid-storage" in visible
    assert "Capacity data supports the estimate." in visible
    assert "Interconnection is never a constraint." in visible
    assert "Contradicted" in visible
    assert "The claim conflicts with operator filings." in visible
    assert "Insufficient evidence" in visible


def test_completed_limitations_and_errors_are_separate_semantic_sections() -> None:
    app = _running_app(
        _completed_report_snapshot(
            limitations=["Pilot-scale sodium-ion data may not generalize."],
            errors=[
                {
                    "error_type": "researcher.tool_failed",
                    "source": "researcher",
                    "message": "A research step had an issue; continuing",
                    "recoverable": True,
                    "details": {"private": "SECRET-DIAGNOSTIC"},
                }
            ],
        )
    )
    visible = _visible_main_text(app)

    assert "LIMITATIONS" in visible
    assert "Pilot-scale sodium-ion data may not generalize." in visible
    assert "EXECUTION ERRORS" in visible
    assert "Execution errors were recorded during the run." in visible
    assert "SECRET-DIAGNOSTIC" not in visible
    assert len(app.error) == 1
    assert len(app.warning) == 0


def test_completed_report_omits_unavailable_telemetry_entirely() -> None:
    app = _running_app(
        _completed_report_snapshot(token_usage=None, trace_url=None)
    )
    visible = _visible_main_text(app)

    assert "Tokens used" not in visible
    assert "Open LangSmith trace" not in _button_values(app)
    assert len(app.metric) == 0
    assert not any(
        button.label == "Open LangSmith trace"
        for button in app.main.get("link_button")
    )


def test_max_iterations_keeps_partial_report_and_uses_amber_language() -> None:
    app = _running_app(
        _completed_report_snapshot(
            status="max_iterations",
            report="# Partial report\n\nReadable stopping-point findings.",
            token_usage=None,
            trace_url=None,
        )
    )
    visible = _visible_main_text(app)

    assert "Max iterations" in visible
    assert "Partial report" in visible
    assert "Readable stopping-point findings." in visible
    assert "The run reached its configured iteration limit." in visible
    assert "Stopped at iteration 2 of 4" in visible
    assert "Completed in 2 of 4 iterations" not in visible
    assert "Failed" not in visible


def test_incomplete_report_uses_incomplete_iteration_wording() -> None:
    app = _running_app(
        _snapshot(
            status="incomplete",
            report="# Incomplete report\n\nThe stopping point is readable.",
        )
    )
    visible = _visible_main_text(app)

    assert "Incomplete" in visible
    assert "Incomplete after 2 of 4 iterations" in visible
    assert "Completed in 2 of 4 iterations" not in visible


def test_max_iterations_snapshot_has_amber_terminal_treatment() -> None:
    app = _running_app(
        _snapshot(status="max_iterations", report="Partial report.")
    )
    visible = _visible_main_text(app)

    assert "Max iterations" in visible
    assert "Partial report." in visible
    assert "Running" not in visible


def _history_entries() -> list[SessionHistoryEntry]:
    return [
        _entry("a" * 32, "Newest completed research question", "completed", 0),
        _entry("b" * 32, "Active research question", "running", 1),
        _entry("c" * 32, "Stopped after too many iterations", "max_iterations", 2),
        _entry("d" * 32, "Paused before the research finished", "incomplete", 3),
        _entry("e" * 32, "Research question with an error", "failed", 4),
        _entry("f" * 32, "Old completed research question", "completed", 5),
    ]


def _history_app(entries: list[SessionHistoryEntry] | None = None) -> AppTest:
    app = _app(entries or _history_entries()).run()
    app.button(key="session_history").click().run()
    return app


def test_history_screen_has_searchable_newest_first_status_rows_and_open_actions(
) -> None:
    app = _history_app()
    visible = _visible_main_text(app)

    assert "Research sessions" in visible
    assert "6 sessions total" in visible
    assert app.text_input(key=_HISTORY_SEARCH_KEY).label == "Search research questions"
    assert app.segmented_control(key=_HISTORY_FILTER_KEY).options == [
        "All",
        "Running",
        "Completed",
        "Issues",
    ]
    assert "Newest completed research question" in visible
    assert "Old completed research question" in visible
    assert visible.index("Newest completed research question") < visible.index(
        "Old completed research question"
    )
    assert all(
        status in visible
        for status in ("Completed", "Max iterations", "Incomplete", "Failed")
    )
    assert len([button for button in app.main.button if button.label == "Open"]) == 6
    assert len(app.dataframe) == 0


def test_history_search_is_case_insensitive_and_survives_reruns() -> None:
    app = _history_app()

    app.text_input(key=_HISTORY_SEARCH_KEY).set_value("STOPPED").run()
    visible = _visible_main_text(app)

    assert "Stopped after too many iterations" in visible
    assert "Newest completed research question" not in visible
    assert app.session_state[_HISTORY_SEARCH_KEY] == "STOPPED"

    app.run()
    assert app.session_state[_HISTORY_SEARCH_KEY] == "STOPPED"
    assert "Stopped after too many iterations" in _visible_main_text(app)


def test_history_filters_completed_and_issues_without_changing_archive_order() -> None:
    app = _history_app()

    app.segmented_control(key=_HISTORY_FILTER_KEY).set_value("Issues").run()
    issues = _visible_main_text(app)
    assert "Stopped after too many iterations" in issues
    assert "Paused before the research finished" in issues
    assert "Research question with an error" in issues
    assert "Newest completed research question" not in issues
    assert app.session_state[_HISTORY_FILTER_KEY] == "Issues"

    app.segmented_control(key=_HISTORY_FILTER_KEY).set_value("Completed").run()
    completed = _visible_main_text(app)
    assert "Newest completed research question" in completed
    assert "Old completed research question" in completed
    assert "Stopped after too many iterations" not in completed


def test_historical_running_session_is_shown_as_incomplete_when_not_active() -> None:
    app = _history_app([_entry("b" * 32, "Persisted running question", "running", 0)])
    visible = _visible_main_text(app)

    assert "Persisted running question" in visible
    assert "Incomplete" in visible
    assert "Running" not in visible


def test_active_running_session_keeps_running_status_in_history() -> None:
    session_id = "b" * 32
    app = _app([_entry(session_id, "Active research question", "running", 0)]).run()
    app.session_state[_ACTIVE_SESSION_KEY] = session_id
    app.session_state[_VIEW_KEY] = "history"
    app.run()

    visible = _visible_main_text(app)
    assert "Active research question" in visible
    assert "Running" in visible
    assert "Incomplete" not in visible


def test_history_archive_is_complete_searchable_filtered_and_newest_first() -> None:
    entries = [
        _entry(
            f"{index + 1:032x}",
            f"Archive question {index + 1:02d}",
            "failed" if index == 59 else "completed",
            index,
        )
        for index in range(60)
    ]
    app = _history_app(entries)
    visible = _visible_main_text(app)

    assert "60 sessions total" in visible
    assert "Archive question 01" in visible
    assert "Archive question 60" in visible
    assert visible.index("Archive question 01") < visible.index(
        "Archive question 60"
    )

    app.text_input(key=_HISTORY_SEARCH_KEY).set_value(
        "ARCHIVE QUESTION 60"
    ).run()
    searched = _visible_main_text(app)
    assert "Archive question 60" in searched
    assert "Archive question 01" not in searched

    app.segmented_control(key=_HISTORY_FILTER_KEY).set_value("Issues").run()
    filtered = _visible_main_text(app)
    assert "Archive question 60" in filtered
    assert "Archive question 01" not in filtered


def test_two_active_sessions_stay_running_when_history_is_opened() -> None:
    first_id = "a" * 32
    second_id = "b" * 32
    controller = MultiActiveFakeController(
        [
            _entry(first_id, "First active question", "running", 0),
            _entry(second_id, "Second active question", "running", 1),
        ],
        {first_id, second_id},
    )
    app = AppTest.from_function(
        render_app,
        kwargs={"controller": controller},
    ).run()
    app.session_state[_ACTIVE_SESSION_KEY] = first_id
    app.session_state[_VIEW_KEY] = "history"
    app.run()

    visible = _visible_main_text(app)
    assert "First active question" in visible
    assert "Second active question" in visible
    assert "Running" in visible
    assert "Incomplete" not in visible

    app.button(key=f"history_open_{second_id}").click().run()
    reopened = _visible_main_text(app)
    assert "Second active question" in reopened
    assert "Running" in reopened
    assert "Incomplete" not in reopened


def test_selecting_older_active_session_sets_that_session_as_live_target() -> None:
    older_id = "a" * 32
    newer_id = "b" * 32
    controller = MultiActiveFakeController(
        [
            _entry(older_id, "Older active question", "running", 10),
            _entry(newer_id, "Newer active question", "running", 0),
        ],
        {older_id, newer_id},
    )
    app = AppTest.from_function(
        render_app,
        kwargs={"controller": controller},
    ).run()

    app.button(key=f"session_{older_id}").click().run()

    assert app.session_state[_LIVE_SESSION_STATE_KEY] == older_id
    assert app.session_state[_SELECTED_SESSION_KEY] == older_id
    assert app.session_state[_VIEW_KEY] == "current"
    assert "Older active question" in _visible_main_text(app)


def test_concurrent_sidebar_refresh_keeps_status_with_its_session_row() -> None:
    older_id = "c" * 32
    newer_id = "d" * 32

    class ConcurrentController(MultiActiveFakeController):
        def __init__(self) -> None:
            self.older_id = older_id
            self.newer_id = newer_id
            super().__init__(
                [
                    _entry(older_id, "Older active question", "running", 10),
                    _entry(newer_id, "Newer active question", "running", 0),
                ],
                {older_id, newer_id},
            )
            self.live_snapshots = {
                older_id: _snapshot(status="completed", report="Done").model_copy(
                    update={"session_id": older_id}
                ),
                newer_id: _snapshot(status="running").model_copy(
                    update={"session_id": newer_id}
                ),
            }

        def snapshot(self, requested_id: str) -> UiSessionSnapshot:
            return self.live_snapshots[requested_id].model_copy(deep=True)

    controller = ConcurrentController()

    def sidebar_script(active_controller) -> None:
        import streamlit as st

        from deep_research.ui.app import _ACTIVE_SESSION_KEY
        from deep_research.ui.components import render_sidebar, render_sidebar_status

        st.session_state[_ACTIVE_SESSION_KEY] = active_controller.newer_id
        render_sidebar(active_controller)
        render_sidebar_status(
            active_controller,
            active_controller.live_snapshots[active_controller.older_id],
        )

    app = AppTest.from_function(
        sidebar_script,
        kwargs={"active_controller": controller},
    ).run()

    older_row = app.sidebar.container(key=f"dr-session-row-{controller.older_id}")
    newer_row = app.sidebar.container(key=f"dr-session-row-{controller.newer_id}")
    older_text = "\n".join(
        [item.value for item in older_row.markdown]
        + [item.value for item in older_row.caption]
    )
    newer_text = "\n".join(
        [item.value for item in newer_row.markdown]
        + [item.value for item in newer_row.caption]
    )

    assert "Completed" in older_text
    assert "Running" not in older_text
    assert "Running" in newer_text
    assert "Completed" not in newer_text


@pytest.mark.parametrize(
    ("status", "expected_label", "report", "report_path"),
    [
        ("failed", "Failed", None, None),
        (
            "failed",
            "Failed",
            "# Failed partial report",
            "reports/failed-partial.md",
        ),
        ("incomplete", "Incomplete", None, None),
        (
            "max_iterations",
            "Max iterations",
            "# Max-iteration report",
            "reports/max.md",
        ),
        ("completed", "Completed", "# Completed report", "reports/completed.md"),
    ],
)
def test_restarted_history_reopens_each_terminal_state_without_rerunning(
    status: str,
    expected_label: str,
    report: str | None,
    report_path: str | None,
) -> None:
    topic = UiSubTopicProgress(
        index=2,
        title="Last active topic",
        status="running",
    )
    session_id = f"{200 + len(status):032x}"
    entry = SessionHistoryEntry(
        session_id=session_id,
        question="Restart-safe question",
        status=status,
        started_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 9, 0, 10, tzinfo=timezone.utc),
        current_agent=None,
        last_agent="researcher",
        iteration=2,
        max_iterations=4,
        planned_sub_topic_count=5,
        research_phase_complete=False,
        sub_topics=[topic],
        last_sub_topic=topic,
        recent_activity=[
            UiRecentActivity(event_type="one", summary="Last known activity"),
            UiRecentActivity(event_type="two", summary="Older activity"),
        ],
        report_path=report_path,
        source_summary=UiSourceSummary(
            total=0,
            high=0,
            moderate=0,
            low=0,
            unrated=0,
        ),
        fact_check_summary=UiFactCheckSummary(
            verified=0,
            unverified=0,
            contradicted=0,
            insufficient_evidence=0,
        ),
    )
    controller = RestartedFakeController([entry])
    if report is not None:
        controller.history_reports[session_id] = report

    app = AppTest.from_function(
        render_app,
        kwargs={"controller": controller},
    ).run()
    app.button(key="session_history").click().run()
    app.button(key=f"history_open_{session_id}").click().run()

    visible = _visible_main_text(app)
    assert expected_label in visible
    assert controller.start_calls == []
    if status == "completed":
        assert report in visible
    else:
        assert "Researcher" in visible
        assert "Last active topic" in visible
        assert "Last known activity" in visible
    if report_path is None:
        assert "No report was available for this session." in visible
    else:
        assert report_path in visible


def test_history_open_rebuilds_completed_report_in_same_shell_without_rerun() -> None:
    session_id = "a" * 32
    app = _history_app([_entry(session_id, "Completed question", "completed", 0)])
    controller = app.session_state[_CONTROLLER_KEY]
    controller.history_reports[session_id] = "# Reopened report\n\nSaved answer."

    app.button(key=f"history_open_{session_id}").click().run()

    assert app.session_state[_SELECTED_SESSION_KEY] == session_id
    assert app.session_state[_VIEW_KEY] == "current"
    assert controller.start_calls == []
    visible = _visible_main_text(app)
    assert "Reopened report" in visible
    assert "Saved answer." in visible
    assert "Research sessions" not in visible


def test_history_open_failure_retains_safe_terminal_context_and_partial_report(
) -> None:
    session_id = "e" * 32
    entry = _entry(session_id, "Failed question", "failed", 0)
    app = _history_app([entry])
    controller = app.session_state[_CONTROLLER_KEY]
    controller.history_reports[session_id] = (
        "# Partial stopping point\n\nUseful findings."
    )

    app.button(key=f"history_open_{session_id}").click().run()

    visible = _visible_main_text(app)
    assert "Failed" in visible
    assert "Partial stopping point" in visible
    assert "Useful findings." in visible
    assert controller.start_calls == []


def test_history_selected_row_has_a_non_color_selected_cue() -> None:
    session_id = "a" * 32
    app = _history_app([_entry(session_id, "Selected question", "completed", 0)])

    app.button(key=f"history_open_{session_id}").click().run()
    app.button(key="session_history").click().run()

    selected_row = app.main.container(key=f"dr-history-row-selected-{session_id}")
    assert any("Selected session" in item.value for item in selected_row.caption)
    assert "st-key-dr-history-row-selected-" in STATIC_CSS


def test_failed_snapshot_retains_last_progress_and_uses_safe_error() -> None:
    app = _running_app(
        _snapshot(
            status="failed",
            errors=[
                {
                    "error_type": "ui.research.failed",
                    "source": "ui",
                    "message": "Research run failed unexpectedly.",
                    "recoverable": False,
                    "details": {"diagnostic": "SECRET-STACK-TRACE"},
                }
            ],
            sub_topics=[
                UiSubTopicProgress(
                    index=2,
                    title="Last active topic",
                    status="running",
                )
            ],
            recent_activity=[
                UiRecentActivity(event_type="last", summary="Last known activity")
            ],
        )
    )
    visible = _visible_main_text(app)

    assert "Failed" in visible
    assert "Last active topic" in visible
    assert "Last known activity" in visible
    assert "Research run failed unexpectedly." in visible
    assert "SECRET-STACK-TRACE" not in visible


def test_failed_partial_snapshot_retains_report_path_and_safe_error() -> None:
    app = _running_app(
        _snapshot(
            status="failed",
            report="# Retained partial report\n\nLast verified findings.",
            report_path="reports/retained-partial.md",
            errors=[
                {
                    "error_type": "ui.research.failed",
                    "source": "ui",
                    "message": "Research run failed unexpectedly.",
                    "recoverable": False,
                    "details": {"diagnostic": "SECRET-FAILED-REPORT"},
                }
            ],
        )
    )
    visible = _visible_main_text(app)

    assert "RESEARCH FAILED" in visible
    assert "Failed" in visible
    assert "reports/retained-partial.md" in visible
    assert "# Retained partial report" in visible
    assert "Last verified findings." in visible
    assert "Research run failed unexpectedly." in visible
    assert "SECRET-FAILED-REPORT" not in visible


def test_failed_runner_snapshot_renders_last_known_agent_and_activity(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "graph": {"max_iterations": 4},
                "output": {"directory": str(tmp_path / "output")},
            }
        ),
        encoding="utf-8",
    )
    runner = FailingSyncRunner(
        RuntimeError("private provider failure"),
        events=[
            ResearchEvent(
                event_type="planner.planning.completed",
                source="test",
                message="Planning complete.",
                metadata={"sub_topic_count": 5},
            ),
            ResearchEvent(
                event_type="graph.node.started",
                source="test",
                message="Researcher started.",
                metadata={"node": "researcher", "iteration": 2},
            ),
            ResearchEvent(
                event_type="researcher.sub_topic.started",
                source="test",
                message="Subtopic started.",
                metadata={"index": 2, "sub_topic": "Last active topic"},
            ),
        ],
    )
    controller = LocalResearchController(
        config_path=str(config_path),
        runner=runner,
        preflight=lambda **_: object(),
    )
    snapshot = controller.start(question="Question", max_iterations=2)
    for _ in range(100):
        if controller.snapshot(snapshot.session_id).status == "failed":
            break
        sleep(0.01)

    app = AppTest.from_function(
        render_app,
        kwargs={"controller": controller},
    ).run()
    app.session_state[_ACTIVE_SESSION_KEY] = snapshot.session_id
    app.session_state[_SELECTED_SESSION_KEY] = snapshot.session_id
    app.session_state[_VIEW_KEY] = "current"
    app.run()
    visible = _visible_main_text(app)

    assert "Researcher" in visible
    assert "Subtopic 2 of 5" in visible
    assert "Last active topic" in visible
    assert "Research run failed unexpectedly." in visible


def test_demo_controller_covers_all_offline_visual_scenarios() -> None:
    controller = DemoController()

    entries = controller.list_history()
    assert len(entries) >= 10
    assert {
        "running",
        "completed",
        "max_iterations",
        "incomplete",
        "failed",
    } <= {entry.status for entry in entries}

    running = controller.snapshot(controller.running_session_id)
    assert [topic.status for topic in running.sub_topics] == [
        "completed",
        "running",
        "queued",
        "queued",
        "queued",
    ]
    assert len(running.recent_activity) == 3
    assert running.token_usage is not None
    assert running.trace_url

    no_telemetry = controller.snapshot(controller.no_telemetry_session_id)
    assert no_telemetry.token_usage is None
    assert no_telemetry.trace_url is None

    completed = controller.snapshot(controller.completed_session_id)
    assert completed.report is not None
    assert completed.report.count("## ") >= 3
    assert "- " in completed.report
    assert "https://" in completed.report
    assert "| Scenario |" in completed.report
    assert completed.source_summary.total == 4
    assert {
        completed.source_summary.high,
        completed.source_summary.moderate,
        completed.source_summary.low,
        completed.source_summary.unrated,
    } == {1}
    assert {
        completed.fact_check_summary.verified,
        completed.fact_check_summary.unverified,
        completed.fact_check_summary.contradicted,
        completed.fact_check_summary.insufficient_evidence,
    } == {1}
    assert completed.limitations

    max_iterations = controller.snapshot(controller.max_iterations_session_id)
    failed_partial = controller.snapshot(controller.failed_partial_session_id)
    configuration_error_snapshot = controller.snapshot(
        controller.configuration_error_session_id
    )
    assert max_iterations.status == "max_iterations"
    assert max_iterations.report
    assert failed_partial.status == "failed"
    assert failed_partial.report
    assert failed_partial.errors
    assert configuration_error_snapshot.status == "failed"
    assert configuration_error_snapshot.report is None
    assert configuration_error_snapshot.errors[0].details["reason"] == (
        "missing_secrets"
    )


def test_demo_controller_start_is_deterministic_and_stays_offline() -> None:
    controller = DemoController()

    first = controller.start(
        question="A deterministic question",
        max_iterations=4,
    )
    second = controller.start(
        question="A deterministic question",
        max_iterations=4,
    )

    assert first.session_id == second.session_id
    assert first.status == "running"
    assert controller.start_calls == [
        {
            "question": "A deterministic question",
            "max_iterations": 4,
            "output_format": "markdown",
        },
        {
            "question": "A deterministic question",
            "max_iterations": 4,
            "output_format": "markdown",
        },
    ]


def test_manual_mock_app_exposes_only_development_state_selector() -> None:
    app = AppTest.from_file(Path(__file__).with_name("manual_mock_app.py")).run()

    selectors = [item for item in app.selectbox if item.label == "Demo state"]
    assert len(selectors) == 1
    assert selectors[0].options == [
        "New",
        "Running",
        "Completed",
        "History",
        "Max iterations",
        "Failed/partial",
    ]
    assert "Demo state" not in _visible_main_text(app)


def test_manual_mock_app_renders_each_selectable_offline_state() -> None:
    app = AppTest.from_file(Path(__file__).with_name("manual_mock_app.py")).run()
    expected_headings = {
        "New": "What would you like to research?",
        "Running": "RESEARCH IN PROGRESS",
        "Completed": "RESEARCH COMPLETED",
        "History": "Research sessions",
        "Max iterations": "RESEARCH PAUSED",
        "Failed/partial": "RESEARCH FAILED",
    }

    for mode, heading in expected_headings.items():
        next(item for item in app.selectbox if item.label == "Demo state").set_value(
            mode
        ).run()
        assert heading in _visible_main_text(app)


def test_demo_app_flow_researches_completes_and_reopens_history() -> None:
    controller = DemoController()
    app = AppTest.from_function(
        render_app,
        kwargs={"controller": controller},
    ).run()

    assert "What would you like to research?" in _visible_main_text(app)
    app.text_area(key="research_question").set_value(
        "How will grid-scale batteries reshape energy markets by 2030?"
    ).run()
    app.button(key="start_research").click().run()

    started_id = app.session_state[_ACTIVE_SESSION_KEY]
    assert started_id == controller.started_session_id
    assert app.session_state[_VIEW_KEY] == "current"
    assert "RESEARCH IN PROGRESS" in _visible_main_text(app)
    assert "Researcher" in _visible_main_text(app)

    controller.complete_started_session()
    app.run()
    completed_text = _visible_main_text(app)
    assert app.session_state[_VIEW_KEY] == "current"
    assert "RESEARCH COMPLETED" in completed_text
    assert "# Executive Summary" in completed_text

    app.button(key="session_history").click().run()
    assert app.session_state[_VIEW_KEY] == "history"
    assert "Research sessions" in _visible_main_text(app)
    app.button(key=f"history_open_{started_id}").click().run()

    assert app.session_state[_VIEW_KEY] == "current"
    assert app.session_state[_SELECTED_SESSION_KEY] == started_id
    reopened_text = _visible_main_text(app)
    assert "RESEARCH COMPLETED" in reopened_text
    assert "# Executive Summary" in reopened_text


def test_demo_app_exposes_accessible_status_words_and_no_hidden_report_tab() -> None:
    controller = DemoController()
    app = AppTest.from_function(
        render_app,
        kwargs={"controller": controller},
    ).run()

    app.session_state[_ACTIVE_SESSION_KEY] = controller.running_session_id
    app.session_state[_SELECTED_SESSION_KEY] = controller.running_session_id
    app.session_state[_VIEW_KEY] = "current"
    app.run()
    running_text = _visible_main_text(app)

    assert "How will grid-scale batteries reshape energy markets by 2030?" in (
        running_text
    )
    assert all(
        status in running_text
        for status in ("Running", "Complete", "Active", "Queued")
    )
    assert any(
        button.label == "Open LangSmith trace"
        for button in app.main.get("link_button")
    )
    assert "Overall progress" not in running_text
    assert re.search(r"\bETA\b", running_text) is None
    activity_summaries = (
        "Completed subtopic 1",
        "Started subtopic 2",
        "Evaluated 4 new sources",
    )
    assert sum(summary in running_text for summary in activity_summaries) == 3

    app.session_state[_SELECTED_SESSION_KEY] = controller.completed_session_id
    app.session_state[_ACTIVE_SESSION_KEY] = None
    app.run()
    completed_text = _visible_main_text(app)
    assert any(
        button.label == "Open LangSmith trace"
        for button in app.main.get("link_button")
    )
    assert "SOURCE CREDIBILITY" in completed_text
    assert "FACT-CHECK SUMMARY" in completed_text
    assert "LIMITATIONS" in completed_text
    assert "EXECUTION ERRORS" not in completed_text
    assert len(app.tabs) == 0
