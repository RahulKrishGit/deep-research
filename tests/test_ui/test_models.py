"""Contract tests for framework-light Streamlit presentation models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from deep_research.ui.models import (
    SessionHistoryEntry,
    UiClaimDetail,
    UiCredibilityTier,
    UiFactCheckSummary,
    UiRecentActivity,
    UiSessionSnapshot,
    UiSessionStatus,
    UiSourceDetail,
    UiSourceSummary,
    UiSubTopicProgress,
    UiTokenUsage,
    UiToolCallSummary,
    history_entry_from_snapshot,
)
from deep_research.utils.types import ResearchError


def _source_summary(*, with_details: bool = False) -> UiSourceSummary:
    details = []
    if with_details:
        details.append(
            UiSourceDetail(
                title="Source title",
                url="https://example.org/source",
                tier="high",
                overall_score=0.9,
                rationale="Strong primary source.",
                corroboration_score=0.8,
                related_sub_topics=["Background"],
            )
        )
    return UiSourceSummary(
        total=1,
        high=1,
        moderate=0,
        low=0,
        unrated=0,
        details=details,
    )


def _fact_check_summary(*, with_details: bool = False) -> UiFactCheckSummary:
    details = []
    if with_details:
        details.append(
            UiClaimDetail(
                text="The claim is supported.",
                verdict="verified",
                confidence=0.9,
                source_urls=["https://example.org/source"],
                evidence=["The source directly supports the claim."],
                contradictions=[],
            )
        )
    return UiFactCheckSummary(
        verified=1,
        unverified=0,
        contradicted=0,
        insufficient_evidence=0,
        details=details,
    )


def _snapshot() -> UiSessionSnapshot:
    return UiSessionSnapshot(
        session_id="session-1",
        question="What happened?",
        status="completed",
        started_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 9, 0, 1, tzinfo=timezone.utc),
        current_agent="Researcher",
        iteration=2,
        max_iterations=3,
        sub_topics=[
            UiSubTopicProgress(
                index=1,
                title="Background",
                status="completed",
                priority=1,
                findings=2,
            )
        ],
        recent_activity=[
            UiRecentActivity(event_type="search", summary="Searched sources")
        ],
        tool_calls=[
            UiToolCallSummary(
                tool_name="web_search",
                display_label="Searched sources",
                calls=2,
                failures=0,
            )
        ],
        token_usage=UiTokenUsage(input_tokens=10, output_tokens=20),
        trace_url="https://smith.langchain.com/trace/1",
        report_path="reports/session-1.md",
        report="# Report",
        source_summary=_source_summary(with_details=True),
        fact_check_summary=_fact_check_summary(with_details=True),
        errors=[
            ResearchError(
                error_type="recoverable",
                source="researcher",
                message="A source was skipped.",
                details={
                    "prompt": "private prompt",
                    "api_key": "secret-value",
                    "tool_arguments": {"query": "private query"},
                },
            )
        ],
        limitations=["One source was unavailable."],
        events_seen=4,
    )


@pytest.mark.parametrize(
    "status",
    ["running", "completed", "max_iterations", "incomplete", "failed"],
)
def test_ui_session_status_accepts_the_defined_values(status: UiSessionStatus) -> None:
    snapshot = UiSessionSnapshot.model_validate(
        {**_snapshot().model_dump(), "status": status}
    )

    assert snapshot.status == status


@pytest.mark.parametrize("tier", ["high", "moderate", "low", "unrated"])
def test_source_detail_accepts_the_defined_credibility_tiers(
    tier: UiCredibilityTier,
) -> None:
    detail = UiSourceDetail(
        title="Source",
        url="https://example.org",
        tier=tier,
        overall_score=0.5,
        rationale="Rationale",
        corroboration_score=0.5,
    )

    assert detail.tier == tier


def test_ui_models_reject_unknown_statuses() -> None:
    with pytest.raises(ValidationError):
        UiSessionSnapshot.model_validate(
            {**_snapshot().model_dump(), "status": "paused"}
        )


def test_counts_are_nonnegative_and_token_total_is_derived() -> None:
    assert UiTokenUsage(input_tokens=7, output_tokens=8).total_tokens == 15

    with pytest.raises(ValidationError):
        UiTokenUsage(input_tokens=-1, output_tokens=0)
    with pytest.raises(ValidationError):
        UiToolCallSummary(
            tool_name="search",
            display_label="Searched sources",
            calls=0,
            failures=-1,
        )
    with pytest.raises(ValidationError):
        UiSourceSummary(total=-1, high=0, moderate=0, low=0, unrated=0)
    with pytest.raises(ValidationError):
        UiFactCheckSummary(
            verified=0,
            unverified=0,
            contradicted=0,
            insufficient_evidence=-1,
        )


@pytest.mark.parametrize("status", ["queued", "running", "completed"])
def test_subtopic_progress_accepts_each_lifecycle_status(status: str) -> None:
    progress = UiSubTopicProgress(index=1, title="Topic", status=status)

    assert progress.status == status


def test_models_strip_strings_and_forbid_extra_fields() -> None:
    usage = UiTokenUsage(input_tokens=1, output_tokens=2)
    detail = UiSourceDetail(
        title="  Source  ",
        url="  https://example.org  ",
        tier="high",
        overall_score=0.5,
        rationale="  Rationale  ",
        corroboration_score=0.5,
        related_sub_topics=["  Topic  "],
    )

    with pytest.raises(ValidationError):
        UiTokenUsage(input_tokens=1, output_tokens=2, unexpected=True)

    assert usage.model_dump() == {"input_tokens": 1, "output_tokens": 2}
    assert detail.title == "Source"
    assert detail.url == "https://example.org"
    assert detail.rationale == "Rationale"
    assert detail.related_sub_topics == ["Topic"]


def test_history_entry_contains_only_persistable_metadata() -> None:
    assert set(SessionHistoryEntry.model_fields) == {
        "session_id",
        "question",
        "status",
        "started_at",
        "finished_at",
        "iteration",
        "max_iterations",
        "report_path",
        "trace_url",
        "token_usage",
        "source_summary",
        "fact_check_summary",
        "errors",
        "limitations",
    }
    forbidden_fields = {
        "report",
        "events",
        "recent_activity",
        "raw_findings",
        "prompts",
        "tool_results",
        "model_messages",
    }
    assert forbidden_fields.isdisjoint(SessionHistoryEntry.model_fields)


def test_history_entry_from_snapshot_strips_detail_payloads() -> None:
    history = history_entry_from_snapshot(_snapshot())
    payload = history.model_dump(mode="json")

    assert isinstance(history, SessionHistoryEntry)
    assert payload["source_summary"]["details"] == []
    assert payload["fact_check_summary"]["details"] == []
    assert payload["source_summary"]["total"] == 1
    assert payload["fact_check_summary"]["verified"] == 1
    assert "report" not in payload
    assert "events" not in payload


def test_history_entry_sanitizes_errors_and_breaks_snapshot_aliases() -> None:
    snapshot = _snapshot()
    history = history_entry_from_snapshot(snapshot)

    assert history.errors[0] is not snapshot.errors[0]
    assert history.errors[0].details == {}
    assert "private prompt" not in history.model_dump_json()
    assert "secret-value" not in history.model_dump_json()
    assert "private query" not in history.model_dump_json()

    snapshot.errors[0].message = "changed after compaction"
    assert history.errors[0].message == "A source was skipped."


def test_history_entry_model_enforces_error_detail_redaction() -> None:
    snapshot = _snapshot()
    history = SessionHistoryEntry(
        session_id=snapshot.session_id,
        question=snapshot.question,
        status=snapshot.status,
        started_at=snapshot.started_at,
        iteration=snapshot.iteration,
        max_iterations=snapshot.max_iterations,
        source_summary=snapshot.source_summary,
        fact_check_summary=snapshot.fact_check_summary,
        errors=snapshot.errors,
    )

    assert history.errors[0].details == {}
    assert history.errors[0] is not snapshot.errors[0]
