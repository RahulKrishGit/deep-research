"""Offline AppTests for the persistent Streamlit shell and router."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from streamlit.testing.v1 import AppTest

from deep_research.ui.app import (
    _ACTIVE_SESSION_KEY,
    _SELECTED_SESSION_KEY,
    _VIEW_KEY,
    render_app,
)
from deep_research.ui.models import (
    SessionHistoryEntry,
    UiFactCheckSummary,
    UiSessionSnapshot,
    UiSourceSummary,
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
    def __init__(self, entries: list[SessionHistoryEntry]) -> None:
        self.entries = entries

    def list_history(self, *, limit: int = 50) -> list[SessionHistoryEntry]:
        return self.entries[:limit]

    def history_entry(self, session_id: str) -> SessionHistoryEntry | None:
        return next(
            (entry for entry in self.entries if entry.session_id == session_id),
            None,
        )

    def snapshot(self, session_id: str) -> UiSessionSnapshot:
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


def _app(entries: list[SessionHistoryEntry]) -> AppTest:
    return AppTest.from_function(
        render_app,
        kwargs={"controller": FakeController(entries)},
    )


def _button_values(app: AppTest) -> list[str]:
    return [button.label for button in app.button]


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
