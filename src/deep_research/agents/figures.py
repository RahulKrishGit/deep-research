"""Figure normalisation for the Evidence Verifier (spec §5.1 step 2).

One fixed, question-independent rule set reads a figure out of text:

- digit grouping and spacing: ``10,400`` equals ``10400``;
- unit spelling: ``GW`` equals ``gigawatt(s)``, ``MWh`` equals
  ``megawatt-hour(s)``, ``%`` equals ``percent``;
- unit scale within one dimension: kW, MW, GW, TW; kWh, MWh, GWh, TWh;
- value and unit are adjacent, joined by a hyphen, or separated only by one
  parenthetical; an ``ac``/``dc`` suffix on the abbreviated unit is ignored;
- a single number word before a unit ("ten gigawatts") is its number;
- a currency spelling (``$``, ``US$``, ``USD``, ``dollar(s)``) is one unit,
  and a score's ``/5`` is one unit with ``out of 5`` (D13), but only when
  :func:`same_quantity` *compares* two already-parsed figures: neither gets a
  scale (``unit_dimension`` still reads ``None`` for both), ``Quantity.unit``
  keeps the plain spelling ``_canonical_unit`` read, and a figure's own
  ``value``/``unit`` are never rewritten -- so :func:`figure_in_text` still
  finds "390 dollars" or "4.8 out of 5" exactly as the page wrote them.

Two live uses: :func:`figure_in_text` and :func:`quantities_in` decide whether
a snippet states the figure when the Context Check could not judge it (PD-26),
and :func:`parse_figure` with :func:`same_quantity` compares two figures'
values -- the row grouping (PD-9) and a report sentence's restatement of a
figure. Nothing here ever reads a finding's ``content``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from deep_research.agents.evidence import cosmetic_text
from deep_research.utils.types import UnitDimension

_SCALES: dict[str, tuple[UnitDimension, Decimal]] = {
    "kw": ("power", Decimal("1e3")),
    "mw": ("power", Decimal("1e6")),
    "gw": ("power", Decimal("1e9")),
    "tw": ("power", Decimal("1e12")),
    "kwh": ("energy", Decimal("1e3")),
    "mwh": ("energy", Decimal("1e6")),
    "gwh": ("energy", Decimal("1e9")),
    "twh": ("energy", Decimal("1e12")),
    "%": ("percent", Decimal("1")),
}
_PREFIX = {"kilo": "k", "mega": "m", "giga": "g", "tera": "t"}
_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}

# Longest spellings first, so "megawatt-hours" is never read as "megawatt".
_UNIT = (
    r"(?:kilo|mega|giga|tera)watt[- ]?hours?"
    r"|(?:kilo|mega|giga|tera)watts?"
    r"|[kmgt]wh(?:ac|dc)?\b|[kmgt]w(?:ac|dc)?\b|per ?cent\b|%"
)
_NUMBER = r"\d{1,3}(?:[, ]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_WORD = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
_GAP = r"[\s-]*(?:\([^()]{0,40}\)\s*)?"
_QUANTITY = re.compile(
    rf"(?<![\w.,])(?P<value>{_NUMBER}|\b(?:{_WORD})\b){_GAP}"
    rf"\(?\s*(?P<unit>{_UNIT})\s*\)?"
)


@dataclass(frozen=True)
class Quantity:
    """One value with its unit, normalised (see the module docstring)."""

    number: Decimal
    unit: str
    dimension: UnitDimension | None
    base: Decimal | None
    value_text: str
    unit_text: str
    start: int = 0
    end: int = 0


def _known_unit(text: str) -> str | None:
    """The canonical spelling ``text`` names, or ``None`` for a unit not known."""
    if text in {"%", "percent", "per cent"}:
        return "%"
    spelled = re.fullmatch(r"(kilo|mega|giga|tera)watts?( ?hours?)?", text)
    if spelled:
        return _PREFIX[spelled.group(1)] + ("wh" if spelled.group(2) else "w")
    abbreviated = re.fullmatch(r"([kmgt]wh?)(?:ac|dc)?", text.replace(" ", ""))
    if abbreviated:
        return abbreviated.group(1)
    return None


# A unit written with its own abbreviation in brackets ("gigawatts (GW)",
# "GW (gigawatts)", "megawatt hours (MWh)"): the two spellings name one unit, so
# the bracketed form is read only when they agree — or when the other half is
# the ac/dc qualifier a unit may carry. A half that scales or denominates the
# unit is not its abbreviation ("kWh (millions)" is a million kWh, "GW
# (thousands)" a thousand GW, "kWh (per capita)" a rate), and reading one half
# while dropping the other gave those units a base that was off by orders of
# magnitude (RevFF1r3's Important 3).
_BRACKETED_UNIT = re.compile(r"^(?P<outer>[^()]+?)\((?P<inner>[^()]+)\)$")
_UNIT_QUALIFIERS = frozenset({"ac", "dc", "acdc"})

# A currency spelling names one unit for comparison only (D13): the run's own
# prices came back as "$390" one loop and "390 USD" the next, and the two
# spellings never folded into one fact. Never added to ``_SCALES`` -- a
# currency has no scale to convert, and a target asking for one is answered
# through the unscaled path (``unit_dimension`` stays ``None``), same as
# before.
_CURRENCY_UNITS = frozenset({"$", "us$", "usd", "dollar", "dollars"})
# A score's denominator is part of the unit, not the value: "4.8/5" and "4.8
# out of 5" are one figure, but "4.8/5" and "4.8/10" are not the same scale.
_SCORE_UNIT = re.compile(r"^(?:(?:out\s*of|of)\s+(\d+(?:\.\d+)?)|/\s*(\d+(?:\.\d+)?))$")


def _canonical_unit(unit: str) -> str:
    text = " ".join(cosmetic_text(unit).replace("-", " ").split())
    known = _known_unit(text)
    if known is not None:
        return known
    bracketed = _BRACKETED_UNIT.fullmatch(text)
    if bracketed:
        halves = [bracketed.group(part).strip() for part in ("outer", "inner")]
        units = [_known_unit(half) for half in halves]
        # The ac/dc qualifier is written with whatever separator the page uses
        # ("AC/DC", "a/c", "A.C."), so it is tested with them all removed
        # (ReRevFF1p1's N2).
        qualified = [re.sub(r"[^a-z]", "", half.casefold()) in _UNIT_QUALIFIERS
                     for half in halves]
        if units[0] is not None and units[0] == units[1]:
            return units[0]
        if units[0] is not None and qualified[1]:
            return units[0]
        if units[1] is not None and qualified[0]:
            return units[1]
    return text


def _comparison_unit(unit: str) -> str:
    """``unit`` (already ``_canonical_unit``-read) folded for comparison only.

    P1 fix: this must never feed ``Quantity.unit`` -- ``figure_in_text``
    builds its literal search from that field, so folding it there made the
    search look for "usd"/"/5" instead of the spelling the page actually
    used, and a verbatim price or score figure stopped matching (dropped by
    the Context Check's PD-26 fallback as unsupported). Read only by
    ``same_quantity``'s unscaled branch, which compares two already-parsed
    figures and never touches what either one prints or is searched for.
    """
    if unit in _CURRENCY_UNITS:
        return "usd"
    score = _SCORE_UNIT.fullmatch(unit)
    if score:
        return f"/{score.group(1) or score.group(2)}"
    return unit


def _number(value: str) -> Decimal | None:
    text = cosmetic_text(value)
    if text in _NUMBER_WORDS:
        return Decimal(_NUMBER_WORDS[text])
    digits = text.replace(",", "").replace(" ", "")
    if not re.fullmatch(r"\d+(?:\.\d+)?", digits):
        return None
    try:
        return Decimal(digits)
    except InvalidOperation:
        return None


def _quantity(value: str, unit: str, *, start: int = 0, end: int = 0) -> Quantity | None:
    number = _number(value)
    if number is None:
        return None
    canonical = _canonical_unit(unit)
    scale = _SCALES.get(canonical)
    return Quantity(
        number=number,
        unit=canonical,
        dimension=scale[0] if scale else None,
        base=number * scale[1] if scale else None,
        value_text=value,
        unit_text=unit,
        start=start,
        end=end,
    )


def unit_dimension(unit: str) -> UnitDimension | None:
    """power, energy or percent for a known unit spelling, else ``None``."""
    scale = _SCALES.get(_canonical_unit(unit))
    return scale[0] if scale else None


_MONTH_WORDS = frozenset({
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec",
})
_ISO_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}(?:-\d{2})?(?!\d)")
# The units that name a date outright, and only those: "days" and "years" name
# durations, which the run's phrase "at the latest two weeks after" also does,
# so a period correction on those is a correction like any other.
_DATE_UNITS = frozenset({"date", "dates", "deadline", "deadlines"})


def is_a_date(value: str, unit: str) -> bool:
    """Whether a figure is a calendar date rather than a measured quantity (improvement 9).

    A date is not a measure: it names no unit dimension of its own, and its
    value reads as a calendar date ("2 August 2025", "August 2, 2025",
    "2027-08-02") or its unit names one ("date", "deadline"). The live run
    recorded five of them as stated figures, and the verifier then "corrected"
    each figure's period to the date the figure *was*, publishing them as
    corrected context and dropping one for the ISO spelling of the date its own
    words spell out. Nothing about such a figure's period is a correction of
    anything: the date is what the page states.
    """
    if unit_dimension(unit) is not None:
        return False
    text = cosmetic_text(f"{value} {unit}")
    if _ISO_DATE.search(text):
        return True
    if set(text.split()) & _MONTH_WORDS:
        return True
    return cosmetic_text(unit) in _DATE_UNITS


def parse_figure(value: str, unit: str) -> Quantity | None:
    """A structured figure as a quantity, or ``None`` when ``value`` is no number."""
    return _quantity(value.strip(), unit.strip())


def quantities_in(text: str) -> list[Quantity]:
    """Every known-unit quantity in ``text``; offsets index ``cosmetic_text(text)``."""
    normalised = cosmetic_text(text)
    found: list[Quantity] = []
    for match in _QUANTITY.finditer(normalised):
        quantity = _quantity(
            match.group("value"), match.group("unit"), start=match.start(), end=match.end()
        )
        if quantity is not None:
            found.append(quantity)
    return found


def same_quantity(left: Quantity, right: Quantity) -> bool:
    """Equal after scale for known units; equal comparison unit and number otherwise (D13)."""
    if left.base is not None and right.base is not None:
        return left.dimension == right.dimension and left.base == right.base
    return _comparison_unit(left.unit) == _comparison_unit(right.unit) and left.number == right.number


def figure_in_text(value: str, unit: str, text: str) -> bool:
    """Spec §5.1 step 2: the figure occurs in ``text`` under the fixed rules."""
    target = parse_figure(value, unit)
    if target is None:
        return False
    if target.base is not None:
        return any(same_quantity(target, found) for found in quantities_in(text))
    literal = re.compile(
        rf"(?<![\w.,])(?P<value>{_NUMBER}|\b(?:{_WORD})\b){_GAP}"
        rf"\(?\s*{re.escape(target.unit)}(?!\w)"
    )
    return any(
        _number(match.group("value")) == target.number
        for match in literal.finditer(cosmetic_text(text))
    )
