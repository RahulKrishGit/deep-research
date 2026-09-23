"""Tests for the Source Evaluator's scoring maths and record building."""

from __future__ import annotations

import pytest

from deep_research.agents.evidence import (
    TemporalClaim,
    build_evidence_unit,
    build_read_dossiers,
    build_read_record,
    compute_assessment_revision,
    read_assessment_revision,
    read_dated_tokens,
    source_origin_id,
)
from deep_research.agents.fact_checker import (
    AdjudicationPacket,
    ClaimDraft,
    ClaimVerdictDraft,
    SupportAssessment,
    _packet_has_pair,
    adjudication_messages,
    build_adjudication_packet,
    claim_eligibility,
    validate_adjudication,
)
from deep_research.agents.source_evaluator import (
    AUTHORITY_WEIGHT,
    FALLBACK_REASONS,
    LOW_CONFIDENCE_THRESHOLD,
    RECENCY_WEIGHT,
    RELEVANCE_WEIGHT,
    REPUTATION_BLEND,
    EvaluatedSources,
    SourceEvaluationTask,
    SourceEvaluatorAgent,
    SourceScoreDraft,
    SourceScoresDraft,
    assess_new_sources,
    average_score,
    blend_authority,
    build_rationale,
    build_scored_source,
    clamp_unit,
    evaluation_completed_event,
    fallback_scored_source,
    low_confidence_count,
    overall_score,
)
from deep_research.agents.sources import SourceGroup, normalize_source_url
from deep_research.agents.steps import ReActRun
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import TokenUsage, Tracker
from deep_research.providers import (
    ProviderOutputLimitError,
    ProviderResponseTelemetry,
)
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    QUALITY_CONTRACT_VERSION,
    Claim,
    EvidenceUnit,
    Finding,
    MemorySnapshot,
    ReadRecord,
    ResearchState,
    ScoredSource,
    merge_research_state,
)
from tests.agent_fakes import ScriptedCompleter
from tests.research_fakes import FakeReputationSource

EVAL_EXTRACTED_AT = "2026-08-01T12:00:00+00:00"


def _output_limit_error() -> ProviderOutputLimitError:
    return ProviderOutputLimitError(
        ProviderResponseTelemetry(
            finish_reason_category="length",
            configured_max_tokens=4096,
            usage=TokenUsage(input_tokens=5, output_tokens=4096),
            request_attempt=1,
        )
    )


def _group(
    *,
    url: str = "https://example.org/a",
    sub_topics: list[str] | None = None,
) -> SourceGroup:
    topics = sub_topics if sub_topics is not None else ["Alpha"]
    return SourceGroup(
        url=url,
        domain="example.org",
        title="QEC 2025",
        sub_topics=topics,
        findings=[
            Finding(
                content="Logical error rates fell below break-even.",
                source_url=url,
                source_title="QEC 2025",
                extracted_at=EVAL_EXTRACTED_AT,
                confidence=0.8,
                related_sub_topic=topic,
            )
            for topic in topics
        ],
    )


def _draft(
    *,
    url: str = "https://example.org/a",
    authority: float = 0.8,
    recency: float = 0.6,
    relevance: float = 0.9,
    rationale: str = "Peer-reviewed venue with dated results.",
    source_role: str = "",
    transport_relation: str = "",
    self_interest: str = "",
    publication_date: TemporalClaim | None = None,
    data_period: TemporalClaim | None = None,
    forecast_horizon: TemporalClaim | None = None,
    effective_date: TemporalClaim | None = None,
    freshness_status: str = "",
    methods_score: float | None = None,
    issuer: str = "",
    doi: str = "",
    year: str = "",
    derived_from: list[str] | None = None,
    report_number: str = "",
) -> SourceScoreDraft:
    return SourceScoreDraft(
        url=url,
        authority_score=authority,
        recency_score=recency,
        relevance_score=relevance,
        rationale=rationale,
        source_role=source_role,
        transport_relation=transport_relation,
        self_interest=self_interest,
        publication_date=publication_date,
        data_period=data_period,
        forecast_horizon=forecast_horizon,
        effective_date=effective_date,
        freshness_status=freshness_status,
        methods_score=methods_score,
        issuer=issuer,
        doi=doi,
        year=year,
        derived_from=list(derived_from or []),
        report_number=report_number,
    )


def test_the_weights_are_a_convex_combination() -> None:
    total = (
        AUTHORITY_WEIGHT
        + RECENCY_WEIGHT
        + RELEVANCE_WEIGHT
    )

    assert total == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(-3.0, 0.0), (0.0, 0.0), (0.5, 0.5), (1.0, 1.0), (7.5, 1.0)],
)
def test_clamp_unit_pins_every_value_into_the_unit_interval(
    raw: float, expected: float
) -> None:
    assert clamp_unit(raw) == pytest.approx(expected)


def test_overall_score_is_the_weighted_mean_of_the_three_dimensions() -> None:
    score = overall_score(
        authority=0.8, recency=0.6, relevance=0.9
    )

    assert score == pytest.approx(
        0.45 * 0.8 + 0.15 * 0.6 + 0.40 * 0.9
    )


def test_overall_score_stays_in_bounds_for_extreme_inputs() -> None:
    assert overall_score(
        authority=1.0, recency=1.0, relevance=1.0
    ) == pytest.approx(1.0)
    assert overall_score(
        authority=0.0, recency=0.0, relevance=0.0
    ) == pytest.approx(0.0)


def test_authority_ignores_reputation_when_none_is_known() -> None:
    assert blend_authority(0.8, None) == pytest.approx(0.8)


def test_authority_blends_a_known_reputation() -> None:
    blended = blend_authority(0.8, 0.3)

    assert blended == pytest.approx(
        (1 - REPUTATION_BLEND) * 0.8 + REPUTATION_BLEND * 0.3
    )
    assert blended < 0.8


def test_rationale_records_source_context_and_reputation() -> None:
    rationale = build_rationale(
        "Peer-reviewed.",
        reputation=0.9,
        sub_topics=["Alpha", "Beta"],
    )

    assert rationale.startswith("Peer-reviewed.")
    assert "Cited for: Alpha, Beta." in rationale
    assert "Corroboration" not in rationale
    assert "Prior reputation 0.90" in rationale


def test_rationale_is_never_blank_when_the_model_returned_nothing() -> None:
    rationale = build_rationale(
        "   ", reputation=None, sub_topics=[]
    )

    assert rationale.strip()
    assert "no sub-topic" in rationale
    assert "No prior reputation on record." in rationale


def test_a_scored_source_clamps_out_of_range_model_scores() -> None:
    source = build_scored_source(
        _group(),
        _draft(authority=9.0, recency=-2.0, relevance=0.9),
        reputation=None,
    )

    assert isinstance(source, ScoredSource)
    assert source.authority_score == pytest.approx(1.0)
    assert source.recency_score == pytest.approx(0.0)
    assert source.overall_score == pytest.approx(
        overall_score(
            authority=1.0, recency=0.0, relevance=0.9
        )
    )
    assert source.low_confidence is False


def test_a_weak_source_is_flagged_low_confidence() -> None:
    source = build_scored_source(
        _group(),
        _draft(authority=0.1, recency=0.1, relevance=0.1),
        reputation=None,
    )

    assert source.overall_score < LOW_CONFIDENCE_THRESHOLD
    assert source.low_confidence is True


def test_a_fallback_record_is_explicitly_unscored() -> None:
    source = fallback_scored_source(
        _group(), reason="unscored_provider"
    )

    assert source.url == "https://example.org/a"
    assert source.title == "QEC 2025"
    assert source.recency_score is None
    assert source.relevance_score is None
    assert source.authority_score is None
    assert source.overall_score is None
    assert source.evaluation_status == "unscored_provider"
    assert source.low_confidence is False
    assert "could not be reached" in source.rationale


def test_a_fallback_record_rejects_an_unenumerated_reason() -> None:
    with pytest.raises(ValueError, match="reason"):
        fallback_scored_source(
            _group(), reason="because"
        )


def test_observability_aggregates_are_finite_for_an_empty_run() -> None:
    assert average_score([]) is None
    assert low_confidence_count([]) == 0


def test_unscored_sources_do_not_affect_quality_aggregates() -> None:
    source = ScoredSource(
        url="https://cap.test/source",
        title="Capped source",
        authority_score=None,
        recency_score=None,
        relevance_score=None,
        overall_score=None,
        rationale="This source was not scored.",
        evaluation_status="unscored_cap",
    )

    assert average_score([source]) is None
    assert low_confidence_count([source]) == 0


def test_unrelated_findings_do_not_change_source_quality() -> None:
    first = build_scored_source(
        _group(url="https://first.test/a", sub_topics=["Alpha"]),
        _draft(url="https://first.test/a"),
        reputation=None,
    )
    second = build_scored_source(
        _group(url="https://second.test/b", sub_topics=["Alpha"]),
        _draft(url="https://second.test/b"),
        reputation=None,
    )

    assert first.overall_score == second.overall_score


@pytest.mark.asyncio
async def test_scoring_batches_unique_sources_and_stops_at_the_total_cap(
    tracker: Tracker,
) -> None:
    findings = [
        _eval_finding(f"https://source-{index}.test/page")
        for index in range(5)
    ]
    first_batch = SourceScoresDraft(
        sources=[
            _draft(url="https://source-0.test/page"),
            _draft(url="https://source-1.test/page"),
        ]
    )
    second_batch = SourceScoresDraft(
        sources=[
            _draft(url="https://source-2.test/page"),
            _draft(url="https://source-3.test/page"),
        ]
    )
    completer = ScriptedCompleter(outputs=[first_batch, second_batch])
    agent = _evaluator(
        tracker,
        completer,
        batch_size=2,
        max_total_sources=4,
    )

    task, _, _ = await agent.lookup_reputations(
        agent.build_task(_eval_state(findings))
    )
    sources, errors, provider_failed = await agent.score_sources(task)

    assert errors == []
    assert provider_failed is False
    assert [source.url for source in sources] == [
        f"https://source-{index}.test/page" for index in range(5)
    ]
    assert [source.evaluation_status for source in sources] == [
        "scored",
        "scored",
        "scored",
        "scored",
        "unscored_cap",
    ]
    assert sources[-1].overall_score is None
    assert len(completer.calls) == 2


@pytest.mark.asyncio
async def test_provider_failure_marks_only_the_failed_and_remaining_batches(
    tracker: Tracker,
) -> None:
    findings = [
        _eval_finding(f"https://source-{index}.test/page")
        for index in range(5)
    ]
    first_batch = SourceScoresDraft(
        sources=[
            _draft(url="https://source-0.test/page"),
            _draft(url="https://source-1.test/page"),
        ]
    )
    completer = ScriptedCompleter(outputs=[first_batch, _output_limit_error()])
    agent = _evaluator(
        tracker,
        completer,
        batch_size=2,
        max_total_sources=5,
    )

    task, _, _ = await agent.lookup_reputations(
        agent.build_task(_eval_state(findings))
    )
    sources, errors, provider_failed = await agent.score_sources(task)

    assert provider_failed is True
    assert errors[0].error_type == "source_evaluator_scoring_provider_error"
    assert [source.evaluation_status for source in sources] == [
        "scored",
        "scored",
        "unscored_provider",
        "unscored_provider",
        "unscored_provider",
    ]
    assert all(source.overall_score is None for source in sources[2:])


@pytest.mark.asyncio
async def test_new_sources_are_not_capped_by_prior_scored_sources(
    tracker: Tracker,
) -> None:
    findings = [
        _eval_finding("https://source-0.test/page"),
        _eval_finding("https://source-1.test/page"),
        _eval_finding("https://source-2.test/page"),
    ]
    prior = [
        build_scored_source(
            _group(url=finding.source_url),
            _draft(url=finding.source_url),
            reputation=None,
        )
        for finding in findings[:2]
    ]
    completer = ScriptedCompleter(
        outputs=[
            SourceScoresDraft(
                sources=[_draft(url="https://source-2.test/page")]
            )
        ]
    )
    agent = _evaluator(
        tracker,
        completer,
        batch_size=2,
        max_total_sources=2,
    )
    state = _eval_state(findings, evaluated_sources=prior)

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.result is not None
    assert [source.url for source in outcome.result.sources] == [
        finding.source_url for finding in findings
    ]
    assert outcome.result.sources[-1].evaluation_status == "scored"
    assert outcome.result.sources[-1].overall_score is not None
    assert len(completer.calls) == 1


@pytest.mark.asyncio
async def test_prior_unscored_cap_sources_are_eligible_on_a_later_pass(
    tracker: Tracker,
) -> None:
    findings = [
        _eval_finding("https://source-0.test/page"),
        _eval_finding("https://source-1.test/page"),
        _eval_finding("https://source-2.test/page"),
    ]
    prior = [
        build_scored_source(
            _group(url=finding.source_url),
            _draft(url=finding.source_url),
            reputation=None,
        )
        for finding in findings[:2]
    ]
    prior.append(
        fallback_scored_source(
            _group(url=findings[2].source_url), reason="unscored_cap"
        )
    )
    completer = ScriptedCompleter(
        outputs=[
            SourceScoresDraft(
                sources=[_draft(url="https://source-2.test/page")]
            )
        ]
    )
    agent = _evaluator(
        tracker,
        completer,
        batch_size=2,
        max_total_sources=2,
    )
    state = _eval_state(findings, evaluated_sources=prior)

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.result is not None
    assert outcome.result.sources[-1].evaluation_status == "scored"
    assert outcome.result.sources[-1].overall_score is not None
    assert len(completer.calls) == 1


def test_observability_aggregates_summarize_scored_sources() -> None:
    strong = build_scored_source(
        _group(), _draft(), reputation=None
    )
    weak = fallback_scored_source(
        _group(url="https://weak.test/b"),
        reason="unscored_missing",
    )

    assert low_confidence_count([strong, weak]) == 0
    assert average_score([strong, weak]) == pytest.approx(strong.overall_score)


def _evaluator(
    tracker: Tracker,
    completer: ScriptedCompleter,
    *,
    reputation: object | None = None,
    max_sources: int | None = None,
    batch_size: int | None = None,
    max_total_sources: int | None = None,
) -> SourceEvaluatorAgent:
    return SourceEvaluatorAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1",
            agent_name="source_evaluator",
            max_entries=20,
        ),
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=0),
        reputation=reputation,
        max_sources=max_sources,
        batch_size=batch_size,
        max_total_sources=max_total_sources,
    )


def _eval_state(findings: list[Finding], **overrides: object) -> ResearchState:
    payload: dict[str, object] = {
        "session_id": "session-1",
        "original_question": "How mature is quantum error correction?",
        "raw_findings": findings,
    }
    payload.update(overrides)
    return ResearchState.model_validate(payload)


def _eval_finding(url: str, sub_topic: str = "Alpha") -> Finding:
    return Finding(
        content="Logical error rates fell below break-even.",
        source_url=url,
        source_title="QEC 2025",
        extracted_at=EVAL_EXTRACTED_AT,
        confidence=0.8,
        related_sub_topic=sub_topic,
    )


def test_build_task_groups_findings_and_seeds_remembered_reputations(
    tracker: Tracker,
) -> None:
    agent = _evaluator(tracker, ScriptedCompleter())
    state = _eval_state(
        [
            _eval_finding("https://example.org/a", "Alpha"),
            _eval_finding("https://other.test/b", "Alpha"),
        ],
        memory_context=MemorySnapshot(
            known_source_reputations={"https://example.org/a": 0.9}
        ),
    )

    task = agent.build_task(state)

    assert isinstance(task, SourceEvaluationTask)
    assert [group.url for group in task.groups] == [
        "https://example.org/a",
        "https://other.test/b",
    ]
    assert task.reputations == {"https://example.org/a": 0.9}
    assert "How mature is quantum error correction?" in task.instruction


@pytest.mark.asyncio
async def test_reputation_lookup_overrides_the_seed_and_is_recorded(
    tracker: Tracker,
) -> None:
    memory = FakeReputationSource(reputations={"https://example.org/a": 0.2})
    agent = _evaluator(tracker, ScriptedCompleter(), reputation=memory)
    state = _eval_state(
        [_eval_finding("https://example.org/a")],
        memory_context=MemorySnapshot(
            known_source_reputations={"https://example.org/a": 0.9}
        ),
    )

    task, errors, hits = await agent.lookup_reputations(agent.build_task(state))

    assert memory.queried == ["https://example.org/a"]
    assert task.reputations == {"https://example.org/a": 0.2}
    assert hits == 1
    assert errors == []


@pytest.mark.asyncio
async def test_a_failed_reputation_lookup_keeps_direct_scoring(
    tracker: Tracker,
) -> None:
    memory = FakeReputationSource(error=RuntimeError("chroma is down"))
    agent = _evaluator(tracker, ScriptedCompleter(), reputation=memory)
    state = _eval_state([_eval_finding("https://example.org/a")])

    task, errors, hits = await agent.lookup_reputations(agent.build_task(state))

    assert task.reputations == {}
    assert hits == 0
    assert errors[0].error_type == "source_evaluator_reputation_unavailable"
    assert errors[0].recoverable is True
    assert errors[0].details["failures"] == 1
    assert "chroma is down" not in str(errors[0].details)


@pytest.mark.asyncio
async def test_scoring_stamps_computed_fields_onto_model_scores(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        outputs=[
            SourceScoresDraft(
                sources=[
                    SourceScoreDraft(
                        url="https://example.org/a",
                        authority_score=0.9,
                        recency_score=0.8,
                        relevance_score=0.9,
                        rationale="Peer-reviewed.",
                    )
                ]
            )
        ]
    )
    agent = _evaluator(tracker, completer)
    state = _eval_state(
        [
            _eval_finding("https://example.org/a", "Alpha"),
            _eval_finding("https://other.test/b", "Alpha"),
        ]
    )
    task, _, _ = await agent.lookup_reputations(agent.build_task(state))

    sources, errors, provider_failed = await agent.score_sources(task)

    assert provider_failed is False
    assert errors == []
    assert [source.url for source in sources] == [
        "https://example.org/a",
        "https://other.test/b",
    ]
    scored = sources[0]
    assert scored.evaluation_status == "scored"
    assert scored.low_confidence is False
    # other.test was never scored by the model, so it still gets a record.
    assert sources[1].evaluation_status == "unscored_missing"
    assert sources[1].low_confidence is False
    assert sources[1].overall_score is None
    assert "returned no score" in sources[1].rationale


@pytest.mark.asyncio
async def test_every_source_still_gets_a_record_when_the_provider_fails(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(outputs=[_output_limit_error()])
    agent = _evaluator(tracker, completer)
    state = _eval_state([_eval_finding("https://example.org/a")])
    task, _, _ = await agent.lookup_reputations(agent.build_task(state))

    sources, errors, provider_failed = await agent.score_sources(task)

    assert provider_failed is True
    assert [source.url for source in sources] == ["https://example.org/a"]
    assert sources[0].evaluation_status == "unscored_provider"
    assert sources[0].low_confidence is False
    assert sources[0].overall_score is None
    assert "could not be reached" in sources[0].rationale
    assert errors[0].error_type == "source_evaluator_scoring_provider_error"
    assert errors[0].recoverable is False
    assert errors[0].details["operation"] == "source_evaluator_scoring"
    provider = errors[0].details["provider_failure"]
    assert provider["kind"] == "output_limit"
    assert provider["configured_max_tokens"] == 4096
    assert provider["request_attempt"] == 1


@pytest.mark.asyncio
async def test_sources_past_the_cap_are_recorded_not_dropped(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        outputs=[
            SourceScoresDraft(
                sources=[
                    SourceScoreDraft(
                        url="https://example.org/a",
                        authority_score=0.9,
                        recency_score=0.9,
                        relevance_score=0.9,
                        rationale="Strong.",
                    )
                ]
            )
        ]
    )
    agent = _evaluator(tracker, completer, max_sources=1)
    state = _eval_state(
        [
            _eval_finding("https://example.org/a"),
            _eval_finding("https://other.test/b"),
        ]
    )
    task, _, _ = await agent.lookup_reputations(agent.build_task(state))

    sources, _, _ = await agent.score_sources(task)

    assert [source.url for source in sources] == [
        "https://example.org/a",
        "https://other.test/b",
    ]
    assert sources[1].evaluation_status == "unscored_cap"
    assert sources[1].low_confidence is False
    assert sources[1].overall_score is None
    assert "past this run's scoring cap" in sources[1].rationale
    # Only the sources under the cap reached the prompt.
    scoring_call = completer.calls[-1]
    assert "https://other.test/b" not in scoring_call[2][1].content


@pytest.mark.asyncio
async def test_scoring_makes_no_provider_call_without_findings(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter()
    agent = _evaluator(tracker, completer)
    task, _, _ = await agent.lookup_reputations(agent.build_task(_eval_state([])))

    sources, errors, provider_failed = await agent.score_sources(task)

    assert sources == []
    assert errors == []
    assert provider_failed is False
    assert completer.calls == []


def test_state_update_carries_scored_sources_and_errors(
    tracker: Tracker,
) -> None:
    agent = _evaluator(tracker, ScriptedCompleter())
    scored = build_scored_source(
        _group(), _draft(), reputation=None
    )
    run = ReActRun(agent_name="source_evaluator", stop_reason="finished")

    update = agent.state_update(EvaluatedSources(sources=[scored]), run)

    assert update["evaluated_sources"] == [scored]
    assert update["errors"] == []


@pytest.mark.asyncio
async def test_a_second_pass_carries_the_sources_of_the_first(
    tracker: Tracker,
) -> None:
    """``evaluated_sources`` replaces, so the update is a whole snapshot.

    An update carrying only this pass's sources would erase every earlier one
    when the state merges it, so the producer merges into the snapshot it
    found on the state it was handed.
    """
    earlier = ScoredSource(
        url="https://earlier.test/z",
        title="Scored in an earlier pass",
        authority_score=0.5,
        recency_score=0.5,
        relevance_score=0.5,
        overall_score=0.45,
        rationale="Scored before this pass began.",
    )
    agent = _evaluator(tracker, ScriptedCompleter(outputs=[_scoring_response()]))
    state = _eval_state(
        [
            _eval_finding("https://example.org/a", "Alpha"),
            _eval_finding("https://other.test/b", "Alpha"),
        ],
        evaluated_sources=[earlier],
    )

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    expected = [
        "https://earlier.test/z",
        "https://example.org/a",
        "https://other.test/b",
    ]
    assert [
        source.url for source in outcome.state_update["evaluated_sources"]
    ] == expected
    merged = merge_research_state(state, outcome.state_update)
    assert [source.url for source in merged.evaluated_sources] == expected


def _scoring_response() -> SourceScoresDraft:
    return SourceScoresDraft(
        sources=[
            SourceScoreDraft(
                url="https://example.org/a",
                authority_score=0.9,
                recency_score=0.8,
                relevance_score=0.9,
                rationale="Peer-reviewed.",
            ),
            SourceScoreDraft(
                url="https://other.test/b",
                authority_score=0.1,
                recency_score=0.1,
                relevance_score=0.1,
                rationale="Anonymous blog.",
            ),
        ]
    )


def test_the_completed_event_reports_the_three_required_counts() -> None:
    strong = build_scored_source(
        _group(), _draft(), reputation=None
    )
    weak = fallback_scored_source(
        _group(url="https://weak.test/b"),
        reason="unscored_missing",
    )

    event = evaluation_completed_event(
        [strong, weak], reputation_hits=1, reputation_failures=0
    )

    assert event.event_type == "source_evaluator.evaluation.completed"
    assert event.source == "agent.source_evaluator"
    assert event.metadata["source_count"] == 2
    assert event.metadata["low_confidence_count"] == 0
    assert event.metadata["average_score"] == pytest.approx(
        average_score([strong, weak])
    )
    assert event.metadata["scored_count"] == 1
    assert event.metadata["unscored_missing_count"] == 1
    assert event.metadata["unscored_cap_count"] == 0
    assert event.metadata["unscored_provider_count"] == 0
    assert event.metadata["unique_source_count"] == 2
    assert event.metadata["reputation_hits"] == 1
    assert event.metadata["reputation_failures"] == 0


def test_the_completed_event_is_finite_for_an_empty_run() -> None:
    event = evaluation_completed_event(
        [], reputation_hits=0, reputation_failures=0
    )

    assert event.metadata["source_count"] == 0
    assert event.metadata["average_score"] is None


@pytest.mark.asyncio
async def test_a_full_run_writes_sources_events_and_span_outputs(
    tracker: Tracker,
) -> None:
    memory = FakeReputationSource(reputations={"https://example.org/a": 0.95})
    completer = ScriptedCompleter(outputs=[_scoring_response()])
    agent = _evaluator(
        tracker, completer,
        reputation=memory,
    )
    state = _eval_state(
        [
            _eval_finding("https://example.org/a", "Alpha"),
            _eval_finding("https://other.test/b", "Alpha"),
        ]
    )

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.agent_name == "source_evaluator"
    assert outcome.react.stop_reason == "finished"
    assert outcome.result is not None
    assert len(outcome.result.sources) == 2
    requested_schemas = [call[0] for call in completer.calls]
    assert requested_schemas == ["SourceScoresDraft"]
    assert "ReActDecision" not in requested_schemas
    # Control: this agent asks no model to select a tool, so it never crosses
    # the native ReAct boundary at all.
    assert completer.react_calls == []

    merged = merge_research_state(state, outcome.state_update)
    assert [source.url for source in merged.evaluated_sources] == [
        "https://example.org/a",
        "https://other.test/b",
    ]
    assert merged.evaluated_sources[1].low_confidence is True

    event_types = [event.event_type for event in outcome.state_update["events"]]
    assert event_types == [
        "source_evaluator.evaluation.started",
        "source_evaluator.evaluation.completed",
    ]
    completed = outcome.state_update["events"][1]
    assert completed.metadata["source_count"] == 2
    assert completed.metadata["low_confidence_count"] == 1
    assert completed.metadata["reputation_hits"] == 1


@pytest.mark.asyncio
async def test_a_run_without_findings_records_a_recoverable_error(
    tracker: Tracker,
) -> None:
    agent = _evaluator(tracker, ScriptedCompleter())
    state = _eval_state([])

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.result is not None
    assert outcome.result.sources == []
    assert outcome.errors[0].error_type == "source_evaluator_no_sources"
    assert outcome.errors[0].recoverable is True
    started = outcome.state_update["events"][0]
    assert started.metadata["finding_count"] == 0
    assert started.metadata["source_count"] == 0


@pytest.mark.asyncio
async def test_a_reputation_failure_is_visible_in_state_and_events(
    tracker: Tracker,
) -> None:
    memory = FakeReputationSource(error=RuntimeError("chroma is down"))
    agent = _evaluator(
        tracker,
        ScriptedCompleter(outputs=[_scoring_response()]),
        reputation=memory,
    )
    state = _eval_state([_eval_finding("https://example.org/a")])

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.result is not None
    assert len(outcome.result.sources) == 1
    types = {error.error_type for error in outcome.errors}
    assert "source_evaluator_reputation_unavailable" in types
    completed = outcome.state_update["events"][-1]
    assert completed.metadata["reputation_failures"] == 1


@pytest.mark.asyncio
async def test_a_score_returned_for_an_example_url_is_ignored(
    tracker: Tracker,
) -> None:
    """Copied example URLs must not become scores for real sources.

    The reply-format examples use reserved ``.example.test`` URLs. A model that
    echoes one back is scored against the real dossier URL list, so the real
    group falls back to ``unscored_missing`` instead of being credited with
    the example's numbers.
    """
    completer = ScriptedCompleter(
        outputs=[
            SourceScoresDraft(
                sources=[
                    SourceScoreDraft(
                        url="https://weak.example.test/post",
                        authority_score=0.1,
                        recency_score=0.5,
                        relevance_score=0.2,
                        rationale="Copied from the weak example.",
                    ),
                    SourceScoreDraft(
                        url="https://strong.example.test/standard",
                        authority_score=0.95,
                        recency_score=0.9,
                        relevance_score=0.95,
                        rationale="Copied from the strong example.",
                    ),
                ]
            )
        ]
    )
    agent = _evaluator(tracker, completer)
    state = _eval_state([_eval_finding("https://real.test/one", "Alpha")])

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.result is not None
    assert [source.url for source in outcome.result.sources] == [
        "https://real.test/one"
    ]
    real = outcome.result.sources[0]
    assert real.low_confidence is False
    assert real.evaluation_status == "unscored_missing"
    assert real.rationale.startswith(FALLBACK_REASONS["unscored_missing"])


# --------------------------------------------------------------------------
# Task 4: the shared read-backed assessment service
# --------------------------------------------------------------------------

READ_SESSION = "session-1"
READ_AT = "2026-09-16T10:00:00+00:00"
LAB_REPORT_URL = "https://lab.example/report"
MIRROR_REPORT_URL = "https://repository.example/mirror/report"
NEWS_URL = "https://news.example/statistic"
REVIEW_URL = "https://review.example/analysis"
COMPANY_URL = "https://acme.example/statement"

# The report names its own publisher, dates, and DOI, the way a title page
# does — which is what makes every one of those an evidenced anchor rather
# than something the model asserted about a page nobody checked.
LAB_REPORT_TEXT = (
    "Grid Storage Outlook. Published by Example Lab on 2026-01-15. "
    "Example Lab measured that 1,200 MW of interconnection capacity was "
    "withheld during 2024. doi:10.1234/grid.2025"
)
FORECAST_TEXT = (
    "Grid Storage Outlook. Published by Example Lab on 2026-01-15. "
    "Observed data cover 2024; the projection runs to 2035. "
    "doi:10.1234/grid.2025"
)
OLD_RULE_TEXT = (
    "Interconnection Rule. Published by Example Lab on 1998-05-14. "
    "The rule took effect on 1998-07-01 and still governs filings. "
    "doi:10.1234/rule.1998"
)
OBSOLETE_TEXT = (
    "Capacity Update. Published by Example Lab on 2026-03-02. "
    "The figures repeat the 2019 survey. doi:10.1234/capacity.2026"
)


def _read(
    url: str = LAB_REPORT_URL,
    *,
    title: str = "Grid Storage Outlook",
    text: str = LAB_REPORT_TEXT,
    request_url: str | None = None,
    reader: str = "web_scraper",
    extraction_complete: bool = True,
    target_ids: tuple[str, ...] = (),
) -> ReadRecord:
    """One successful read, through the shared strict read producer."""
    return build_read_record(
        session_id=READ_SESSION,
        reader=reader,
        requested_url=request_url or url,
        resolved_url=url,
        title=title,
        retrieved_at=READ_AT,
        text=text,
        passages={"chunk-0": text},
        extraction_complete=extraction_complete,
        target_ids=target_ids,
    )


def _scores(*drafts: SourceScoreDraft) -> SourceScoresDraft:
    """The one provider response shape the scoring call returns."""
    return SourceScoresDraft(sources=list(drafts))


async def _assess(
    reads: list[ReadRecord],
    outputs: list[object],
    *,
    existing: list[ScoredSource] | None = None,
    cited: dict[str, list[str]] | None = None,
    reputations: dict[str, float] | None = None,
) -> tuple[list[ScoredSource], ScriptedCompleter]:
    """Run the service once and hand back what it produced next to its calls."""
    completer = ScriptedCompleter(outputs=outputs)
    sources = await assess_new_sources(
        completer,
        reads,
        existing or [],
        cited_sub_topics=cited,
        reputations=reputations,
    )
    return sources, completer


def _claim(value: str, quote: str) -> TemporalClaim:
    """One model-proposed date with the document's own words for it."""
    return TemporalClaim(value=value, quote=quote)


def _lab_draft(**overrides: object) -> SourceScoreDraft:
    fields: dict[str, object] = {
        "url": LAB_REPORT_URL,
        "authority": 0.9,
        "recency": 0.8,
        "relevance": 0.95,
        "rationale": "A dated primary report from the laboratory itself.",
        "source_role": "original_report",
        "transport_relation": "original",
        "self_interest": "none",
        "issuer": "Example Lab",
        "doi": "10.1234/grid.2025",
        "year": "2025",
    }
    fields.update(overrides)
    return _draft(**fields)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_the_original_report_becomes_a_read_backed_origin() -> None:
    read = _read()

    sources, completer = await _assess([read], [_scores(_lab_draft())])

    assert len(completer.calls) == 1
    assert completer.calls[0][0] == "SourceScoresDraft"
    source = sources[0]
    assert source.evaluation_status == "scored"
    assert source.source_role == "original_report"
    assert source.transport_relation == "original"
    assert source.self_interest == "none"
    assert source.serving_host == "lab.example"
    assert source.publisher_id == "example lab"
    assert source.work_id == "doi:10.1234/grid.2025"
    assert source.assessment_revision.startswith("assess-")
    assert source_origin_id(source) == "work:doi:10.1234/grid.2025"
    assert source.cited_sub_topics == []


@pytest.mark.asyncio
async def test_the_official_mirror_is_usable_but_adds_no_origin() -> None:
    """A mirror is evidence; it is not a second publisher or a second work."""
    original = _read(LAB_REPORT_URL)
    mirror = _read(MIRROR_REPORT_URL)
    drafts = [
        _lab_draft(),
        _lab_draft(url=MIRROR_REPORT_URL, transport_relation="mirror"),
    ]

    sources, _ = await _assess([original, mirror], [_scores(*drafts)])

    by_url = {source.url: source for source in sources}
    first = by_url[LAB_REPORT_URL]
    copied = by_url[MIRROR_REPORT_URL]
    assert first.transport_relation == "original"
    assert copied.transport_relation == "mirror"
    assert copied.publisher_id == first.publisher_id
    assert copied.work_id == first.work_id
    # Still usable: it carries the same assessment as the document it serves.
    assert copied.evaluation_status == "scored"
    assert copied.overall_score == pytest.approx(first.overall_score)
    # But it contributes exactly the origin its original already contributes.
    assert source_origin_id(copied) == source_origin_id(first)


# A report with no DOI and no report number: the only identity a read can
# evidence is its complete content, and two copies of one report that were
# re-typeset do not share that.
PLAIN_REPORT_TEXT = (
    "Grid Storage Outlook. Published by Example Lab on 2026-01-15. "
    "Example Lab measured that 1,200 MW of interconnection capacity was "
    "withheld during 2024."
)


@pytest.mark.asyncio
async def test_a_re_typeset_mirror_adds_no_origin() -> None:
    """A copy whose wrapper differs is not a second origin, and not an origin.

    Two byte-different copies of one report share no content hash, so a bare
    hash can never be the origin they are compared by. A copy's publisher is
    no better: the repository's own name is its own claim, not the origin of
    the figure it repeats (§2.2 rule 4), so a hash-only mirror contributes no
    origin at all. The readable original keeps its publisher origin.
    """
    original = _read(LAB_REPORT_URL, text=PLAIN_REPORT_TEXT)
    retypeset = _read(
        MIRROR_REPORT_URL,
        text=(
            PLAIN_REPORT_TEXT
            + " Downloaded from the institutional repository."
        ),
    )
    drafts = [
        _lab_draft(doi=""),
        _lab_draft(
            url=MIRROR_REPORT_URL, doi="", transport_relation="mirror"
        ),
    ]

    sources, _ = await _assess([original, retypeset], [_scores(*drafts)])

    by_url = {source.url: source for source in sources}
    first = by_url[LAB_REPORT_URL]
    copied = by_url[MIRROR_REPORT_URL]
    # The scenario really is the byte-different one: the hashes disagree.
    assert original.content_sha256 != retypeset.content_sha256
    assert first.work_id != copied.work_id
    assert first.work_id is not None and first.work_id.startswith("sha256:")
    assert copied.work_id.startswith("sha256:")
    assert copied.publisher_id == first.publisher_id == "example lab"
    # One origin, not two: the readable original's, and the copy's none.
    assert source_origin_id(first) == "publisher:example lab"
    assert source_origin_id(copied) is None


@pytest.mark.asyncio
async def test_different_publishers_keep_distinct_origins() -> None:
    """Folding the publisher in must not collapse everyone onto one origin.

    Two genuinely independent documents from two publishers are exactly the
    pair a corroboration check exists to allow.
    """
    first_read = _read(LAB_REPORT_URL, text=PLAIN_REPORT_TEXT)
    second_read = _read(
        REVIEW_URL,
        title="We measured the queue ourselves",
        text=(
            "We measured the queue ourselves. Published by Review Weekly on "
            "2026-02-10. This outlet ran its own measurement of 1,180 MW "
            "during 2024."
        ),
    )
    drafts = [
        _lab_draft(doi=""),
        _draft(
            url=REVIEW_URL,
            source_role="independent_research",
            transport_relation="original",
            issuer="Review Weekly",
            rationale="Original measurement published by the outlet.",
        ),
    ]

    sources, _ = await _assess([first_read, second_read], [_scores(*drafts)])

    origins = [source_origin_id(source) for source in sources]
    assert origins == ["publisher:example lab", "publisher:review weekly"]
    assert origins[0] != origins[1]


@pytest.mark.asyncio
async def test_a_derivative_news_statistic_is_not_an_origin() -> None:
    """Repeating one report is not independent evidence for what it reports."""
    read = _read(
        NEWS_URL,
        title="Grid capacity withheld, report says",
        text=(
            "Grid capacity withheld, report says. Published by News Daily on "
            "2026-02-02. The outlet repeats Example Lab's figure of 1,200 MW "
            "for 2024."
        ),
    )
    draft = _draft(
        url=NEWS_URL,
        authority=0.6,
        relevance=0.7,
        rationale="A news report repeating the laboratory's figure.",
        source_role="derivative",
        transport_relation="original",
        issuer="News Daily",
    )

    sources, _ = await _assess([read], [_scores(draft)])

    source = sources[0]
    assert source.source_role == "derivative"
    assert source.publisher_id == "news daily"
    assert source.evaluation_status == "scored"
    # It is support for "News Daily reported this", never an independent origin
    # for the measurement it repeats.
    assert source_origin_id(source) is None


@pytest.mark.asyncio
async def test_an_independently_researched_article_is_an_origin() -> None:
    read = _read(
        REVIEW_URL,
        title="We measured the queue ourselves",
        text=(
            "We measured the queue ourselves. Published by Review Weekly on "
            "2026-02-10. This outlet ran its own measurement of 1,180 MW "
            "during 2024."
        ),
    )
    draft = _draft(
        url=REVIEW_URL,
        authority=0.75,
        relevance=0.9,
        rationale="Original measurement published by the outlet.",
        source_role="independent_research",
        transport_relation="original",
        issuer="Review Weekly",
    )

    sources, _ = await _assess([read], [_scores(draft)])

    source = sources[0]
    assert source.source_role == "independent_research"
    # Its identity rests on a bare content hash, so the origin it contributes
    # is its publisher's: a hash names bytes, and a re-typeset copy of it
    # would hash differently while still being the same origin.
    assert source_origin_id(source) == "publisher:review weekly"


@pytest.mark.asyncio
async def test_a_company_statement_stays_attributed_and_self_interested() -> None:
    """Section 2.1: it establishes what the company said, and nothing more."""
    read = _read(
        COMPANY_URL,
        title="Acme statement on its own recall",
        text=(
            "Acme statement on its own recall. Published by Acme on "
            "2026-02-11. Acme said its own packs were safe."
        ),
    )
    draft = _draft(
        url=COMPANY_URL,
        authority=0.5,
        relevance=0.8,
        rationale="The manufacturer's own statement about its product.",
        source_role="company_statement",
        transport_relation="original",
        self_interest="evidenced",
        issuer="Acme",
    )

    sources, _ = await _assess([read], [_scores(draft)])

    source = sources[0]
    assert source.publisher_id == "acme"
    assert source.source_role == "company_statement"
    # The typed self-interest constraint survives; the score never absorbs it.
    assert source.self_interest == "evidenced"


@pytest.mark.asyncio
async def test_a_company_statement_cannot_lose_its_self_interest() -> None:
    read = _read(
        COMPANY_URL,
        title="Acme statement on its own recall",
        text=(
            "Acme statement on its own recall. Published by Acme on "
            "2026-02-11. Acme said its own packs were safe."
        ),
    )
    draft = _draft(
        url=COMPANY_URL,
        rationale="The manufacturer's own statement about its product.",
        source_role="company_statement",
        issuer="Acme",
        self_interest="none",
    )

    sources, _ = await _assess([read], [_scores(draft)])

    assert sources[0].self_interest == "evidenced"


@pytest.mark.asyncio
async def test_a_mixed_role_article_cannot_claim_a_clean_origin() -> None:
    """Derivative statistics and original interviews in one article.

    The article really is partly original work, which is exactly why the
    source-level label may not decide it: it repeats someone else's figure
    *and* adds its own reporting, so only the claim-specific assessment can
    say which part supports what.
    """
    read = _read(
        REVIEW_URL,
        title="Capacity withheld, and what operators say",
        text=(
            "Capacity withheld, and what operators say. Published by Review "
            "Weekly on 2026-02-10. It repeats Example Lab's 1,200 MW figure "
            "for 2024 and adds its own interviews with three operators."
        ),
    )
    draft = _draft(
        url=REVIEW_URL,
        authority=0.8,
        relevance=0.85,
        rationale="Parts repeat the laboratory's figure; parts are original.",
        source_role="mixed",
        transport_relation="original",
        issuer="Review Weekly",
    )

    sources, _ = await _assess([read], [_scores(draft)])

    source = sources[0]
    assert source.source_role == "mixed"
    assert source_origin_id(source) is None
    assert source.evaluation_status == "scored"


@pytest.mark.asyncio
async def test_an_unknown_issuer_records_no_origin() -> None:
    """Unknown identity cannot establish independence."""
    read = _read(
        "https://anonymous.test/post",
        title="A post about capacity",
        text="A post about capacity. 1,200 MW was withheld during 2024.",
    )
    draft = _draft(
        url="https://anonymous.test/post",
        authority=0.9,
        relevance=0.9,
        rationale="The post claims a laboratory produced the figure.",
        source_role="original_report",
        issuer="Example Lab",
        doi="10.1234/grid.2025",
        year="2025",
    )

    sources, _ = await _assess([read], [_scores(draft)])

    source = sources[0]
    # The proposed issuer and DOI are nowhere in the read, so neither is taken.
    assert source.source_role == "unknown"
    assert source.work_id == f"sha256:{read.content_sha256}"
    assert source.publisher_id == "anonymous.test"
    assert source_origin_id(source) is None


@pytest.mark.asyncio
async def test_an_unsupported_transport_claim_is_downgraded() -> None:
    """A mirror with no evidenced issuer inherits nothing to mirror."""
    read = _read(
        "https://anonymous.test/post",
        title="A post about capacity",
        text="A post about capacity. 1,200 MW was withheld during 2024.",
    )
    draft = _draft(
        url="https://anonymous.test/post",
        transport_relation="mirror",
        source_role="original_report",
        rationale="Claims to be a mirror copy.",
    )

    sources, _ = await _assess([read], [_scores(draft)])

    assert sources[0].transport_relation == "unknown"
    assert sources[0].source_role == "unknown"


@pytest.mark.asyncio
async def test_publication_date_observation_period_and_horizon_stay_separate() -> None:
    """Section 2.3: three different dates, and none may stand for another."""
    read = _read(text=FORECAST_TEXT)
    draft = _lab_draft(
        publication_date=_claim(
            "2026-01-15", "Published by Example Lab on 2026-01-15."
        ),
        data_period=_claim("2024", "Observed data cover 2024;"),
        forecast_horizon=_claim("2035", "the projection runs to 2035."),
        freshness_status="projection",
    )

    sources, _ = await _assess([read], [_scores(draft)])

    temporal = sources[0].temporal
    assert temporal.publication_date == "2026-01-15"
    assert temporal.data_period == "2024"
    assert temporal.forecast_horizon == "2035"
    assert temporal.status == "projection"
    assert len(
        {
            temporal.publication_date,
            temporal.data_period,
            temporal.forecast_horizon,
        }
    ) == 3


@pytest.mark.asyncio
async def test_an_old_official_rule_that_still_applies_is_not_stale() -> None:
    """An old effective date is not a stale source."""
    read = _read(
        "https://lab.example/rule",
        title="Interconnection Rule",
        text=OLD_RULE_TEXT,
    )
    draft = _draft(
        url="https://lab.example/rule",
        authority=0.9,
        recency=0.3,
        relevance=0.9,
        rationale="The rule still governs filings.",
        source_role="original_report",
        transport_relation="original",
        issuer="Example Lab",
        publication_date=_claim(
            "1998-05-14", "Published by Example Lab on 1998-05-14."
        ),
        effective_date=_claim(
            "1998-07-01", "The rule took effect on 1998-07-01"
        ),
        freshness_status="effective",
    )

    sources, _ = await _assess([read], [_scores(draft)])

    temporal = sources[0].temporal
    assert temporal.status == "effective"
    assert temporal.publication_date == "1998-05-14"
    assert temporal.effective_date == "1998-07-01"
    assert temporal.data_period is None
    assert sources[0].evaluation_status == "scored"


@pytest.mark.asyncio
async def test_a_new_article_repeating_obsolete_data_is_not_current() -> None:
    """A 2026 publication date does not make 2019 figures current."""
    read = _read(
        "https://lab.example/capacity",
        title="Capacity Update",
        text=OBSOLETE_TEXT,
    )
    draft = _draft(
        url="https://lab.example/capacity",
        authority=0.8,
        recency=0.9,
        relevance=0.9,
        rationale="Newly published, but the figures are from 2019.",
        source_role="derivative",
        transport_relation="original",
        issuer="Example Lab",
        publication_date=_claim(
            "2026-03-02", "Published by Example Lab on 2026-03-02."
        ),
        data_period=_claim("2019", "The figures repeat the 2019 survey."),
        freshness_status="stale_data",
    )

    sources, _ = await _assess([read], [_scores(draft)])

    temporal = sources[0].temporal
    assert temporal.publication_date == "2026-03-02"
    assert temporal.data_period == "2019"
    assert temporal.status == "stale_data"


@pytest.mark.asyncio
async def test_a_date_whose_quote_is_not_in_the_read_is_not_admitted() -> None:
    """The scoring call proposes; only the read's own words admit a date.

    The model returns a February 2026 date the document never states, with a
    plausible-looking quote nobody wrote. It is not a date this source carries,
    and no freshness judgement may rest on it — the status goes back to
    unknown rather than describing a revision the model imagined.
    """
    read = _read()
    draft = _lab_draft(
        publication_date=_claim(
            "2026-02-01", "Published by Example Lab on 2026-02-01."
        ),
        data_period=_claim("2019", "Observed data cover 2019."),
        freshness_status="current",
    )

    sources, _ = await _assess([read], [_scores(draft)])

    temporal = sources[0].temporal
    assert temporal.publication_date is None
    assert temporal.data_period is None
    assert temporal.status == "unknown"
    # The scoring judgement itself is still recorded: it is the missing
    # evidence that decides the dates, not the model's opinion of them.
    assert sources[0].evaluation_status == "scored"


@pytest.mark.asyncio
async def test_an_unfounded_freshness_status_is_not_recorded() -> None:
    """A status the read cannot support is recorded as unknown, not guessed."""
    read = _read()
    draft = _lab_draft(
        freshness_status="projection",
        forecast_horizon=_claim("2035", "the projection runs to 2035."),
    )

    sources, _ = await _assess([read], [_scores(draft)])

    assert sources[0].temporal.status == "unknown"
    assert sources[0].temporal.forecast_horizon is None


@pytest.mark.asyncio
async def test_a_current_authority_that_does_not_answer_the_target() -> None:
    """A high authority score cannot wash relevance, dates, or role away."""
    read = _read(
        "https://lab.example/other",
        title="Unrelated standards note",
        text=(
            "Unrelated standards note. Published by Example Lab on "
            "2026-01-15. doi:10.1234/note.2025"
        ),
    )
    draft = _draft(
        url="https://lab.example/other",
        authority=0.99,
        recency=0.99,
        relevance=0.05,
        rationale="A current, authoritative document about something else.",
        source_role="original_report",
        transport_relation="original",
        issuer="Example Lab",
        publication_date=_claim(
            "2026-01-15", "Published by Example Lab on 2026-01-15."
        ),
        freshness_status="current",
        methods_score=0.95,
    )

    sources, _ = await _assess(
        [read],
        [_scores(draft)],
        cited={read.resolved_url: ["Grid storage"]},
    )

    source = sources[0]
    assert source.authority_score == pytest.approx(0.99)
    assert source.relevance_score == pytest.approx(0.05)
    # No single dimension carries the source. Authority and recency together
    # are worth at most 0.60 of the combination, so an off-target source
    # cannot reach a high overall however authoritative it is, and the
    # relevance that would have made it useful is worth more than 0.30 here.
    assert source.overall_score < source.authority_score
    assert source.overall_score < 0.65
    assert overall_score(
        authority=0.99, recency=0.99, relevance=0.99
    ) - source.overall_score > 0.3
    assert source.cited_sub_topics == ["Grid storage"]
    assert source.temporal.status == "current"
    # The methods judgement is recorded beside the score, not blended into it.
    assert source.methods_score == pytest.approx(0.95)


@pytest.mark.asyncio
async def test_a_high_authority_score_cannot_override_unsupported_content() -> None:
    """Quality and dependence are different fields, and neither substitutes."""
    read = _read(
        REVIEW_URL,
        title="Capacity withheld, and what operators say",
        text=(
            "Capacity withheld, and what operators say. Published by Review "
            "Weekly on 2026-02-10. It repeats Example Lab's 1,200 MW figure "
            "for 2024."
        ),
    )
    draft = _draft(
        url=REVIEW_URL,
        authority=0.99,
        recency=0.99,
        relevance=0.99,
        rationale="Authoritative, current, and on topic.",
        source_role="derivative",
        transport_relation="original",
        issuer="Review Weekly",
    )

    sources, _ = await _assess(
        [read],
        [_scores(draft)],
        cited={read.resolved_url: ["Grid storage"]},
    )

    source = sources[0]
    assert source.overall_score > 0.9
    # The same record still cannot contribute an origin: the independence
    # judgement is not a quality threshold that a high score can buy.
    assert source_origin_id(source) is None
    assert source.source_role == "derivative"
    assert source.cited_sub_topics == ["Grid storage"]


def test_role_and_transport_never_change_the_overall_score() -> None:
    """Labels describe the relation; they are not quality dimensions.

    Driven through a real read-backed dossier so both records carry genuinely
    different labels — an evidenced issuer is what lets a role or a transport
    relation be recorded at all, so a variant without one would normalise both
    sides to ``unknown`` and assert nothing.
    """
    read = _read()
    dossier = build_read_dossiers([read])[0]
    group = _group(url=read.resolved_url)
    plain = build_scored_source(
        group,
        _lab_draft(url=read.resolved_url),
        reputation=None,
        dossier=dossier,
    )
    labelled = build_scored_source(
        group,
        _lab_draft(
            url=read.resolved_url,
            source_role="derivative",
            transport_relation="mirror",
            self_interest="evidenced",
        ),
        reputation=None,
        dossier=dossier,
    )

    # The differential really reached the records.
    assert plain.source_role == "original_report"
    assert plain.transport_relation == "original"
    assert plain.self_interest == "none"
    assert labelled.source_role == "derivative"
    assert labelled.transport_relation == "mirror"
    assert labelled.self_interest == "evidenced"
    # The dependence judgement moved: one is an origin, the other is not.
    assert source_origin_id(plain) is not None
    assert source_origin_id(labelled) is None

    # And none of it moved a single number.
    assert plain.overall_score == pytest.approx(labelled.overall_score)
    assert plain.authority_score == pytest.approx(labelled.authority_score)
    assert plain.recency_score == pytest.approx(labelled.recency_score)
    assert plain.relevance_score == pytest.approx(labelled.relevance_score)
    assert plain.low_confidence is False
    assert labelled.low_confidence is False


def test_the_assessment_revision_is_content_metadata_and_time() -> None:
    """The reuse key names all three: no component may be dropped."""
    base = compute_assessment_revision(
        content_sha256="a" * 64,
        extraction_complete=True,
        metadata_fingerprint="m-1",
        temporal_fingerprint="t-1",
    )

    assert base.startswith("assess-")
    assert base == compute_assessment_revision(
        content_sha256="a" * 64,
        extraction_complete=True,
        metadata_fingerprint="m-1",
        temporal_fingerprint="t-1",
    )
    assert base != compute_assessment_revision(
        content_sha256="b" * 64,
        extraction_complete=True,
        metadata_fingerprint="m-1",
        temporal_fingerprint="t-1",
    )
    assert base != compute_assessment_revision(
        content_sha256="a" * 64,
        extraction_complete=True,
        metadata_fingerprint="m-2",
        temporal_fingerprint="t-1",
    )
    assert base != compute_assessment_revision(
        content_sha256="a" * 64,
        extraction_complete=True,
        metadata_fingerprint="m-1",
        temporal_fingerprint="t-2",
    )


@pytest.mark.asyncio
async def test_an_unchanged_read_reuses_its_assessment_without_a_model_call() -> None:
    read = _read()

    first, completer = await _assess([read], [_scores(_lab_draft())])
    assert len(completer.calls) == 1

    # An empty script: any second model call raises instead of being counted.
    second = await assess_new_sources(ScriptedCompleter(outputs=[]), [read], first)

    assert [source.model_dump() for source in second] == [
        source.model_dump() for source in first
    ]


@pytest.mark.asyncio
async def test_changed_content_at_one_url_is_reassessed() -> None:
    read = _read()
    first, _ = await _assess([read], [_scores(_lab_draft())])
    revised = _read(text=LAB_REPORT_TEXT + " Revised in the 2026 edition.")
    assert read.read_id != revised.read_id

    second = await assess_new_sources(
        ScriptedCompleter(outputs=[_scores(_lab_draft(authority=0.4))]),
        [revised],
        first,
    )

    assert len(second) == 1
    assert second[0].authority_score == pytest.approx(0.4)
    assert second[0].assessment_revision != first[0].assessment_revision


@pytest.mark.asyncio
async def test_a_changed_title_at_one_url_is_reassessed() -> None:
    read = _read()
    first, _ = await _assess([read], [_scores(_lab_draft())])
    retitled = _read(title="Grid Storage Outlook (revised)")
    assert retitled.content_sha256 == read.content_sha256

    second = await assess_new_sources(
        ScriptedCompleter(outputs=[_scores(_lab_draft(authority=0.3))]),
        [retitled],
        first,
    )

    assert second[0].authority_score == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_changed_dates_at_one_url_are_reassessed() -> None:
    read = _read()
    first, _ = await _assess([read], [_scores(_lab_draft())])
    redated = _read(
        text=LAB_REPORT_TEXT.replace("2024", "2019").replace(
            "doi:10.1234/grid.2025", "doi:10.1234/grid.2019"
        )
    )

    assert read_dated_tokens(read) != read_dated_tokens(redated)
    second = await assess_new_sources(
        ScriptedCompleter(outputs=[_scores(_lab_draft(authority=0.2))]),
        [redated],
        first,
    )

    assert second[0].authority_score == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_a_provider_failure_keeps_identity_and_an_unscored_status() -> None:
    read = _read()
    completer = ScriptedCompleter(outputs=[_output_limit_error()])

    sources = await assess_new_sources(completer, [read], [])

    source = sources[0]
    assert source.evaluation_status == "unscored_provider"
    assert source.overall_score is None
    assert source.low_confidence is False
    # Deterministic identity survives the outage; no score is invented.
    assert source.serving_host == "lab.example"
    assert source.work_id == f"sha256:{read.content_sha256}"
    assert source.assessment_revision.startswith("assess-")
    assert source.assessment_revision == read_assessment_revision(read)
    # And an unscored source can never be half of an independent pair.
    assert source_origin_id(source) is None


@pytest.mark.asyncio
async def test_a_changed_read_that_reassesses_unscored_drops_the_old_score() -> None:
    """A score about content that no longer exists must not survive.

    The prior assessment was computed from the old body. The new read is a
    different document, and this pass could not score it, so keeping 0.87
    would credit content the model never saw with a judgement about content
    that is gone.
    """
    read = _read()
    first, _ = await _assess([read], [_scores(_lab_draft())])
    prior = first[0]
    assert prior.evaluation_status == "scored"
    assert prior.overall_score is not None

    changed = _read(
        text=LAB_REPORT_TEXT + " Revised: the 2024 figure was restated."
    )
    assert changed.content_sha256 != read.content_sha256
    sources = await assess_new_sources(
        ScriptedCompleter(outputs=[_output_limit_error()]),
        [changed],
        first,
    )

    source = sources[0]
    assert source.assessment_revision != prior.assessment_revision
    assert source.evaluation_status == "unscored_provider"
    assert source.overall_score is None
    assert source.authority_score is None
    # The stale score is gone, and the record still names what it is about.
    assert source.work_id == f"sha256:{changed.content_sha256}"
    assert source_origin_id(source) is None


@pytest.mark.asyncio
async def test_a_missing_model_row_is_unscored_rather_than_invented() -> None:
    read = _read()
    completer = ScriptedCompleter(outputs=[SourceScoresDraft(sources=[])])

    sources = await assess_new_sources(completer, [read], [])

    assert sources[0].evaluation_status == "unscored_missing"
    assert sources[0].overall_score is None
    assert source_origin_id(sources[0]) is None


@pytest.mark.asyncio
async def test_a_partial_read_is_assessed_but_never_an_identity_edge() -> None:
    incomplete = _read(
        "https://lab.example/report.pdf",
        reader="document_reader",
        extraction_complete=False,
    )
    draft = _draft(
        url=incomplete.resolved_url,
        source_role="original_report",
        issuer="Example Lab",
        rationale="Only part of the document could be extracted.",
    )

    sources, _ = await _assess([incomplete], [_scores(draft)])

    source = sources[0]
    assert source.evaluation_status == "scored"
    assert source.overall_score is not None
    # Admissible for the pages it read, never an identity/equality edge: the
    # pages it never saw could say anything, including that this is another
    # document or another publisher's copy of one.
    assert source.serving_host == "lab.example"
    assert source.publisher_id is None
    assert source.work_id is None
    assert source_origin_id(source) is None


@pytest.mark.asyncio
async def test_the_snapshot_stays_cumulative_across_assessments() -> None:
    earlier = ScoredSource(
        url="https://earlier.test/z",
        title="Assessed before this run",
        authority_score=0.5,
        recency_score=0.5,
        relevance_score=0.5,
        overall_score=0.45,
        rationale="Recorded by an earlier pass.",
    )
    read = _read()

    sources, _ = await _assess([read], [_scores(_lab_draft())], existing=[earlier])

    assert [source.url for source in sources] == [
        "https://earlier.test/z",
        LAB_REPORT_URL,
    ]
    assert sources[0] == earlier


@pytest.mark.asyncio
async def test_reassessment_replaces_only_the_source_whose_read_changed() -> None:
    read = _read()
    first, _ = await _assess([read], [_scores(_lab_draft())])
    other = build_scored_source(
        _group(url="https://other.test/b"),
        _draft(url="https://other.test/b"),
        reputation=None,
    )

    second = await assess_new_sources(
        ScriptedCompleter(outputs=[_scores(_lab_draft(authority=0.25))]),
        [_read(text=LAB_REPORT_TEXT + " Extra paragraph.")],
        [*first, other],
    )

    by_url = {source.url: source for source in second}
    assert by_url["https://other.test/b"] == other
    assert by_url[LAB_REPORT_URL].authority_score == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_scoring_a_read_never_mutates_it_or_its_evidence() -> None:
    from deep_research.agents.evidence import build_evidence_unit

    read = _read(target_ids=("target-1",))
    unit = build_evidence_unit(
        read=read,
        locator="chunk-0",
        excerpt=read.passages["chunk-0"],
        origin="researcher",
        target_ids=["target-1"],
    )
    before_read = read.model_dump()
    before_unit = unit.model_dump()

    sources, _ = await _assess(
        [read],
        [_scores(_lab_draft())],
        cited={read.resolved_url: ["Grid storage"]},
    )

    assert read.model_dump() == before_read
    assert unit.model_dump() == before_unit
    assert sources[0].target_ids == ["target-1"]
    assert "evidence_id" not in ScoredSource.model_fields


@pytest.mark.asyncio
async def test_the_total_cap_records_unscored_identity_not_a_dropped_source() -> None:
    first = _read(LAB_REPORT_URL)
    second = _read(MIRROR_REPORT_URL)

    completer = ScriptedCompleter(outputs=[_scores(_lab_draft())])
    sources = await assess_new_sources(
        completer,
        [first, second],
        [],
        max_total_sources=1,
    )

    assert [source.evaluation_status for source in sources] == [
        "scored",
        "unscored_cap",
    ]
    assert sources[1].overall_score is None
    # The capped copy is byte-identical to the scored DOI original, so its
    # identity is that work — resolved across both reads, not from its own.
    assert first.content_sha256 == second.content_sha256
    assert sources[1].work_id == sources[0].work_id == "doi:10.1234/grid.2025"
    assert source_origin_id(sources[1]) is None


# --------------------------------------------------------------------------
# Canonical identity: what the Source Evaluator resolves is what the Fact
# Checker pairs on. Every test below goes through the real producers and the
# real pair test — no hand-built eligibility.
# --------------------------------------------------------------------------

IDENTITY_CLAIM = (
    "Example Lab measured that 1,200 MW of interconnection capacity was "
    "withheld during 2024."
)
MIRROR_HOST_URL = "https://mirror.example/grid-outlook"
PDF_REPORT_URL = "https://lab.example/report.pdf"
STORY_URL = "https://news.example/queue-story"
REPORT_WORK = "doi:10.1234/grid.2025"
REPORT_ORIGIN = f"work:{REPORT_WORK}"
DATASET_DOI = "10.5555/queue.2024"

# The same report, re-typeset by a repository on another host: the issuer is
# still stated, the DOI is not, and the bytes differ.
RETYPESET_MIRROR_TEXT = (
    "Grid Storage Outlook (repository copy). Published by Example Lab on "
    "2026-01-15. The laboratory measured that 1,200 MW of interconnection "
    "capacity was withheld during 2024."
)
INDEPENDENT_TEXT = (
    "We measured the queue ourselves. Published by Review Weekly on "
    "2026-02-10. This outlet ran its own measurement of 1,180 MW during 2024."
)
STORY_TEXT = (
    "Queue backlog, by the numbers. Published by News Daily on 2026-02-02. "
    "Our analysis of the Example Lab report (doi:10.1234/grid.2025) finds "
    "1,200 MW was withheld during 2024."
)


def _review_read() -> ReadRecord:
    return _read(
        REVIEW_URL,
        title="We measured the queue ourselves",
        text=INDEPENDENT_TEXT,
    )


def _review_draft(**overrides: object) -> SourceScoreDraft:
    fields: dict[str, object] = {
        "url": REVIEW_URL,
        "source_role": "independent_research",
        "transport_relation": "original",
        "issuer": "Review Weekly",
        "rationale": "Original measurement published by the outlet.",
    }
    fields.update(overrides)
    return _draft(**fields)  # type: ignore[arg-type]


def _by_url(sources: list[ScoredSource]) -> dict[str, ScoredSource]:
    return {normalize_source_url(source.url): source for source in sources}


def _unit_for(read: ReadRecord) -> EvidenceUnit:
    return build_evidence_unit(
        read=read,
        locator="chunk-0",
        excerpt=read.passages["chunk-0"],
        origin="researcher",
        target_ids=["target-1"],
    )


def _identity_packet(
    reads: list[ReadRecord], sources: list[ScoredSource]
) -> AdjudicationPacket:
    """The claim's packet over these reads, with the identity the checker resolves.

    Eligibility comes from ``claim_eligibility`` over the read and the source
    the Source Evaluator produced for it — exactly what the Fact Checker hands
    the pair test.
    """
    by_url = _by_url(sources)
    units = [_unit_for(read) for read in reads]
    return build_adjudication_packet(
        ClaimDraft(text=IDENTITY_CLAIM, source_urls=[reads[0].resolved_url]),
        units,
        eligibility={
            unit.evidence_id: claim_eligibility(
                unit,
                read=read,
                source=by_url[normalize_source_url(read.resolved_url)],
                assessment=None,
            )
            for unit, read in zip(units, reads, strict=True)
        },
    )


def _may_pair(
    left: ReadRecord, right: ReadRecord, sources: list[ScoredSource]
) -> bool:
    """Whether the local pair test lets these two reads corroborate a claim.

    This is the test that decides, before any adjudication, whether a packet
    already carries a qualifying pair — so a ``True`` here is a pair the model
    would be allowed to certify.
    """
    return _packet_has_pair(_identity_packet([left, right], sources))


async def _run_producer(
    producer: str,
    reads: list[ReadRecord],
    drafts: list[SourceScoreDraft],
    tracker: Tracker,
) -> list[ScoredSource]:
    """Assess ``reads`` through the shared service or through the agent."""
    if producer == "service":
        sources, _ = await _assess(reads, [_scores(*drafts)])
        return sources
    agent = _evaluator(tracker, ScriptedCompleter(outputs=[_scores(*drafts)]))
    state = _eval_state(
        [_eval_finding(read.resolved_url) for read in reads],
        read_records={read.read_id: read for read in reads},
        quality_contract_version=QUALITY_CONTRACT_VERSION,
    )
    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)
    assert outcome.result is not None
    # One resolved snapshot: what the agent returns is what it persists.
    assert outcome.state_update["evaluated_sources"] == outcome.result.sources
    return list(outcome.result.sources)


@pytest.mark.asyncio
@pytest.mark.parametrize("producer", ["service", "agent"])
async def test_a_doi_original_and_its_identical_doi_less_mirror_are_one_work(
    producer: str, tracker: Tracker
) -> None:
    """Section 2.2 rule 2: identity resolves across records, not per URL."""
    original = _read(LAB_REPORT_URL)
    mirror = _read(MIRROR_HOST_URL)
    assert original.content_sha256 == mirror.content_sha256

    sources = await _run_producer(
        producer,
        [original, mirror],
        [
            _lab_draft(),
            _lab_draft(url=MIRROR_HOST_URL, doi="", transport_relation="mirror"),
        ],
        tracker,
    )

    by_url = _by_url(sources)
    assert by_url[LAB_REPORT_URL].work_id == REPORT_WORK
    assert by_url[MIRROR_HOST_URL].work_id == REPORT_WORK
    assert not _may_pair(original, mirror, sources)


@pytest.mark.asyncio
async def test_a_re_typeset_mirror_on_another_host_never_corroborates_its_original() -> (
    None
):
    """The reproduced false pair: two hosts, one publisher, one report.

    The Source Evaluator validated "Example Lab" on both copies. A pair test
    that re-derived the publisher from the serving host would see two
    publishers, two works, and two origins, and certify one report as two
    independent accounts.
    """
    original = _read(LAB_REPORT_URL)
    mirror = _read(
        MIRROR_HOST_URL,
        title="Grid Storage Outlook (repository copy)",
        text=RETYPESET_MIRROR_TEXT,
    )

    sources, _ = await _assess(
        [original, mirror],
        [
            _scores(
                _lab_draft(),
                _lab_draft(
                    url=MIRROR_HOST_URL, doi="", transport_relation="mirror"
                ),
            )
        ],
    )

    by_url = _by_url(sources)
    assert by_url[MIRROR_HOST_URL].publisher_id == "example lab"
    assert by_url[LAB_REPORT_URL].publisher_id == "example lab"
    assert not _may_pair(original, mirror, sources)


@pytest.mark.asyncio
async def test_a_pdf_and_html_of_one_doi_are_one_known_work_that_can_still_pair() -> (
    None
):
    """Two renderings of one DOI hash differently and are still one work.

    Distinct bytes under one DOI are the PDF and the HTML of one report, not
    a conflict — and treating them as unresolved would stop the original from
    ever pairing with a genuinely independent measurement.
    """
    html = _read(LAB_REPORT_URL)
    pdf = _read(
        PDF_REPORT_URL,
        reader="document_reader",
        text=LAB_REPORT_TEXT + " Appendix: interconnection queue by region.",
    )
    independent = _review_read()
    assert html.content_sha256 != pdf.content_sha256

    sources, _ = await _assess(
        [html, pdf, independent],
        [_scores(_lab_draft(), _lab_draft(url=PDF_REPORT_URL), _review_draft())],
    )

    by_url = _by_url(sources)
    for url in (LAB_REPORT_URL, PDF_REPORT_URL):
        identity = by_url[url].work_identity
        assert identity is not None, url
        assert identity.identity_status == "known", url
        assert identity.key == REPORT_WORK
        assert by_url[url].work_id == REPORT_WORK
        assert {
            REPORT_WORK,
            f"sha256:{html.content_sha256}",
            f"sha256:{pdf.content_sha256}",
        } <= set(identity.aliases)
    assert not _may_pair(html, pdf, sources)
    assert _may_pair(pdf, independent, sources)


@pytest.mark.asyncio
async def test_a_story_derived_from_the_report_cannot_corroborate_it() -> None:
    """Section 2.2 rule 5: repeating one report is not a second account.

    The story is its own work from its own publisher, labelled as its own
    research — every identity field differs. What it states is where its figure
    comes from, and that lineage is what refuses the pair.
    """
    report = _read()
    story = _read(
        STORY_URL, title="Queue backlog, by the numbers", text=STORY_TEXT
    )
    story_draft = _draft(
        url=STORY_URL,
        source_role="independent_research",
        transport_relation="original",
        issuer="News Daily",
        rationale="An outlet's analysis of the laboratory's report.",
        derived_from=["10.1234/grid.2025", "10.9999/never-cited"],
    )

    sources, _ = await _assess(
        [report, story], [_scores(_lab_draft(), story_draft)]
    )

    by_url = _by_url(sources)
    derived = by_url[STORY_URL]
    assert derived.publisher_id == "news daily"
    assert derived.work_id != by_url[LAB_REPORT_URL].work_id
    assert derived.work_identity is not None
    # Only the lineage the story itself states is recorded; the DOI it never
    # prints is reported as an unevidenced anchor instead.
    assert derived.work_identity.derives_from_work_ids == [REPORT_WORK]
    assert "derived_from" in derived.rationale
    assert not _may_pair(report, story, sources)


@pytest.mark.asyncio
async def test_two_articles_on_one_dataset_cannot_corroborate_each_other() -> None:
    """Section 2.2 rule 6: shared data is one origin however it is written up."""
    first = _read(
        "https://first.example/analysis",
        title="Queue analysis",
        text=(
            "Queue analysis. Published by First Outlet on 2026-02-02. Using "
            f"the national queue dataset (doi:{DATASET_DOI}), we count "
            "1,200 MW withheld during 2024."
        ),
    )
    second = _read(
        "https://second.example/feature",
        title="Queue feature",
        text=(
            "Queue feature. Published by Second Outlet on 2026-02-05. The "
            f"national queue dataset, doi:{DATASET_DOI}, shows 1,200 MW "
            "withheld during 2024."
        ),
    )
    drafts = [
        _draft(
            url=read.resolved_url,
            source_role="independent_research",
            transport_relation="original",
            issuer=issuer,
            rationale="An outlet's own write-up of a public dataset.",
            derived_from=[DATASET_DOI],
        )
        for read, issuer in ((first, "First Outlet"), (second, "Second Outlet"))
    ]

    sources, _ = await _assess([first, second], [_scores(*drafts)])

    assert sources[0].publisher_id != sources[1].publisher_id
    assert sources[0].work_id != sources[1].work_id
    for source in sources:
        assert source.work_identity is not None
        assert source.work_identity.derives_from_work_ids == [f"doi:{DATASET_DOI}"]
    assert not _may_pair(first, second, sources)


async def _independent_packet() -> AdjudicationPacket:
    """A report and a genuinely independent measurement, assessed for real."""
    report = _read()
    review = _review_read()
    sources, _ = await _assess(
        [report, review], [_scores(_lab_draft(), _review_draft())]
    )
    return _identity_packet([report, review], sources)


def _support(evidence_id: str, **fields: object) -> SupportAssessment:
    return SupportAssessment(
        evidence_id=evidence_id,
        stance="supports",
        complete_support=True,
        scope_compatible=True,
        **fields,  # type: ignore[arg-type]
    )


def _adjudicate(
    packet: AdjudicationPacket, right_fields: dict[str, object]
) -> Claim:
    left, right = (unit.evidence_id for unit in packet.units)
    return validate_adjudication(
        ClaimVerdictDraft(
            verdict="verified",
            confidence=0.9,
            assessments=[
                _support(left, dependence="primary", rationale="The report."),
                _support(right, **right_fields),
            ],
            support_ids=[left, right],
            contradiction_ids=[],
            rationale="Both passages state the figure.",
        ),
        packet,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "right_fields", "flag"),
    [
        ("derivative", {"dependence": "derivative"}, None),
        ("dependence-omitted", {}, None),
        ("unrecognised-dependence", {"dependence": "independent"}, None),
        (
            "independent-analysis-of-the-report-origin",
            {"dependence": "independent_analysis", "origin_group_id": REPORT_ORIGIN},
            "shared_origin",
        ),
        (
            "origin-not-in-the-packet",
            {"dependence": "primary", "origin_group_id": "work:doi:10.9999/made-up"},
            "model_disagreement",
        ),
    ],
)
async def test_only_primary_or_independent_analysis_rows_can_complete_a_pair(
    label: str, right_fields: dict[str, object], flag: str | None
) -> None:
    """Section 2.2 rule 7 and the dependence contract, row by row.

    The control proves the packet is a real pair: two primary rows on two
    independent origins verify. Each case then changes only the model's
    judgement of the second row, and none of them may verify.
    """
    packet = await _independent_packet()
    control = _adjudicate(
        packet, {"dependence": "primary", "rationale": "Its own measurement."}
    )
    assert control.evidence_status == "verified_pair"

    claim = _adjudicate(packet, right_fields)

    assert claim.verdict == "insufficient_evidence", label
    assert claim.evidence_status == "source_supported", label
    if flag is not None:
        assert flag in claim.audit_flags, label


@pytest.mark.asyncio
async def test_the_adjudication_request_offers_each_candidate_origin() -> None:
    """The model can only name an allowlisted origin if it was shown them."""
    packet = await _independent_packet()

    body = "\n".join(
        message.content
        for message in adjudication_messages(packet, evidence_chars=4000)
    )

    origins = {
        eligibility.origin_group_id for eligibility in packet.eligibility.values()
    }
    assert origins == {REPORT_ORIGIN, "publisher:review weekly"}
    for origin in origins:
        assert origin in body
    assert "independent_analysis" in body


# --------------------------------------------------------------------------
# What a copy may contribute: §2.2 rule 4. A mirror "contributes no additional
# corroboration" but "can be the first primary support", so it is the *origin*
# that a copy may not manufacture — its publisher is not the origin of the work
# it copies, and a hash-only copy has no other origin to offer.
# --------------------------------------------------------------------------

REPOSITORY_MIRROR_URL = "https://repository.example/lab/grid-outlook"
WIRE_ECHO_URL = "https://wire.example/grid-outlook"
REPORT_NUMBER = "TR-2025-01"
REPOSITORY_MIRROR_TEXT = (
    "Grid Storage Outlook (mirrored copy). Published by Repository Archive on "
    "2026-01-15. Example Lab measured that 1,200 MW of interconnection "
    "capacity was withheld during 2024."
)


@pytest.mark.asyncio
async def test_a_hash_only_mirror_that_stamps_itself_publisher_contributes_no_origin() -> (
    None
):
    """A mirror's publisher is not the origin of the work it mirrors.

    The repository states its own name, so the copy carries an evidenced
    publisher and its own bytes: every identity field the pair test compares
    differs from the original's. What it must not be able to do is call itself
    the origin of a figure it is repeating — ``publisher:repository archive``
    is the *host's* claim about itself, and a hash-only copy has no strong
    alias to offer instead.
    """
    original = _read(LAB_REPORT_URL)
    mirror = _read(
        REPOSITORY_MIRROR_URL,
        title="Grid Storage Outlook (mirrored copy)",
        text=REPOSITORY_MIRROR_TEXT,
    )

    sources, _ = await _assess(
        [original, mirror],
        [
            _scores(
                _lab_draft(),
                _lab_draft(
                    url=REPOSITORY_MIRROR_URL,
                    doi="",
                    issuer="Repository Archive",
                    source_role="independent_research",
                    transport_relation="mirror",
                ),
            )
        ],
    )

    by_url = _by_url(sources)
    copied = by_url[REPOSITORY_MIRROR_URL]
    assert copied.transport_relation == "mirror"
    assert copied.publisher_id == "repository archive"
    assert copied.work_id is not None and copied.work_id.startswith("sha256:")
    assert source_origin_id(copied) is None
    assert not _may_pair(original, mirror, sources)


@pytest.mark.asyncio
async def test_a_mirror_carrying_the_shared_doi_still_contributes_the_works_origin() -> (
    None
):
    """An identifier-bearing copy is the first primary support, not a refusal.

    The copy states the DOI, so it is the same work however it was served and
    whoever served it: it refuses to pair with its own original by work
    equality, and it still pairs with a genuinely independent third source —
    which a blanket refusal of anything transported as a mirror would break.
    """
    original = _read(LAB_REPORT_URL)
    mirror = _read(
        REPOSITORY_MIRROR_URL,
        title="Grid storage outlook",
        text=REPOSITORY_MIRROR_TEXT + " doi:10.1234/grid.2025",
    )
    independent = _review_read()

    sources, _ = await _assess(
        [original, mirror, independent],
        [
            _scores(
                _lab_draft(),
                _lab_draft(
                    url=REPOSITORY_MIRROR_URL,
                    doi="10.1234/grid.2025",
                    issuer="Repository Archive",
                    source_role="independent_research",
                    transport_relation="mirror",
                ),
                _review_draft(),
            )
        ],
    )

    copied = _by_url(sources)[REPOSITORY_MIRROR_URL]
    assert copied.work_id == REPORT_WORK
    assert source_origin_id(copied) == REPORT_ORIGIN
    assert not _may_pair(original, mirror, sources)
    assert _may_pair(mirror, independent, sources)


@pytest.mark.asyncio
async def test_a_syndicated_copy_carrying_the_report_number_keeps_the_works_origin() -> (
    None
):
    """A syndication is transport too, and the report number survives it."""
    report_number_text = PLAIN_REPORT_TEXT + f" Report no. {REPORT_NUMBER}."
    original = _read(LAB_REPORT_URL, text=report_number_text)
    syndicated = _read(
        WIRE_ECHO_URL,
        text=report_number_text + " Syndicated copy.",
    )
    independent = _review_read()
    drafts = [
        _lab_draft(doi="", report_number=REPORT_NUMBER),
        _lab_draft(
            url=WIRE_ECHO_URL,
            doi="",
            report_number=REPORT_NUMBER,
            transport_relation="syndication",
        ),
        _review_draft(),
    ]

    sources, _ = await _assess(
        [original, syndicated, independent], [_scores(*drafts)]
    )

    expected = f"work:report:example lab:{REPORT_NUMBER.casefold()}"
    by_url = _by_url(sources)
    assert source_origin_id(by_url[WIRE_ECHO_URL]) == expected
    assert source_origin_id(by_url[LAB_REPORT_URL]) == expected
    assert not _may_pair(original, syndicated, sources)
    assert _may_pair(syndicated, independent, sources)


@pytest.mark.asyncio
async def test_a_hash_only_source_of_unknown_transport_keeps_its_publisher_origin() -> (
    None
):
    """The control: only a copy loses the publisher fallback.

    A page nobody labelled as a copy is exactly the case ``publisher:`` was
    written for — its own publisher is the most the identity evidence can
    establish, and refusing it would drop sources the ledger depends on.
    """
    original = _read(LAB_REPORT_URL, text=PLAIN_REPORT_TEXT)

    sources, _ = await _assess(
        [original], [_scores(_lab_draft(doi="", transport_relation="unknown"))]
    )

    source = sources[0]
    assert source.transport_relation == "unknown"
    assert source.publisher_id == "example lab"
    assert source_origin_id(source) == "publisher:example lab"


# --------------------------------------------------------------------------
# Lineage and legacy snapshots: a citation of a report NUMBER names the work
# that number keys, and a snapshot written before anchors were persisted keeps
# the identity it already carries (§2.2 rules 3 and 5).
# --------------------------------------------------------------------------

NUMBERED_REPORT_TEXT = (
    PLAIN_REPORT_TEXT + f" Report no. {REPORT_NUMBER}."
)
NUMBERED_REPORT_WORK = f"report:example lab:{REPORT_NUMBER.casefold()}"
NUMBERED_STORY_TEXT = (
    "Queue backlog, by the numbers. Published by News Daily on 2026-02-02. "
    f"Our analysis of the Example Lab report {REPORT_NUMBER} finds 1,200 MW "
    "was withheld during 2024."
)


@pytest.mark.asyncio
async def test_a_story_citing_the_reports_number_cannot_corroborate_it() -> None:
    """A report number names a work, so citing one is citing that work.

    A number is unique inside its issuer's namespace, which is why the work key
    is ``report:<issuer>:<number>`` — and the issuer of the *cited* work is not
    something a citing document establishes, so the citation is recorded as the
    number it printed and matched against the key it names. A citation a
    comparison could never resolve would let a derivative story pass every
    other test — different publisher, different work, different origin — while
    repeating the report it cites (§2.2 rule 5).
    """
    from deep_research.agents.evidence import REPORT_NUMBER_LINEAGE

    report = _read(LAB_REPORT_URL, text=NUMBERED_REPORT_TEXT)
    story = _read(
        STORY_URL, title="Queue backlog, by the numbers", text=NUMBERED_STORY_TEXT
    )
    story_draft = _draft(
        url=STORY_URL,
        source_role="independent_research",
        transport_relation="original",
        issuer="News Daily",
        rationale="An outlet's analysis of the laboratory's report.",
        derived_from=[REPORT_NUMBER],
    )

    sources, _ = await _assess(
        [report, story],
        [_scores(_lab_draft(doi="", report_number=REPORT_NUMBER), story_draft)],
    )

    by_url = _by_url(sources)
    assert by_url[LAB_REPORT_URL].work_id == NUMBERED_REPORT_WORK
    derived = by_url[STORY_URL]
    assert derived.work_identity is not None
    assert derived.work_identity.derives_from_work_ids == [
        f"{REPORT_NUMBER_LINEAGE}{REPORT_NUMBER.casefold()}",
    ]
    # The citation was evidenced: it is recorded, not reported unsupported.
    assert "derived_from" not in derived.rationale
    assert not _may_pair(report, story, sources)


@pytest.mark.asyncio
async def test_a_story_citing_another_number_is_not_refused_by_lineage() -> None:
    """The control: a citation of a *different* number is not that report.

    Lineage reads the number a citation prints, never the shape of a citation,
    so a story citing some other report is refused for whatever else is wrong
    with it and not for this.
    """
    report = _read(LAB_REPORT_URL, text=NUMBERED_REPORT_TEXT)
    story = _read(
        STORY_URL,
        title="Queue backlog, by the numbers",
        text=NUMBERED_STORY_TEXT.replace(REPORT_NUMBER, "IR-1900-77"),
    )
    story_draft = _draft(
        url=STORY_URL,
        source_role="independent_research",
        transport_relation="original",
        issuer="News Daily",
        rationale="An outlet's analysis of another report.",
        derived_from=["IR-1900-77"],
    )

    sources, _ = await _assess(
        [report, story],
        [_scores(_lab_draft(doi="", report_number=REPORT_NUMBER), story_draft)],
    )

    assert _may_pair(report, story, sources)


def _legacy_source(
    url: str,
    *,
    title: str,
    work_id: str,
    publisher_id: str = "example lab",
) -> ScoredSource:
    """A scored source as a snapshot written before anchors were persisted.

    Its identity was resolved with anchors that snapshot does not carry, so
    only ``work_id``/``publisher_id`` survive to say what was assessed.
    """
    return ScoredSource(
        url=url,
        title=title,
        authority_score=0.9,
        recency_score=0.8,
        relevance_score=0.9,
        overall_score=0.88,
        rationale="Assessed by an earlier pass.",
        publisher_id=publisher_id,
        work_id=work_id,
    )


LEGACY_UPDATE_URL = "https://data.example/capacity-update"
LEGACY_UPDATE_TEXT = (
    "Capacity Update. Published by Example Lab on 2026-03-02. Example Lab "
    "measured that 900 MW of interconnection capacity was withheld during 2024."
)


def test_a_legacy_snapshot_keeps_the_identity_it_already_carries() -> None:
    """Re-resolution may not demote a stored identity (§2.2 rule 3).

    A record written before ``identity_anchors`` existed carries what its
    assessment resolved with anchors this snapshot no longer has. Without them
    the read establishes only a serving host and a bare hash, so replacing the
    stored values moves the publisher to whichever host served the copy and the
    work to a set of bytes — and two works from one evidenced issuer, which the
    publisher test refuses, become two hosts that may pass it.
    """
    from deep_research.agents.evidence import resolve_source_identities

    report = _read(LAB_REPORT_URL)
    update = _read(LEGACY_UPDATE_URL, title="Capacity Update", text=LEGACY_UPDATE_TEXT)
    sources = [
        _legacy_source(
            LAB_REPORT_URL, title="Grid Storage Outlook", work_id=REPORT_WORK
        ),
        _legacy_source(
            LEGACY_UPDATE_URL,
            title="Capacity Update",
            work_id="doi:10.1234/capacity.2026",
        ),
    ]

    resolved = resolve_source_identities(sources, [report, update])

    assert [source.work_id for source in resolved] == [
        REPORT_WORK,
        "doi:10.1234/capacity.2026",
    ]
    assert [source.publisher_id for source in resolved] == [
        "example lab",
        "example lab",
    ]
    assert [source.identity_anchors for source in resolved] == [{}, {}]
    assert not _may_pair(report, update, resolved)


def test_a_legacy_strong_work_id_still_names_the_group_it_joins() -> None:
    """A stored strong key is an alias, and a contradicting one is a conflict.

    The legacy record's own work is preserved, and the key it names still joins
    the resolution: a second record of the same body under a different DOI is
    then reported ``conflicting`` rather than silently re-keying the body to
    the identifier the older record never saw (§2.2 rule 3).
    """
    from deep_research.agents.evidence import resolve_source_identities

    read = _read(
        LAB_REPORT_URL,
        text=LAB_REPORT_TEXT + " Re-registered as doi:10.9999/registry-change.",
    )
    sources = [
        _legacy_source(LAB_REPORT_URL, title="Grid Storage Outlook", work_id=REPORT_WORK),
        _legacy_source(
            LAB_REPORT_URL,
            title="Grid Storage Outlook",
            work_id="doi:10.9999/registry-change",
        ).model_copy(
            update={
                "work_id": None,
                "publisher_id": None,
                "identity_anchors": {
                    "doi": "10.9999/registry-change",
                    "issuer": "Example Lab",
                },
            }
        ),
    ]

    resolved = resolve_source_identities(sources, [read])

    assert resolved[0].work_id == REPORT_WORK
    re_registered = resolved[1].work_identity
    assert re_registered is not None
    assert re_registered.identity_status == "conflicting"
    assert re_registered.key is None
    assert set(re_registered.aliases) == {
        REPORT_WORK,
        "doi:10.9999/registry-change",
        f"sha256:{read.content_sha256}",
    }

def test_a_scoring_dossier_shows_the_passages_that_serve_the_plan() -> None:
    """The scoring pass judges the passages that answer its own obligation.

    The Source Evaluator's own path passed no query, so a dossier was the
    read's first four passages in document order: the EIA 65964 page's 5.9 GW
    and 7.0 GW sat past that window, and PUDL's "1 megawatt" with them — the
    pass that judges relevance and identity scored the page's opening. The
    plan is the query this path has, and these are the passages it names.
    """
    from deep_research.agents.source_evaluator import (
        DEFAULT_EXCERPT_CHARS,
        SourceEvaluatorAgent,
        dossier_queries,
    )
    from deep_research.utils.types import (
        QUALITY_CONTRACT_VERSION,
        EvidenceTarget,
        Finding,
        ResearchState,
        SubTopic,
    )

    opening = "Skip to main content. Recent articles. ".ljust(
        DEFAULT_EXCERPT_CHARS, "x"
    )
    middle = "Sign up for our newsletter. ".ljust(DEFAULT_EXCERPT_CHARS, "y")
    other = "About the data. ".ljust(DEFAULT_EXCERPT_CHARS, "z")
    figure = (
        "Battery storage. We expect 5.9 GW of new battery storage capacity in "
        "the first half of 2025, and 7.0 GW in Texas alone."
    )
    body = "\n\n".join((opening, middle, other, figure))
    read = build_read_record(
        session_id="session-1",
        reader="web_scraper",
        requested_url="https://eia.gov/todayinenergy/detail.php?id=65964",
        resolved_url="https://eia.gov/todayinenergy/detail.php?id=65964",
        title="EIA capacity additions",
        retrieved_at="2026-08-20T00:00:00+00:00",
        text=body,
        passages={
            "chunk-0": opening,
            "chunk-1": middle,
            "chunk-2": other,
            "chunk-3": figure,
        },
        extraction_complete=True,
    )
    sub_topic = SubTopic(
        coverage_id="topic-02",
        title="Latest 2025 forecasts",
        rationale="The question asks what the latest forecast is.",
        search_queries=["2025 utility-scale battery storage forecast"],
        success_criteria=[
            "The 2025 forecast figure for battery storage additions in GW"
        ],
        priority=1,
        evidence_targets=[
            EvidenceTarget(
                target_id="topic-02-target-01",
                coverage_id="topic-02",
                question=(
                    "How much battery storage capacity is projected for the "
                    "first half of 2025, and in Texas?"
                ),
                required_dimensions=["value"],
                required=True,
                critical=True,
                support_policy="primary_attribution",
            )
        ],
    )
    state = ResearchState(
        session_id="session-1",
        original_question="How much battery capacity was added in 2024?",
        sub_topics=[sub_topic],
        raw_findings=[
            Finding(
                content="Battery storage additions are forecast for 2025.",
                source_url="https://eia.gov/todayinenergy/detail.php?id=65964",
                source_title="EIA capacity additions",
                extracted_at="2026-08-20T00:00:00+00:00",
                confidence=0.8,
                related_sub_topic=sub_topic.title,
            )
        ],
        read_records={read.read_id: read},
        quality_contract_version=QUALITY_CONTRACT_VERSION,
    )
    agent = object.__new__(SourceEvaluatorAgent)
    agent._excerpt_chars = DEFAULT_EXCERPT_CHARS

    queries = dossier_queries([sub_topic.title], [sub_topic])
    assert queries
    assert "5.9 GW" not in " ".join(queries)

    task = agent.build_task(state)

    (dossier,) = task.dossiers.values()
    assert dossier.excerpts, dossier.excerpts
    assert any("5.9 GW" in excerpt for excerpt in dossier.excerpts)


def test_a_scoring_dossier_shows_each_obligations_figures_behind_navigation() -> None:
    """Navigation first, figures later, and every obligation still visible.

    The scoring path judges relevance and identity from four excerpts. A page
    whose opening is site navigation, whose middle states one obligation's
    figure and whose end states another's, must show both: the excerpts come
    from what the run extracted from the source and from the plan text of each
    obligation it was cited for, not from the page's first characters.
    """
    from deep_research.agents.source_evaluator import (
        DEFAULT_EXCERPT_CHARS,
        SourceEvaluatorAgent,
    )
    from deep_research.utils.types import (
        QUALITY_CONTRACT_VERSION,
        EvidenceTarget,
        Finding,
        ResearchState,
        SubTopic,
    )

    url = "https://www.eia.gov/todayinenergy/detail.php?id=64586"
    navigation = "Skip to main content. ".ljust(700, "n")
    forecast = (
        "Battery storage. In 2025, capacity growth from battery storage could "
        "set a record as we expect 18.2 GW of utility-scale battery storage to "
        "be added to the grid, up from the 10.3 GW added in 2024."
    )
    other = "About the data. ".ljust(400, "o")
    half_year = (
        "Battery storage accounted for 26% (5.9 GW) of capacity additions in "
        "the first half of 2025, with 7.0 GW expected in Texas."
    )
    body = "\n\n".join((navigation, forecast, other, half_year))
    read = build_read_record(
        session_id="session-1",
        reader="web_scraper",
        requested_url=url,
        resolved_url=url,
        title="EIA capacity additions",
        retrieved_at="2026-08-20T00:00:00+00:00",
        text=body,
        passages={
            "chunk-0": navigation,
            "chunk-1": forecast,
            "chunk-2": other,
            "chunk-3": half_year,
        },
        extraction_complete=True,
    )

    def topic(coverage_id: str, title: str, question: str) -> SubTopic:
        return SubTopic(
            coverage_id=coverage_id,
            title=title,
            rationale="The question asks for it.",
            search_queries=["utility-scale battery storage capacity additions"],
            success_criteria=["The stated figure in GW"],
            priority=1,
            evidence_targets=[
                EvidenceTarget(
                    target_id=f"{coverage_id}-target-01",
                    coverage_id=coverage_id,
                    question=question,
                    required_dimensions=["value"],
                    required=True,
                    critical=False,
                    support_policy="primary_attribution",
                )
            ],
        )

    topics = [
        topic("topic-01", "2024 actuals", "How much was added in 2024?"),
        topic("topic-02", "2025 forecasts", "How much is expected in 2025?"),
    ]
    state = ResearchState(
        session_id="session-1",
        original_question="How much battery capacity was added and forecast?",
        sub_topics=topics,
        raw_findings=[
            Finding(
                content=(
                    "The EIA forecast that 18.2 GW of utility-scale battery "
                    "storage would be added in 2025."
                ),
                source_url=url,
                source_title="EIA capacity additions",
                extracted_at="2026-08-20T00:00:00+00:00",
                confidence=0.8,
                related_sub_topic=topics[1].title,
            ),
            Finding(
                content=(
                    "Battery storage accounted for 26% (5.9 GW) of capacity "
                    "additions in the first half of 2025."
                ),
                source_url=url,
                source_title="EIA capacity additions",
                extracted_at="2026-08-20T00:00:00+00:00",
                confidence=0.8,
                related_sub_topic=topics[1].title,
            ),
        ],
        read_records={read.read_id: read},
        quality_contract_version=QUALITY_CONTRACT_VERSION,
    )
    agent = object.__new__(SourceEvaluatorAgent)
    agent._excerpt_chars = DEFAULT_EXCERPT_CHARS

    task = agent.build_task(state)

    (dossier,) = task.dossiers.values()
    shown = "\n".join(dossier.excerpts)
    assert len(dossier.excerpts) <= 4
    assert "18.2 GW" in shown
    assert "5.9 GW" in shown
    assert "7.0 GW" in shown
    assert "1 Megawatt" not in shown  # the control: nothing invented


def test_a_scoring_dossier_reserves_an_excerpt_for_each_obligation() -> None:
    """The real update-round shape: boilerplate that carries the plan's words.

    On 64586 the navigation and the data note contain the obligation's terms —
    "battery storage", "capacity additions", "utility-scale", "generator
    inventory" — while the passage stating the figure sits at rank 3 of that
    obligation's ranking, and the group's four findings each name only a date.
    Rank-major interleaving over one query list gave every slot to the
    findings' picks. Each obligation therefore reserves an excerpt, taken from
    its own complete ranking and preferring the passage that states a figure.
    """
    from deep_research.agents.evidence import build_read_dossiers
    from deep_research.agents.source_evaluator import DEFAULT_EXCERPT_CHARS

    url = "https://www.eia.gov/todayinenergy/detail.php?id=64586"
    navigation = (
        "Solar, battery storage to lead new U.S. generating capacity "
        "additions in 2025 - U.S. Energy Information Administration (EIA) "
        "Statistics Analysis Tools Education News Search Today in Energy Skip "
        "to page content Recent articles liquid fuels natural gas electricity "
        "utility-scale battery storage capacity additions generator inventory "
        "prices map states exports imports coal renewables forecasts "
        "projections gasoline capacity steo short-term energy outlook Archive "
        "About Glossary FAQS In-brief analysis February 24, 2025 "
    )
    solar = (
        "In 2024, generators added a record 30 GW of utility-scale solar to "
        "the U.S. grid, accounting for 61% of capacity additions last year."
    )
    data_note = (
        "Data source: Preliminary Monthly Electric Generator Inventory. The "
        "inventory covers utility-scale battery storage capacity additions and "
        "is published monthly with a three-month lag behind the period."
    )
    figure = (
        "Battery storage. In 2025, capacity growth from battery storage could "
        "set a record as we expect 18.2 GW of utility-scale battery storage to "
        "be added to the grid, up from the 10.3 GW added in 2024."
    )
    glossary = "Glossary and archive of every previous edition of this note."
    body = "\n\n".join((navigation, solar, data_note, figure, glossary))
    read = build_read_record(
        session_id="session-1",
        reader="web_scraper",
        requested_url=url,
        resolved_url=url,
        title="EIA capacity additions",
        retrieved_at="2026-08-20T00:00:00+00:00",
        text=body,
        passages={
            "chunk-0": navigation,
            "chunk-1": solar,
            "chunk-2": data_note,
            "chunk-3": figure,
            "chunk-4": glossary,
        },
        extraction_complete=True,
    )
    obligation = (
        "2024 U.S. grid-scale battery capacity additions reported actuals EIA "
        "electric generator inventory utility-scale battery storage capacity"
    )
    date_only_findings = [
        f"The EIA article is dated February {day}, 2025 and covers capacity."
        for day in (2, 14, 20, 24)
    ]

    (dossier,) = build_read_dossiers(
        [read],
        queries={read.resolved_url: date_only_findings},
        obligation_queries={read.resolved_url: [obligation]},
        excerpt_chars=DEFAULT_EXCERPT_CHARS,
    )

    shown = "\n".join(dossier.excerpts)
    assert len(dossier.excerpts) <= 4
    assert "18.2 GW" in shown
    assert "10.3 GW" in shown
