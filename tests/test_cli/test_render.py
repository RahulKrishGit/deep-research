"""Tests for what the CLI prints."""

from __future__ import annotations

from deep_research.agents.events import agent_event
from deep_research.cli import render_progress, render_summary, render_warnings
from deep_research.graph.events import (
    node_completed_event,
    node_started_event,
    refinement_started_event,
    route_decided_event,
    session_completed_event,
    session_started_event,
)
from deep_research.observability import TokenUsage
from deep_research.request_budget import (
    ProviderCategory,
    RequestBudgetSnapshot,
)
from deep_research.runtime.outcome import ResearchOutcome, ToolCallSummary
from deep_research.utils.types import (
    Critique,
    ReportQualitySnapshot,
    ResearchError,
    ResearchEvent,
    ResearchState,
)

QUESTION = "How mature is quantum error correction?"

REPORT_PATH = "output/report-session-1-0.md"
EVIDENCE_PATH = "output/report-session-1-0-evidence.md"


def build_outcome(**overrides) -> ResearchOutcome:
    state = overrides.pop("state", None) or ResearchState(
        session_id="session-1", original_question=QUESTION
    )
    defaults = {
        "session_id": "session-1",
        "question": QUESTION,
        "status": "completed",
        "state": state,
        "trace_url": None,
        "report_path": REPORT_PATH,
        "token_usage": TokenUsage(),
        "tool_calls": (),
    }
    defaults.update(overrides)
    return ResearchOutcome(**defaults)


def quality_state(
    *,
    critique: Critique | None = None,
    quality: ReportQualitySnapshot | None = None,
    with_critique: bool = True,
) -> ResearchState:
    """One composed, judged pass: the numbers the summary must print."""
    return ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        quality=quality
        if quality is not None
        else quality_snapshot(),
        critique=(
            critique if critique is not None else gap_critique()
        )
        if with_critique
        else None,
    )


def quality_snapshot(**overrides: object) -> ReportQualitySnapshot:
    payload: dict[str, object] = {
        "coverage_ratio": 3 / 7,
        "planned_topics": 7,
        "covered_topics": 3,
        "unresolved_topic_ids": [
            "topic-03",
            "topic-05",
            "topic-06",
            "topic-07",
        ],
        "unique_findings": 10,
        "unique_sources": 14,
        "cited_sources": 12,
        "scored_cited_source_ratio": 10 / 12,
        "verified_claims": 14,
        "contradicted_claims": 1,
        "duplicate_claims": 0,
        "duplicate_source_rows": 0,
        "uncited_settled_points": 0,
    }
    payload.update(overrides)
    return ReportQualitySnapshot.model_validate(payload)


def gap_critique() -> Critique:
    """A model review that asked for more work, so the run is partial."""
    return Critique(
        score=6,
        gaps=[],
        unsupported_claims=[],
        recommended_queries=["qec cost 2025"],
        should_continue=True,
        rationale="Recorded for renderer tests.",
    )


def accepting_critique() -> Critique:
    return Critique(
        score=6,
        gaps=[],
        unsupported_claims=[],
        recommended_queries=[],
        should_continue=False,
        rationale="Recorded for renderer tests.",
    )


def error_state() -> ResearchState:
    return ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        errors=[
            ResearchError(
                error_type="researcher_sub_topic_skipped",
                source="agent.researcher",
                message=(
                    "A planned sub-topic was never researched; the report "
                    "will be incomplete for it."
                ),
                details={"coverage_id": "topic-03", "reason": "cap"},
            ),
            ResearchError(
                error_type="researcher_extraction_provider_error",
                source="agent.researcher",
                message="The model provider failed during research.",
                details={"coverage_id": "topic-05"},
            ),
            ResearchError(
                error_type="web_search_failed",
                source="tools.web_search",
                message="The search provider timed out.",
                details={"tool": "web_search"},
            ),
        ],
    )


def progress_state() -> ResearchState:
    return ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        events=[
            session_started_event(
                session_id="session-1", max_iterations=2, checkpointing=False
            ),
            node_started_event("planner", iteration=0),
            ResearchEvent(
                event_type="observability.span.started",
                source="observability",
                message="agent.planner started.",
            ),
            node_started_event("researcher", iteration=0),
            refinement_started_event(iteration=1, max_iterations=2),
            session_completed_event(
                status="completed", iteration=1, error_count=0, has_report=True
            ),
        ],
    )


def index_of(lines: list[str], prefix: str) -> int:
    return next(
        index for index, line in enumerate(lines) if line.startswith(prefix)
    )


# --- live progress ---------------------------------------------------------


def test_progress_returns_the_line_for_one_plain_event() -> None:
    line = render_progress(node_started_event("planner", iteration=0), verbose=False)

    assert line == "  [0] Node planner started."


def test_plain_progress_streams_every_allowed_event_type() -> None:
    assert render_progress(
        session_started_event(
            session_id="session-1", max_iterations=2, checkpointing=False
        ),
        verbose=False,
    )
    assert render_progress(
        refinement_started_event(iteration=1, max_iterations=2), verbose=False
    )
    assert render_progress(
        route_decided_event(
            destination="refine",
            reason="refinement_requested",
            iteration=1,
            max_iterations=2,
            should_continue=True,
        ),
        verbose=False,
    )


def test_progress_skips_span_lifecycle_events_in_both_modes() -> None:
    span = ResearchEvent(
        event_type="observability.span.started",
        source="observability",
        message="agent.planner started.",
    )

    assert render_progress(span, verbose=False) is None
    assert render_progress(span, verbose=True) is None


def test_plain_progress_skips_an_agent_completion() -> None:
    completed = node_completed_event(
        "planner", iteration=0, event_count=2, error_count=0
    )

    assert render_progress(completed, verbose=False) is None
    assert (
        render_progress(completed, verbose=True)
        == "  [0] Node planner completed."
    )


def test_verbose_progress_streams_an_agents_own_completion() -> None:
    completed = agent_event(
        agent_name="researcher",
        event_type="researcher.research.completed",
        message="Research pass complete.",
        metadata={"iteration": 1, "finding_count": 4},
    )

    assert render_progress(completed, verbose=False) is None
    assert render_progress(completed, verbose=True) == (
        "  [1] Research pass complete."
    )


def test_verbose_progress_streams_the_enumerated_provider_failure() -> None:
    failure = agent_event(
        agent_name="critic",
        event_type="agent.provider_failure",
        message="The model provider failed during the review.",
        metadata={"iteration": 0},
    )

    assert render_progress(failure, verbose=False) is None
    assert render_progress(failure, verbose=True) == (
        "  [0] The model provider failed during the review."
    )


def test_verbose_progress_streams_nothing_else() -> None:
    """Verbose adds completions and typed errors, never every agent record."""
    started = agent_event(
        agent_name="researcher",
        event_type="researcher.sub_topic.started",
        message="Sub-topic research started.",
        metadata={"iteration": 0},
    )
    tool_call = agent_event(
        agent_name="researcher",
        event_type="researcher.tool_call",
        message="The researcher called web_search.",
        metadata={"iteration": 0},
    )

    assert render_progress(started, verbose=True) is None
    assert render_progress(tool_call, verbose=True) is None


# --- grouped warnings ------------------------------------------------------


def test_warnings_group_by_the_source_that_recorded_them() -> None:
    lines = render_warnings(build_outcome(state=error_state()))

    assert lines == [
        "Warnings:",
        "  agent.researcher: 2 errors (coverage topic-03, topic-05)",
        "  tools.web_search: 1 error",
    ]


def test_warnings_omit_the_coverage_fragment_when_none_is_named() -> None:
    state = ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        errors=[
            ResearchError(
                error_type="web_search_failed",
                source="tools.web_search",
                message="The search provider timed out.",
            )
        ],
    )

    assert render_warnings(build_outcome(state=state)) == [
        "Warnings:",
        "  tools.web_search: 1 error",
    ]


def test_no_errors_means_no_warnings() -> None:
    assert render_warnings(build_outcome()) == []


def test_verbose_warnings_add_the_typed_messages() -> None:
    lines = render_warnings(build_outcome(state=error_state()), verbose=True)

    assert lines == [
        "Warnings:",
        "  agent.researcher: 2 errors (coverage topic-03, topic-05)",
        "    warning: [researcher_sub_topic_skipped] A planned sub-topic was "
        "never researched; the report will be incomplete for it.",
        "    warning: [researcher_extraction_provider_error] The model "
        "provider failed during research.",
        "  tools.web_search: 1 error",
        "    warning: [web_search_failed] The search provider timed out.",
    ]


# --- the summary -----------------------------------------------------------


def test_the_summary_leads_with_the_quality_block() -> None:
    outcome = build_outcome(
        state=quality_state(), evidence_path=EVIDENCE_PATH
    )

    lines = render_summary(outcome, verbose=False)
    joined = "\n".join(lines)

    assert (
        "Quality: partial (critic 6/10; 3/7 topics covered, 43%)" in joined
    )
    assert (
        "Evidence: 12 cited sources; 10 scored; 14 verified, 1 contradicted"
        in joined
    )
    assert (
        "Integrity: 0 duplicate claims; 0 duplicate source rows; "
        "0 uncited settled points" in joined
    )
    assert "Open coverage: topic-03, topic-05, topic-06, topic-07" in joined
    assert f"Report: {REPORT_PATH}" in joined
    assert f"Evidence ledger: {EVIDENCE_PATH}" in joined


def test_the_summary_prints_identity_and_status_before_the_quality_block() -> None:
    outcome = build_outcome(
        state=quality_state(), evidence_path=EVIDENCE_PATH
    )

    lines = render_summary(outcome, verbose=False)

    assert index_of(lines, "Session ID:") < index_of(lines, "Status:")
    assert index_of(lines, "Status:") < index_of(lines, "Quality:")
    assert index_of(lines, "Quality:") < index_of(lines, "Evidence:")
    assert index_of(lines, "Evidence:") < index_of(lines, "Integrity:")
    assert index_of(lines, "Integrity:") < index_of(lines, "Open coverage:")
    assert index_of(lines, "Open coverage:") < index_of(lines, "Report:")
    assert index_of(lines, "Report:") < index_of(lines, "Evidence ledger:")


def test_the_critic_fragment_is_omitted_without_a_model_review() -> None:
    """Step 3: the critic fragment needs a model review to exist."""
    outcome = build_outcome(
        state=quality_state(
            with_critique=False,
            quality=quality_snapshot(
                coverage_ratio=1.0,
                planned_topics=2,
                covered_topics=2,
                unresolved_topic_ids=[],
            ),
        )
    )

    joined = "\n".join(render_summary(outcome, verbose=False))

    assert "Quality: partial (2/2 topics covered, 100%)" in joined
    assert "critic" not in joined


def test_an_accepted_run_says_accepted() -> None:
    outcome = build_outcome(
        state=quality_state(critique=accepting_critique())
    )

    joined = "\n".join(render_summary(outcome, verbose=False))

    assert "Quality: accepted (critic 6/10; 3/7 topics covered, 43%)" in joined


def test_the_quality_line_stands_alone_without_a_snapshot() -> None:
    """No quality pass judged this run: no counts are invented for it."""
    lines = render_summary(build_outcome(state=progress_state()), verbose=False)
    joined = "\n".join(lines)

    assert "Quality: partial" in joined
    assert "Evidence:" not in joined
    assert "Integrity:" not in joined
    assert "Open coverage:" not in joined


def test_open_coverage_is_omitted_when_nothing_is_open() -> None:
    outcome = build_outcome(
        state=quality_state(
            quality=quality_snapshot(
                coverage_ratio=1.0, covered_topics=7, unresolved_topic_ids=[]
            )
        )
    )

    joined = "\n".join(render_summary(outcome, verbose=False))

    assert "Open coverage:" not in joined


def test_a_missing_evidence_ledger_file_says_so() -> None:
    outcome = build_outcome(state=quality_state(), evidence_path=None)

    joined = "\n".join(render_summary(outcome, verbose=False))

    assert "Evidence ledger: not written to disk" in joined


def test_the_scored_count_comes_back_exactly() -> None:
    """``scored_cited_source_ratio`` is ``scored / cited`` by construction."""
    exact = build_outcome(
        state=quality_state(
            quality=quality_snapshot(
                cited_sources=3, scored_cited_source_ratio=2 / 3
            )
        )
    )
    none_cited = build_outcome(
        state=quality_state(
            quality=quality_snapshot(
                cited_sources=0, scored_cited_source_ratio=0.0
            )
        )
    )

    assert "3 cited sources; 2 scored;" in "\n".join(
        render_summary(exact, verbose=False)
    )
    assert "0 cited sources; 0 scored;" in "\n".join(
        render_summary(none_cited, verbose=False)
    )


def test_the_summary_names_the_session_status_and_report() -> None:
    lines = render_summary(build_outcome(), verbose=False)

    joined = "\n".join(lines)
    assert "Session ID: session-1" in joined
    assert "Status: completed" in joined
    assert f"Report: {REPORT_PATH}" in joined
    assert "Trace" not in joined


def test_the_summary_prints_the_trace_url_when_there_is_one() -> None:
    lines = render_summary(
        build_outcome(trace_url="https://smith.example/run/1"), verbose=False
    )

    assert "Trace: https://smith.example/run/1" in "\n".join(lines)


def test_the_summary_is_explicit_when_no_report_reached_disk() -> None:
    lines = render_summary(build_outcome(report_path=None), verbose=False)

    assert "Report: not written to disk" in "\n".join(lines)


def test_a_limited_run_says_so_without_calling_itself_a_failure() -> None:
    lines = render_summary(build_outcome(status="max_iterations"), verbose=False)

    joined = "\n".join(lines)
    assert "Status: max_iterations" in joined
    assert "refinement budget" in joined


def test_verbose_summary_reports_tool_calls_and_tokens() -> None:
    outcome = build_outcome(
        token_usage=TokenUsage(input_tokens=900, output_tokens=100),
        tool_calls=(
            ToolCallSummary(tool_name="web_search", calls=4, failures=1),
            ToolCallSummary(tool_name="query_memory", calls=2, failures=0),
        ),
    )

    joined = "\n".join(render_summary(outcome, verbose=True))

    assert "web_search: 4 calls (1 failed)" in joined
    assert "query_memory: 2 calls" in joined
    assert "Tokens: 1000 total (900 in / 100 out)" in joined


def test_verbose_summary_says_tokens_are_unavailable_when_none_were_seen() -> None:
    joined = "\n".join(render_summary(build_outcome(), verbose=True))

    assert "Tokens: not available" in joined


def test_verbose_summary_is_explicit_when_no_tool_calls_were_recorded() -> None:
    joined = "\n".join(render_summary(build_outcome(), verbose=True))

    assert "Tool calls: none recorded" in joined


# --- the request budget ----------------------------------------------------


def budget_snapshot(
    provider: ProviderCategory,
    *,
    attempts: int,
    ceiling: int | None = None,
    effective_limit: int | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> RequestBudgetSnapshot:
    return RequestBudgetSnapshot(
        provider=provider,
        attempts=attempts,
        ceiling=ceiling,
        effective_limit=effective_limit,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def budgeted_outcome(**overrides: object) -> ResearchOutcome:
    """An outcome carrying the three provider categories a run really records."""
    snapshots = overrides.pop(
        "request_budget_snapshots",
        (
            budget_snapshot(
                "deepseek",
                attempts=3,
                ceiling=10,
                effective_limit=10,
                input_tokens=900,
                output_tokens=100,
            ),
            budget_snapshot("openai", attempts=2),
            budget_snapshot(
                "tavily", attempts=11, ceiling=11, effective_limit=11
            ),
        ),
    )
    return build_outcome(request_budget_snapshots=snapshots, **overrides)


def test_the_request_budget_section_names_all_three_providers() -> None:
    joined = "\n".join(render_summary(budgeted_outcome(), verbose=True))

    assert "Request budget:" in joined
    assert "deepseek: 3 attempts reserved" in joined
    assert "openai: 2 attempts reserved" in joined
    assert "tavily: 11 attempts reserved" in joined


def test_a_request_budget_ceiling_renders_its_effective_limit() -> None:
    joined = "\n".join(render_summary(budgeted_outcome(), verbose=True))

    assert "(ceiling 10, effective limit 10)" in joined
    assert "(ceiling 11, effective limit 11)" in joined


def test_an_absent_request_budget_ceiling_is_never_rendered_as_zero() -> None:
    joined = "\n".join(render_summary(budgeted_outcome(), verbose=True))
    line = next(
        row for row in joined.splitlines() if row.strip().startswith("openai:")
    )

    assert "ceiling not set" in line
    assert "0" not in line
    assert "effective limit" not in line


def test_the_request_budget_tokens_are_post_response_counts_not_cost() -> None:
    joined = "\n".join(render_summary(budgeted_outcome(), verbose=True))

    assert "reported post-response" in joined
    assert "not a cost" in joined
    assert "1000 total (900 in / 100 out)" in joined


def test_the_request_budget_section_is_verbose_only() -> None:
    joined = "\n".join(render_summary(budgeted_outcome(), verbose=False))

    assert "Request budget" not in joined
    assert "post-response" not in joined
    assert "deepseek" not in joined
    assert "openai" not in joined
    assert "tavily" not in joined


def test_verbose_summary_does_not_print_request_budget_tokens_twice() -> None:
    """The snapshots carry the tokens once the budget reported them."""
    outcome = budgeted_outcome(
        token_usage=TokenUsage(input_tokens=900, output_tokens=100)
    )

    joined = "\n".join(render_summary(outcome, verbose=True))

    assert joined.count("900 in") == 1
    assert "Tokens: 1000 total (900 in / 100 out)" not in joined


def test_a_legacy_outcome_without_request_budget_snapshots_keeps_the_token_line() -> (
    None
):
    """An injected outcome from before the budget existed still reports."""
    outcome = build_outcome(
        token_usage=TokenUsage(input_tokens=900, output_tokens=100)
    )

    joined = "\n".join(render_summary(outcome, verbose=True))

    assert "Tokens: 1000 total (900 in / 100 out)" in joined


def test_a_scraper_failure_and_request_budget_render_no_urls_or_queries() -> None:
    """Bounded fields only: no error text, no URL, no query content."""
    state = ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        errors=[
            ResearchError(
                error_type="scraper_failure",
                source="tools.scraper",
                message="The scraper could not read one source.",
                details={"url": "https://example.invalid/secret-page"},
            )
        ],
    )
    outcome = budgeted_outcome(state=state)

    joined = "\n".join(
        render_summary(outcome, verbose=True)
        + render_warnings(outcome, verbose=True)
    )

    assert "Request budget:" in joined
    assert "https://" not in joined
    assert "example.invalid" not in joined
    assert "secret-page" not in joined
    assert QUESTION not in joined
