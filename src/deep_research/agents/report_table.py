"""The question-shaped table (spec §4) — pure, offline table builders.

Decision 1's structural choice rule (§4.1), picked *after* the writer's
statements are checked: an **options table** assembled from option marks on
kept (``consistent``/``corrected``) statements, when a required part on its
own names >= 2 options; else a **findings table** of verified figures, when
>= 2 qualify; else no table. No model call ever writes table text — every
word in a cell is either a verbatim span of a checked sentence (options) or a
page-verified field (findings).

:func:`build_table` is the one entry point the Report Writer calls
(spec §6.7); :func:`options_table` and :func:`findings_table` are exposed
separately because each is independently testable against its own fixture
(spec §14 T2) and each may be asked to build a table the caller then decides
not to use.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from typing import NamedTuple

from deep_research.agents.evidence import cosmetic_text, excerpt_matches
from deep_research.agents.figures import parse_figure, quantities_in, same_quantity
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.sources import normalize_source_url, publisher_identity
from deep_research.agents.verified_facts import (
    same_organisation,
    same_period,
    same_subject,
)
from deep_research.utils.types import (
    EarlierEdition,
    FactRow,
    FigureResult,
    Finding,
    ItemMark,
    PageCredit,
    ReportComposition,
    ReportStatement,
    ReportTable,
    TableCell,
    TableEntry,
)

__all__ = ["build_table", "options_table", "findings_table"]

# Row/column bounds (§4.2, §4.3): a table is a summary, never the whole log.
MAX_OPTION_ROWS = 8
MAX_OPTION_PART_COLUMNS = 4
MAX_FULL_PAGES_PER_CELL = 2
MAX_FINDING_ROWS = 12

_KEPT_VERDICTS = frozenset({"consistent", "corrected"})
_OPTIONS_COLUMNS_HEAD = "Option"
_RECOMMENDED_BY_COLUMN = "Recommended by"
_FINDINGS_COLUMNS = [
    "What was measured",
    "Result",
    "Who reported it (and when)",
    "Source",
]
_OPTIONS_CAPTION = (
    "Each cell quotes the report's own sentence about the option in that "
    "part; the whole sentence is in the section of the same name. "
    '"Recommended by" names the sources that pick the option. Options '
    "picked by more sources come first."
)
_POSSESSIVE_PRONOUNS = frozenset({"it", "its", "this", "these", "their", "they"})
_QUOTE_CLAMP_CHARS = 140

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
    finding_by_url = {_norm(f.source_url): f for f in composition.findings}
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
            relay = _relay_credit(
                finding_by_url.get(mark_url), composition.page_credits, mark_url
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
    """§4.1: options when one required part alone marks >= 2 options; else findings when >= 2 qualify; else none.

    The >= 2 test applies per required part (consistent with the column
    rule, §4.2): two different required parts each marking one distinct
    option do not qualify, since neither part alone names a comparison. If
    the gate passes but the resulting options table still has fewer than 2
    rows (every marked option's only cell lay outside the parts that
    ultimately qualified as columns), this falls through to the findings
    table instead of publishing a near-empty options table.
    """
    resolved_marks, dropped = _resolve_marks(composition)
    _merge_dropped_marks(composition, dropped)
    required = _required_coverage_ids(composition)
    per_part = _required_part_option_counts(resolved_marks, required)
    if any(len(keys) >= 2 for keys in per_part.values()):
        table = _build_options_table(resolved_marks, composition)
        if table is not None and len(table.rows) >= 2:
            return table
    table = findings_table(composition)
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
# §4.3 findings table
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


def _row_eligible(
    row: FactRow,
    finding_by_id: Mapping[str, Finding],
    required_target_ids: set[str],
    cited: set[str],
) -> bool:
    if row.context_unchecked:
        return False
    if _explicitly_answers_required(row, finding_by_id, required_target_ids):
        return True
    return bool(_row_and_duplicate_ids(row) & cited)


def _select_rows(
    eligible: Sequence[FactRow],
    finding_by_id: Mapping[str, Finding],
    required_target_ids: set[str],
    bottom_line_cited: set[str],
) -> list[FactRow]:
    if len(eligible) <= MAX_FINDING_ROWS:
        return list(eligible)

    def priority(row: FactRow) -> int:
        if _explicitly_answers_required(row, finding_by_id, required_target_ids):
            return 0
        if _row_and_duplicate_ids(row) & bottom_line_cited:
            return 1
        return 2

    ordered = sorted(
        eligible, key=priority
    )  # stable: keeps original order within a priority group
    return ordered[:MAX_FINDING_ROWS]


def _display_order(
    rows: Sequence[FactRow], composition: ReportComposition
) -> list[FactRow]:
    part_by_finding = _part_by_finding(composition)
    plan_order = [topic.coverage_id for topic in composition.sub_topics]

    def key(row: FactRow) -> tuple[int, str]:
        part = part_by_finding.get(row.finding_id)
        index = plan_order.index(part) if part in plan_order else len(plan_order)
        return (index, row.row_id)

    return sorted(rows, key=key)


def _words(text: str) -> set[str]:
    return set(_WORD.findall(cosmetic_text(text)))


def _words_present(candidate: str, text: str) -> bool:
    wanted = _words(candidate)
    if not wanted:
        return True
    return wanted <= _words(text)


def _kept_figure_result(finding: Finding, row: FactRow) -> FigureResult | None:
    """The finding's own kept figure that states ``row``'s value (mirrors report.py's context lookup)."""
    if finding.verification is None:
        return None
    stated = quantities_in(row.value)
    for result in finding.verification.figure_results:
        if not result.kept:
            continue
        quantity = parse_figure(result.figure.value, result.figure.unit)
        if quantity is not None and any(
            same_quantity(quantity, other) for other in stated
        ):
            return result
        if cosmetic_text(
            f"{result.figure.value} {result.figure.unit}"
        ) == cosmetic_text(row.value):
            return result
    return None


def _clamp_around_value(words: str, value: str) -> str:
    """§4.3: clamped to 140 characters at word boundaries around the value."""
    if len(words) <= _QUOTE_CLAMP_CHARS:
        return words
    anchor = 0
    lead = (value or "").strip().split()
    if lead:
        found = cosmetic_text(words).find(cosmetic_text(lead[0]))
        if found >= 0:
            anchor = found
    half = _QUOTE_CLAMP_CHARS // 2
    start = max(0, anchor - half)
    end = min(len(words), anchor + half)
    if start > 0:
        next_space = words.find(" ", start)
        if 0 <= next_space < anchor:
            start = next_space + 1
    if end < len(words):
        prev_space = words.rfind(" ", 0, end)
        if prev_space > anchor:
            end = prev_space
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(words) else ""
    return f"{prefix}{words[start:end].strip()}{suffix}"


def _quoted_form(finding: Finding | None, row: FactRow) -> str:
    result = _kept_figure_result(finding, row) if finding is not None else None
    words = (result.evidence_words if result is not None else None) or row.value
    return f'"{_clamp_around_value(words, row.value)}"'


def _has_rival(row: FactRow, table_rows: Sequence[FactRow]) -> bool:
    """§4.3: same kind, matching periods (or both empty), compatible subjects, a different value."""
    for other in table_rows:
        if other is row:
            continue
        if row.kind != other.kind:
            continue
        periods_match = same_period(row.period, other.period) or (
            not row.period and not other.period
        )
        if not periods_match:
            continue
        if not same_subject(row.subject, other.subject):
            continue
        if row.value == other.value:
            continue
        return True
    return False


def _period_resolved_from_basis(row: FactRow, finding: Finding | None) -> str:
    """P3-3: name the actual basis a relative period was resolved from, rather
    than always saying "the page's date" — a resolved period may specifically
    be counted from the finding's own admitted release or statement date."""
    if finding is not None and row.period_resolved_from:
        if finding.release_date and row.period_resolved_from == finding.release_date:
            return "the release date"
        if (
            finding.statement_date
            and row.period_resolved_from == finding.statement_date
        ):
            return "the statement date"
    return "the page's date"


def _what_was_measured(row: FactRow, finding: Finding | None, rival: bool) -> str:
    subject = (row.subject or "").strip()
    starts_with_pronoun = (
        bool(subject) and cosmetic_text(subject).split()[0] in _POSSESSIVE_PRONOUNS
    )
    if not subject or starts_with_pronoun or rival:
        return _quoted_form(finding, row)
    text = subject[0].upper() + subject[1:]
    if row.scope and not _words_present(row.scope, text):
        text = f"{text} ({row.scope})"
    if row.period:
        if row.period_resolved_from:
            basis = _period_resolved_from_basis(row, finding)
            text = f"{text}, {row.period} (counted from {basis}, {row.period_resolved_from})"
        elif not _words_present(row.period, text):
            text = f"{text}, {row.period}"
    return text


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


def _who_text(
    row: FactRow, finding: Finding | None, page_credits: Mapping[str, PageCredit]
) -> str:
    source_url = finding.source_url if finding is not None else ""
    host = publisher_identity(source_url) if source_url else ""
    credited = _page_publisher(page_credits, source_url) if source_url else None
    if row.attribution == "own":
        org = row.organisation
        if not org or same_organisation(org, host):
            org = credited or host
        who = org
    elif row.attribution == "relayed":
        who = f"{row.organisation}, reported by {credited or (row.relay_host or '')}"
    else:  # unattributed
        who = credited or host
    return f"{who}{_when_text(finding, row.kind)}"


def findings_table(composition: ReportComposition) -> ReportTable | None:
    """§4.3: eligible verified figures, capped and ordered, or ``None`` when fewer than 2 qualify."""
    required_target_ids = {
        target.target_id
        for topic in composition.sub_topics
        for target in topic.evidence_targets
        if target.required
    }
    finding_by_id = _finding_by_id(composition)
    cited = _cited_finding_ids(composition)
    bottom_line_cited = _cited_finding_ids(composition, bottom_line_only=True)

    eligible = [
        row
        for row in composition.fact_rows
        if _row_eligible(row, finding_by_id, required_target_ids, cited)
    ]
    if len(eligible) < 2:
        return None

    total = len(eligible)
    selected = _select_rows(
        eligible, finding_by_id, required_target_ids, bottom_line_cited
    )
    displayed = _display_order(selected, composition)

    kinds = {row.kind for row in displayed}
    mixed = len(kinds) > 1
    kind_caption = ""
    if not mixed and kinds:
        only_kind = next(iter(kinds))
        if only_kind == "actual":
            kind_caption = "No figure in this table is a forecast."
        elif only_kind == "forecast":
            kind_caption = "Every figure in this table is a forecast."

    rows: list[list[TableCell]] = []
    for row in displayed:
        finding = finding_by_id.get(row.finding_id)
        rival = _has_rival(row, displayed)
        row_ids = [row.row_id]
        finding_ids = _row_finding_ids(row)
        rows.append(
            [
                TableCell(
                    text=_what_was_measured(row, finding, rival),
                    row_ids=row_ids,
                    finding_ids=finding_ids,
                ),
                TableCell(
                    text=_result_text(row, mixed, finding_by_id),
                    row_ids=row_ids,
                    finding_ids=finding_ids,
                ),
                TableCell(
                    text=_who_text(row, finding, composition.page_credits),
                    row_ids=row_ids,
                    finding_ids=finding_ids,
                ),
                TableCell(text="", row_ids=row_ids, finding_ids=finding_ids),
            ]
        )

    if total > MAX_FINDING_ROWS:
        caption = f"Showing {MAX_FINDING_ROWS} of {total} verified figures; all are in the evidence log."
        if kind_caption:
            caption = f"{caption} {kind_caption}"
    else:
        caption = kind_caption

    return ReportTable(
        shape="findings", columns=list(_FINDINGS_COLUMNS), rows=rows, caption=caption
    )
