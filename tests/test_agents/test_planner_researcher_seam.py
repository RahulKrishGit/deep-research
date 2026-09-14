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

The plan is five materially different constraint mechanisms for one
unqualified question, each carrying its own evidence type, jurisdiction, and
measurement — an all-placeholder Alpha/Beta fixture would satisfy the id and
coverage assertions without ever showing that a real plan can be separated.
The mechanisms live here as a fixture, not in production: the planner holds
no taxonomy of its own.
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

# Draft order, deliberately not priority order: the planner is what orders
# the plan, so the state the Researcher reads is in priority order however
# the model listed the sub-topics. Every mechanism is drafted at or above
# ``researcher.HIGH_PRIORITY_THRESHOLD`` (2), because only a high-priority
# sub-topic the cap drops gets a recoverable ``researcher_sub_topic_skipped``
# record — the Finding 1 regression this seam test pins.
_DRAFTED_SUB_TOPICS = (
    (
        "Wholesale market rules and storage compensation",
        2,
        "FERC Order 841 storage market participation compensation 2026",
        "An ISO market filing documents the United States compensation a "
        "2026 storage project can earn.",
    ),
    (
        "Grid connection and interconnection queue position",
        1,
        "FERC interconnection queue storage wait times 2026",
        "A filing or queue dataset gives measured United States "
        "interconnection wait times for storage in 2026.",
    ),
    (
        "Project economics and financing",
        2,
        "grid-scale battery storage levelized cost financing 2026",
        "A lender or utility filing reports the measured United States cost "
        "and financing terms for 2026 projects.",
    ),
    (
        "Siting, permitting, and fire safety",
        1,
        "NFPA 855 UL 9540A local siting permit requirements 2026",
        "A standard or permit record names the United States fire-safety "
        "thresholds a 2026 project must meet.",
    ),
    (
        "Equipment supply chain and trade exposure",
        1,
        "battery cell supply chain tariffs 2026 United States",
        "A trade dataset or standards-body report measures United States "
        "cell and inverter lead times in 2026.",
    ),
)

SUB_TOPIC_TITLES = [
    "Grid connection and interconnection queue position",
    "Siting, permitting, and fire safety",
    "Equipment supply chain and trade exposure",
    "Wholesale market rules and storage compensation",
    "Project economics and financing",
]

# What makes a success criterion source-oriented rather than a restatement of
# the title: a named evidence type, in a named jurisdiction.
EVIDENCE_TYPES = ("filing", "dataset", "standard", "study", "report", "order")
JURISDICTIONS = ("United States", "FERC", "ERCOT", "CAISO", "EU", "NFPA")


def _plan_draft() -> ResearchPlanDraft:
    """Five constraint mechanisms, drafted out of priority order."""
    return ResearchPlanDraft(
        sub_topics=[
            SubTopicDraft(
                title=title,
                rationale=f"{title} constrains what can be deployed.",
                search_queries=[query],
                success_criteria=[criterion],
                priority=priority,
            )
            for title, priority, query, criterion in _DRAFTED_SUB_TOPICS
        ]
    )


def _state() -> ResearchState:
    return ResearchState(
        session_id="session-1",
        original_question=(
            "What are the current constraints on grid-scale battery storage "
            "deployment?"
        ),
        memory_context=MemorySnapshot(),
    )


def _search_and_scrape_decisions(query: str) -> list[object]:
    return [
        use_tool("Find sources.", "web_search", f'{{"query": "{query}"}}'),
        use_tool(
            "Read the best source.",
            "web_scraper",
            '{"url": "https://example.test/interconnection"}',
        ),
        finish("I have a source-backed answer.", "Evidence found."),
    ]


@pytest.mark.asyncio
async def test_a_full_planner_output_composes_into_the_researcher(
    tracker: Tracker,
) -> None:
    # The fixture is only evidence of ordering if the draft disagrees with
    # the order the plan must end up in.
    assert [title for title, _, _, _ in _DRAFTED_SUB_TOPICS] != SUB_TOPIC_TITLES

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
    # Every planned sub-topic carries the id the planner stamped for its
    # position in priority order, and its success criterion names both the
    # evidence type and the jurisdiction that would settle it. Titles are no
    # longer the only thing a later stage can report coverage against.
    assert [sub_topic.coverage_id for sub_topic in state.sub_topics] == [
        f"topic-{position:02d}" for position in range(1, 6)
    ]
    for sub_topic in state.sub_topics:
        criterion = " ".join(sub_topic.success_criteria)
        assert any(
            term in criterion.casefold() for term in EVIDENCE_TYPES
        ), sub_topic.coverage_id
        assert any(
            place in criterion for place in JURISDICTIONS
        ), sub_topic.coverage_id

    from deep_research.agents.researcher import FindingDraft, SubTopicFindingsDraft

    def _findings_draft(title: str) -> SubTopicFindingsDraft:
        return SubTopicFindingsDraft(
            findings=[
                FindingDraft(
                    content=f"{title} finding.",
                    source_url="https://example.test/interconnection",
                    source_title="Grid-scale storage deployment data",
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

    # The 2 sub-topics the max_sub_topics cap dropped (the two least
    # important mechanisms) must be recorded as skipped, not silently
    # missing — this is the regression pin for Finding 1.
    skipped_errors = {
        error.details["sub_topic"]: error
        for error in state.errors
        if error.error_type == "researcher_sub_topic_skipped"
    }
    assert set(skipped_errors) == set(SUB_TOPIC_TITLES[DEFAULT_MAX_SUB_TOPICS:])
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
