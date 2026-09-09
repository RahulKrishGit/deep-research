"""Reusable native Streamlit components for the persistent app shell."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from datetime import datetime, timezone
from html import escape
from typing import TYPE_CHECKING, Any

import streamlit as st

from deep_research.runtime.errors import (
    CONFIGURATION_HINTS,
    ResearchConfigurationError,
)
from deep_research.ui.models import (
    SessionHistoryEntry,
    UiSessionSnapshot,
    history_entry_from_snapshot,
)

if TYPE_CHECKING:
    from deep_research.ui.runner import LocalResearchController


_VIEW_KEY = "_deep_research_view"
_ACTIVE_SESSION_KEY = "_deep_research_active_session_id"
_SELECTED_SESSION_KEY = "_deep_research_selected_session_id"
_START_ERROR_KEY = "_deep_research_start_error"
_START_IN_FLIGHT_KEY = "_deep_research_start_in_flight"

_CONFIGURATION_FAILURE_MESSAGE = "Research service configuration is unavailable."
_FALLBACK_CONFIGURATION_HINT = "Review the research configuration and try again."
_UNEXPECTED_START_MESSAGE = (
    "Research could not be started. Review the form and try again."
)

_STATUS_PRESENTATION: Mapping[str, tuple[str, str, str]] = {
    "ready": ("●", "Ready to start", "neutral"),
    "running": ("◌", "Running", "running"),
    "completed": ("✓", "Completed", "completed"),
    "max_iterations": ("▲", "Max iterations", "max-iterations"),
    "incomplete": ("Ⅱ", "Incomplete", "incomplete"),
    "failed": ("×", "Failed", "failed"),
}


def status_presentation(status: str) -> tuple[str, str, str]:
    """Return the explicit shape, label, and semantic class for a status."""
    return _STATUS_PRESENTATION.get(status, ("•", "Unknown status", "neutral"))


def render_status(status: str, *, label: str | None = None) -> None:
    """Render a status with both visible wording and a non-color cue."""
    icon, default_label, css_name = status_presentation(status)
    text = default_label if label is None else label
    st.markdown(
        (
            f'<span class="dr-status dr-status--{css_name}">'
            f'<span aria-hidden="true">{icon}</span> {text}</span>'
        ),
        unsafe_allow_html=True,
    )


def _set_view(view: str, state: MutableMapping[str, Any]) -> None:
    state[_VIEW_KEY] = view


def _select_session(session_id: str, state: MutableMapping[str, Any]) -> None:
    state[_SELECTED_SESSION_KEY] = session_id
    _set_view("current", state)


def _entry_for_active_session(
    controller: LocalResearchController,
    state: MutableMapping[str, Any],
) -> SessionHistoryEntry | None:
    active_id = state.get(_ACTIVE_SESSION_KEY)
    if not isinstance(active_id, str):
        return None
    try:
        snapshot = controller.snapshot(active_id)
    except (AttributeError, KeyError):
        return None
    return history_entry_from_snapshot(snapshot)


def _recent_entries(
    controller: LocalResearchController,
    state: MutableMapping[str, Any],
) -> list[SessionHistoryEntry]:
    entries = list(controller.list_history(limit=50))
    active_entry = _entry_for_active_session(controller, state)
    if active_entry is not None:
        entries = [
            entry for entry in entries if entry.session_id != active_entry.session_id
        ]
        entries.insert(0, active_entry)
    return entries[:5]


def _session_date(entry: SessionHistoryEntry) -> str:
    """Format compact sidebar metadata without exposing a locale dependency."""
    started_at = entry.started_at
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    if started_at.astimezone(timezone.utc).date() == datetime.now(timezone.utc).date():
        return "Today"
    return f"{started_at:%b} {started_at.day}"


def _render_recent_row(
    entry: SessionHistoryEntry,
    *,
    selected_id: str | None,
    state: MutableMapping[str, Any],
) -> None:
    row_key = (
        f"dr-session-row-selected-{entry.session_id}"
        if entry.session_id == selected_id
        else f"dr-session-row-{entry.session_id}"
    )
    with st.container(key=row_key, gap="small"):
        st.markdown(
            f'<div class="dr-session-question">{escape(entry.question)}</div>',
            unsafe_allow_html=True,
        )
        status_column, action_column = st.columns(
            [1, 0.45],
            gap="small",
            vertical_alignment="center",
        )
        with status_column:
            render_status(entry.status)
            st.caption(_session_date(entry))
        with action_column:
            st.button(
                "Open",
                key=f"session_{entry.session_id}",
                type="secondary",
                use_container_width=True,
                on_click=_select_session,
                args=(entry.session_id, state),
            )


def render_sidebar(controller: LocalResearchController) -> None:
    """Render the persistent sidebar and update navigation session state."""
    state = st.session_state
    with st.sidebar:
        st.markdown(
            """
            <div class="dr-product-identity">
              <span class="dr-product-mark" aria-hidden="true">⌕</span>
              <span>
                <span class="dr-product-name">Deep Research</span><br>
                <span class="dr-product-subtitle">Multi-agent analyst</span>
              </span>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button(
            "+ New research",
            key="new_research",
            type="primary",
            use_container_width=True,
        ):
            state[_SELECTED_SESSION_KEY] = None
            state[_START_ERROR_KEY] = None
            _set_view("new", state)

        st.markdown(
            '<div class="dr-section-label">RECENT SESSIONS</div>',
            unsafe_allow_html=True,
        )
        selected_id = state.get(_SELECTED_SESSION_KEY)
        for entry in _recent_entries(controller, state):
            _render_recent_row(
                entry,
                selected_id=selected_id if isinstance(selected_id, str) else None,
                state=state,
            )

        with st.container(key="dr-sidebar-spacer"):
            st.markdown('<div aria-hidden="true"></div>', unsafe_allow_html=True)
        if st.button(
            "Session history",
            key="session_history",
            type="secondary",
            use_container_width=True,
        ):
            _set_view("history", state)


def _snapshot_for_selection(
    controller: LocalResearchController,
    state: MutableMapping[str, Any],
) -> UiSessionSnapshot | None:
    selected_id = state.get(_SELECTED_SESSION_KEY)
    if not isinstance(selected_id, str):
        return None
    try:
        return controller.snapshot(selected_id)
    except (AttributeError, KeyError):
        return None


def _configuration_error_details(
    error: ResearchConfigurationError,
) -> tuple[str, str]:
    reason = error.reason if isinstance(error.reason, str) else ""
    hint = CONFIGURATION_HINTS.get(reason, _FALLBACK_CONFIGURATION_HINT)
    return _CONFIGURATION_FAILURE_MESSAGE, hint


def _render_start_error(error_details: object) -> None:
    if not (
        isinstance(error_details, tuple)
        and len(error_details) == 2
        and all(isinstance(value, str) for value in error_details)
    ):
        return
    message, hint = error_details
    st.error(message)
    st.caption(hint)


def _start_research(
    controller: LocalResearchController,
    *,
    question: str,
    max_iterations: int,
    state: MutableMapping[str, Any],
) -> None:
    state[_START_IN_FLIGHT_KEY] = True
    try:
        snapshot = controller.start(
            question=question.strip(),
            max_iterations=max_iterations,
            output_format="markdown",
        )
    except ResearchConfigurationError as error:
        state[_START_ERROR_KEY] = _configuration_error_details(error)
        state[_START_IN_FLIGHT_KEY] = False
        return
    except Exception:
        state[_START_ERROR_KEY] = (
            _UNEXPECTED_START_MESSAGE,
            "Check the question and research settings, then try again.",
        )
        state[_START_IN_FLIGHT_KEY] = False
        return

    state[_START_ERROR_KEY] = None
    state[_START_IN_FLIGHT_KEY] = False
    state[_ACTIVE_SESSION_KEY] = snapshot.session_id
    state[_SELECTED_SESSION_KEY] = snapshot.session_id
    state[_VIEW_KEY] = "current"
    st.rerun()


def render_new_research_view(controller: LocalResearchController) -> None:
    """Render the question-first New Research screen and its start form."""
    state = st.session_state
    _render_start_error(state.get(_START_ERROR_KEY))

    with st.form("new_research_form", clear_on_submit=False):
        st.markdown(
            '<div class="dr-editorial-column dr-section-label">'
            "NEW RESEARCH SESSION</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="dr-editorial-column dr-shell-title">'
            "<h1>What would you like to research?</h1></div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="dr-editorial-column dr-shell-copy">'
            "Describe a question in plain language. The research agent will "
            "break it into subtopics, search and evaluate sources, and "
            "synthesize a long-form report.</div>",
            unsafe_allow_html=True,
        )
        question = st.text_area(
            "Research question",
            key="research_question",
            height=152,
            max_chars=500,
            placeholder=(
                "Ask a focused question with a timeframe or scope where relevant."
            ),
        )
        st.caption("Be specific. Include a timeframe or scope where relevant.")
        st.divider()

        config_left, config_right = st.columns(2, gap="large")
        with config_left:
            st.number_input(
                "Maximum iterations",
                min_value=1,
                max_value=20,
                value=controller.default_max_iterations,
                step=1,
                key="max_iterations",
                help="Macro refinement passes before the report is finalized.",
            )
        with config_right:
            st.markdown("**Output format**")
            st.markdown(
                '<div class="dr-readonly-field" aria-label="Output format: Markdown">'
                "Markdown</div>",
                unsafe_allow_html=True,
            )
            st.caption("Read-only for this local build.")

        question_is_blank = not isinstance(question, str) or not question.strip()
        start_in_flight = state.get(_START_IN_FLIGHT_KEY) is True
        action_status, action_button = st.columns(
            [1, 0.5],
            gap="large",
            vertical_alignment="bottom",
        )
        with action_status:
            render_status("ready")
            if question_is_blank:
                st.caption("Enter a research question to start.")
        with action_button:
            submitted = st.form_submit_button(
                "Start Research  →",
                key="start_research",
                type="primary",
                use_container_width=True,
                disabled=question_is_blank or start_in_flight,
            )

        if submitted:
            _start_research(
                controller,
                question=question,
                max_iterations=int(st.session_state["max_iterations"]),
                state=state,
            )
            if state.get(_VIEW_KEY) == "new":
                _render_start_error(state.get(_START_ERROR_KEY))

    st.markdown(
        '<div class="dr-shell-rule" aria-hidden="true"></div>'
        '<div class="dr-section-label">WHAT HAPPENS NEXT</div>',
        unsafe_allow_html=True,
    )
    next_steps = st.columns(3, gap="large")
    for column, title, description in zip(
        next_steps,
        ("1. Plan subtopics", "2. Search & evaluate", "3. Synthesize report"),
        (
            "The question is decomposed into focused research subtopics.",
            "Each subtopic is researched and sources are checked for credibility.",
            "Findings are merged into one long-form Markdown report with citations.",
        ),
        strict=True,
    ):
        with column:
            st.markdown(f"**{title}**")
            st.caption(description)


def render_current_session_view(controller: LocalResearchController) -> None:
    """Render the current-session shell without implementing screen details."""
    snapshot = _snapshot_for_selection(controller, st.session_state)
    if snapshot is None:
        active_id = st.session_state.get(_ACTIVE_SESSION_KEY)
        if isinstance(active_id, str):
            try:
                snapshot = controller.snapshot(active_id)
            except (AttributeError, KeyError):
                snapshot = None
    if snapshot is None:
        st.markdown(
            '<div class="dr-editorial-column dr-shell-title">'
            "<h1>Current session</h1></div>",
            unsafe_allow_html=True,
        )
        st.caption("Select a recent session to inspect it.")
        return

    st.markdown(
        '<div class="dr-editorial-column dr-shell-title">'
        f"<h1>{escape(snapshot.question)}</h1></div>",
        unsafe_allow_html=True,
    )
    render_status(snapshot.status)
    st.caption("Session detail content will appear here.")


def render_history_view(controller: LocalResearchController) -> None:
    """Render the history route scaffold; detailed archive UI belongs later."""
    del controller
    st.markdown(
        '<div class="dr-editorial-column dr-shell-title">'
        "<h1>Session history</h1></div>",
        unsafe_allow_html=True,
    )
    st.caption("Searchable session history will appear here.")


__all__ = [
    "render_current_session_view",
    "render_history_view",
    "render_new_research_view",
    "render_sidebar",
    "render_status",
    "status_presentation",
]
