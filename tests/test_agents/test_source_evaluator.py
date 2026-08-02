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
    SourceScoreDraft,
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
from deep_research.utils.types import Finding, ScoredSource

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
