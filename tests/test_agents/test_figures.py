"""Spec §5.1 step 2: the fixed, question-independent figure normalisation."""

from __future__ import annotations

import pytest

from deep_research.agents.figures import (
    bare_numbers,
    dates_in,
    figure_in_text,
    parse_figure,
    quantities_in,
    same_quantity,
    unit_dimension,
    without_dates,
)

PAGE = (
    "Developers added 10.4 gigawatts (GW) of utility-scale battery storage in "
    "2024. The monitor counted 12,314 MW across all segments, or 37,143 "
    "megawatt-hours, and 16 GW/47.3 GWh in 2025."
)


@pytest.mark.parametrize(
    ("value", "unit"),
    [
        ("10.4", "GW"),
        ("10.4", "gigawatts"),
        ("10,400", "MW"),
        ("10400", "megawatts"),
        ("12,314", "MW"),
        ("12314", "megawatt"),
        ("37,143", "MWh"),
        ("37.143", "GWh"),
        ("16", "GW"),
        ("47.3", "gigawatt-hours"),
    ],
)
def test_a_figure_matches_under_the_fixed_normalisation(value: str, unit: str) -> None:
    assert figure_in_text(value, unit, PAGE)


@pytest.mark.parametrize(
    ("value", "unit"),
    [
        ("12.3", "GW"),  # 12,314 MW is 12.314 GW: rounding is not a match
        ("10.4", "GWh"),  # the right number in the wrong dimension
        ("104", "GW"),
        ("2024", "GW"),  # a year is never a figure
        ("47.3", "GW"),
    ],
)
def test_a_figure_that_is_not_there_does_not_match(value: str, unit: str) -> None:
    assert not figure_in_text(value, unit, PAGE)


def test_a_parenthetical_may_sit_between_value_and_unit() -> None:
    assert figure_in_text("19.6", "GW", "plans to add 19.6 (nineteen point six) GW")
    assert figure_in_text("12,314", "MW", "deployed 12,314 (MW) in 2024")


def test_value_and_unit_must_be_adjacent() -> None:
    assert not figure_in_text("15", "GW", "15 projects totalling several GW")


def test_a_hyphen_may_join_the_value_and_the_unit() -> None:
    assert figure_in_text("300", "MW", "a 300-MW battery")


def test_an_ac_or_dc_suffix_on_the_unit_still_matches() -> None:
    assert figure_in_text("300", "MW", "300 MWdc")
    assert figure_in_text("300", "MW", "300 MWac")


def test_percent_spellings_are_one_unit() -> None:
    assert figure_in_text("66", "%", "capacity increased 66 percent in 2024")
    assert figure_in_text("66", "percent", "capacity increased 66% in 2024")
    assert figure_in_text("47", "%", "growth of 47 per cent")


def test_a_single_number_word_before_a_unit_is_its_number() -> None:
    assert figure_in_text("10", "GW", "about ten gigawatts were added")


def test_unknown_units_match_literally_after_grouping() -> None:
    assert figure_in_text("1,200", "projects", "1200 projects came online")
    assert not figure_in_text("1,200", "projects", "1200 plants came online")


def test_unit_dimension() -> None:
    assert unit_dimension("GW") == "power"
    assert unit_dimension("megawatt-hours") == "energy"
    assert unit_dimension("%") == "percent"
    assert unit_dimension("projects") is None


def test_same_quantity_compares_across_scales() -> None:
    ten_gw = parse_figure("10.4", "GW")
    [found] = [q for q in quantities_in("added 10,400 MW") if q.unit == "mw"]
    assert ten_gw is not None and same_quantity(ten_gw, found)


def test_bare_numbers_skip_years_dates_labels_and_quantities() -> None:
    text = "On March 12, 2025, 3 states in Q3 reported 1,250 systems and 10.4 GW."
    assert bare_numbers(text) == ["1250"]
    assert bare_numbers("released 2025-03-12; 12 March 2025; Q1 2025") == []     # F2
    assert "2025-03-12" in dates_in("released 2025-03-12 and 12 March 2025")
    assert len(dates_in("released 2025-03-12 and 12 March 2025")) == 2
    without_iso = without_dates("released 2025-03-12")
    assert "03" not in without_iso
    assert "2025" not in without_iso


def test_a_day_before_a_month_name_is_a_date_label_not_a_number() -> None:
    assert bare_numbers("on 12 March the grid") == []
