"""The wording rules the Evidence Verifier and the Report Writer share:
forecast versus outcome, and the stated roles and scopes a page carries
(PD-19).
"""

from __future__ import annotations

import re
from typing import Literal


# What separates one assertion from the next inside a sentence. The modality
# and scope checks both decide per *clause*: a statement that hardens one
# clause is not excused by a hedge in another, and a note that asserts a
# source's boundary is not excused by a later clause about this pass. Without
# the conjunctions and dashes a single comma carried the whole decision.
# A terminator only separates when a space or the end follows it: "18.2" is a
# figure, not a sentence, and splitting on that full stop cut a reporting verb
# away from the clause it governs.
_CLAUSE_SPLIT = re.compile(
    r"[,;]|[:!?](?=\s|$)|\.(?=\s|$|[A-Z])"
    r'|[\u2014\u2013()\[\]\u201c\u201d"|/]'
    r"|(?<=\s)-(?=\s)"
    r"|\b(?:and|but|while|which|so|thus|therefore|though|although)\b"
)


# Separators a page's own title uses between its headline and its site or
# publisher name ("Article Title | Site Name", "Article Title - Publisher").
TITLE_SEPARATOR = re.compile(r"\s*[|\u2013\u2014]\s*|\s+-\s+")


def title_segments(title: str) -> list[str]:
    """A page's own title, split on its own separators, in the order it writes them.

    "Article Title | Work | Site" gives the three segments a reader sees, so a
    caller can tell the title's headline, the body it serves and the site label
    apart without guessing at anything the title does not itself spell.
    """
    return [part.strip() for part in TITLE_SEPARATOR.split(title) if part.strip()]


def clause_around(text: str, position: int) -> str:
    """The clause of ``text`` containing the character at ``position``."""
    start = 0
    for match in _CLAUSE_SPLIT.finditer(text):
        if match.start() > position:
            return text[start : match.start()]
        start = match.end()
    return text[start:]


_FORECAST_MARKER_PATTERN = re.compile(
    r"\b(?:plan|plans|planned|planning|project|projects|projected|"
    r"projection|projections|forecast|forecasts|forecasted|forecasting|"
    r"expect|expects|expected|anticipate|anticipates|anticipated)\b",
    re.IGNORECASE,
)


def _forecast_role(text: str) -> bool:
    """True when the text reads as a plan, projection, or forecast — not a
    stated outcome. A text that reads either way must never be matched
    against an outcome that merely shares its figure and year: one issuer's
    own forecast is not its own later actual.
    """
    return bool(_FORECAST_MARKER_PATTERN.search(text))


# A past-tense, realised-outcome verb. Matched only outside a future or
# conditional modal's own clause ("would be installed" still names a plan,
# not a report of what happened) via ``clause_around``.
_REALIZED_OUTCOME_PATTERN = re.compile(
    r"\b(?:installed|added|deployed|commissioned|came\s+online|built|"
    r"reached|hit|beat|exceeded|surpassed)\b",
    re.IGNORECASE,
)
_FUTURE_MODAL_PATTERN = re.compile(
    r"\b(?:would|will|could|might|may|should|shall)\b", re.IGNORECASE
)


def realized_outcome(text: str) -> bool:
    """True when the text reports something that already happened.

    A matched verb inside a clause a future or conditional modal governs is
    still a plan ("EIA forecast that 16 GW would be installed"), not a
    report of an outcome, so it does not count. Nor does a verb read as an
    infinitive complement ("expected to hit 15 GW", "expected to be added"):
    "hit" and "beat" spell their infinitive and past-tense forms identically,
    and a verb right after "to" (optionally "to be" or "to have been") is
    what a forecast is expected *to do*, not a report that it did it.
    """
    for match in _REALIZED_OUTCOME_PATTERN.finditer(text):
        if re.search(
            r"\bto\s+(?:be\s+|have\s+been\s+)?\Z", text[: match.start()], re.IGNORECASE
        ):
            continue
        if not _FUTURE_MODAL_PATTERN.search(clause_around(text, match.start())):
            return True
    return False


def stated_role(text: str) -> Literal["forecast", "actual", "mixed"]:
    """Whether the text reads as a forecast, a realised outcome, or both.

    A forecast marker alone is not the whole story: "the operator beat
    projections: 16 GW was installed in 2025" carries a forecast marker
    ("projections") *and* a past-tense, realised-outcome verb ("beat",
    "installed") — it reports what happened, not an open plan. "mixed" is
    the conservative reading for that case: neither a forecast nor an
    actual is safe to match it against, because a real one of either kind
    could be absorbed into a sentence that already settled the question the
    other one still has open.
    """
    forecast = _forecast_role(text)
    outcome = realized_outcome(text)
    if forecast and outcome:
        return "mixed"
    return "forecast" if forecast else "actual"


# Segment and basis words of the one domain the scope check was measured on
# (energy markets; Fable C-a): bounded, and a no-op for any other question,
# whose scopes the Statement Check judges in prose (spec §6.2: "no ... scope
# that the cited findings' verified fields do not carry"). Longest first.
SCOPE_TERMS: tuple[str, ...] = (
    "commercial and industrial", "front-of-the-meter", "behind-the-meter",
    "utility-scale", "grid-scale", "all segments", "all sectors", "residential",
    "commercial", "industrial", "distributed", "community", "c&i",
)


# "commercial and industrial" is "c&i" spelled out; a report and a finding
# that use different spellings of the same segment must read as one scope.
_SCOPE_CANONICAL: dict[str, str] = {"commercial and industrial": "c&i"}


def stated_scopes(text: str) -> list[str]:
    """The scope terms ``text`` states, hyphen and space spellings alike.

    The longest term is tested first, and its matched span is blanked before
    a shorter term is tested, so "commercial and industrial" does not also
    yield its own "commercial" and "industrial" components. "non-residential"
    and "non residential" state no scope: a negated segment is not the scope
    it names.
    """
    folded = " ".join(text.casefold().replace("-", " ").split())
    found: list[str] = []
    for term in SCOPE_TERMS:
        pattern = re.escape(term.replace("-", " "))
        match = re.search(rf"(?<![a-z&]){pattern}(?![a-z])", folded)
        if not match:
            continue
        negated = bool(re.search(r"\bnon\s*$", folded[: match.start()]))
        folded = (
            folded[: match.start()]
            + " " * len(match.group(0))
            + folded[match.end() :]
        )
        if negated:
            continue
        canonical = _SCOPE_CANONICAL.get(term, term)
        if canonical not in found:
            found.append(canonical)
    return found
