"""Facts from verified findings (spec §5.3, §6.1, §6.4, §6.6).

Deterministic and field-driven: target answering, duplicates and revisions,
the Not found list and number tracing read verified fields and structured
figures only. Nothing here parses a finding's ``content``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import get_args

from deep_research.agents.evidence import cosmetic_text
from deep_research.agents.figures import (
    Quantity,
    parse_figure,
    same_quantity,
    unit_dimension,
)
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.sources import publisher_identity
from deep_research.agents.wording import SCOPE_TERMS, stated_scopes
from deep_research.utils.types import (
    AcquisitionState,
    EarlierEdition,
    EvidenceTarget,
    FactRow,
    Finding,
    FigureContext,
    FindingFigure,
    NotFoundTarget,
    SubTopic,
    UnitDimension,
)

_WORD = re.compile(r"[A-Z]{2,}(?![a-z])|[A-Z]?[a-z]+|[A-Z]|\d+")
_HOST = re.compile(r"(?:[a-z0-9-]+\.)+[a-z]{2,}")
_COUNTRY_WORDS = frozenset({"us", "usa", "uk"})
_CONNECTORS = frozenset({"of", "and", "the", "for", "on", "in"})
_LEGAL_SUFFIXES = frozenset(
    {"inc", "llc", "ltd", "corp", "corporation", "co", "association", "institute", "council", "agency"}
)
# A host label may stand for an organisation's name only on a suffix whose
# label is the organisation's own choice or an institution's (PD-18): never on
# a suffix anyone buys to look like someone else ("eia.news").
_NAMEABLE_SUFFIXES = frozenset({"gov", "edu", "int", "mil", "com", "org"})
_YEAR = re.compile(r"(?:19|20)\d{2}")
_PERIOD_FILLER = frozenset({"in", "during", "calendar", "year", "full", "the", "of", "cy"})
_MONTHS = {
    name: number
    for number, names in enumerate(
        (("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
         ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
         ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
         ("december", "dec")),
        start=1,
    )
    for name in names
}
_MONTH_YEAR = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s+((?:19|20)\d{2})\b",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"\b((?:19|20)\d{2})(?:-(\d{1,2})(?:-(\d{1,2}))?)?\b")
_MEASURE_BY_DIMENSION = {"power": "power capacity", "energy": "energy capacity", "percent": "share"}
# The dimensions figures.py scales (spec §6.6). A target naming any other word
# (D10: currency, count, ...) is answered by a figure in a unit the parser does
# not scale, so its period, kind and organisation are still checked.
_SCALED_DIMENSIONS: frozenset[str] = frozenset(get_args(UnitDimension))
_ATTRIBUTION_RANK = {"own": 0, "relayed": 1, "unattributed": 2}
# "grid-scale" and "utility-scale" name the same segment in practice (spec
# §6.6 gap): a target asking for one is answered by a figure stating the
# other. No other pair of ``wording.SCOPE_TERMS`` is treated as equivalent.
_SCOPE_EQUIVALENTS = {"grid-scale": "utility-scale"}


@dataclass(frozen=True)
class VerifiedFigure:
    """One kept figure of a citable finding, with its verified context."""

    finding: Finding
    finding_id: str
    index: int
    figure: FindingFigure
    context: FigureContext
    quantity: Quantity | None
    unchecked: bool


def citable_findings(findings: Sequence[Finding]) -> list[Finding]:
    """§4: only verified and verified_corrected findings can be cited."""
    return [f for f in findings if f.verification is not None and f.verification.status != "dropped"]


def verified_figures(findings: Sequence[Finding]) -> list[VerifiedFigure]:
    """Every kept figure of every citable finding, in finding order."""
    figures: list[VerifiedFigure] = []
    for finding in citable_findings(findings):
        verification = finding.verification
        assert verification is not None
        finding_id = finding_fingerprint(finding)
        for index, result in enumerate(verification.figure_results):
            if result.kept and result.context is not None:
                figures.append(
                    VerifiedFigure(
                        finding=finding, finding_id=finding_id, index=index,
                        figure=result.figure, context=result.context,
                        quantity=parse_figure(result.figure.value, result.figure.unit),
                        unchecked=verification.context_unchecked,
                    )
                )
    return figures


def _tokens(value: str) -> list[str]:
    """A name's words, case kept, camel case split, "U.S." read as one word."""
    text = (
        value.replace("U.S.", "US").replace("U.K.", "UK").replace("A/S", "AS")
        .replace("&", " and ")
    )
    return _WORD.findall(text)


# A legal form is the registration's word, not the organisation's: a filing's
# "Apple Inc." and a newsroom's "Apple" are one organisation, and a page whose
# own name is spelled the other way is still that organisation's page (F4).
_LEGAL_FORMS = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited",
    "plc", "llc", "llp", "lp", "ag", "sa", "se", "nv", "bv", "gmbh", "kg", "ab",
    "as", "oy", "oyj", "kk", "pty", "spa", "srl", "sarl",
})


def _core(tokens: Sequence[str]) -> list[str]:
    """The name's words without a country word, a connector or a trailing legal form.

    At least one word survives, so a name that is nothing but a legal form
    ("Company") keeps the word it has.
    """
    words = [t.casefold() for t in tokens if t.casefold() not in _COUNTRY_WORDS | _CONNECTORS]
    while len(words) > 1 and words[-1] in _LEGAL_FORMS:
        words.pop()
    return words


def _initials_variants(tokens: Sequence[str]) -> set[str]:
    """The name's initials, and again with a trailing Agency/Institute/
    Association word kept.

    "Solar Energy Industries Association" spells SEIA, not SEI, and
    "International Energy Agency" spells IEA, not IE: a trailing legal- or
    institutional-form word is noise for most organisations ("Wood Mackenzie
    Inc" is still "Wood Mackenzie"), but an agency's own acronym often
    includes it. Both readings are offered rather than guessed at.
    """
    kept = [t for t in tokens if t.casefold() not in _COUNTRY_WORDS | _CONNECTORS]

    def spell(words: Sequence[str]) -> str:
        return "".join(
            t.casefold() if t.isupper() and len(t) > 1 else t[0].casefold() for t in words
        )

    variants = {spell(kept)}
    if len(kept) > 1 and kept[-1].casefold() in _LEGAL_SUFFIXES:
        variants.add(spell(kept[:-1]))
    return variants


# A leading article is the page's grammar, not part of the name: "the IPCC" is
# "IPCC", and the article must not stop it being read as the acronym it is.
_LEADING_ARTICLES = frozenset({"the", "a", "an"})


def _single_token(value: str) -> str | None:
    """The one token a host label or an all-capitals acronym stands for, else
    ``None``.

    A Title Case one-word name ("Energy", "Wood") is refused here: it is one
    word of a longer organisation's own name, not a stand-in for the whole
    of it -- accepting it let "energy.gov" (the Department of Energy) read
    as the U.S. Energy Information Administration, whose name happens to
    start with the same word.
    """
    text = value.strip().casefold()
    if _HOST.fullmatch(text):
        label, _, suffix = publisher_identity(f"https://{text}").partition(".")
        return label if suffix.rsplit(".", 1)[-1] in _NAMEABLE_SUFFIXES else None
    tokens = [t for t in _tokens(value) if t.casefold() not in _LEADING_ARTICLES]
    if len(tokens) == 1 and tokens[0].isupper() and len(tokens[0]) > 1:
        return tokens[0].casefold()
    return None


_TRAILING_PARENTHETICAL = re.compile(r"\s*\([^()]*\)\s*$")


def _drop_trailing_parenthetical(value: str) -> str:
    """A name's own trailing "(ACRONYM)" aside, dropped before it is split
    into words: "U.S. Energy Information Administration (EIA)" is one name,
    and its aside must not add a spurious extra word to the initials the
    name, read without it, already spells.
    """
    return _TRAILING_PARENTHETICAL.sub("", value)


def same_organisation(left: str, right: str) -> bool:
    """Whether two organisation names, acronyms or hosts name one organisation."""
    left = _drop_trailing_parenthetical(left)
    right = _drop_trailing_parenthetical(right)
    if not left.strip() or not right.strip():
        return False
    if _HOST.fullmatch(left.strip().casefold()) and _HOST.fullmatch(right.strip().casefold()):
        return publisher_identity(f"https://{left.strip()}") == publisher_identity(f"https://{right.strip()}")
    if not _HOST.fullmatch(left.strip().casefold()) and not _HOST.fullmatch(right.strip().casefold()):
        if _core(_tokens(left)) == _core(_tokens(right)):
            return True
    for one, other in ((left, right), (right, left)):
        token = _single_token(one)
        if token is None:
            continue
        if _single_token(other) == token:
            return True
        if _HOST.fullmatch(other.strip().casefold()):
            continue
        tokens = _tokens(other)
        core_words = _core(tokens)
        joined = "".join(core_words)
        if token in _initials_variants(tokens) | {joined}:
            return True
        # The four-letters-or-more prefix reading is for a host label that
        # blends several of the name's words ("woodmac" for Wood Mackenzie):
        # restricted to host labels, and refused when the token merely
        # spells one of the name's own words whole ("energy" is a literal
        # prefix of "energyinformationadministration", but energy.gov is the
        # Department of Energy, not the agency whose name starts that word).
        if (
            _HOST.fullmatch(one.strip().casefold())
            and len(token) >= 4
            and joined.startswith(token)
            and token not in core_words
        ):
            return True
    return False


def _acronym_leads_the_name(left: str, right: str) -> bool:
    """Whether one name is the all-capitals first word of the other.

    "TIOBE" is "TIOBE Software" and "IEEE" is "IEEE Spectrum": an
    organisation's acronym leads its own name. This is an *answering* rule, not
    a name identity (RevFF1p2's D-1): it says a figure whose own organisation is
    "TIOBE" may answer a target the plan addressed to "TIOBE Software", and it
    deliberately does not reach the matcher every merge, fold and page label
    reads, where "IEA" and "IEA PVPS" are two bodies with different numbers for
    one year.

    Four guards keep it to that reading: a Title Case first word never stands
    for the rest ("Energy" is not "Energy Information Administration"), a
    country word is not an organisation, the token side is never a host label
    (a host is what ``page_owner`` falls back to, not a name), and the longer
    name's own remaining words must carry no all-capitals or digit-bearing
    token -- "IEA PVPS", "IEA Wind TCP", "NREL ATB" and "EIA-923" name
    programmes and a form, not the body whose acronym leads them.
    """
    for one, other in ((left, right), (right, left)):
        if _HOST.fullmatch(one.strip().casefold()):
            continue
        token = _single_token(one)
        if token is None or token in _COUNTRY_WORDS:
            continue
        tokens = _tokens(other)
        if len(tokens) < 2 or not tokens[0].isupper() or token != tokens[0].casefold():
            continue
        if any(
            (part.isupper() and len(part) > 1) or any(ch.isdigit() for ch in part)
            for part in tokens[1:]
        ):
            continue
        return True
    return False


def _answers_organisation(target_organisation: str, name: str) -> bool:
    """Whether ``name`` answers a target's organisation (the answering paths only).

    Two rules, and nothing else: ``same_organisation``'s identity, and the
    acronym that leads an organisation's own name. The second is answering-only
    (RevFF1p2's D-1/D-2), so no merge, revision fold, page label or attribution
    resolution reads it.
    """
    return same_organisation(target_organisation, name) or _acronym_leads_the_name(
        target_organisation, name
    )


def _period_key(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    words = [w for w in re.findall(r"[a-z0-9]+", _folded_period(value)) if w not in _PERIOD_FILLER]
    return " ".join(words) or None


# One period, spelled as the planner asked and as the page writes it (I4):
# "FY2025" is "fiscal 2025", "Q4 2025" is "fourth quarter of 2025", "H1 2025"
# is "first half of 2025". The abbreviation is split from its number first, so
# "FY2025" and "FY 2025" are one spelling, and a fiscal year is folded onto
# "fiscal" but never onto a bare calendar year: only the spellings of *one*
# fiscal period are made to agree.
_PERIOD_ABBREVIATION = re.compile(r"\b(fy|q|h)(?=\d)")
_PERIOD_FOLDS = {"fy": "fiscal", "q": "quarter", "h": "half"}
_ORDINAL_NUMBER = {"first": "1", "second": "2", "third": "3", "fourth": "4"}
_SPELLED_ORDINAL_PERIOD = re.compile(r"\b(first|second|third|fourth)[\s-]+(quarter|half)\b")
# A two-digit year reads as its four-digit one only where the page writes it as
# a year: after an apostrophe ("Q1'25", "H1'25", "FY'25") or directly after
# "FY" ("FY25"). Nothing else attaches one -- a part number that happens to look
# like a period ("H20 chips", "H100", a bare "Q25") is not a year -- and a
# four-digit year is never touched.
_TWO_DIGIT_YEAR = re.compile(r"(?P<lead>['\u2019]|\bfy)(?P<year>\d{2})(?!\d)")


def _full_year(year: str) -> str:
    """The four-digit year a two-digit one stands for, by the usual pivot."""
    return ("20" if int(year) <= 49 else "19") + year


def _folded_period(value: str) -> str:
    text = _SPELLED_ORDINAL_PERIOD.sub(
        lambda match: f"{match.group(2)} {_ORDINAL_NUMBER[match.group(1)]}", cosmetic_text(value)
    )
    text = _TWO_DIGIT_YEAR.sub(
        lambda match: f"{match.group('lead')} {_full_year(match.group('year'))}", text
    )
    return " ".join(_PERIOD_FOLDS.get(word, word) for word in _split_period_words(text))


def _split_period_words(text: str) -> list[str]:
    """``text``'s words, with the abbreviation folded and no apostrophe left in a word.

    An apostrophe between an abbreviation and its year ("fy' 2025", "q1' 2025")
    is the page's own spelling of the gap the fold inserts, so it is dropped
    before the abbreviation is split: without this, the bare token "fy'" never
    reaches the fold table and "FY'25" would keep a key of its own.
    """
    words: list[str] = []
    for word in text.split():
        cleaned = re.sub(r"['\u2019]+", "", word)
        words.extend(_PERIOD_ABBREVIATION.sub(lambda m: f"{m.group(1)} ", cleaned).split())
    return words


def _period_tokens(value: str | None) -> frozenset[str] | None:
    """A period's folded words as a set, or ``None`` when it states none.

    A period's words carry no order -- "Q3 2025" is "2025 Q3" -- while
    ``_period_stated_in`` needs the key's own *run* to find it in a sentence, so
    this is the comparison form and the key stays the ordered one.
    """
    key = _period_key(value)
    return frozenset(key.split()) if key is not None else None


def same_period(left: str | None, right: str | None) -> bool:
    """Equal after cosmetic normalisation, in any word order, or both the same bare year."""
    tokens = _period_tokens(left)
    return tokens is not None and tokens == _period_tokens(right)


# A period the text continues as a *year span* ("FY2024-25", "FY24-25",
# "FY25/26", "2024–25", "2024-25/26") states the years it spans, not the one it
# starts in. A *date* continues the same way and does state its year
# ("2024-01-15", "2024-1-5", "2024/01/15"), so the two readings are separate
# patterns: a run is skipped only when the continuation is a span and not a date
# (ReRevFF1r5's finding 1 -- stacking the date reading into the span pattern
# suppressed the guard entirely, and a three-year span stated its start year
# again).
_RANGE_CONTINUATION = re.compile(r"^\s*[-/\u2013]\s*(?:\d{2}|\d{4})(?!\d)")
_DATE_CONTINUATION = re.compile(
    r"^\s*[-/\u2013]\s*(?:0?[1-9]|1[0-2])\s*[-/\u2013]\s*(?:0?[1-9]|[12]\d|3[01])(?!\d)"
)
# Enough of the tail to see a date's own separator and a two-digit day, spaces
# and all (" - 01 - 05").
_CONTINUATION_CHARS = 12


def _period_stated_in(text: str, period: str | None) -> bool:
    """Whether ``text`` states ``period``, however either one spells it.

    The period and the text fold to one key ("Q4 2025" is "fourth quarter of
    2025"), and the key counts as stated when the text's own folded tokens carry
    it as a contiguous run: a sentence that dates a figure "at the end of Q1'25"
    states the period a page or a reply writes "Q1 2025".

    A run the text continues as a range does not count: "FY2024-25" states the
    fiscal year the range ends in, not "fiscal 2024" (RevFF1r3's Important 2).
    """
    key = _period_key(period)
    if key is None:
        return False
    wanted = key.split()
    folded = _folded_period(text)
    tokens = [match for match in re.finditer(r"[a-z0-9]+", folded)
              if match.group() not in _PERIOD_FILLER]
    for start in range(len(tokens) - len(wanted) + 1):
        run = tokens[start : start + len(wanted)]
        if [match.group() for match in run] != wanted:
            continue
        tail = folded[run[-1].end() : run[-1].end() + _CONTINUATION_CHARS]
        if _RANGE_CONTINUATION.match(tail) and not _DATE_CONTINUATION.match(tail):
            continue
        return True
    return False


# Fable §8.6 step 2's filler words, less "a" and "an": a single letter can be a
# model's name ("Model A"), and the subset rule already tolerates an article.
_SUBJECT_FILLER = frozenset({"the", "of", "for", "in", "on", "and", "or", "its", "this", "that"})
# An article is filler in a *target's* words but never in a subject's: "Model A"
# and "Model B" differ by one letter, so a question's "a kettle" must not erase
# it (fix round 1, CRITICAL 1).
_ARTICLE = frozenset({"a", "an"})

# The words that say *how much*, never *what about*: a subject and a target's
# measure sharing only these are not about the same thing. The live pre-flight
# printed the EIA's 2025 forecasts for utility-scale solar (32.5 GW), wind
# (7.7 GW) and natural gas (4.4 GW) under the measure "projected grid-scale
# battery storage capacity additions", because every field but the subject
# matched (review-02).
_MEASURE_FILLER = frozenset({
    "capacity", "addition", "additions", "added", "projected", "projection",
    "projected", "forecast", "forecasts", "value", "total", "number", "amount",
    "change", "growth", "increase", "decrease", "level", "rate", "percent",
    "percentage", "average", "mean", "share", "size", "figure", "data",
})
# Unit spellings, so a subject that only restates its own unit ("gigawatts of
# storage") says nothing about what the figure is about.
_UNIT_WORDS = frozenset({
    "gw", "mw", "kw", "tw", "gwh", "mwh", "kwh", "twh", "watt", "watts",
    "gigawatt", "gigawatts", "megawatt", "megawatts", "kilowatt", "kilowatts",
    "terawatt", "terawatts", "percent", "percentage", "dollar", "dollars",
})
# Period spellings: a year or a quarter is when a figure is about, not what.
_PERIOD_WORDS = frozenset({"quarter", "quarters", "half", "fiscal", "annual", "monthly",
                           "yearly", "ytd"}) | frozenset(_MONTHS)
# Every word of the scope vocabulary (``wording.SCOPE_TERMS``): "utility-scale"
# and "grid-scale" are the same segment in a subject as in a measure, so they
# tell two measures apart in neither direction.
_SCOPE_WORDS = frozenset(
    word for term in SCOPE_TERMS for word in re.split(r"[\W_]+", term) if word
)


def _subject_tokens(text: str | None) -> tuple[str, ...]:
    """Fable §8.6 step 2, in order: casefolded words, "U.S." as "us", punctuation as spaces, filler dropped."""
    if not text or not text.strip():
        return ()
    folded = re.sub(r"\bu\.s\.", "us", text.casefold())
    return tuple(word for word in re.sub(r"[\W_]+", " ", folded).split() if word not in _SUBJECT_FILLER)


def _subject_words(text: str | None) -> frozenset[str]:
    return frozenset(_subject_tokens(text))


def _stated_words(*texts: str | None) -> frozenset[str]:
    """The words a target states in these texts, less its articles (fix round 1, CRITICAL 1)."""
    words: set[str] = set()
    for text in texts:
        words |= _subject_words(text)
    return frozenset(words) - _ARTICLE


def subject_context(target_ids: Iterable[str], targets: Iterable[EvidenceTarget]) -> frozenset[str]:
    """Fable §8.6 step 3: the words *every* one of these targets states.

    A subject that only restates what its targets already say ("United States"
    on a target about the United States) names nothing, so it matches any
    subject. The words are the shared ones, not the pooled ones (fix round 1,
    IMPORTANT 2): of two sibling targets asking one thing of two places
    ("Spain", "Italy"), each name belongs to one target alone, so neither
    subject is stripped. With one shared target this is that target's words.
    Articles never count (fix round 1, CRITICAL 1), so "Model A" keeps its "A"
    even when the target's question says "a kettle".
    """
    wanted = set(target_ids)
    shared: frozenset[str] | None = None
    for target in targets:
        if target.target_id in wanted:
            words = _stated_words(target.measure, target.question, target.geography)
            shared = words if shared is None else shared & words
    return shared or frozenset()


@dataclass(frozen=True)
class _TargetFields:
    """What the targets two sides share state as their own fields (Task 5.6c).

    The distinguishing test maps each subject's give-away word to one field --
    the geography if it states the word, else the measure, else the rest of the
    target, its question, period and organisation -- and tells two subjects
    apart only when their give-aways share a field. ``articles`` holds the
    articles those targets write themselves: an article is a question word, and
    a target that never writes one has named nothing by it.
    """

    measure: frozenset[str] = frozenset()
    geography: frozenset[str] = frozenset()
    articles: frozenset[str] = frozenset()


def _target_fields(target_ids: Iterable[str],
                   targets: Iterable[EvidenceTarget]) -> _TargetFields:
    """The fields of the targets ``target_ids`` name, intersected across them.

    Intersected like ``subject_context``: with one shared target (the
    comparison case) this is that target's own fields.
    """
    wanted = set(target_ids)
    measure: frozenset[str] | None = None
    geography: frozenset[str] | None = None
    articles: frozenset[str] | None = None
    for target in targets:
        if target.target_id not in wanted:
            continue
        stated_measure = _stated_words(target.measure)
        stated_geography = _stated_words(target.geography)
        spelled = (_subject_words(target.measure) | _subject_words(target.question)
                   | _subject_words(target.geography)) & _ARTICLE
        measure = stated_measure if measure is None else measure & stated_measure
        geography = stated_geography if geography is None else geography & stated_geography
        articles = spelled if articles is None else articles & spelled
    return _TargetFields(measure or frozenset(), geography or frozenset(), articles or frozenset())


def _give_away_field(word: str, fields: _TargetFields) -> str:
    """The one field a give-away word belongs to: geography, then measure, then the question."""
    if word in fields.geography:
        return "geography"
    if word in fields.measure:
        return "measure"
    return "question"


def _names_one_thing(left: frozenset[str], right: frozenset[str], *,
                     context_words: frozenset[str] = frozenset()) -> bool:
    """Fable §8.6 steps 4-5: either side names nothing beyond the context, or one contains the other.

    The comparison ``_subject_fits`` makes -- one subject against the words a
    target spells its own subject with -- is this rule alone. Two *subjects*
    against each other also get Task 5.6c's distinguishing test first, in
    ``same_subject``, which is where a target that names both options is seen.
    """
    left_words = left - context_words
    right_words = right - context_words
    if not left_words or not right_words:
        return True
    return left_words <= right_words or right_words <= left_words


def _told_apart(left: frozenset[str], right: frozenset[str], *,
                context_words: frozenset[str] = frozenset(),
                target_fields: _TargetFields = _TargetFields()) -> bool:
    """Task 5.6c: whether the target names a give-away of each side in the *same* field.

    A target that names both options states a word only the one side carries
    and a word only the other does ("Kettle K1" and "Kettle K2"), and those
    give-aways belong to the same field -- both are the target's own question
    words -- so the two sides are different things however much of either
    subject it also restates. A give-away the target states *elsewhere* tells
    them nothing apart: a subject that is the target's measure and one that is
    its geography ("widget adoption" and "United States") are one topic the
    target describes, and either side may carry extra words of the other's
    field ("battery storage capacity" against "United States"), so single-subject
    runs keep the rows they had. Only an article a target actually writes counts
    as a give-away (fix round 1, Minor 3), and an article the *subject* carries
    is a question word like any other.
    """
    stated = context_words | target_fields.articles
    left_give_aways = (left - right) & stated
    right_give_aways = (right - left) & stated
    if not left_give_aways or not right_give_aways:
        return False
    left_fields = {_give_away_field(word, target_fields) for word in left_give_aways}
    right_fields = {_give_away_field(word, target_fields) for word in right_give_aways}
    return bool(left_fields & right_fields)


def same_subject(left: str | None, right: str | None, *,
                 context_words: frozenset[str] = frozenset(),
                 target_fields: _TargetFields = _TargetFields()) -> bool:
    """Fable §8.6 steps 1-5: whether two subjects can name one thing.

    Compatible when either names nothing beyond the context, or when one set of
    words contains the other ("X200" and "Acme X200"). Overlap is not enough:
    "version 10.02" and "version 10.03" share "version" and stay apart. A
    target that names both sides' give-aways in one field tells the two
    subjects apart even though it restates both of them (Task 5.6c).
    """
    left_words = _subject_words(left)
    right_words = _subject_words(right)
    if _told_apart(left_words, right_words, context_words=context_words,
                   target_fields=target_fields):
        return False
    return _names_one_thing(left_words, right_words, context_words=context_words)


def _periods_match(left_period: str | None, right_period: str | None,
                   left_subject: str | None, right_subject: str | None, *,
                   context_words: frozenset[str] = frozenset(),
                   target_fields: _TargetFields = _TargetFields()) -> bool:
    """PD-9's period test, plus controller ruling N1.

    Two figures that both state no period (a current price, a product rating)
    are one period only when both carry a subject and it is the same one, so
    one product's rating on its own page and on a relay is one fact and a
    re-test folds as a revision. Without subjects, no period never matches, as
    before, so a single-subject run is unchanged.
    """
    if _period_key(left_period) is None and _period_key(right_period) is None:
        return bool(left_subject and right_subject) and same_subject(
            left_subject, right_subject, context_words=context_words, target_fields=target_fields)
    return same_period(left_period, right_period)


def subject_named_in(text: str, subject: str | None, *,
                     context_words: frozenset[str] = frozenset()) -> bool:
    """Whether ``text`` names ``subject``: its distinctive words as one run of words.

    A run, not a set (PlanCheck F9): "Model B scored a 4.5" does not name
    "Model A", although both of its words occur there. True with no subject.
    """
    wanted = tuple(word for word in _subject_tokens(subject) if word not in context_words)
    if not wanted:
        return True
    words = tuple(word for word in _subject_tokens(text) if word not in context_words)
    return any(words[i:i + len(wanted)] == wanted for i in range(len(words) - len(wanted) + 1))


def subject_distinguishes(text: str, subject: str | None, rival: str | None, *,
                          context_words: frozenset[str] = frozenset(),
                          target_fields: _TargetFields = _TargetFields()) -> bool:
    """Whether ``text`` names what tells ``subject`` from ``rival`` (Task 5.6c).

    A target that names both options ("the Kettle K1 and the Kettle K2") strips
    what either subject would say alone, so ``subject_named_in`` cannot tell the
    two apart. The words that distinguish one from the other are then the
    target's own give-away ("K1"), and a sentence about one of them states them.
    Two subjects that are the same thing have nothing to distinguish, so they
    are never refused here. A give-away that is nothing but an article
    distinguishes nothing on its own -- "Model A" against "Model B" leaves just
    the "a", which any sentence may carry -- so the whole name is required
    instead ("model a"; fix round 1, Important 2).
    """
    if same_subject(subject, rival, context_words=context_words, target_fields=target_fields):
        return True
    distinctive = _subject_words(subject) - _subject_words(rival)
    wanted = tuple(word for word in _subject_tokens(subject) if word in distinctive)
    if wanted and all(word in _ARTICLE for word in wanted):
        wanted = _subject_tokens(subject)
    if not wanted:
        return True
    words = _subject_tokens(text)
    return any(words[i:i + len(wanted)] == wanted for i in range(len(words) - len(wanted) + 1))


def subject_names_row(text: str, row: FactRow, rows: Sequence[FactRow],
                      targets: Iterable[EvidenceTarget]) -> bool:
    """Whether ``text`` is about ``row``: it names the row's subject, and tells it from each rival.

    The one rule both the reader's labels (``report._point_labels``) and the
    writer's restatement guard ask of a sentence (Task 5.6c). A row with no
    subject is named by any sentence. Where the candidate rows' subjects are
    different things -- a target that names both options, say -- a sentence
    counts for a row only when it states what distinguishes that row from every
    rival, so "Kettle K1 scored 4.5" is never taken for Kettle K2.
    """
    if not subject_named_in(text, row.subject,
                            context_words=subject_context(row.target_ids, targets)):
        return False
    for other in rows:
        if other is row:
            continue
        shared = set(row.target_ids) & set(other.target_ids)
        if not subject_distinguishes(text, row.subject, other.subject,
                                     context_words=subject_context(shared, targets),
                                     target_fields=_target_fields(shared, targets)):
            return False
    return True


def _asks_the_same(left: EvidenceTarget, right: EvidenceTarget) -> bool:
    return (
        " ".join(left.measure.casefold().split()) == " ".join(right.measure.casefold().split())
        and _period_tokens(left.period) == _period_tokens(right.period)
        and left.kind == right.kind and left.unit_dimension == right.unit_dimension
        and (left.organisation or "").casefold() == (right.organisation or "").casefold()
    )


def _subject_fits(figure: VerifiedFigure, target: EvidenceTarget,
                  plan_targets: Sequence[EvidenceTarget]) -> bool:
    """D11 (Fable §8.5): of targets asking one thing of different subjects, a figure answers its own.

    Siblings share measure, period, kind, unit dimension and organisation, so
    the words of a target's question and geography that not all of them share
    name its subject ("Spain", "Model A"). A plan without siblings, or a figure
    without a subject, is never refused here. The subject keeps its own
    articles and the target's words do not (fix round 1, CRITICAL 1): a
    question's "in a lab" must not read as the "A" of "Model A".
    """
    siblings = [t for t in plan_targets if t.target_id != target.target_id and _asks_the_same(t, target)]
    if not siblings or not figure.context.subject:
        return True
    shared = frozenset.intersection(
        *(_stated_words(t.question, t.geography) for t in (target, *siblings))
    )
    subject_words = _subject_words(figure.context.subject)
    distinctive = _folded_words(subject_words - shared)
    # The words the siblings are told apart *by*: what each of them states about
    # itself and the others do not.
    distinguishing = _folded_words(
        frozenset.union(*(
            _subject_words(t.question) | _subject_words(t.geography) for t in (target, *siblings)
        )) - shared
    )
    if distinctive and not (distinctive & distinguishing):
        # The siblings differ only by words this subject cannot carry: "the model
        # RTINGS ranks highest" against "the model Wirecutter ranks highest", two
        # price targets no product's subject names either publisher of. The rule
        # has nothing to match on, so it defers to the extraction's own binding
        # (``finding_answers`` already requires the target id) rather than
        # refusing both and printing "Not found" beside the report's answer (F3).
        return True
    return _names_one_thing(subject_words,
                            _stated_words(target.question, target.geography),
                            context_words=shared)


def canonical_scopes(text: str | None) -> set[str]:
    """The scope terms ``text`` states, with grid-scale/utility-scale folded
    into one term; empty for no stated scope."""
    if not text:
        return set()
    return {_SCOPE_EQUIVALENTS.get(term, term) for term in stated_scopes(text)}


# The words that make a subject a *measure* phrase rather than the name of a
# variant: "utility-scale solar capacity" claims a quantity, "version 10.02"
# says which edition of one. Only a measure-shaped subject can contradict a
# target's measure, so only one is tested against it (``two-versions-one-target``
# and the D11 sibling rows depend on the other kind answering).
_MEASURE_WORDS = _MEASURE_FILLER | frozenset({
    "power", "energy", "price", "prices", "cost", "costs", "output", "production",
    "demand", "consumption", "emissions",
})


def _folded_words(words: frozenset[str]) -> frozenset[str]:
    """``words`` with a simple plural folded away, so "kettles" is "kettle".

    A target's question and a page's subject spell the same thing with
    different number ("Which kettles ..." against the subject "Kettle K1"), and
    only the stems have to agree. A three-letter word is never folded, so "gas"
    stays "gas".
    """
    return frozenset(
        word[:-1] if len(word) >= 5 and word.endswith("s") else word for word in words
    )


def _distinctive_words(text: str | None, unit: str | None = None) -> frozenset[str]:
    """The words that say *what* ``text`` is about, less the boilerplate.

    A word is distinctive when it is at least four letters and is not a unit
    spelling (the figure's own unit included), a period word, a scope word or
    one of the measure fillers: "utility-scale solar capacity" leaves "solar",
    and "projected grid-scale battery storage capacity additions" leaves
    "battery" and "storage".
    """
    unit_words = frozenset(re.findall(r"[a-z]+", (unit or "").casefold()))
    return _folded_words(frozenset(
        word for word in _subject_tokens(text)
        if len(word) >= 4
        and not _YEAR.fullmatch(word)
        and word not in _UNIT_WORDS and word not in unit_words
        and word not in _PERIOD_WORDS and word not in _SCOPE_WORDS
        and word not in _MEASURE_FILLER
    ))


def _names_a_measure(text: str, unit: str | None) -> bool:
    """Whether ``text`` is itself a measure phrase: it states a quantity word.

    A figure's unit is part of that ("4.5 out of 5" makes "out of 5" a
    quantity), so a subject is read with the unit its figure carries.
    """
    words = set(_subject_tokens(text)) | set(re.findall(r"[a-z]+", (unit or "").casefold()))
    return bool(words & _MEASURE_WORDS)


def _subject_names_the_measure(figure: VerifiedFigure, target: EvidenceTarget) -> bool:
    """Whether a figure that names a subject is about this target's measure.

    ``_subject_fits`` answers the sibling question (which of two targets asking
    one thing this figure is about) and never refuses a subject-less figure.
    This answers the other one (review-02): a figure whose subject states a
    *different* measure does not answer the target, however well its unit,
    period, kind and organisation fit. Two ways to fit are admitted — the
    subject and the measure share a distinctive word, or the subject names
    nothing the target does not already state itself (the restatement case:
    "United States", "Kettle K1" for a target that names them).

    A subject that is not a measure phrase at all is never tested: it says
    which entity or variant the figure is about ("version 10.02", "Kettle K1",
    "Spain"), which is what the sibling rule tells apart, and it cannot
    contradict a measure it never claims. A figure with no subject answers
    exactly as it did.
    """
    if not figure.context.subject:
        return True
    if not _names_a_measure(figure.context.subject, figure.figure.unit):
        return True
    distinctive = _distinctive_words(figure.context.subject, figure.figure.unit)
    if not distinctive:
        return True
    if distinctive & _distinctive_words(target.measure):
        return True
    stated = _folded_words(_stated_words(target.measure, target.question, target.geography))
    return distinctive <= stated


def _figure_answers(figure: VerifiedFigure, target: EvidenceTarget,
                    plan_targets: Sequence[EvidenceTarget] = ()) -> bool:
    target_scopes = canonical_scopes(target.measure)
    figure_scopes = canonical_scopes(figure.context.scope)
    if target_scopes and figure_scopes and target_scopes.isdisjoint(figure_scopes):
        # A target whose measure names a scope ("grid-scale additions") is
        # refused by a figure stating a *different* one ("all segments");
        # a figure with no stated scope is never refused on this ground.
        return False
    # A value the number parser cannot read -- a currency symbol, a sign, an
    # approximation, a range -- is still a figure in a unit the page wrote, so
    # the unit's own dimension says what the figure is about (I3). An unknown
    # unit names no dimension, which is what a target asking for a currency or
    # a count asks for.
    dimension = (figure.quantity.dimension if figure.quantity is not None
                 else unit_dimension(figure.figure.unit))
    if target.unit_dimension in _SCALED_DIMENSIONS:
        fits = dimension == target.unit_dimension
    else:
        fits = dimension is None
    return (
        fits
        and (target.period is None or same_period(figure.context.period, target.period))
        and (target.kind is None or figure.context.kind == target.kind)
        and (target.organisation is None
             or _answers_organisation(target.organisation, figure.context.organisation))
        and _subject_names_the_measure(figure, target)
        and _subject_fits(figure, target, plan_targets)
    )


def _finding_organisations(finding: Finding) -> list[str]:
    names = [figure.context.organisation for figure in verified_figures([finding])]
    if finding.attributed_issuer:
        names.append(finding.attributed_issuer)
    names.append(publisher_identity(finding.source_url))
    return names


def finding_answers(finding: Finding, target: EvidenceTarget, *,
                    plan_targets: Sequence[EvidenceTarget] = ()) -> bool:
    """§6.6, plus PD-7 for a target with no unit dimension, and D11's sibling rule."""
    if finding.verification is None or finding.verification.status == "dropped":
        return False
    if target.target_id not in finding.target_ids:
        return False
    if target.unit_dimension is None:
        # A qualitative target's organisation is the plan's preference, not a
        # gate on the page's own statements (Defect C): a host name is not
        # evidence of who a page speaks for (playvalorant.com is Riot Games's,
        # github.blog is GitHub's, every EU body is europa.eu), so requiring the
        # finding's own names to spell it refused the organisation's own pages
        # and reported the obligation "Not found" against its own evidence.
        #
        # The one thing the page's own credit decides is a relay: a finding the
        # page states as another body's -- through the finding's admitted issuer
        # or through a figure the Context Check read as that body's -- answers
        # that body's obligations. A finding with neither credit carries no
        # organisation of its own to weigh -- and the report labels every row
        # with its own source, so no statement is credited to the target's body
        # by this.
        if not finding.attributed_issuer and not any(
            figure.context.attribution == "relayed" for figure in verified_figures([finding])
        ):
            return True
        return target.organisation is None or any(
            _answers_organisation(target.organisation, name)
            for name in _finding_organisations(finding)
        )
    return any(_figure_answers(figure, target, plan_targets) for figure in verified_figures([finding]))


def answered_target_ids(
    findings: Sequence[Finding], targets: Sequence[EvidenceTarget]
) -> dict[str, list[str]]:
    """Target id -> the ids of the findings that answer it (answered targets only)."""
    answered: dict[str, list[str]] = {}
    for target in targets:
        ids = [finding_fingerprint(f) for f in findings
               if finding_answers(f, target, plan_targets=targets)]
        if ids:
            answered[target.target_id] = ids
    return answered


def release_text(finding: Finding) -> str | None:
    """The finding's edition as the reader sees it: vintage, then release or statement date."""
    parts: list[str] = []
    if finding.vintage:
        parts.append(finding.vintage)
    if finding.release_date:
        parts.append(f"released {finding.release_date}")
    elif finding.statement_date:
        parts.append(f"stated {finding.statement_date}")
    return "; ".join(parts) or None


def _date_key(text: str | None) -> tuple[int, int, int] | None:
    if not text:
        return None
    month_year = _MONTH_YEAR.search(text)
    if month_year:
        return (int(month_year.group(2)), _MONTHS[month_year.group(1).casefold()], 0)
    iso = _ISO_DATE.search(text)
    if iso:
        return (int(iso.group(1)), int(iso.group(2) or 0), int(iso.group(3) or 0))
    return None


def release_key(finding: Finding) -> tuple[int, int, int] | None:
    """A sortable release: release date, else statement date, else vintage."""
    for value in (finding.release_date, finding.statement_date, finding.vintage):
        key = _date_key(value)
        if key is not None:
            return key
    return None


_RELATIVE_PERIOD = re.compile(
    r"\b(?:(?P<window>the|over the|in the|during the)\s+)?"
    r"(?:(?P<direction>this|current|last|next|coming)\s+"
    r"(?P<period>year|quarter|half|month|season)(?!\s+of\b)"
    r"|(?P<to_date>year[- ]to[- ]date|so far this year))\b",
    re.IGNORECASE,
)
_MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December")
_RELATIVE_STEP = {"this": 0, "current": 0, "last": -1, "next": 1, "coming": 1}
_PERIOD_MONTHS = {"quarter": 3, "half": 6, "month": 1}
# "over the last quarter" and "during the last half" state a window of time, not
# a calendar period, the way "the last year" is the trailing twelve months, so
# neither is resolved; "in the last quarter" keeps its calendar-quarter reading.
_DURATION_PREFIXES = frozenset({"over the", "during the"})
# A comparison marker before the phrase means it names the base the figure is
# *compared with*, not the period it applies to: "output rose 12 percent over
# last year" measures this year, so resolving it to last year would file the
# figure under a period the page never gave it (P2-1).
_COMPARISON_MARKERS = frozenset({"over", "than", "from", "vs", "versus", "compared", "since"})
_COMPARISON_LOOKBACK = 3


def _names_a_comparison_base(evidence_words: str, match: re.Match[str]) -> bool:
    """True when the phrase ``match`` found is a comparison base, not a period."""
    before = evidence_words[: match.start()].split()[-_COMPARISON_LOOKBACK:]
    return any(word.casefold() in _COMPARISON_MARKERS for word in before)


def resolve_relative_period(evidence_words: str, page_date: str | None) -> str | None:
    """Spec §5.2 (D11): the period a relative phrase names, counted from the page's own date.

    "this year" on a page dated 2026-02-20 is 2026 and "last quarter" is
    Q4 2025. ``None`` when the words carry no relative phrase, when there is no
    page date, for a season (its year is the page's to state), and for a
    quarter, half or month against a date with no month or an impossible one.
    A window of time — "the past year", "the last year", "over the last
    quarter" — is the months before the page rather than a calendar period, and
    a phrase the words themselves date ("the last quarter of 2024") states its
    period, so neither is resolved: an invented period would put a figure the
    page never stated into the report. A phrase used as a *comparison base* is
    not a period either (P2-1).
    """
    key = _date_key(page_date)
    match = _RELATIVE_PERIOD.search(evidence_words or "")
    if key is None or match is None:
        return None
    year, month, _ = key
    if match.group("to_date"):
        return str(year)
    direction = match.group("direction").casefold()
    unit = match.group("period").casefold()
    prefix = (match.group("window") or "").casefold()
    if direction == "last" and (prefix in _DURATION_PREFIXES or (unit == "year" and prefix)):
        return None
    if direction == "last" and _names_a_comparison_base(evidence_words, match):
        return None
    step = _RELATIVE_STEP[direction]
    if unit == "year":
        return str(year + step)
    if unit == "season" or not 1 <= month <= 12:
        return None
    size = _PERIOD_MONTHS[unit]
    start_year, start_month = divmod(((year * 12 + month - 1) // size + step) * size, 12)
    if unit == "quarter":
        return f"Q{start_month // 3 + 1} {start_year}"
    if unit == "half":
        return f"H{start_month // 6 + 1} {start_year}"
    return f"{_MONTH_NAMES[start_month]} {start_year}"


def _value_text(figure: FindingFigure) -> str:
    return f"{figure.value} {figure.unit}"


def _shared_target_words(left_ids: Iterable[str], right_ids: Iterable[str],
                         by_id: Mapping[str, EvidenceTarget]) -> frozenset[str]:
    """The words the two sides' shared targets state, less their articles (fix round 1)."""
    return subject_context(set(left_ids) & set(right_ids), by_id.values())


def _rows_share_a_subject(left: FactRow, right: FactRow,
                          targets: Iterable[EvidenceTarget]) -> bool:
    """Whether two rows are one subject: ``same_subject`` over their shared targets' context.

    The duplicate gate and ``fact_rows`` ask the same question of a pair of rows,
    so they build the context the same way -- words and fields alike (Task 5.6c
    fix round 1).
    """
    shared = set(left.target_ids) & set(right.target_ids)
    return same_subject(left.subject, right.subject,
                        context_words=subject_context(shared, targets),
                        target_fields=_target_fields(shared, targets))


def _figures_share_a_subject(left: VerifiedFigure, right: VerifiedFigure,
                             by_id: Mapping[str, EvidenceTarget]) -> bool:
    """Whether two figures are about the same thing (their shared targets' words)."""
    shared = set(left.finding.target_ids) & set(right.finding.target_ids)
    return same_subject(left.context.subject, right.context.subject,
                        context_words=_shared_target_words(left.finding.target_ids,
                                                          right.finding.target_ids, by_id),
                        target_fields=_target_fields(shared, by_id.values()))


def _answered_targets(figure: VerifiedFigure,
                      targets: Sequence[EvidenceTarget]) -> frozenset[str]:
    """The targets this figure answers: the ids its finding binds and the fields fit.

    The same rule ``fact_rows`` builds a row's own ``target_ids`` with, so what
    two figures share here is exactly the obligation their row would answer.
    """
    return frozenset(
        target.target_id for target in targets
        if target.target_id in figure.finding.target_ids
        and _figure_answers(figure, target, targets)
    )


def _same_fact(left: VerifiedFigure, right: VerifiedFigure,
               by_id: Mapping[str, EvidenceTarget],
               answered: Mapping[tuple[str, int], frozenset[str]] | None = None) -> bool:
    if left.context.kind != right.context.kind:
        return False
    # PD-9's measure family is "the unit dimension plus the target the finding
    # answers" (§5.3), so two figures that each answer a *different* obligation
    # are two facts however equal their values (I6). A figure that answers no
    # target at all -- an unbound extraction, a unit no target asks for --
    # carries no measure to compare and groups as it did.
    targets = list(by_id.values())
    left_ids = _figure_answer_ids(left, targets, answered)
    right_ids = _figure_answer_ids(right, targets, answered)
    if left_ids and right_ids and not left_ids & right_ids:
        return False
    shared = set(left.finding.target_ids) & set(right.finding.target_ids)
    words = _shared_target_words(left.finding.target_ids, right.finding.target_ids, by_id)
    fields = _target_fields(shared, by_id.values())
    if not same_subject(left.context.subject, right.context.subject,
                        context_words=words, target_fields=fields):
        return False
    if not _periods_match(left.context.period, right.context.period,
                          left.context.subject, right.context.subject,
                          context_words=words, target_fields=fields):
        return False
    if not same_organisation(left.context.organisation, right.context.organisation):
        return False
    if left.quantity is not None and right.quantity is not None:
        return same_quantity(left.quantity, right.quantity)
    return cosmetic_text(_value_text(left.figure)) == cosmetic_text(_value_text(right.figure))


def _figure_answer_ids(figure: VerifiedFigure, targets: Sequence[EvidenceTarget],
                       answered: Mapping[tuple[str, int], frozenset[str]] | None) -> frozenset[str]:
    """``_answered_targets`` for one figure, reusing ``fact_rows``' own computation."""
    if answered is None:
        return _answered_targets(figure, targets)
    return answered.get((figure.finding_id, figure.index), frozenset())


def _primary(group: Sequence[VerifiedFigure]) -> VerifiedFigure:
    """§5.3: the organisation's own page ahead of a relay; then the latest release.

    A member that names a subject comes before one that does not (I2): the
    group is one fact, so the row's own member is the one that can say what
    the fact is about, and a row built from a subject-less member would print
    no subject beside a named sibling's row.
    """
    def rank(figure: VerifiedFigure) -> tuple[int, int, tuple[int, int, int]]:
        key = release_key(figure.finding) or (0, 0, 0)
        return (_ATTRIBUTION_RANK[figure.context.attribution],
                0 if figure.context.subject else 1,
                tuple(-part for part in key))  # type: ignore[return-value]
    return min(group, key=rank)


def fact_rows(findings: Sequence[Finding], targets: Sequence[EvidenceTarget]) -> list[FactRow]:
    """§5.3 and PD-9: one row per fact; revisions folded; row ids K001, K002, ..."""
    by_id = {target.target_id: target for target in targets}
    figures = verified_figures(findings)
    # What each figure answers, computed once: the group test and the row's own
    # target ids ask the same question of the same figures (I6).
    answered = {(figure.finding_id, figure.index): _answered_targets(figure, list(targets))
                for figure in figures}
    groups: list[list[VerifiedFigure]] = []
    for figure in figures:
        for group in groups:
            # The first member's test is the BASE rule, so a run with no subjects
            # groups exactly as before; the subject is then checked against every
            # *member*, not only the first, so a subject-less figure cannot act as
            # a wildcard that admits a second subject to one row (fix round 1,
            # IMPORTANT 3).
            if _same_fact(group[0], figure, by_id, answered) and all(
                _figures_share_a_subject(member, figure, by_id) for member in group
            ):
                group.append(figure)
                break
        else:
            groups.append([figure])
    rows: list[FactRow] = []
    row_findings: list[Finding] = []
    for group in groups:
        primary = _primary(group)
        # The row prints one subject, so its obligations are the ones its own
        # subject-bearing members answer (I2). A member with no subject answers
        # every sibling target it fits, because nothing can refuse it -- it
        # cannot say which of two subjects it belongs to -- and letting that
        # ride into a named row answers the other subject's obligation with a
        # row about this one.
        answering = [figure for figure in group if figure.context.subject] or group
        target_ids = sorted({t for figure in answering
                             for t in _figure_answer_ids(figure, list(targets), answered)})
        dimension = primary.quantity.dimension if primary.quantity is not None else None
        measure = next((by_id[t].measure for t in target_ids if by_id[t].measure), None)
        subject = primary.context.subject or next(
            (member.context.subject for member in group if member.context.subject), None
        )
        rows.append(
            FactRow(
                row_id="pending",
                organisation=primary.context.organisation,
                attribution=primary.context.attribution,
                relay_host=publisher_identity(primary.finding.source_url)
                if primary.context.attribution == "relayed" else None,
                subject=subject,
                measure=measure or _MEASURE_BY_DIMENSION.get(dimension or "", "stated figure"),
                period=primary.context.period,
                value=_value_text(primary.figure),
                kind=primary.context.kind,
                scope=primary.context.scope,
                release=release_text(primary.finding),
                period_resolved_from=primary.context.period_resolved_from,
                finding_id=primary.finding_id,
                duplicate_finding_ids=sorted({f.finding_id for f in group} - {primary.finding_id}),
                target_ids=target_ids,
                context_unchecked=primary.unchecked,
            )
        )
        row_findings.append(primary.finding)
    folded = _fold_revisions(list(zip(rows, row_findings)), by_id)
    return [row.model_copy(update={"row_id": f"K{n:03d}"}) for n, row in enumerate(folded, start=1)]


def _same_period_and_subject(left: FactRow, right: FactRow,
                             by_id: Mapping[str, EvidenceTarget]) -> bool:
    """Whether two rows are about one thing in one period (PD-9, ruling N1, fix round 1).

    A fold claims a release history, so the two subjects must name the *same*
    thing: their distinctive words equal once the shared targets' words are
    dropped. "X200" and "X200 Pro" are one mergeable fact but two subjects, so
    they never fold; two rows with no subject at all are still one subject, as
    at BASE, and merely nested spellings ("Acme X200" and "X200") still merge
    under ``_same_fact`` — they just do not earn a release history.
    """
    shared = set(left.target_ids) & set(right.target_ids)
    words = _shared_target_words(left.target_ids, right.target_ids, by_id)
    fields = _target_fields(shared, by_id.values())
    if _subject_words(left.subject) - words != _subject_words(right.subject) - words:
        return False
    return _periods_match(left.period, right.period, left.subject, right.subject,
                          context_words=words, target_fields=fields)


def _fold_revisions(rows: Sequence[tuple[FactRow, Finding]],
                    by_id: Mapping[str, EvidenceTarget]) -> list[FactRow]:
    """PD-9: same organisation, target, period, subject and kind, both released, releases differ.

    Pairs a row with the ``Finding`` its own primary figure came from,
    rather than re-looking it up by ``finding_fingerprint`` afterward: two
    distinct revisions of one page can share a fingerprint (their content text is
    unchanged; only the structured figure and its release differ), so a
    fingerprint-keyed map would collapse them and could never tell which of
    two colliding rows is the later edition.
    """
    kept = list(rows)
    while True:
        pair = next(
            ((a, b) for a in kept for b in kept
             if a is not b and set(a[0].target_ids) & set(b[0].target_ids)
             and a[0].kind == b[0].kind and _same_period_and_subject(a[0], b[0], by_id)
             and same_organisation(a[0].organisation, b[0].organisation)
             and (ka := release_key(a[1])) is not None
             and (kb := release_key(b[1])) is not None and ka > kb),
            None,
        )
        if pair is None:
            return [row for row, _ in kept]
        (latest_row, latest_finding), (earlier_row, _) = pair
        merged = latest_row.model_copy(update={"earlier": sorted(
            [
                *latest_row.earlier,
                EarlierEdition(value=earlier_row.value, release=earlier_row.release, finding_id=earlier_row.finding_id),
                *earlier_row.earlier,
            ],
            key=lambda edition: _date_key(edition.release) or (0, 0, 0),
            reverse=True,
        )})
        kept = [(merged, latest_finding) if row is latest_row else (row, finding)
                for row, finding in kept if row is not earlier_row]


def not_found_targets(
    sub_topics: Sequence[SubTopic],
    answered: Mapping[str, list[str]],
    acquisition: Mapping[str, AcquisitionState],
) -> list[NotFoundTarget]:
    """§6.1 item 5: each required target with no verified finding, and where it was searched."""
    rows: list[NotFoundTarget] = []
    for topic in sub_topics:
        state = acquisition.get(topic.coverage_id)
        for target in topic.evidence_targets:
            if not target.required or target.target_id in answered:
                continue
            pages = list(dict.fromkeys([*(state.read_urls if state else []), *(state.attempted_urls if state else [])]))
            searched = bool(state and (state.attempted_urls or state.read_urls
                                       or state.consecutive_searches or state.empty_searches))
            rows.append(NotFoundTarget(target_id=target.target_id, question=target.question,
                                       queries=list(topic.search_queries), pages_read=pages,
                                       searched=searched))
    return rows

