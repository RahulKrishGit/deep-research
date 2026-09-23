"""End-to-end seam: checked evidence becomes a report, then a critique.

Every other Synthesizer and Critic test builds state by hand, so nothing
exercises the real seam: that ``FactCheckerAgent`` writes claims whose labels
and URLs the Synthesizer can cite, that the report it composes is what the
Critic reviews, and that the Critic's routing recommendation lands in the
same state a graph would read. This test runs all three agents in sequence,
merging state the way the orchestrator will.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from deep_research.agents.critic import (
    QUESTION_TARGET_ID,
    CriticAgent,
    CritiqueDraft,
    CritiqueGapDraft,
)
from deep_research.agents.evidence import build_evidence_unit, build_read_record
from deep_research.agents.fact_checker import (
    ClaimDraft,
    ClaimsDraft,
    ClaimVerdictDraft,
    EvidencePassageDraft,
    FactCheckerAgent,
    PassageVerdictDraft,
    SupportAssessment,
)
from deep_research.agents.quality import compute_report_quality
from deep_research.agents.report import (
    REPORT_SECTIONS,
    composition_statements,
    validate_report_statements,
)
from deep_research.agents.source_evaluator import (
    SourceScoreDraft,
    SourceScoresDraft,
)
from deep_research.agents.sources import normalize_source_url
from deep_research.agents.synthesizer import (
    ConstraintDraft,
    ReportDraft,
    ReportPointDraft,
    ReportSectionDraft,
    SynthesizerAgent,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers.contracts import ChatMessage
from deep_research.runtime.outcome import evidence_path_from_state
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    Finding,
    MemorySnapshot,
    ReadRecord,
    ResearchState,
    ScoredSource,
    SubTopic,
    merge_research_state,
)
from tests.agent_fakes import ScriptedCompleter, finish, use_tool
from tests.research_fakes import (
    FakeMemory,
    FakeSearchClient,
    critic_tools,
    fact_checker_tools,
    page_client,
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
                PassageVerdictDraft(
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
    # Task 7: every reader statement is mapped, and the independent passage
    # that carried the attribution is a reference in its own right.
    assert state.composition is not None
    assert validate_report_statements(state.composition) == []
    statements = composition_statements(state.composition)
    # The claim is primary-source attribution, not corroboration, so no
    # statement may read as settled; the "not stated" table cell is this
    # pass's own context statement rather than a finding.
    assert "attributed" in {statement.mode for statement in statements}
    assert "settled" not in {statement.mode for statement in statements}
    assert f"2. Independent review — {SEAM_INDEPENDENT_URL}" in state.report
    assert "## Statement support map" in (state.report_evidence or "")
    assert "- Break-even was reached. [1][2]" in state.report
    # Both artifacts are composed into state; synthesis publishes neither and
    # keeps nothing in long-term memory.
    assert state.report_evidence is not None
    # A composed ledger name is not a write. ``state.evidence_path`` stays
    # ``None`` until the terminal finalizer records the write that succeeded,
    # so a run halted after this node cannot advertise an ``Evidence ledger:``
    # line for a file that was never created.
    assert state.evidence_path is None
    assert evidence_path_from_state(state) is None
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
                    gaps=[
                        # The typed provider shape: a live reply that sent a
                        # bare string is repaired, not rewritten (fix round 4).
                        CritiqueGapDraft(
                            target_ids=[QUESTION_TARGET_ID],
                            kind="coverage",
                            severity="major",
                            repair_action="acquire",
                            problem="No source was scored.",
                            recommended_queries=[],
                        )
                    ],
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


# ---------------------------------------------------------------------------
# Task 6 seam: the document a verification reads is assessed, saved and cited
# ---------------------------------------------------------------------------

VERIFIED_CLAIM = "Wind capacity reached 10 GW in 2025."
UPSTREAM_URL = "https://lab-a.test/wind"
UPSTREAM_TITLE = "Lab A report"
UPSTREAM_TEXT = "Lab A reported that wind capacity reached 10 GW in 2025."
RETRIEVED_URL = "https://third.test/audit"
# The document attributes itself, so the read evidences the issuer the score
# names and the pair test can use its identity rather than guessing it.
RETRIEVED_TEXT = (
    "Published by Third Party. Third Party audited the figure: wind "
    "capacity reached 10 GW in 2025."
)


def _retrieval_topic() -> SubTopic:
    return SubTopic(
        coverage_id="topic-01",
        title="Added capacity",
        rationale="It answers part of the question.",
        search_queries=["wind capacity 2025"],
        success_criteria=["A capacity value is stated."],
        priority=1,
    )


def _upstream_read() -> ReadRecord:
    return build_read_record(
        session_id="session-1",
        reader="web_scraper",
        requested_url=UPSTREAM_URL,
        resolved_url=UPSTREAM_URL,
        title=UPSTREAM_TITLE,
        retrieved_at=SEAM_EXTRACTED_AT,
        text=UPSTREAM_TEXT,
        passages={"chunk-0": UPSTREAM_TEXT},
        extraction_complete=True,
    )


def _retrieval_state() -> ResearchState:
    """One upstream read and no local pair: verification has to retrieve."""
    read = _upstream_read()
    unit = build_evidence_unit(
        read=read,
        locator="chunk-0",
        excerpt=UPSTREAM_TEXT,
        origin="researcher",
    )
    return ResearchState(
        session_id="session-1",
        original_question="How much wind capacity was added in 2025?",
        sub_topics=[_retrieval_topic()],
        raw_findings=[
            Finding(
                content=UPSTREAM_TEXT,
                source_url=UPSTREAM_URL,
                source_title=UPSTREAM_TITLE,
                extracted_at=SEAM_EXTRACTED_AT,
                confidence=0.8,
                related_sub_topic="Added capacity",
            )
        ],
        evaluated_sources=[
            ScoredSource(
                url=UPSTREAM_URL,
                title=UPSTREAM_TITLE,
                authority_score=0.9,
                recency_score=0.9,
                relevance_score=0.9,
                overall_score=0.9,
                rationale="Independent and dated.",
                serving_host="lab-a.test",
                publisher_id="lab-a.test",
                work_id=f"sha256:{read.content_sha256}",
                transport_relation="original",
                source_role="independent_research",
                self_interest="none",
                evaluation_status="scored",
            )
        ],
        read_records={read.read_id: read},
        evidence_units={unit.evidence_id: unit},
        memory_context=MemorySnapshot(),
    )


def _shown_evidence_ids(messages: list[ChatMessage]) -> list[str]:
    body = "\n".join(message.content for message in messages)
    return list(dict.fromkeys(re.findall(r"id: (ev-[0-9a-f]+)", body)))


def _select_every_shown_passage(
    messages: list[ChatMessage], schema: type[ClaimVerdictDraft]
) -> ClaimVerdictDraft:
    """The adjudication a model makes over the packet it was shown."""
    ids = _shown_evidence_ids(messages)
    assert ids, "\n".join(message.content for message in messages)
    return schema(
        verdict="verified",
        confidence=0.9,
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
        rationale="Two independent reports state the same figure.",
    )


def _reader_reference_urls(report: str) -> list[str]:
    """The URLs the reader's own reference list prints, in order."""
    section = report.rsplit("## References", 1)[-1]
    return re.findall(r"^\d+\.\s+.*?—\s+(\S+)$", section, flags=re.MULTILINE)


@pytest.mark.asyncio
async def test_the_source_verification_read_is_assessed_saved_and_counted(
    tracker: Tracker, tmp_path: Path
) -> None:
    """Task 6: a document read during verification is saved with its score.

    No local pair exists, so the claim is settled only by the document the
    Fact Checker retrieves. That document ends up carrying a report statement,
    so its assessment has to travel in the same update as its read: a read
    without a saved assessment leaves the reader citing a source the record
    never judged, hard-fails ``unscored_cited_sources``, and makes the
    Methodology count contradict the References list printed under it.
    """
    state = _retrieval_state()
    checker = FactCheckerAgent(
        provider=ScriptedCompleter(
            decisions=[
                use_tool(
                    "Look for an independent audit.",
                    "web_search",
                    '{"query": "wind capacity 2025 audit"}',
                ),
                use_tool(
                    "Read the audit before judging the claim.",
                    "web_scraper",
                    f'{{"url": "{RETRIEVED_URL}"}}',
                ),
                finish("Enough retrieved.", "An independent audit agrees."),
            ],
            outputs=[
                ClaimsDraft(
                    claims=[
                        ClaimDraft(
                            text=VERIFIED_CLAIM,
                            source_urls=[UPSTREAM_URL],
                        )
                    ]
                ),
                SourceScoresDraft(
                    sources=[
                        SourceScoreDraft(
                            url=RETRIEVED_URL,
                            authority_score=0.9,
                            recency_score=0.9,
                            relevance_score=0.9,
                            source_role="independent_research",
                            issuer="Third Party",
                            rationale="Independent and dated.",
                        )
                    ]
                ),
                _select_every_shown_passage,
            ],
        ),
        tracker=tracker,
        scratchpad=_pad("fact_checker"),
        tools=fact_checker_tools(
            tracker,
            search=FakeSearchClient([search_response(url=RETRIEVED_URL)]),
            http=page_client(title="Third party audit", body=RETRIEVED_TEXT),
        ),
        config=AgentRuntimeConfig(max_iterations=3, tool_budget=2),
    )
    synthesizer = SynthesizerAgent(
        provider=ScriptedCompleter(
            outputs=[
                ReportDraft(
                    executive_summary=[
                        ReportPointDraft(
                            text="10 GW of wind capacity was added in 2025.",
                            claim_ids=["C001"],
                            source_urls=[UPSTREAM_URL],
                        )
                    ],
                    ranked_constraints=[
                        ConstraintDraft(
                            constraint="Track the audited capacity figure.",
                            deployment_mechanism="independent audit",
                            geography="not stated",
                            claim_ids=["C001"],
                            source_urls=[UPSTREAM_URL],
                        )
                    ],
                    sections=[
                        ReportSectionDraft(
                            title="Added capacity",
                            points=[
                                ReportPointDraft(
                                    text="The audited figure is 10 GW.",
                                    claim_ids=["C001"],
                                    source_urls=[UPSTREAM_URL],
                                )
                            ],
                        )
                    ],
                    uncertainty_notes=["The audit covers one year only."],
                )
            ]
        ),
        tracker=tracker,
        scratchpad=_pad("synthesizer"),
        tools=synthesizer_tools(
            tracker, output_root=tmp_path, memory=FakeMemory()
        ),
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=0),
    )

    async with tracker.session_span("session-1", state.original_question):
        state = merge_research_state(
            state, (await checker.run(state)).state_update
        )
        (claim,) = state.verified_claims
        assessed = {
            normalize_source_url(source.url): source
            for source in state.evaluated_sources
        }
        state = merge_research_state(
            state, (await synthesizer.run(state)).state_update
        )

    # The pair that settled the claim rests on the retrieved document -- and
    # both it and the earlier source survived the merge.
    assert claim.verdict == "verified"
    assert claim.evidence_status == "verified_pair"
    assert normalize_source_url(UPSTREAM_URL) in assessed
    assert normalize_source_url(RETRIEVED_URL) in assessed
    assert (
        assessed[normalize_source_url(RETRIEVED_URL)].evaluation_status
        == "scored"
    )
    # What downstream reads is the identity the pair test itself used: the
    # snapshot is resolved once over the whole read registry, so the saved row
    # carries the publisher and work its read establishes — the issuer this
    # document attributes itself to — rather than an unknown identity, and the
    # earlier source keeps the host-level identity its own read establishes.
    assert (
        assessed[normalize_source_url(RETRIEVED_URL)].publisher_id
        == "third party"
    )
    assert assessed[normalize_source_url(RETRIEVED_URL)].work_id
    assert assessed[normalize_source_url(UPSTREAM_URL)].publisher_id == "lab-a.test"

    report = state.report or ""
    composition = state.composition
    assert composition is not None
    # The reader cites the retrieved document, and the count of sources it
    # says this pass reviewed is the list printed under it.
    references = _reader_reference_urls(report)
    assert normalize_source_url(RETRIEVED_URL) in references
    match = re.search(
        r"(\d+) reviewed source\(s\): (\d+) scored, (\d+) unscored", report
    )
    assert match is not None, report
    reviewed, scored, unscored = (int(group) for group in match.groups())
    assert unscored == 0
    assert reviewed == scored == len(references)
    assert "unscored_cited_sources" not in compute_report_quality(
        state, composition
    ).hard_failures
