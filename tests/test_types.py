"""Tests for shared research domain contracts."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from deep_research.utils.types import (
    Claim,
    Critique,
    Finding,
    MemorySnapshot,
    ResearchError,
    ResearchEvent,
    ScoredSource,
    SubTopic,
)


def scored_source(**overrides: object) -> ScoredSource:
    values = {
        "url": "https://example.com/source",
        "title": "Example source",
        "authority_score": 0.8,
        "recency_score": 0.7,
        "relevance_score": 0.9,
        "corroboration_score": 0.6,
        "overall_score": 0.75,
        "rationale": "Relevant and independently supported.",
    }
    values.update(overrides)
    return ScoredSource.model_validate(values)


def critique(**overrides: object) -> Critique:
    values = {
        "score": 7,
        "gaps": [],
        "unsupported_claims": [],
        "recommended_queries": [],
        "should_continue": False,
        "rationale": "The report meets the threshold.",
    }
    values.update(overrides)
    return Critique.model_validate(values)


def test_domain_models_preserve_required_fields() -> None:
    topic = SubTopic(
        title="Adoption",
        rationale="Measure current adoption patterns.",
        search_queries=["enterprise AI adoption 2026"],
        success_criteria=["Find two independent estimates."],
        priority=1,
    )
    finding = Finding(
        content="Adoption increased year over year.",
        source_url="not-validated-at-this-boundary",
        source_title="Industry survey",
        extracted_at="2026-07-25T12:00:00+00:00",
        confidence=0.8,
        related_sub_topic="Adoption",
    )
    claim = Claim(
        text="Adoption increased year over year.",
        source_urls=["https://example.com/a", "https://example.org/b"],
        verdict="verified",
        confidence=0.9,
        evidence=["Two independent surveys report an increase."],
        contradictions=["One regional survey reported flat adoption."],
    )
    memory = MemorySnapshot(
        similar_findings=[finding],
        known_source_reputations={"example.com": 0.85},
        suggested_strategies=["Compare independent surveys."],
    )

    assert topic.priority == 1
    assert finding.source_url == "not-validated-at-this-boundary"
    assert claim.contradictions == ["One regional survey reported flat adoption."]
    assert memory.similar_findings == [finding]


@pytest.mark.parametrize("value", [-0.01, 1.01])
def test_unit_scores_reject_out_of_range_values(value: float) -> None:
    with pytest.raises(ValidationError):
        scored_source(authority_score=value)


@pytest.mark.parametrize("value", [0, 11])
def test_critic_score_rejects_out_of_range_values(value: int) -> None:
    with pytest.raises(ValidationError):
        critique(score=value)


def test_finding_rejects_timezone_naive_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        Finding(
            content="A finding.",
            source_url="https://example.com/source",
            source_title="Example source",
            extracted_at="2026-07-25T12:00:00",
            confidence=0.8,
            related_sub_topic="Adoption",
        )


def test_missing_required_field_fails_validation() -> None:
    with pytest.raises(ValidationError):
        SubTopic.model_validate(
            {
                "title": "Adoption",
                "rationale": "Measure adoption.",
                "search_queries": ["enterprise adoption"],
                "success_criteria": ["Find two estimates."],
            }
        )


def test_domain_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        scored_source(undocumented_score=0.5)


def test_research_event_serializes_and_round_trips() -> None:
    event = ResearchEvent(
        event_type="agent.started",
        source="planner",
        message="Planner started.",
        timestamp="2026-07-25T12:00:00+00:00",
        metadata={
            "iteration": 0,
            "queries": ["enterprise AI adoption"],
            "counts": {"sub_topics": 3},
        },
    )
    payload = event.model_dump(mode="json")

    assert payload == {
        "event_type": "agent.started",
        "source": "planner",
        "message": "Planner started.",
        "timestamp": "2026-07-25T12:00:00+00:00",
        "metadata": {
            "iteration": 0,
            "queries": ["enterprise AI adoption"],
            "counts": {"sub_topics": 3},
        },
    }
    assert ResearchEvent.model_validate(payload) == event

def test_research_error_serializes_and_round_trips() -> None:
    error = ResearchError(
        error_type="search_timeout",
        source="web_search",
        message="The search provider timed out.",
        recoverable=True,
        timestamp="2026-07-25T12:01:00Z",
        details={"retry_count": 2, "provider": "tavily"},
    )
    payload = error.model_dump(mode="json")
    assert payload["recoverable"] is True
    assert payload["timestamp"] == "2026-07-25T12:01:00Z"
    assert payload["details"] == {"retry_count": 2, "provider": "tavily"}
    assert ResearchError.model_validate(payload) == error

def test_event_and_error_defaults_are_json_safe_and_timezone_aware() -> None:
    event = ResearchEvent(
        event_type="session.started",
        source="orchestrator",
        message="Research session started.",
    )
    error = ResearchError(
        error_type="trace_failure",
        source="langsmith",
        message="Tracing failed; continuing locally.",
    )
    assert datetime.fromisoformat(event.timestamp).utcoffset() is not None
    assert datetime.fromisoformat(error.timestamp).utcoffset() is not None
    assert event.metadata == {}
    assert error.details == {}
    assert error.recoverable is True
