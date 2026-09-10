"""Reusable native Streamlit components for the persistent app shell."""

from __future__ import annotations

import inspect
import re
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from math import isfinite
from typing import TYPE_CHECKING, Any

import streamlit as st

from deep_research.runtime.errors import (
    CONFIGURATION_HINTS,
    ResearchConfigurationError,
)
from deep_research.ui.models import (
    SessionHistoryEntry,
    UiClaimDetail,
    UiExecutionErrorPresentation,
    UiSessionSnapshot,
    UiSourceDetail,
    UiSubTopicProgress,
    history_entry_from_snapshot,
)
from deep_research.ui.progress import display_agent_action, display_agent_name
from deep_research.ui.styles import COLORS, RADII, SPACING
from deep_research.utils.types import ResearchError

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


@dataclass(frozen=True, slots=True)
class _ExecutionErrorSpec:
    category: str
    message: str
    recoverable_effect: str
    terminal_effect: str
    recovery_hint: str


_EXECUTION_ERROR_SPECS: Mapping[str, _ExecutionErrorSpec] = {
    "ui.research.configuration_error": _ExecutionErrorSpec(
        category="Configuration issue",
        message=_CONFIGURATION_FAILURE_MESSAGE,
        recoverable_effect=(
            "The session could not begin with the current configuration."
        ),
        terminal_effect="The session could not begin with the current configuration.",
        recovery_hint="Review the local research configuration and try again.",
    ),
    "ui.research.failed": _ExecutionErrorSpec(
        category="Execution stopped",
        message="Research run failed unexpectedly.",
        recoverable_effect="The workflow recorded an execution issue and continued.",
        terminal_effect="The run stopped; any retained report is partial.",
        recovery_hint=(
            "Review the retained report if present, then start a new session."
        ),
    ),
    "graph_agent_configuration_error": _ExecutionErrorSpec(
        category="Configuration issue",
        message="The research workflow could not initialize its agents.",
        recoverable_effect="The workflow continued with reduced execution coverage.",
        terminal_effect="The workflow stopped before research could be completed.",
        recovery_hint="Review the local research configuration and try again.",
    ),
    "graph_planning_failed": _ExecutionErrorSpec(
        category="Planning issue",
        message="The research plan could not be prepared.",
        recoverable_effect="The workflow continued without one planning result.",
        terminal_effect="The workflow stopped before all research could be planned.",
        recovery_hint="Start a new session to retry the planning step.",
    ),
    "graph_provider_configuration_error": _ExecutionErrorSpec(
        category="Configuration issue",
        message="A research workflow provider is not configured.",
        recoverable_effect="The workflow continued with reduced provider coverage.",
        terminal_effect="The workflow stopped before all research could be completed.",
        recovery_hint="Review the local research configuration and try again.",
    ),
    "graph_invalid_agent_state": _ExecutionErrorSpec(
        category="Workflow state issue",
        message="The research workflow reached an invalid state.",
        recoverable_effect="The workflow continued after recording a state issue.",
        terminal_effect="The workflow stopped before all planned work completed.",
        recovery_hint="Start a new session to retry the workflow.",
    ),
    "graph_invalid_route": _ExecutionErrorSpec(
        category="Workflow state issue",
        message="The research workflow could not select its next step.",
        recoverable_effect="The workflow continued with reduced execution coverage.",
        terminal_effect="The workflow stopped before all planned work completed.",
        recovery_hint="Start a new session to retry the workflow.",
    ),
    "researcher_sub_topic_without_findings": _ExecutionErrorSpec(
        category="Research coverage issue",
        message="One research subtopic returned no citable findings.",
        recoverable_effect=(
            "The research pass continued, but coverage may be thinner for one "
            "subtopic."
        ),
        terminal_effect=(
            "The research pass stopped with one subtopic lacking citable "
            "findings."
        ),
        recovery_hint=(
            "Review the report coverage and rerun the session if the gap "
            "matters."
        ),
    ),
    "researcher_sub_topic_skipped": _ExecutionErrorSpec(
        category="Research coverage issue",
        message="One planned research subtopic was not researched.",
        recoverable_effect=(
            "The research pass continued, but the report may omit one "
            "subtopic."
        ),
        terminal_effect=(
            "The research pass stopped before one planned subtopic was "
            "researched."
        ),
        recovery_hint=(
            "Review the stopping point and rerun the session for full "
            "coverage."
        ),
    ),
    "researcher_extraction_provider_error": _ExecutionErrorSpec(
        category="Finding extraction issue",
        message="Finding extraction was unavailable for one research step.",
        recoverable_effect=(
            "The research pass continued, but one finding set may be "
            "incomplete."
        ),
        terminal_effect=(
            "The research pass stopped before all planned subtopics were "
            "researched."
        ),
        recovery_hint=(
            "Review the stopping point and start a new session to retry the "
            "missing step."
        ),
    ),
    "researcher_invalid_finding": _ExecutionErrorSpec(
        category="Finding validation issue",
        message="One extracted finding did not pass validation.",
        recoverable_effect="The research pass continued without that finding.",
        terminal_effect="The research pass stopped with an invalid finding result.",
        recovery_hint="Review the retained coverage and rerun the session if needed.",
    ),
    "researcher_no_sub_topics": _ExecutionErrorSpec(
        category="Planning issue",
        message="No research subtopics were available to investigate.",
        recoverable_effect="The workflow continued with no subtopic findings.",
        terminal_effect="The workflow stopped before research could begin.",
        recovery_hint="Start a new session with a more specific research question.",
    ),
    "source_evaluator_reputation_unavailable": _ExecutionErrorSpec(
        category="Source evaluation issue",
        message="Remembered source credibility context was unavailable.",
        recoverable_effect=(
            "The source credibility pass continued with direct scoring, so "
            "the report may have less remembered-source context."
        ),
        terminal_effect=(
            "The source credibility pass stopped before all sources were "
            "evaluated."
        ),
        recovery_hint=(
            "Review source details and rerun the session if credibility "
            "coverage is important."
        ),
    ),
    "source_evaluator_scoring_provider_error": _ExecutionErrorSpec(
        category="Source evaluation issue",
        message="Model-based source scoring was unavailable.",
        recoverable_effect=(
            "The source credibility pass continued with reduced scoring "
            "coverage."
        ),
        terminal_effect=(
            "The source credibility pass stopped; retained sources use "
            "lower-confidence fallback context."
        ),
        recovery_hint=(
            "Review source credibility details and rerun the session for "
            "full scoring."
        ),
    ),
    "source_evaluator_no_sources": _ExecutionErrorSpec(
        category="Source coverage issue",
        message="No sources were available for credibility evaluation.",
        recoverable_effect=(
            "The workflow continued, but source coverage may be limited."
        ),
        terminal_effect="The workflow stopped without sources to evaluate.",
        recovery_hint=(
            "Review the research question and rerun the session to gather "
            "sources."
        ),
    ),
    "fact_checker_extraction_provider_error": _ExecutionErrorSpec(
        category="Fact-check issue",
        message="Claim extraction was unavailable for one fact-checking step.",
        recoverable_effect="The fact-check pass continued with reduced claim coverage.",
        terminal_effect=(
            "The fact-check pass stopped before all claims could be checked."
        ),
        recovery_hint=(
            "Treat unchecked claims cautiously and rerun the session for "
            "full verification."
        ),
    ),
    "fact_checker_invalid_claim": _ExecutionErrorSpec(
        category="Fact-check issue",
        message="One extracted claim did not pass validation.",
        recoverable_effect="The fact-check pass continued without that claim.",
        terminal_effect="The fact-check pass stopped with an invalid claim result.",
        recovery_hint="Review the fact-check summary and rerun the session if needed.",
    ),
    "fact_checker_no_findings": _ExecutionErrorSpec(
        category="Fact-check issue",
        message="No findings were available for fact-checking.",
        recoverable_effect="The workflow continued without additional claim checks.",
        terminal_effect="The workflow stopped before claims could be checked.",
        recovery_hint=(
            "Review the report evidence and rerun the session for "
            "verification."
        ),
    ),
    "fact_checker_verification_provider_error": _ExecutionErrorSpec(
        category="Fact-check issue",
        message="Claim verification was unavailable for one fact-checking step.",
        recoverable_effect=(
            "The fact-check pass continued with reduced verification "
            "coverage."
        ),
        terminal_effect=(
            "The fact-check pass stopped before all claims could be "
            "verified."
        ),
        recovery_hint=(
            "Treat unchecked claims cautiously and rerun the session for "
            "verification."
        ),
    ),
    "synthesizer_report_provider_error": _ExecutionErrorSpec(
        category="Report synthesis issue",
        message="Report synthesis was unavailable.",
        recoverable_effect=(
            "The workflow continued, but the report may have reduced "
            "synthesis coverage."
        ),
        terminal_effect=(
            "The workflow stopped before the report could be fully "
            "synthesized."
        ),
        recovery_hint=(
            "Review any retained report and start a new session to retry "
            "synthesis."
        ),
    ),
    "synthesizer_invalid_section": _ExecutionErrorSpec(
        category="Report synthesis issue",
        message="One synthesized report section did not pass validation.",
        recoverable_effect="The workflow continued without that section.",
        terminal_effect="The workflow stopped with an incomplete report structure.",
        recovery_hint=(
            "Review the retained report and rerun the session if coverage is "
            "missing."
        ),
    ),
    "synthesizer_report_not_written": _ExecutionErrorSpec(
        category="Report delivery issue",
        message="The synthesized report could not be saved locally.",
        recoverable_effect=(
            "The workflow continued, but the saved report may be unavailable."
        ),
        terminal_effect="The workflow stopped before the report could be saved.",
        recovery_hint="Check the local output directory and rerun the session.",
    ),
    "synthesizer_memory_save_failed": _ExecutionErrorSpec(
        category="Research memory issue",
        message="Research memory could not be updated after synthesis.",
        recoverable_effect=(
            "The report workflow continued without saving this context to "
            "memory."
        ),
        terminal_effect="The workflow stopped while saving research context.",
        recovery_hint=(
            "Use the retained report and rerun the session if memory "
            "continuity matters."
        ),
    ),
    "synthesizer_no_evidence": _ExecutionErrorSpec(
        category="Report evidence issue",
        message="No evidence was available for report synthesis.",
        recoverable_effect=(
            "The workflow continued, but the report may have limited "
            "evidence."
        ),
        terminal_effect=(
            "The workflow stopped before an evidence-backed report could be "
            "synthesized."
        ),
        recovery_hint="Broaden the question or rerun the session to gather evidence.",
    ),
    "critic_review_provider_error": _ExecutionErrorSpec(
        category="Report review issue",
        message="The report review step was unavailable.",
        recoverable_effect="The workflow continued without one review result.",
        terminal_effect=(
            "The workflow stopped before the report could be fully reviewed."
        ),
        recovery_hint=(
            "Review the retained report and rerun the session for another "
            "review pass."
        ),
    ),
    "critic_missing_report": _ExecutionErrorSpec(
        category="Report review issue",
        message="No report was available for the review step.",
        recoverable_effect="The workflow continued without a report review.",
        terminal_effect=(
            "The workflow stopped because no report was available to review."
        ),
        recovery_hint=(
            "Rerun the session after confirming that research produced "
            "evidence."
        ),
    ),
    "agent_unknown_tool": _ExecutionErrorSpec(
        category="Research step issue",
        message="A requested research tool was unavailable.",
        recoverable_effect="The research step continued with one tool action omitted.",
        terminal_effect=(
            "The research step stopped because a required tool was "
            "unavailable."
        ),
        recovery_hint=(
            "Review the report coverage and rerun the session if the missing "
            "action matters."
        ),
    ),
    "agent_tool_budget_exhausted": _ExecutionErrorSpec(
        category="Research step issue",
        message="A research step reached its tool-action limit.",
        recoverable_effect=(
            "The research step continued with the evidence collected so far."
        ),
        terminal_effect=(
            "The research step stopped after reaching its tool-action limit."
        ),
        recovery_hint=(
            "Review the evidence collected and rerun the session for more "
            "coverage."
        ),
    ),
    "agent_invalid_tool_input": _ExecutionErrorSpec(
        category="Research step issue",
        message="A research tool action could not be validated.",
        recoverable_effect="The research step continued without that tool action.",
        terminal_effect=(
            "The research step stopped after a tool action could not be "
            "validated."
        ),
        recovery_hint="Review the report coverage and rerun the session if needed.",
    ),
    "agent_tool_failed": _ExecutionErrorSpec(
        category="Research step issue",
        message="Execution errors were recorded during the run.",
        recoverable_effect=(
            "The workflow continued, but the result may have less evidence."
        ),
        terminal_effect="The research step stopped before all evidence was collected.",
        recovery_hint=(
            "Review the retained evidence and rerun the session if coverage "
            "is incomplete."
        ),
    ),
    "agent_provider_error": _ExecutionErrorSpec(
        category="Research step issue",
        message="A model-assisted research step was unavailable.",
        recoverable_effect="The workflow continued with reduced research coverage.",
        terminal_effect="The research step stopped before all evidence was collected.",
        recovery_hint=(
            "Review the stopping point and start a new session to retry the "
            "step."
        ),
    ),
    "ValidationError": _ExecutionErrorSpec(
        category="Input validation issue",
        message="A research action did not pass validation.",
        recoverable_effect="The workflow continued without that action.",
        terminal_effect="The workflow stopped after an action failed validation.",
        recovery_hint="Review the report coverage and rerun the session if needed.",
    ),
    "ResponseValidationError": _ExecutionErrorSpec(
        category="Research response issue",
        message="A research response did not pass validation.",
        recoverable_effect="The workflow continued without that response.",
        terminal_effect=(
            "The workflow stopped after a research response failed "
            "validation."
        ),
        recovery_hint="Rerun the session to retry the affected research step.",
    ),
    "unsupported_document_format": _ExecutionErrorSpec(
        category="Document input issue",
        message="A document format was not supported for research.",
        recoverable_effect="The workflow continued without that document.",
        terminal_effect="The workflow stopped before the document could be used.",
        recovery_hint="Provide a supported document format and rerun the session.",
    ),
    "document_extraction_failed": _ExecutionErrorSpec(
        category="Document input issue",
        message="Text could not be extracted from one document.",
        recoverable_effect=(
            "The workflow continued without that document's extracted text."
        ),
        terminal_effect="The workflow stopped while extracting document text.",
        recovery_hint="Check the document and rerun the session.",
    ),
    "unsupported_content_type": _ExecutionErrorSpec(
        category="Source input issue",
        message="A source content type was not supported for research.",
        recoverable_effect="The workflow continued without that source content.",
        terminal_effect="The workflow stopped before that source could be used.",
        recovery_hint="Review the source selection and rerun the session.",
    ),
    "robots_disallowed": _ExecutionErrorSpec(
        category="Source access issue",
        message="A source declined automated access.",
        recoverable_effect="The workflow continued without that source.",
        terminal_effect="The workflow stopped while accessing a source.",
        recovery_hint=(
            "Review the remaining sources and rerun the session if coverage "
            "is insufficient."
        ),
    ),
}

_FALLBACK_EXECUTION_ERROR_SPEC = _ExecutionErrorSpec(
    category="Execution issue",
    message="Execution errors were recorded during the run.",
    recoverable_effect="The workflow continued, but the result may have less coverage.",
    terminal_effect="The run stopped before all planned work completed.",
    recovery_hint=(
        "Review the retained result and start a new session if coverage is "
        "incomplete."
    ),
)

_SAFE_STAGE_BY_SOURCE = {
    "agent.researcher": "Researcher",
    "researcher": "Researcher",
    "agent.source_evaluator": "Source evaluator",
    "source_evaluator": "Source evaluator",
    "agent.fact_checker": "Fact checker",
    "fact_checker": "Fact checker",
    "agent.synthesizer": "Report synthesis",
    "synthesizer": "Report synthesis",
    "agent.critic": "Report review",
    "critic": "Report review",
    "graph": "Research workflow",
    "engine": "Research workflow",
    "ui": "Application",
}

_SAFE_DETAIL_FIELDS = (
    ("attempts", "Attempts"),
    ("failures", "Failures"),
    ("failure_count", "Failures"),
    ("sources", "Sources"),
    ("source_count", "Sources"),
    ("iterations", "Iterations"),
    ("iteration", "Iteration"),
    ("max_iterations", "Iteration limit"),
    ("tool_calls", "Tool calls"),
    ("tool_budget", "Tool-action limit"),
    ("attempted", "Attempted items"),
    ("rejected", "Rejected items"),
    ("priority", "Priority"),
    ("status_code", "Response status"),
    ("exception_type", "Exception type"),
    ("reason", "Recorded reason"),
    ("stop_reason", "Stop reason"),
)

_SAFE_REASON_LABELS = {
    "cap": "Iteration cap",
    "provider_failure_stopped_processing": "Provider failure stopped processing",
    "tool_budget_exhausted": "Tool-action limit reached",
    "provider_error": "Provider failure",
    "tool_failed": "Tool failure",
    "malformed_result": "Malformed result",
    "finished": "Completed",
    "completed": "Completed",
}
_SAFE_EXCEPTION_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
_UNSAFE_EXCEPTION_MARKERS = (
    "secret",
    "password",
    "credential",
    "token",
    "payload",
    "trace",
    "raw",
    "config",
    "key",
)

_STATUS_PRESENTATION: Mapping[str, tuple[str, str, str]] = {
    "ready": ("●", "Ready to start", "neutral"),
    "running": ("◌", "Running", "running"),
    "completed": ("✓", "Completed", "completed"),
    "max_iterations": ("▲", "Max iterations", "max-iterations"),
    "incomplete": ("Ⅱ", "Incomplete", "incomplete"),
    "failed": ("×", "Failed", "failed"),
}

_QUALITY_ROW_PRESENTATION: Mapping[tuple[str, str], tuple[str, str]] = {
    ("source", "High"): ("●", "success"),
    ("source", "Moderate"): ("◐", "warning"),
    ("source", "Low"): ("●", "error"),
    ("source", "Unrated"): ("○", "neutral"),
    ("fact", "Verified"): ("✓", "success"),
    ("fact", "Unverified"): ("○", "neutral"),
    ("fact", "Contradicted"): ("×", "error"),
    ("fact", "Insufficient evidence"): ("△", "warning"),
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
  margin: 16px 0 !important;
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


def _safe_stage(source: str) -> str:
    return _SAFE_STAGE_BY_SOURCE.get(source, "Research workflow")


def _safe_integer(value: object) -> str | None:
    """Return only bounded numeric diagnostics from a structured error."""
    if type(value) is int:
        number = value
    elif type(value) is float and isfinite(value) and value.is_integer():
        number = int(value)
    else:
        return None
    if number < 0 or number > 1_000_000:
        return None
    return str(number)


def _safe_exception_type(value: object) -> str | None:
    if not isinstance(value, str) or not _SAFE_EXCEPTION_TYPE.fullmatch(value):
        return None
    lowered = value.casefold()
    if any(marker in lowered for marker in _UNSAFE_EXCEPTION_MARKERS):
        return None
    return value


def _safe_diagnostic_value(field: str, value: object) -> str | None:
    if field in {"reason", "stop_reason"}:
        if isinstance(value, str):
            return _SAFE_REASON_LABELS.get(value)
        return None
    if field == "exception_type":
        return _safe_exception_type(value)
    if field in {"attempted", "rejected"} and isinstance(
        value, (list, tuple)
    ):
        return _safe_integer(len(value))
    return _safe_integer(value)


def _safe_diagnostic_context(error: ResearchError) -> dict[str, str]:
    """Allowlist compact, non-sensitive context for the details expander."""
    context = {"Stage": _safe_stage(error.source)}
    seen_labels = set(context)
    for field, label in _SAFE_DETAIL_FIELDS:
        if label in seen_labels or field not in error.details:
            continue
        safe_value = _safe_diagnostic_value(field, error.details[field])
        if safe_value is None:
            continue
        context[label] = safe_value
        seen_labels.add(label)
    return context


def _presentation_from_spec(
    error: ResearchError,
    spec: _ExecutionErrorSpec,
) -> UiExecutionErrorPresentation:
    effect = spec.recoverable_effect if error.recoverable else spec.terminal_effect
    return UiExecutionErrorPresentation(
        category=spec.category,
        message=spec.message,
        effect=effect,
        recovery_hint=spec.recovery_hint,
        diagnostic_context=_safe_diagnostic_context(error),
    )


def execution_error_presentation(
    error: ResearchError,
) -> UiExecutionErrorPresentation:
    """Map one structured engine error to safe, project-owned UI copy.

    The persisted error message and untrusted detail values are intentionally
    not used as user-facing text.  Only the error type, recoverability flag,
    and a small allowlist of structured numeric/context fields are consulted.
    """
    spec = _EXECUTION_ERROR_SPECS.get(
        error.error_type,
        _FALLBACK_EXECUTION_ERROR_SPEC,
    )
    return _presentation_from_spec(error, spec)


def _fallback_execution_error_presentation(
    *,
    terminal: bool,
) -> UiExecutionErrorPresentation:
    spec = _FALLBACK_EXECUTION_ERROR_SPEC
    return UiExecutionErrorPresentation(
        category=spec.category,
        message=(
            "Research run failed unexpectedly."
            if terminal
            else spec.message
        ),
        effect=spec.terminal_effect if terminal else spec.recoverable_effect,
        recovery_hint=spec.recovery_hint,
        diagnostic_context={"Stage": "Research workflow"},
    )


def _execution_error_presentations(
    snapshot: UiSessionSnapshot,
) -> tuple[UiExecutionErrorPresentation, ...]:
    if snapshot.errors:
        return tuple(
            execution_error_presentation(error) for error in snapshot.errors
        )
    return (_fallback_execution_error_presentation(terminal=True),)


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
    controller: LocalResearchController | None = None,
) -> SessionHistoryEntry:
    """Use controller lifecycle truth when normalizing persisted entries."""
    active = False
    checker = getattr(controller, "is_session_active", None)
    if callable(checker):
        try:
            active = bool(checker(entry.session_id))
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
            active = False
    else:
        active_id = state.get(_ACTIVE_SESSION_KEY)
        active = entry.session_id == active_id

    if entry.status == "running" and not active:
        return entry.model_copy(
            deep=True,
            update={
                "status": "incomplete",
                "current_agent": None,
                "last_agent": entry.last_agent or entry.current_agent,
            },
        )
    if entry.status != "running":
        return entry.model_copy(
            deep=True,
            update={
                "current_agent": None,
                "last_agent": entry.last_agent or entry.current_agent,
            },
        )
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
        _history_entry_for_display(entry, state, controller)
        for entry in controller.list_history()
    ]
    return sorted(entries, key=_history_sort_key, reverse=True)


def _recent_entries(
    controller: LocalResearchController,
    state: MutableMapping[str, Any],
) -> list[SessionHistoryEntry]:
    entries = _history_entries(controller, state)
    active_entry = _entry_for_active_session(controller, state)
    if active_entry is not None:
        active_entry = _history_entry_for_display(active_entry, state, controller)
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
        current_agent=entry.current_agent,
        last_agent=entry.last_agent,
        planned_sub_topic_count=entry.planned_sub_topic_count,
        research_phase_complete=entry.research_phase_complete,
        sub_topics=[topic.model_copy(deep=True) for topic in entry.sub_topics],
        last_sub_topic=(
            entry.last_sub_topic.model_copy(deep=True)
            if entry.last_sub_topic is not None
            else None
        ),
        recent_activity=[
            activity.model_copy(deep=True)
            for activity in entry.recent_activity
        ],
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
    return _history_entry_for_display(entry, state, controller)


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


def _render_new_research_content(controller: LocalResearchController) -> None:
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


def render_new_research_view(controller: LocalResearchController) -> None:
    """Render the question-first view inside its bounded editorial column."""
    with _st_container(key="dr-new-research-column"):
        _render_new_research_content(controller)


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


def _render_stopping_point(snapshot: UiSessionSnapshot) -> None:
    """Render compact durable context for a non-live session."""
    st.markdown(
        '<div class="dr-editorial-column dr-section-label">STOPPING POINT</div>',
        unsafe_allow_html=True,
    )
    agent = snapshot.last_agent or snapshot.current_agent
    topic = snapshot.last_sub_topic or _active_subtopic(snapshot)
    if agent:
        st.caption(f"Last known agent · {display_agent_name(agent)}")
    if topic is not None:
        total = snapshot.planned_sub_topic_count
        subtopic_label = (
            f"Subtopic {topic.index} of {total}"
            if total
            else f"Subtopic {topic.index}"
        )
        st.caption(f"Last known subtopic · {subtopic_label} · {topic.title}")
    if snapshot.research_phase_complete:
        st.caption("Research phase · Complete")

    has_progress = bool(
        agent
        or topic is not None
        or snapshot.research_phase_complete
        or snapshot.sub_topics
        or snapshot.recent_activity
        or snapshot.planned_sub_topic_count
    )
    if not has_progress:
        st.caption("No stopping-point metadata was recorded.")
        return
    if snapshot.sub_topics or snapshot.planned_sub_topic_count:
        _render_subtopic_sequence(snapshot)
    if snapshot.recent_activity:
        _render_recent_activity(snapshot)


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

    if snapshot.errors:
        with st.expander("Execution issues", expanded=False):
            for presentation in _execution_error_presentations(snapshot):
                st.markdown(f"**{escape(presentation.category)}**")
                st.caption(f"Effect · {presentation.effect}")
                if presentation.recovery_hint:
                    st.caption(f"Recovery · {presentation.recovery_hint}")


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
    return _execution_error_presentations(snapshot)[0].message


def _render_safe_diagnostic_details(
    presentations: tuple[UiExecutionErrorPresentation, ...],
) -> None:
    with st.expander("Safe diagnostic details", expanded=False):
        for index, presentation in enumerate(presentations):
            if index:
                st.divider()
            if len(presentations) > 1:
                st.markdown(f"**{escape(presentation.category)}**")
            for label, value in presentation.diagnostic_context.items():
                st.caption(f"{label} · {value}")


def _render_execution_errors(snapshot: UiSessionSnapshot) -> None:
    """Render safe effects and recovery without exposing engine text."""
    render_status("failed", label="EXECUTION ERRORS")
    presentations = _execution_error_presentations(snapshot)
    for presentation in presentations:
        st.error(f"{presentation.category}: {presentation.message}")
        st.caption(f"Effect · {presentation.effect}")
        if presentation.recovery_hint:
            st.caption(f"Recovery · {presentation.recovery_hint}")
    _render_safe_diagnostic_details(presentations)


def _render_terminal_snapshot(snapshot: UiSessionSnapshot) -> None:
    has_retained_report = snapshot.report is not None
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
        _render_execution_errors(snapshot)
        if snapshot.report is None:
            st.caption("No report was available for this session.")
        _render_stopping_point(snapshot)
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
    kind: str,
    rows: tuple[tuple[str, int], ...],
) -> None:
    st.markdown(
        f'<div class="dr-section-label">{escape(heading)}</div>',
        unsafe_allow_html=True,
    )
    rendered_rows = [
        '<div class="dr-quality-list" role="list">'
    ]
    for label, count in rows:
        icon, tone = _QUALITY_ROW_PRESENTATION.get(
            (kind, label),
            ("•", "neutral"),
        )
        slug = label.casefold().replace(" ", "-")
        rendered_rows.append(
            f'<div class="dr-quality-row dr-quality-row--{kind}-{slug} '
            f'dr-quality-row--{tone}" role="listitem">'
            f'<span class="dr-quality-icon" aria-hidden="true">{icon}</span>'
            f'<span class="dr-quality-label">{escape(label)}</span>'
            f'<span class="dr-quality-count">{count}</span>'
            "</div>"
        )
    rendered_rows.append("</div>")
    st.markdown("".join(rendered_rows), unsafe_allow_html=True)


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
        kind="source",
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
        kind="fact",
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
        _render_execution_errors(snapshot)
    elif snapshot.status == "completed":
        st.caption("No errors reported.")


def _render_completed_report_body(
    snapshot: UiSessionSnapshot,
    *,
    eyebrow: str,
    status: str,
    status_label: str,
) -> None:
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
    metadata = [f"Markdown · {iteration_metadata}"]
    finished_at = _format_datetime(
        snapshot.finished_at,
        prefix="Completed" if snapshot.status == "completed" else "Ended",
    )
    if finished_at:
        metadata.append(finished_at)
    st.caption(" · ".join(metadata))
    if snapshot.report_path:
        st.caption("Report path")
        st.code(snapshot.report_path, language=None)
    if snapshot.status == "max_iterations":
        st.caption("The run reached its configured iteration limit.")

    if snapshot.report:
        # Keep report Markdown on the base canvas so the answer remains the
        # dominant object and Streamlit owns its safe Markdown rendering.
        st.markdown(snapshot.report)
    else:
        st.caption("No report was available for this session.")
    if snapshot.status != "completed":
        _render_stopping_point(snapshot)
    _render_report_issues(snapshot)


def _render_completed_snapshot(snapshot: UiSessionSnapshot) -> None:
    eyebrow, status, status_label = _report_status_label(snapshot)
    main_column, details_column = st.columns([3, 1], gap="large")
    with main_column:
        with _st_container(key="dr-report-column"):
            _render_completed_report_body(
                snapshot,
                eyebrow=eyebrow,
                status=status,
                status_label=status_label,
            )

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
    "execution_error_presentation",
    "render_current_session_view",
    "render_history_view",
    "render_new_research_view",
    "render_sidebar",
    "render_sidebar_status",
    "render_status",
    "status_presentation",
]
