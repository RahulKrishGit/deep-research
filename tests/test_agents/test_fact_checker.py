"""Tests for the Fact Checker's claim extraction and verification."""

from __future__ import annotations

from typing import get_args

import pytest

from deep_research.agents.base import AgentRun
from deep_research.agents.fact_checker import (
    DEFAULT_FINDING_DIGEST,
    MAX_PASSAGE_EXCERPT_CHARS,
    MAX_PASSAGE_LOCATOR_CHARS,
    VERDICT_VALUES,
    ClaimDraft,
    ClaimsDraft,
    ClaimTask,
    ClaimVerdictDraft,
    EvidencePassageDraft,
    FactCheckerAgent,
    VerifiedClaims,
    _critique_texts,
    _finding_is_new,
    build_claim,
    build_claim_drafts,
    claim_checked_event,
    claim_extraction_messages,
    claim_verification_messages,
    claimed_domains_for,
    consumed_provenance,
    fact_check_completed_event,
    independent_domains,
    insufficient_claim,
    known_source_urls,
    normalize_verdict,
    ordered_findings_for_extraction,
    resolve_verdict,
    retrieved_source_urls,
    union_claim_provenance,
    valid_verification_passages,
    verdict_counts,
)
from deep_research.agents.identity import (
    claim_fingerprint,
    finding_fingerprint,
)
from deep_research.agents.prompts import AgentTask
from deep_research.agents.steps import (
    ReActDecision,
    ReActObservation,
    ReActRun,
    ReActStep,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import TokenUsage, Tracker
from deep_research.providers import (
    ProviderOutputLimitError,
    ProviderResponseTelemetry,
    ProviderTimeoutError,
)
from deep_research.tools.base import ToolResult
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    Claim,
    ClaimVerdict,
    Critique,
    CritiqueGap,
    EvidencePassage,
    Finding,
    MemorySnapshot,
    ResearchState,
    ScoredSource,
    SubTopic,
    merge_research_state,
)
from tests.agent_fakes import ScriptedCompleter, finish, use_tool
from tests.research_fakes import FakeSearchClient, fact_checker_tools, search_response

CHECK_EXTRACTED_AT = "2026-08-01T12:00:00+00:00"


def _output_limit_error() -> ProviderOutputLimitError:
    return ProviderOutputLimitError(
        ProviderResponseTelemetry(
            finish_reason_category="length",
            configured_max_tokens=4096,
            usage=TokenUsage(input_tokens=5, output_tokens=4096),
            request_attempt=1,
        )
    )


def _check_finding(
    url: str = "https://example.org/a",
    *,
    content: str = "Logical error rates fell below break-even in 2025.",
    sub_topic: str = "Alpha",
) -> Finding:
    return Finding(
        content=content,
        source_url=url,
        source_title="QEC 2025",
        extracted_at=CHECK_EXTRACTED_AT,
        confidence=0.8,
        related_sub_topic=sub_topic,
    )


def _scored(url: str, *, low: bool = False) -> ScoredSource:
    return ScoredSource(
        url=url,
        title="QEC 2025",
        authority_score=0.2 if low else 0.9,
        recency_score=0.2 if low else 0.9,
        relevance_score=0.2 if low else 0.9,
        overall_score=0.16 if low else 0.9,
        rationale="Because.",
        low_confidence=low,
    )


def _check_state(
    findings: list[Finding] | None = None,
    sources: list[ScoredSource] | None = None,
) -> ResearchState:
    return ResearchState(
        session_id="session-1",
        original_question="How mature is quantum error correction?",
        raw_findings=findings or [],
        evaluated_sources=sources or [],
        memory_context=MemorySnapshot(),
    )


def test_known_source_urls_are_canonical_and_deduplicated() -> None:
    state = _check_state(
        [
            _check_finding("https://example.org/a"),
            _check_finding("https://WWW.example.org/a/"),
            _check_finding("https://other.test/b"),
        ]
    )

    assert known_source_urls(state) == [
        "https://example.org/a",
        "https://other.test/b",
    ]


def test_claim_drafts_keep_only_urls_the_findings_actually_used() -> None:
    draft = ClaimsDraft(
        claims=[
            ClaimDraft(
                text="Logical error rates fell below break-even in 2025.",
                source_urls=[
                    "https://WWW.example.org/a/",
                    "https://invented.test/x",
                ],
            )
        ]
    )

    claims, rejected = build_claim_drafts(
        draft, known_urls=["https://example.org/a"]
    )

    assert rejected == []
    assert claims[0].source_urls == ["https://example.org/a"]


def test_a_claim_with_no_known_source_is_rejected_with_a_safe_reason() -> None:
    draft = ClaimsDraft(
        claims=[
            ClaimDraft(text="Invented.", source_urls=["https://invented.test/x"]),
            ClaimDraft(text="Real.", source_urls=["https://example.org/a"]),
        ]
    )

    claims, rejected = build_claim_drafts(
        draft, known_urls=["https://example.org/a"]
    )

    assert [claim.text for claim in claims] == ["Real."]
    assert rejected == ["claim 1: no source url from the collected findings"]
    assert "invented.test" not in " ".join(rejected)


def test_a_blank_claim_is_rejected() -> None:
    draft = ClaimsDraft(
        claims=[ClaimDraft(text="   ", source_urls=["https://example.org/a"])]
    )

    claims, rejected = build_claim_drafts(
        draft, known_urls=["https://example.org/a"]
    )

    assert claims == []
    assert rejected == ["claim 1: blank claim text"]


def test_claim_extraction_skips_old_snapshot_unless_evidence_or_critic_changes(
) -> None:
    claim = _claim_draft()
    prior = insufficient_claim(claim, reason="no_independent_source")
    draft = ClaimsDraft(claims=[claim])

    claims, rejected = build_claim_drafts(
        draft,
        known_urls=claim.source_urls,
        prior_claims=[prior],
    )

    assert claims == []
    assert rejected == ["claim 1: already checked"]

    changed, rejected = build_claim_drafts(
        draft.model_copy(
            update={
                "claims": [
                    claim.model_copy(
                        update={
                            "source_urls": [
                                *claim.source_urls,
                                "https://new.test/evidence",
                            ]
                        }
                    )
                ]
            }
        ),
        known_urls=[*claim.source_urls, "https://new.test/evidence"],
        prior_claims=[prior],
        new_source_urls=["https://new.test/evidence"],
    )
    assert [item.text for item in changed] == [claim.text]
    assert rejected == []

    same_url_changed, rejected = build_claim_drafts(
        draft,
        known_urls=claim.source_urls,
        prior_claims=[prior],
        new_source_urls=claim.source_urls,
    )
    assert [item.text for item in same_url_changed] == [claim.text]
    assert rejected == []

    reverified, rejected = build_claim_drafts(
        draft,
        known_urls=claim.source_urls,
        prior_claims=[prior],
        critique_texts=[f"Re-verify this claim: {claim.text}"],
    )
    assert [item.text for item in reverified] == [claim.text]
    assert rejected == []


def test_a_critic_gap_still_reaches_the_reverification_matcher() -> None:
    """A targetable gap carries its prose into the Critic-text matcher.

    The Critic's gaps are typed objects now. If only the raw list is handed
    on, every gap is filtered out and an explicit "re-verify this claim"
    instruction inside one is silently lost.
    """
    state = ResearchState(
        session_id="session-1",
        original_question="How mature is quantum error correction?",
        critique=Critique(
            score=4,
            gaps=[
                CritiqueGap(
                    coverage_id=None,
                    problem="Re-verify this claim: Break-even was reached.",
                    recommended_queries=[],
                )
            ],
            unsupported_claims=[],
            recommended_queries=[],
            should_continue=True,
            rationale="One claim is load-bearing.",
        ),
    )

    assert _critique_texts(state) == (
        "Re-verify this claim: Break-even was reached.",
    )


def test_a_new_publisher_restatement_is_new_evidence() -> None:
    """Both directions of the central provenance rule, from real provenance.

    ``insufficient_claim(claim, reason="no_independent_source")`` leaves
    ``consumed_finding_fingerprints`` empty, and ``_finding_is_new`` tests
    membership in that empty set — so asserting ``is True`` against such a
    prior passed for *any* implementation, ``return True`` included. The prior
    here records a provenance that names a *different* finding, which is what
    makes the positive half carry information; the negative half pins that a
    finding whose fingerprint IS recorded stops looking new.
    """
    other_finding = _check_finding("https://independent.test/older")
    claim = _claim_draft()
    prior = Claim(
        claim_id=claim_fingerprint(claim.text),
        text=claim.text,
        source_urls=list(claim.source_urls),
        verdict="insufficient_evidence",
        confidence=0.0,
        evidence=[],
        contradictions=[],
        verification_evidence=[],
        consumed_finding_fingerprints=[finding_fingerprint(other_finding)],
        consumed_coverage_ids=["topic-01"],
    )
    finding = _check_finding(
        "https://independent.test/report", content=claim.text
    )

    assert prior.consumed_finding_fingerprints == [
        finding_fingerprint(other_finding)
    ]
    assert _finding_is_new(finding, [prior]) is True
    assert _finding_is_new(other_finding, [prior]) is False


def test_extraction_messages_show_findings_and_source_quality() -> None:
    state = _check_state(
        [_check_finding()],
        [_scored("https://example.org/a", low=True)],
    )

    messages = claim_extraction_messages(state, max_findings=10)

    assert messages[0].role == "developer"
    body = messages[1].content
    assert "How mature is quantum error correction?" in body
    assert "1. [Alpha] Logical error rates fell below break-even" in body
    assert (
        "https://example.org/a: score=0.16 status=scored "
        "low_confidence=true"
    ) in body
    assert "Return an empty list" in body


def test_extraction_messages_cap_the_number_of_findings_rendered() -> None:
    findings = [
        _check_finding(content=f"Fact number {index}.")
        for index in range(1, 6)
    ]

    messages = claim_extraction_messages(_check_state(findings), max_findings=2)

    body = messages[1].content
    assert "Fact number 1." in body
    assert "Fact number 3." not in body


def _tool_step(
    iteration: int,
    tool_name: str,
    data: object,
    *,
    success: bool = True,
) -> ReActStep:
    return ReActStep(
        iteration=iteration,
        thought=f"Call {tool_name}.",
        action="use_tool",
        tool_name=tool_name,
        observation=ReActObservation(
            tool_name=tool_name,
            success=success,
            summary=f"{tool_name} ran",
        ),
        tool_result=(
            ToolResult(
                tool_name=tool_name, success=True, data=data, latency_ms=1.0
            )
            if success
            else ToolResult(
                tool_name=tool_name,
                success=False,
                error={"type": "TimeoutError", "message": "upstream timed out"},
                latency_ms=1.0,
            )
        ),
    )


def _verdict_draft(
    *,
    verdict: str = "verified",
    confidence: float = 0.9,
    evidence: list[str] | None = None,
    contradictions: list[str] | None = None,
    passages: list[EvidencePassageDraft] | None = None,
) -> ClaimVerdictDraft:
    if passages is None:
        passages = [
            EvidencePassageDraft(
                source_url="https://third.test/x",
                source_title="Independent review",
                locator="p. 1",
                excerpt=excerpt,
                stance="supports",
            )
            for excerpt in (
                evidence
                if evidence is not None
                else ["A third party agrees."]
            )
        ]
        passages.extend(
            EvidencePassageDraft(
                source_url="https://third.test/x",
                source_title="Independent regulator",
                locator="p. 2",
                excerpt=excerpt,
                stance="contradicts",
            )
            for excerpt in (contradictions or [])
        )
    return ClaimVerdictDraft(
        verdict=verdict,
        confidence=confidence,
        passages=passages,
    )


def _claim_draft(
    *, urls: list[str] | None = None
) -> ClaimDraft:
    return ClaimDraft(
        text="Logical error rates fell below break-even in 2025.",
        source_urls=urls or ["https://example.org/a"],
    )


def test_verdict_values_match_the_shared_claim_verdict_type() -> None:
    assert set(VERDICT_VALUES) == set(get_args(ClaimVerdict))


def test_retrieved_urls_are_pulled_from_every_evidence_carrying_tool() -> None:
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_search",
                {"results": [{"title": "T", "url": "https://third.test/x"}]},
            ),
            _tool_step(
                2,
                "web_scraper",
                {"url": "https://WWW.third.test/x/", "text": "Body."},
            ),
            _tool_step(
                3,
                "document_reader",
                {"source": "https://fourth.test/d.csv", "chunks": ["a"]},
            ),
            _tool_step(
                4,
                "query_memory",
                {
                    "matches": [
                        {
                            "content": "A remembered passage.",
                            "metadata": {"source_url": "https://fifth.test/m"},
                        }
                    ]
                },
            ),
        ],
        iterations=4,
        tool_calls=4,
    )

    assert retrieved_source_urls(run) == [
        "https://third.test/x",
        "https://fourth.test/d.csv",
        "https://fifth.test/m",
    ]


def test_search_only_results_are_candidates_not_retrieved_evidence() -> None:
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_search",
                {"results": [{"title": "T", "url": "https://third.test/x"}]},
            )
        ],
        iterations=1,
        tool_calls=1,
    )

    assert retrieved_source_urls(run) == []


def test_empty_and_failed_tool_payloads_yield_no_urls() -> None:
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(1, "web_search", {"results": []}),
            _tool_step(2, "web_scraper", {"url": "https://a.test/x", "text": " "}),
            _tool_step(
                3,
                "document_reader",
                {"source": "https://b.test/d", "chunks": []},
            ),
            _tool_step(4, "web_search", None, success=False),
        ],
        iterations=4,
        tool_calls=4,
    )

    assert retrieved_source_urls(run) == []


def test_the_claims_own_domains_are_never_independent() -> None:
    assert claimed_domains_for(["https://www.example.org/a"]) == ["example.org"]
    assert independent_domains(
        ["https://example.org/other", "https://third.test/x"],
        claimed_domains=["example.org"],
    ) == ["third.test"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("verified", "verified"),
        ("  CONTRADICTED ", "contradicted"),
        ("insufficient evidence", "insufficient_evidence"),
        ("insufficient-evidence", "insufficient_evidence"),
        ("probably true", "insufficient_evidence"),
        ("", "insufficient_evidence"),
    ],
)
def test_verdict_normalization_is_total(raw: str, expected: str) -> None:
    assert normalize_verdict(raw) == expected


def test_a_claim_with_no_independent_source_is_insufficient() -> None:
    verdict, confidence = resolve_verdict(
        _verdict_draft(passages=[]), independent=["third.test"]
    )

    assert verdict == "insufficient_evidence"
    assert confidence == pytest.approx(0.0)


def test_passages_require_read_urls_and_keep_bounded_independent_provenance() -> None:
    long_locator = "locator " * 100
    long_excerpt = "independent passage " * 100
    draft = _verdict_draft(
        passages=[
            EvidencePassageDraft(
                source_url="https://search-only.test/result",
                source_title="Search candidate",
                locator="result",
                excerpt="Never read.",
                stance="supports",
            ),
            EvidencePassageDraft(
                source_url="https://example.org/claim-source",
                source_title="Claim source",
                locator="p. 1",
                excerpt="Own source.",
                stance="supports",
            ),
            EvidencePassageDraft(
                source_url="https://evidence.example.test/report",
                source_title="Copied example",
                locator="p. 1",
                excerpt="Prompt example.",
                stance="supports",
            ),
            EvidencePassageDraft(
                source_url="https://independent.test/report/",
                source_title="Independent report",
                locator=long_locator,
                excerpt=long_excerpt,
                stance="supports",
            ),
            EvidencePassageDraft(
                source_url="https://WWW.independent.test/report",
                source_title="Duplicate report",
                locator=long_locator,
                excerpt=long_excerpt,
                stance="supports",
            ),
        ]
    )

    passages = valid_verification_passages(
        draft,
        retrieved_urls=[
            "https://example.org/claim-source",
            "https://evidence.example.test/report",
            "https://independent.test/report",
        ],
        claimed_publishers=["example.org"],
    )

    assert len(passages) == 1
    assert passages[0].source_url == "https://independent.test/report"
    assert len(passages[0].locator) <= MAX_PASSAGE_LOCATOR_CHARS
    assert len(passages[0].excerpt) <= MAX_PASSAGE_EXCERPT_CHARS


def test_reported_contradictions_downgrade_a_verified_verdict() -> None:
    draft = _verdict_draft(
        verdict="verified", contradictions=["A regulator disputes it."]
    )
    verdict, confidence = resolve_verdict(
        draft,
        independent=["third.test"],
        passages=[
            EvidencePassage.model_validate(passage.model_dump())
            for passage in draft.passages
        ],
    )

    assert verdict == "contradicted"
    assert confidence == pytest.approx(0.9)


def test_confidence_is_clamped_and_zeroed_for_insufficient_evidence() -> None:
    verdict, confidence = resolve_verdict(
        _verdict_draft(
            verdict="insufficient_evidence", confidence=0.8, passages=[]
        ),
        independent=["third.test"],
    )
    assert verdict == "insufficient_evidence"
    assert confidence == pytest.approx(0.0)

    high_draft = _verdict_draft(confidence=4.0)
    _, high = resolve_verdict(
        high_draft,
        independent=["third.test"],
        passages=[
            EvidencePassage.model_validate(passage.model_dump())
            for passage in high_draft.passages
        ],
    )
    assert high == pytest.approx(1.0)


def test_a_built_claim_keeps_its_own_sources_and_the_models_evidence() -> None:
    claim = build_claim(
        _claim_draft(),
        _verdict_draft(evidence=["Third party agrees."]),
        independent=["third.test"],
        retrieved_urls=["https://third.test/x"],
    )

    assert isinstance(claim, Claim)
    assert claim.source_urls == ["https://example.org/a"]
    assert claim.verdict == "verified"
    assert claim.evidence == ["Third party agrees."]
    assert claim.contradictions == []
    # Minor 1: the emitted identity is the canonical fingerprint of the text,
    # not a label — that is the value later passes merge on.
    assert claim.claim_id == claim_fingerprint(claim.text)
    assert len(claim.verification_evidence) == 1


def test_an_insufficient_claim_names_its_reason_and_invents_no_confidence() -> None:
    claim = insufficient_claim(_claim_draft(), reason="loop_failed")

    assert claim.verdict == "insufficient_evidence"
    assert claim.confidence == pytest.approx(0.0)
    assert claim.evidence == []
    assert claim.contradictions == []
    assert claim.source_urls == ["https://example.org/a"]
    assert claim.claim_id == claim_fingerprint(claim.text)
    # The reason used to reach only the event metadata, so no published
    # artifact could say why a claim went unjudged. It is on the record now,
    # as the enumerated token the caller passed rather than its explanation.
    assert claim.insufficient_reason == "loop_failed"


def test_an_insufficient_claim_rejects_an_unenumerated_reason() -> None:
    with pytest.raises(ValueError, match="reason"):
        insufficient_claim(_claim_draft(), reason="because")


def test_a_judged_claim_records_no_insufficient_reason() -> None:
    """``build_claim`` reaches ``insufficient_evidence`` too, from a verdict.

    A model that answered over evidence which was read, but whose answer
    resolved to nothing usable, produces an insufficient claim with no
    reason: nothing was unavailable, the verdict was thin. Leaving the field
    unset is what keeps that distinguishable from an unread claim.
    """
    claim = build_claim(
        _claim_draft(),
        _verdict_draft(verdict="probably true", passages=[]),
        independent=["third.test"],
        retrieved_urls=["https://third.test/x"],
    )

    assert claim.verdict == "insufficient_evidence"
    assert claim.insufficient_reason is None


def test_verification_messages_carry_the_claim_and_its_evidence() -> None:
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_scraper",
                {
                    "url": "https://third.test/x",
                    "text": "An independent review agrees.",
                },
            )
        ],
        iterations=1,
        tool_calls=1,
    )
    task = ClaimTask(
        instruction="Verify one claim.",
        claim=_claim_draft(),
        claimed_domains=["example.org"],
    )

    messages = claim_verification_messages(
        task, run, evidence_chars=500, independent=["third.test"]
    )

    body = messages[1].content
    assert "Logical error rates fell below break-even in 2025." in body
    assert "https://example.org/a" in body
    assert "third.test" in body
    assert "insufficient_evidence" in body


def test_verdict_counts_cover_every_verdict_value() -> None:
    claims = [
        build_claim(
            _claim_draft(),
            _verdict_draft(),
            independent=["third.test"],
            retrieved_urls=["https://third.test/x"],
        ),
        insufficient_claim(_claim_draft(), reason="no_independent_source"),
    ]

    counts = verdict_counts(claims)

    assert counts == {
        "verified": 1,
        "unverified": 0,
        "contradicted": 0,
        "insufficient_evidence": 1,
    }


def _checker(
    tracker: Tracker,
    completer: ScriptedCompleter,
    *,
    tools: list[object] | None = None,
    max_claims: int = 5,
) -> FactCheckerAgent:
    return FactCheckerAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name="fact_checker", max_entries=20
        ),
        tools=tools if tools is not None else fact_checker_tools(tracker),
        config=AgentRuntimeConfig(max_iterations=3, tool_budget=3),
        max_claims=max_claims,
    )


@pytest.mark.asyncio
async def test_extraction_keeps_only_claims_backed_by_real_sources(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(
                        text="Break-even was crossed in 2025.",
                        source_urls=["https://example.org/a"],
                    ),
                    ClaimDraft(
                        text="Invented.",
                        source_urls=["https://invented.test/x"],
                    ),
                ]
            )
        ]
    )
    agent = _checker(tracker, completer)
    state = _check_state([_check_finding("https://example.org/a")])

    claims, errors, provider_failed = await agent.extract_claims(state)

    assert provider_failed is False
    assert [claim.text for claim in claims] == [
        "Break-even was crossed in 2025."
    ]
    assert errors[0].error_type == "fact_checker_invalid_claim"
    assert errors[0].recoverable is True


@pytest.mark.asyncio
async def test_extraction_makes_no_provider_call_without_findings(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter()
    agent = _checker(tracker, completer)

    claims, errors, provider_failed = await agent.extract_claims(_check_state([]))

    assert claims == []
    assert provider_failed is False
    assert errors[0].error_type == "fact_checker_no_findings"
    assert completer.calls == []


@pytest.mark.asyncio
async def test_extraction_provider_failure_is_non_recoverable(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(outputs=[_output_limit_error()])
    agent = _checker(tracker, completer)
    state = _check_state([_check_finding("https://example.org/a")])

    claims, errors, provider_failed = await agent.extract_claims(state)

    assert claims == []
    assert provider_failed is True
    assert errors[0].error_type == "fact_checker_extraction_provider_error"
    assert errors[0].recoverable is False
    assert errors[0].details["operation"] == "fact_checker_claim_extraction"
    provider = errors[0].details["provider_failure"]
    assert provider["kind"] == "output_limit"
    assert provider["configured_max_tokens"] == 4096
    assert provider["request_attempt"] == 1


@pytest.mark.asyncio
async def test_a_claim_with_only_its_own_domain_retrieved_is_insufficient(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter()
    agent = _checker(tracker, completer)
    task = agent.claim_task(
        AgentTask(instruction="Check claims."), _claim_draft()
    )
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_scraper",
                {"url": "https://example.org/other", "text": "Same publisher."},
            )
        ],
        iterations=1,
        tool_calls=1,
    )

    claim, reason, errors, provider_failed = await agent.verify_claim(task, run)

    assert claim.verdict == "insufficient_evidence"
    assert claim.confidence == pytest.approx(0.0)
    assert reason == "no_independent_source"
    assert errors == []
    assert provider_failed is False
    assert completer.calls == []


@pytest.mark.asyncio
async def test_a_loop_that_died_to_the_provider_is_insufficient(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter()
    agent = _checker(tracker, completer)
    task = agent.claim_task(
        AgentTask(instruction="Check claims."), _claim_draft()
    )
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="provider_error",
        steps=[
            _tool_step(
                1,
                "web_scraper",
                {
                    "url": "https://third.test/x",
                    "text": "An independent review agrees.",
                },
            )
        ],
        iterations=1,
        tool_calls=1,
    )

    claim, reason, _, _ = await agent.verify_claim(task, run)

    assert claim.verdict == "insufficient_evidence"
    assert reason == "loop_failed"
    assert completer.calls == []


@pytest.mark.asyncio
async def test_every_tool_call_failing_is_insufficient_not_unverified(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter()
    agent = _checker(tracker, completer)
    task = agent.claim_task(
        AgentTask(instruction="Check claims."), _claim_draft()
    )
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(1, "web_search", None, success=False),
            _tool_step(2, "web_scraper", None, success=False),
        ],
        iterations=2,
        tool_calls=2,
    )

    claim, reason, _, _ = await agent.verify_claim(task, run)

    assert claim.verdict == "insufficient_evidence"
    assert reason == "no_independent_source"
    assert completer.calls == []


@pytest.mark.asyncio
async def test_independent_evidence_produces_a_model_verdict(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(outputs=[_verdict_draft(verdict="verified")])
    agent = _checker(tracker, completer)
    task = agent.claim_task(
        AgentTask(instruction="Check claims."), _claim_draft()
    )
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_scraper",
                {
                    "url": "https://third.test/x",
                    "text": "An independent review agrees.",
                },
            )
        ],
        iterations=1,
        tool_calls=1,
    )

    claim, reason, errors, provider_failed = await agent.verify_claim(task, run)

    assert claim.verdict == "verified"
    assert claim.confidence == pytest.approx(0.9)
    assert reason is None
    assert errors == []
    assert provider_failed is False


@pytest.mark.asyncio
async def test_a_contradiction_survives_a_verified_model_answer(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        outputs=[
            _verdict_draft(
                verdict="verified",
                contradictions=["A regulator published the opposite figure."],
            )
        ]
    )
    agent = _checker(tracker, completer)
    task = agent.claim_task(
        AgentTask(instruction="Check claims."), _claim_draft()
    )
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_scraper",
                {
                    "url": "https://third.test/x",
                    "text": "An independent review agrees.",
                },
            )
        ],
        iterations=1,
        tool_calls=1,
    )

    claim, reason, _, _ = await agent.verify_claim(task, run)

    assert claim.verdict == "contradicted"
    assert claim.contradictions == [
        "A regulator published the opposite figure."
    ]
    assert reason is None


@pytest.mark.asyncio
async def test_a_verification_provider_failure_is_insufficient_not_invented(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(outputs=[ProviderTimeoutError("timed out")])
    agent = _checker(tracker, completer)
    task = agent.claim_task(
        AgentTask(instruction="Check claims."), _claim_draft()
    )
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_scraper",
                {
                    "url": "https://third.test/x",
                    "text": "An independent review agrees.",
                },
            )
        ],
        iterations=1,
        tool_calls=1,
    )

    claim, reason, errors, provider_failed = await agent.verify_claim(task, run)

    assert claim.verdict == "insufficient_evidence"
    assert claim.confidence == pytest.approx(0.0)
    assert reason == "provider_unavailable"
    assert provider_failed is True
    assert errors[0].error_type == "fact_checker_verification_provider_error"
    assert errors[0].recoverable is False


@pytest.mark.asyncio
async def test_attribution_never_consumes_a_post_digest_finding(
    tracker: Tracker,
) -> None:
    """Past the digest cut, an unseen finding must not look consumed.

    ``claim_extraction_messages`` shows the model only the first
    ``finding_digest`` candidate findings, but the prompt's coverage order
    still lists every planned topic. A claim citing a URL whose finding sits
    past that cut is attributed nothing: the model never saw the finding, so
    it cannot have consumed it, and counting its coverage id would report a
    topic as covered on evidence no one read. The correct failure direction is
    the branch's own — extra work, never skipped evidence.
    """
    topics = [
        SubTopic(
            coverage_id=f"topic-{index:02d}",
            title=f"Alpha {index}",
            rationale="Load-bearing.",
            search_queries=[f"alpha {index} evidence"],
            success_criteria=["A named source."],
            priority=index,
        )
        for index in range(1, 42)
    ]
    findings = [
        _check_finding(
            f"https://example.org/{index}",
            content=f"Fact number {index}.",
            sub_topic=topic.title,
        )
        for index, topic in enumerate(topics, start=1)
    ]
    post_digest_url = findings[-1].source_url
    post_digest_coverage = topics[-1].coverage_id
    state = ResearchState(
        session_id="session-1",
        original_question="How mature is quantum error correction?",
        sub_topics=topics,
        raw_findings=findings,
        evaluated_sources=[_scored(finding.source_url) for finding in findings],
        memory_context=MemorySnapshot(),
    )
    assert len(findings) > DEFAULT_FINDING_DIGEST
    assert post_digest_url not in claim_extraction_messages(
        state, max_findings=DEFAULT_FINDING_DIGEST
    )[1].content

    completer = ScriptedCompleter(
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(
                        text="The post-digest finding states a fact.",
                        source_urls=[post_digest_url],
                    )
                ]
            )
        ]
    )
    agent = _checker(tracker, completer)

    claims, _, _ = await agent.extract_claims(state)
    task = agent.claim_task(AgentTask(instruction="Check claims."), claims[0])

    assert [claim.text for claim in claims] == [
        "The post-digest finding states a fact."
    ]
    assert task.consumed_finding_fingerprints == []
    assert task.consumed_coverage_ids == []
    assert post_digest_coverage not in task.consumed_coverage_ids


def _provenance_task() -> ClaimTask:
    return ClaimTask(
        instruction='Verify this claim against independent sources.',
        claim=_claim_draft(),
        claimed_domains=["example.org"],
        consumed_finding_fingerprints=[finding_fingerprint(_check_finding())],
        consumed_coverage_ids=["topic-01"],
    )


@pytest.mark.asyncio
async def test_a_provider_failure_records_no_consumed_provenance(
    tracker: Tracker,
) -> None:
    """An unjudged claim must not make its finding look already consumed.

    ``consumed_finding_fingerprints`` is the only thing ``_finding_is_new``
    consults and ``extract_claims`` returns early once no finding is new, so
    recording provenance on a claim no model ever judged makes a transient
    provider blip suppress that finding for the rest of the run. The recorded
    rationale ("the finding was read and judged") is true for
    ``no_independent_source`` and false for both failure reasons.
    """
    finding = _check_finding()
    independent_run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        steps=[
            _tool_step(
                1,
                "web_scraper",
                {
                    "url": "https://third.test/x",
                    "text": "An independent review agrees.",
                },
            )
        ],
        iterations=1,
        tool_calls=1,
    )
    failed_loop_run = independent_run.model_copy(
        update={"stop_reason": "provider_error"}
    )

    failed_loop, reason, _, _ = await _checker(
        tracker, ScriptedCompleter()
    ).verify_claim(_provenance_task(), failed_loop_run)
    outage, outage_reason, _, _ = await _checker(
        tracker, ScriptedCompleter(outputs=[ProviderTimeoutError("timed out")])
    ).verify_claim(_provenance_task(), independent_run)

    assert reason == "loop_failed"
    assert outage_reason == "provider_unavailable"
    for claim in (failed_loop, outage):
        assert claim.verdict == "insufficient_evidence"
        assert claim.consumed_finding_fingerprints == []
        assert claim.consumed_coverage_ids == []
        assert _finding_is_new(finding, [claim]) is True


@pytest.mark.asyncio
async def test_a_judged_insufficient_claim_still_records_its_provenance(
    tracker: Tracker,
) -> None:
    """The positive control for the failure-reason fix.

    ``no_independent_source`` is a judgement about this finding: the model was
    never called because nothing independent of the claim's own publisher was
    retrieved, and re-reading the same finding next pass would buy nothing.
    Its provenance must survive, or the fix would have traded a suppressed
    re-extraction for an unbounded one.
    """
    finding = _check_finding()
    claim, reason, _, _ = await _checker(
        tracker, ScriptedCompleter()
    ).verify_claim(
        _provenance_task(),
        ReActRun(
            agent_name="fact_checker",
            stop_reason="finished",
            steps=[
                _tool_step(
                    1,
                    "web_scraper",
                    {"url": "https://example.org/other", "text": "Same publisher."},
                )
            ],
            iterations=1,
            tool_calls=1,
        ),
    )

    assert reason == "no_independent_source"
    assert claim.consumed_finding_fingerprints == [
        finding_fingerprint(finding)
    ]
    assert claim.consumed_coverage_ids == ["topic-01"]
    assert _finding_is_new(finding, [claim]) is False


def test_state_update_carries_verified_claims_and_errors(
    tracker: Tracker,
) -> None:
    agent = _checker(tracker, ScriptedCompleter())
    claim = insufficient_claim(_claim_draft(), reason="no_independent_source")
    run = ReActRun(agent_name="fact_checker", stop_reason="finished")

    update = agent.state_update(VerifiedClaims(claims=[claim]), run)

    assert update["verified_claims"] == [claim]
    assert update["errors"] == []


@pytest.mark.asyncio
async def test_a_second_pass_carries_the_claims_of_the_first(
    tracker: Tracker,
) -> None:
    """``verified_claims`` replaces, so the update is a whole snapshot.

    An update carrying only this pass's claims would erase every earlier one
    when the state merges it, so the producer merges into the snapshot it
    found on the state it was handed.
    """
    earlier = Claim(
        claim_id=claim_fingerprint("An earlier pass verified this."),
        text="An earlier pass verified this.",
        source_urls=["https://example.org/a"],
        verdict="verified",
        confidence=0.8,
        evidence=["An independent source reported the same figure."],
        contradictions=[],
        verification_evidence=[
            EvidencePassage(
                source_url="https://third.test/x",
                source_title="Independent review",
                locator="p. 1",
                excerpt="An independent source reported the same figure.",
                stance="supports",
            )
        ],
    )
    completer = ScriptedCompleter(
        decisions=list(_check_decisions()),
        outputs=[
            ClaimsDraft(claims=[_claim_draft()]),
            _verdict_draft(verdict="verified"),
        ],
    )
    agent = _checker(
        tracker,
        completer,
        tools=fact_checker_tools(
            tracker,
            search=FakeSearchClient(
                [search_response(url="https://third.test/x")]
            ),
        ),
    )
    state = _check_state(
        [_check_finding("https://example.org/a")],
        [_scored("https://example.org/a")],
    ).model_copy(update={"verified_claims": [earlier]})

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    expected = [
        "An earlier pass verified this.",
        "Logical error rates fell below break-even in 2025.",
    ]
    assert [
        claim.text for claim in outcome.state_update["verified_claims"]
    ] == expected
    merged = merge_research_state(state, outcome.state_update)
    assert [claim.text for claim in merged.verified_claims] == expected


def _check_decisions() -> list[object]:
    return [
        use_tool(
            "Look for an independent source.",
            "web_search",
            '{"query": "break-even 2025"}',
        ),
        use_tool(
            "Read the independent source before judging the claim.",
            "web_scraper",
            '{"url": "https://third.test/x"}',
        ),
        finish("I have independent material.", "Checked."),
    ]


def test_the_per_claim_event_reports_tool_calls_and_the_verdict() -> None:
    claim = insufficient_claim(_claim_draft(), reason="no_independent_source")
    run = ReActRun(
        agent_name="fact_checker",
        stop_reason="finished",
        iterations=2,
        tool_calls=2,
    )

    event = claim_checked_event(
        claim, run, index=1, independent_sources=0,
        reason="no_independent_source",
    )

    assert event.event_type == "fact_checker.claim.checked"
    assert event.source == "agent.fact_checker"
    assert event.metadata["verdict"] == "insufficient_evidence"
    assert event.metadata["tool_calls"] == 2
    assert event.metadata["independent_sources"] == 0
    assert event.metadata["reason"] == "no_independent_source"
    assert event.metadata["contradictions"] == 0


def test_claim_events_report_bounded_provenance_counts_without_excerpts() -> None:
    claim = Claim(
        claim_id=claim_fingerprint("A measured result was reported."),
        text="A measured result was reported.",
        source_urls=["https://example.org/a"],
        verdict="verified",
        confidence=0.9,
        evidence=["An independent study agrees."],
        contradictions=[],
        verification_evidence=[
            EvidencePassage(
                source_url="https://third.test/x",
                source_title="Independent study",
                locator="p. 4",
                excerpt="An independent study agrees.",
                stance="supports",
            )
        ],
    )
    event = claim_checked_event(
        claim,
        ReActRun(agent_name="fact_checker", stop_reason="finished"),
        index=1,
        independent_sources=1,
        reason=None,
    )

    assert event.metadata["support_passages"] == 1
    assert event.metadata["contradiction_passages"] == 0
    assert event.metadata["unique_publishers"] == 1
    assert "An independent study agrees." not in event.metadata.values()


# --- Provenance-driven idempotence: real multiple passes -------------------
#
# The regression below runs the REAL agent over several states in sequence —
# one ``agent.run`` per research pass, each with its own extraction and
# verification provider calls — instead of hand-merging ``Claim`` objects.
# That is the only shape that can expose the reviewed defect: the extraction
# prompt is allowed to rewrite a finding into a self-contained claim, so the
# raw finding and the extracted claim here are deliberate paraphrases that
# share no normalized text.

ALPHA = "Alpha"
BETA = "Beta"
SHARED_URL = "https://example.org/a"
INDEPENDENT_URL = "https://third.test/x"
FINDING_A_CONTENT = (
    "The measured logical error rate dropped below the break-even "
    "threshold during 2025."
)
CHANGED_FINDING_A_CONTENT = (
    "A re-measurement found the logical error rate climbing above the "
    "break-even threshold late in 2025."
)
FINDING_B_CONTENT = (
    "A separate throughput benchmark recorded a 30 percent rise in queue "
    "capacity in 2025."
)
CLAIM_A_TEXT = "Logical error rates fell below break-even in 2025."
CLAIM_B_TEXT = "Queue throughput rose 30 percent in 2025."


def _topic(title: str, *, priority: int) -> SubTopic:
    return SubTopic(
        coverage_id=f"topic-{priority:02d}",
        title=title,
        rationale=f"Measure what happened in {title}.",
        search_queries=[f"{title} 2025 measurements"],
        success_criteria=[f"Two independent estimates for {title}."],
        priority=priority,
    )


def _alpha_finding(content: str = FINDING_A_CONTENT) -> Finding:
    return _check_finding(SHARED_URL, content=content, sub_topic=ALPHA)


def _beta_finding() -> Finding:
    return _check_finding(SHARED_URL, content=FINDING_B_CONTENT, sub_topic=BETA)


def _coverage_state(
    findings: list[Finding],
    *,
    critique: Critique | None = None,
) -> ResearchState:
    """One planned two-topic pass with both topics sharing one source URL.

    Both findings live at ``SHARED_URL`` on purpose: URL overlap must never
    stand in for coverage, so a claim extracted for Alpha must leave Beta
    uncovered even though the two findings carry the same URL.
    """
    return ResearchState(
        session_id="session-1",
        original_question="How mature is quantum error correction?",
        sub_topics=[_topic(ALPHA, priority=1), _topic(BETA, priority=2)],
        raw_findings=list(findings),
        evaluated_sources=[_scored(SHARED_URL)],
        critique=critique,
        memory_context=MemorySnapshot(),
    )


def _verification_decisions() -> list[object]:
    """One verification loop for one claim: search, read it, then judge."""
    return [
        use_tool(
            "Look for an independent source.",
            "web_search",
            '{"query": "break-even 2025"}',
        ),
        use_tool(
            "Read the independent source before judging the claim.",
            "web_scraper",
            f'{{"url": "{INDEPENDENT_URL}"}}',
        ),
        finish("I have independent material.", "Checked."),
    ]


async def _research_pass(
    agent: FactCheckerAgent,
    tracker: Tracker,
    state: ResearchState,
) -> AgentRun[VerifiedClaims]:
    """One full pass over one state, exactly as the graph would run it."""
    async with tracker.session_span(state.session_id, state.original_question):
        return await agent.run(state)


def _snapshot(outcome: AgentRun[VerifiedClaims]) -> list[Claim]:
    return list(outcome.state_update["verified_claims"])


def _structured_names(completer: ScriptedCompleter, since: int) -> list[str]:
    return [name for name, _, _ in completer.calls[since:]]


def _checker_for_passes(
    tracker: Tracker,
    completer: ScriptedCompleter,
    *,
    searches: int,
) -> FactCheckerAgent:
    return _checker(
        tracker,
        completer,
        tools=fact_checker_tools(
            tracker,
            search=FakeSearchClient(
                [
                    search_response(url=INDEPENDENT_URL)
                    for _ in range(searches)
                ]
            ),
        ),
    )


@pytest.mark.asyncio
async def test_unchanged_evidence_makes_every_later_pass_free(
    tracker: Tracker,
) -> None:
    """(a) A repeated fact must not re-buy extraction or verification.

    Four real passes over the same unchanged evidence: the first extracts
    and verifies, and passes two through four must issue ZERO provider
    calls of either kind while the canonical snapshot stays exactly one
    claim.
    """
    completer = ScriptedCompleter(
        decisions=list(_verification_decisions()),
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(text=CLAIM_A_TEXT, source_urls=[SHARED_URL])
                ]
            ),
            _verdict_draft(verdict="verified"),
        ],
    )
    agent = _checker_for_passes(tracker, completer, searches=1)
    state = _coverage_state([_alpha_finding()])

    first = await _research_pass(agent, tracker, state)
    first_snapshot = _snapshot(first)
    assert [claim.verdict for claim in first_snapshot] == ["verified"]
    assert _structured_names(completer, 0) == [
        "ClaimsDraft",
        "ClaimVerdictDraft",
    ]
    assert first_snapshot[0].consumed_finding_fingerprints == [
        finding_fingerprint(_alpha_finding())
    ]
    assert first_snapshot[0].consumed_coverage_ids == ["topic-01"]

    after_first = len(completer.calls)
    react_after_first = len(completer.react_calls)
    carried = merge_research_state(state, first.state_update)
    for _ in range(3):
        outcome = await _research_pass(agent, tracker, carried)

        assert _structured_names(completer, after_first) == []
        assert completer.react_calls[react_after_first:] == []
        snapshot = _snapshot(outcome)
        assert len(snapshot) == 1
        assert snapshot[0].claim_id == first_snapshot[0].claim_id
        assert snapshot[0].verdict == "verified"
        carried = merge_research_state(carried, outcome.state_update)


@pytest.mark.asyncio
async def test_a_changed_finding_at_the_same_url_reopens_the_claim(
    tracker: Tracker,
) -> None:
    """(b) New content behind a known URL is new evidence, and the earlier
    record's provenance survives the replacement (R1's union)."""
    completer = ScriptedCompleter(
        decisions=[
            *_verification_decisions(),
            *_verification_decisions(),
        ],
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(text=CLAIM_A_TEXT, source_urls=[SHARED_URL])
                ]
            ),
            _verdict_draft(verdict="verified"),
            ClaimsDraft(
                claims=[
                    ClaimDraft(text=CLAIM_A_TEXT, source_urls=[SHARED_URL])
                ]
            ),
            _verdict_draft(
                verdict="verified",
                evidence=["A second independent review confirms the figure."],
            ),
        ],
    )
    agent = _checker_for_passes(tracker, completer, searches=2)
    state = _coverage_state([_alpha_finding()])

    first = await _research_pass(agent, tracker, state)
    after_first = len(completer.calls)
    carried = merge_research_state(state, first.state_update)
    changed = carried.model_copy(
        update={"raw_findings": [_alpha_finding(CHANGED_FINDING_A_CONTENT)]}
    )

    second = await _research_pass(agent, tracker, changed)

    assert _structured_names(completer, after_first) == [
        "ClaimsDraft",
        "ClaimVerdictDraft",
    ]
    snapshot = _snapshot(second)
    assert len(snapshot) == 1
    assert snapshot[0].claim_id == first.state_update["verified_claims"][
        0
    ].claim_id
    assert snapshot[0].evidence == [
        "A second independent review confirms the figure."
    ]
    assert snapshot[0].consumed_finding_fingerprints == [
        finding_fingerprint(_alpha_finding()),
        finding_fingerprint(_alpha_finding(CHANGED_FINDING_A_CONTENT)),
    ]
    assert snapshot[0].consumed_coverage_ids == ["topic-01"]


@pytest.mark.asyncio
async def test_an_untouched_coverage_id_sharing_the_url_stays_uncovered(
    tracker: Tracker,
) -> None:
    """(c) One shared URL must not cover two planned topics.

    Alpha's claim cites the URL both findings carry. Beta's coverage id was
    never consumed, so Beta's finding stays uncovered and must sort first in
    the next pass's extraction order — the old URL-overlap rule marked it
    covered and left Alpha's finding in front.
    """
    completer = ScriptedCompleter(
        decisions=[
            *_verification_decisions(),
            *_verification_decisions(),
        ],
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(text=CLAIM_A_TEXT, source_urls=[SHARED_URL])
                ]
            ),
            _verdict_draft(verdict="verified"),
            ClaimsDraft(
                claims=[
                    ClaimDraft(text=CLAIM_B_TEXT, source_urls=[SHARED_URL])
                ]
            ),
            _verdict_draft(verdict="verified"),
        ],
    )
    agent = _checker_for_passes(tracker, completer, searches=2)
    state = _coverage_state([_alpha_finding(), _beta_finding()])

    first = await _research_pass(agent, tracker, state)
    covered = _snapshot(first)[0]
    assert covered.consumed_coverage_ids == ["topic-01"]
    assert covered.consumed_finding_fingerprints == [
        finding_fingerprint(_alpha_finding())
    ]

    after_first = len(completer.calls)
    carried = merge_research_state(state, first.state_update)
    ordered = ordered_findings_for_extraction(
        carried, prior_claims=_snapshot(first)
    )

    assert ordered[0].content == FINDING_B_CONTENT

    second = await _research_pass(agent, tracker, carried)

    assert _structured_names(completer, after_first) == [
        "ClaimsDraft",
        "ClaimVerdictDraft",
    ]
    snapshot = _snapshot(second)
    assert [claim.text for claim in snapshot] == [CLAIM_A_TEXT, CLAIM_B_TEXT]
    extracted_beta = snapshot[1]
    assert extracted_beta.consumed_finding_fingerprints == [
        finding_fingerprint(_beta_finding())
    ]
    assert extracted_beta.consumed_coverage_ids == ["topic-02"]
    assert {
        coverage_id
        for claim in snapshot
        for coverage_id in claim.consumed_coverage_ids
    } == {"topic-01", "topic-02"}


@pytest.mark.asyncio
async def test_a_critic_reverification_replaces_the_claim_without_duplicating(
    tracker: Tracker,
) -> None:
    """(d) The Critic's explicit override still re-opens a claim whose
    evidence is unchanged, and the replacement keeps one record."""
    completer = ScriptedCompleter(
        decisions=[
            *_verification_decisions(),
            *_verification_decisions(),
        ],
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(text=CLAIM_A_TEXT, source_urls=[SHARED_URL])
                ]
            ),
            _verdict_draft(verdict="verified"),
            ClaimsDraft(
                claims=[
                    ClaimDraft(text=CLAIM_A_TEXT, source_urls=[SHARED_URL])
                ]
            ),
            _verdict_draft(
                verdict="verified",
                contradictions=["A regulator disputes the figure."],
            ),
        ],
    )
    agent = _checker_for_passes(tracker, completer, searches=2)
    state = _coverage_state([_alpha_finding()])

    first = await _research_pass(agent, tracker, state)
    first_claim = _snapshot(first)[0]
    after_first = len(completer.calls)
    carried = merge_research_state(state, first.state_update).model_copy(
        update={
            "critique": Critique(
                score=4,
                gaps=["The break-even claim needs a second look."],
                unsupported_claims=[],
                recommended_queries=[f"Re-verify this claim: {CLAIM_A_TEXT}"],
                should_continue=True,
                rationale="One claim is load-bearing.",
            )
        }
    )

    second = await _research_pass(agent, tracker, carried)

    assert _structured_names(completer, after_first) == [
        "ClaimsDraft",
        "ClaimVerdictDraft",
    ]
    snapshot = _snapshot(second)
    assert len(snapshot) == 1
    assert snapshot[0].claim_id == first_claim.claim_id
    assert snapshot[0].verdict == "contradicted"
    assert snapshot[0].contradictions == ["A regulator disputes the figure."]
    assert snapshot[0].consumed_finding_fingerprints == (
        first_claim.consumed_finding_fingerprints
    )
    assert snapshot[0].consumed_coverage_ids == ["topic-01"]


def test_consumed_provenance_attributes_one_shared_url_to_one_finding() -> None:
    """The R4 narrowing: per consumed finding, first in extraction order."""
    draft = ClaimDraft(text=CLAIM_A_TEXT, source_urls=[SHARED_URL])
    coverage_ids = {ALPHA.casefold(): "topic-01", BETA.casefold(): "topic-02"}

    fingerprints, coverage = consumed_provenance(
        draft,
        findings=[_beta_finding(), _alpha_finding()],
        coverage_ids=coverage_ids,
    )

    assert fingerprints == [finding_fingerprint(_beta_finding())]
    assert coverage == ["topic-02"]


def test_union_claim_provenance_never_loses_what_the_earlier_pass_consumed(
) -> None:
    """A re-checked claim keeps both passes' provenance, in order."""
    earlier = build_claim(
        _claim_draft(),
        _verdict_draft(),
        retrieved_urls=[INDEPENDENT_URL],
        consumed_finding_fingerprints=["first"],
        consumed_coverage_ids=["topic-01"],
    )
    rechecked = build_claim(
        _claim_draft(),
        _verdict_draft(),
        retrieved_urls=[INDEPENDENT_URL],
        consumed_finding_fingerprints=["second"],
        consumed_coverage_ids=["topic-02"],
    )

    merged = union_claim_provenance([earlier], [rechecked])

    assert merged[0].consumed_finding_fingerprints == ["first", "second"]
    assert merged[0].consumed_coverage_ids == ["topic-01", "topic-02"]
    assert merged[0].verdict == rechecked.verdict


def test_the_completed_event_reports_every_verdict_count() -> None:
    verified = build_claim(
        _claim_draft(),
        _verdict_draft(),
        independent=["third.test"],
        retrieved_urls=["https://third.test/x"],
    )
    contradicted = build_claim(
        _claim_draft(),
        _verdict_draft(contradictions=["Disputed."]),
        independent=["third.test"],
        retrieved_urls=["https://third.test/x", "https://fourth.test/x"],
    )

    event = fact_check_completed_event(
        [verified, contradicted], tool_calls=5
    )

    assert event.event_type == "fact_checker.fact_check.completed"
    assert event.metadata["claim_count"] == 2
    assert event.metadata["verified"] == 1
    assert event.metadata["contradicted"] == 1
    assert event.metadata["unverified"] == 0
    assert event.metadata["insufficient_evidence"] == 0
    assert event.metadata["contradiction_count"] == 1
    assert event.metadata["tool_calls"] == 5


@pytest.mark.asyncio
async def test_a_full_run_verifies_each_claim_and_reports_the_counts(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=list(_check_decisions()),
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(
                        text="Break-even was crossed in 2025.",
                        source_urls=["https://example.org/a"],
                    )
                ]
            ),
            _verdict_draft(verdict="verified"),
        ],
    )
    agent = _checker(
        tracker,
        completer,
        tools=fact_checker_tools(
            tracker,
            search=FakeSearchClient(
                [search_response(url="https://third.test/x")]
            ),
        ),
    )
    state = _check_state(
        [_check_finding("https://example.org/a")],
        [_scored("https://example.org/a")],
    )

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.agent_name == "fact_checker"
    assert outcome.result is not None
    assert [claim.verdict for claim in outcome.result.claims] == ["verified"]

    merged = merge_research_state(state, outcome.state_update)
    assert len(merged.verified_claims) == 1

    events = outcome.state_update["events"]
    types = [event.event_type for event in events]
    assert types[0] == "fact_checker.claims.extracted"
    assert "fact_checker.claim.checked" in types
    assert types[-1] == "fact_checker.fact_check.completed"
    completed = events[-1]
    assert completed.metadata["claim_count"] == 1
    assert completed.metadata["verified"] == 1
    assert completed.metadata["contradiction_count"] == 0
    checked = next(
        event for event in events
        if event.event_type == "fact_checker.claim.checked"
    )
    assert checked.metadata["tool_calls"] >= 1


@pytest.mark.asyncio
async def test_fact_checker_react_handles_empty_unused_tool_name(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[
            use_tool(
                "Look for an independent source.",
                "web_search",
                '{"query": "break-even 2025"}',
            ),
            ReActDecision(
                thought="The independent source is enough.",
                action="finish",
                tool_name="",
                tool_input_json="{}",
                final_answer="Checked.",
            ),
        ],
        outputs=[
            ClaimsDraft(claims=[_claim_draft()]),
            _verdict_draft(verdict="verified"),
        ],
    )
    agent = _checker(
        tracker,
        completer,
        tools=fact_checker_tools(
            tracker,
            search=FakeSearchClient(
                [search_response(url="https://third.test/x")]
            ),
        ),
    )
    state = _check_state([_check_finding()])

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    assert outcome.react.stop_reason == "finished"
    assert outcome.react.steps[-1].tool_name is None


@pytest.mark.asyncio
async def test_a_run_without_findings_verifies_nothing_and_says_so(
    tracker: Tracker,
) -> None:
    agent = _checker(tracker, ScriptedCompleter())
    state = _check_state([])

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.result is not None
    assert outcome.result.claims == []
    assert outcome.errors[0].error_type == "fact_checker_no_findings"
    completed = outcome.state_update["events"][-1]
    assert completed.metadata["claim_count"] == 0
    assert completed.metadata["insufficient_evidence"] == 0


@pytest.mark.asyncio
async def test_a_verification_provider_failure_stops_further_claims(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[*_check_decisions(), *_check_decisions()],
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(text="First.", source_urls=["https://example.org/a"]),
                    ClaimDraft(text="Second.", source_urls=["https://example.org/a"]),
                ]
            ),
            _verdict_draft(),
            _output_limit_error(),
        ],
    )
    agent = _checker(
        tracker,
        completer,
        tools=fact_checker_tools(
            tracker,
            search=FakeSearchClient(
                [
                    search_response(url="https://third.test/x"),
                    search_response(url="https://third.test/x"),
                ]
            ),
        ),
    )
    state = _check_state([_check_finding("https://example.org/a")])

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.result is not None
    assert [claim.verdict for claim in outcome.result.claims] == [
        "verified",
        "insufficient_evidence",
    ]
    assert outcome.react.stop_reason == "provider_error"
    types = {error.error_type for error in outcome.errors}
    assert "fact_checker_verification_provider_error" in types
    verification_error = next(
        error
        for error in outcome.errors
        if error.error_type == "fact_checker_verification_provider_error"
    )
    assert verification_error.details["operation"] == (
        "fact_checker_claim_verification"
    )
    provider = verification_error.details["provider_failure"]
    assert provider["kind"] == "output_limit"
    assert provider["configured_max_tokens"] == 4096
    assert provider["request_attempt"] == 1


@pytest.mark.asyncio
async def test_react_decision_output_limit_remains_a_conservative_fallback(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[_output_limit_error()],
        outputs=[
            ClaimsDraft(
                claims=[
                    ClaimDraft(
                        text="First.", source_urls=["https://example.org/a"]
                    ),
                    ClaimDraft(
                        text="Second.", source_urls=["https://example.org/a"]
                    ),
                ]
            )
        ],
    )
    agent = _checker(tracker, completer)
    state = _check_state([_check_finding("https://example.org/a")])

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.result is not None
    assert [claim.text for claim in outcome.result.claims] == ["First."]
    claim = outcome.result.claims[0]
    assert claim.verdict == "insufficient_evidence"
    assert claim.confidence == pytest.approx(0.0)
    assert claim.evidence == []
    assert claim.contradictions == []

    assert outcome.react.stop_reason == "provider_error"
    assert outcome.react.steps == []
    assert [error.error_type for error in outcome.errors] == [
        "agent_provider_error"
    ]
    provider_error = outcome.errors[0]
    assert provider_error.recoverable is False
    assert provider_error.details["operation"] == "react_decision"
    provider = provider_error.details["provider_failure"]
    assert provider["kind"] == "output_limit"
    assert provider["configured_max_tokens"] == 4096
    assert provider["request_attempt"] == 1

    checked = next(
        event
        for event in outcome.state_update["events"]
        if event.event_type == "fact_checker.claim.checked"
    )
    assert checked.metadata["verdict"] == "insufficient_evidence"
    assert checked.metadata["reason"] == "loop_failed"

    assert [schema for schema, _, _ in completer.calls] == [
        "ClaimsDraft",
    ]
    assert completer.budgets == [None]
    assert completer.react_budgets == [
        AgentRuntimeConfig().react_decision_max_tokens
    ]


def test_build_claim_drafts_drops_the_example_url() -> None:
    """The claim-extraction example URL is not one of the collected findings."""
    draft = ClaimsDraft(
        claims=[
            ClaimDraft(
                text="Copied from the example.",
                source_urls=["https://evidence.example.test/report"],
            ),
            ClaimDraft(
                text="A real finding.",
                source_urls=["https://real.test/one"],
            ),
        ]
    )

    claims, rejected = build_claim_drafts(
        draft, known_urls=("https://real.test/one",)
    )

    assert [claim.text for claim in claims] == ["A real finding."]
    assert rejected == [
        "claim 1: no source url from the collected findings"
    ]
