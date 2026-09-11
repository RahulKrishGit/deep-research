"""Offline visual-review harness for the four-state Streamlit handoff."""

from __future__ import annotations

import streamlit as st

from deep_research.ui.app import (
    _ACTIVE_SESSION_KEY,
    _PENDING_START_KEY,
    _SELECTED_SESSION_KEY,
    _START_ERROR_KEY,
    _START_IN_FLIGHT_KEY,
    _VIEW_KEY,
    render_app,
)
from tests.test_ui.fakes import DemoController


def main() -> None:
    controller = st.session_state.setdefault("_demo_controller", DemoController())
    mode = st.sidebar.selectbox(
        "Demo state",
        options=list(controller.modes),
        help="Development-only selector; all data is deterministic and offline.",
    )

    if mode in {"New", "Starting", "History"}:
        st.session_state[_VIEW_KEY] = "history" if mode == "History" else "new"
        st.session_state[_ACTIVE_SESSION_KEY] = None
        st.session_state[_START_IN_FLIGHT_KEY] = mode == "Starting"
        st.session_state[_START_ERROR_KEY] = None
        st.session_state[_PENDING_START_KEY] = None
        if mode in {"New", "Starting"}:
            st.session_state[_SELECTED_SESSION_KEY] = None
    else:
        st.session_state[_START_IN_FLIGHT_KEY] = False
        st.session_state[_START_ERROR_KEY] = None
        st.session_state[_PENDING_START_KEY] = None
        session_id = controller.session_id_for_mode(mode)
        if session_id is None:
            raise ValueError(f"Unknown demo mode: {mode}")
        st.session_state[_VIEW_KEY] = "current"
        st.session_state[_ACTIVE_SESSION_KEY] = (
            session_id if mode == "Running" else None
        )
        st.session_state[_SELECTED_SESSION_KEY] = session_id

    render_app(controller)


if __name__ == "__main__":
    main()
