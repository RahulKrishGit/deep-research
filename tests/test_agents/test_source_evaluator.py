"""Tests for the Source Evaluator's scoring maths and record building."""

from __future__ import annotations

import pytest

from deep_research.agents.source_evaluator import (
    AUTHORITY_WEIGHT,
    CORROBORATION_WEIGHT,
    LOW_CONFIDENCE_THRESHOLD,
    RECENCY_WEIGHT,
    RELEVANCE_WEIGHT,
    REPUTATION_BLEND,
    EvaluatedSources,
    SourceEvaluationTask,
    SourceEvaluatorAgent,
    SourceScoreDraft,
    SourceScoresDraft,
    average_score,
    blend_authority,
    build_rationale,
    build_scored_source,
    clamp_unit,
    fallback_scored_source,
    low_confidence_count,
    overall_score,
)
from deep_research.agents.sources import SourceGroup
from deep_research.agents.steps import ReActRun
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ProviderTimeoutError
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    Finding,
    MemorySnapshot,
    ResearchState,
    ScoredSource,
)
from tests.agent_fakes import ScriptedCompleter
from tests.research_fakes import FakeReputationSource

EVAL_EXTRACTED_AT = "2026-08-01T12:00:00+00:00"


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
) -> SourceScoreDraft:
    return SourceScoreDraft(
        url=url,
        authority_score=authority,
        recency_score=recency,
        relevance_score=relevance,
        rationale=rationale,
    )


def test_the_weights_are_a_convex_combination() -> None:
    total = (
        AUTHORITY_WEIGHT
        + RECENCY_WEIGHT
        + RELEVANCE_WEIGHT
        + CORROBORATION_WEIGHT
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


def test_overall_score_is_the_weighted_mean_of_the_four_dimensions() -> None:
    score = overall_score(
        authority=0.8, recency=0.6, relevance=0.9, corroboration=0.5
    )

    assert score == pytest.approx(
        0.35 * 0.8 + 0.15 * 0.6 + 0.30 * 0.9 + 0.20 * 0.5
    )


def test_overall_score_stays_in_bounds_for_extreme_inputs() -> None:
    assert overall_score(
        authority=1.0, recency=1.0, relevance=1.0, corroboration=1.0
    ) == pytest.approx(1.0)
    assert overall_score(
        authority=0.0, recency=0.0, relevance=0.0, corroboration=0.0
    ) == pytest.approx(0.0)


def test_authority_ignores_reputation_when_none_is_known() -> None:
    assert blend_authority(0.8, None) == pytest.approx(0.8)


def test_authority_blends_a_known_reputation() -> None:
    blended = blend_authority(0.8, 0.3)

    assert blended == pytest.approx(
        (1 - REPUTATION_BLEND) * 0.8 + REPUTATION_BLEND * 0.3
    )
    assert blended < 0.8


def test_rationale_always_records_corroboration_and_reputation() -> None:
    rationale = build_rationale(
        "Peer-reviewed.",
        corroboration=0.5,
        reputation=0.9,
        sub_topics=["Alpha", "Beta"],
    )

    assert rationale.startswith("Peer-reviewed.")
    assert "Cited for: Alpha, Beta." in rationale
    assert "Corroboration 0.50" in rationale
    assert "Prior reputation 0.90" in rationale


def test_rationale_is_never_blank_when_the_model_returned_nothing() -> None:
    rationale = build_rationale(
        "   ", corroboration=0.0, reputation=None, sub_topics=[]
    )

    assert rationale.strip()
    assert "no sub-topic" in rationale
    assert "No prior reputation on record." in rationale


def test_a_scored_source_clamps_out_of_range_model_scores() -> None:
    source = build_scored_source(
        _group(),
        _draft(authority=9.0, recency=-2.0, relevance=0.9),
        corroboration=0.5,
        reputation=None,
    )

    assert isinstance(source, ScoredSource)
    assert source.authority_score == pytest.approx(1.0)
    assert source.recency_score == pytest.approx(0.0)
    assert source.overall_score == pytest.approx(
        overall_score(
            authority=1.0, recency=0.0, relevance=0.9, corroboration=0.5
        )
    )
    assert source.low_confidence is False


def test_a_weak_source_is_flagged_low_confidence() -> None:
    source = build_scored_source(
        _group(),
        _draft(authority=0.1, recency=0.1, relevance=0.1),
        corroboration=0.0,
        reputation=None,
    )

    assert source.overall_score < LOW_CONFIDENCE_THRESHOLD
    assert source.low_confidence is True


def test_a_fallback_record_is_conservative_and_always_low_confidence() -> None:
    source = fallback_scored_source(
        _group(), corroboration=0.5, reputation=0.9, reason="model_unavailable"
    )

    assert source.url == "https://example.org/a"
    assert source.title == "QEC 2025"
    assert source.recency_score == pytest.approx(0.0)
    assert source.relevance_score == pytest.approx(0.0)
    assert source.corroboration_score == pytest.approx(0.5)
    assert source.authority_score == pytest.approx(blend_authority(0.0, 0.9))
    assert source.low_confidence is True
    assert "could not be reached" in source.rationale


def test_a_fallback_record_rejects_an_unenumerated_reason() -> None:
    with pytest.raises(ValueError, match="reason"):
        fallback_scored_source(
            _group(), corroboration=0.0, reputation=None, reason="because"
        )


def test_observability_aggregates_are_finite_for_an_empty_run() -> None:
    assert average_score([]) == pytest.approx(0.0)
    assert low_confidence_count([]) == 0


def test_observability_aggregates_summarize_scored_sources() -> None:
    strong = build_scored_source(
        _group(), _draft(), corroboration=1.0, reputation=None
    )
    weak = fallback_scored_source(
        _group(url="https://weak.test/b"),
        corroboration=0.0,
        reputation=None,
        reason="not_scored_by_model",
    )

    assert low_confidence_count([strong, weak]) == 1
    assert average_score([strong, weak]) == pytest.approx(
        round((strong.overall_score + weak.overall_score) / 2, 4)
    )


def _evaluator(
    tracker: Tracker,
    completer: ScriptedCompleter,
    *,
    reputation: object | None = None,
    max_sources: int = 12,
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
    assert task.corroborations["https://example.org/a"] == pytest.approx(1.0)
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
    assert scored.corroboration_score == pytest.approx(1.0)
    assert scored.low_confidence is False
    # other.test was never scored by the model, so it still gets a record.
    assert sources[1].low_confidence is True
    assert "returned no score" in sources[1].rationale


@pytest.mark.asyncio
async def test_every_source_still_gets_a_record_when_the_provider_fails(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(outputs=[ProviderTimeoutError("timed out")])
    agent = _evaluator(tracker, completer)
    state = _eval_state([_eval_finding("https://example.org/a")])
    task, _, _ = await agent.lookup_reputations(agent.build_task(state))

    sources, errors, provider_failed = await agent.score_sources(task)

    assert provider_failed is True
    assert [source.url for source in sources] == ["https://example.org/a"]
    assert sources[0].low_confidence is True
    assert "could not be reached" in sources[0].rationale
    assert errors[0].error_type == "source_evaluator_scoring_provider_error"
    assert errors[0].recoverable is False
    assert errors[0].details["exception_type"] == "ProviderTimeoutError"


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
    assert sources[1].low_confidence is True
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
        _group(), _draft(), corroboration=1.0, reputation=None
    )
    run = ReActRun(agent_name="source_evaluator", stop_reason="finished")

    update = agent.state_update(EvaluatedSources(sources=[scored]), run)

    assert update["evaluated_sources"] == [scored]
    assert update["errors"] == []
