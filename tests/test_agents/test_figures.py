"""Spec §5.1 step 2: the fixed, question-independent figure normalisation."""

from __future__ import annotations

from decimal import Decimal

import pytest

from deep_research.agents.figures import (
    is_a_date,
    figure_in_text,
    parse_figure,
    quantities_in,
    same_quantity,
    unit_dimension,
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


# ---------------------------------------------------------------------------
# D13: currency and score spellings compare as one unit, unscaled
# ---------------------------------------------------------------------------


def test_currency_spellings_are_one_unit_for_comparison() -> None:
    """"dollars", "USD", "$" and "US$" name one unit when two figures are
    compared (D13), so the same price extracted with two spellings is one
    fact. Never scaled: ``unit_dimension`` still reads ``None`` for either."""
    dollars = parse_figure("390", "$")
    assert dollars is not None
    for spelling in ("USD", "US$", "dollars", "dollar"):
        other = parse_figure("390", spelling)
        assert other is not None
        assert same_quantity(dollars, other), spelling
        assert unit_dimension(spelling) is None
    assert unit_dimension("$") is None


def test_score_scales_are_one_unit_for_comparison() -> None:
    """"/5" and "out of 5" name one scale when two figures are compared."""
    slash = parse_figure("4.8", "/5")
    spelled = parse_figure("4.8", "out of 5")
    assert slash is not None and spelled is not None
    assert same_quantity(slash, spelled)
    assert unit_dimension("/5") is None and unit_dimension("out of 5") is None


def test_different_score_denominators_are_not_one_scale() -> None:
    out_of_5 = parse_figure("4.8", "/5")
    out_of_10 = parse_figure("4.8", "/10")
    assert out_of_5 is not None and out_of_10 is not None
    assert not same_quantity(out_of_5, out_of_10)


# ---------------------------------------------------------------------------
# Round 3, Defect B: a unit written with its own abbreviation in brackets.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("unit", "dimension", "base"),
    [
        ("gigawatts (GW)", "power", Decimal("26e9")),
        ("GW (gigawatts)", "power", Decimal("26e9")),
        ("megawatt hours (MWh)", "energy", Decimal("26e6")),
    ],
)
def test_a_bracketed_unit_keeps_its_dimension(unit: str, dimension: str, base: Decimal) -> None:
    """Round 3 (pre-flight run 3): the page writes "26 gigawatts (GW)", and the
    unit then had no dimension at all — so the figure could not answer a power
    or energy target, could not be compared, and its row fell back to "stated
    figure". Pages in every domain write a unit with its abbreviation in
    brackets, in either order.
    """
    quantity = parse_figure("26", unit)

    assert quantity is not None
    assert (quantity.dimension, quantity.base) == (dimension, base)
    assert unit_dimension(unit) == dimension


def test_a_bracketed_unit_naming_two_known_units_stays_unparsed() -> None:
    """The bound: "GW (MWh)" names two different units, so nothing is read from
    it — the same as any other unit this rule set does not know."""
    quantity = parse_figure("26", "GW (MWh)")

    assert quantity is not None and quantity.dimension is None
    assert unit_dimension("GW (MWh)") is None


def test_a_bracketed_qualifier_that_scales_or_denominates_stays_unparsed() -> None:
    """RevFF1r3's Important 3: the bracketed form is the unit's own
    abbreviation, so a half that scales it ("kWh (millions)") or denominates it
    ("kWh (per capita)") is not that: reading one half and dropping the other
    gave 26 million kWh the base of 26 kWh.

    An ac/dc qualifier is the exception the parser already reads beside a unit.
    """
    for unit in ("kWh (millions)", "GW (thousands)", "kWh (per capita)",
                 "MW (per household)", "kWh (billion)"):
        quantity = parse_figure("26", unit)
        assert quantity is not None and quantity.dimension is None, unit
        assert unit_dimension(unit) is None, unit

    assert parse_figure("26", "GW (AC)").dimension == "power"


def test_a_slash_joined_qualifier_still_reads_the_unit() -> None:
    """ReRevFF1p1's N2: a page writing capacity as "MW (AC/DC)" (solar and wind
    datasheets do) keeps its dimension, so the qualifier is tested with its
    separators normalised."""
    for unit in ("MW (AC/DC)", "GW (ac/dc)", "GW (A.C.)", "MW (a/c)"):
        quantity = parse_figure("26", unit)
        assert quantity is not None and quantity.dimension == "power", unit


@pytest.mark.parametrize(("value", "unit", "dated"), [
    ("2 August 2025", "date", True),
    ("August 2, 2025", "date", True),
    ("2 Aug 2025", "date", True),
    ("2027-08-02", "date", True),
    ("2 August 2025", "day", True),
    ("2", "day", False),
    ("12", "GW", False),
    ("45", "percent", False),
    ("two", "weeks", False),
    ("3", "days", False),
    ("2025", "year", False),
])
def test_a_date_is_not_a_measure(value, unit, dated) -> None:
    """Run-2 improvement 9: the run's five spurious corrections were all dates
    read as measured figures, so a date figure is told apart by its own words."""
    assert is_a_date(value, unit) is dated
