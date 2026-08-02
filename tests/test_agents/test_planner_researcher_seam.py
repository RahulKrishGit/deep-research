"""End-to-end seam test: PlannerAgent's output feeds ResearcherAgent.

Every other ``ResearcherAgent`` test builds ``ResearchState(sub_topics=...)``
by hand, so nothing ever exercised the actual seam between the Planner
(which produces 3-7 sub-topics) and the Researcher (whose
``max_sub_topics`` defaults to 3). That gap is exactly why a 5-sub-topic,
all-high-priority plan used to have 2 sub-topics vanish with no record in
``state.errors`` and no trace in the event stream — see the Finding 1 fix in
``researcher.py``. This test runs the real Planner, merges its plan into
``ResearchState`` the way the orchestrator would, then runs the real
Researcher against it and asserts every sub-topic is accounted for.
"""

from __future__ import annotations

import pytest

from deep_research.agents.planner import PlannerAgent, ResearchPlanDraft, SubTopicDraft
from deep_research.agents.researcher import DEFAULT_MAX_SUB_TOPICS, ResearcherAgent
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    MemorySnapshot,
    ResearchState,
    merge_research_state,
)
from tests.agent_fakes import ScriptedCompleter, finish, use_tool
from tests.research_fakes import (
    FakeSearchClient,
    planner_tools,
    research_tools,
    search_response,
)

SUB_TOPIC_TITLES = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"]


def _plan_draft() -> ResearchPlanDraft:
    """Five sub-topics, all priority 1 — all high-priority, all above the cap."""
    return ResearchPlanDraft(
        sub_topics=[
            SubTopicDraft(
                title=title,
                rationale=f"{title} is load-bearing for the answer.",
                search_queries=[f"{title} 2025"],
                success_criteria=[f"A named source about {title}."],
                priority=1,
            )
            for title in SUB_TOPIC_TITLES
        ]
    )


def _state() -> ResearchState:
    return ResearchState(
        session_id="session-1",
        original_question="What are the security implications of quantum computing?",
        memory_context=MemorySnapshot(),
    )


def _search_and_scrape_decisions(query: str) -> list[object]:
    return [
        use_tool("Find sources.", "web_search", f'{{"query": "{query}"}}'),
        use_tool(
            "Read the best source.",
            "web_scraper",
            '{"url": "https://example.test/qec"}',
        ),
        finish("I have a source-backed answer.", "Evidence found."),
    ]


@pytest.mark.asyncio
async def test_a_full_planner_output_composes_into_the_researcher(
    tracker: Tracker,
) -> None:
    planner_completer = ScriptedCompleter(
        decisions=[finish("I understand the question.", "Five angles matter.")],
        outputs=[_plan_draft()],
    )
    planner = PlannerAgent(
        provider=planner_completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name="planner", max_entries=20
        ),
        tools=planner_tools(tracker),
        config=AgentRuntimeConfig(max_iterations=3, tool_budget=3),
    )

    state = _state()
    async with tracker.session_span("session-1", state.original_question):
        planner_outcome = await planner.run(state)
    state = merge_research_state(state, planner_outcome.state_update)

    assert [sub_topic.title for sub_topic in state.sub_topics] == SUB_TOPIC_TITLES
    assert DEFAULT_MAX_SUB_TOPICS == 3

    from deep_research.agents.researcher import FindingDraft, SubTopicFindingsDraft

    def _findings_draft(title: str) -> SubTopicFindingsDraft:
        return SubTopicFindingsDraft(
            findings=[
                FindingDraft(
                    content=f"{title} finding.",
                    source_url="https://example.test/qec",
                    source_title="Quantum error correction in 2025",
                    confidence=0.8,
                )
            ]
        )

    researched_titles = SUB_TOPIC_TITLES[:DEFAULT_MAX_SUB_TOPICS]
    researcher_completer = ScriptedCompleter(
        decisions=[
            decision
            for title in researched_titles
            for decision in _search_and_scrape_decisions(title)
        ],
        outputs=[_findings_draft(title) for title in researched_titles],
    )
    researcher = ResearcherAgent(
        provider=researcher_completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name="researcher", max_entries=20
        ),
        tools=research_tools(
            tracker,
            search=FakeSearchClient([search_response() for _ in researched_titles]),
        ),
        config=AgentRuntimeConfig(max_iterations=4, tool_budget=4),
    )

    async with tracker.session_span("session-1", state.original_question):
        researcher_outcome = await researcher.run(state)
    state = merge_research_state(state, researcher_outcome.state_update)

    # The 3 sub-topics under the cap were actually researched and produced
    # findings.
    assert [
        finding.related_sub_topic for finding in state.raw_findings
    ] == researched_titles

    # The 2 sub-topics the max_sub_topics cap dropped (Delta, Epsilon) must
    # be recorded as skipped, not silently missing — this is the regression
    # pin for Finding 1.
    skipped_errors = {
        error.details["sub_topic"]: error
        for error in state.errors
        if error.error_type == "researcher_sub_topic_skipped"
    }
    assert set(skipped_errors) == {"Delta", "Epsilon"}
    for error in skipped_errors.values():
        assert error.recoverable is True
        assert error.details["reason"] == "cap"

    # The event stream agrees: 5 planned, 3 researched, 2 skipped.
    completed_event = next(
        event
        for event in researcher_outcome.state_update["events"]
        if event.event_type == "researcher.research.completed"
    )
    assert completed_event.metadata["sub_topics_planned"] == 5
    assert completed_event.metadata["sub_topics_researched"] == 3
    assert completed_event.metadata["sub_topics_skipped"] == 2
