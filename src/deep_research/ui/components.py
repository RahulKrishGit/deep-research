"""Reusable native Streamlit components for the persistent app shell."""

from __future__ import annotations

import inspect
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
    UiClaimDetail,
    UiSessionSnapshot,
    UiSourceDetail,
    UiSubTopicProgress,
    history_entry_from_snapshot,
)
from deep_research.ui.progress import display_agent_action, display_agent_name
from deep_research.ui.styles import COLORS, RADII, SPACING

if TYPE_CHECKING:
    from deep_research.ui.runner import LocalResearchController


_VIEW_KEY = "_deep_research_view"
_ACTIVE_SESSION_KEY = "_deep_research_active_session_id"
_SELECTED_SESSION_KEY = "_deep_research_selected_session_id"
_START_ERROR_KEY = "_deep_research_start_error"
_START_IN_FLIGHT_KEY = "_deep_research_start_in_flight"
_SIDEBAR_STATUS_PLACEHOLDER_KEY = "_deep_research_sidebar_status_placeholder"

_CONFIGURATION_FAILURE_MESSAGE = "Research service configuration is unavailable."
_HISTORY_FAILURE_MESSAGE = (
    "Research session could not be started because local history is unavailable."
)
_FALLBACK_CONFIGURATION_HINT = "Review the research configuration and try again."
_START_VALIDATION_MESSAGE = "Research request could not be started."
_START_VALIDATION_HINT = "Check the research question and settings, then try again."
_START_FAILURE_MESSAGE = "Research session could not be started."
_START_FAILURE_HINT = "Check the local output directory and try again."
_HISTORY_FILTER_OPTIONS = ("All", "Running", "Completed", "Issues")
_HISTORY_ISSUE_STATUSES = frozenset(
    {"max_iterations", "incomplete", "failed"}
)
_HISTORY_SEARCH_KEY = "_deep_research_history_search"
_HISTORY_FILTER_KEY = "_deep_research_history_filter"

_STATUS_PRESENTATION: Mapping[str, tuple[str, str, str]] = {
    "ready": ("●", "Ready to start", "neutral"),
    "running": ("◌", "Running", "running"),
    "completed": ("✓", "Completed", "completed"),
    "max_iterations": ("▲", "Max iterations", "max-iterations"),
    "incomplete": ("Ⅱ", "Incomplete", "incomplete"),
    "failed": ("×", "Failed", "failed"),
}

REPORT_CSS = """
[data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3,
[data-testid="stMarkdownContainer"] h4 {
  color: var(--dr-text);
  font-family: Georgia, "Times New Roman", serif !important;
  font-weight: 600 !important;
  letter-spacing: -0.02em !important;
}

[data-testid="stMainBlockContainer"] hr {
  margin: 4px 0 !important;
}
"""


def _st_container(
    *,
    key: str | None = None,
    gap: str | None = None,
    border: bool | None = None,
) -> Any:
    """Call ``st.container`` with only the keywords this Streamlit supports.

    The UI keeps the declared ``streamlit>=1.37`` contract.  ``key`` and
    ``gap`` were added to ``st.container`` after that floor, so they are
    retained on newer runtimes but omitted when the installed API does not
    expose them.
    """
    try:
        parameters = inspect.signature(st.container).parameters
    except (TypeError, ValueError):
        parameters = {}
    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    values = {"key": key, "gap": gap, "border": border}
    kwargs = {
        name: value
        for name, value in values.items()
        if value is not None and (accepts_kwargs or name in parameters)
    }
    return st.container(**kwargs)


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


def _history_entry_for_display(
    entry: SessionHistoryEntry,
    state: Mapping[str, Any],
) -> SessionHistoryEntry:
    """Normalize persisted running metadata unless it is live in this session."""
    active_id = state.get(_ACTIVE_SESSION_KEY)
    if entry.status == "running" and entry.session_id != active_id:
        return entry.model_copy(update={"status": "incomplete"})
    return entry.model_copy(deep=True)


def _history_sort_key(entry: SessionHistoryEntry) -> datetime:
    started_at = entry.started_at
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        return started_at.replace(tzinfo=timezone.utc)
    return started_at.astimezone(timezone.utc)


def _history_entries(
    controller: LocalResearchController,
    state: MutableMapping[str, Any],
) -> list[SessionHistoryEntry]:
    entries = [
        _history_entry_for_display(entry, state)
        for entry in controller.list_history(limit=50)
    ]
    return sorted(entries, key=_history_sort_key, reverse=True)


def _recent_entries(
    controller: LocalResearchController,
    state: MutableMapping[str, Any],
) -> list[SessionHistoryEntry]:
    entries = _history_entries(controller, state)
    active_entry = _entry_for_active_session(controller, state)
    if active_entry is not None:
        active_entry = _history_entry_for_display(active_entry, state)
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
    with _st_container(key=row_key, gap="small"):
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
            if entry.session_id == state.get(_ACTIVE_SESSION_KEY):
                placeholder = st.empty()
                state[_SIDEBAR_STATUS_PLACEHOLDER_KEY] = placeholder
                with placeholder.container():
                    render_status(entry.status)
                    st.caption(_session_date(entry))
            else:
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
            type="secondary",
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

        with _st_container(key="dr-sidebar-spacer"):
            st.markdown('<div aria-hidden="true"></div>', unsafe_allow_html=True)
        if st.button(
            "Session history",
            key="session_history",
            type="secondary",
            use_container_width=True,
        ):
            _set_view("history", state)


def render_sidebar_status(snapshot: UiSessionSnapshot) -> None:
    """Update the active row's status from the live fragment only."""
    placeholder = st.session_state.get(_SIDEBAR_STATUS_PLACEHOLDER_KEY)
    if placeholder is None:
        return
    entry = history_entry_from_snapshot(snapshot)
    with placeholder.container():
        render_status(entry.status)
        st.caption(_session_date(entry))


def _snapshot_for_selection(
    controller: LocalResearchController,
    state: MutableMapping[str, Any],
) -> UiSessionSnapshot | None:
    selected_id = state.get(_SELECTED_SESSION_KEY)
    if not isinstance(selected_id, str):
        return None
    active_id = state.get(_ACTIVE_SESSION_KEY)
    try:
        snapshot = controller.snapshot(selected_id)
    except (AttributeError, KeyError):
        snapshot = None

    if snapshot is not None and (
        selected_id == active_id or snapshot.status != "running"
    ):
        if snapshot.report is None and snapshot.status != "running":
            report = _read_history_report(controller, selected_id)
            if report is not None:
                snapshot = snapshot.model_copy(update={"report": report})
        return snapshot

    entry = _history_entry(controller, selected_id, state)
    if entry is None:
        return None
    report = _read_history_report(controller, selected_id, entry=entry)
    return UiSessionSnapshot(
        session_id=entry.session_id,
        question=entry.question,
        status=entry.status,
        started_at=entry.started_at,
        finished_at=entry.finished_at,
        iteration=entry.iteration,
        max_iterations=entry.max_iterations,
        report_path=entry.report_path,
        report=report,
        source_summary=entry.source_summary,
        fact_check_summary=entry.fact_check_summary,
        errors=entry.errors,
        limitations=entry.limitations,
    )


def _history_entry(
    controller: LocalResearchController,
    session_id: str,
    state: Mapping[str, Any],
) -> SessionHistoryEntry | None:
    try:
        entry = controller.history_entry(session_id)
    except (AttributeError, KeyError):
        return None
    if entry is None:
        return None
    return _history_entry_for_display(entry, state)


def _read_history_report(
    controller: LocalResearchController,
    session_id: str,
    *,
    entry: SessionHistoryEntry | None = None,
) -> str | None:
    if entry is None:
        try:
            entry = controller.history_entry(session_id)
        except (AttributeError, KeyError):
            return None
    if entry is None:
        return None
    reader = getattr(controller, "read_history_report", None)
    if not callable(reader):
        return None
    try:
        report = reader(entry)
    except (AttributeError, OSError, ValueError):
        return None
    return report if isinstance(report, str) else None


def _configuration_error_details(
    error: ResearchConfigurationError,
) -> tuple[str, str]:
    reason = error.reason if isinstance(error.reason, str) else ""
    hint = CONFIGURATION_HINTS.get(reason, _FALLBACK_CONFIGURATION_HINT)
    message = (
        _HISTORY_FAILURE_MESSAGE
        if reason == "history_unavailable"
        else _CONFIGURATION_FAILURE_MESSAGE
    )
    return message, hint


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
        return
    except ValueError:
        state[_START_ERROR_KEY] = (
            _START_VALIDATION_MESSAGE,
            _START_VALIDATION_HINT,
        )
        return
    except Exception:
        state[_START_ERROR_KEY] = (
            _START_FAILURE_MESSAGE,
            _START_FAILURE_HINT,
        )
        return
    finally:
        state[_START_IN_FLIGHT_KEY] = False

    state[_START_ERROR_KEY] = None
    state[_ACTIVE_SESSION_KEY] = snapshot.session_id
    state[_SELECTED_SESSION_KEY] = snapshot.session_id
    state[_VIEW_KEY] = "current"
    st.rerun()


def render_new_research_view(controller: LocalResearchController) -> None:
    """Render the question-first New Research screen and its start form."""
    state = st.session_state
    error_details = state.get(_START_ERROR_KEY)
    if error_details is None:
        startup_error = getattr(controller, "startup_error", None)
        if isinstance(startup_error, ResearchConfigurationError):
            error_details = _configuration_error_details(startup_error)
    _render_start_error(error_details)

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


def _format_token_total(total: int) -> str:
    if total >= 1000:
        return f"{total / 1000:.1f}k"
    return f"{total:,}"


def _valid_trace_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    url = value.strip()
    if not url.startswith(("https://", "http://")):
        return None
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if not parts.netloc:
        return None
    return url


def _active_subtopic(snapshot: UiSessionSnapshot) -> UiSubTopicProgress | None:
    return next(
        (topic for topic in snapshot.sub_topics if topic.status == "running"),
        None,
    )


def _render_health(snapshot: UiSessionSnapshot) -> None:
    unrecoverable = any(not error.recoverable for error in snapshot.errors)
    if snapshot.status == "failed" or unrecoverable:
        st.markdown(
            '<span class="dr-status dr-status--failed">'
            '<span aria-hidden="true">×</span> Research stopped with an error'
            "</span>",
            unsafe_allow_html=True,
        )
        return
    issue_count = len(snapshot.errors)
    issue_count += sum(call.failures for call in snapshot.tool_calls)
    if issue_count:
        noun = "issue" if issue_count == 1 else "issues"
        st.caption(f"{issue_count} {noun}; continuing")
        return
    st.markdown(
        '<span class="dr-status dr-status--completed">'
        '<span aria-hidden="true">✓</span> No issues detected'
        "</span>",
        unsafe_allow_html=True,
    )


def _render_current_activity(snapshot: UiSessionSnapshot) -> None:
    active = _active_subtopic(snapshot)
    agent = display_agent_name(snapshot.current_agent)
    if active is None or snapshot.current_agent != "researcher":
        st.markdown(f"**{agent}**")
        st.caption(display_agent_action(snapshot.current_agent))
    else:
        total = snapshot.planned_sub_topic_count
        subtopic_label = (
            f"Subtopic {active.index} of {total}"
            if total
            else f"Subtopic {active.index}"
        )
        st.markdown(
            f"**{agent}**  →  {subtopic_label}  →  "
            f"{display_agent_action(snapshot.current_agent)}"
        )
    st.caption(
        f"Macro iteration {snapshot.iteration} of {snapshot.max_iterations}"
    )

    total = snapshot.planned_sub_topic_count
    indexes = {topic.index for topic in snapshot.sub_topics}
    has_complete_sequence = indexes == set(range(1, total + 1))
    completed = sum(topic.status == "completed" for topic in snapshot.sub_topics)
    if total and has_complete_sequence and completed:
        fraction = completed / total
        percentage = round(fraction * 100)
        st.progress(fraction, text="Phase progress")
        st.caption(f"Phase progress · {completed} of {total} subtopics · {percentage}%")
    _render_health(snapshot)


def _render_subtopic_sequence(snapshot: UiSessionSnapshot) -> None:
    st.markdown(
        '<div class="dr-editorial-column dr-section-label">SUBTOPIC SEQUENCE</div>',
        unsafe_allow_html=True,
    )
    if (
        snapshot.planned_sub_topic_count
        and len(snapshot.sub_topics) != snapshot.planned_sub_topic_count
    ):
        st.caption(
            f"{len(snapshot.sub_topics)} known of "
            f"{snapshot.planned_sub_topic_count} planned subtopics"
        )
    for topic in snapshot.sub_topics:
        icon, label = {
            "completed": ("✓", "Complete"),
            "running": ("⌕", "Active"),
            "queued": ("○", "Queued"),
        }[topic.status]
        row_class = f"dr-subtopic-row dr-subtopic-row--{topic.status}"
        row_style = ""
        if topic.status == "running":
            row_style = (
                f' style="background: {COLORS["active_tint"]}; '
                f"border-radius: {RADII['container']}; "
                f"padding: {SPACING['3']} {SPACING['2']};"
                '"'
            )
        st.markdown(
            f'<div class="{row_class}"{row_style}>'
            f'<span class="dr-subtopic-icon" aria-hidden="true">{icon}</span>'
            f'<span class="dr-subtopic-title">{escape(topic.title)}</span>'
            f'<span class="dr-subtopic-status">{label}</span>'
            "</div>",
            unsafe_allow_html=True,
        )


def _render_recent_activity(snapshot: UiSessionSnapshot) -> None:
    st.markdown(
        '<div class="dr-editorial-column dr-section-label">RECENT ACTIVITY</div>',
        unsafe_allow_html=True,
    )
    for index, activity in enumerate(snapshot.recent_activity[-3:]):
        with _st_container(key=f"dr-recent-activity-{index}", gap="small"):
            st.markdown(f"◷ {escape(activity.summary)}")


def _display_tool_label(tool_name: str, display_label: str) -> str:
    labels = {
        "document_reader": "Reviewed documents",
        "web_search": "Searched sources",
        "web_scraper": "Reviewed sources",
        "query_memory": "Checked research memory",
        "save_to_memory": "Saved research context",
    }
    return labels.get(tool_name, display_label)


def _render_details_rail(snapshot: UiSessionSnapshot) -> None:
    if snapshot.token_usage is not None:
        st.metric(
            "Tokens used",
            _format_token_total(snapshot.token_usage.total_tokens),
        )
    trace_url = _valid_trace_url(snapshot.trace_url)
    if trace_url is not None:
        st.link_button("Open LangSmith trace", trace_url, use_container_width=True)

    with st.expander("Tool activity", expanded=False):
        for call in snapshot.tool_calls:
            label = _display_tool_label(call.tool_name, call.display_label)
            suffix = f" · {call.failures} issues" if call.failures else ""
            st.caption(f"{label} · {call.calls} calls{suffix}")
        if not snapshot.tool_calls:
            st.caption("No tool activity recorded yet.")

    with st.expander("Agent details", expanded=False):
        st.caption(f"Current agent · {display_agent_name(snapshot.current_agent)}")
        st.caption(
            f"Macro iteration · {snapshot.iteration} of {snapshot.max_iterations}"
        )


def _render_running_snapshot(snapshot: UiSessionSnapshot) -> None:
    main_column, details_column = st.columns([3, 1], gap="large")
    with main_column:
        st.markdown(
            '<div class="dr-editorial-column dr-section-label">'
            "RESEARCH IN PROGRESS</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="dr-editorial-column dr-shell-title">'
            f"<h1>{escape(snapshot.question)}</h1></div>",
            unsafe_allow_html=True,
        )
        render_status("running")
        st.caption(f"Markdown  ·  Max {snapshot.max_iterations} iterations")
        with _st_container(border=True, key="dr-current-activity"):
            _render_current_activity(snapshot)
        _render_subtopic_sequence(snapshot)
        _render_recent_activity(snapshot)
    with details_column:
        st.markdown(
            '<div class="dr-section-label">SESSION DETAILS</div>',
            unsafe_allow_html=True,
        )
        _render_details_rail(snapshot)


def _safe_failure_message(snapshot: UiSessionSnapshot) -> str:
    if any(
        error.error_type == "ui.research.configuration_error"
        for error in snapshot.errors
    ):
        return _CONFIGURATION_FAILURE_MESSAGE
    return "Research run failed unexpectedly."


def _render_terminal_snapshot(snapshot: UiSessionSnapshot) -> None:
    has_retained_report = (
        snapshot.report is not None or snapshot.report_path is not None
    )
    if snapshot.status in {"completed", "max_iterations", "incomplete"} or (
        snapshot.status == "failed" and has_retained_report
    ):
        _render_completed_snapshot(snapshot)
        return

    st.markdown(
        '<div class="dr-editorial-column dr-section-label">RESEARCH SESSION</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="dr-editorial-column dr-shell-title">'
        f"<h1>{escape(snapshot.question)}</h1></div>",
        unsafe_allow_html=True,
    )
    render_status(snapshot.status)
    st.caption(f"Markdown  ·  Max {snapshot.max_iterations} iterations")
    if snapshot.status == "failed":
        st.error(_safe_failure_message(snapshot))
        _render_current_activity(snapshot)
        _render_subtopic_sequence(snapshot)
        _render_recent_activity(snapshot)
        return
    if snapshot.report:
        st.markdown(snapshot.report)
    elif snapshot.status == "max_iterations":
        st.warning("The run reached its iteration limit before a report was available.")


def _format_datetime(value: datetime | None, *, prefix: str) -> str | None:
    if value is None:
        return None
    timestamp = value
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    hour = timestamp.strftime("%I").lstrip("0") or "0"
    return (
        f"{prefix} {timestamp:%b} {timestamp.day}, {timestamp:%Y} "
        f"at {hour}:{timestamp:%M} {timestamp:%p}"
    )


def _report_status_label(snapshot: UiSessionSnapshot) -> tuple[str, str, str]:
    if snapshot.status == "completed":
        return "RESEARCH COMPLETED", "completed", "Completed"
    if snapshot.status == "max_iterations":
        return "RESEARCH PAUSED", "max_iterations", "Max iterations · Partial report"
    if snapshot.status == "failed":
        label = "Failed · Partial report" if snapshot.report else "Failed"
        return "RESEARCH FAILED", "failed", label
    return "RESEARCH INCOMPLETE", "incomplete", "Incomplete"


def _render_quality_rows(
    *,
    heading: str,
    rows: tuple[tuple[str, int], ...],
) -> None:
    st.markdown(
        f'<div class="dr-section-label">{escape(heading)}</div>',
        unsafe_allow_html=True,
    )
    for label, count in rows:
        st.caption(f"{label} · {count}")


def _source_tier_label(tier: str) -> str:
    return {
        "high": "High",
        "moderate": "Moderate",
        "low": "Low",
        "unrated": "Unrated",
    }.get(tier, "Unrated")


def _render_source_detail(detail: UiSourceDetail) -> None:
    st.markdown(f"**{escape(detail.title)}**")
    st.caption(f"Source · {detail.url}")
    st.caption(f"Tier · {_source_tier_label(detail.tier)}")
    st.caption(f"Rationale · {detail.rationale}")
    topics = ", ".join(detail.related_sub_topics) or "Not associated with a subtopic"
    st.caption(f"Used in research topics · {topics}")
    st.caption(f"Corroboration score · {detail.corroboration_score:.2f}")


def _render_claim_detail(detail: UiClaimDetail) -> None:
    st.markdown(f"**{escape(detail.text)}**")
    verdict = {
        "verified": "Verified",
        "unverified": "Unverified",
        "contradicted": "Contradicted",
        "insufficient_evidence": "Insufficient evidence",
    }.get(detail.verdict, "Unknown")
    st.caption(f"Verdict · {verdict}")
    st.caption(f"Confidence · {detail.confidence:.0%}")
    if detail.source_urls:
        st.caption(f"Supporting URLs · {', '.join(detail.source_urls)}")
    for evidence in detail.evidence:
        st.caption(f"Evidence · {evidence}")
    for contradiction in detail.contradictions:
        st.caption(f"Contradiction · {contradiction}")


def _render_completed_details_rail(snapshot: UiSessionSnapshot) -> None:
    source_summary = snapshot.source_summary
    _render_quality_rows(
        heading="SOURCE CREDIBILITY",
        rows=(
            ("High", source_summary.high),
            ("Moderate", source_summary.moderate),
            ("Low", source_summary.low),
            ("Unrated", source_summary.unrated),
        ),
    )
    with st.expander("Source details", expanded=False):
        if source_summary.details:
            for detail in source_summary.details:
                _render_source_detail(detail)
                st.divider()
        else:
            st.caption("No source details recorded.")

    fact_summary = snapshot.fact_check_summary
    _render_quality_rows(
        heading="FACT-CHECK SUMMARY",
        rows=(
            ("Verified", fact_summary.verified),
            ("Unverified", fact_summary.unverified),
            ("Contradicted", fact_summary.contradicted),
            ("Insufficient evidence", fact_summary.insufficient_evidence),
        ),
    )
    with st.expander("Claim details", expanded=False):
        if fact_summary.details:
            for detail in fact_summary.details:
                _render_claim_detail(detail)
                st.divider()
        else:
            st.caption("No claim details recorded.")

    if snapshot.token_usage is not None:
        st.metric(
            "Tokens used",
            _format_token_total(snapshot.token_usage.total_tokens),
        )
    trace_url = _valid_trace_url(snapshot.trace_url)
    if trace_url is not None:
        st.link_button("Open LangSmith trace", trace_url, use_container_width=True)

    with st.expander("Session metadata", expanded=False):
        st.caption(f"Session ID · {snapshot.session_id}")
        started = _format_datetime(snapshot.started_at, prefix="Started")
        completed = _format_datetime(snapshot.finished_at, prefix="Completed")
        if started:
            st.caption(started)
        if completed:
            st.caption(completed)
        st.caption(f"Configured limit · {snapshot.max_iterations} iterations")
        st.caption(f"Actual iteration · {snapshot.iteration}")
        st.caption(f"Terminal status · {status_presentation(snapshot.status)[1]}")
        if snapshot.report_path:
            st.caption(f"Report path · {snapshot.report_path}")


def _render_report_issues(snapshot: UiSessionSnapshot) -> None:
    if snapshot.limitations:
        render_status("max_iterations", label="LIMITATIONS")
        for limitation in snapshot.limitations:
            st.markdown(f"- {limitation}")

    if snapshot.errors:
        render_status("failed", label="EXECUTION ERRORS")
        if snapshot.status == "failed":
            st.error(_safe_failure_message(snapshot))
        else:
            st.error("Execution errors were recorded during the run.")
    elif snapshot.status == "completed":
        st.caption("No errors reported.")


def _render_completed_snapshot(snapshot: UiSessionSnapshot) -> None:
    eyebrow, status, status_label = _report_status_label(snapshot)
    main_column, details_column = st.columns([3, 1], gap="large")
    with main_column:
        st.markdown(
            f'<div class="dr-editorial-column dr-section-label">{eyebrow}</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="dr-editorial-column dr-shell-title">'
            f"<h1>{escape(snapshot.question)}</h1></div>",
            unsafe_allow_html=True,
        )
        render_status(status, label=status_label)
        if snapshot.status == "completed":
            iteration_metadata = (
                f"Completed in {snapshot.iteration} of "
                f"{snapshot.max_iterations} iterations"
            )
        elif snapshot.status == "max_iterations":
            iteration_metadata = (
                f"Stopped at iteration {snapshot.iteration} of "
                f"{snapshot.max_iterations}"
            )
        elif snapshot.status == "incomplete":
            iteration_metadata = (
                f"Incomplete after {snapshot.iteration} of "
                f"{snapshot.max_iterations} iterations"
            )
        else:
            iteration_metadata = (
                f"Failed after {snapshot.iteration} of "
                f"{snapshot.max_iterations} iterations"
            )
        metadata = [
            f"Markdown · {iteration_metadata}"
        ]
        finished_at = _format_datetime(
            snapshot.finished_at,
            prefix="Completed" if snapshot.status == "completed" else "Ended",
        )
        if finished_at:
            metadata.append(finished_at)
        st.caption(" · ".join(metadata))
        if snapshot.report_path:
            st.caption(f"Report path · `{escape(snapshot.report_path)}`")
        if snapshot.status == "max_iterations":
            st.caption("The run reached its configured iteration limit.")

        if snapshot.report:
            # Keep report Markdown on the base canvas so the answer remains the
            # dominant object and Streamlit owns its safe Markdown rendering.
            st.markdown(snapshot.report)
        else:
            st.caption("No report was available for this session.")
        _render_report_issues(snapshot)

    with details_column:
        _render_completed_details_rail(snapshot)


def render_current_session_view(controller: LocalResearchController) -> None:
    """Render the active snapshot while preserving the Question canvas."""
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

    if snapshot.status == "running":
        active_id = st.session_state.get(_ACTIVE_SESSION_KEY)
        if active_id == snapshot.session_id:
            from deep_research.ui.app import render_live_progress

            render_live_progress(controller)
        else:
            _render_running_snapshot(snapshot)
        return
    _render_terminal_snapshot(snapshot)


def _history_time_label(entry: SessionHistoryEntry) -> str:
    timestamp = entry.started_at
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    timestamp = timestamp.astimezone(timezone.utc)
    if timestamp.date() == datetime.now(timezone.utc).date():
        return f"Today, {timestamp:%H:%M}"
    return f"{timestamp:%b} {timestamp.day}, {timestamp:%H:%M}"


def _history_context(entry: SessionHistoryEntry) -> str:
    if entry.status == "running":
        return f"Macro iteration {entry.iteration} of {entry.max_iterations}"
    if entry.status == "completed":
        return (
            f"Completed in {entry.iteration} of {entry.max_iterations} iterations"
        )
    if entry.status == "max_iterations":
        return f"Stopped at {entry.iteration} of {entry.max_iterations} iterations"
    if entry.status == "failed":
        return f"Stopped at iteration {entry.iteration} of {entry.max_iterations}"
    return f"Paused after iteration {entry.iteration} of {entry.max_iterations}"


def _render_history_row(
    entry: SessionHistoryEntry,
    *,
    selected_id: str | None,
    state: MutableMapping[str, Any],
) -> None:
    selected = entry.session_id == selected_id
    row_key = (
        f"dr-history-row-selected-{entry.session_id}"
        if selected
        else f"dr-history-row-{entry.session_id}"
    )
    with _st_container(key=row_key, gap="small"):
        if selected:
            st.caption("Selected session")
        question_column, status_column, action_column = st.columns(
            [3, 1.15, 0.5],
            gap="medium",
            vertical_alignment="center",
        )
        with question_column:
            st.markdown(
                f'<div class="dr-session-question"><strong>{escape(entry.question)}'
                "</strong></div>",
                unsafe_allow_html=True,
            )
            st.caption(f"{_history_time_label(entry)}  ·  {_history_context(entry)}")
        with status_column:
            render_status(entry.status)
        with action_column:
            st.button(
                "Open",
                key=f"history_open_{entry.session_id}",
                type="secondary",
                use_container_width=True,
                on_click=_select_session,
                args=(entry.session_id, state),
            )


def render_history_view(controller: LocalResearchController) -> None:
    """Render a searchable, newest-first archive of local session metadata."""
    state = st.session_state
    entries = _history_entries(controller, state)
    st.markdown(
        '<div class="dr-editorial-column dr-shell-title">'
        "<h1>Research sessions</h1></div>",
        unsafe_allow_html=True,
    )
    st.caption(f"{len(entries)} sessions total")

    search_column, filter_column = st.columns([2.7, 1.35], gap="small")
    with search_column:
        search = st.text_input(
            "Search research questions",
            key=_HISTORY_SEARCH_KEY,
            placeholder="Search research questions...",
        )
    with filter_column:
        current_filter = state.get(_HISTORY_FILTER_KEY, "All")
        matching_filter = next(
            (
                option
                for option in _HISTORY_FILTER_OPTIONS
                if str(current_filter).casefold() == option.casefold()
            ),
            "All",
        )
        if current_filter != matching_filter:
            state[_HISTORY_FILTER_KEY] = matching_filter
        segmented_control = getattr(st, "segmented_control", None)
        if callable(segmented_control):
            selected_filter = segmented_control(
                "Status",
                options=list(_HISTORY_FILTER_OPTIONS),
                key=_HISTORY_FILTER_KEY,
                label_visibility="collapsed",
            )
        else:
            # Streamlit 1.37 is supported, but segmented_control is newer;
            # retain the same values for older supported installations.
            selected_filter = st.selectbox(
                "Status",
                options=list(_HISTORY_FILTER_OPTIONS),
                key=_HISTORY_FILTER_KEY,
            )

    query = search.strip().casefold() if isinstance(search, str) else ""
    filter_name = str(selected_filter).casefold()
    visible_entries = [
        entry
        for entry in entries
        if (not query or query in entry.question.casefold())
        and (
            filter_name == "all"
            or (
                filter_name == "issues"
                and entry.status in _HISTORY_ISSUE_STATUSES
            )
            or entry.status == filter_name
        )
    ]

    st.markdown(
        '<div class="dr-history-header dr-section-label">'
        "QUESTION <span>NEWEST FIRST</span></div>",
        unsafe_allow_html=True,
    )
    selected_id = state.get(_SELECTED_SESSION_KEY)
    selected_id = selected_id if isinstance(selected_id, str) else None
    for index, entry in enumerate(visible_entries):
        _render_history_row(
            entry,
            selected_id=selected_id,
            state=state,
        )
        if index < len(visible_entries) - 1:
            with _st_container(key=f"dr-history-divider-{index}"):
                st.divider()
    if not visible_entries:
        st.caption("No research sessions match this search and status filter.")


__all__ = [
    "_render_running_snapshot",
    "REPORT_CSS",
    "render_current_session_view",
    "render_history_view",
    "render_new_research_view",
    "render_sidebar",
    "render_sidebar_status",
    "render_status",
    "status_presentation",
]
