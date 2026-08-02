"""End-to-end seam: Researcher output feeds the evidence-quality agents.

Every other Source Evaluator and Fact Checker test builds
``ResearchState`` by hand, so nothing exercises the real seam: that
``ResearcherAgent`` writes ``raw_findings`` whose URLs the Source
Evaluator can group, and that the Fact Checker only ever cites URLs that
actually reached state. This test runs all three agents in sequence,
merging state the way the orchestrator will.
"""

from __future__ import annotations

import pytest

from deep_research.agents.fact_checker import (
    ClaimDraft,
    ClaimsDraft,
    ClaimVerdictDraft,
    FactCheckerAgent,
)
from deep_research.agents.researcher import (
    FindingDraft,
    ResearcherAgent,
    SubTopicFindingsDraft,
)
from deep_research.agents.source_evaluator import (
    SourceEvaluatorAgent,
    SourceScoreDraft,
    SourceScoresDraft,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    MemorySnapshot,
    ResearchState,
    SubTopic,
    merge_research_state,
)
from tests.agent_fakes import ScriptedCompleter, finish, use_tool
from tests.research_fakes import (
    FakeReputationSource,
    FakeSearchClient,
    fact_checker_tools,
    research_tools,
    search_response,
)

SOURCE_URL = "https://example.test/qec"
INDEPENDENT_URL = "https://third.test/review"


def _state() -> ResearchState:
    return ResearchState(
        session_id="session-1",
        original_question="How mature is quantum error correction?",
        sub_topics=[
            SubTopic(
                title="Alpha",
                rationale="Alpha is load-bearing.",
                search_queries=["alpha 2025"],
                success_criteria=["A named source about Alpha."],
                priority=1,
            )
        ],
        memory_context=MemorySnapshot(),
    )


def _pad(agent_name: str) -> ScratchpadMemory:
    return ScratchpadMemory(
        session_id="session-1", agent_name=agent_name, max_entries=20
    )


@pytest.mark.asyncio
async def test_findings_flow_through_scoring_into_verified_claims(
    tracker: Tracker,
) -> None:
    state = _state()

    researcher = ResearcherAgent(
        provider=ScriptedCompleter(
            decisions=[
                use_tool("Find sources.", "web_search", '{"query": "alpha"}'),
                use_tool(
                    "Read the source.",
                    "web_scraper",
                    f'{{"url": "{SOURCE_URL}"}}',
                ),
                finish("I have a source-backed answer.", "Evidence found."),
            ],
            outputs=[
                SubTopicFindingsDraft(
                    findings=[
                        FindingDraft(
                            content="Break-even was crossed in 2025.",
                            source_url=SOURCE_URL,
                            source_title="Quantum error correction in 2025",
                            confidence=0.8,
                        )
                    ]
                )
            ],
        ),
        tracker=tracker,
        scratchpad=_pad("researcher"),
        tools=research_tools(
            tracker, search=FakeSearchClient([search_response()])
        ),
        config=AgentRuntimeConfig(max_iterations=4, tool_budget=4),
    )
    async with tracker.session_span("session-1", state.original_question):
        outcome = await researcher.run(state)
    state = merge_research_state(state, outcome.state_update)

    assert [finding.source_url for finding in state.raw_findings] == [SOURCE_URL]

    evaluator = SourceEvaluatorAgent(
        provider=ScriptedCompleter(
            outputs=[
                SourceScoresDraft(
                    sources=[
                        SourceScoreDraft(
                            url=SOURCE_URL,
                            authority_score=0.9,
                            recency_score=0.8,
                            relevance_score=0.9,
                            rationale="Peer-reviewed and dated.",
                        )
                    ]
                )
            ]
        ),
        tracker=tracker,
        scratchpad=_pad("source_evaluator"),
        reputation=FakeReputationSource(reputations={SOURCE_URL: 0.8}),
    )
    async with tracker.session_span("session-1", state.original_question):
        outcome = await evaluator.run(state)
    state = merge_research_state(state, outcome.state_update)

    # Every source behind a finding is scored, keyed by its canonical URL.
    assert [source.url for source in state.evaluated_sources] == [SOURCE_URL]
    assert state.evaluated_sources[0].low_confidence is False
    assert 0.0 <= state.evaluated_sources[0].overall_score <= 1.0

    checker = FactCheckerAgent(
        provider=ScriptedCompleter(
            decisions=[
                use_tool(
                    "Find an independent source.",
                    "web_search",
                    '{"query": "break-even 2025"}',
                ),
                finish("I have independent material.", "Checked."),
            ],
            outputs=[
                ClaimsDraft(
                    claims=[
                        ClaimDraft(
                            text="Break-even was crossed in 2025.",
                            source_urls=[SOURCE_URL],
                        )
                    ]
                ),
                ClaimVerdictDraft(
                    verdict="verified",
                    confidence=0.85,
                    evidence=["An unrelated review reports the same result."],
                    contradictions=[],
                ),
            ],
        ),
        tracker=tracker,
        scratchpad=_pad("fact_checker"),
        tools=fact_checker_tools(
            tracker,
            search=FakeSearchClient([search_response(url=INDEPENDENT_URL)]),
        ),
        config=AgentRuntimeConfig(max_iterations=3, tool_budget=3),
    )
    async with tracker.session_span("session-1", state.original_question):
        outcome = await checker.run(state)
    state = merge_research_state(state, outcome.state_update)

    # The claim cites only a URL that actually reached state, and its
    # verdict came from an independent domain.
    assert len(state.verified_claims) == 1
    claim = state.verified_claims[0]
    assert claim.source_urls == [SOURCE_URL]
    assert claim.verdict == "verified"

    completed = next(
        event
        for event in outcome.state_update["events"]
        if event.event_type == "fact_checker.fact_check.completed"
    )
    assert completed.metadata["claim_count"] == 1
    assert completed.metadata["verified"] == 1
    assert completed.metadata["contradiction_count"] == 0
