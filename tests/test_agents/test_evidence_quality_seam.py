"""End-to-end seam: Researcher output feeds the evidence-quality agents.

Every other Source Evaluator and Fact Checker test builds
``ResearchState`` by hand, so nothing exercises the real seam: that
``ResearcherAgent`` writes ``raw_findings`` whose URLs the Source
Evaluator can group, and that the Fact Checker only ever cites URLs that
actually reached state. This test runs all three agents in sequence,
merging state the way the orchestrator will.
"""

from __future__ import annotations

import re

import pytest

from deep_research.agents.fact_checker import (
    ClaimDraft,
    ClaimsDraft,
    ClaimVerdictDraft,
    FactCheckerAgent,
    SupportAssessment,
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
from deep_research.providers import ChatMessage
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
    qec_read_record,
    research_tools,
    search_response,
)

SOURCE_URL = "https://example.test/qec"
INDEPENDENT_URL = "https://third.test/review"
SOURCE_READ = qec_read_record()


def _state() -> ResearchState:
    return ResearchState(
        session_id="session-1",
        original_question="How mature is quantum error correction?",
        sub_topics=[
            SubTopic(
                coverage_id="topic-01",
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


def _select_every_shown_passage(
    messages: list[ChatMessage], schema: type[ClaimVerdictDraft]
) -> ClaimVerdictDraft:
    """The adjudication a real model makes over the packet it was shown.

    The ids a packet carries are minted from the reads, so a caller cannot
    script them as fixed objects: this reads them back out of the request the
    same way the model does, which also proves the request actually carries
    exact passages under selectable ids.
    """
    body = "\n".join(message.content for message in messages)
    ids = list(dict.fromkeys(re.findall(r"id: (ev-[0-9a-f]+)", body)))
    assert ids, body
    return schema(
        verdict="verified",
        confidence=0.85,
        assessments=[
            SupportAssessment(
                evidence_id=evidence_id,
                stance="supports",
                complete_support=True,
                scope_compatible=True,
                dependence="primary",
            )
            for evidence_id in ids
        ],
        support_ids=ids,
        contradiction_ids=[],
        rationale="Both passages state the same result.",
    )


@pytest.mark.asyncio
async def test_findings_flow_through_scoring_into_an_adjudicated_claim(
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
                            source_title=SOURCE_READ.title,
                            confidence=0.8,
                            read_id=SOURCE_READ.read_id,
                            locator="chunk-0",
                            excerpt=SOURCE_READ.passages["chunk-0"],
                            target_ids=["topic-01"],
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

    # Every source behind a finding has a scored assessment or explicit status,
    # keyed by its canonical URL.
    assert [source.url for source in state.evaluated_sources] == [SOURCE_URL]
    assert state.evaluated_sources[0].low_confidence is False
    assert 0.0 <= state.evaluated_sources[0].overall_score <= 1.0

    # The assessment is read-backed: the source carries the identity and the
    # revision the read registry evidences, not just the URL a model reported.
    assessed = state.evaluated_sources[0]
    stored = next(
        read
        for read in state.read_records.values()
        if read.resolved_url == SOURCE_URL
    )
    assert assessed.serving_host == "example.test"
    assert assessed.work_id == f"sha256:{stored.content_sha256}"
    assert assessed.assessment_revision.startswith("assess-")
    assert assessed.cited_sub_topics == ["Alpha"]

    checker = FactCheckerAgent(
        provider=ScriptedCompleter(
            decisions=[
                use_tool(
                    "Find an independent source.",
                    "web_search",
                    '{"query": "break-even 2025"}',
                ),
                use_tool(
                    "Read the independent source before judging the claim.",
                    "web_scraper",
                    f'{{"url": "{INDEPENDENT_URL}"}}',
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
                # The source the verifier itself read is scored through Task
                # 4's shared service before it may carry a statement, so the
                # assessment request comes before the verdict request.
                SourceScoresDraft(
                    sources=[
                        SourceScoreDraft(
                            url=INDEPENDENT_URL,
                            authority_score=0.8,
                            recency_score=0.7,
                            relevance_score=0.9,
                            rationale="Independent and dated.",
                        )
                    ]
                ),
                _select_every_shown_passage,
            ],
        ),        tracker=tracker,
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

    # The claim cites only a URL that actually reached state, and the verdict
    # is decided from the exact passages of the packet rather than from the
    # domains they were served from.
    #
    # The honest verdict here is NOT ``verified``, and that is the point of the
    # Task 6 seam. Two things make the pair fail, and they are worth stating
    # exactly:
    #
    # * both reads were served the same body, so their ``content_sha256`` is
    #   byte-identical and Section 2.2's equal-hash rule makes them ONE work —
    #   a second domain is a transport fact, never a second work;
    # * neither assessment evidences an issuer, so the source role stays
    #   ``unknown`` and ``source_origin_id`` resolves no claim-specific origin
    #   at all (the publisher id itself *does* resolve, from the serving host).
    #
    # The named reason is therefore the provable one — same work — rather than
    # the generic "identity unknown". The old verdict called this verified on
    # domain difference alone; it never rested on a pair test.
    assert len(state.verified_claims) == 1
    claim = state.verified_claims[0]
    assert claim.source_urls == [SOURCE_URL]
    assert claim.verdict == "insufficient_evidence"
    assert claim.evidence_status == "source_supported"
    assert claim.insufficient_reason == "same_work"
    assert claim.verification_evidence
    assert {passage.source_url for passage in claim.verification_evidence} == {
        SOURCE_URL,
        INDEPENDENT_URL,
    }

    completed = next(
        event
        for event in outcome.state_update["events"]
        if event.event_type == "fact_checker.fact_check.completed"
    )
    assert completed.metadata["claim_count"] == 1
    assert completed.metadata["verified"] == 0
    assert completed.metadata["insufficient_evidence"] == 1
    assert completed.metadata["contradiction_count"] == 0
