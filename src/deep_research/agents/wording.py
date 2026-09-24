"""The wording rules the Evidence Verifier and the Report Writer share: hedges,
forecast versus outcome, attested names, years and scopes (PD-19).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

# What a factual assertion introduces that a paraphrase does not. A statement
# may reword its evidence freely — that is what prose is for — but a figure or
# a named entity the evidence does not carry is a new fact, and a new fact is
# not something a report may assert on its own authority.
#
# The figure pattern captures the number alone; ``_significant_figures``
# attaches a following word only when that word is a recognised unit, so a
# figure token can never swallow the next ordinary word ("1,200 in total" is
# the figure 1,200, not the token "1,200 in").
_FIGURE_PATTERN = re.compile(r"\d[\d,.'\u2019]*")
# A capitalised word that is not an acronym is a name candidate only when it
# is not the first word of a sentence: a sentence opener is capitalised by
# position, and "Charge" is indistinguishable from "California" without an
# English lexicon. The alternative — a stopword list that has to contain every
# word a sentence may open with — is not a list anyone can audit. The accepted
# cost is that a sentence-initial MIXED-case place name in prose ("California
# added capacity") stays undetected; Task 10 carries that residual.
_PROPER_NOUN_PATTERN = re.compile(r"\b[A-Z][A-Za-z][\w'-]*\b")
# An acronym is the exception to the position exemption, because no ordinary
# sentence opener is all-caps: "EU" is a name in the middle of a sentence and
# at the start of one, and the four-character floor that used to hide it was a
# length rule standing in for a case rule.
_ACRONYM_PATTERN = re.compile(r"[A-Z]{2,}")
# What opens a sentence, for the position exemption: the start of the text, a
# terminator, a line-initial bullet or blockquote marker, or an opening quote
# or bracket. A dash *inside* a line is not an opener — an ordinary word after
# an em-dash is lowercase, so a capitalised word there is a name — and neither
# is a closing bracket or the apostrophe inside a word.
_SENTENCE_INITIAL = re.compile(
    r"(?:^|[.!?:]\s+"
    r"|(?:^|\n)\s*[-*>\u2013\u2014]\s+"
    r"|(?:^|\s)[\u201c\u2018\"'(\[]\s*)$"
)


def _is_significant_figure(token: str) -> bool:
    """True when a numeric token asserts a quantity rather than numbers a word.

    "890 GW", "12", "2025" and "0.76" are measurements: each carries a unit, a
    separator, or at least two digits. A bare single digit is a label — a list
    position, an ordinal, the full stop after a sentence — and treating one as
    an unsupported figure would refuse prose for its numbering rather than for
    its content.
    """
    stripped = token.strip().strip(".,'")
    if not stripped:
        return False
    if re.search(r"[A-Za-z%]", stripped):
        return True
    digits = re.sub(r"[^\d]", "", stripped)
    if len(digits) >= 2:
        return True
    return bool(re.search(r"[.,']", stripped))


def _figure_number(token: str) -> str:
    """The numeric value of a figure token, without its unit."""
    match = re.match(r"[\d,.']+", token)
    return (match.group(0) if match else token).strip(".,'")


def _significant_figures(text: str) -> list[str]:
    """Every figure token that asserts a quantity, with its unit attached.

    A unit is attached only when the next word is one this contract knows, so
    "890 GW" is one token while "1,200 in total" is the figure 1,200 followed
    by prose — which is what keeps a malformed token out of a disposition.
    """
    figures: list[str] = []
    for match in _FIGURE_PATTERN.finditer(text):
        number = match.group(0).strip(".,'\u2019")
        if not number:
            continue
        unit = re.match(r"\s+([A-Za-z%][A-Za-z%/-]*)", text[match.end() :])
        token = number
        if unit and unit.group(1).casefold() in _UNIT_WORDS:
            token = f"{number} {unit.group(1)}"
        if _is_significant_figure(token) and token not in figures:
            figures.append(token)
    return figures


# The modality markers a claim may carry and a statement may not drop. A
# statement may reword its evidence; it may not out-assert it. The audited
# report turned "capacity growth from battery storage could set a record" into
# "would set a record" and published the source's own uncertainty as a fact.
#
# Verb and adverb forms only. The noun uses of the same words hedge nothing —
# "the EIA battery storage forecast identifies its data vintage" is a title,
# not an uncertainty — and reading them as hedges refused correct statements
# whose point also cited a forecast for its citation. "preliminary" is absent
# for the same reason: it qualifies a document's title ("Preliminary Monthly
# Electric Generator Inventory"), not an assertion.
_HEDGE_PATTERN = re.compile(
    r"\b(?:could|might|possibly|potentially|perhaps|likely|unlikely|expects?|"
    r"expected|anticipates?|anticipated|projected|planned|intends?|"
    r"intended|suggests?|suggested|implies|implied|appears?|seems?|reportedly|"
    r"allegedly|estimates?|estimated|approximately|roughly)\b",
    re.IGNORECASE,
)
# The reporting verbs whose *verb* use hedges a statement and whose noun use
# names a document. "EIA forecasts 18.2 GW will be added" hedges; "EIA's
# February 24, 2025 forecast of 18.2 GW" is a citation label, and reading it as
# a hedge let the audited "would set a record" through the short-circuit. The
# lookahead is the discriminator: a reporting verb introduces a clause or a
# figure, a label is followed by a noun.
_HEDGE_REPORTING_PATTERN = re.compile(
    r"\b(?:forecasts?|projects?|predicts?)\b(?=\s+(?:that\b|\d))",
    re.IGNORECASE,
)
# ``may`` is the one hedge that collides with a month name, so it is matched
# on its own and refused when a date follows it.
_HEDGE_MAY_PATTERN = re.compile(r"\bmay\b(?!\s+\d)", re.IGNORECASE)

# The modal verbs a statement asserts with when it states as settled what its
# evidence hedged. Compared against the cited evidence, because a claim can
# have hardened its source already: the audited claim said "would" where the
# page said "could", so the claim alone cannot witness the loss.
_STRONG_MODALS = ("would", "will")

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


def clause_around(text: str, position: int) -> str:
    """The clause of ``text`` containing the character at ``position``."""
    start = 0
    for match in _CLAUSE_SPLIT.finditer(text):
        if match.start() > position:
            return text[start : match.start()]
        start = match.end()
    return text[start:]


# The words a capitalised token may be without naming anything: function words
# and connectives that a report's own argument is carried by. Kept to that
# role on purpose — the position exemption in ``unattested_atoms`` is what
# keeps ordinary sentence openers safe, and a list grown to cover every word a
# sentence may open with would make each of those words freely fabricable.
_ATTESTATION_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "from", "into", "than",
        "then", "they", "their", "there", "these", "those", "have", "has",
        "had", "was", "were", "are", "is", "be", "been", "being", "not",
        "but", "its", "it's", "also", "such", "when", "which", "while",
        "would", "could", "should", "must", "may", "might", "can", "will",
        "about", "after", "before", "between", "during", "each", "every",
        "more", "most", "other", "some", "only", "over", "under", "very",
    }
)


def _content_tokens(text: str) -> list[str]:
    """The words a claim to be *about something* is carried by."""
    return [
        token
        for token in re.findall(r"[a-z][a-z0-9'-]{2,}", text.casefold())
        if token not in _ATTESTATION_STOPWORDS
    ]


def _corpus_tokens(corpus: str) -> set[str]:
    """The words and figures the corpus carries, as whole tokens.

    Whole tokens, not substrings: "1200 hectares" does not attest "12", and a
    substring test would let one measured figure vouch for a different one
    that happens to share its digits. The trailing separators are stripped so
    the corpus side and ``_figure_number`` agree: an excerpt is an exact
    sentence of a source document, so its figures very often end one. Only the
    ends are stripped, so "1,200" keeps its internal separator and stays
    distinct from "1200".
    """
    tokens = {
        token.strip(".,'-\u2019")
        for token in re.findall(r"[a-z0-9][a-z0-9'.,\u2019-]*", corpus.casefold())
    }
    tokens.discard("")
    return tokens


def unattested_names(text: str, corpus: str, raw_corpus: str = "") -> list[str]:
    """The proper names ``text`` states that ``corpus`` does not: the name half of
    ``unattested_atoms``, for sentences whose figures are checked by the
    structured-figure rule instead (spec §6.2)."""
    tokens = _corpus_tokens(corpus)
    found: list[str] = []
    for match in _PROPER_NOUN_PATTERN.finditer(text):
        token = match.group(0)
        # A sentence-opening capitalised word is capitalised by position: this
        # checker cannot tell "Charge" from "California" without a lexicon, so
        # the position exemption stays. An acronym is the exception, because
        # no ordinary sentence opener is all-caps. The acronym is read without
        # its possessive ("IEA's", "SEIA's"), and a word with an internal
        # capital ("BloombergNEF") is a name wherever it stands.
        base = re.sub(r"['\u2019]s$", "", token)
        if (
            _SENTENCE_INITIAL.search(text[: match.start()])
            and not _ACRONYM_PATTERN.fullmatch(base)
            and not re.search(r"[a-z][A-Z]", base)
        ):
            continue
        folded = token.casefold()
        # A unit is not a name: "GW" in a statement whose evidence spells out
        # "gigawatts" is an abbreviation of the same measured quantity, and the
        # figure it belongs to has already been checked against that evidence.
        # A common abbreviation is not a name for the same reason: the domain
        # writes "EVs" where its evidence writes "electric vehicles".
        if folded in _UNIT_WORDS or folded in _COMMON_ABBREVIATIONS:
            continue
        if (
            not _name_attested(token, tokens, raw_corpus or corpus)
            and token not in found
        ):
            found.append(token)
    return found


def unattested_atoms(text: str, corpus: str, raw_corpus: str = "") -> list[str]:
    """Specific factual atoms the corpus does not carry.

    Figures and names are what a paraphrase does not invent and a fabrication
    does. An unattested ordinary word is prose; an unattested figure or proper
    noun is a new fact, and this is the deterministic half of the support
    review — the half that cannot be argued with.

    ``raw_corpus``, when given, is the *case-preserved* text the acronym
    check reads: ``corpus`` itself is usually casefolded already by its
    caller, and a folded corpus can never carry a capitalised name run.
    Omitted, the acronym check falls back to ``corpus`` as given, which is
    strict rather than permissive when that text has already lost its case.
    """
    tokens = _corpus_tokens(corpus)
    found: list[str] = []
    for token in _significant_figures(text):
        number = _figure_number(token)
        if number and number not in tokens and token not in found:
            found.append(token)
    return found + unattested_names(text, corpus, raw_corpus)


# Words an organisation's or a place's initials skip: "Energy Information
# Administration" is EIA, and "United States of America" is USA.
_INITIAL_SKIP = frozenset({"of", "and", "the", "for", "on", "in", "&"})


# A leading word a name run may drop without adding to its own initials:
# "U.S. Energy Information Administration" spells EIA, not UEIA, because the
# leading "U.S." names the country the agency belongs to, not a word of the
# agency's own name. Kept narrow, on the review's own example, rather than
# generalised to "any leading word may be dropped" — that would let a run
# spell an acronym it never wrote by discarding whichever word makes it fit.
_COUNTRY_PREFIXES = frozenset({"us", "u.s", "uk", "u.k"})
# Punctuation stripped from a run word's ends before it is compared as a
# whole word: a token carries the sentence's own comma or closing period, and
# neither is part of the word.
_NAME_PUNCT = ".,;:!?()[]{}\u2019'\""


def _is_title_word(word: str) -> bool:
    """True when ``word``'s first letter is capitalised."""
    match = re.search(r"[A-Za-z]", word)
    return bool(match) and match.group(0).isupper()


def _run_initial(word: str) -> str:
    """The initial letter of one name-run word ("U.S." spells "U")."""
    match = re.search(r"[A-Za-z]", word)
    return match.group(0).upper() if match else ""


def _name_runs(corpus: str) -> list[list[str]]:
    """Maximal runs of capitalised words in case-preserved ``corpus`` text.

    Tokenised on whitespace, so "U.S." stays one token and contributes one
    initial rather than splitting into the two letters "u" and "s" — the
    split that let a claim spelling "U.S. Energy Information Administration"
    also spell the invented "SEIA". A connector from ``_INITIAL_SKIP``
    bridges two capitalised words without ending the run ("United States of
    America"); any other lowercase word ends it, and so does a sentence
    carrying no capitalisation at all — a lowercase phrase such as "installed
    energy additions" is never a name run, however its initials happen to
    fall.
    """
    runs: list[list[str]] = []
    current: list[str] = []
    pending: list[str] = []
    for token in corpus.split():
        bare = token.strip(_NAME_PUNCT).casefold()
        if _is_title_word(token):
            current.extend(pending)
            current.append(token)
            pending = []
        elif bare in _INITIAL_SKIP and current:
            pending.append(token)
        else:
            if current:
                runs.append(current)
            current, pending = [], []
    if current:
        runs.append(current)
    return runs


def _run_words(run: Sequence[str]) -> list[str]:
    """A run's words, connectors dropped: they spell no initial of their own."""
    return [
        word for word in run if word.strip(_NAME_PUNCT).casefold() not in _INITIAL_SKIP
    ]


def _spelled_out(acronym: str, corpus: str) -> bool:
    """True when case-preserved ``corpus`` writes the name ``acronym`` spells.

    Matched only against a contiguous span inside one maximal run of
    capitalised words — never a whole sentence — so a lowercase phrase such
    as "installed energy additions" carries no name at all, however its
    initials happen to fall. "U.S. Energy Information Administration" spells
    EIA, and only EIA once a leading country token ("U.S.") is dropped from
    the search, never the "SEIA" the old letter-only scan read out of a split
    "u"/"s"; the span search (rather than requiring the whole run) is what
    still finds EIA when the run runs on into an adjacent capitalised word —
    a claim's own "... Administration's March 12, 2025 analysis" sweeps the
    month into the run, and the name it spells does not move for that.
    """
    wanted = acronym.upper()
    size = len(wanted)
    if size == 0:
        return False
    for run in _name_runs(corpus):
        words = _run_words(run)
        searched = [words]
        if words and words[0].strip(_NAME_PUNCT).casefold() in _COUNTRY_PREFIXES:
            searched.append(words[1:])
        for candidate in searched:
            for start in range(len(candidate) - size + 1):
                span = candidate[start : start + size]
                if "".join(_run_initial(word) for word in span) == wanted:
                    return True
    return False


def _name_attested(token: str, tokens: set[str], corpus: str) -> bool:
    """Whether the corpus states this name, in any form it writes names in.

    The audited pass refused "EIA's", "Monitor's" and "EIA-based" although
    the claims said "U.S. Energy Information Administration", "Energy Storage
    Monitor" and "citing EIA": a possessive and a "-based" compound name the
    same body, and an acronym is the body its evidence spells out. Every
    capitalised part of a compound still has to be attested, so an invented
    "IEA-based" or "Wood Mackenzie's" is refused as before.
    """
    # The evidence writing the name whole ("short-term", "year-in-review",
    # "eia's") attests it before any part of it is looked at.
    if token.casefold() in tokens:
        return True
    base = re.sub(r"['\u2019]s$", "", token)
    if base.casefold() in tokens:
        return True
    parts = [part for part in base.split("-") if part[:1].isupper()] or [base]
    for part in parts:
        folded = part.casefold()
        if folded in tokens:
            continue
        if _ACRONYM_PATTERN.fullmatch(part) and _spelled_out(part, corpus):
            continue
        return False
    return True


def unattested_words(text: str, corpus: str) -> list[str]:
    """Every content word of a short cell the corpus does not carry.

    Whole tokens, for the same reason the figures use them: a substring test
    lets "generation" vouch for "gen", and a cell is meant to be lifted from
    the evidence rather than composed. A mechanism or a geography nobody's
    evidence states is the uncited cell this check exists to repair.
    """
    tokens = _corpus_tokens(corpus)
    return [token for token in _content_tokens(text) if token not in tokens]


def hedge_marker(text: str) -> str:
    """The first modality marker a text carries, or ``""`` for none."""
    match = (
        _HEDGE_PATTERN.search(text)
        or _HEDGE_REPORTING_PATTERN.search(text)
        or _HEDGE_MAY_PATTERN.search(text)
    )
    return match.group(0).casefold() if match else ""


def hardened_modality(text: str, corpus: str) -> str:
    """The uncertainty the cited evidence states and a statement hardens away.

    Read from the *evidence*, not from the claim: a claim can have hardened
    its source already — the audited claim said "would set a record" where the
    page said "could set a record" — so the claim's own wording cannot witness
    what was lost. The statement is refused only when it asserts with a strong
    modal what the evidence hedged; a statement that carries the evidence's own
    uncertainty, or hedges in any other way, states no more than it was shown.
    """
    if not hedge_marker(corpus):
        return ""
    for modal in _STRONG_MODALS:
        for match in re.finditer(rf"\b{modal}\b", text, re.IGNORECASE):
            # The exemption is the modal's own clause. A statement that
            # reports a figure ("EIA forecast 18.2 GW …") and then asserts an
            # outcome in the next clause ("which would set a record") hedged
            # nothing about that outcome, and exempting the whole statement on
            # the reporting verb published the audited hardening.
            if hedge_marker(clause_around(text, match.start())):
                continue
            return modal
    return ""


# The units a figure may carry, so that removing an unsupported figure takes
# its unit with it and nothing else. "890 GW" is one phrase; "2030 horizon" is
# a year and a noun, and the noun has to survive the repair.
_UNIT_WORDS = frozenset(
    {
        "gw", "gws", "mw", "mws", "kw", "kws", "tw", "gwh", "mwh", "kwh",
        "kwh/y", "gw/y", "mw/y", "twh", "km", "km2", "sq", "kg", "t", "mt",
        "kt", "bn", "million", "billion", "trillion", "usd", "eur", "gbp",
        "dollars", "euros", "pounds", "percent", "%", "pct", "tonnes",
        "tons", "jobs", "units", "seconds", "minutes", "hours", "days",
        "weeks", "months", "years", "people", "households", "vehicles",
    }
)

# Abbreviations that name a technology, a quantity or a common noun rather
# than a place or an organisation. Auditable on purpose: an entry here is a
# token the name check will never refuse, so each one has to be defensible —
# and the domain's own vocabulary (EVs, PV, CO2, GDP, HVDC, PPAs) is written
# this way constantly, while the evidence spells it out. Plurals are listed
# beside their singular for the same reason: "GHGs" is the same noun as "ghg".
#
# "UK", "EU", "US" and "IEA" are deliberately absent: catching a place or an
# agency the evidence never names is what the acronym check is for.
#
# This carve-out is for *prose*. A table cell is checked by
# ``unattested_words`` too, which reads "EVs" as a content word, so a cell
# still has to be lifted from the evidence rather than abbreviated — the cell
# path is the one place the wording is copied rather than composed.
_COMMON_ABBREVIATIONS = frozenset(
    {
        "ai", "ac", "api", "bess", "bevs", "cagr", "capex", "ccs", "ccus",
        "ch4", "co2", "covid", "dc", "ders", "ev", "evs", "gdp", "ghg",
        "ghgs", "gpus", "hvdc", "ice", "ict", "iot", "lcoe", "llms", "lng",
        "ml", "ndcs", "nox", "ok", "opex", "phevs", "ppa", "ppas", "pv",
        "smrs", "tsos", "it",
    }
)


_FORECAST_MARKER_PATTERN = re.compile(
    r"\b(?:plan|plans|planned|planning|project|projects|projected|"
    r"projection|projections|forecast|forecasts|forecasted|forecasting|"
    r"expect|expects|expected|anticipate|anticipates|anticipated)\b",
    re.IGNORECASE,
)


def _forecast_role(text: str) -> bool:
    """True when the text reads as a plan, projection, or forecast — not a
    stated outcome. Broader than ``hedge_marker``'s own pattern on purpose:
    "carried the projection ... plans to add" and "planned to add" are the
    same forecast role in different words, and a text that reads either way
    must never be matched against an outcome that merely shares its figure
    and year — one issuer's own forecast is not its own later actual.
    """
    return bool(_FORECAST_MARKER_PATTERN.search(text))


# A past-tense, realised-outcome verb. Matched only outside a future or
# conditional modal's own clause ("would be installed" still names a plan,
# not a report of what happened) via ``clause_around``, the same governing
# scope ``hardened_modality`` reads a strong modal's exemption from.
_REALIZED_OUTCOME_PATTERN = re.compile(
    r"\b(?:installed|added|deployed|commissioned|came\s+online|built|"
    r"reached|hit|beat|exceeded|surpassed)\b",
    re.IGNORECASE,
)
_FUTURE_MODAL_PATTERN = re.compile(
    r"\b(?:would|will|could|might|may|should|shall)\b", re.IGNORECASE
)


def _realized_outcome(text: str) -> bool:
    """True when the text reports something that already happened.

    A matched verb inside a clause a future or conditional modal governs is
    still a plan ("EIA forecast that 16 GW would be installed"), not a
    report of an outcome, so it does not count. Nor does a verb read as an
    infinitive complement ("expected to hit 15 GW", "expected to be added"):
    "hit" and "beat" spell their infinitive and past-tense forms identically,
    and a verb right after "to" (optionally "to be" or "to have been") is
    what a forecast is expected *to do*, not a report that it did it. Mirrors
    ``claim_clusters.py``'s own to-infinitive guard for the same ambiguity,
    so the two classifiers agree.
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
    outcome = _realized_outcome(text)
    if forecast and outcome:
        return "mixed"
    return "forecast" if forecast else "actual"


_YEAR_TOKEN = re.compile(r"\b(?:19|20)\d{2}\b")


def stated_years(text: str) -> list[str]:
    """The four-digit years ``text`` names, in order, once each."""
    return list(dict.fromkeys(_YEAR_TOKEN.findall(text)))


# Question-independent segment and basis words (spec §6.2: "no ... scope that the
# cited findings' verified fields do not carry"). Longest first.
SCOPE_TERMS: tuple[str, ...] = (
    "commercial and industrial", "front-of-the-meter", "behind-the-meter",
    "utility-scale", "grid-scale", "all segments", "all sectors", "residential",
    "commercial", "industrial", "distributed", "community", "c&i",
)


def stated_scopes(text: str) -> list[str]:
    """The scope terms ``text`` states, hyphen and space spellings alike."""
    folded = " ".join(text.casefold().replace("-", " ").split())
    found: list[str] = []
    for term in SCOPE_TERMS:
        pattern = re.escape(term.replace("-", " "))
        if re.search(rf"(?<![a-z&]){pattern}(?![a-z])", folded) and term not in found:
            found.append(term)
    return found


_PAST_PASSIVE = re.compile(r"\b(was|were|has been|have been)\s+(added|installed|deployed|commissioned|built)\b", re.I)
_PAST_ACTIVE = re.compile(r"\b(added|installed|deployed|commissioned|built|reached|hit|exceeded|surpassed)\b", re.I)
_BASE_FORM = {"added": "add", "installed": "install", "deployed": "deploy", "commissioned": "commission",
              "built": "build", "reached": "reach", "hit": "hit", "exceeded": "exceed", "surpassed": "surpass"}


_WILL_WOULD = re.compile(r"\b(will|would)\b", re.I)
# Lower case only: "May" in "May 2025" is a month, not a hedge.
_PAGE_MODAL = re.compile(r"\b(could|might|may)\b")


def page_modal(text: str) -> str:
    """The first hedging modal the page's own words use ("could"), or ""."""
    match = _PAGE_MODAL.search(text)
    return match.group(1) if match else ""


def hedge_forecast(text: str, organisation: str, *, marker: str = "") -> str:
    """Spec §6.2: re-attach the verified hedge to a forecast stated as fact, once.

    ``marker`` is the page's own modal for the figure (``page_modal`` of its
    evidence words). A hardened "will"/"would" takes that modal, else "is
    expected to" (F3); a past-tense outcome verb becomes an expectation.
    """
    if _WILL_WOULD.search(text):
        modal = marker if marker in {"could", "might", "may"} else "is expected to"
        return _WILL_WOULD.sub(modal, text)

    def passive(match: re.Match[str]) -> str:
        plural = match.group(1).casefold() in {"were", "have been"}
        return f"{'are' if plural else 'is'} expected to be {match.group(2)}"

    def active(match: re.Match[str]) -> str:
        if re.search(r"\bbe\s+\Z", match.string[: match.start()], re.I):
            return match.group(0)
        return f"expected to {_BASE_FORM[match.group(1).casefold()]}"

    hedged = _PAST_ACTIVE.sub(active, _PAST_PASSIVE.sub(passive, text))
    if not _forecast_role(hedged):
        hedged = f"{hedged.rstrip().rstrip('.')}, according to {organisation}'s forecast."
    return hedged
