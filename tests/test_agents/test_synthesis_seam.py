"""End-to-end seam: checked evidence becomes a report, then a critique.

Every other Synthesizer and Critic test builds state by hand, so nothing
exercises the real seam: that ``FactCheckerAgent`` writes claims whose labels
and URLs the Synthesizer can cite, that the report it composes is what the
Critic reviews, and that the Critic's routing recommendation lands in the
same state a graph would read. This test runs all three agents in sequence,
merging state the way the orchestrator will.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deep_research.agents.critic import CriticAgent, CritiqueDraft
from deep_research.agents.fact_checker import (
    ClaimDraft,
    ClaimsDraft,
    ClaimVerdictDraft,
    EvidencePassageDraft,
    FactCheckerAgent,
)
from deep_research.agents.report import REPORT_SECTIONS
from deep_research.agents.synthesizer import (
    ConstraintDraft,
    ReportDraft,
    ReportPointDraft,
    ReportSectionDraft,
    SynthesizerAgent,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    Finding,
    ResearchState,
    ScoredSource,
    merge_research_state,
)
from tests.agent_fakes import ScriptedCompleter, finish, use_tool
from tests.research_fakes import (
    FakeMemory,
    FakeSearchClient,
    critic_tools,
    fact_checker_tools,
    search_response,
    synthesizer_tools,
)

SEAM_SOURCE_URL = "https://example.test/qec"
SEAM_INDEPENDENT_URL = "https://third.test/review"
SEAM_EXTRACTED_AT = "2026-08-01T12:00:00+00:00"


def _pad(agent_name: str) -> ScratchpadMemory:
    return ScratchpadMemory(
        session_id="session-1", agent_name=agent_name, max_entries=20
    )


def _seam_state() -> ResearchState:
    return ResearchState(
        session_id="session-1",
        original_question="How mature is quantum error correction?",
        raw_findings=[
            Finding(
                content="Logical error rates fell below break-even in 2025.",
                source_url=SEAM_SOURCE_URL,
                source_title="QEC 2025",
                extracted_at=SEAM_EXTRACTED_AT,
                confidence=0.9,
                related_sub_topic="Alpha",
            )
        ],
        evaluated_sources=[
            ScoredSource(
                url=SEAM_SOURCE_URL,
                title="QEC 2025",
                authority_score=0.8,
                recency_score=0.7,
                relevance_score=0.9,
                overall_score=0.76,
                rationale="Peer-reviewed and corroborated.",
            )
        ],
        max_iterations=3,
    )


@pytest.mark.asyncio
async def test_verified_claims_become_a_cited_report_the_critic_accepts(
    tracker: Tracker, tmp_path: Path
) -> None:
    state = _seam_state()
    memory = FakeMemory()

    checker = FactCheckerAgent(
        provider=ScriptedCompleter(
            decisions=[
                use_tool(
                    "Look for an independent review.",
                    "web_search",
                    '{"query": "qec break-even 2025"}',
                ),
                use_tool(
                    "Read the independent review before judging the claim.",
                    "web_scraper",
                    f'{{"url": "{SEAM_INDEPENDENT_URL}"}}',
                ),
                finish("Enough retrieved.", "An independent review agrees."),
            ],
            outputs=[
                ClaimsDraft(
                    claims=[
                        ClaimDraft(
                            text=(
                                "Logical error rates fell below break-even "
                                "in 2025."
                            ),
                            source_urls=[SEAM_SOURCE_URL],
                        )
                    ]
                ),
                ClaimVerdictDraft(
                    verdict="verified",
                    confidence=0.9,
                    passages=[
                        EvidencePassageDraft(
                            source_url=SEAM_INDEPENDENT_URL,
                            source_title="Independent review",
                            locator="p. 1",
                            excerpt=(
                                "An independent review states the same figure."
                            ),
                            stance="supports",
                        )
                    ],
                ),
            ],
        ),
        tracker=tracker,
        scratchpad=_pad("fact_checker"),
        tools=fact_checker_tools(
            tracker,
            search=FakeSearchClient(
                [search_response(url=SEAM_INDEPENDENT_URL)]
            ),
        ),
        config=AgentRuntimeConfig(max_iterations=3, tool_budget=2),
    )
    synthesizer = SynthesizerAgent(
        provider=ScriptedCompleter(
            outputs=[
                ReportDraft(
                    executive_summary=[
                        ReportPointDraft(
                            text="Break-even was reached in 2025.",
                            claim_ids=["C001"],
                            source_urls=[SEAM_SOURCE_URL],
                        )
                    ],
                    ranked_constraints=[
                        ConstraintDraft(
                            constraint="Hold the logical error rate below "
                            "break-even.",
                            deployment_mechanism="error-corrected logical "
                            "qubits",
                            geography="not stated",
                            claim_ids=["C001"],
                            source_urls=[SEAM_SOURCE_URL],
                        )
                    ],
                    sections=[
                        ReportSectionDraft(
                            title="Error correction",
                            points=[
                                ReportPointDraft(
                                    text="Break-even was reached.",
                                    claim_ids=["C001"],
                                    source_urls=[SEAM_SOURCE_URL],
                                )
                            ],
                        )
                    ],
                    uncertainty_notes=["Vendor numbers remain unaudited."],
                )
            ]
        ),
        tracker=tracker,
        scratchpad=_pad("synthesizer"),
        tools=synthesizer_tools(tracker, output_root=tmp_path, memory=memory),
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=0),
    )
    critic = CriticAgent(
        provider=ScriptedCompleter(
            outputs=[
                CritiqueDraft(
                    score=8,
                    gaps=[],
                    unsupported_claims=[],
                    recommended_queries=[],
                    rationale="Well sourced for the question asked.",
                )
            ]
        ),
        tracker=tracker,
        scratchpad=_pad("critic"),
        tools=critic_tools(tracker),
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=0),
    )

    async with tracker.session_span("session-1", state.original_question):
        state = merge_research_state(
            state, (await checker.run(state)).state_update
        )
        state = merge_research_state(
            state, (await synthesizer.run(state)).state_update
        )
        state = merge_research_state(
            state, (await critic.run(state)).state_update
        )

    assert state.report is not None
    for heading in REPORT_SECTIONS:
        assert heading in state.report
    # The claim the Fact Checker verified is cited against the source the
    # Researcher actually retrieved, inline in the point that rests on it.
    assert "- Break-even was reached. [1]" in state.report
    assert f"1. QEC 2025 — {SEAM_SOURCE_URL}" in state.report
    # Both artifacts are composed into state; synthesis publishes neither and
    # keeps nothing in long-term memory.
    assert state.report_evidence is not None
    assert state.evidence_path == "report-session-1-0-evidence.md"
    assert state.unique_source_count == 1
    assert state.unique_claim_count == 1
    assert list(tmp_path.iterdir()) == []
    assert memory.saved == []

    assert state.critique is not None
    assert state.critique.should_continue is False
    assert state.critique.score == 8
    assert [event.event_type for event in state.events][-2:] == [
        "critic.critique.started",
        "critic.critique.completed",
    ]


@pytest.mark.asyncio
async def test_a_weak_pass_reports_its_limits_and_asks_for_another_cycle(
    tracker: Tracker, tmp_path: Path
) -> None:
    state = _seam_state().model_copy(
        update={"evaluated_sources": []}, deep=True
    )
    synthesizer = SynthesizerAgent(
        provider=ScriptedCompleter(
            outputs=[
                ReportDraft(
                    executive_summary=[],
                    ranked_constraints=[],
                    sections=[],
                    uncertainty_notes=[],
                )
            ]
        ),
        tracker=tracker,
        scratchpad=_pad("synthesizer"),
        tools=synthesizer_tools(tracker, output_root=tmp_path),
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=0),
    )
    critic = CriticAgent(
        provider=ScriptedCompleter(
            outputs=[
                CritiqueDraft(
                    score=3,
                    gaps=["No source was scored."],
                    unsupported_claims=[],
                    recommended_queries=["qec break-even independent review"],
                    rationale="One unscored source carries everything.",
                )
            ]
        ),
        tracker=tracker,
        scratchpad=_pad("critic"),
        tools=critic_tools(tracker),
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=0),
    )

    async with tracker.session_span("session-1", state.original_question):
        state = merge_research_state(
            state, (await synthesizer.run(state)).state_update
        )
        state = merge_research_state(
            state, (await critic.run(state)).state_update
        )

    assert state.report is not None
    assert "No source behind these findings was scored" in state.report
    assert "No claim was verified" in state.report
    assert state.critique is not None
    assert state.critique.should_continue is True
    assert state.critique.recommended_queries == [
        "qec break-even independent review"
    ]
