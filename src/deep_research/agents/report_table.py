"""The question-shaped table (spec §4) — pure, offline table builders.

Decision 1's structural choice rule (§4.1), picked *after* the writer's
statements are checked: an **options table** assembled from option marks on
kept (``consistent``/``corrected``) statements, when a required part on its
own names >= 2 options; else **Key figures** (notes-progress-report spec
§7.4), the verified figures labelled ``item · measure`` and merged per
passage, when >= 2 rows qualify; else no table. No model call ever writes
table text — every word in a cell is either a verbatim span of a checked
sentence (options) or a page-verified field (Key figures).

:func:`build_table` is the one entry point the Report Writer calls
(spec §6.7); :func:`options_table` and :func:`key_figures_table` are exposed
separately because each is independently testable against its own fixture
(spec §14 T2) and each may be asked to build a table the caller then decides
not to use.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import NamedTuple

from deep_research.agents.evidence import cosmetic_text, excerpt_matches
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.sources import normalize_source_url, publisher_identity
from deep_research.agents.verified_facts import same_organisation
from deep_research.utils.types import (
    NOTE_COVERAGE_PREFIX,
    NOTE_TOPIC_TITLE_PREFIX,
    EarlierEdition,
    FactRow,
    Finding,
    ItemMark,
    PageCredit,
    ReportComposition,
    ReportStatement,
    ReportTable,
    SubTopic,
    TableCell,
    TableEntry,
)

__all__ = ["KeyFigureGroup", "build_table", "key_figures_table", "merge_key_figures", "options_table"]

# Row/column bounds (§4.2; notes-progress-report spec §7.4): a table is a
# summary, never the whole log.
MAX_OPTION_ROWS = 8
MAX_OPTION_PART_COLUMNS = 4
MAX_FULL_PAGES_PER_CELL = 2
MAX_KEY_FIGURE_ROWS = 10

_KEPT_VERDICTS = frozenset({"consistent", "corrected"})

# D10: for these answer kinds the question plans no figure target of its
# own -- a figure the researcher happens to find is evidence inside a
# finding, not an obligation -- so a findings-table row only qualifies when
# it answers a target that itself asks for a quantity (§4.3).
_QUANTITY_ONLY_ANSWER_KINDS = frozenset({"explanation", "constraints"})
_OPTIONS_COLUMNS_HEAD = "Option"
_RECOMMENDED_BY_COLUMN = "Recommended by"
_KEY_FIGURE_COLUMNS = ["What", "Figure", "Source"]
#: ``verified_facts.fact_rows``' measure for a figure no planned target or unit names.
_STATED_FIGURE = "stated figure"
#: A note topic's label in the Key figures table: at most this many characters, cut on a
#: word boundary, so a planner-sized measure ("battery storage power capacity added")
#: and a note's subject read alike.
_NOTE_MEASURE_CHARS = 40
#: A value's shape (spec §7.4 item 3): each run of digits, dots and commas reads ``#``.
_VALUE_NUMBERS = re.compile(r"[\d.,]+")
_OPTIONS_CAPTION = (
    "Each cell quotes the report's own sentence about the option in that "
    "part; the whole sentence is in the section of the same name. "
    '"Recommended by" names the sources that pick the option. Options '
    "picked by more sources come first."
)
_POSSESSIVE_PRONOUNS = frozenset({"it", "its", "this", "these", "their", "they"})

_DASH_CLASS = re.compile(r"\s*[-\u2010\u2011\u2012\u2013\u2014\u2015\u2212]\s*")
_WORD = re.compile(r"[a-z0-9]+")


def _norm(url: str) -> str:
    return normalize_source_url(url)


# =============================================================================
# shared: report order, parts, kept marks
# =============================================================================


def _placed_statements(
    composition: ReportComposition,
) -> list[tuple[ReportStatement, str | None]]:
    """Every statement with its own section's coverage id, in report order.

    ``None`` for a bottom-line statement: a bottom-line mark's parts are
    resolved per mark in :func:`_resolve_marks`, from the cited finding whose
    own page is the mark's source, never from the whole statement's finding
    set.
    """
    placed: list[tuple[ReportStatement, str | None]] = []
    for point in composition.summary:
        if point.statement is not None:
            placed.append((point.statement, None))
    for section in composition.sections:
        for point in section.points:
            if point.statement is not None:
                placed.append((point.statement, section.coverage_id))
    return placed


def _part_by_finding(composition: ReportComposition) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for part in composition.parts:
        for finding_id in part.finding_ids:
            mapping.setdefault(finding_id, part.coverage_id)
    return mapping


def _finding_source_urls(composition: ReportComposition) -> dict[str, str]:
    return {
        finding_fingerprint(finding): _norm(finding.source_url)
        for finding in composition.findings
    }


def _required_coverage_ids(composition: ReportComposition) -> set[str]:
    return {
        topic.coverage_id
        for topic in composition.sub_topics
        if any(target.required for target in topic.evidence_targets)
    }


def _option_key(name: str) -> str:
    """§4.2: the row key — case, whitespace and dash variants folded (cosmetic_text plus dash-class collapse)."""
    return _DASH_CLASS.sub("-", cosmetic_text(name))


class _ResolvedMark(NamedTuple):
    """One valid mark, tied to the parts it counts toward (§4.2's "a statement's parts")."""

    statement_id: str
    mark: ItemMark
    parts: frozenset[str]
    order: int
    relay: tuple[str, str] | None
    """(credited_body, relay_publisher) when the mark's backing finding reports
    another body's judgement through this page; ``None`` when the page speaks
    for itself (P1-1)."""


def _relay_credit(
    finding: Finding | None, page_credits: Mapping[str, PageCredit], url: str
) -> tuple[str, str] | None:
    """P1-1: (credited_body, relay_publisher) when ``finding`` reports another
    body's judgement relayed through ``url``'s page; ``None`` when the page
    speaks for itself. Never credits the relay alone: a page whose own words
    hand a pick or verdict to a named body ('according to Wirecutter, as
    reported by Business Insider') must credit that body, with the relay
    named beside it.
    """
    if finding is None:
        return None
    page_publisher = _page_publisher(page_credits, url) or publisher_identity(url)
    attributed = (finding.attributed_issuer or "").strip()
    if attributed and not same_organisation(attributed, page_publisher):
        return attributed, page_publisher
    if finding.verification is not None:
        for result in finding.verification.figure_results:
            if not result.kept or result.context is None:
                continue
            if result.context.attribution == "relayed" and not same_organisation(
                result.context.organisation, page_publisher
            ):
                return result.context.organisation, page_publisher
    return None


def _mark_relay_credit(
    mark: ItemMark,
    statement: ReportStatement,
    mark_url: str,
    page_credits: Mapping[str, PageCredit],
    finding_by_id: Mapping[str, Finding],
) -> tuple[str, str] | None:
    """R-2: the relay credit for one mark, decided by the finding it actually
    rests on — never by whichever finding happens to share its page.

    Prefers ``mark.finding_id`` (the fingerprint the draft's ``by`` resolved
    to, set by the writer's ``_apply_marks``). When it is unset (older data,
    or a mark the writer left unresolved), falls back to the statement's own
    cited findings on this page: when they all agree (the same credited
    body, or all the page's own), that shared reading applies; a genuine
    disagreement never guesses which one the mark rests on, so the page's
    own reading applies instead.
    """
    if mark.finding_id:
        return _relay_credit(finding_by_id.get(mark.finding_id), page_credits, mark_url)
    candidates = [
        finding_by_id[fid]
        for fid in statement.finding_ids
        if fid in finding_by_id and _norm(finding_by_id[fid].source_url) == mark_url
    ]
    if not candidates:
        return None
    credits = {
        _relay_credit(candidate, page_credits, mark_url) for candidate in candidates
    }
    if len(credits) == 1:
        return next(iter(credits))
    return None  # mixed citations: never guess which one the mark rests on


def _resolve_marks(
    composition: ReportComposition,
) -> tuple[list[_ResolvedMark], list[str]]:
    """Kept statements' valid marks, in report order, each tied to its own part(s).

    Three checks before a mark counts (§4.2 last bullet): the statement is
    kept (``consistent``/``corrected``, never ``unchecked``); ``name`` and
    ``verdict`` are verbatim spans of the statement's final text; and the
    mark's own ``source_url`` is the page of one of the findings the
    statement cites — a mark cannot credit a page the sentence never rested
    on, and a statement that cites no known finding at all fails this check
    too (a mark trusts nothing it cannot check against). A mark failing
    either source check is dropped; the drop messages are returned rather
    than written to ``composition.dropped_marks`` directly (P3-1), so a
    caller invoked more than once on the same composition merges instead of
    duplicating them.

    A section statement's marks count toward its own section's part. A
    bottom-line statement can cite findings from more than one part; a mark
    there counts only toward the parts of the findings whose own page is the
    mark's ``source_url`` — never every part the statement happens to cite —
    so a source's verdict never shows under a criterion it did not judge.
    """
    part_by_finding = _part_by_finding(composition)
    finding_url = _finding_source_urls(composition)
    finding_by_id = _finding_by_id(composition)
    resolved: list[_ResolvedMark] = []
    dropped: list[str] = []
    order = 0
    for statement, own_section in _placed_statements(composition):
        verdict = composition.statement_verdicts.get(statement.statement_id, "")
        if verdict not in _KEPT_VERDICTS:
            continue
        cited_urls = {
            finding_url[fid] for fid in statement.finding_ids if fid in finding_url
        }
        for mark in statement.items:
            if not excerpt_matches(statement.text, mark.name):
                continue
            if mark.verdict and not excerpt_matches(statement.text, mark.verdict):
                continue
            mark_url = _norm(mark.source_url)
            if not cited_urls:
                dropped.append(
                    f"{statement.statement_id}: '{mark.name}' credits a page, but "
                    "the statement cites no finding this report carries"
                )
                continue
            if mark_url not in cited_urls:
                dropped.append(
                    f"{statement.statement_id}: '{mark.name}' credits a page "
                    "the statement does not cite"
                )
                continue
            if own_section is not None:
                parts = frozenset({own_section})
            else:
                parts = frozenset(
                    part_by_finding[fid]
                    for fid in statement.finding_ids
                    if fid in part_by_finding and finding_url.get(fid) == mark_url
                )
            relay = _mark_relay_credit(
                mark, statement, mark_url, composition.page_credits, finding_by_id
            )
            resolved.append(
                _ResolvedMark(statement.statement_id, mark, parts, order, relay)
            )
            order += 1
    return resolved, dropped


def _merge_dropped_marks(
    composition: ReportComposition, dropped: Sequence[str]
) -> None:
    """P3-1: merge without duplicating, so calling a builder twice on the same
    composition cannot double-record the same drop."""
    for message in dropped:
        if message not in composition.dropped_marks:
            composition.dropped_marks.append(message)


def _required_part_option_counts(
    resolved_marks: Sequence[_ResolvedMark], required: set[str]
) -> dict[str, set[str]]:
    counts: dict[str, set[str]] = {}
    for resolved in resolved_marks:
        for part in resolved.parts:
            if part not in required:
                continue
            counts.setdefault(part, set()).add(_option_key(resolved.mark.name))
    return counts


# =============================================================================
# the choice rule (§4.1)
# =============================================================================


def build_table(composition: ReportComposition) -> ReportTable | None:
    """§4.1: options when one required part alone marks >= 2 options; else Key figures when >= 2 rows qualify; else none.

    The >= 2 test applies per required part (consistent with the column
    rule, §4.2): two different required parts each marking one distinct
    option do not qualify, since neither part alone names a comparison. If
    the gate passes but the resulting options table still has fewer than 2
    rows (every marked option's only cell lay outside the parts that
    ultimately qualified as columns), this falls through to Key figures
    instead of publishing a near-empty options table.
    """
    resolved_marks, dropped = _resolve_marks(composition)
    _merge_dropped_marks(composition, dropped)
    required = _required_coverage_ids(composition)
    per_part = _required_part_option_counts(resolved_marks, required)
    if any(len(keys) >= 2 for keys in per_part.values()):
        table = _build_options_table(resolved_marks, composition)
        if table is not None and len(table.rows) >= 2:
            return table
    table = key_figures_table(composition)
    if table is not None and len(table.rows) >= 2:
        return table
    return None


# =============================================================================
# §4.2 options table
# =============================================================================


def _section_titles(composition: ReportComposition) -> dict[str, str]:
    return {
        section.coverage_id: section.title
        for section in composition.sections
        if section.coverage_id
    }


def _part_title(
    coverage_id: str, composition: ReportComposition, section_titles: Mapping[str, str]
) -> str:
    if coverage_id in section_titles:
        return section_titles[coverage_id]
    for part in composition.parts:
        if part.coverage_id == coverage_id:
            return part.sub_topic_title
    return coverage_id


def _dedup_join(verdicts: Sequence[str]) -> str:
    seen: set[str] = set()
    parts: list[str] = []
    for verdict in verdicts:
        if not verdict:
            continue
        key = cosmetic_text(verdict)
        if key in seen:
            continue
        seen.add(key)
        parts.append(verdict)
    return "; ".join(parts)


def _cell_pages(
    resolved_marks: Sequence[_ResolvedMark],
    part_cid: str,
    option_key: str,
) -> tuple["OrderedDict[str, list[str]]", dict[str, tuple[str, str] | None], list[str]]:
    pages: OrderedDict[str, list[str]] = OrderedDict()
    relays: dict[str, tuple[str, str] | None] = {}
    statement_ids: list[str] = []
    for resolved in resolved_marks:
        if part_cid not in resolved.parts:
            continue
        if _option_key(resolved.mark.name) != option_key:
            continue
        url = _norm(resolved.mark.source_url)
        pages.setdefault(url, []).append(resolved.mark.verdict)
        if resolved.relay is not None:
            relays[url] = resolved.relay
        else:
            relays.setdefault(url, None)
        if resolved.statement_id not in statement_ids:
            statement_ids.append(resolved.statement_id)
    return pages, relays, statement_ids


def _part_cell(
    resolved_marks: Sequence[_ResolvedMark],
    part_cid: str,
    option_key: str,
) -> TableCell:
    pages, relays, statement_ids = _cell_pages(resolved_marks, part_cid, option_key)
    entries: list[TableEntry] = []
    for index, (url, verdicts) in enumerate(pages.items()):
        if index >= MAX_FULL_PAGES_PER_CELL:
            entries.append(TableEntry(text="", source_url=url))
            continue
        text = _dedup_join(verdicts)
        relay = relays.get(url)
        if relay is not None and text:
            # P1-1: never credit the relay alone — name the body whose
            # judgement this is, with the relay page still cited by its marker.
            text = f"{text}, according to {relay[0]}"
        entries.append(TableEntry(text=text, source_url=url))
    return TableCell(entries=entries, statement_ids=statement_ids)


def _recommended_by_cell(
    resolved_marks: Sequence[_ResolvedMark],
    option_key: str,
    page_credits: Mapping[str, PageCredit],
) -> TableCell:
    pages: OrderedDict[str, tuple[str, str] | None] = OrderedDict()
    statement_ids: list[str] = []
    for resolved in resolved_marks:
        if _option_key(resolved.mark.name) != option_key or not resolved.mark.picked:
            continue
        url = _norm(resolved.mark.source_url)
        if resolved.relay is not None:
            pages[url] = resolved.relay
        else:
            pages.setdefault(url, None)
        if resolved.statement_id not in statement_ids:
            statement_ids.append(resolved.statement_id)
    entries = [
        TableEntry(
            text=f"{relay[0]}, reported by {relay[1]}" if relay is not None else "",
            source_url=url,
            date=page_credits[url].date if url in page_credits else None,
        )
        for url, relay in pages.items()
    ]
    return TableCell(entries=entries, statement_ids=statement_ids)


def _distinct_pick_pages(
    resolved_marks: Sequence[_ResolvedMark], option_key: str
) -> int:
    pages: set[str] = set()
    for resolved in resolved_marks:
        if _option_key(resolved.mark.name) == option_key and resolved.mark.picked:
            pages.add(_norm(resolved.mark.source_url))
    return len(pages)


def _build_options_table(
    resolved_marks: Sequence[_ResolvedMark], composition: ReportComposition
) -> ReportTable:
    required = _required_coverage_ids(composition)
    plan_order = [topic.coverage_id for topic in composition.sub_topics]

    option_labels: dict[str, str] = {}
    option_first_order: dict[str, int] = {}
    part_option_keys: dict[str, set[str]] = {}
    for resolved in resolved_marks:
        key = _option_key(resolved.mark.name)
        if key not in option_labels:
            option_labels[key] = resolved.mark.name
            option_first_order[key] = resolved.order
        for part in resolved.parts:
            part_option_keys.setdefault(part, set()).add(key)

    included_parts = [cid for cid, keys in part_option_keys.items() if len(keys) >= 2]

    def _plan_index(cid: str) -> int:
        return plan_order.index(cid) if cid in plan_order else len(plan_order)

    included_parts.sort(key=lambda cid: (cid not in required, _plan_index(cid)))
    included_parts = included_parts[:MAX_OPTION_PART_COLUMNS]

    section_titles = _section_titles(composition)
    columns = (
        [_OPTIONS_COLUMNS_HEAD]
        + [_part_title(cid, composition, section_titles) for cid in included_parts]
        + [_RECOMMENDED_BY_COLUMN]
    )

    rows_data: list[tuple[str, list[TableCell], int, int, int]] = []
    for key, label in option_labels.items():
        row_cells = [TableCell(text=label)]
        non_empty = 0
        for cid in included_parts:
            cell = _part_cell(resolved_marks, cid, key)
            if cell.entries:
                non_empty += 1
            row_cells.append(cell)
        recommended_cell = _recommended_by_cell(
            resolved_marks, key, composition.page_credits
        )
        if non_empty == 0 and not recommended_cell.entries:
            continue  # §4.2: a row needs >= 1 non-empty cell
        row_cells.append(recommended_cell)
        pick_pages = _distinct_pick_pages(resolved_marks, key)
        rows_data.append(
            (label, row_cells, pick_pages, non_empty, option_first_order[key])
        )

    rows_data.sort(key=lambda row: (-row[2], -row[3], row[4]))

    total = len(rows_data)
    capped = rows_data[:MAX_OPTION_ROWS]
    caption = _OPTIONS_CAPTION
    if total > MAX_OPTION_ROWS:
        caption = (
            f"{caption} Showing {MAX_OPTION_ROWS} of {total} options; "
            "the rest are in the sections below."
        )

    return ReportTable(
        shape="options",
        columns=columns,
        rows=[row[1] for row in capped],
        caption=caption,
    )


def options_table(composition: ReportComposition) -> ReportTable:
    """§4.2: the table code assembles from option marks, the parts and the page credits.

    Builds from *every* kept, valid mark regardless of its part's required
    flag — the structural gate that decides whether an options table is used
    at all lives in :func:`build_table` (§4.1); this function only shapes
    whatever marks exist.
    """
    resolved_marks, dropped = _resolve_marks(composition)
    _merge_dropped_marks(composition, dropped)
    return _build_options_table(resolved_marks, composition)


# =============================================================================
# §4.3's eligible figures, as notes-progress-report spec §7.4's Key figures
# =============================================================================


def _finding_by_id(composition: ReportComposition) -> dict[str, Finding]:
    return {finding_fingerprint(finding): finding for finding in composition.findings}


def _row_and_duplicate_ids(row: FactRow) -> set[str]:
    return {row.finding_id, *row.duplicate_finding_ids}


def _row_finding_ids(row: FactRow) -> list[str]:
    """Every finding a number in this row comes from, the row's own first (marker/audit ids)."""
    return list(
        dict.fromkeys(
            [row.finding_id, *(edition.finding_id for edition in row.earlier)]
        )
    )


def _cited_finding_ids(
    composition: ReportComposition, *, bottom_line_only: bool = False
) -> set[str]:
    cited: set[str] = set()
    statements: Sequence[ReportStatement]
    if bottom_line_only:
        statements = [
            point.statement
            for point in composition.summary
            if point.statement is not None
        ]
    else:
        statements = composition.statements
    for statement in statements:
        if (
            composition.statement_verdicts.get(statement.statement_id, "")
            not in _KEPT_VERDICTS
        ):
            continue
        cited.update(statement.finding_ids)
    return cited


def _explicitly_answers_required(
    row: FactRow,
    finding_by_id: Mapping[str, Finding],
    required_target_ids: set[str],
) -> bool:
    """§6.13: "answers a required target" means an explicit binding on the row's own finding(s)."""
    for fid in _row_and_duplicate_ids(row):
        finding = finding_by_id.get(fid)
        if finding is not None and set(finding.target_ids) & required_target_ids:
            return True
    return False


def _quantity_target_ids(composition: ReportComposition) -> set[str]:
    """D10: targets that ask for a quantity -- an ``EvidenceTarget`` with
    ``unit_dimension`` set; ``None`` marks a qualitative target."""
    return {
        target.target_id
        for topic in composition.sub_topics
        for target in topic.evidence_targets
        if target.unit_dimension is not None
    }


def _row_eligible(
    row: FactRow,
    finding_by_id: Mapping[str, Finding],
    required_target_ids: set[str],
    cited: set[str],
    quantity_target_ids: set[str] | None = None,
) -> bool:
    if row.context_unchecked:
        return False
    if quantity_target_ids is not None:
        # D10: for explanation/constraints answers, a row qualifies only
        # when it answers a target that asks for a quantity -- being cited,
        # or bound to a qualitative target (even a required one), no longer
        # qualifies it for these kinds.
        return _explicitly_answers_required(row, finding_by_id, quantity_target_ids)
    if _explicitly_answers_required(row, finding_by_id, required_target_ids):
        return True
    return bool(_row_and_duplicate_ids(row) & cited)


def _row_priority(
    row: FactRow,
    finding_by_id: Mapping[str, Finding],
    required_target_ids: set[str],
    bottom_line_cited: set[str],
) -> int:
    """§4.3's cap priority: a row that explicitly answers a required target
    first, then one the bottom line cites, then the rest."""
    if _explicitly_answers_required(row, finding_by_id, required_target_ids):
        return 0
    if _row_and_duplicate_ids(row) & bottom_line_cited:
        return 1
    return 2


def _words(text: str) -> set[str]:
    return set(_WORD.findall(cosmetic_text(text)))


def _words_present(candidate: str, text: str) -> bool:
    wanted = _words(candidate)
    if not wanted:
        return True
    return wanted <= _words(text)


def _earlier_edition_date(
    edition: EarlierEdition, finding_by_id: Mapping[str, Finding]
) -> str | None:
    """§4.3: the earlier edition's own release date, else its statement date — never the unverified vintage."""
    earlier_finding = finding_by_id.get(edition.finding_id)
    if earlier_finding is None:
        return None
    return earlier_finding.release_date or earlier_finding.statement_date


def _result_text(
    row: FactRow, mixed_kinds: bool, finding_by_id: Mapping[str, Finding]
) -> str:
    text = row.value
    if mixed_kinds:
        text = f"{text}, {'actual' if row.kind == 'actual' else 'forecast'}"
    for edition in row.earlier:
        date = _earlier_edition_date(edition, finding_by_id)
        text = (
            f"{text}; earlier: {edition.value} ({date})"
            if date
            else f"{text}; earlier: {edition.value}"
        )
    return text


def _page_publisher(page_credits: Mapping[str, PageCredit], url: str) -> str | None:
    credit = page_credits.get(_norm(url))
    return credit.publisher if credit is not None else None


def _when_text(finding: Finding | None, kind: str) -> str:
    if finding is not None and finding.release_date:
        return f" (released {finding.release_date})"
    if finding is not None and finding.statement_date:
        return f" (stated {finding.statement_date})"
    if kind == "forecast":
        return " (no release date given)"
    return ""


def _who_name(
    row: FactRow, finding: Finding | None, page_credits: Mapping[str, PageCredit]
) -> str:
    """Who a row's figure is credited to, as the Source column names it, its date left off."""
    source_url = finding.source_url if finding is not None else ""
    host = publisher_identity(source_url) if source_url else ""
    credited = _page_publisher(page_credits, source_url) if source_url else None
    if row.attribution == "own":
        org = row.organisation
        if not org or same_organisation(org, host):
            org = credited or host
        return org
    if row.attribution == "relayed":
        return f"{row.organisation}, reported by {credited or (row.relay_host or '')}"
    return credited or host  # unattributed


def _who_text(
    row: FactRow, finding: Finding | None, page_credits: Mapping[str, PageCredit]
) -> str:
    return f"{_who_name(row, finding, page_credits)}{_when_text(finding, row.kind)}"


def _label_source(
    row: FactRow, finding: Finding | None, page_credits: Mapping[str, PageCredit]
) -> str:
    """D40: the source that reported a row, for the label of a row with no named
    item -- the organisation a relayed figure is credited to, else the name the
    Source column prints (the publisher, or the page's own site)."""
    if row.attribution == "relayed" and row.organisation:
        return row.organisation
    return _who_name(row, finding, page_credits)


@dataclass(frozen=True)
class KeyFigureGroup:
    """One merged Key figures row (notes-progress-report spec §7.4 item 3): the
    values one passage states about one item, under one label. ``rows`` holds
    every fact row merged here, in row order; ``shown`` the ones whose values
    print -- a row whose value repeats a shown one only adds its finding ids."""

    label: str
    rows: tuple[FactRow, ...]
    shown: tuple[FactRow, ...]

    @property
    def values(self) -> str:
        return " \u00b7 ".join(row.value for row in self.shown)


def _capitalised(text: str) -> str:
    """``text`` with its first letter upper-cased -- unless its second letter
    already is, as in a name written ``iJava`` or ``eBay``, whose case stands."""
    if len(text) > 1 and text[1].isupper():
        return text
    return text[:1].upper() + text[1:]


def _note_topic_measure(topic: SubTopic) -> str:
    """Final review P3-1: a research note's own targets carry the note's whole question as
    their measure (``reader_notes._note_topic``), which is no label for a table cell. Its
    topic's title without the "Your note: " prefix names the same subject: whitespace
    normalised, trailing punctuation dropped, cut at ``_NOTE_MEASURE_CHARS`` on a word
    boundary. ``""`` when nothing is left."""
    text = " ".join(topic.title.split()).removeprefix(NOTE_TOPIC_TITLE_PREFIX).rstrip(" .?!:;,")
    if len(text) > _NOTE_MEASURE_CHARS:
        cut = text[: _NOTE_MEASURE_CHARS + 1]
        boundary = cut.rfind(" ")
        text = (cut[:boundary] if boundary > 0 else text[:_NOTE_MEASURE_CHARS]).rstrip(" .?!:;,")
    return text


def _key_figure_measure(row: FactRow, composition: ReportComposition) -> str:
    """Spec §7.4 item 2: read the row by the sub-topic owning the most of its
    planned targets (the earlier in plan order on a tie), and take that
    sub-topic's first such target in plan order whose ``unit_dimension`` is
    set, else its first such target; a row answering no planned target keeps
    ``row.measure``. ``row.measure`` itself is the first answered target in
    sorted id order (``verified_facts.fact_rows``), which can name another
    sub-topic's measure. A note's own topic (``note-{id}``) is labelled by its
    short title instead (``_note_topic_measure``); the target keeps its measure."""
    wanted = set(row.target_ids)
    owning = [
        (index, [target for target in topic.evidence_targets if target.target_id in wanted])
        for index, topic in enumerate(composition.sub_topics)
    ]
    owning = [(index, targets) for index, targets in owning if targets]
    if not owning:
        return row.measure
    index, targets = max(owning, key=lambda pair: (len(pair[1]), -pair[0]))
    topic = composition.sub_topics[index]
    if topic.coverage_id.startswith(NOTE_COVERAGE_PREFIX):
        short = _note_topic_measure(topic)
        if short:
            return short
    quantity = next((target for target in targets if target.unit_dimension is not None), None)
    return (quantity or targets[0]).measure


def _key_figure_label(
    row: FactRow, composition: ReportComposition, finding_by_id: Mapping[str, Finding]
) -> str | None:
    """Spec §7.4 item 2, as D40 amends it: ``{Item} \u00b7 {measure}``, the item
    being the row's subject unless it starts with a pronoun; a row with no named
    item is labelled by the source that reported it, ``{Source} \u00b7 {Measure}``
    (``_label_source``), so figures from different findings keep separate rows --
    ``{Measure}`` alone only when no source can be named. Then ``, {period}``
    when the label does not already say it. ``None`` for a row with no item whose
    measure is only "stated figure": such a row is not eligible. Never a quoted
    snippet."""
    subject = " ".join((row.subject or "").split())
    item = "" if subject and cosmetic_text(subject).split()[0] in _POSSESSIVE_PRONOUNS else subject
    measure = " ".join(_key_figure_measure(row, composition).split())
    if not item and cosmetic_text(measure) == _STATED_FIGURE:
        return None
    if item:
        label = f"{_capitalised(item)} \u00b7 {measure}"
    else:
        finding = finding_by_id.get(row.finding_id)
        source = " ".join(_label_source(row, finding, composition.page_credits).split())
        label = f"{source} \u00b7 {_capitalised(measure)}" if source else _capitalised(measure)
    if row.period and not _words_present(row.period, label):
        label = f"{label}, {row.period}"
    return label


def _value_shape(value: str) -> str:
    return cosmetic_text(_VALUE_NUMBERS.sub("#", value))


def merge_key_figures(rows: Sequence[FactRow], composition: ReportComposition) -> list[KeyFigureGroup]:
    """Spec §7.4 items 2-3, before eligibility and the cap: label each row (D40:
    a row with no named item by the source that reported it), and merge the
    values one passage states about one item.

    Rows sharing (label, primary finding, kind) form a group. Within a group, a
    row whose value equals a shown value (``cosmetic_text``) only joins that
    merged row; any other joins the first merged row holding no value of its
    shape, or starts a new one -- so "4.2 of 5 bubbles" and "87 reviews" from
    one passage merge, while two ratings never share a row. Rows from two
    passages never merge. Merged rows come back in the order their first rows
    appear; a row ``_key_figure_label`` refuses is left out.
    """
    finding_by_id = _finding_by_id(composition)
    merged: list[tuple[str, list[FactRow], list[FactRow]]] = []
    positions_by_group: dict[tuple[str, str, str], list[int]] = {}
    for row in rows:
        label = _key_figure_label(row, composition, finding_by_id)
        if label is None:
            continue
        positions = positions_by_group.setdefault((label, row.finding_id, row.kind), [])
        same = next(
            (p for p in positions
             if any(cosmetic_text(shown.value) == cosmetic_text(row.value) for shown in merged[p][2])),
            None,
        )
        if same is not None:
            merged[same][1].append(row)
            continue
        shape = _value_shape(row.value)
        slot = next(
            (p for p in positions if all(_value_shape(shown.value) != shape for shown in merged[p][2])),
            None,
        )
        if slot is None:
            merged.append((label, [], []))
            slot = len(merged) - 1
            positions.append(slot)
        merged[slot][1].append(row)
        merged[slot][2].append(row)
    return [KeyFigureGroup(label=label, rows=tuple(all_rows), shown=tuple(shown)) for label, all_rows, shown in merged]


def _group_display_order(
    groups: Sequence[KeyFigureGroup], composition: ReportComposition
) -> list[KeyFigureGroup]:
    """Each merged row in the plan order of its first row's part, then by row id."""
    part_by_finding = _part_by_finding(composition)
    plan_order = [topic.coverage_id for topic in composition.sub_topics]

    def key(group: KeyFigureGroup) -> tuple[int, str]:
        part = part_by_finding.get(group.rows[0].finding_id)
        index = plan_order.index(part) if part in plan_order else len(plan_order)
        return (index, group.rows[0].row_id)

    return sorted(groups, key=key)


def key_figures_table(composition: ReportComposition) -> ReportTable | None:
    """Notes-progress-report spec §7.4: the eligible verified figures (§4.3's
    rule, ``_row_eligible``) labelled and merged, one row per label, at most
    ``MAX_KEY_FIGURE_ROWS``, with the What / Figure / Source columns; ``None``
    when fewer than 2 fact rows qualify.

    The cap keeps the merged rows whose best fact row ranks first by §4.3's
    priority (``_row_priority``; stable within a rank); of merged rows sharing
    a label only the first by that priority prints -- the table cannot tell
    them apart, and a repeated label with different figures reads as a
    contradiction (D36) -- and every other stays in the evidence log.
    """
    required_target_ids = {
        target.target_id
        for topic in composition.sub_topics
        for target in topic.evidence_targets
        if target.required
    }
    finding_by_id = _finding_by_id(composition)
    cited = _cited_finding_ids(composition)
    bottom_line_cited = _cited_finding_ids(composition, bottom_line_only=True)
    quantity_target_ids = (
        _quantity_target_ids(composition)
        if composition.answer_kind in _QUANTITY_ONLY_ANSWER_KINDS
        else None
    )
    eligible = [
        row
        for row in composition.fact_rows
        if _row_eligible(row, finding_by_id, required_target_ids, cited, quantity_target_ids)
    ]
    groups = merge_key_figures(eligible, composition)
    eligible_count = sum(len(group.rows) for group in groups)
    if eligible_count < 2:
        return None

    def priority(group: KeyFigureGroup) -> int:
        return min(
            _row_priority(row, finding_by_id, required_target_ids, bottom_line_cited)
            for row in group.rows
        )

    labels: set[str] = set()
    distinct: list[KeyFigureGroup] = []
    for group in sorted(groups, key=priority):  # stable: appearance order within a rank
        if group.label not in labels:
            labels.add(group.label)
            distinct.append(group)
    displayed = _group_display_order(distinct[:MAX_KEY_FIGURE_ROWS], composition)

    kinds = {group.rows[0].kind for group in displayed}
    mixed = len(kinds) > 1
    kind_caption = ""
    if not mixed and kinds:
        only_kind = next(iter(kinds))
        if only_kind == "actual":
            kind_caption = "No figure in this table is a forecast."
        elif only_kind == "forecast":
            kind_caption = "Every figure in this table is a forecast."

    rows: list[list[TableCell]] = []
    for group in displayed:
        first = group.rows[0]
        row_ids = [row.row_id for row in group.rows]
        finding_ids = list(dict.fromkeys(fid for row in group.rows for fid in _row_finding_ids(row)))
        figure = " \u00b7 ".join(_result_text(row, mixed, finding_by_id) for row in group.shown)
        who = _who_text(first, finding_by_id.get(first.finding_id), composition.page_credits)
        rows.append([
            TableCell(text=group.label, row_ids=row_ids, finding_ids=finding_ids),
            TableCell(text=figure, row_ids=row_ids, finding_ids=finding_ids),
            TableCell(text=who, row_ids=row_ids, finding_ids=finding_ids),
        ])

    shown_count = sum(len(group.rows) for group in displayed)
    caption = kind_caption
    if shown_count < eligible_count:
        caption = f"Showing {shown_count} of {eligible_count} verified figures; all are in the evidence log."
        if kind_caption:
            caption = f"{caption} {kind_caption}"
    return ReportTable(
        shape="findings", columns=list(_KEY_FIGURE_COLUMNS), rows=rows, caption=caption
    )
