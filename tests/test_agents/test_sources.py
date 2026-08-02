"""Tests for URL normalization, source grouping, and corroboration."""

from __future__ import annotations

import pytest

from deep_research.agents.sources import (
    SourceGroup,
    corroboration_score,
    group_findings_by_url,
    normalize_source_url,
    source_domain,
)
from deep_research.utils.types import Finding

EXTRACTED_AT = "2026-08-01T12:00:00+00:00"


def _finding(
    url: str,
    *,
    sub_topic: str = "Alpha",
    title: str = "QEC 2025",
    content: str = "Logical error rates fell below break-even.",
) -> Finding:
    # Finding forbids a blank source_title at validation, but the grouping
    # fallback is exactly what must handle one, so blank-title findings are
    # built by constructing with a valid title and overriding the field
    # afterwards (model_copy does not revalidate).
    effective_title = title if title.strip() else "QEC 2025"
    finding = Finding(
        content=content,
        source_url=url,
        source_title=effective_title,
        extracted_at=EXTRACTED_AT,
        confidence=0.8,
        related_sub_topic=sub_topic,
    )
    if not title.strip():
        finding = finding.model_copy(update={"source_title": title})
    return finding


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Example.ORG/a/", "https://example.org/a"),
        ("https://www.example.org/a", "https://example.org/a"),
        ("https://example.org:443/a", "https://example.org/a"),
        ("http://example.org:80/a", "http://example.org/a"),
        ("https://example.org/a#section", "https://example.org/a"),
        ("https://example.org/a?q=1", "https://example.org/a?q=1"),
        ("  https://example.org/  ", "https://example.org"),
        ("not a url at all", "not a url at all"),
    ],
)
def test_url_normalization_is_canonical_and_total(raw: str, expected: str) -> None:
    assert normalize_source_url(raw) == expected


def test_source_domain_strips_scheme_port_and_www() -> None:
    assert source_domain("https://www.Example.ORG:443/a") == "example.org"
    assert source_domain("opaque source") == "opaque source"


def test_findings_group_by_normalized_url_in_first_seen_order() -> None:
    groups = group_findings_by_url(
        [
            _finding("https://example.org/a", sub_topic="Alpha"),
            _finding("https://other.test/b", sub_topic="Beta"),
            _finding("https://WWW.example.org/a/", sub_topic="Beta"),
        ]
    )

    assert [group.url for group in groups] == [
        "https://example.org/a",
        "https://other.test/b",
    ]
    assert groups[0].domain == "example.org"
    assert groups[0].sub_topics == ["Alpha", "Beta"]
    assert len(groups[0].findings) == 2


def test_group_title_is_the_first_non_blank_source_title() -> None:
    groups = group_findings_by_url(
        [
            _finding("https://example.org/a", title="  "),
            _finding("https://example.org/a", title="Real Title"),
        ]
    )

    assert groups[0].title == "Real Title"


def test_group_title_falls_back_to_the_url() -> None:
    groups = group_findings_by_url([_finding("https://example.org/a", title=" ")])

    assert groups[0].title == "https://example.org/a"


def test_corroboration_is_the_fraction_of_sub_topics_other_domains_cover() -> None:
    groups = group_findings_by_url(
        [
            _finding("https://example.org/a", sub_topic="Alpha"),
            _finding("https://example.org/a", sub_topic="Beta"),
            _finding("https://other.test/b", sub_topic="Alpha"),
        ]
    )

    assert corroboration_score(groups[0], groups) == pytest.approx(0.5)
    assert corroboration_score(groups[1], groups) == pytest.approx(1.0)


def test_the_same_domain_never_corroborates_itself() -> None:
    groups = group_findings_by_url(
        [
            _finding("https://example.org/a", sub_topic="Alpha"),
            _finding("https://example.org/b", sub_topic="Alpha"),
        ]
    )

    assert corroboration_score(groups[0], groups) == pytest.approx(0.0)


def test_corroboration_of_a_group_with_no_sub_topics_is_zero() -> None:
    empty = SourceGroup(
        url="https://example.org/a",
        domain="example.org",
        title="A",
    )

    assert corroboration_score(empty, [empty]) == pytest.approx(0.0)
