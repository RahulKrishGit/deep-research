"""Offline AppTests for the persistent Streamlit shell and router."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from streamlit.testing.v1 import AppTest

from deep_research.runtime.errors import configuration_error
from deep_research.ui.app import (
    _ACTIVE_SESSION_KEY,
    _CONTROLLER_KEY,
    _SELECTED_SESSION_KEY,
    _START_ERROR_KEY,
    _VIEW_KEY,
    render_app,
)
from deep_research.ui.components import _start_research
from deep_research.ui.models import (
    SessionHistoryEntry,
    UiFactCheckSummary,
    UiRecentActivity,
    UiSessionSnapshot,
    UiSourceSummary,
    UiSubTopicProgress,
    UiTokenUsage,
    UiToolCallSummary,
)


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

    def list_history(self, *, limit: int = 50) -> list[SessionHistoryEntry]:
        return self.entries[:limit]

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


def _button_values(app: AppTest) -> list[str]:
    return [button.label for button in app.button]


def _snapshot(
    *,
    status: str = "running",
    token_usage: UiTokenUsage | None = None,
    trace_url: str | None = None,
    sub_topics: list[UiSubTopicProgress] | None = None,
    recent_activity: list[UiRecentActivity] | None = None,
    errors: list[object] | None = None,
    report: str | None = None,
) -> UiSessionSnapshot:
    from deep_research.utils.types import ResearchError

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
        current_agent="researcher",
        iteration=2,
        max_iterations=4,
        sub_topics=sub_topics or [],
        recent_activity=recent_activity or [],
        tool_calls=[
            UiToolCallSummary(
                tool_name="web_search",
                display_label="Web search",
                calls=2,
                failures=0,
            )
        ],
        token_usage=token_usage,
        trace_url=trace_url,
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
    )


def _running_app(snapshot: UiSessionSnapshot) -> AppTest:
    app = _app(
        [_entry(snapshot.session_id, snapshot.question, snapshot.status, 0)]
    ).run()
    controller = app.session_state[_CONTROLLER_KEY]
    controller.started_snapshots[snapshot.session_id] = snapshot
    app.session_state[_SELECTED_SESSION_KEY] = snapshot.session_id
    app.session_state[_VIEW_KEY] = "current"
    app.run()
    return app


def _visible_main_text(app: AppTest) -> str:
    return "\n".join(
        [item.value for item in app.main.markdown if "<style>" not in item.value]
        + [item.value for item in app.main.caption]
        + [item.value for item in app.main.error]
        + [item.value for item in app.main.warning]
    )


def test_new_research_screen_has_question_form_and_ready_state() -> None:
    app = _app([]).run()

    assert app.text_area(key="research_question").label == "Research question"
    assert app.number_input(key="max_iterations").label == "Maximum iterations"
    assert any("Markdown" in item.value for item in app.main.markdown)
    assert any("Start Research" in value for value in _button_values(app))
    assert any("Ready to start" in item.value for item in app.main.markdown)
    assert not any("Session history" in item.value for item in app.main.markdown)
    assert not any("Current session" in item.value for item in app.main.markdown)


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


def test_unexpected_start_error_surfaces_without_form_guidance() -> None:
    controller = FakeController(
        [],
        start_error=RuntimeError("unexpected persistence failure"),
    )
    state: dict[str, object] = {_START_ERROR_KEY: None}

    with pytest.raises(RuntimeError, match="unexpected persistence failure"):
        _start_research(
            controller,
            question="A valid question",
            max_iterations=4,
            state=state,
        )

    assert state[_START_ERROR_KEY] is None


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
    assert any("Running" in item.value for item in app.markdown)


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


def test_running_rerun_does_not_start_a_duplicate_worker() -> None:
    app = _app([]).run()
    app.text_area(key="research_question").set_value("A valid question").run()
    app.button(key="start_research").click().run()

    controller = app.session_state[_CONTROLLER_KEY]
    app.run()

    assert len(controller.start_calls) == 1


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


def test_max_iterations_snapshot_has_amber_terminal_treatment() -> None:
    app = _running_app(
        _snapshot(status="max_iterations", report="Partial report.")
    )
    visible = _visible_main_text(app)

    assert "Max iterations" in visible
    assert "Partial report." in visible
    assert "Running" not in visible


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
