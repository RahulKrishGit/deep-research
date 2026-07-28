"""Tests for research state construction, serialization, and updates."""

import pytest
from pydantic import ValidationError

from deep_research.utils.types import (
    Claim,
    Critique,
    Finding,
    MemorySnapshot,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ScoredSource,
    SubTopic,
    advance_research_iteration,
    merge_research_state,
)


def sub_topic(title: str = "Adoption", priority: int = 1) -> SubTopic:
    return SubTopic(
        title=title,
        rationale=f"Research {title.lower()}.",
        search_queries=[f"{title.lower()} evidence"],
        success_criteria=[f"Find evidence about {title.lower()}."],
        priority=priority,
    )


def finding(content: str = "Adoption increased.") -> Finding:
    return Finding(
        content=content,
        source_url="https://example.com/source",
        source_title="Example source",
        extracted_at="2026-07-25T12:00:00+00:00",
        confidence=0.8,
        related_sub_topic="Adoption",
    )


def source(title: str = "Example source") -> ScoredSource:
    return ScoredSource(
        url="https://example.com/source",
        title=title,
        authority_score=0.8,
        recency_score=0.7,
        relevance_score=0.9,
        corroboration_score=0.6,
        overall_score=0.75,
        rationale="Relevant and independently corroborated.",
    )


def claim(text: str = "Adoption increased.") -> Claim:
    return Claim(
        text=text,
        source_urls=["https://example.com/source"],
        verdict="verified",
        confidence=0.9,
        evidence=["The source reports a year-over-year increase."],
        contradictions=[],
    )


def critique(score: int = 8) -> Critique:
    return Critique(
        score=score,
        gaps=[],
        unsupported_claims=[],
        recommended_queries=[],
        should_continue=False,
        rationale="The report is complete.",
    )


def test_default_state_construction_uses_independent_values() -> None:
    first = ResearchState(session_id="session-1", original_question="Question one?")
    second = ResearchState(session_id="session-2", original_question="Question two?")

    first.sub_topics.append(sub_topic())
    first.memory_context.suggested_strategies.append("Compare surveys.")

    assert first.iteration == 0
    assert first.max_iterations == 3
    assert first.report is None
    assert first.critique is None
    assert second.sub_topics == []
    assert second.memory_context == MemorySnapshot()
    assert second.events == []
    assert second.errors == []


def test_state_round_trips_through_json_compatible_dict() -> None:
    state = ResearchState(
        session_id="session-1",
        original_question="How is enterprise AI adoption changing?",
        sub_topics=[sub_topic()],
        raw_findings=[finding()],
        evaluated_sources=[source()],
        verified_claims=[claim()],
        report="# Research report",
        critique=critique(),
        iteration=1,
        max_iterations=3,
        memory_context=MemorySnapshot(
            similar_findings=[finding("Prior adoption also increased.")],
            known_source_reputations={"example.com": 0.85},
            suggested_strategies=["Compare independent surveys."],
        ),
        events=[
            ResearchEvent(
                event_type="agent.completed",
                source="planner",
                message="Planner completed.",
                timestamp="2026-07-25T12:01:00+00:00",
                metadata={"sub_topic_count": 1},
            )
        ],
        errors=[
            ResearchError(
                error_type="search_timeout",
                source="web_search",
                message="One search request timed out.",
                timestamp="2026-07-25T12:02:00+00:00",
                details={"retry_count": 2},
            )
        ],
    )

    payload = state.model_dump(mode="json")
    restored = ResearchState.model_validate(payload)

    assert restored == state
    assert restored.model_dump(mode="json") == payload


@pytest.mark.parametrize(
    ("session_id", "question"),
    [("", "A question?"), ("session-1", "")],
)
def test_state_rejects_empty_identity_fields(
    session_id: str,
    question: str,
) -> None:
    with pytest.raises(ValidationError):
        ResearchState(session_id=session_id, original_question=question)


def test_state_rejects_iteration_above_maximum() -> None:
    with pytest.raises(ValidationError, match="iteration cannot exceed max_iterations"):
        ResearchState(
            session_id="session-1",
            original_question="A question?",
            iteration=4,
            max_iterations=3,
        )


@pytest.mark.parametrize(
    ("field_name", "item"),
    [
        ("sub_topics", sub_topic()),
        ("raw_findings", finding()),
        ("evaluated_sources", source()),
        ("verified_claims", claim()),
        (
            "events",
            ResearchEvent(
                event_type="agent.started",
                source="researcher",
                message="Researcher started.",
            ),
        ),
        (
            "errors",
            ResearchError(
                error_type="search_timeout",
                source="web_search",
                message="Search timed out.",
            ),
        ),
    ],
)
def test_merge_appends_lists_without_mutating_original(
    field_name: str,
    item: object,
) -> None:
    state = ResearchState(session_id="session-1", original_question="A question?")

    merged = merge_research_state(state, {field_name: [item]})

    assert getattr(merged, field_name) == [item]
    assert getattr(state, field_name) == []


def test_merge_preserves_multi_item_append_order() -> None:
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        sub_topics=[sub_topic("Existing")],
    )

    merged = merge_research_state(
        state,
        {"sub_topics": [sub_topic("First"), sub_topic("Second")]},
    )

    assert [topic.title for topic in merged.sub_topics] == [
        "Existing",
        "First",
        "Second",
    ]


def test_merge_isolates_supplied_append_items() -> None:
    supplied = sub_topic("Supplied")
    state = ResearchState(session_id="session-1", original_question="A question?")

    merged = merge_research_state(state, {"sub_topics": [supplied]})
    merged.sub_topics[0].search_queries.append("new query")

    assert merged.sub_topics[0].search_queries == ["supplied evidence", "new query"]
    assert supplied.search_queries == ["supplied evidence"]


def test_merge_replaces_scalars_critique_report_and_memory() -> None:
    old_critique = critique(score=6).model_copy(update={"should_continue": True})
    new_critique = critique(score=9)
    new_memory = MemorySnapshot(
        similar_findings=[finding("A recalled finding.")],
        known_source_reputations={"example.com": 0.9},
        suggested_strategies=["Prefer primary sources."],
    )
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        report="Old report",
        critique=old_critique,
        memory_context=MemorySnapshot(suggested_strategies=["Old strategy."]),
    )

    merged = merge_research_state(
        state,
        {
            "report": "New report",
            "critique": new_critique,
            "max_iterations": 5,
            "memory_context": new_memory,
        },
    )

    assert merged.report == "New report"
    assert merged.critique == new_critique
    assert merged.max_iterations == 5
    assert merged.memory_context == new_memory
    assert state.report == "Old report"
    assert state.critique == old_critique


def test_merge_validates_invalid_scalar_replacement() -> None:
    state = ResearchState(session_id="session-1", original_question="A question?")

    with pytest.raises(ValidationError):
        merge_research_state(state, {"max_iterations": 0})


def test_merge_deep_copies_unchanged_nested_values() -> None:
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        memory_context=MemorySnapshot(similar_findings=[finding()]),
    )

    merged = merge_research_state(state, {"report": "Draft report"})
    merged.memory_context.similar_findings.append(finding("A new finding."))

    assert len(merged.memory_context.similar_findings) == 2
    assert len(state.memory_context.similar_findings) == 1


def test_merge_deep_copies_supplied_nested_values() -> None:
    replacement = MemorySnapshot(similar_findings=[finding()])
    state = ResearchState(session_id="session-1", original_question="A question?")

    merged = merge_research_state(state, {"memory_context": replacement})
    merged.memory_context.similar_findings.append(finding("A new finding."))

    assert len(merged.memory_context.similar_findings) == 2
    assert len(replacement.similar_findings) == 1


def test_merge_rejects_unknown_fields() -> None:
    state = ResearchState(session_id="session-1", original_question="A question?")

    with pytest.raises(ValueError, match="unknown ResearchState fields"):
        merge_research_state(state, {"unknown": "value"})


def test_merge_rejects_iteration_changes() -> None:
    state = ResearchState(session_id="session-1", original_question="A question?")

    with pytest.raises(ValueError, match="advance_research_iteration"):
        merge_research_state(state, {"iteration": 1})


def test_graph_iteration_advance_returns_a_new_state() -> None:
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        iteration=1,
        max_iterations=3,
    )

    advanced = advance_research_iteration(state)

    assert advanced.iteration == 2
    assert state.iteration == 1


def test_graph_iteration_cannot_advance_past_maximum() -> None:
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        iteration=3,
        max_iterations=3,
    )

    with pytest.raises(ValueError, match="max_iterations"):
        advance_research_iteration(state)
