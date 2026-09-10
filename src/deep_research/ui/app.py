"""Streamlit entry point and the session-state-driven app router."""

from __future__ import annotations

from typing import Any

import streamlit as st

from deep_research.ui.runner import LocalResearchController

_CONTROLLER_KEY = "_deep_research_controller"
_VIEW_KEY = "_deep_research_view"
_ACTIVE_SESSION_KEY = "_deep_research_active_session_id"
_LIVE_SESSION_KEY = "_deep_research_live_session_id"
_SELECTED_SESSION_KEY = "_deep_research_selected_session_id"
_HISTORY_SEARCH_KEY = "_deep_research_history_search"
_HISTORY_FILTER_KEY = "_deep_research_history_filter"
_START_ERROR_KEY = "_deep_research_start_error"
_START_IN_FLIGHT_KEY = "_deep_research_start_in_flight"

_VALID_VIEWS = {"new", "current", "history"}


def resolve_controller(
    controller: LocalResearchController | None = None,
) -> LocalResearchController:
    """Keep one supplied/default controller in this Streamlit session only."""
    if controller is not None:
        st.session_state[_CONTROLLER_KEY] = controller
        return controller

    existing = st.session_state.get(_CONTROLLER_KEY)
    if existing is None:
        existing = LocalResearchController()
        st.session_state[_CONTROLLER_KEY] = existing
    return existing


def _initialize_session_state() -> None:
    defaults: dict[str, Any] = {
        _VIEW_KEY: "new",
        _ACTIVE_SESSION_KEY: None,
        _LIVE_SESSION_KEY: None,
        _SELECTED_SESSION_KEY: None,
        _HISTORY_SEARCH_KEY: "",
        _HISTORY_FILTER_KEY: "All",
        _START_ERROR_KEY: None,
        _START_IN_FLIGHT_KEY: False,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _current_view() -> str:
    view = st.session_state.get(_VIEW_KEY, "new")
    if view not in _VALID_VIEWS:
        st.session_state[_VIEW_KEY] = "new"
        return "new"
    return view


@st.fragment(run_every="2s")
def render_live_progress(controller: LocalResearchController) -> None:
    """Refresh only the active research region on the two-second cadence."""
    # `_ACTIVE_SESSION_KEY` is lifecycle metadata for the most recently
    # started session.  `_LIVE_SESSION_KEY` is the independently selectable
    # session currently being polled by this fragment.  The fallback keeps
    # direct callers that predate the separate key working until their first
    # render initializes it.
    if _LIVE_SESSION_KEY in st.session_state:
        session_id = st.session_state.get(_LIVE_SESSION_KEY)
    else:
        session_id = st.session_state.get(_ACTIVE_SESSION_KEY)
    if not isinstance(session_id, str):
        return
    snapshot = controller.snapshot(session_id)
    from deep_research.ui.components import (
        _render_running_snapshot,
        render_sidebar_status,
    )

    render_sidebar_status(snapshot)

    if snapshot.status == "running":
        st.session_state.setdefault(_LIVE_SESSION_KEY, session_id)
        _render_running_snapshot(snapshot)
    else:
        # A terminal snapshot must leave the recurring fragment.  Clearing the
        # target is the idempotence guard: the app-level rerun below renders the
        # selected terminal snapshot through the stable route exactly once.
        st.session_state[_LIVE_SESSION_KEY] = None
        st.rerun()
        return


def render_app(controller=None) -> None:
    """Apply the shell, render navigation, and route among the three views."""
    # AppTest.from_function executes the supplied function in a small isolated
    # script. Keep these imports local so the public entry point works there as
    # well as when Streamlit runs this module as an application.
    import streamlit as st

    from deep_research.ui.app import (
        _current_view,
        _initialize_session_state,
        resolve_controller,
    )
    from deep_research.ui.components import (
        REPORT_CSS,
        render_current_session_view,
        render_history_view,
        render_new_research_view,
        render_sidebar,
    )
    from deep_research.ui.styles import STATIC_CSS

    resolved_controller = resolve_controller(controller)
    _initialize_session_state()
    st.markdown(
        STATIC_CSS.replace("</style>", f"{REPORT_CSS}</style>", 1),
        unsafe_allow_html=True,
    )
    render_sidebar(resolved_controller)

    view = _current_view()
    if view == "history":
        render_history_view(resolved_controller)
    elif view == "current":
        render_current_session_view(resolved_controller)
    else:
        render_new_research_view(resolved_controller)


if __name__ == "__main__":
    render_app()


__all__ = [
    "_ACTIVE_SESSION_KEY",
    "_CONTROLLER_KEY",
    "_HISTORY_FILTER_KEY",
    "_HISTORY_SEARCH_KEY",
    "_LIVE_SESSION_KEY",
    "_SELECTED_SESSION_KEY",
    "_START_ERROR_KEY",
    "_START_IN_FLIGHT_KEY",
    "_VIEW_KEY",
    "render_live_progress",
    "render_app",
    "resolve_controller",
]
