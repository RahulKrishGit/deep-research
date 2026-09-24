"""Facts from verified findings (spec §5.3, §6.1, §6.4, §6.6).

Deterministic and field-driven: target answering, duplicates and revisions,
the Not found list and number tracing read verified fields and structured
figures only. Nothing here parses a finding's ``content``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from deep_research.agents.evidence import cosmetic_text
from deep_research.agents.figures import (
    Quantity,
    bare_numbers,
    parse_figure,
    quantities_in,
    same_quantity,
)
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.sources import publisher_identity
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
_ATTRIBUTION_RANK = {"own": 0, "relayed": 1, "unattributed": 2}


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
    text = value.replace("U.S.", "US").replace("U.K.", "UK").replace("&", " and ")
    return _WORD.findall(text)


def _core(tokens: Sequence[str]) -> list[str]:
    return [t.casefold() for t in tokens if t.casefold() not in _COUNTRY_WORDS | _CONNECTORS]


def _initials(tokens: Sequence[str]) -> str:
    kept = [t for t in tokens if t.casefold() not in _COUNTRY_WORDS | _CONNECTORS]
    if len(kept) > 1 and kept[-1].casefold() in _LEGAL_SUFFIXES:
        kept = kept[:-1]
    return "".join(t.casefold() if t.isupper() and len(t) > 1 else t[0].casefold() for t in kept)


def _single_token(value: str) -> str | None:
    """The one token a host label or a one-word name stands for, else ``None``."""
    text = value.strip().casefold()
    if _HOST.fullmatch(text):
        label, _, suffix = publisher_identity(f"https://{text}").partition(".")
        return label if suffix.rsplit(".", 1)[-1] in _NAMEABLE_SUFFIXES else None
    tokens = _tokens(value)
    return tokens[0].casefold() if len(tokens) == 1 else None


def same_organisation(left: str, right: str) -> bool:
    """Whether two organisation names, acronyms or hosts name one organisation."""
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
        joined = "".join(_core(tokens))
        if token in {_initials(tokens), joined} or (len(token) >= 4 and joined.startswith(token)):
            return True
    return False


def _period_key(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    words = [w for w in re.findall(r"[a-z0-9]+", cosmetic_text(value)) if w not in _PERIOD_FILLER]
    return " ".join(words) or None


def same_period(left: str | None, right: str | None) -> bool:
    """Equal after cosmetic normalisation, or both the same bare year."""
    key = _period_key(left)
    return key is not None and key == _period_key(right)


def _figure_answers(figure: VerifiedFigure, target: EvidenceTarget) -> bool:
    return (
        figure.quantity is not None
        and figure.quantity.dimension == target.unit_dimension
        and (target.period is None or same_period(figure.context.period, target.period))
        and (target.kind is None or figure.context.kind == target.kind)
        and (target.organisation is None or same_organisation(target.organisation, figure.context.organisation))
    )


def _finding_organisations(finding: Finding) -> list[str]:
    names = [figure.context.organisation for figure in verified_figures([finding])]
    if finding.attributed_issuer:
        names.append(finding.attributed_issuer)
    names.append(publisher_identity(finding.source_url))
    return names


def finding_answers(finding: Finding, target: EvidenceTarget) -> bool:
    """§6.6, plus PD-7 for a target with no unit dimension."""
    if finding.verification is None or finding.verification.status == "dropped":
        return False
    if target.target_id not in finding.target_ids:
        return False
    if target.unit_dimension is None:
        return target.organisation is None or any(
            same_organisation(target.organisation, name) for name in _finding_organisations(finding)
        )
    return any(_figure_answers(figure, target) for figure in verified_figures([finding]))


def answered_target_ids(
    findings: Sequence[Finding], targets: Sequence[EvidenceTarget]
) -> dict[str, list[str]]:
    """Target id -> the ids of the findings that answer it (answered targets only)."""
    answered: dict[str, list[str]] = {}
    for target in targets:
        ids = [finding_fingerprint(f) for f in findings if finding_answers(f, target)]
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


def _value_text(figure: FindingFigure) -> str:
    return f"{figure.value} {figure.unit}"


def _same_fact(left: VerifiedFigure, right: VerifiedFigure) -> bool:
    if left.context.kind != right.context.kind:
        return False
    if not same_period(left.context.period, right.context.period):
        return False
    if not same_organisation(left.context.organisation, right.context.organisation):
        return False
    if left.quantity is not None and right.quantity is not None:
        return same_quantity(left.quantity, right.quantity)
    return cosmetic_text(_value_text(left.figure)) == cosmetic_text(_value_text(right.figure))


def _primary(group: Sequence[VerifiedFigure]) -> VerifiedFigure:
    """§5.3: the organisation's own page ahead of a relay; then the latest release."""
    def rank(figure: VerifiedFigure) -> tuple[int, tuple[int, int, int]]:
        key = release_key(figure.finding) or (0, 0, 0)
        return (_ATTRIBUTION_RANK[figure.context.attribution], tuple(-part for part in key))  # type: ignore[return-value]
    return min(group, key=rank)


def fact_rows(findings: Sequence[Finding], targets: Sequence[EvidenceTarget]) -> list[FactRow]:
    """§5.3 and PD-9: one row per fact; revisions folded; row ids K001, K002, ..."""
    by_id = {target.target_id: target for target in targets}
    groups: list[list[VerifiedFigure]] = []
    for figure in verified_figures(findings):
        for group in groups:
            if _same_fact(group[0], figure):
                group.append(figure)
                break
        else:
            groups.append([figure])
    rows: list[FactRow] = []
    row_findings: list[Finding] = []
    for group in groups:
        primary = _primary(group)
        target_ids = sorted(
            {t for figure in group for t in figure.finding.target_ids
             if t in by_id and _figure_answers(figure, by_id[t])}
        )
        dimension = primary.quantity.dimension if primary.quantity is not None else None
        measure = next((by_id[t].measure for t in target_ids if by_id[t].measure), None)
        rows.append(
            FactRow(
                row_id="pending",
                organisation=primary.context.organisation,
                attribution=primary.context.attribution,
                relay_host=publisher_identity(primary.finding.source_url)
                if primary.context.attribution == "relayed" else None,
                measure=measure or _MEASURE_BY_DIMENSION.get(dimension or "", "stated figure"),
                period=primary.context.period,
                value=_value_text(primary.figure),
                kind=primary.context.kind,
                scope=primary.context.scope,
                release=release_text(primary.finding),
                finding_id=primary.finding_id,
                duplicate_finding_ids=sorted({f.finding_id for f in group} - {primary.finding_id}),
                target_ids=target_ids,
                context_unchecked=primary.unchecked,
            )
        )
        row_findings.append(primary.finding)
    folded = _fold_revisions(list(zip(rows, row_findings)))
    return [row.model_copy(update={"row_id": f"K{n:03d}"}) for n, row in enumerate(folded, start=1)]


def _fold_revisions(rows: Sequence[tuple[FactRow, Finding]]) -> list[FactRow]:
    """PD-9: same organisation, target, period and kind, both released, releases differ.

    Pairs a row with the ``Finding`` its own primary figure came from, rather
    than re-looking it up by ``finding_fingerprint`` afterward: two distinct
    revisions of one page can share a fingerprint (their content text is
    unchanged; only the structured figure and its release differ), so a
    fingerprint-keyed map would collapse them and could never tell which of
    two colliding rows is the later edition.
    """
    kept = list(rows)
    while True:
        pair = next(
            ((a, b) for a in kept for b in kept
             if a is not b and set(a[0].target_ids) & set(b[0].target_ids)
             and a[0].kind == b[0].kind and same_period(a[0].period, b[0].period)
             and same_organisation(a[0].organisation, b[0].organisation)
             and (ka := release_key(a[1])) is not None
             and (kb := release_key(b[1])) is not None and ka > kb),
            None,
        )
        if pair is None:
            return [row for row, _ in kept]
        (latest_row, latest_finding), (earlier_row, _) = pair
        merged = latest_row.model_copy(update={"earlier": [
            *latest_row.earlier,
            EarlierEdition(value=earlier_row.value, release=earlier_row.release, finding_id=earlier_row.finding_id),
            *earlier_row.earlier,
        ]})
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


def untraced_numbers(text: str, cited: Sequence[Finding]) -> list[str]:
    """§6.4: the numbers ``text`` states that no kept figure of ``cited`` carries."""
    figures = verified_figures(cited)
    known = [f.quantity for f in figures if f.quantity is not None and f.quantity.base is not None]
    literal = {
        cosmetic_text(f.figure.value).replace(",", "").replace(" ", "")
        for f in figures if f.quantity is None or f.quantity.base is None
    }
    # ``quantities_in``'s offsets index ``cosmetic_text(text)`` (casefolded), so
    # slicing the *original* text at those offsets -- not ``q.value_text``/
    # ``q.unit_text`` -- is what keeps the page's own capitalisation ("GW",
    # not "gw") in a number the report has to trace back to what was written.
    untraced = [
        text[q.start:q.end].strip() for q in quantities_in(text)
        if not any(same_quantity(q, k) for k in known)
    ]
    untraced.extend(number for number in bare_numbers(text) if number not in literal)
    return untraced
