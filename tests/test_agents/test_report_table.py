"""Tests for agents.report_table (spec §4, §14 T2): the question-shaped table.

Pure functions, no provider: ``build_table`` picks the shape (§4.1),
``options_table`` and ``findings_table`` build it from checked statements and
verified figures only (§4.2, §4.3). Fixtures follow the §4.2 Fable
acceptance paragraph and the §13.2/§13.3 examples.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report_table import build_table, findings_table, options_table
from deep_research.utils.types import (
    EarlierEdition,
    EvidenceTarget,
    FactRow,
    FigureContext,
    FigureResult,
    Finding,
    FindingVerification,
    ItemMark,
    PageCredit,
    ReportComposition,
    ReportPart,
    ReportPoint,
    ReportSection,
    ReportStatement,
    SubTopic,
)
from tests.evidence_fakes import figure, make_finding, make_read

# --- generic composition scaffolding -----------------------------------------


def _composition(**overrides: object) -> ReportComposition:
    payload: dict[str, object] = {
        "question": "which is best?",
        "session_id": "session-1",
        "sub_topics": [],
        "sources": [],
        "findings": [],
        "fact_rows": [],
        "summary": [],
        "sections": [],
        "parts": [],
        "page_credits": {},
        "statement_verdicts": {},
    }
    payload.update(overrides)
    return ReportComposition.model_validate(payload)


def _topic(coverage_id: str, *, required: bool, priority: int) -> SubTopic:
    return SubTopic(
        coverage_id=coverage_id,
        title=coverage_id,
        rationale="r",
        search_queries=["q"],
        success_criteria=["c"],
        priority=priority,
        evidence_targets=[
            EvidenceTarget(
                target_id=f"{coverage_id}-target-01",
                coverage_id=coverage_id,
                question="q",
                required=required,
                measure="m",
            )
        ],
    )


def _stmt(
    statement_id: str,
    text: str,
    *,
    items: Sequence[ItemMark] = (),
    finding_ids: Sequence[str] = (),
) -> ReportPoint:
    return ReportPoint(
        text=text,
        source_urls=[m.source_url for m in items] or None,
        statement=ReportStatement(
            statement_id=statement_id,
            text=text,
            finding_ids=list(finding_ids),
            items=list(items),
        ),
    )


def _finding_for_url(url: str, *, title: str = "Page") -> Finding:
    """One minimal Finding whose own page is ``url``, so a mark citing it passes
    the mark-source-url guard (``report_table._resolve_marks``)."""
    read = make_read(f"Content about {url}.", url=url, title=title)
    return make_finding(read, f"Content about {url}.")


def _backed_stmt(
    statement_id: str,
    text: str,
    *,
    items: Sequence[ItemMark],
    findings_by_url: Mapping[str, Finding],
) -> ReportPoint:
    """Like ``_stmt``, but derives ``finding_ids`` from each mark's own page via
    ``findings_by_url``, so the marks are backed by a real cited finding."""
    finding_ids = list(
        dict.fromkeys(
            finding_fingerprint(findings_by_url[mark.source_url]) for mark in items
        )
    )
    return _stmt(statement_id, text, items=items, finding_ids=finding_ids)


def _backed(
    points: Sequence[ReportPoint], findings_by_url: Mapping[str, Finding]
) -> list[ReportPoint]:
    """Fill each point's ``finding_ids`` from its own marks' pages, so the
    mark-source-url guard trusts them (``report_table._resolve_marks``)."""
    backed: list[ReportPoint] = []
    for point in points:
        statement = point.statement
        assert statement is not None
        finding_ids = list(
            dict.fromkeys(
                finding_fingerprint(findings_by_url[mark.source_url])
                for mark in statement.items
            )
        )
        backed.append(
            point.model_copy(
                update={
                    "statement": statement.model_copy(
                        update={"finding_ids": finding_ids}
                    )
                }
            )
        )
    return backed


def _section(
    coverage_id: str, title: str, points: Sequence[ReportPoint]
) -> ReportSection:
    return ReportSection(title=title, coverage_id=coverage_id, points=list(points))


def _verdicts(*statement_ids: str, verdict: str = "consistent") -> dict[str, str]:
    return dict.fromkeys(statement_ids, verdict)


# =============================================================================
# (1) The §4.2 Fable options-table fixture.
# =============================================================================

SOUNDGUYS = "https://soundguys.com/best-headphones"
CNET = "https://cnet.com/best-headphones"
WHATHIFI = "https://whathifi.com/best-headphones-mic"
BUSINESSINSIDER = "https://businessinsider.com/best-headphones"

XM6 = "Sony WH-1000XM6"
COLLEXION = "Sony 1000X The Collexion"
MAX2 = "Apple AirPods Max 2"
JBL = "JBL Tour ONE M3"
BOSE = "Bose QuietComfort Ultra Headphones (2nd Gen)"
ANKER = "Anker Soundcore Liberty 5 Pro"
WF1000XM5 = "Sony WF-1000XM5"
AIRPODS_PRO3 = "Apple AirPods Pro 3"


def _fable_page_credits() -> dict[str, PageCredit]:
    return {
        SOUNDGUYS: PageCredit(publisher="SoundGuys", date="2026-09-17"),
        CNET: PageCredit(publisher="CNET", date="2026-08-06"),
        WHATHIFI: PageCredit(publisher="What Hi-Fi?", date="2026-01-16"),
        BUSINESSINSIDER: PageCredit(publisher="Business Insider", date="2026-05-29"),
    }


def _fable_sound_quality_points() -> list[ReportPoint]:
    return [
        _stmt(
            "S101",
            "SoundGuys names the Sony WH-1000XM6 the best wireless headphones and "
            "gives it a sound score of 4.8 out of 5.",
            items=[
                ItemMark(
                    name=XM6,
                    verdict="the best wireless headphones",
                    picked=True,
                    source_url=SOUNDGUYS,
                ),
                ItemMark(
                    name=XM6,
                    verdict="sound score of 4.8 out of 5",
                    source_url=SOUNDGUYS,
                ),
            ],
        ),
        _stmt(
            "S102",
            "CNET gives the Sony WH-1000XM6 a score of 9.3.",
            items=[ItemMark(name=XM6, verdict="a score of 9.3", source_url=CNET)],
        ),
        _stmt(
            "S103",
            "CNET gives the Sony 1000X The Collexion a 9.2 score, its top pick.",
            items=[
                ItemMark(
                    name=COLLEXION, verdict="a 9.2 score", picked=True, source_url=CNET
                )
            ],
        ),
        _stmt(
            "S104",
            "SoundGuys gives the Apple AirPods Max 2 a sound score of 4.3 out of 5 "
            "and calls it the best noise canceling headphones.",
            items=[
                ItemMark(
                    name=MAX2,
                    verdict="sound score of 4.3 out of 5",
                    source_url=SOUNDGUYS,
                ),
                ItemMark(
                    name=MAX2,
                    verdict="the best noise canceling headphones",
                    picked=True,
                    source_url=SOUNDGUYS,
                ),
            ],
        ),
        _stmt(
            "S105",
            "CNET gives the Apple AirPods Max 2 a score of 9.1.",
            items=[ItemMark(name=MAX2, verdict="a score of 9.1", source_url=CNET)],
        ),
        _stmt(
            "S106",
            "SoundGuys gives the JBL Tour ONE M3 a sound score of 3.9 out of 5.",
            items=[
                ItemMark(
                    name=JBL,
                    verdict="sound score of 3.9 out of 5",
                    source_url=SOUNDGUYS,
                )
            ],
        ),
        _stmt(
            "S107",
            "CNET gives the Bose QuietComfort Ultra Headphones (2nd Gen) a 9.2 score.",
            items=[ItemMark(name=BOSE, verdict="a 9.2 score", source_url=CNET)],
        ),
    ]


def _fable_microphone_points() -> list[ReportPoint]:
    return [
        _stmt(
            "S201",
            "SoundGuys says the Sony WH-1000XM6 has good mic quality and clear voice capture.",
            items=[
                ItemMark(name=XM6, verdict="good mic quality", source_url=SOUNDGUYS),
                ItemMark(name=XM6, verdict="clear voice capture", source_url=SOUNDGUYS),
            ],
        ),
        _stmt(
            "S202",
            "CNET says the Sony WH-1000XM6 offers excellent voice-calling performance with more mics.",
            items=[
                ItemMark(
                    name=XM6,
                    verdict="excellent voice-calling performance with more mics",
                    source_url=CNET,
                )
            ],
        ),
        _stmt(
            "S203",
            "What Hi-Fi? names the Sony WH-1000XM6 the best over-ear headphones with a mic.",
            items=[
                ItemMark(
                    name=XM6,
                    verdict="the best over-ear headphones with a mic",
                    picked=True,
                    source_url=WHATHIFI,
                )
            ],
        ),
        _stmt(
            "S204",
            "CNET says the Sony 1000X The Collexion offers excellent voice-calling.",
            items=[
                ItemMark(
                    name=COLLEXION, verdict="excellent voice-calling", source_url=CNET
                )
            ],
        ),
        _stmt(
            "S205",
            "CNET says the Apple AirPods Max 2 has slightly improved calls.",
            items=[
                ItemMark(name=MAX2, verdict="slightly improved calls", source_url=CNET)
            ],
        ),
        _stmt(
            "S206",
            "SoundGuys says the JBL Tour ONE M3 performs well for calls.",
            items=[
                ItemMark(
                    name=JBL, verdict="performs well for calls", source_url=SOUNDGUYS
                )
            ],
        ),
        _stmt(
            "S207",
            "CNET says the Bose QuietComfort Ultra Headphones (2nd Gen) offers excellent voice-calling.",
            items=[
                ItemMark(name=BOSE, verdict="excellent voice-calling", source_url=CNET)
            ],
        ),
        _stmt(
            "S208",
            "CNET names the Anker Soundcore Liberty 5 Pro its top voice-calling performance pick.",
            items=[
                ItemMark(
                    name=ANKER,
                    verdict="top voice-calling performance",
                    picked=True,
                    source_url=CNET,
                )
            ],
        ),
        _stmt(
            "S209",
            "What Hi-Fi? names the Sony WF-1000XM5 the best headphones with a mic for most people.",
            items=[
                ItemMark(
                    name=WF1000XM5,
                    verdict="the best headphones with a mic for most people",
                    picked=True,
                    source_url=WHATHIFI,
                )
            ],
        ),
        _stmt(
            "S210",
            "What Hi-Fi? says the Apple AirPods Pro 3 offers great call quality.",
            items=[
                ItemMark(
                    name=AIRPODS_PRO3, verdict="great call quality", source_url=WHATHIFI
                )
            ],
        ),
    ]


def _fable_prices_points() -> list[ReportPoint]:
    return [
        _stmt(
            "S301",
            "SoundGuys lists the Sony WH-1000XM6 at $390 at Amazon.",
            items=[ItemMark(name=XM6, verdict="$390 at Amazon", source_url=SOUNDGUYS)],
        ),
        _stmt(
            "S302",
            "CNET lists the Sony 1000X The Collexion at $599.",
            items=[ItemMark(name=COLLEXION, verdict="$599", source_url=CNET)],
        ),
        _stmt(
            "S303",
            "SoundGuys lists the Apple AirPods Max 2 at $479 at Amazon.",
            items=[ItemMark(name=MAX2, verdict="$479 at Amazon", source_url=SOUNDGUYS)],
        ),
        _stmt(
            "S304",
            "SoundGuys lists the JBL Tour ONE M3 at $449.95 at Amazon.",
            items=[
                ItemMark(name=JBL, verdict="$449.95 at Amazon", source_url=SOUNDGUYS)
            ],
        ),
        _stmt(
            "S305",
            "SoundGuys picks the Bose QuietComfort Ultra Headphones (2nd Gen) at $399.99 at Amazon.",
            items=[
                ItemMark(
                    name=BOSE,
                    verdict="$399.99 at Amazon",
                    picked=True,
                    source_url=SOUNDGUYS,
                )
            ],
        ),
        _stmt(
            "S306",
            "Business Insider picks the Bose QuietComfort Ultra Headphones (2nd Gen) at $369.",
            items=[
                ItemMark(
                    name=BOSE, verdict="$369", picked=True, source_url=BUSINESSINSIDER
                )
            ],
        ),
        _stmt(
            "S307",
            "CNET lists the Anker Soundcore Liberty 5 Pro at $170.",
            items=[ItemMark(name=ANKER, verdict="$170", source_url=CNET)],
        ),
        _stmt(
            "S308",
            "What Hi-Fi? lists the Apple AirPods Pro 3 at $199 at Amazon.",
            items=[
                ItemMark(
                    name=AIRPODS_PRO3, verdict="$199 at Amazon", source_url=WHATHIFI
                )
            ],
        ),
    ]


def _fable_findings_by_url() -> dict[str, Finding]:
    return {
        url: _finding_for_url(url)
        for url in (SOUNDGUYS, CNET, WHATHIFI, BUSINESSINSIDER)
    }


def _fable_composition(
    *, extra_prices_point: ReportPoint | None = None
) -> ReportComposition:
    findings_by_url = _fable_findings_by_url()
    prices_points = _backed(_fable_prices_points(), findings_by_url)
    if extra_prices_point is not None:
        prices_points = [
            *prices_points,
            *_backed([extra_prices_point], findings_by_url),
        ]
    sections = [
        _section(
            "topic-01",
            "Sound quality",
            _backed(_fable_sound_quality_points(), findings_by_url),
        ),
        _section(
            "topic-02",
            "Microphone",
            _backed(_fable_microphone_points(), findings_by_url),
        ),
        _section("topic-03", "Prices", prices_points),
    ]
    all_ids = [p.statement_id for section in sections for p in section.points]
    return _composition(
        sub_topics=[
            _topic("topic-01", required=True, priority=1),
            _topic("topic-02", required=True, priority=2),
            _topic("topic-03", required=False, priority=3),
        ],
        findings=list(findings_by_url.values()),
        sections=sections,
        page_credits=_fable_page_credits(),
        statement_verdicts=_verdicts(*all_ids),
    )


def test_fable_options_table_columns_and_row_order() -> None:
    table = options_table(_fable_composition())
    assert table.shape == "options"
    assert table.columns == [
        "Option",
        "Sound quality",
        "Microphone",
        "Prices",
        "Recommended by",
    ]
    labels = [row[0].text for row in table.rows]
    assert labels == [XM6, BOSE, COLLEXION, MAX2, ANKER, WF1000XM5, JBL, AIRPODS_PRO3]


def test_fable_options_table_microphone_cell_caps_at_two_full_pages() -> None:
    table = options_table(_fable_composition())
    xm6_row = table.rows[0]
    mic_cell = xm6_row[2]  # Option, Sound quality, Microphone, Prices, Recommended by
    assert [e.source_url for e in mic_cell.entries] == [SOUNDGUYS, CNET, WHATHIFI]
    assert mic_cell.entries[0].text == "good mic quality; clear voice capture"
    assert (
        mic_cell.entries[1].text == "excellent voice-calling performance with more mics"
    )
    assert mic_cell.entries[2].text == ""  # bare marker: beyond the 2-page cap


def test_fable_options_table_empty_cell_is_untouched() -> None:
    table = options_table(_fable_composition())
    anker_row = table.rows[4]
    assert labels_of(table)[4] == ANKER
    sound_cell = anker_row[1]
    assert sound_cell.entries == []


def labels_of(table):
    return [row[0].text for row in table.rows]


def test_fable_options_table_recommended_by_with_dates() -> None:
    table = options_table(_fable_composition())
    xm6_row = table.rows[0]
    recommended = xm6_row[-1]
    assert [(e.source_url, e.date) for e in recommended.entries] == [
        (SOUNDGUYS, "2026-09-17"),
        (WHATHIFI, "2026-01-16"),
    ]


def test_fable_options_table_no_pick_renders_empty_recommended_by() -> None:
    table = options_table(_fable_composition())
    jbl_row = table.rows[6]
    assert labels_of(table)[6] == JBL
    assert jbl_row[-1].entries == []


def test_fable_options_table_ninth_option_triggers_cap_caption() -> None:
    ninth = _stmt(
        "S309",
        "CNET lists the Sony WF-C700N at $99.",
        items=[ItemMark(name="Sony WF-C700N", verdict="$99", source_url=CNET)],
    )
    composition = _fable_composition(extra_prices_point=ninth)
    composition = composition.model_copy(
        update={
            "statement_verdicts": _verdicts(*composition.statement_verdicts, "S309")
        }
    )
    table = options_table(composition)
    assert len(table.rows) == 8
    assert (
        "Showing 8 of 9 options; the rest are in the sections below." in table.caption
    )
    assert table.caption.startswith(
        "Each cell quotes the report's own sentence about the option in that part"
    )


# =============================================================================
# (2) The choice rule.
# =============================================================================


def test_choice_rule_marks_only_in_optional_part_give_no_options_table() -> None:
    findings_by_url = {"https://a.test/x": _finding_for_url("https://a.test/x")}
    composition = _composition(
        sub_topics=[_topic("topic-09", required=False, priority=1)],
        findings=list(findings_by_url.values()),
        sections=[
            _section(
                "topic-09",
                "Prices",
                [
                    _backed_stmt(
                        "S1",
                        "Site A lists Model A at $10.",
                        items=[
                            ItemMark(
                                name="Model A",
                                verdict="$10",
                                source_url="https://a.test/x",
                            )
                        ],
                        findings_by_url=findings_by_url,
                    ),
                    _backed_stmt(
                        "S2",
                        "Site A lists Model B at $20.",
                        items=[
                            ItemMark(
                                name="Model B",
                                verdict="$20",
                                source_url="https://a.test/x",
                            )
                        ],
                        findings_by_url=findings_by_url,
                    ),
                ],
            )
        ],
        statement_verdicts=_verdicts("S1", "S2"),
    )
    assert build_table(composition) is None


def test_choice_rule_two_eligible_rows_give_findings_table() -> None:
    row_a, finding_a = _row(
        "K001",
        url="https://a.test/x",
        value="10",
        unit="GW",
        target_ids=["t-01"],
        finding_target_ids=["t-01"],
    )
    row_b, finding_b = _row(
        "K002",
        url="https://b.test/y",
        value="20",
        unit="GW",
        target_ids=["t-02"],
        finding_target_ids=["t-02"],
    )
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-x",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="t-01",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                    EvidenceTarget(
                        target_id="t-02",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                ],
            )
        ],
        findings=[finding_a, finding_b],
        fact_rows=[row_a, row_b],
    )
    table = build_table(composition)
    assert table is not None
    assert table.shape == "findings"


def test_choice_rule_one_row_gives_no_table() -> None:
    row_a, finding_a = _row(
        "K001",
        url="https://a.test/x",
        value="10",
        unit="GW",
        target_ids=["t-01"],
        finding_target_ids=["t-01"],
    )
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-x",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="t-01",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    )
                ],
            )
        ],
        findings=[finding_a],
        fact_rows=[row_a],
    )
    assert build_table(composition) is None


# =============================================================================
# (3) Marks on unchecked statements are ignored.
# =============================================================================


def test_marks_on_unchecked_statements_are_ignored() -> None:
    findings_by_url = {"https://a.test/x": _finding_for_url("https://a.test/x")}
    composition = _composition(
        sub_topics=[_topic("topic-01", required=True, priority=1)],
        findings=list(findings_by_url.values()),
        sections=[
            _section(
                "topic-01",
                "Sound quality",
                [
                    _backed_stmt(
                        "S1",
                        "Site A says Model A is the best.",
                        items=[
                            ItemMark(
                                name="Model A",
                                verdict="the best",
                                source_url="https://a.test/x",
                            )
                        ],
                        findings_by_url=findings_by_url,
                    ),
                    _backed_stmt(
                        "S2",
                        "Site A says Model B is the best.",
                        items=[
                            ItemMark(
                                name="Model B",
                                verdict="the best",
                                source_url="https://a.test/x",
                            )
                        ],
                        findings_by_url=findings_by_url,
                    ),
                ],
            )
        ],
        statement_verdicts={"S1": "consistent", "S2": "unchecked"},
    )
    assert build_table(composition) is None
    table = options_table(composition)
    assert labels_of(table) == [] or "Model B" not in labels_of(table)


# =============================================================================
# helpers for the findings table
# =============================================================================


def _row(
    row_id: str,
    *,
    url: str,
    value: str,
    unit: str = "GW",
    period: str | None = None,
    kind: str = "actual",
    organisation: str = "Example Org",
    attribution: str = "own",
    relay_host: str | None = None,
    subject: str | None = None,
    scope: str | None = None,
    period_resolved_from: str | None = None,
    target_ids: Sequence[str] = (),
    finding_target_ids: Sequence[str] | None = None,
    context_unchecked: bool = False,
    duplicate_finding_ids: Sequence[str] = (),
    earlier: Sequence[EarlierEdition] = (),
    release_date: str | None = None,
    statement_date: str | None = None,
    evidence_words: str | None = None,
    vintage: str | None = None,
) -> tuple[FactRow, Finding]:
    snippet = evidence_words or f"{organisation} reports {value} {unit} for {row_id}."
    read = make_read(snippet, url=url, title=f"{organisation} page")
    finding = make_finding(
        read,
        snippet,
        figures=[figure(value, unit, period, kind)],
        target_ids=list(
            finding_target_ids if finding_target_ids is not None else target_ids
        ),
        release_date=release_date,
        statement_date=statement_date,
        vintage=vintage,
    )
    result = FigureResult(
        figure=finding.figures[0],
        matched=True,
        evidence_words=snippet,
        context=FigureContext(
            period=period,
            attribution=attribution,
            organisation=organisation or "Unattributed page",
            kind=kind,
            subject=subject,
            scope=scope,
            period_resolved_from=period_resolved_from,
        ),
    )
    finding = finding.model_copy(
        update={
            "verification": FindingVerification(
                status="verified", figure_results=[result]
            )
        }
    )
    row = FactRow(
        row_id=row_id,
        organisation=organisation,
        attribution=attribution,
        relay_host=relay_host,
        subject=subject,
        measure="m",
        period=period,
        value=f"{value} {unit}",
        kind=kind,
        scope=scope,
        period_resolved_from=period_resolved_from,
        finding_id=finding_fingerprint(finding),
        duplicate_finding_ids=list(duplicate_finding_ids),
        earlier=list(earlier),
        target_ids=list(target_ids),
        context_unchecked=context_unchecked,
    )
    return row, finding


def _cite(*finding_ids: str, statement_id: str) -> ReportPoint:
    text = f"Statement {statement_id}."
    return ReportPoint(
        text=text,
        source_urls=[],
        statement=ReportStatement(
            statement_id=statement_id, text=text, finding_ids=list(finding_ids)
        ),
    )


# =============================================================================
# (4) The Example 13.2 rows: rivals, who strings, mixed-kind suffixes.
# =============================================================================


def test_rival_rows_both_get_quoted_form() -> None:
    row1, finding1 = _row(
        "K001",
        url="https://house.gov/a",
        value="10.4",
        unit="GW",
        period="2024",
        kind="actual",
        subject="Battery storage capacity",
        organisation="house.gov",
        attribution="unattributed",
        target_ids=["req-01"],
        finding_target_ids=["req-01"],
        evidence_words="Generators added 10.4 GW of new battery storage capacity in 2024.",
    )
    row2, finding2 = _row(
        "K002",
        url="https://house.gov/a",
        value="26",
        unit="GW",
        period="2024",
        kind="actual",
        subject="Battery storage capacity",
        organisation="house.gov",
        attribution="unattributed",
        target_ids=["req-01"],
        finding_target_ids=["req-01"],
        evidence_words="Cumulative utility-scale battery storage capacity exceeded 26 GW in 2024.",
    )
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-01",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="req-01",
                        coverage_id="topic-01",
                        question="q",
                        required=True,
                        measure="m",
                    )
                ],
            )
        ],
        findings=[finding1, finding2],
        fact_rows=[row1, row2],
    )
    table = findings_table(composition)
    assert table is not None
    texts = [row[0].text for row in table.rows]
    assert all(text.startswith('"') for text in texts)


def test_row_with_no_subject_is_quoted_even_without_a_rival() -> None:
    row, finding = _row(
        "K006",
        url="https://eia.gov/a",
        value="8.3",
        unit="GW",
        period="2026",
        kind="actual",
        subject=None,
        organisation="EIA",
        attribution="own",
        target_ids=["req-06"],
        finding_target_ids=["req-06"],
        evidence_words="Battery storage rose 8.3 GW in the first half of 2026.",
    )
    other_row, other_finding = _row(
        "K099",
        url="https://eia.gov/b",
        value="1",
        unit="GW",
        period="2019",
        kind="actual",
        subject="Unrelated widget output",
        organisation="EIA",
        attribution="own",
        target_ids=["req-99"],
        finding_target_ids=["req-99"],
    )
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-06",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="req-06",
                        coverage_id="topic-06",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                    EvidenceTarget(
                        target_id="req-99",
                        coverage_id="topic-06",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                ],
            )
        ],
        findings=[finding, other_finding],
        fact_rows=[row, other_row],
    )
    table = findings_table(composition)
    assert table is not None
    quoted_texts = [row[0].text for row in table.rows if row[0].text.startswith('"')]
    assert any("8.3 GW" in text for text in quoted_texts)


def test_who_own_relayed_and_unattributed_strings() -> None:
    own_row, own_finding = _row(
        "K001",
        url="https://example.test/own",
        value="10",
        unit="GW",
        organisation="Acme Testing Corp",
        attribution="own",
        target_ids=["req-a"],
        finding_target_ids=["req-a"],
        release_date="2025-03-12",
    )
    relay_row, relay_finding = _row(
        "K002",
        url="https://utilitydive.test/relay",
        value="65",
        unit="GW",
        organisation="Energy Information Administration",
        attribution="relayed",
        relay_host="utilitydive.test",
        target_ids=["req-b"],
        finding_target_ids=["req-b"],
        release_date="2025-06-10",
    )
    unattributed_row, unattributed_finding = _row(
        "K003",
        url="https://newswire.test/story",
        value="5",
        unit="GW",
        organisation="",
        attribution="unattributed",
        target_ids=["req-c"],
        finding_target_ids=["req-c"],
    )
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-x",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="req-a",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                    EvidenceTarget(
                        target_id="req-b",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                    EvidenceTarget(
                        target_id="req-c",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                ],
            )
        ],
        findings=[own_finding, relay_finding, unattributed_finding],
        fact_rows=[own_row, relay_row, unattributed_row],
        page_credits={
            "https://utilitydive.test/relay": PageCredit(publisher="Utility Dive"),
            "https://newswire.test/story": PageCredit(publisher="Example Newswire"),
        },
    )
    table = findings_table(composition)
    assert table is not None
    who_by_row_id = {row[0].row_ids[0]: row[2].text for row in table.rows}
    assert who_by_row_id["K001"] == "Acme Testing Corp (released 2025-03-12)"
    assert (
        who_by_row_id["K002"]
        == "Energy Information Administration, reported by Utility Dive (released 2025-06-10)"
    )
    assert who_by_row_id["K003"] == "Example Newswire"


def test_own_organisation_equal_to_host_prints_the_page_publisher() -> None:
    row, finding = _row(
        "K001",
        url="https://example.test/report",
        value="10",
        unit="GW",
        organisation="example.test",
        attribution="own",
        target_ids=["req-a"],
        finding_target_ids=["req-a"],
    )
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-x",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="req-a",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    )
                ],
            )
        ],
        findings=[finding],
        fact_rows=[
            row,
            row.model_copy(
                update={
                    "row_id": "K002",
                    "finding_id": finding_fingerprint(finding),
                    "value": "11 GW",
                }
            ),
        ],
        page_credits={
            "https://example.test/report": PageCredit(
                publisher="Example Test Publisher"
            )
        },
    )
    table = findings_table(composition)
    assert table is not None
    who_texts = {row[0].row_ids[0]: row[2].text for row in table.rows}
    assert who_texts["K001"].startswith("Example Test Publisher")


def test_relayed_falls_back_to_relay_host_with_no_page_credit() -> None:
    row, finding = _row(
        "K001",
        url="https://relay.test/story",
        value="10",
        unit="GW",
        organisation="Some Org",
        attribution="relayed",
        relay_host="relay.test",
        target_ids=["req-a"],
        finding_target_ids=["req-a"],
    )
    other_row, other_finding = _row(
        "K002",
        url="https://another.test/x",
        value="5",
        unit="GW",
        organisation="Another Org",
        target_ids=["req-b"],
        finding_target_ids=["req-b"],
    )
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-x",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="req-a",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                    EvidenceTarget(
                        target_id="req-b",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                ],
            )
        ],
        findings=[finding, other_finding],
        fact_rows=[row, other_row],
    )
    table = findings_table(composition)
    assert table is not None
    who = {r[0].row_ids[0]: r[2].text for r in table.rows}
    assert who["K001"] == "Some Org, reported by relay.test"


def test_mixed_kinds_suffix_each_result() -> None:
    actual_row, actual_finding = _row(
        "K001",
        url="https://a.test/x",
        value="10",
        unit="GW",
        kind="actual",
        target_ids=["req-a"],
        finding_target_ids=["req-a"],
    )
    forecast_row, forecast_finding = _row(
        "K002",
        url="https://b.test/y",
        value="20",
        unit="GW",
        kind="forecast",
        target_ids=["req-b"],
        finding_target_ids=["req-b"],
    )
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-x",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="req-a",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                    EvidenceTarget(
                        target_id="req-b",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                        kind="forecast",
                    ),
                ],
            )
        ],
        findings=[actual_finding, forecast_finding],
        fact_rows=[actual_row, forecast_row],
    )
    table = findings_table(composition)
    assert table is not None
    assert table.caption == ""
    results = {r[0].row_ids[0]: r[1].text for r in table.rows}
    assert results["K001"] == "10 GW, actual"
    assert results["K002"] == "20 GW, forecast"


def test_all_actual_rows_get_the_no_forecast_caption() -> None:
    row1, finding1 = _row(
        "K001",
        url="https://a.test/x",
        value="10",
        unit="GW",
        target_ids=["req-a"],
        finding_target_ids=["req-a"],
    )
    row2, finding2 = _row(
        "K002",
        url="https://b.test/y",
        value="20",
        unit="GW",
        target_ids=["req-b"],
        finding_target_ids=["req-b"],
    )
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-x",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="req-a",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                    EvidenceTarget(
                        target_id="req-b",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                ],
            )
        ],
        findings=[finding1, finding2],
        fact_rows=[row1, row2],
    )
    table = findings_table(composition)
    assert table is not None
    assert table.caption == "No figure in this table is a forecast."
    results = {r[0].row_ids[0]: r[1].text for r in table.rows}
    assert results["K001"] == "10 GW"  # no suffix when every row shares one kind


def test_context_unchecked_rows_are_excluded() -> None:
    row1, finding1 = _row(
        "K001",
        url="https://a.test/x",
        value="10",
        unit="GW",
        target_ids=["req-a"],
        finding_target_ids=["req-a"],
    )
    row2, finding2 = _row(
        "K002",
        url="https://b.test/y",
        value="20",
        unit="GW",
        target_ids=["req-b"],
        finding_target_ids=["req-b"],
    )
    row3, finding3 = _row(
        "K003",
        url="https://c.test/z",
        value="30",
        unit="GW",
        target_ids=["req-c"],
        finding_target_ids=["req-c"],
        context_unchecked=True,
    )
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-x",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="req-a",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                    EvidenceTarget(
                        target_id="req-b",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                    EvidenceTarget(
                        target_id="req-c",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                ],
            )
        ],
        findings=[finding1, finding2, finding3],
        fact_rows=[row1, row2, row3],
    )
    table = findings_table(composition)
    assert table is not None
    row_ids = {r[0].row_ids[0] for r in table.rows}
    assert row_ids == {"K001", "K002"}


def test_vintage_never_printed() -> None:
    row1, finding1 = _row(
        "K001",
        url="https://a.test/x",
        value="10",
        unit="GW",
        target_ids=["req-a"],
        finding_target_ids=["req-a"],
        vintage="January 2025 Preliminary Monthly Electric Generator Inventory",
    )
    row2, finding2 = _row(
        "K002",
        url="https://b.test/y",
        value="20",
        unit="GW",
        target_ids=["req-b"],
        finding_target_ids=["req-b"],
    )
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-x",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="req-a",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                    EvidenceTarget(
                        target_id="req-b",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                ],
            )
        ],
        findings=[finding1, finding2],
        fact_rows=[row1, row2],
    )
    table = findings_table(composition)
    assert table is not None
    for row in table.rows:
        for cell in row:
            assert "Preliminary Monthly" not in cell.text


def test_cap_12_selection_priority() -> None:
    required_row, required_finding = _row(
        "K001",
        url="https://req.test/x",
        value="1",
        unit="GW",
        target_ids=["req-a"],
        finding_target_ids=["req-a"],
    )
    bottom_line_row, bottom_line_finding = _row(
        "K002",
        url="https://bl.test/y",
        value="2",
        unit="GW",
    )
    # 11 more rows cited only by section statements, none required, none in the bottom line.
    other_rows: list[FactRow] = []
    other_findings: list[Finding] = []
    section_points: list[ReportPoint] = []
    for n in range(11):
        row, finding = _row(
            f"K{n + 10:03d}", url=f"https://other{n}.test/x", value=str(n), unit="GW"
        )
        other_rows.append(row)
        other_findings.append(finding)
        section_points.append(_cite(finding_fingerprint(finding), statement_id=f"S{n}"))
    bl_statement_id = "B01"
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-x",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="req-a",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    )
                ],
            )
        ],
        findings=[required_finding, bottom_line_finding, *other_findings],
        fact_rows=[required_row, bottom_line_row, *other_rows],
        summary=[
            _cite(
                finding_fingerprint(bottom_line_finding), statement_id=bl_statement_id
            )
        ],
        sections=[_section("topic-x", "Section", section_points)],
        statement_verdicts=_verdicts(
            bl_statement_id, *[p.statement_id for p in section_points]
        ),
    )
    table = findings_table(composition)
    assert table is not None
    assert len(table.rows) == 12
    row_ids = {r[0].row_ids[0] for r in table.rows}
    assert "K001" in row_ids  # answers a required target: always kept
    assert "K002" in row_ids  # cited by the bottom line: kept ahead of "the rest"
    assert table.caption == (
        "Showing 12 of 13 verified figures; all are in the evidence log. "
        "No figure in this table is a forecast."
    )


# =============================================================================
# Review round 1 fixes.
# =============================================================================


def test_capped_findings_table_keeps_the_forecast_caption() -> None:
    required_row, required_finding = _row(
        "K000",
        url="https://req.test/x",
        value="1",
        unit="GW",
        kind="forecast",
        target_ids=["req-a"],
        finding_target_ids=["req-a"],
    )
    other_rows: list[FactRow] = []
    other_findings: list[Finding] = []
    section_points: list[ReportPoint] = []
    for n in range(12):
        row, finding = _row(
            f"K{n + 1:03d}",
            url=f"https://f{n}.test/x",
            value=str(n),
            unit="GW",
            kind="forecast",
        )
        other_rows.append(row)
        other_findings.append(finding)
        section_points.append(_cite(finding_fingerprint(finding), statement_id=f"S{n}"))
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-x",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="req-a",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    )
                ],
            )
        ],
        findings=[required_finding, *other_findings],
        fact_rows=[required_row, *other_rows],
        sections=[_section("topic-x", "Section", section_points)],
        statement_verdicts=_verdicts(*[p.statement_id for p in section_points]),
    )
    table = findings_table(composition)
    assert table is not None
    assert table.caption == (
        "Showing 12 of 13 verified figures; all are in the evidence log. "
        "Every figure in this table is a forecast."
    )


def test_capped_findings_table_keeps_the_actual_caption() -> None:
    required_row, required_finding = _row(
        "K000",
        url="https://req.test/x",
        value="1",
        unit="GW",
        kind="actual",
        target_ids=["req-a"],
        finding_target_ids=["req-a"],
    )
    other_rows: list[FactRow] = []
    other_findings: list[Finding] = []
    section_points: list[ReportPoint] = []
    for n in range(12):
        row, finding = _row(
            f"K{n + 1:03d}",
            url=f"https://f{n}.test/x",
            value=str(n),
            unit="GW",
            kind="actual",
        )
        other_rows.append(row)
        other_findings.append(finding)
        section_points.append(_cite(finding_fingerprint(finding), statement_id=f"S{n}"))
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-x",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="req-a",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    )
                ],
            )
        ],
        findings=[required_finding, *other_findings],
        fact_rows=[required_row, *other_rows],
        sections=[_section("topic-x", "Section", section_points)],
        statement_verdicts=_verdicts(*[p.statement_id for p in section_points]),
    )
    table = findings_table(composition)
    assert table is not None
    assert table.caption == (
        "Showing 12 of 13 verified figures; all are in the evidence log. "
        "No figure in this table is a forecast."
    )


def test_earlier_edition_date_comes_from_the_earlier_findings_own_dates() -> None:
    earlier_row, earlier_finding = _row(
        "K000",
        url="https://earlier.test/x",
        value="9",
        unit="GW",
        target_ids=["req-earlier"],
        finding_target_ids=["req-earlier"],
        release_date="2024-01-01",
        vintage="January 2024 Preliminary Inventory",
    )
    row, finding = _row(
        "K001",
        url="https://a.test/x",
        value="10",
        unit="GW",
        target_ids=["req-a"],
        finding_target_ids=["req-a"],
        earlier=[
            EarlierEdition(
                value="9 GW",
                release="January 2024 Preliminary Inventory",
                finding_id=finding_fingerprint(earlier_finding),
            )
        ],
    )
    other_row, other_finding = _row(
        "K002",
        url="https://b.test/y",
        value="20",
        unit="GW",
        target_ids=["req-b"],
        finding_target_ids=["req-b"],
    )
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-x",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="req-a",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                    EvidenceTarget(
                        target_id="req-b",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                ],
            )
        ],
        findings=[earlier_finding, finding, other_finding],
        fact_rows=[row, other_row],
    )
    table = findings_table(composition)
    assert table is not None
    result = {r[0].row_ids[0]: r[1].text for r in table.rows}["K001"]
    assert result == "10 GW; earlier: 9 GW (2024-01-01)"
    assert "January 2024 Preliminary Inventory" not in result


def test_earlier_edition_falls_back_to_the_earlier_findings_statement_date() -> None:
    earlier_row, earlier_finding = _row(
        "K000",
        url="https://earlier.test/x",
        value="9",
        unit="GW",
        target_ids=["req-earlier"],
        finding_target_ids=["req-earlier"],
        statement_date="2024-02-02",
    )
    row, finding = _row(
        "K001",
        url="https://a.test/x",
        value="10",
        unit="GW",
        target_ids=["req-a"],
        finding_target_ids=["req-a"],
        earlier=[
            EarlierEdition(
                value="9 GW",
                release=None,
                finding_id=finding_fingerprint(earlier_finding),
            )
        ],
    )
    other_row, other_finding = _row(
        "K002",
        url="https://b.test/y",
        value="20",
        unit="GW",
        target_ids=["req-b"],
        finding_target_ids=["req-b"],
    )
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-x",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="req-a",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                    EvidenceTarget(
                        target_id="req-b",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                ],
            )
        ],
        findings=[earlier_finding, finding, other_finding],
        fact_rows=[row, other_row],
    )
    table = findings_table(composition)
    assert table is not None
    result = {r[0].row_ids[0]: r[1].text for r in table.rows}["K001"]
    assert result == "10 GW; earlier: 9 GW (2024-02-02)"


def test_mark_credited_to_an_uncited_page_is_dropped() -> None:
    read = make_read("Model A is great.", url="https://correct.test/page", title="Page")
    finding = make_finding(read, "Model A is great.")
    composition = _composition(
        sub_topics=[_topic("topic-01", required=True, priority=1)],
        findings=[finding],
        sections=[
            _section(
                "topic-01",
                "Sound",
                [
                    _stmt(
                        "S1",
                        "Site A says Model A is great.",
                        items=[
                            ItemMark(
                                name="Model A",
                                verdict="great",
                                source_url="https://correct.test/page",
                            )
                        ],
                        finding_ids=[finding_fingerprint(finding)],
                    ),
                    _stmt(
                        "S2",
                        "Site A says Model B is great.",
                        items=[
                            ItemMark(
                                name="Model B",
                                verdict="great",
                                source_url="https://wrong.test/other",
                            )
                        ],
                        finding_ids=[finding_fingerprint(finding)],
                    ),
                    _stmt(
                        "S3",
                        "Site A says Model C is great.",
                        items=[
                            ItemMark(
                                name="Model C",
                                verdict="great",
                                source_url="https://correct.test/page",
                            )
                        ],
                        finding_ids=[finding_fingerprint(finding)],
                    ),
                ],
            )
        ],
        statement_verdicts=_verdicts("S1", "S2", "S3"),
    )
    table = options_table(composition)
    assert "Model B" not in labels_of(table)
    assert "Model A" in labels_of(table)
    assert "Model C" in labels_of(table)
    assert any("Model B" in msg and "S2" in msg for msg in composition.dropped_marks)


def test_choice_rule_two_required_parts_with_one_option_each_give_no_options_table() -> (
    None
):
    """P1 regression: the >=2 test is per required part, not a cross-part union.

    An optional part where BOTH options are marked is added deliberately: it
    gives ``_build_options_table`` a qualifying column to hang >= 2 rows on,
    so a reverted, union-counting gate would actually publish a 2-row table
    here rather than being masked by the separate "fewer than 2 rows, fall
    through" safety net (see the mutation-tested proof in the review reply).
    """
    findings_by_url = {"https://a.test/x": _finding_for_url("https://a.test/x")}
    composition = _composition(
        sub_topics=[
            _topic("topic-01", required=True, priority=1),
            _topic("topic-02", required=True, priority=2),
            _topic("topic-03", required=False, priority=3),
        ],
        findings=list(findings_by_url.values()),
        sections=[
            _section(
                "topic-01",
                "Sound",
                [
                    _backed_stmt(
                        "S1",
                        "Site A says Model A is great.",
                        items=[
                            ItemMark(
                                name="Model A",
                                verdict="great",
                                source_url="https://a.test/x",
                            )
                        ],
                        findings_by_url=findings_by_url,
                    )
                ],
            ),
            _section(
                "topic-02",
                "Mic",
                [
                    _backed_stmt(
                        "S2",
                        "Site A says Model B is great.",
                        items=[
                            ItemMark(
                                name="Model B",
                                verdict="great",
                                source_url="https://a.test/x",
                            )
                        ],
                        findings_by_url=findings_by_url,
                    )
                ],
            ),
            _section(
                "topic-03",
                "Prices",
                [
                    _backed_stmt(
                        "S3",
                        "Site A lists Model A at $10.",
                        items=[
                            ItemMark(
                                name="Model A",
                                verdict="$10",
                                source_url="https://a.test/x",
                            )
                        ],
                        findings_by_url=findings_by_url,
                    ),
                    _backed_stmt(
                        "S4",
                        "Site A lists Model B at $20.",
                        items=[
                            ItemMark(
                                name="Model B",
                                verdict="$20",
                                source_url="https://a.test/x",
                            )
                        ],
                        findings_by_url=findings_by_url,
                    ),
                ],
            ),
        ],
        statement_verdicts=_verdicts("S1", "S2", "S3", "S4"),
    )
    assert build_table(composition) is None


def test_choice_rule_one_required_part_with_two_options_gives_options_table() -> None:
    findings_by_url = {"https://a.test/x": _finding_for_url("https://a.test/x")}
    composition = _composition(
        sub_topics=[_topic("topic-01", required=True, priority=1)],
        findings=list(findings_by_url.values()),
        sections=[
            _section(
                "topic-01",
                "Sound",
                [
                    _backed_stmt(
                        "S1",
                        "Site A says Model A is great.",
                        items=[
                            ItemMark(
                                name="Model A",
                                verdict="great",
                                source_url="https://a.test/x",
                            )
                        ],
                        findings_by_url=findings_by_url,
                    ),
                    _backed_stmt(
                        "S2",
                        "Site A says Model B is great.",
                        items=[
                            ItemMark(
                                name="Model B",
                                verdict="great",
                                source_url="https://a.test/x",
                            )
                        ],
                        findings_by_url=findings_by_url,
                    ),
                ],
            ),
        ],
        statement_verdicts=_verdicts("S1", "S2"),
    )
    table = build_table(composition)
    assert table is not None
    assert table.shape == "options"
    assert len(table.rows) == 2


def test_bottom_line_mark_lands_only_in_the_part_of_its_own_cited_page() -> None:
    sound_url = "https://sound.test/page"
    price_url = "https://price.test/page"
    price_url2 = "https://price2.test/page"
    sound_read = make_read("Model A scores high.", url=sound_url, title="Sound page")
    price_read = make_read("Model A costs $10.", url=price_url, title="Price page")
    price_read2 = make_read("Model C costs $30.", url=price_url2, title="Price page 2")
    sound_finding = make_finding(sound_read, "Model A scores high.")
    price_finding = make_finding(price_read, "Model A costs $10.")
    price_finding2 = make_finding(price_read2, "Model C costs $30.")
    composition = _composition(
        sub_topics=[
            _topic("topic-01", required=True, priority=1),
            _topic("topic-02", required=False, priority=2),
        ],
        findings=[sound_finding, price_finding, price_finding2],
        parts=[
            ReportPart(
                coverage_id="topic-01",
                sub_topic_title="Sound",
                finding_ids=[finding_fingerprint(sound_finding)],
                status="written",
            ),
            ReportPart(
                coverage_id="topic-02",
                sub_topic_title="Prices",
                finding_ids=[
                    finding_fingerprint(price_finding),
                    finding_fingerprint(price_finding2),
                ],
                status="written",
            ),
        ],
        sections=[
            _section(
                "topic-01",
                "Sound",
                [
                    _stmt(
                        "S1",
                        "Site A says Model B scores high too.",
                        items=[
                            ItemMark(
                                name="Model B",
                                verdict="scores high too",
                                source_url=sound_url,
                            )
                        ],
                        finding_ids=[finding_fingerprint(sound_finding)],
                    )
                ],
            ),
            _section(
                "topic-02",
                "Prices",
                [
                    _stmt(
                        "S2",
                        "Site A lists Model B at $20.",
                        items=[
                            ItemMark(
                                name="Model B", verdict="$20", source_url=price_url
                            )
                        ],
                        finding_ids=[finding_fingerprint(price_finding)],
                    ),
                    _stmt(
                        "S3",
                        "Site A lists Model C at $30.",
                        items=[
                            ItemMark(
                                name="Model C", verdict="$30", source_url=price_url2
                            )
                        ],
                        finding_ids=[finding_fingerprint(price_finding2)],
                    ),
                ],
            ),
        ],
        summary=[
            _stmt(
                "B1",
                "Site A says Model A scores high and costs $10.",
                items=[
                    ItemMark(
                        name="Model A", verdict="scores high", source_url=sound_url
                    )
                ],
                finding_ids=[
                    finding_fingerprint(sound_finding),
                    finding_fingerprint(price_finding),
                ],
            )
        ],
        statement_verdicts=_verdicts("S1", "S2", "S3", "B1"),
    )
    table = options_table(composition)
    assert table.columns == ["Option", "Sound", "Prices", "Recommended by"]
    labels = labels_of(table)
    a_row = table.rows[labels.index("Model A")]
    sound_cell, price_cell = a_row[1], a_row[2]
    assert (
        sound_cell.entries
    )  # the bottom-line mark counted for Sound, its own page's part
    assert (
        price_cell.entries == []
    )  # never spread to Prices, though B1 also cites price_finding


def test_verbatim_span_guard_drops_a_mark_whose_verdict_is_not_in_the_sentence() -> (
    None
):
    findings_by_url = {"https://a.test/x": _finding_for_url("https://a.test/x")}
    composition = _composition(
        sub_topics=[_topic("topic-01", required=True, priority=1)],
        findings=list(findings_by_url.values()),
        sections=[
            _section(
                "topic-01",
                "Sound",
                [
                    _stmt(
                        "S1",
                        "Site A says Model A is great.",
                        items=[
                            ItemMark(
                                name="Model A",
                                verdict="a verdict never written in the sentence",
                                source_url="https://a.test/x",
                            )
                        ],
                    ),
                    _backed_stmt(
                        "S2",
                        "Site A says Model B is great.",
                        items=[
                            ItemMark(
                                name="Model B",
                                verdict="great",
                                source_url="https://a.test/x",
                            )
                        ],
                        findings_by_url=findings_by_url,
                    ),
                    _backed_stmt(
                        "S3",
                        "Site A says Model C is great.",
                        items=[
                            ItemMark(
                                name="Model C",
                                verdict="great",
                                source_url="https://a.test/x",
                            )
                        ],
                        findings_by_url=findings_by_url,
                    ),
                ],
            )
        ],
        statement_verdicts=_verdicts("S1", "S2", "S3"),
    )
    table = options_table(composition)
    assert "Model A" not in labels_of(table)
    assert "Model B" in labels_of(table)
    assert "Model C" in labels_of(table)


def test_option_keys_fold_case_whitespace_and_dash_variants() -> None:
    findings_by_url = {
        "https://a.test/x": _finding_for_url("https://a.test/x"),
        "https://b.test/y": _finding_for_url("https://b.test/y"),
    }
    composition = _composition(
        sub_topics=[_topic("topic-01", required=True, priority=1)],
        findings=list(findings_by_url.values()),
        sections=[
            _section(
                "topic-01",
                "Sound",
                [
                    _backed_stmt(
                        "S1",
                        "Site A says Sony WH-1000XM6 is great.",
                        items=[
                            ItemMark(
                                name="Sony WH-1000XM6",
                                verdict="great",
                                source_url="https://a.test/x",
                            )
                        ],
                        findings_by_url=findings_by_url,
                    ),
                    _backed_stmt(
                        "S2",
                        "Site B says sony  wh \u2013 1000xm6 is excellent.",
                        items=[
                            ItemMark(
                                name="sony  wh \u2013 1000xm6",
                                verdict="excellent",
                                source_url="https://b.test/y",
                            )
                        ],
                        findings_by_url=findings_by_url,
                    ),
                    _backed_stmt(
                        "S3",
                        "Site A says Model B is fine.",
                        items=[
                            ItemMark(
                                name="Model B",
                                verdict="fine",
                                source_url="https://a.test/x",
                            )
                        ],
                        findings_by_url=findings_by_url,
                    ),
                ],
            )
        ],
        statement_verdicts=_verdicts("S1", "S2", "S3"),
    )
    table = options_table(composition)
    labels = labels_of(table)
    assert labels.count("Sony WH-1000XM6") == 1  # the two spellings folded to one row
    assert "Sony WH-1000XM6" in labels
    merged_row = table.rows[labels.index("Sony WH-1000XM6")]
    assert {e.source_url for e in merged_row[1].entries} == {
        "https://a.test/x",
        "https://b.test/y",
    }


# =============================================================================
# Review round 2: fail closed when a statement cites no known finding.
# =============================================================================


def test_mark_with_no_cited_findings_is_dropped_not_trusted() -> None:
    findings_by_url = {"https://a.test/x": _finding_for_url("https://a.test/x")}
    composition = _composition(
        sub_topics=[_topic("topic-01", required=True, priority=1)],
        findings=list(findings_by_url.values()),
        sections=[
            _section(
                "topic-01",
                "Sound",
                [
                    _stmt(
                        "S1",
                        "Site A says Model A is great.",
                        items=[
                            ItemMark(
                                name="Model A",
                                verdict="great",
                                source_url="https://anywhere.test/x",
                            )
                        ],
                        finding_ids=[],
                    ),
                    _backed_stmt(
                        "S2",
                        "Site A says Model B is great.",
                        items=[
                            ItemMark(
                                name="Model B",
                                verdict="great",
                                source_url="https://a.test/x",
                            )
                        ],
                        findings_by_url=findings_by_url,
                    ),
                    _backed_stmt(
                        "S3",
                        "Site A says Model C is great.",
                        items=[
                            ItemMark(
                                name="Model C",
                                verdict="great",
                                source_url="https://a.test/x",
                            )
                        ],
                        findings_by_url=findings_by_url,
                    ),
                ],
            )
        ],
        statement_verdicts=_verdicts("S1", "S2", "S3"),
    )
    table = options_table(composition)
    assert "Model A" not in labels_of(table)
    assert "Model B" in labels_of(table)
    assert "Model C" in labels_of(table)
    assert any(
        "Model A" in msg and "the statement cites no finding this report carries" in msg
        for msg in composition.dropped_marks
    )


def test_mark_citing_only_unknown_finding_ids_is_dropped_not_trusted() -> None:
    findings_by_url = {"https://a.test/x": _finding_for_url("https://a.test/x")}
    composition = _composition(
        sub_topics=[_topic("topic-01", required=True, priority=1)],
        findings=list(
            findings_by_url.values()
        ),  # "F-unknown" resolves to nothing: no such finding is carried
        sections=[
            _section(
                "topic-01",
                "Sound",
                [
                    _stmt(
                        "S1",
                        "Site A says Model A is great.",
                        items=[
                            ItemMark(
                                name="Model A",
                                verdict="great",
                                source_url="https://anywhere.test/x",
                            )
                        ],
                        finding_ids=["F-unknown"],
                    ),
                    _backed_stmt(
                        "S2",
                        "Site A says Model B is great.",
                        items=[
                            ItemMark(
                                name="Model B",
                                verdict="great",
                                source_url="https://a.test/x",
                            )
                        ],
                        findings_by_url=findings_by_url,
                    ),
                    _backed_stmt(
                        "S3",
                        "Site A says Model C is great.",
                        items=[
                            ItemMark(
                                name="Model C",
                                verdict="great",
                                source_url="https://a.test/x",
                            )
                        ],
                        findings_by_url=findings_by_url,
                    ),
                ],
            )
        ],
        statement_verdicts=_verdicts("S1", "S2", "S3"),
    )
    table = options_table(composition)
    assert "Model A" not in labels_of(table)
    assert "Model B" in labels_of(table)
    assert "Model C" in labels_of(table)
    assert any(
        "Model A" in msg and "the statement cites no finding this report carries" in msg
        for msg in composition.dropped_marks
    )


def test_earlier_edition_with_no_known_finding_prints_no_date_and_no_vintage() -> None:
    row, finding = _row(
        "K001",
        url="https://a.test/x",
        value="10",
        unit="GW",
        target_ids=["req-a"],
        finding_target_ids=["req-a"],
        earlier=[
            EarlierEdition(
                value="9 GW",
                release="January 2024 Preliminary Inventory",
                finding_id="F-unknown-earlier",
            )
        ],
    )
    other_row, other_finding = _row(
        "K002",
        url="https://b.test/y",
        value="20",
        unit="GW",
        target_ids=["req-b"],
        finding_target_ids=["req-b"],
    )
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-x",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="req-a",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                    EvidenceTarget(
                        target_id="req-b",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                ],
            )
        ],
        findings=[
            finding,
            other_finding,
        ],  # note: no "F-unknown-earlier" finding in the composition
        fact_rows=[row, other_row],
    )
    table = findings_table(composition)
    assert table is not None
    result = {r[0].row_ids[0]: r[1].text for r in table.rows}["K001"]
    assert result == "10 GW; earlier: 9 GW"
    assert "January 2024 Preliminary Inventory" not in result


# =============================================================================
# Whole-branch review: P1-1 (relay credit), P3-1 (dropped_marks dedup),
# P3-3 (name the period_resolved_from basis).
# =============================================================================


def test_options_table_credits_the_relayed_bodys_judgement_not_the_relay_alone() -> (
    None
):
    bi_url = "https://businessinsider.com/best-headphones"
    other_url = "https://other.test/page"
    text = (
        "According to Wirecutter, as reported by Business Insider, the Sony "
        "WH-1000XM6 is the best overall pick."
    )
    read = make_read(text, url=bi_url, title="Best Headphones")
    finding = make_finding(read, text, attributed_issuer="Wirecutter")
    other_finding = _finding_for_url(other_url)
    composition = _composition(
        sub_topics=[_topic("topic-01", required=True, priority=1)],
        findings=[finding, other_finding],
        page_credits={bi_url: PageCredit(publisher="Business Insider")},
        sections=[
            _section(
                "topic-01",
                "Sound",
                [
                    _stmt(
                        "S1",
                        text,
                        items=[
                            ItemMark(
                                name=XM6,
                                verdict="the best overall pick",
                                picked=True,
                                source_url=bi_url,
                            )
                        ],
                        finding_ids=[finding_fingerprint(finding)],
                    ),
                    _backed_stmt(
                        "S2",
                        "Site B says Model B is fine.",
                        items=[
                            ItemMark(
                                name="Model B", verdict="fine", source_url=other_url
                            )
                        ],
                        findings_by_url={other_url: other_finding},
                    ),
                ],
            )
        ],
        statement_verdicts=_verdicts("S1", "S2"),
    )
    table = options_table(composition)
    xm6_row = table.rows[labels_of(table).index(XM6)]
    sound_cell, recommended = xm6_row[1], xm6_row[-1]
    assert (
        sound_cell.entries[0].text == "the best overall pick, according to Wirecutter"
    )
    assert recommended.entries[0].text == "Wirecutter, reported by Business Insider"
    # never credit the relay alone: "Business Insider" must not stand in for the picker
    assert recommended.entries[0].text != "Business Insider"


def test_options_table_credits_the_pages_own_pick_unchanged() -> None:
    url = "https://cnet.com/best-headphones"
    findings_by_url = {url: _finding_for_url(url)}
    composition = _composition(
        sub_topics=[_topic("topic-01", required=True, priority=1)],
        findings=list(findings_by_url.values()),
        page_credits={url: PageCredit(publisher="CNET")},
        sections=[
            _section(
                "topic-01",
                "Sound",
                [
                    _backed_stmt(
                        "S1",
                        "CNET picks the Sony WH-1000XM6 as the best overall.",
                        items=[
                            ItemMark(
                                name=XM6,
                                verdict="the best overall",
                                picked=True,
                                source_url=url,
                            )
                        ],
                        findings_by_url=findings_by_url,
                    ),
                    _backed_stmt(
                        "S2",
                        "CNET says Model B is fine.",
                        items=[
                            ItemMark(name="Model B", verdict="fine", source_url=url)
                        ],
                        findings_by_url=findings_by_url,
                    ),
                ],
            )
        ],
        statement_verdicts=_verdicts("S1", "S2"),
    )
    table = options_table(composition)
    xm6_row = table.rows[labels_of(table).index(XM6)]
    sound_cell, recommended = xm6_row[1], xm6_row[-1]
    assert sound_cell.entries[0].text == "the best overall"
    assert recommended.entries[0].text == ""


def test_options_table_credits_the_relayed_body_from_verified_figure_context() -> None:
    """A finding with no ``attributed_issuer`` can still be a relay: its own
    kept figure's Context Check already found ``attribution == "relayed"``."""
    relay_url = "https://relay.test/page"
    text = "Example Wire reports the Sony WH-1000XM6 costs $390, its lab's own pick."
    read = make_read(text, url=relay_url, title="Relay page")
    finding = make_finding(read, text, figures=[figure("390", "USD")])
    result = FigureResult(
        figure=finding.figures[0],
        matched=True,
        evidence_words=text,
        context=FigureContext(
            attribution="relayed", organisation="Example Wire", kind="actual"
        ),
    )
    finding = finding.model_copy(
        update={
            "verification": FindingVerification(
                status="verified", figure_results=[result]
            )
        }
    )
    other_finding = _finding_for_url("https://other.test/page")
    composition = _composition(
        sub_topics=[_topic("topic-01", required=True, priority=1)],
        findings=[finding, other_finding],
        page_credits={relay_url: PageCredit(publisher="Relay Host")},
        sections=[
            _section(
                "topic-01",
                "Sound",
                [
                    _stmt(
                        "S1",
                        text,
                        items=[
                            ItemMark(
                                name=XM6,
                                verdict="its lab's own pick",
                                picked=True,
                                source_url=relay_url,
                            )
                        ],
                        finding_ids=[finding_fingerprint(finding)],
                    ),
                    _backed_stmt(
                        "S2",
                        "Site B says Model B is fine.",
                        items=[
                            ItemMark(
                                name="Model B",
                                verdict="fine",
                                source_url="https://other.test/page",
                            )
                        ],
                        findings_by_url={"https://other.test/page": other_finding},
                    ),
                ],
            )
        ],
        statement_verdicts=_verdicts("S1", "S2"),
    )
    table = options_table(composition)
    xm6_row = table.rows[labels_of(table).index(XM6)]
    recommended = xm6_row[-1]
    assert recommended.entries[0].text == "Example Wire, reported by Relay Host"


def test_dropped_marks_are_not_duplicated_across_repeated_calls() -> None:
    composition = _composition(
        sub_topics=[_topic("topic-01", required=True, priority=1)],
        sections=[
            _section(
                "topic-01",
                "Sound",
                [
                    _stmt(
                        "S1",
                        "Site A says Model A is great.",
                        items=[
                            ItemMark(
                                name="Model A",
                                verdict="great",
                                source_url="https://anywhere.test/x",
                            )
                        ],
                        finding_ids=[],
                    )
                ],
            )
        ],
        statement_verdicts=_verdicts("S1"),
    )
    options_table(composition)
    options_table(composition)
    matching = [
        msg
        for msg in composition.dropped_marks
        if "Model A" in msg
        and "the statement cites no finding this report carries" in msg
    ]
    assert len(matching) == 1


def test_period_resolved_from_names_the_release_date_as_basis() -> None:
    row, finding = _row(
        "K001",
        url="https://a.test/x",
        value="10",
        unit="GW",
        period="2025",
        period_resolved_from="2025-06-10",
        subject="Battery storage capacity",
        target_ids=["req-a"],
        finding_target_ids=["req-a"],
        release_date="2025-06-10",
    )
    other_row, other_finding = _row(
        "K002",
        url="https://b.test/y",
        value="20",
        unit="GW",
        target_ids=["req-b"],
        finding_target_ids=["req-b"],
    )
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-x",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="req-a",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                    EvidenceTarget(
                        target_id="req-b",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                ],
            )
        ],
        findings=[finding, other_finding],
        fact_rows=[row, other_row],
    )
    table = findings_table(composition)
    assert table is not None
    what = {r[0].row_ids[0]: r[0].text for r in table.rows}["K001"]
    assert "counted from the release date, 2025-06-10" in what
    assert "counted from the page's date" not in what


def test_period_resolved_from_names_the_statement_date_as_basis() -> None:
    row, finding = _row(
        "K001",
        url="https://a.test/x",
        value="10",
        unit="GW",
        period="2025",
        period_resolved_from="2025-03-12",
        subject="Battery storage capacity",
        target_ids=["req-a"],
        finding_target_ids=["req-a"],
        statement_date="2025-03-12",
    )
    other_row, other_finding = _row(
        "K002",
        url="https://b.test/y",
        value="20",
        unit="GW",
        target_ids=["req-b"],
        finding_target_ids=["req-b"],
    )
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-x",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="req-a",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                    EvidenceTarget(
                        target_id="req-b",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                ],
            )
        ],
        findings=[finding, other_finding],
        fact_rows=[row, other_row],
    )
    table = findings_table(composition)
    assert table is not None
    what = {r[0].row_ids[0]: r[0].text for r in table.rows}["K001"]
    assert "counted from the statement date, 2025-03-12" in what


def test_period_resolved_from_falls_back_to_the_pages_date() -> None:
    row, finding = _row(
        "K001",
        url="https://a.test/x",
        value="10",
        unit="GW",
        period="2026",
        period_resolved_from="2026-02-20",
        subject="Battery storage capacity",
        target_ids=["req-a"],
        finding_target_ids=["req-a"],
    )
    other_row, other_finding = _row(
        "K002",
        url="https://b.test/y",
        value="20",
        unit="GW",
        target_ids=["req-b"],
        finding_target_ids=["req-b"],
    )
    composition = _composition(
        sub_topics=[
            SubTopic(
                coverage_id="topic-x",
                title="t",
                rationale="r",
                search_queries=["q"],
                success_criteria=["c"],
                priority=1,
                evidence_targets=[
                    EvidenceTarget(
                        target_id="req-a",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                    EvidenceTarget(
                        target_id="req-b",
                        coverage_id="topic-x",
                        question="q",
                        required=True,
                        measure="m",
                    ),
                ],
            )
        ],
        findings=[finding, other_finding],
        fact_rows=[row, other_row],
    )
    table = findings_table(composition)
    assert table is not None
    what = {r[0].row_ids[0]: r[0].text for r in table.rows}["K001"]
    assert "counted from the page's date, 2026-02-20" in what


# =============================================================================
# Whole-branch re-review R-2: the relay credit must follow the finding the
# mark rests on, never whichever finding happens to share its page.
# =============================================================================


def _relay_and_own_findings_on_one_page(bi_url: str) -> tuple[Finding, Finding]:
    relay_text = (
        "According to Wirecutter, as reported by Business Insider, the Sony "
        "WH-1000XM6 is the best overall pick."
    )
    relay_read = make_read(relay_text, url=bi_url, title="Best Headphones")
    relayed_finding = make_finding(
        relay_read, relay_text, attributed_issuer="Wirecutter"
    )
    own_text = "Business Insider also lists the Sony WH-1000XM6 at $390."
    own_read = make_read(own_text, url=bi_url, title="Best Headphones")
    own_finding = make_finding(own_read, own_text)
    return relayed_finding, own_finding


def test_relay_credit_ignores_finding_list_order_when_the_statement_cites_one() -> None:
    """R-2 repro: the relay decision must follow the finding the STATEMENT
    cites on that page, never whichever finding happens to come last in
    ``composition.findings`` for that URL."""
    bi_url = "https://businessinsider.com/best-headphones"
    other_url = "https://other.test/page"
    relayed_finding, own_finding = _relay_and_own_findings_on_one_page(bi_url)
    other_finding = _finding_for_url(other_url)

    def _table_for(findings_order: list[Finding]) -> None:
        composition = _composition(
            sub_topics=[_topic("topic-01", required=True, priority=1)],
            findings=[*findings_order, other_finding],
            page_credits={bi_url: PageCredit(publisher="Business Insider")},
            sections=[
                _section(
                    "topic-01",
                    "Sound",
                    [
                        _stmt(
                            "S1",
                            "According to Wirecutter, as reported by Business Insider, "
                            "the Sony WH-1000XM6 is the best overall pick.",
                            items=[
                                ItemMark(
                                    name=XM6,
                                    verdict="the best overall pick",
                                    picked=True,
                                    source_url=bi_url,
                                )
                            ],
                            finding_ids=[finding_fingerprint(relayed_finding)],
                        ),
                        _backed_stmt(
                            "S2",
                            "Site B says Model B is fine.",
                            items=[
                                ItemMark(
                                    name="Model B", verdict="fine", source_url=other_url
                                )
                            ],
                            findings_by_url={other_url: other_finding},
                        ),
                    ],
                )
            ],
            statement_verdicts=_verdicts("S1", "S2"),
        )
        table = options_table(composition)
        xm6_row = table.rows[labels_of(table).index(XM6)]
        recommended = xm6_row[-1]
        assert recommended.entries[0].text == "Wirecutter, reported by Business Insider"

    _table_for([relayed_finding, own_finding])
    _table_for([own_finding, relayed_finding])


def test_relay_credit_defers_to_the_page_when_the_statement_cites_mixed_findings() -> (
    None
):
    """R-2: when a statement cites more than one finding on the same page and
    they disagree on who is credited, a bare mark (no ``finding_id``) never
    guesses which one it rests on — the page's own reading applies."""
    bi_url = "https://businessinsider.com/best-headphones"
    other_url = "https://other.test/page"
    relayed_finding, own_finding = _relay_and_own_findings_on_one_page(bi_url)
    other_finding = _finding_for_url(other_url)
    composition = _composition(
        sub_topics=[_topic("topic-01", required=True, priority=1)],
        findings=[relayed_finding, own_finding, other_finding],
        page_credits={bi_url: PageCredit(publisher="Business Insider")},
        sections=[
            _section(
                "topic-01",
                "Sound",
                [
                    _stmt(
                        "S1",
                        "According to Wirecutter, as reported by Business Insider, the Sony "
                        "WH-1000XM6 is the best overall pick, also listed at $390.",
                        items=[
                            ItemMark(
                                name=XM6,
                                verdict="the best overall pick",
                                picked=True,
                                source_url=bi_url,
                            )
                        ],
                        finding_ids=[
                            finding_fingerprint(relayed_finding),
                            finding_fingerprint(own_finding),
                        ],
                    ),
                    _backed_stmt(
                        "S2",
                        "Site B says Model B is fine.",
                        items=[
                            ItemMark(
                                name="Model B", verdict="fine", source_url=other_url
                            )
                        ],
                        findings_by_url={other_url: other_finding},
                    ),
                ],
            )
        ],
        statement_verdicts=_verdicts("S1", "S2"),
    )
    table = options_table(composition)
    xm6_row = table.rows[labels_of(table).index(XM6)]
    recommended = xm6_row[-1]
    assert recommended.entries[0].text == ""


def test_relay_credit_falls_back_to_statement_citations_when_mark_finding_id_is_none() -> (
    None
):
    """R-2: ``ItemMark.finding_id`` defaults to ``None`` (unset by the writer,
    or older data); until it is set, the relay decision falls back to the
    statement's own cited findings on the mark's page."""
    assert ItemMark(name="x", source_url="https://a.test/x").finding_id is None
    bi_url = "https://businessinsider.com/best-headphones"
    relayed_finding, _own_finding = _relay_and_own_findings_on_one_page(bi_url)
    composition = _composition(
        sub_topics=[_topic("topic-01", required=True, priority=1)],
        findings=[relayed_finding],
        page_credits={bi_url: PageCredit(publisher="Business Insider")},
        sections=[
            _section(
                "topic-01",
                "Sound",
                [
                    _stmt(
                        "S1",
                        "According to Wirecutter, as reported by Business Insider, "
                        "the Sony WH-1000XM6 is the best overall pick.",
                        items=[
                            ItemMark(
                                name=XM6,
                                verdict="the best overall pick",
                                picked=True,
                                source_url=bi_url,
                            )
                        ],
                        finding_ids=[finding_fingerprint(relayed_finding)],
                    ),
                    _stmt(
                        "S2",
                        "Site A says Model B is fine.",
                        items=[
                            ItemMark(name="Model B", verdict="fine", source_url=bi_url)
                        ],
                        finding_ids=[finding_fingerprint(relayed_finding)],
                    ),
                ],
            )
        ],
        statement_verdicts=_verdicts("S1", "S2"),
    )
    table = options_table(composition)
    xm6_row = table.rows[labels_of(table).index(XM6)]
    recommended = xm6_row[-1]
    assert recommended.entries[0].text == "Wirecutter, reported by Business Insider"


def test_relay_credit_prefers_the_marks_own_finding_id_over_the_fallback() -> None:
    """R-2: when ``mark.finding_id`` names one of two mixed findings on the
    same page explicitly, that decides the credit — the mark is not left to
    the "mixed citations, defer to the page" fallback that would otherwise
    apply when a bare mark cites both."""
    bi_url = "https://businessinsider.com/best-headphones"
    other_url = "https://other.test/page"
    relayed_finding, own_finding = _relay_and_own_findings_on_one_page(bi_url)
    other_finding = _finding_for_url(other_url)
    composition = _composition(
        sub_topics=[_topic("topic-01", required=True, priority=1)],
        findings=[relayed_finding, own_finding, other_finding],
        page_credits={bi_url: PageCredit(publisher="Business Insider")},
        sections=[
            _section(
                "topic-01",
                "Sound",
                [
                    _stmt(
                        "S1",
                        "According to Wirecutter, as reported by Business Insider, the Sony "
                        "WH-1000XM6 is the best overall pick, also listed at $390.",
                        items=[
                            ItemMark(
                                name=XM6,
                                verdict="the best overall pick",
                                picked=True,
                                source_url=bi_url,
                                finding_id=finding_fingerprint(relayed_finding),
                            )
                        ],
                        finding_ids=[
                            finding_fingerprint(relayed_finding),
                            finding_fingerprint(own_finding),
                        ],
                    ),
                    _backed_stmt(
                        "S2",
                        "Site B says Model B is fine.",
                        items=[
                            ItemMark(
                                name="Model B", verdict="fine", source_url=other_url
                            )
                        ],
                        findings_by_url={other_url: other_finding},
                    ),
                ],
            )
        ],
        statement_verdicts=_verdicts("S1", "S2"),
    )
    table = options_table(composition)
    xm6_row = table.rows[labels_of(table).index(XM6)]
    recommended = xm6_row[-1]
    assert recommended.entries[0].text == "Wirecutter, reported by Business Insider"
