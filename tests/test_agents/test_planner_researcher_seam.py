"""End-to-end seam test: PlannerAgent's output feeds ResearcherAgent.

Every other ``ResearcherAgent`` test builds ``ResearchState(sub_topics=...)``
by hand, so nothing ever exercised the actual seam between the Planner
(which produces 3-7 sub-topics) and the Researcher (whose
``max_sub_topics`` used to default to 3). That gap is exactly why a
5-sub-topic, all-high-priority plan used to have 2 sub-topics vanish with no
record in ``state.errors`` and no trace in the event stream — see the Finding
1 fix in ``researcher.py``. This test runs the real Planner, merges its plan
into ``ResearchState`` the way the orchestrator would, then runs the real
Researcher against it and asserts every sub-topic is accounted for: the
default cap now attempts the whole plan, and every planned coverage id ends
with either a source-backed finding or an explicit record saying why not.

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
from deep_research.agents.researcher import (
    DEFAULT_MAX_SUB_TOPICS,
    FindingDraft,
    ResearcherAgent,
    SubTopicFindingsDraft,
)
from deep_research.agents.steps import summarize_text
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ProviderTimeoutError
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
# the model listed the sub-topics. Priorities deliberately span the
# ``HIGH_PRIORITY_THRESHOLD`` boundary: the last mechanism is drafted at
# priority 3, below it, so a skipped low-priority sub-topic has to be named
# too — being unattempted is what earns a ``researcher_sub_topic_skipped``
# record, not being important.
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
        3,
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


async def _planned_state(tracker: Tracker) -> ResearchState:
    """Run the real Planner and merge its plan the way the orchestrator does."""
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
    return merge_research_state(state, planner_outcome.state_update)


def _researcher_for(
    tracker: Tracker,
    decisions: list[object],
    outputs: list[object],
) -> ResearcherAgent:
    return ResearcherAgent(
        provider=ScriptedCompleter(decisions=decisions, outputs=outputs),
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name="researcher", max_entries=20
        ),
        tools=research_tools(
            tracker,
            search=FakeSearchClient([search_response() for _ in decisions]),
        ),
        config=AgentRuntimeConfig(max_iterations=4, tool_budget=4),
    )


@pytest.mark.asyncio
async def test_a_full_planner_output_composes_into_the_researcher(
    tracker: Tracker,
) -> None:
    # The fixture is only evidence of ordering if the draft disagrees with
    # the order the plan must end up in.
    assert [title for title, _, _, _ in _DRAFTED_SUB_TOPICS] != SUB_TOPIC_TITLES

    state = await _planned_state(tracker)

    assert [sub_topic.title for sub_topic in state.sub_topics] == SUB_TOPIC_TITLES
    # The default cap attempts the whole plan: a five-sub-topic plan must not
    # be silently truncated to fit a smaller production default.
    assert DEFAULT_MAX_SUB_TOPICS >= len(state.sub_topics)
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

    researched_titles = [sub_topic.title for sub_topic in state.sub_topics]
    researcher = _researcher_for(
        tracker,
        decisions=[
            decision
            for title in researched_titles
            for decision in _search_and_scrape_decisions(title)
        ],
        outputs=[_findings_draft(title) for title in researched_titles],
    )

    async with tracker.session_span("session-1", state.original_question):
        researcher_outcome = await researcher.run(state)
    state = merge_research_state(state, researcher_outcome.state_update)

    # All five sub-topics were attempted and produced a finding.
    assert [
        finding.related_sub_topic for finding in state.raw_findings
    ] == researched_titles

    # Every planned coverage id is accounted for by evidence, and nothing was
    # skipped: this is the regression pin for Finding 1, now that the default
    # cap covers the whole plan instead of dropping the two least important
    # mechanisms.
    assert state.errors == []
    findings_by_title = {
        finding.related_sub_topic for finding in state.raw_findings
    }
    for sub_topic in state.sub_topics:
        assert sub_topic.title in findings_by_title, sub_topic.coverage_id

    # The event stream agrees: 5 planned, 5 researched, 0 skipped.
    completed_event = next(
        event
        for event in researcher_outcome.state_update["events"]
        if event.event_type == "researcher.research.completed"
    )
    assert completed_event.metadata["sub_topics_planned"] == 5
    assert completed_event.metadata["sub_topics_researched"] == 5
    assert completed_event.metadata["sub_topics_skipped"] == 0


@pytest.mark.asyncio
async def test_a_provider_failure_names_every_topic_never_attempted(
    tracker: Tracker,
) -> None:
    """The other half of the contract: attempted, or explicitly recorded.

    The second sub-topic's loop dies on a non-recoverable provider failure.
    The three after it never get a turn, and each one — whatever its
    priority, and whatever the cap would have done with it — is named by its
    ``coverage_id`` with the reason it was not researched, so a short pass is
    visible in ``state.errors`` rather than inferred from a count.
    """
    state = await _planned_state(tracker)
    assert len(state.sub_topics) == 5

    first, second = state.sub_topics[0], state.sub_topics[1]
    researcher = _researcher_for(
        tracker,
        decisions=[
            *_search_and_scrape_decisions(first.title),
            ProviderTimeoutError("timed out"),
        ],
        outputs=[_findings_draft(first.title)],
    )

    async with tracker.session_span("session-1", state.original_question):
        researcher_outcome = await researcher.run(state)

    attempted = [
        event.metadata["sub_topic"]
        for event in researcher_outcome.state_update["events"]
        if event.event_type == "researcher.sub_topic.started"
    ]
    assert attempted == [first.title, second.title]

    skipped = {
        error.details["coverage_id"]: error.details
        for error in researcher_outcome.errors
        if error.error_type == "researcher_sub_topic_skipped"
    }
    assert set(skipped) == {
        sub_topic.coverage_id for sub_topic in state.sub_topics[2:]
    }
    for sub_topic in state.sub_topics[2:]:
        details = skipped[sub_topic.coverage_id]
        assert details["sub_topic"] == summarize_text(sub_topic.title)
        assert details["reason"] == "provider_failure_stopped_processing"

    # The failed sub-topic is an outage, not an unattempted topic: it gets the
    # non-recoverable provider error, never a skip record.
    assert second.coverage_id not in skipped
