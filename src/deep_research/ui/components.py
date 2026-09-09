"""Reusable native Streamlit components for the persistent app shell."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from datetime import datetime, timezone
from html import escape
from typing import TYPE_CHECKING, Any

import streamlit as st

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


def render_new_research_view() -> None:
    """Render the intentionally small New Research shell placeholder."""
    st.markdown(
        '<div class="dr-editorial-column dr-shell-title"><h1>New research</h1></div>',
        unsafe_allow_html=True,
    )
    st.caption("Question and run configuration will appear here.")


def render_current_session_view(controller: LocalResearchController) -> None:
    """Render the current-session shell without implementing screen details."""
    snapshot = _snapshot_for_selection(controller, st.session_state)
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
