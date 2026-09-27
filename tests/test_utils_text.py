"""Tests for the text normalization the planner and the recall path share."""

from __future__ import annotations

from deep_research.utils.text import collapse_whitespace, unique_phrases


def test_whitespace_is_collapsed_to_single_spaces() -> None:
    assert collapse_whitespace("  Grid   connection \n withheld ") == (
        "Grid connection withheld"
    )
    assert collapse_whitespace("   ") == ""


def test_duplicates_are_removed_case_insensitively_in_first_seen_order() -> None:
    """One phrase, one entry — and the first spelling is the one kept."""
    assert unique_phrases(
        [
            "measure: capacity",
            "Measure:  Capacity",
            "measure: capacity ",
            "period: 2026",
        ]
    ) == ["measure: capacity", "period: 2026"]


def test_blank_entries_are_dropped() -> None:
    assert unique_phrases(["", "   ", "fact", "\n"]) == ["fact"]
    assert unique_phrases([]) == []


def test_the_order_of_the_input_is_preserved() -> None:
    assert unique_phrases(["b", "a", "b", "c"]) == ["b", "a", "c"]
