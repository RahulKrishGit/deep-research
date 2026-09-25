"""Figure normalisation for the Evidence Verifier (spec §5.1 step 2).

One fixed, question-independent rule set decides whether a figure is in a
snippet, whether the Context Check's evidence words carry it, and whether a
report sentence states it:

- digit grouping and spacing: ``10,400`` equals ``10400``;
- unit spelling: ``GW`` equals ``gigawatt(s)``, ``MWh`` equals
  ``megawatt-hour(s)``, ``%`` equals ``percent``;
- unit scale within one dimension: kW, MW, GW, TW; kWh, MWh, GWh, TWh;
- value and unit are adjacent, joined by a hyphen, or separated only by one
  parenthetical; an ``ac``/``dc`` suffix on the abbreviated unit is ignored;
- a single number word before a unit ("ten gigawatts") is its number.

Callers pass a snippet, evidence words, or a report sentence. Nothing here
ever reads a finding's ``content``.
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


def _canonical_unit(unit: str) -> str:
    text = " ".join(cosmetic_text(unit).replace("-", " ").split())
    if text in {"%", "percent", "per cent"}:
        return "%"
    spelled = re.fullmatch(r"(kilo|mega|giga|tera)watts?( ?hours?)?", text)
    if spelled:
        return _PREFIX[spelled.group(1)] + ("wh" if spelled.group(2) else "w")
    abbreviated = re.fullmatch(r"([kmgt]wh?)(?:ac|dc)?", text.replace(" ", ""))
    if abbreviated:
        return abbreviated.group(1)
    return text


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
    """Equal after scale for known units; equal number and unit text otherwise."""
    if left.base is not None and right.base is not None:
        return left.dimension == right.dimension and left.base == right.base
    return left.unit == right.unit and left.number == right.number


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
