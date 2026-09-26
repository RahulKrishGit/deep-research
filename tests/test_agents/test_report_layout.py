"""Spec §3, §8, §9, §10: the answer-first skeleton, the Sources line, the
evidence log's audit view, and the fallbacks -- plus the label machinery
(``_point_labels``/``_row_label``) the terminal reviewer still depends on
(R1), which this format keeps out of the reader-facing report itself.
"""

from __future__ import annotations

import re

from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report import (
    _FACTS_HEADER,
    _point_labels,
    _row_label,
    evidence_report_filename,
    figure_label,
    render_finding_log,
    render_quality_record,
    render_written_report,
    written_citations,
)
from deep_research.agents.sources import normalize_source_url
from deep_research.utils.types import (
    EarlierEdition,
    FactRow,
    FigureContext,
    FigureResult,
    Finding,
    FindingVerification,
    NotFoundTarget,
    PageCredit,
    RejectedDraftPoint,
    ReportComposition,
    ReportPart,
    ReportPoint,
    ReportSection,
    ReportStatement,
    ReportTable,
    ResearchState,
    SubTopic,
    TableCell,
    TableEntry,
    UnreachablePage,
)
from tests.evidence_fakes import figure, make_finding, make_read, make_target

VERDICT_WORDS = re.compile(r"\b(verified|unverified|corroborat\w*|independently|insufficient evidence|contested|contradicted|not established)\b", re.I)


def _finding(url, snippet, value, unit, *, organisation, attribution="own", kind="actual", period="2024", dropped_figure=None):
    read = make_read(snippet, url=url, title=f"Page at {url}")
    figures = [figure(value, unit, period, kind)] + ([dropped_figure] if dropped_figure else [])
    finding = make_finding(read, snippet, figures=figures, target_ids=["topic-01-target-01"])
    results = [FigureResult(figure=figures[0], matched=True, evidence_words=snippet,
                            context=FigureContext(period=period, attribution=attribution,
                                                  organisation=organisation, kind=kind))]
    if dropped_figure:
        results.append(FigureResult(figure=dropped_figure, matched=True, dropped_reason="context_rejected",
                                    reason="A growth rate, not a capacity."))
    status = "verified_corrected" if dropped_figure else "verified"
    return finding.model_copy(update={"verification": FindingVerification(status=status, figure_results=results)})


def _composition():
    eia = _finding("https://www.eia.gov/todayinenergy/detail.php?id=64705",
                   "Generators added 10.4 gigawatts (GW) of new battery storage capacity in 2024,",
                   "10.4", "GW", organisation="U.S. Energy Information Administration",
                   dropped_figure=figure("66", "%", "2024", "actual"))
    steo = _finding("https://ent.news/2025/1/940.pdf", "battery storage capacity growing by 47% (14 GW) in 2025",
                    "14", "GW", organisation="U.S. Energy Information Administration",
                    attribution="relayed", kind="forecast", period="2025")
    eia_id, steo_id = finding_fingerprint(eia), finding_fingerprint(steo)
    rows = [
        FactRow(row_id="K001", organisation="U.S. Energy Information Administration", attribution="own",
                measure="battery storage power capacity added", period="2024", value="10.4 GW", kind="actual",
                release="released 2025-03-12", finding_id=eia_id,
                target_ids=["topic-01-target-01"]),
        FactRow(row_id="K002", organisation="U.S. Energy Information Administration", attribution="relayed",
                relay_host="ent.news", measure="battery storage power capacity added", period="2025",
                value="14 GW", kind="forecast", release="January 2025 STEO", finding_id=steo_id,
                target_ids=["topic-02-target-01"]),
    ]
    def point(n, text, finding):
        return ReportPoint(text=text, source_urls=[finding.source_url],
                           statement=ReportStatement(statement_id=f"S00{n}", text=text,
                                                     finding_ids=[finding_fingerprint(finding)]))
    return ReportComposition(
        question="How much grid-scale battery storage capacity was added in the United States in 2024, and what do the latest forecasts project for 2025?",
        session_id="s", as_of="2026-09-24T00:00:00+00:00", scope="United States",
        findings=[eia, steo],
        summary=[point(1, "Generators added 10.4 GW of battery storage in 2024.", eia),
                 point(2, "EIA expects 14 GW of battery storage to be added in 2025.", steo)],
        sections=[ReportSection(title="Basis", coverage_id="topic-01",
                                points=[point(3, "EIA counts 10.4 GW of new capacity.", eia)])],
        fact_rows=rows,
        not_found=[NotFoundTarget(target_id="topic-03-target-01", question="What does BloombergNEF project for 2025?",
                                  queries=["BloombergNEF 2025 US storage forecast"], pages_read=["https://about.bnef.com/x"], searched=True)],
        finding_labels={"F01": eia_id, "F02": steo_id},
    )


def _plain_composition(**overrides: object) -> ReportComposition:
    """The fixture without its plan vocabulary or its Not found trail."""
    return _composition().model_copy(update=overrides)


def _section_body(markdown: str, heading: str) -> str:
    """The text between ``heading`` and the next H2 heading (or the end)."""
    start = markdown.index(heading)
    tail = markdown[start + len(heading):]
    match = re.search(r"(?m)^## ", tail)
    return tail[: match.start()] if match else tail


# --- label machinery the terminal reviewer still reads (R1) -------------------
#
# ``_point_labels``/``_row_label``/``figure_label`` no longer print beside a
# reader-facing sentence (spec §3.1 rule 8 cuts that suffix); they stay
# because ``report_reviewer.py`` imports them to show the reviewer model the
# same provenance words the reader used to see. Their own behaviour is
# unchanged, so their tests stay.


def test_figure_label_follows_the_spec_labels() -> None:
    assert figure_label(organisation="EIA", attribution="own", relay_host=None, kind="actual",
                        release="released 2025-03-12", unchecked=False) == "EIA's own figure; actual; released 2025-03-12"
    assert figure_label(organisation="EIA", attribution="relayed", relay_host="ent.news", kind="forecast",
                        release="January 2025 STEO", unchecked=True) == "relayed by ent.news from EIA; forecast (January 2025 STEO); unchecked context"
    assert figure_label(organisation="", attribution="unattributed", relay_host=None, kind="forecast",
                        release=None, unchecked=False) == "source does not attribute it; forecast (release not stated on the page)"


def test_a_resolved_period_is_named_on_the_label() -> None:
    assert figure_label(organisation="Example Statistical Agency", attribution="own",
                        relay_host=None, kind="actual", release=None, unchecked=False,
                        period_resolved_from="2026-02-20") == (
        "Example Statistical Agency's own figure; actual; "
        "period resolved from the page date 2026-02-20"
    )


def test_a_sentence_carries_the_label_of_the_subject_it_names() -> None:
    base = _composition()
    row = base.fact_rows[0]
    rows = [row.model_copy(update={"row_id": "K001", "subject": "Model A"}),
            row.model_copy(update={"row_id": "K002", "subject": "Model B",
                                   "organisation": "Example Test Lab"})]
    composition = base.model_copy(update={"fact_rows": rows})
    point = base.summary[0].model_copy(update={"text": f"Model B had {row.value}."})
    assert _point_labels(point, composition) == [_row_label(rows[1])]


def test_an_article_in_the_targets_question_keeps_a_sentence_to_its_subject() -> None:
    """Fix round 1 (CRITICAL 1): a target's "a" never strips the subject's "A" from a label."""
    base = _composition()
    target = make_target(question="What noise rating did testers give a kettle?")
    topic = SubTopic(coverage_id="topic-01", title="Kettle noise", rationale="r",
                     search_queries=["kettle noise rating"], success_criteria=["c"], priority=1,
                     evidence_targets=[target])
    row = base.fact_rows[0].model_copy(update={"target_ids": [target.target_id]})
    rows = [row.model_copy(update={"row_id": "K001", "subject": "Model A"}),
            row.model_copy(update={"row_id": "K002", "subject": "Model B",
                                   "organisation": "Example Test Lab"})]
    composition = base.model_copy(update={"fact_rows": rows, "sub_topics": [topic]})
    point = base.summary[0].model_copy(update={"text": f"Model B had {row.value}."})
    assert _point_labels(point, composition) == [_row_label(rows[1])]


def test_a_comparison_target_keeps_each_label_with_its_own_product() -> None:
    """Task 5.6c: a sentence naming K1 never carries K2's label, and the reverse."""
    base = _composition()
    target = make_target(question=("How do the Kettle K1 and the Kettle K2 compare on the "
                                   "Example Tester noise rating for 2026?"),
                         measure="noise rating", unit_dimension="rating", period=None,
                         organisation="Example Test Lab")
    topic = SubTopic(coverage_id="topic-01", title="Kettle ratings", rationale="r",
                     search_queries=["kettle noise rating"], success_criteria=["c"], priority=1,
                     evidence_targets=[target])
    row = base.fact_rows[0].model_copy(update={"target_ids": [target.target_id],
                                               "organisation": "Example Test Lab"})
    rows = [row.model_copy(update={"row_id": "K001", "subject": "Kettle K1"}),
            row.model_copy(update={"row_id": "K002", "subject": "Kettle K2",
                                   "organisation": "Example Second Lab"})]
    composition = base.model_copy(update={"fact_rows": rows, "sub_topics": [topic]})
    first = base.summary[0].model_copy(update={"text": f"Kettle K1 scored {row.value}."})
    second = base.summary[0].model_copy(update={"text": f"Kettle K2 scored {row.value}."})
    assert _point_labels(first, composition) == [_row_label(rows[0])]
    assert _point_labels(second, composition) == [_row_label(rows[1])]


def test_an_article_in_the_sentence_never_hands_a_label_to_the_other_subject() -> None:
    """Fix round 1 (Important 2): the give-away "a" is required as the whole name, not alone."""
    base = _composition()
    target = make_target(question=("How do the Model A and the Model B compare on the "
                                   "Example Tester noise rating for 2026?"),
                         measure="noise rating", unit_dimension="rating", period=None,
                         geography=None, organisation="Example Test Lab")
    topic = SubTopic(coverage_id="topic-01", title="Kettle ratings", rationale="r",
                     search_queries=["kettle noise rating"], success_criteria=["c"], priority=1,
                     evidence_targets=[target])
    row = base.fact_rows[0].model_copy(update={"target_ids": [target.target_id],
                                               "organisation": "Example Test Lab"})
    rows = [row.model_copy(update={"row_id": "K001", "subject": "Model A"}),
            row.model_copy(update={"row_id": "K002", "subject": "Model B",
                                   "organisation": "Example Second Lab"})]
    composition = base.model_copy(update={"fact_rows": rows, "sub_topics": [topic]})
    about_b = base.summary[0].model_copy(update={"text": f"Model B scored a {row.value} noise rating."})
    about_a = base.summary[0].model_copy(update={"text": f"Model A scored a {row.value} noise rating."})
    assert _point_labels(about_b, composition) == [_row_label(rows[1])]
    assert _point_labels(about_a, composition) == [_row_label(rows[0])]


def _two_period_composition() -> ReportComposition:
    """One page stating one value twice: a 2024 actual and a 2025 forecast."""
    text = "Sales grew 12 percent in 2024, and the agency expects growth of 12 percent in 2025."
    read = make_read(text, url="https://agency.example.test/outlook", title="Outlook")
    figures = [figure("12", "%", "2024", "actual"), figure("12", "%", "2025", "forecast")]
    finding = make_finding(read, text, figures=figures, target_ids=["topic-01-target-01"])
    results = [
        FigureResult(figure=figures[0], matched=True, evidence_words=text,
                     context=FigureContext(period="2024", attribution="own",
                                           organisation="Example Agency", kind="actual")),
        FigureResult(figure=figures[1], matched=True, evidence_words=text,
                     context=FigureContext(period="2025", attribution="own",
                                           organisation="Example Agency", kind="forecast")),
    ]
    finding = finding.model_copy(update={"verification": FindingVerification(status="verified",
                                                                            figure_results=results)})
    fingerprint = finding_fingerprint(finding)
    rows = [
        FactRow(row_id="K001", organisation="Example Agency", attribution="own", measure="sales growth",
                period="2024", value="12 %", kind="actual", finding_id=fingerprint),
        FactRow(row_id="K002", organisation="Example Agency", attribution="own", measure="sales growth",
                period="2025", value="12 %", kind="forecast", finding_id=fingerprint),
    ]

    def point(n: int, text: str) -> ReportPoint:
        return ReportPoint(text=text, source_urls=[finding.source_url],
                           statement=ReportStatement(statement_id=f"S00{n}", text=text,
                                                     finding_ids=[fingerprint]))

    return ReportComposition(
        question="How did sales grow in 2024, and what does the agency expect for 2025?",
        session_id="s", as_of="2026-09-24T00:00:00+00:00", scope="Example",
        findings=[finding], fact_rows=rows, finding_labels={"F01": fingerprint},
        summary=[point(1, "Sales grew 12 percent in 2024."),
                 point(2, "The agency expects growth of 12 percent in 2025.")],
    )


def test_a_sentence_carries_only_the_row_of_the_period_it_states() -> None:
    composition = _two_period_composition()
    current, forecast = composition.summary
    assert _point_labels(current, composition) == [_row_label(composition.fact_rows[0])]
    assert _point_labels(forecast, composition) == [_row_label(composition.fact_rows[1])]


def test_a_sentence_citing_a_relay_copy_carries_the_relays_label() -> None:
    """D13: the label beside a sentence is its own cited page's, not the row primary's."""
    own_page = _finding("https://agency.example.test/own-release",
                        "The agency expects 14 GW of additions in 2025.",
                        "14", "GW", organisation="Example Agency", kind="forecast", period="2025")
    relay_page = _finding("https://gazette.example.test/story",
                          "According to the Example Agency, as reported by the Example Gazette, "
                          "14 GW of additions are expected in 2025.",
                          "14", "GW", organisation="Example Agency", attribution="relayed",
                          kind="forecast", period="2025")
    primary = finding_fingerprint(own_page)
    row = FactRow(row_id="K001", organisation="Example Agency", attribution="own", measure="additions",
                  period="2025", value="14 GW", kind="forecast", finding_id=primary,
                  duplicate_finding_ids=[finding_fingerprint(relay_page)])
    text = "According to the Example Agency, as reported by the Example Gazette, 14 GW of additions are expected in 2025."
    point = ReportPoint(text=text, source_urls=[relay_page.source_url],
                        statement=ReportStatement(statement_id="S001", text=text,
                                                  finding_ids=[finding_fingerprint(relay_page)]))
    composition = ReportComposition(
        question="What is the outlook for additions?", session_id="s", as_of="2026-09-24T00:00:00+00:00",
        scope="Example", findings=[own_page, relay_page], fact_rows=[row], summary=[point],
    )
    assert _point_labels(point, composition) == [
        "relayed by gazette.example.test from Example Agency; "
        "forecast (release not stated on the page)"
    ]


# --- §3: the reader skeleton, in order -----------------------------------------


def _skeleton_composition() -> ReportComposition:
    finding = _finding("https://agency.example.test/report",
                       "Example Agency reports 10 GW added in 2024.",
                       "10", "GW", organisation="Example Agency")
    fid = finding_fingerprint(finding)
    point = ReportPoint(text="Example Agency reports 10 GW added in 2024.",
                        source_urls=[finding.source_url],
                        statement=ReportStatement(statement_id="S001",
                                                  text="Example Agency reports 10 GW added in 2024.",
                                                  finding_ids=[fid]))
    table = ReportTable(
        shape="findings",
        columns=["What was measured", "Result", "Who reported it (and when)", "Source"],
        rows=[[TableCell(text="Capacity added"), TableCell(text="10 GW, actual"),
              TableCell(text="Example Agency"), TableCell(finding_ids=[fid])]],
        caption="No figure in this table is a forecast.",
    )
    return ReportComposition(
        question="How much capacity was added in 2024?", session_id="s1", iteration=0,
        as_of="2026-09-25T00:00:00+00:00", findings=[finding], summary=[point], table=table,
        sections=[ReportSection(title="Capacity", coverage_id="topic-01", points=[point])],
        not_found=[NotFoundTarget(target_id="topic-02-target-01", question="What about 2025?", searched=True)],
    )


def test_the_reader_report_has_the_spec_skeleton_in_order() -> None:
    report = render_written_report(_skeleton_composition())
    headings = [line for line in report.splitlines() if line.startswith("#")]

    assert headings == [
        "# How much capacity was added in 2024?",
        "## Bottom line",
        "## Capacity",
        "## What we couldn't confirm",
        "## Sources",
    ]
    assert "## Executive summary" not in report
    assert "## Key facts" not in report
    assert "## Not found" not in report
    assert "Scope:" not in report
    assert "sources cited" not in report
    assert "| What was measured | Result | Who reported it (and when) | Source |" in report
    assert "*No figure in this table is a forecast.*" in report
    assert report.rstrip().endswith(
        "How this was researched: [evidence log]"
        f"({evidence_report_filename(session_id='s1', iteration=0)})"
    )


def test_no_table_prints_nothing_when_none_qualifies() -> None:
    report = render_written_report(_plain_composition(fact_rows=[]))
    assert "|" not in report
    assert "No figure passed the Evidence Verifier." not in report


# --- §3.1.2: the evidence line --------------------------------------------------


def test_the_evidence_line_states_the_date_and_source_count() -> None:
    report = render_written_report(_composition())
    assert report.splitlines()[2] == "Evidence as of 2026-09-24 · 2 sources"


def test_the_evidence_line_says_no_source_could_be_checked_when_as_of_is_empty() -> None:
    composition = _composition().model_copy(update={"as_of": ""})
    report = render_written_report(composition)
    assert report.splitlines()[2] == "No source could be checked."


def test_the_evidence_line_uses_the_singular_for_one_source() -> None:
    finding = _finding("https://agency.example.test/report", "Example Agency reports X.",
                       "1", "unit", organisation="Example Agency")
    point = ReportPoint(text="Example Agency reports X.", source_urls=[finding.source_url],
                        statement=ReportStatement(statement_id="S001", text="Example Agency reports X.",
                                                  finding_ids=[finding_fingerprint(finding)]))
    composition = ReportComposition(question="q", session_id="s", as_of="2026-09-25T00:00:00+00:00",
                                    findings=[finding], summary=[point])
    report = render_written_report(composition)
    assert report.splitlines()[2] == "Evidence as of 2026-09-25 · 1 source"


# --- §3.1.3: bottom-line markers before the stop --------------------------------


def test_bottom_line_markers_land_before_each_sentences_final_stop() -> None:
    report = render_written_report(_composition())
    body = _section_body(report, "## Bottom line")

    assert "Generators added 10.4 GW of battery storage in 2024 [1]." in body
    assert "EIA expects 14 GW of battery storage to be added in 2025 [2]." in body


def _single_point_composition(text: str) -> ReportComposition:
    finding = _bare_finding("https://agency.example.test/report", "A report", text)
    point = ReportPoint(text=text, source_urls=[finding.source_url],
                        statement=ReportStatement(statement_id="S001", text=text,
                                                  finding_ids=[finding_fingerprint(finding)]))
    return ReportComposition(question="q", session_id="s", findings=[finding], summary=[point])


def test_bottom_line_markers_land_before_a_closing_quote_after_the_stop() -> None:
    """P3: a closing quote after the sentence's stop keeps the stop where it
    was, with the markers before it -- never a second stop after the quote."""
    composition = _single_point_composition('He called it "the best."')
    body = _section_body(render_written_report(composition), "## Bottom line")
    assert body.strip() == 'He called it "the best [1]."'


def test_bottom_line_markers_treat_an_ellipsis_as_one_unit() -> None:
    composition = _single_point_composition("It rose to 5 GW...")
    body = _section_body(render_written_report(composition), "## Bottom line")
    assert body.strip() == "It rose to 5 GW [1]..."


def test_bottom_line_markers_land_before_a_closing_paren_after_the_stop() -> None:
    composition = _single_point_composition("It rose to 5 GW.)")
    body = _section_body(render_written_report(composition), "## Bottom line")
    assert body.strip() == "It rose to 5 GW [1].)"



def test_the_bottom_line_falls_back_when_nothing_was_answered() -> None:
    composition = ReportComposition(question="q", session_id="s")
    report = render_written_report(composition)
    body = _section_body(report, "## Bottom line")

    assert "No source we could check answers this question." in body
    assert "## Sources" not in report


def test_the_bottom_line_falls_back_when_every_part_failed() -> None:
    composition = ReportComposition(
        question="q", session_id="s",
        parts=[ReportPart(coverage_id="topic-01", sub_topic_title="Audio", status="failed"),
               ReportPart(coverage_id="topic-02", sub_topic_title="Mic", status="empty")],
    )
    report = render_written_report(composition)
    body = _section_body(report, "## Bottom line")

    assert ("This report's sections could not be written this time; "
           "the evidence log shows what was verified.") in body



def test_the_bottom_line_does_not_claim_a_part_failed_when_parts_are_only_empty() -> None:
    """R-7: a part recorded as merely 'empty' (no failure, nothing to write)
    must not make the bottom line claim a part's sections could not be
    written -- that wording is reserved for an actual failure."""
    finding = _finding("https://agency.example.test/report",
                       "The agency reports 10 GW added in 2024.",
                       "10", "GW", organisation="Example Agency")
    composition = ReportComposition(
        question="q", session_id="s", findings=[finding],
        parts=[ReportPart(coverage_id="topic-01", sub_topic_title="Capacity", status="empty")],
    )

    report = render_written_report(composition)
    body = _section_body(report, "## Bottom line")

    assert "This report's sections could not be written this time" not in body
    assert ("No finding could be placed in a section this time; the evidence "
           "log lists them.") in body



def test_the_bottom_line_never_denies_an_answer_a_citable_finding_gives() -> None:
    """P1-3 belt and braces: a citable (verified) finding must never be
    hidden behind 'No source we could check answers this question.', even
    when no part kept a point that states it. This fixture's finding was
    simply never placed into any part (R-7: no part recorded and no failure
    either), so the honest reading is the plain unplaced-findings sentence,
    not a claim that a part's sections could not be written.
    """
    finding = _finding("https://agency.example.test/report",
                       "The agency reports 10.4 GW added in 2024.",
                       "10.4", "GW", organisation="Example Agency")
    composition = ReportComposition(question="q", session_id="s", findings=[finding])

    report = render_written_report(composition)
    body = _section_body(report, "## Bottom line")

    assert "No source we could check answers this question." not in body
    assert "This report's sections could not be written this time" not in body
    assert ("No finding could be placed in a section this time; the evidence "
           "log lists them.") in body


def test_the_bottom_line_does_not_deny_an_answer_the_sections_give() -> None:
    """P0: an empty bottom line (a D8 outage -- the draft returned no kept
    sentence) must not claim the report cites nothing when a section below
    it, or the table, does answer."""
    finding = _finding("https://agency.example.test/report",
                       "The agency reports 10 GW added in 2024.",
                       "10", "GW", organisation="Example Agency")
    point = ReportPoint(text="The agency reports 10 GW added in 2024.",
                        source_urls=[finding.source_url],
                        statement=ReportStatement(statement_id="S001",
                                                  text="The agency reports 10 GW added in 2024.",
                                                  finding_ids=[finding_fingerprint(finding)]))
    composition = ReportComposition(
        question="q", session_id="s", findings=[finding],
        sections=[ReportSection(title="Capacity", points=[point])],
    )

    report = render_written_report(composition)
    body = _section_body(report, "## Bottom line")

    assert "No source we could check answers this question." not in body
    assert "A summary could not be written this time" in body



# --- §5, §3.1.7-9: citation order and the Sources line --------------------------


def test_citation_order_is_bottom_line_then_table_then_sections() -> None:
    a = _finding("https://a.example.test/1", "A reports X.", "1", "unit", organisation="A")
    b = _finding("https://b.example.test/1", "B reports Y.", "2", "unit", organisation="B")
    c = _finding("https://c.example.test/1", "C reports Z.", "3", "unit", organisation="C")
    table = ReportTable(
        shape="findings",
        columns=["What was measured", "Result", "Who reported it (and when)", "Source"],
        rows=[[TableCell(text="Y"), TableCell(text="2, actual"), TableCell(text="B"),
              TableCell(finding_ids=[finding_fingerprint(b)])]],
    )
    bottom = ReportPoint(text="A reports X.", source_urls=[a.source_url],
                         statement=ReportStatement(statement_id="S001", text="A reports X.",
                                                   finding_ids=[finding_fingerprint(a)]))
    section_point = ReportPoint(text="C reports Z.", source_urls=[c.source_url],
                                statement=ReportStatement(statement_id="S002", text="C reports Z.",
                                                          finding_ids=[finding_fingerprint(c)]))
    composition = ReportComposition(
        question="q", session_id="s", findings=[a, b, c], summary=[bottom], table=table,
        sections=[ReportSection(title="More", points=[section_point])],
    )

    index = written_citations(composition)

    assert [citation.url for citation in index] == [a.source_url, b.source_url, c.source_url]


def _bare_finding(url: str, title: str, content: str) -> Finding:
    read = make_read(content, url=url, title=title)
    return make_finding(read, content, target_ids=[])


def _cited_composition(finding: Finding, **overrides: object) -> ReportComposition:
    fid = finding_fingerprint(finding)
    point = ReportPoint(text=finding.snippet, source_urls=[finding.source_url],
                        statement=ReportStatement(statement_id="S001", text=finding.snippet,
                                                  finding_ids=[fid]))
    payload: dict[str, object] = {"question": "q", "session_id": "s", "findings": [finding], "summary": [point]}
    payload.update(overrides)
    return ReportComposition(**payload)


def test_the_sources_line_strips_the_publisher_named_title_segment() -> None:
    finding = _bare_finding("https://cnet.com/best-headphones", "Best Headphones to Buy | CNET",
                            "CNET reports strong sound quality.")
    credit = PageCredit(publisher="CNET")
    composition = _cited_composition(finding, page_credits={normalize_source_url(finding.source_url): credit})

    report = render_written_report(composition)

    assert "1. CNET — [Best Headphones to Buy](https://cnet.com/best-headphones)" in report


def test_the_sources_line_shows_a_published_date() -> None:
    finding = _bare_finding("https://cnet.com/best-headphones", "Best Headphones to Buy | CNET",
                            "CNET reports strong sound quality.")
    credit = PageCredit(publisher="CNET", date="2026-09-17", date_kind="published")
    composition = _cited_composition(finding, page_credits={normalize_source_url(finding.source_url): credit})

    report = render_written_report(composition)

    assert "(2026-09-17)" in report
    assert "(updated 2026-09-17)" not in report


def test_the_sources_line_shows_an_updated_only_date() -> None:
    finding = _bare_finding("https://cnet.com/best-headphones", "Best Headphones to Buy | CNET",
                            "CNET reports strong sound quality.")
    credit = PageCredit(publisher="CNET", date="2026-09-17", date_kind="updated")
    composition = _cited_composition(finding, page_credits={normalize_source_url(finding.source_url): credit})

    report = render_written_report(composition)

    assert "(updated 2026-09-17)" in report


def test_the_sources_line_falls_back_to_the_host_publisher_without_a_credit() -> None:
    finding = _bare_finding("https://example-agency.test/report", "A report on capacity",
                            "Example Agency reports strong growth.")
    composition = _cited_composition(finding)

    report = render_written_report(composition)

    assert "1. example-agency.test — [A report on capacity](https://example-agency.test/report)" in report


def test_the_sources_line_escapes_a_title_that_could_hijack_the_link() -> None:
    """P2: an untrusted page title containing ``](url)`` must not let the
    printed Sources link point somewhere other than the cited page."""
    finding = _bare_finding("https://cnet.com/real-page",
                            "Guide](https://evil.example.test) to headphones",
                            "CNET reports on headphones.")
    composition = _cited_composition(finding)

    report = render_written_report(composition)

    assert "Guide\\](https://evil.example.test)" in report
    assert re.search(r"(?<!\\)\]\(https://evil\.example\.test\)", report) is None
    assert "](https://cnet.com/real-page)" in report


def test_the_sources_line_percent_encodes_unsafe_url_characters() -> None:
    """P2: a link destination with a raw space or an unbalanced paren must
    still be one non-whitespace Markdown token pointing at the cited page."""
    finding = _bare_finding("https://x.example.test/a b", "A report",
                            "X reports on something.")
    composition = _cited_composition(finding)

    report = render_written_report(composition)

    assert "https://x.example.test/a%20b" in report
    assert "https://x.example.test/a b" not in report


def test_the_quality_records_printed_title_matches_the_sources_line() -> None:
    finding = _bare_finding("https://cnet.com/real-page",
                            "Guide](https://evil.example.test) to headphones",
                            "CNET reports on headphones.")
    composition = _cited_composition(finding, page_credits={
        normalize_source_url(finding.source_url): PageCredit(publisher="CNET")
    })
    state = ResearchState(session_id=composition.session_id, original_question=composition.question)

    record = render_quality_record(state, composition, None)

    assert record["sources"][0]["title"] == "Guide\\](https://evil.example.test) to headphones"


def test_the_table_header_escapes_a_pipe_in_a_column_title() -> None:
    """P2: an options-table column title is the writer's section title
    (model text); an embedded pipe must not break the GFM table."""
    table = ReportTable(shape="options", columns=["Option", "Price | value", "Recommended by"],
                        rows=[[TableCell(text="A"), TableCell(), TableCell()]])
    composition = ReportComposition(question="q", session_id="s", table=table)

    report = render_written_report(composition)

    header = next(line for line in report.splitlines() if line.startswith("| Option"))
    assert header == "| Option | Price \\| value | Recommended by |"



def test_recommended_by_credits_a_relay_mark_not_the_relaying_page_alone() -> None:
    """R-1: when the picking mark's own entry text credits a relay (a page
    reporting another body's pick), Recommended-by must print that credit,
    never the relaying page's bare name as if it were the picker itself."""
    finding = _bare_finding("https://businessinsider.com/best-headphones",
                            "Best headphones", "Business Insider reports the pick.")
    table = ReportTable(
        shape="options", columns=["Option", "Recommended by"],
        rows=[[
            TableCell(text="Sony WH-1000XM6"),
            TableCell(entries=[TableEntry(
                text="Wirecutter, reported by Business Insider",
                source_url=finding.source_url,
            )]),
        ]],
    )
    composition = ReportComposition(
        question="q", session_id="s", findings=[finding], table=table,
        page_credits={normalize_source_url(finding.source_url): PageCredit(publisher="Business Insider")},
    )

    report = render_written_report(composition)

    assert "Wirecutter, reported by Business Insider [1]" in report
    assert "| Business Insider [1] |" not in report


def test_an_option_part_cell_also_credits_a_relay_mark() -> None:
    """The same check for a part cell (already correct at the time of R-1,
    kept as a render-level regression alongside the Recommended-by fix)."""
    finding = _bare_finding("https://businessinsider.com/best-headphones",
                            "Best headphones", "Business Insider reports the pick.")
    table = ReportTable(
        shape="options", columns=["Option", "Audio quality", "Recommended by"],
        rows=[[
            TableCell(text="Sony WH-1000XM6"),
            TableCell(entries=[TableEntry(
                text="the best overall pick, according to Wirecutter",
                source_url=finding.source_url,
            )]),
            TableCell(),
        ]],
    )
    composition = ReportComposition(
        question="q", session_id="s", findings=[finding], table=table,
        page_credits={normalize_source_url(finding.source_url): PageCredit(publisher="Business Insider")},
    )

    report = render_written_report(composition)

    assert "the best overall pick, according to Wirecutter — Business Insider [1]" in report



# --- §10, §3.1.6: what we couldn't confirm --------------------------------------


def test_what_we_couldnt_confirm_groups_searched_and_unsearched_targets() -> None:
    composition = ReportComposition(
        question="q", session_id="s",
        not_found=[
            NotFoundTarget(target_id="t1", question="Searched but unanswered?", searched=True),
            NotFoundTarget(target_id="t2", question="Never researched?", searched=False),
        ],
    )
    report = render_written_report(composition)

    assert "We found no source we could check that answers:" in report
    assert "- Searched but unanswered?" in report
    assert "This run did not research:" in report
    assert "- Never researched?" in report
    assert report.index("We found no source") < report.index("This run did not research")


def test_what_we_couldnt_confirm_lists_answered_but_unstated_targets() -> None:
    """The disclosure group that closes the loop the P1-3 quality fix opened:
    a required target a citable finding answers, but that no printed
    statement states, must reach the reader instead of vanishing silently."""
    target = make_target("topic-01-target-01", question="How much capacity was added?")
    topic = SubTopic(coverage_id="topic-01", title="Capacity", rationale="r",
                     search_queries=["q"], success_criteria=["c"], priority=1,
                     evidence_targets=[target])
    finding = _finding("https://agency.example.test/report",
                       "The agency reports 10 GW added in 2024.",
                       "10", "GW", organisation="Example Agency")
    composition = ReportComposition(
        question="q", session_id="s", sub_topics=[topic], findings=[finding],
    )

    report = render_written_report(composition)

    assert "We found sources on these but could not state a checked answer:" in report
    assert "- How much capacity was added?" in report


def test_what_we_couldnt_confirm_puts_unstated_targets_before_failed_parts() -> None:
    target = make_target("topic-01-target-01", question="How much capacity was added?")
    topic = SubTopic(coverage_id="topic-01", title="Capacity", rationale="r",
                     search_queries=["q"], success_criteria=["c"], priority=1,
                     evidence_targets=[target])
    finding = _finding("https://agency.example.test/report",
                       "The agency reports 10 GW added in 2024.",
                       "10", "GW", organisation="Example Agency")
    composition = ReportComposition(
        question="q", session_id="s", sub_topics=[topic], findings=[finding],
        parts=[ReportPart(coverage_id="topic-02", sub_topic_title="Mic", status="failed")],
    )

    report = render_written_report(composition)

    assert report.index("We found sources on these") < report.index("We could not write up Mic")



def test_what_we_couldnt_confirm_lists_failed_parts() -> None:
    composition = ReportComposition(
        question="q", session_id="s",
        parts=[ReportPart(coverage_id="topic-02", sub_topic_title="Microphone quality",
                          finding_ids=["F02"], status="failed")],
    )
    report = render_written_report(composition)

    assert ("We could not write up Microphone quality; its sources are listed in the "
           "evidence log.") in report


def test_what_we_couldnt_confirm_lists_up_to_five_unreachable_pages_with_plain_reasons() -> None:
    pages = [
        UnreachablePage(url=f"https://denied{n}.example.test/page", title=f"Denied {n}", reason="access_denied")
        for n in range(6)
    ]
    composition = ReportComposition(question="q", session_id="s", unreachable=pages)

    report = render_written_report(composition)

    assert "These pages could not be opened:" in report
    assert "so nothing from them is in this report" not in report
    assert "- Denied 0 (denied0.example.test) — access was denied" in report
    assert "- Denied 4 (denied4.example.test) — access was denied" in report
    assert "Denied 5" not in report
    assert "- and others, listed in the evidence log" in report
    assert "- and others, listed in the evidence log." not in report


def test_an_unreachable_pages_reason_reads_in_plain_words() -> None:
    reasons = {
        "access_denied": "access was denied",
        "not_found": "the page could not be found",
        "http_error": "the site returned an error",
        "transport_failure": "the page could not be reached",
        "unusable_content_shell": "the page's content could not be used",
    }
    for code, plain in reasons.items():
        page = UnreachablePage(url="https://example.test/page", title="A page", reason=code)
        report = render_written_report(ReportComposition(question="q", session_id="s", unreachable=[page]))
        assert plain in report



def test_the_unreachable_pages_header_never_overclaims_when_the_same_work_is_cited() -> None:
    """P3-3: a blocked landing page denied while its own PDF is read and
    cited is still one work reaching the report; the header must not claim
    "nothing from them is in this report" in a case it cannot rule out.
    Chosen fix: soften the header rather than try to detect "the same work"
    from the renderer alone (no reliable signal for that is carried on
    ``UnreachablePage`` or the citation index)."""
    finding = _finding("https://agency.example.test/report.pdf",
                       "The agency reports strong growth.", "1", "unit",
                       organisation="Example Agency")
    point = ReportPoint(text="The agency reports strong growth.",
                        source_urls=[finding.source_url],
                        statement=ReportStatement(statement_id="S001",
                                                  text="The agency reports strong growth.",
                                                  finding_ids=[finding_fingerprint(finding)]))
    page = UnreachablePage(url="https://agency.example.test/report", title="Annual report",
                          reason="access_denied")
    composition = ReportComposition(question="q", session_id="s", findings=[finding],
                                    summary=[point], unreachable=[page])

    report = render_written_report(composition)

    assert "These pages could not be opened:" in report
    assert "so nothing from them is in this report" not in report


# --- §3.1.8: the unchecked-sentence provenance exception ------------------------


def test_an_unchecked_sentence_with_a_fact_row_keeps_a_provenance_line() -> None:
    finding = _finding("https://agency.example.test/report",
                       "The agency reports 10 GW added in 2024.",
                       "10", "GW", organisation="Example Agency")
    fid = finding_fingerprint(finding)
    row = FactRow(row_id="K001", organisation="Example Agency", attribution="own",
                 measure="capacity added", period="2024", value="10 GW", kind="actual",
                 finding_id=fid, target_ids=["topic-01-target-01"])
    point = ReportPoint(text="The agency reports 10 GW added in 2024.",
                        source_urls=[finding.source_url],
                        statement=ReportStatement(statement_id="S001",
                                                  text="The agency reports 10 GW added in 2024.",
                                                  finding_ids=[fid]))
    composition = ReportComposition(
        question="q", session_id="s", findings=[finding], fact_rows=[row], summary=[point],
        statement_verdicts={"S001": "unchecked"},
        page_credits={normalize_source_url(finding.source_url): PageCredit(publisher="Example Agency")},
    )

    report = render_written_report(composition)
    body = _section_body(report, "## Bottom line")

    assert "(figure: Example Agency)" in body


def test_a_checked_sentence_with_a_fact_row_keeps_no_provenance_line() -> None:
    """The exception is for ``unchecked`` sentences only (§3.1 rule 8)."""
    finding = _finding("https://agency.example.test/report",
                       "The agency reports 10 GW added in 2024.",
                       "10", "GW", organisation="Example Agency")
    fid = finding_fingerprint(finding)
    row = FactRow(row_id="K001", organisation="Example Agency", attribution="own",
                 measure="capacity added", period="2024", value="10 GW", kind="actual",
                 finding_id=fid, target_ids=["topic-01-target-01"])
    point = ReportPoint(text="The agency reports 10 GW added in 2024.",
                        source_urls=[finding.source_url],
                        statement=ReportStatement(statement_id="S001",
                                                  text="The agency reports 10 GW added in 2024.",
                                                  finding_ids=[fid]))
    composition = ReportComposition(
        question="q", session_id="s", findings=[finding], fact_rows=[row], summary=[point],
        statement_verdicts={"S001": "consistent"},
    )

    report = render_written_report(composition)

    assert "(figure:" not in report



def test_an_unchecked_sentence_citing_a_relay_copy_gets_the_relays_provenance() -> None:
    """P1/D13: an unchecked sentence that cites only the relay duplicate of a
    fact row must not credit the row's primary page's issuer and release --
    a page the report never even cites."""
    own_page = _finding("https://agency.example.test/own-release",
                        "The agency expects 14 GW of additions in 2025.",
                        "14", "GW", organisation="Example Agency", kind="forecast", period="2025")
    own_page = own_page.model_copy(update={"release_date": "2025-01-14"})
    relay_page = _finding("https://gazette.example.test/story",
                          "According to the Example Agency, as reported by the Example Gazette, "
                          "14 GW of additions are expected in 2025.",
                          "14", "GW", organisation="Example Agency", attribution="relayed",
                          kind="forecast", period="2025")
    primary = finding_fingerprint(own_page)
    row = FactRow(row_id="K001", organisation="Example Agency", attribution="own", measure="additions",
                  period="2025", value="14 GW", kind="forecast", finding_id=primary,
                  duplicate_finding_ids=[finding_fingerprint(relay_page)],
                  target_ids=["topic-01-target-01"])
    text = ("According to the Example Agency, as reported by the Example Gazette, "
           "14 GW of additions are expected in 2025.")
    point = ReportPoint(text=text, source_urls=[relay_page.source_url],
                        statement=ReportStatement(statement_id="S001", text=text,
                                                  finding_ids=[finding_fingerprint(relay_page)]))
    composition = ReportComposition(
        question="What is the outlook for additions?", session_id="s",
        findings=[own_page, relay_page], fact_rows=[row], summary=[point],
        statement_verdicts={"S001": "unchecked"},
    )

    report = render_written_report(composition)
    body = _section_body(report, "## Bottom line")

    assert "2025-01-14" not in body
    assert "reported by gazette.example.test" in body



def test_an_unchecked_sentence_citing_the_own_duplicate_of_a_relay_row_gets_its_own_provenance() -> None:
    """P1/D13, the reverse direction: when the row's own primary is the relay
    page but the sentence cites only the original issuer's duplicate page,
    the suffix must credit that page, never a relay the report never cites."""
    own_page = _finding("https://agency.example.test/own-release",
                        "The agency expects 14 GW of additions in 2025.",
                        "14", "GW", organisation="Example Agency", kind="forecast", period="2025")
    own_page = own_page.model_copy(update={"release_date": "2025-01-14"})
    relay_page = _finding("https://gazette.example.test/story",
                          "According to the Example Agency, as reported by the Example Gazette, "
                          "14 GW of additions are expected in 2025.",
                          "14", "GW", organisation="Example Agency", attribution="relayed",
                          kind="forecast", period="2025")
    primary = finding_fingerprint(relay_page)
    row = FactRow(row_id="K001", organisation="Example Agency", attribution="relayed",
                 relay_host="gazette.example.test", measure="additions",
                 period="2025", value="14 GW", kind="forecast", finding_id=primary,
                 duplicate_finding_ids=[finding_fingerprint(own_page)],
                 target_ids=["topic-01-target-01"])
    text = "The agency expects 14 GW of additions in 2025."
    point = ReportPoint(text=text, source_urls=[own_page.source_url],
                        statement=ReportStatement(statement_id="S001", text=text,
                                                  finding_ids=[finding_fingerprint(own_page)]))
    composition = ReportComposition(
        question="What is the outlook for additions?", session_id="s",
        findings=[own_page, relay_page], fact_rows=[row], summary=[point],
        statement_verdicts={"S001": "unchecked"},
    )

    report = render_written_report(composition)
    body = _section_body(report, "## Bottom line")

    assert "gazette.example.test" not in body
    assert "(figure: Example Agency (released 2025-01-14))" in body


# --- §9: the evidence log ------------------------------------------------------


def test_the_evidence_log_keeps_the_search_trail_the_reader_no_longer_carries() -> None:
    log = render_finding_log(_composition())

    assert "BloombergNEF 2025 US storage forecast" in log
    assert "https://about.bnef.com/x" in log
    assert "What does BloombergNEF project for 2025?" in log


def test_the_evidence_log_shows_every_verified_figure_even_one_that_answers_no_target() -> None:
    """Decision #4: the full fact-row table moves to the evidence log, unfiltered --
    an auditor sees every verified figure, not only the ones a plan target names."""
    base = _composition()
    unplanned = base.fact_rows[1].model_copy(update={"target_ids": []})
    composition = base.model_copy(update={"fact_rows": [base.fact_rows[0], unplanned]})

    log = render_finding_log(composition)

    assert "## Verified figures" in log
    assert f"| {unplanned.value} |" in log


def test_the_evidence_log_says_so_when_no_figure_passed() -> None:
    log = render_finding_log(_plain_composition(fact_rows=[]))
    assert "No figure passed the Evidence Verifier." in log


def test_the_evidence_log_shows_the_subject_column_only_when_a_row_has_a_subject() -> None:
    base = _composition()
    assert _FACTS_HEADER in render_finding_log(base)
    named = base.fact_rows[0].model_copy(update={"subject": "Model A"})
    log = render_finding_log(base.model_copy(update={"fact_rows": [named, *base.fact_rows[1:]]}))
    assert "| Organisation | Subject | Measure |" in log and "| Model A |" in log


def test_the_evidence_log_shows_unchecked_context_on_a_fact_row() -> None:
    base = _composition()
    unchecked_row = base.fact_rows[0].model_copy(update={"context_unchecked": True})
    composition = base.model_copy(update={"fact_rows": [unchecked_row, base.fact_rows[1]]})
    log = render_finding_log(composition)
    assert "actual (unchecked context)" in log


def test_the_evidence_log_shows_an_unattributed_row_reading() -> None:
    base = _composition()
    unattributed_row = base.fact_rows[1].model_copy(update={"attribution": "unattributed", "relay_host": None})
    composition = base.model_copy(update={"fact_rows": [base.fact_rows[0], unattributed_row]})
    log = render_finding_log(composition)
    assert "U.S. Energy Information Administration (source does not attribute it)" in log


def test_the_evidence_log_shows_source_labels_not_citation_markers() -> None:
    log = render_finding_log(_composition())
    row = next(line for line in log.splitlines() if line.startswith("| U.S. Energy Information Administration | battery storage power capacity added | 2024 |"))
    assert row.rstrip().endswith("| F01 |")


def test_an_earlier_editions_value_traces_to_both_pages_in_the_evidence_log() -> None:
    latest = _finding("https://agency.example.test/outlook", "19.6 GW is expected in 2026.",
                      "19.6", "GW", organisation="Example Agency", kind="forecast", period="2026")
    earlier = _finding("https://agency.example.test/outlook-jan", "18.2 GW was expected.",
                       "18.2", "GW", organisation="Example Agency", kind="forecast", period="2025")
    composition = ReportComposition(
        question="What capacity is expected?", session_id="s", as_of="2026-09-24T00:00:00+00:00",
        findings=[latest, earlier],
        fact_rows=[FactRow(row_id="K001", organisation="Example Agency", attribution="own",
                           measure="capacity", period="2026", value="19.6 GW", kind="forecast",
                           release="released 2025-06-14", finding_id=finding_fingerprint(latest),
                           target_ids=["topic-01-target-01"],
                           earlier=[EarlierEdition(value="18.2 GW", release="released 2025-01-14",
                                                   finding_id=finding_fingerprint(earlier))])],
        finding_labels={"F01": finding_fingerprint(latest), "F02": finding_fingerprint(earlier)},
    )

    log = render_finding_log(composition)

    row = next(line for line in log.splitlines() if line.startswith("| Example Agency"))
    assert row.rstrip().endswith("| F01, F02 |")
    assert earlier.source_url in log


def test_the_evidence_log_keeps_snippets_drop_reasons_and_refusals() -> None:
    composition = _composition().model_copy(update={
        "rejected_points": [RejectedDraftPoint(where="summary[2]",
                                               text="Wood Mackenzie reports 18.9 GW of grid-scale storage.",
                                               finding_labels=["F03"],
                                               reason="scope not carried by the cited figures: grid-scale")],
    })
    log = render_finding_log(composition)
    assert "### F01" in log and "Generators added 10.4 gigawatts" in log
    assert "context_rejected" in log and "A growth rate, not a capacity." in log
    assert "Wood Mackenzie reports 18.9 GW of grid-scale storage." in log
    assert "F03" in log and "scope not carried" in log
    assert "forecast (January 2025 STEO)" in log


def test_the_evidence_log_shows_a_dropped_finding_with_its_reason() -> None:
    read = make_read("BloombergNEF projects 20 GW of storage by 2026.",
                     url="https://about.bnef.com/x", title="BNEF forecast")
    dropped = make_finding(read, "BloombergNEF projects 20 GW of storage by 2026.",
                           target_ids=["topic-03-target-01"])
    dropped = dropped.model_copy(update={
        "verification": FindingVerification(status="dropped", dropped_reason="snippet_not_on_page"),
    })
    base = _composition()
    composition = base.model_copy(update={"findings": [*base.findings, dropped]})
    log = render_finding_log(composition)
    assert "dropped (snippet_not_on_page)" in log


def test_a_kept_forecast_finding_with_no_fact_row_falls_back_to_its_own_release() -> None:
    finding = _finding("https://ent.news/2025/1/941.pdf",
                       "battery storage capacity growing by 40% (13 GW) in 2025",
                       "13", "GW", organisation="U.S. Energy Information Administration",
                       attribution="relayed", kind="forecast", period="2025")
    finding = finding.model_copy(update={"release_date": "2025-01-14"})
    base = _composition()
    composition = base.model_copy(update={"findings": [*base.findings, finding]})
    log = render_finding_log(composition)
    assert "forecast (released 2025-01-14)" in log
    assert "release not stated on the page" not in log


def test_two_revision_editions_show_their_own_release_and_label() -> None:
    same_text = "battery storage capacity growing by 40% in 2025"
    edition_a = _finding("https://ent.news/2025/1/942.pdf", same_text, "13", "GW",
                         organisation="U.S. Energy Information Administration",
                         attribution="relayed", kind="forecast", period="2025")
    edition_a = edition_a.model_copy(update={"release_date": "2025-01-10"})
    edition_b = _finding("https://ent.news/2025/1/942.pdf", same_text, "14", "GW",
                         organisation="U.S. Energy Information Administration",
                         attribution="relayed", kind="forecast", period="2025")
    edition_b = edition_b.model_copy(update={"release_date": "2025-02-14"})
    fp = finding_fingerprint(edition_a)
    assert fp == finding_fingerprint(edition_b)
    folded_row = FactRow(row_id="K001", organisation="U.S. Energy Information Administration",
                         attribution="relayed", relay_host="ent.news",
                         measure="battery storage power capacity added", period="2025",
                         value="14 GW", kind="forecast", release="released 2025-02-14", finding_id=fp)
    composition = ReportComposition(
        question="q", session_id="s", findings=[edition_a, edition_b], fact_rows=[folded_row],
        finding_labels={"F01": fp, "F02": fp},
    )
    log = render_finding_log(composition)
    assert log.count("### F01") == 1 and log.count("### F02") == 1
    assert "13 GW: kept" in log and "14 GW: kept" in log
    assert "forecast (released 2025-01-10)" in log
    assert "forecast (released 2025-02-14)" in log



def test_a_fact_row_value_that_spells_its_own_unit_is_not_doubled() -> None:
    """D15: a spelled duration value ('a century') already carries its own
    unit; the fact-row table must not repeat it ('a century years')."""
    base = _composition()
    doubled_row = base.fact_rows[0].model_copy(update={"value": "a century years"})
    composition = base.model_copy(update={"fact_rows": [doubled_row, base.fact_rows[1]]})

    log = render_finding_log(composition)

    assert "| a century |" in log
    assert "a century years" not in log


def test_a_kept_figures_spelled_value_is_not_doubled_with_its_unit() -> None:
    """D15: the same rule applies to a kept figure's own line under the
    evidence log's Findings section, where value and unit are separate
    fields on the figure rather than one already-combined string."""
    finding = _finding(
        "https://example-register.test/notes",
        "The review found effects lasting about a century.",
        "a century", "years", organisation="Example Institute",
    )
    composition = ReportComposition(
        question="q", session_id="s", findings=[finding], fact_rows=[],
        finding_labels={"F01": finding_fingerprint(finding)},
    )

    log = render_finding_log(composition)

    assert "a century: kept" in log
    assert "a century years: kept" not in log


def test_the_evidence_log_about_block_carries_scope_counts_and_parts() -> None:
    composition = _composition().model_copy(update={
        "generated_on": "2026-09-25",
        "answer_kind": "factual",
        "parts": [ReportPart(coverage_id="topic-01", sub_topic_title="2024 capacity",
                             finding_ids=["F01"], status="written")],
    })
    log = render_finding_log(composition)
    section = _section_body(log, "## About this report")

    assert "Scope: United States" in section
    assert "Printed on: 2026-09-25" in section
    assert "factual" in section
    assert "topic-01" in section and "2024 capacity" in section and "written" in section


def test_the_evidence_log_findings_counts_match_the_spec_wording() -> None:
    quoted = make_finding(make_read(), "Something else was found.", target_ids=[]).model_copy(
        update={"verification": FindingVerification(status="quoted")})
    composition = _plain_composition(findings=[*_composition().findings, quoted])
    log = render_finding_log(composition)
    section = _section_body(log, "## About this report")

    assert ("Findings: 1 verified, 1 verified with corrections, "
           "1 quoted (snippet found on the page; not checked for context), "
           "0 dropped; 0 with unchecked context; 1 required questions unanswered") in section


def test_the_evidence_log_lists_dropped_marks_and_unplaced_findings() -> None:
    unplaced = make_finding(make_read(), "An unplaced finding nobody's part claimed.", target_ids=[])
    composition = _composition().model_copy(update={
        "findings": [*_composition().findings, unplaced],
        "dropped_marks": ["S004: 'Model A' is not in the sentence"],
    })
    log = render_finding_log(composition)

    assert "## Dropped option marks" in log
    assert "S004: 'Model A' is not in the sentence" in log
    assert "## Unplaced findings" in log
    assert unplaced.source_url in _section_body(log, "## Unplaced findings")
    assert "spec §6.1" not in log


def test_the_evidence_log_lists_every_unreachable_page_uncapped() -> None:
    pages = [
        UnreachablePage(url=f"https://denied{n}.example.test/page", title=f"Denied {n}", reason="access_denied")
        for n in range(6)
    ]
    composition = _composition().model_copy(update={"unreachable": pages})
    log = render_finding_log(composition)

    assert "## Pages that could not be opened" in log
    assert "Denied 5" in log  # the reader report caps at 5; the ledger does not


# --- §13: end-to-end goldens (T3 acceptance) -----------------------------------
#
# Each composition is built the way the named run's own quality JSON would
# carry it -- findings, a hand-set table, page credits, not-found targets --
# so the assertion exercises the renderer's own assembly, numbering and
# Sources logic against the spec's literal text, not the writer's sentence
# generation or the table builder's own algorithm (both covered by their own
# tests, T4 and T2).


def _pre_flight_run_composition() -> ReportComposition:
    """§13.2 -- pre-flight run 4 (session ``9390e6e1...``, pass 0)."""
    house = _bare_finding(
        "https://docs.house.gov/meetings/II/II00/20260513/119199/HHRG-119-II00-20260513-SD003.pdf",
        "U.S. battery capacity increased 66% in 2024",
        "Generators added 10.4 GW of new battery storage capacity in 2024.",
    )
    utility_dive = _bare_finding(
        "https://utilitydive.com/news/us-utility-scale-energy-storage-to-double-reach-65-gw-by-2027-eia/750338",
        "US utility-scale energy storage to double, reach 65 GW by 2027: EIA",
        "Domestic storage capacity will rise to 64.9 GW.",
    )
    eia = _bare_finding(
        "https://eia.gov/todayinenergy/detail.php?id=67925",
        "Battery storage capacity averaged 70% growth over the last three years",
        "By the end of 2025 the U.S. power system had 43.6 GW.",
    )
    energi = _bare_finding(
        "https://energi.media/news/u-s-electricity-generation-set-to-rise-as-solar-and-battery-capacity-expand-eia-forecasts",
        "U.S. Electricity Generation Set to Rise as Solar and Battery Capacity Expand: EIA Forecasts - Thoughtful Journalism",
        "ERCOT battery capacity will rise to 37 GW by 2027.",
    )
    house_id = finding_fingerprint(house)
    utility_dive_id = finding_fingerprint(utility_dive)
    eia_id = finding_fingerprint(eia)
    energi_id = finding_fingerprint(energi)

    def point(n: int, text: str, url: str) -> ReportPoint:
        return ReportPoint(text=text, source_urls=[url],
                           statement=ReportStatement(statement_id=f"S{n:03d}", text=text))

    summary = [
        point(1, "For 2024, house.gov reports that generators added 10.4 GW of new "
                "battery storage capacity, the second-largest generating capacity "
                "addition after solar.", house.source_url),
        point(2, "The Energy Information Administration's forecast, released "
                "2025-06-10 and reported by Utility Dive, projects domestic storage "
                "capacity rising from about 28 GW at the end of Q1 2025 to 64.9 GW "
                "at the end of 2026.", utility_dive.source_url),
        point(3, "The U.S. Energy Information Administration reports that by the end "
                "of 2025 the U.S. power system had operational battery storage "
                "capacity of 43.6 GW.", eia.source_url),
    ]

    table_rows = [
        ('"Generators added 10.4 GW of new battery storage capacity in 2024, the '
         'second-largest generating capacity addition after solar."',
         "10.4 GW, actual", "house.gov (stated 2025-03-12)", house_id),
        ('"cumulative utility-scale battery storage capacity exceeded 26 gigawatts '
         '(GW) in 2024, according to our January 2025 Preliminary Monthly…"',
         "26 gigawatts (GW), actual", "house.gov (stated 2025-03-12)", house_id),
        ("Battery storage capacity, 2025", "43.6 gigawatts (GW), actual",
         "U.S. Energy Information Administration (stated 2026-08-07)", eia_id),
        ("Battery storage (first six months of 2026)", "8.3 GW, actual",
         "U.S. Energy Information Administration (stated 2026-08-07)", eia_id),
        ('"Utility-scale battery storage in the United States is poised to more '
         'than double over the next two years and will close out 2026 at nearly '
         '65 GW…"', "65 GW, forecast",
         "Energy Information Administration, reported by Utility Dive (released 2025-06-10)",
         utility_dive_id),
        ("Battery storage (utility-scale), Q1 2024", "17 GW, actual",
         "Energy Information Administration, reported by Utility Dive (released 2025-06-10)",
         utility_dive_id),
        ('"Counting projects larger than 1 MW in the electric power sector, EIA '
         "said domestic storage capacity will rise from about 28 GW at the end of "
         "Q1'25…\"", "28 GW, actual",
         "Energy Information Administration, reported by Utility Dive (released 2025-06-10)",
         utility_dive_id),
        ('"…EIA said domestic storage capacity will rise from about 28 GW at the '
         "end of Q1'25 to 64.9 GW at the end of 2026.\"", "64.9 GW, forecast",
         "Energy Information Administration, reported by Utility Dive (released 2025-06-10)",
         utility_dive_id),
        ("ERCOT, 2025", "15 GW, actual", "EIA, reported by energi.media (stated 2026-01-21)", energi_id),
        ("ERCOT, 2027", "37 GW, forecast", "EIA, reported by energi.media (stated 2026-01-21)", energi_id),
    ]
    table = ReportTable(
        shape="findings",
        columns=["What was measured", "Result", "Who reported it (and when)", "Source"],
        rows=[[TableCell(text=what), TableCell(text=result), TableCell(text=who),
              TableCell(finding_ids=[fid])]
             for what, result, who, fid in table_rows],
    )

    sections = [
        ReportSection(title="Capacity added in 2024", points=[
            point(4, "For 2024, house.gov reports that generators added 10.4 GW of "
                    "new battery storage capacity, the second-largest generating "
                    "capacity addition after solar, from the January 2025 "
                    "Preliminary Monthly Electric Generator Inventory.", house.source_url),
            point(5, "house.gov also reports that cumulative utility-scale battery "
                    "storage capacity exceeded 26 GW in 2024, from the same January "
                    "2025 Preliminary Monthly Electric Generator Inventory.", house.source_url),
            point(6, "The U.S. Energy Information Administration reports that by "
                    "the end of 2025 the U.S. power system had operational battery "
                    "storage capacity of 43.6 GW.", eia.source_url),
            point(7, "The U.S. Energy Information Administration reports that "
                    "operators added another 8.3 GW of battery storage during the "
                    "first six months of 2026.", eia.source_url),
        ]),
        ReportSection(title="The EIA's forecasts", points=[
            point(8, "According to the Energy Information Administration, as "
                    "reported by Utility Dive and released 2025-06-10, domestic "
                    "storage capacity will rise from about 28 GW at the end of Q1 "
                    "2025 to 64.9 GW at the end of 2026, counting projects larger "
                    "than 1 MW in the electric power sector.", utility_dive.source_url),
            point(9, "According to the Energy Information Administration, as "
                    "reported by Utility Dive, utility-scale battery storage in the "
                    "United States is forecast to more than double over the next "
                    "two years and to close out 2026 at nearly 65 GW, a rise from "
                    "17 GW in the first quarter of 2024 (forecast released "
                    "2025-06-10).", utility_dive.source_url),
        ]),
        ReportSection(title="Other forecasts", points=[
            point(10, "The EIA expects battery capacity in ERCOT to rise from "
                     "about 15 GW in 2025 to 37 GW by the end of 2027, according "
                     "to EIA, as reported by energi.media.", energi.source_url),
        ]),
    ]

    return ReportComposition(
        question=("How much grid-scale battery storage capacity was added in the "
                  "United States in 2024, and what do the latest forecasts project "
                  "for 2025?"),
        session_id="9390e6e10b324fb6a6dc17477738a531", iteration=0,
        as_of="2026-09-25T00:00:00+00:00",
        findings=[house, utility_dive, eia, energi],
        summary=summary, sections=sections, table=table,
        page_credits={
            normalize_source_url(house.source_url): PageCredit(publisher="house.gov"),
            normalize_source_url(utility_dive.source_url): PageCredit(publisher="Utility Dive"),
            normalize_source_url(eia.source_url): PageCredit(publisher="U.S. Energy Information Administration"),
            normalize_source_url(energi.source_url): PageCredit(publisher="energi.media"),
        },
        not_found=[
            NotFoundTarget(
                target_id="topic-01-target-01",
                question=("How much grid-scale battery storage capacity did the "
                          "United States add in 2024, according to the U.S. Energy "
                          "Information Administration?"),
                searched=True,
            ),
            NotFoundTarget(
                target_id="topic-02-target-01",
                question=("What 2025 grid-scale battery storage capacity additions "
                          "did the U.S. Energy Information Administration's latest "
                          "forecast covering 2025 project?"),
                searched=True,
            ),
        ],
    )


_PRE_FLIGHT_RUN_GOLDEN = """\
# How much grid-scale battery storage capacity was added in the United States in 2024, and what do the latest forecasts project for 2025?

Evidence as of 2026-09-25 · 4 sources

## Bottom line

For 2024, house.gov reports that generators added 10.4 GW of new battery storage capacity, the second-largest generating capacity addition after solar [1]. The Energy Information Administration's forecast, released 2025-06-10 and reported by Utility Dive, projects domestic storage capacity rising from about 28 GW at the end of Q1 2025 to 64.9 GW at the end of 2026 [2]. The U.S. Energy Information Administration reports that by the end of 2025 the U.S. power system had operational battery storage capacity of 43.6 GW [3].

| What was measured | Result | Who reported it (and when) | Source |
|---|---|---|---|
| "Generators added 10.4 GW of new battery storage capacity in 2024, the second-largest generating capacity addition after solar." | 10.4 GW, actual | house.gov (stated 2025-03-12) | [1] |
| "cumulative utility-scale battery storage capacity exceeded 26 gigawatts (GW) in 2024, according to our January 2025 Preliminary Monthly…" | 26 gigawatts (GW), actual | house.gov (stated 2025-03-12) | [1] |
| Battery storage capacity, 2025 | 43.6 gigawatts (GW), actual | U.S. Energy Information Administration (stated 2026-08-07) | [3] |
| Battery storage (first six months of 2026) | 8.3 GW, actual | U.S. Energy Information Administration (stated 2026-08-07) | [3] |
| "Utility-scale battery storage in the United States is poised to more than double over the next two years and will close out 2026 at nearly 65 GW…" | 65 GW, forecast | Energy Information Administration, reported by Utility Dive (released 2025-06-10) | [2] |
| Battery storage (utility-scale), Q1 2024 | 17 GW, actual | Energy Information Administration, reported by Utility Dive (released 2025-06-10) | [2] |
| "Counting projects larger than 1 MW in the electric power sector, EIA said domestic storage capacity will rise from about 28 GW at the end of Q1'25…" | 28 GW, actual | Energy Information Administration, reported by Utility Dive (released 2025-06-10) | [2] |
| "…EIA said domestic storage capacity will rise from about 28 GW at the end of Q1'25 to 64.9 GW at the end of 2026." | 64.9 GW, forecast | Energy Information Administration, reported by Utility Dive (released 2025-06-10) | [2] |
| ERCOT, 2025 | 15 GW, actual | EIA, reported by energi.media (stated 2026-01-21) | [4] |
| ERCOT, 2027 | 37 GW, forecast | EIA, reported by energi.media (stated 2026-01-21) | [4] |

## Capacity added in 2024

- For 2024, house.gov reports that generators added 10.4 GW of new battery storage capacity, the second-largest generating capacity addition after solar, from the January 2025 Preliminary Monthly Electric Generator Inventory [1].
- house.gov also reports that cumulative utility-scale battery storage capacity exceeded 26 GW in 2024, from the same January 2025 Preliminary Monthly Electric Generator Inventory [1].
- The U.S. Energy Information Administration reports that by the end of 2025 the U.S. power system had operational battery storage capacity of 43.6 GW [3].
- The U.S. Energy Information Administration reports that operators added another 8.3 GW of battery storage during the first six months of 2026 [3].

## The EIA's forecasts

- According to the Energy Information Administration, as reported by Utility Dive and released 2025-06-10, domestic storage capacity will rise from about 28 GW at the end of Q1 2025 to 64.9 GW at the end of 2026, counting projects larger than 1 MW in the electric power sector [2].
- According to the Energy Information Administration, as reported by Utility Dive, utility-scale battery storage in the United States is forecast to more than double over the next two years and to close out 2026 at nearly 65 GW, a rise from 17 GW in the first quarter of 2024 (forecast released 2025-06-10) [2].

## Other forecasts

- The EIA expects battery capacity in ERCOT to rise from about 15 GW in 2025 to 37 GW by the end of 2027, according to EIA, as reported by energi.media [4].

## What we couldn't confirm

We found no source we could check that answers:
- How much grid-scale battery storage capacity did the United States add in 2024, according to the U.S. Energy Information Administration?
- What 2025 grid-scale battery storage capacity additions did the U.S. Energy Information Administration's latest forecast covering 2025 project?

## Sources

1. house.gov — [U.S. battery capacity increased 66% in 2024](https://docs.house.gov/meetings/II/II00/20260513/119199/HHRG-119-II00-20260513-SD003.pdf)
2. Utility Dive — [US utility-scale energy storage to double, reach 65 GW by 2027: EIA](https://utilitydive.com/news/us-utility-scale-energy-storage-to-double-reach-65-gw-by-2027-eia/750338)
3. U.S. Energy Information Administration — [Battery storage capacity averaged 70% growth over the last three years](https://eia.gov/todayinenergy/detail.php?id=67925)
4. energi.media — [U.S. Electricity Generation Set to Rise as Solar and Battery Capacity Expand: EIA Forecasts - Thoughtful Journalism](https://energi.media/news/u-s-electricity-generation-set-to-rise-as-solar-and-battery-capacity-expand-eia-forecasts)

How this was researched: [evidence log](report-9390e6e10b324fb6a6dc17477738a531-0-evidence.md)
"""


def test_the_1322_pre_flight_run_renders_the_spec_golden() -> None:
    """§14 T3 acceptance: §13.2's exact rendering from a hand-built
    composition shaped like the run's own quality JSON."""
    assert render_written_report(_pre_flight_run_composition()) == _PRE_FLIGHT_RUN_GOLDEN


def _electoral_college_composition() -> ReportComposition:
    """§13.3 -- the generality smoke run (session ``e5d6cc40...``, pass 0; a
    capped run, 12 required targets left unsearched)."""
    cornell_article_ii = _bare_finding(
        "https://law.cornell.edu/constitution/articleii",
        "Article II | U.S. Constitution | US Law | LII / Legal Information Institute",
        "Each State appoints electors as its Legislature directs.",
    )
    justia = _bare_finding(
        "https://law.justia.com/constitution/us/article-2/03-electoral-college.html",
        "Electoral College :: Article II. Executive Department :: U.S. Constitution Annotated :: Justia",
        "No Senator or Representative shall be appointed an elector.",
    )
    archives_allocation = _bare_finding(
        "https://archives.gov/electoral-college/allocation",
        "Distribution of Electoral Votes",
        "538 electoral votes in all, 270 needed to elect.",
    )
    archives_about = _bare_finding(
        "https://archives.gov/electoral-college/about",
        "What is the Electoral College?",
        "Every State receives a number of votes equal to its delegation.",
    )
    cornell_chiafalo = _bare_finding(
        "https://law.cornell.edu/supremecourt/text/19-465",
        "CHIAFALO v. WASHINGTON | Supreme Court | US Law | LII / Legal Information Institute",
        "A State may enforce an elector's pledge.",
    )
    archives_allocation_id = finding_fingerprint(archives_allocation)

    def point(n: int, text: str, *urls: str) -> ReportPoint:
        return ReportPoint(text=text, source_urls=list(urls),
                           statement=ReportStatement(statement_id=f"S{n:03d}", text=text))

    summary = [
        point(1, "Under the Constitution, each State appoints, in the manner its "
                "Legislature directs, a number of electors equal to its Senators "
                "and Representatives in Congress;",
             cornell_article_ii.source_url, justia.source_url),
        point(2, "archives.gov reports 538 electoral votes in all, with 270 needed "
                "to elect, for the 2024 and 2028 presidential elections.",
             archives_allocation.source_url),
        point(3, "The electors meet in their respective states and vote by ballot "
                "for two persons,", cornell_article_ii.source_url),
        point(4, "and if two or more candidates remain with equal votes, the "
                "Senate chooses the Vice President from them by ballot.", justia.source_url),
    ]

    table_rows = [
        ("The District of Columbia", "three electors", "National Archives"),
        ('"…Senators and Representatives in its U.S. Congressional delegation—two '
         'votes for its Senators in the U.S. Senate…"', "two votes", "National Archives"),
        ("Total Electoral Votes, 2024 and 2028 presidential elections",
         "538 electoral votes", "National Archives"),
        ("Majority Needed to Elect, 2024 and 2028 presidential elections",
         "270 votes", "National Archives"),
    ]
    table = ReportTable(
        shape="findings",
        columns=["What was measured", "Result", "Who reported it (and when)", "Source"],
        rows=[[TableCell(text=what), TableCell(text=result), TableCell(text=who),
              TableCell(finding_ids=[archives_allocation_id])]
             for what, result, who in table_rows],
        caption="No figure in this table is a forecast.",
    )

    sections = [
        ReportSection(title="How many electors there are", points=[
            point(5, "archives.gov reports 538 total electoral votes and 270 votes "
                    "as the majority needed to elect, for the 2024 and 2028 "
                    "presidential elections, on allocations based on the 2020 "
                    "Census.", archives_allocation.source_url),
            point(6, "archives.gov states that under the 23rd Amendment of the "
                    "Constitution the District of Columbia is allocated three "
                    "electors and treated like a State for purposes of the "
                    "Electoral College.", archives_allocation.source_url),
            point(7, "archives.gov states that electoral votes are allocated among "
                    "the States based on the Census, every State receiving a "
                    "number of votes equal to the number of Senators and "
                    "Representatives in its U.S. Congressional delegation — two "
                    "votes for its Senators in the U.S. Senate plus a number of "
                    "votes equal to the number of its Congressional districts, one "
                    "for each Member in the House of Representatives.",
                 archives_allocation.source_url, archives_about.source_url),
        ]),
        ReportSection(title="How electors are appointed and who may serve", points=[
            point(8, "According to cornell.edu and justia.com, the Constitution "
                    "provides that each State shall appoint, in such manner as the "
                    "Legislature thereof may direct, a number of electors equal to "
                    "the whole number of Senators and Representatives to which the "
                    "State may be entitled in the Congress.",
                 cornell_article_ii.source_url, justia.source_url),
            point(9, "According to justia.com and cornell.edu, no Senator or "
                    "Representative, or person holding an office of trust or "
                    "profit under the United States, shall be appointed an "
                    "elector.", cornell_article_ii.source_url, justia.source_url),
            point(10, "cornell.edu states that the Constitution's text and the "
                     "Nation's history both support allowing a State to enforce an "
                     "elector's pledge to support his party's nominee — and the "
                     "state voters' choice — for President.", cornell_chiafalo.source_url),
        ]),
        ReportSection(title="How the electors vote", points=[
            point(11, "According to cornell.edu, the electors shall meet in their "
                     "respective states and vote by ballot for two persons, of "
                     "whom one at least shall not be an inhabitant of the same "
                     "state with themselves.", cornell_article_ii.source_url),
        ]),
        ReportSection(title="When no candidate has a majority", points=[
            point(12, "According to justia.com, if two or more candidates should "
                     "remain with equal votes, the Senate shall choose from them "
                     "by ballot the Vice President.", justia.source_url),
        ]),
    ]

    unresearched_questions = [
        "What date does federal law set for the appointment of presidential electors?",
        "Which states award their electoral votes by congressional district rather than statewide?",
        "On what day must the presidential electors meet to cast their votes?",
        "In what place must the electors meet to cast their votes?",
        "What instrument must a state's executive issue to certify which electors were appointed?",
        "To which officials must the certificate identifying a state's appointed electors be transmitted?",
        "What legal effect does a state's determination of its electors have if it is made under the federal safe-harbor provision?",
        "On what date must Congress count the electoral votes?",
        "Who presides over the joint session at which Congress counts the electoral votes?",
        "Which body elects the President if no candidate receives a majority of the electoral votes?",
        "What voting arrangement governs the House of Representatives when it elects the President?",
        "What voting arrangement governs the Senate when it elects the Vice President?",
    ]

    return ReportComposition(
        question="How does the U.S. Electoral College work?",
        session_id="e5d6cc40fa74403ab7af075a9153c305", iteration=0,
        as_of="2026-09-25T00:00:00+00:00",
        findings=[cornell_article_ii, justia, archives_allocation, archives_about, cornell_chiafalo],
        summary=summary, sections=sections, table=table,
        page_credits={
            normalize_source_url(cornell_article_ii.source_url): PageCredit(publisher="cornell.edu"),
            normalize_source_url(justia.source_url): PageCredit(publisher="Justia"),
            normalize_source_url(archives_allocation.source_url): PageCredit(publisher="National Archives"),
            normalize_source_url(archives_about.source_url): PageCredit(publisher="National Archives"),
            normalize_source_url(cornell_chiafalo.source_url): PageCredit(publisher="cornell.edu"),
        },
        not_found=[
            NotFoundTarget(target_id=f"topic-{n:02d}-target-01", question=question, searched=False)
            for n, question in enumerate(unresearched_questions, start=1)
        ],
    )


_ELECTORAL_COLLEGE_GOLDEN = """\
# How does the U.S. Electoral College work?

Evidence as of 2026-09-25 · 5 sources

## Bottom line

Under the Constitution, each State appoints, in the manner its Legislature directs, a number of electors equal to its Senators and Representatives in Congress [1][2]; archives.gov reports 538 electoral votes in all, with 270 needed to elect, for the 2024 and 2028 presidential elections [3]. The electors meet in their respective states and vote by ballot for two persons [1], and if two or more candidates remain with equal votes, the Senate chooses the Vice President from them by ballot [2].

| What was measured | Result | Who reported it (and when) | Source |
|---|---|---|---|
| The District of Columbia | three electors | National Archives | [3] |
| "…Senators and Representatives in its U.S. Congressional delegation—two votes for its Senators in the U.S. Senate…" | two votes | National Archives | [3] |
| Total Electoral Votes, 2024 and 2028 presidential elections | 538 electoral votes | National Archives | [3] |
| Majority Needed to Elect, 2024 and 2028 presidential elections | 270 votes | National Archives | [3] |

*No figure in this table is a forecast.*

## How many electors there are

- archives.gov reports 538 total electoral votes and 270 votes as the majority needed to elect, for the 2024 and 2028 presidential elections, on allocations based on the 2020 Census [3].
- archives.gov states that under the 23rd Amendment of the Constitution the District of Columbia is allocated three electors and treated like a State for purposes of the Electoral College [3].
- archives.gov states that electoral votes are allocated among the States based on the Census, every State receiving a number of votes equal to the number of Senators and Representatives in its U.S. Congressional delegation — two votes for its Senators in the U.S. Senate plus a number of votes equal to the number of its Congressional districts, one for each Member in the House of Representatives [3][4].

## How electors are appointed and who may serve

- According to cornell.edu and justia.com, the Constitution provides that each State shall appoint, in such manner as the Legislature thereof may direct, a number of electors equal to the whole number of Senators and Representatives to which the State may be entitled in the Congress [1][2].
- According to justia.com and cornell.edu, no Senator or Representative, or person holding an office of trust or profit under the United States, shall be appointed an elector [1][2].
- cornell.edu states that the Constitution's text and the Nation's history both support allowing a State to enforce an elector's pledge to support his party's nominee — and the state voters' choice — for President [5].

## How the electors vote

- According to cornell.edu, the electors shall meet in their respective states and vote by ballot for two persons, of whom one at least shall not be an inhabitant of the same state with themselves [1].

## When no candidate has a majority

- According to justia.com, if two or more candidates should remain with equal votes, the Senate shall choose from them by ballot the Vice President [2].

## What we couldn't confirm

This run did not research:
- What date does federal law set for the appointment of presidential electors?
- Which states award their electoral votes by congressional district rather than statewide?
- On what day must the presidential electors meet to cast their votes?
- In what place must the electors meet to cast their votes?
- What instrument must a state's executive issue to certify which electors were appointed?
- To which officials must the certificate identifying a state's appointed electors be transmitted?
- What legal effect does a state's determination of its electors have if it is made under the federal safe-harbor provision?
- On what date must Congress count the electoral votes?
- Who presides over the joint session at which Congress counts the electoral votes?
- Which body elects the President if no candidate receives a majority of the electoral votes?
- What voting arrangement governs the House of Representatives when it elects the President?
- What voting arrangement governs the Senate when it elects the Vice President?

## Sources

1. cornell.edu — [Article II | U.S. Constitution | US Law | LII / Legal Information Institute](https://law.cornell.edu/constitution/articleii)
2. Justia — [Electoral College :: Article II. Executive Department :: U.S. Constitution Annotated :: Justia](https://law.justia.com/constitution/us/article-2/03-electoral-college.html)
3. National Archives — [Distribution of Electoral Votes](https://archives.gov/electoral-college/allocation)
4. National Archives — [What is the Electoral College?](https://archives.gov/electoral-college/about)
5. cornell.edu — [CHIAFALO v. WASHINGTON | Supreme Court | US Law | LII / Legal Information Institute](https://law.cornell.edu/supremecourt/text/19-465)

How this was researched: [evidence log](report-e5d6cc40fa74403ab7af075a9153c305-0-evidence.md)
"""


def test_the_1333_electoral_college_smoke_renders_the_spec_golden() -> None:
    """§14 T3 acceptance: §13.3's exact rendering from a hand-built
    composition shaped like the run's own quality JSON (a capped run, 12
    required targets never searched)."""
    assert render_written_report(_electoral_college_composition()) == _ELECTORAL_COLLEGE_GOLDEN
