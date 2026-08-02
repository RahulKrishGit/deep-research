"""Tests for structured agent progress events."""

from __future__ import annotations

import pytest

from deep_research.agents.events import agent_event


def test_agent_event_namespaces_the_source_by_agent_name() -> None:
    event = agent_event(
        agent_name="planner",
        event_type="planner.planning.started",
        message="Planning started.",
    )

    assert event.source == "agent.planner"
    assert event.event_type == "planner.planning.started"
    assert event.metadata == {}
    assert event.timestamp


def test_agent_event_carries_counted_metadata() -> None:
    event = agent_event(
        agent_name="researcher",
        event_type="researcher.sub_topic.completed",
        message="Sub-topic 1 complete.",
        metadata={"findings": 2, "stop_reason": "finished", "error_type": None},
    )

    assert event.metadata == {
        "findings": 2,
        "stop_reason": "finished",
        "error_type": None,
    }


def test_agent_event_copies_its_metadata_mapping() -> None:
    metadata: dict[str, int] = {"findings": 1}

    event = agent_event(
        agent_name="researcher",
        event_type="researcher.sub_topic.completed",
        message="Sub-topic 1 complete.",
        metadata=metadata,
    )
    metadata["findings"] = 99

    assert event.metadata == {"findings": 1}


@pytest.mark.parametrize(
    ("agent_name", "event_type", "match"),
    [
        ("   ", "planner.planning.started", "agent_name must not be blank"),
        ("planner", "  ", "event_type must not be blank"),
    ],
)
def test_agent_event_rejects_blank_identifiers(
    agent_name: str, event_type: str, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        agent_event(
            agent_name=agent_name,
            event_type=event_type,
            message="Planning started.",
        )
