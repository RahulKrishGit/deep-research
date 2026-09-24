"""Spec §6.1: the report's shape, its reader labels, and the evidence log."""

from __future__ import annotations

import re

from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report import (
    figure_label,
    render_finding_log,
    render_written_report,
    written_citations,
)
from deep_research.utils.types import (
    FactRow,
    FigureContext,
    FigureResult,
    FindingVerification,
    NotFoundTarget,
    RejectedDraftPoint,
    ReportComposition,
    ReportPoint,
    ReportSection,
    ReportStatement,
)
from tests.evidence_fakes import figure, make_finding, make_read

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
                release="released 2025-03-12", finding_id=eia_id),
        FactRow(row_id="K002", organisation="U.S. Energy Information Administration", attribution="relayed",
                relay_host="ent.news", measure="battery storage power capacity added", period="2025",
                value="14 GW", kind="forecast", release="January 2025 STEO", finding_id=steo_id),
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
        sections=[ReportSection(title="Basis", points=[point(3, "EIA counts 10.4 GW of new capacity.", eia)])],
        fact_rows=rows,
        not_found=[NotFoundTarget(target_id="topic-03-target-01", question="What does BloombergNEF project for 2025?",
                                  queries=["BloombergNEF 2025 US storage forecast"], pages_read=["https://about.bnef.com/x"], searched=True)],
        finding_labels={"F01": eia_id, "F02": steo_id},
        rejected_points=[RejectedDraftPoint(where="summary[2]", text="Wood Mackenzie reports 18.9 GW of grid-scale storage.",
                                            finding_labels=["F03"], reason="scope not carried by the cited figures: grid-scale")],
    )


def test_figure_label_follows_the_spec_labels() -> None:
    assert figure_label(organisation="EIA", attribution="own", relay_host=None, kind="actual",
                        release="released 2025-03-12", unchecked=False) == "EIA's own figure; actual; released 2025-03-12"
    assert figure_label(organisation="EIA", attribution="relayed", relay_host="ent.news", kind="forecast",
                        release="January 2025 STEO", unchecked=True) == "relayed by ent.news from EIA; forecast (January 2025 STEO); unchecked context"
    assert figure_label(organisation="ent.news", attribution="unattributed", relay_host=None, kind="forecast",
                        release=None, unchecked=False) == "source does not attribute it; forecast (release not stated on the page)"


def test_the_report_has_the_spec_shape_in_order() -> None:
    report = render_written_report(_composition())
    headings = [line for line in report.splitlines() if line.startswith("#")]
    assert headings[1:] == ["## Executive summary", "## Key facts", "## Basis", "## Not found", "## Sources"]
    assert "| Organisation | Measure | Period | Value | Kind | Scope | Release or edition | Source |" in report
    assert "U.S. Energy Information Administration (relayed by ent.news)" in report
    assert "U.S. Energy Information Administration's own figure; actual; released 2025-03-12" in report
    assert "relayed by ent.news from U.S. Energy Information Administration; forecast (January 2025 STEO)" in report
    assert '"BloombergNEF 2025 US storage forecast"' in report and "Pages read: 1" in report
    assert not VERDICT_WORDS.search(report)


def test_sources_are_only_the_cited_ones_in_first_use_order() -> None:
    index = written_citations(_composition())
    assert [c.number for c in index] == [1, 2]
    assert index[0].url.startswith("https://eia.gov") and index[1].url.startswith("https://ent.news")


def test_the_evidence_log_keeps_snippets_drop_reasons_and_refusals() -> None:
    log = render_finding_log(_composition())
    assert "### F01" in log and "Generators added 10.4 gigawatts" in log
    assert "context_rejected" in log and "A growth rate, not a capacity." in log
    assert "Wood Mackenzie reports 18.9 GW of grid-scale storage." in log
    assert "F03" in log and "scope not carried" in log
    # The log's release must agree with the report's: it comes from the same
    # fact_rows the report renders from, not a hard-coded "not stated".
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
    """R3: a kept forecast finding no fact row covers must still show the
    release its own page carries, not "release not stated on the page".
    """
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
    """Important 1 + the R2 ruling on the evidence log: two editions of one
    page (same URL, sub-topic and content; a different figure and release)
    share a ``finding_fingerprint``. Each must print its own release, never
    the other edition's (Important 1), and each must keep its own label,
    never collapse onto or drop the other's (R2).
    """
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
    # As ``_fold_revisions`` would produce: one row, the later edition
    # primary, keyed by the fingerprint both editions share.
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
    assert "forecast (released 2025-01-10)" in log   # edition_a's own release
    assert "forecast (released 2025-02-14)" in log   # edition_b's own release


def test_the_report_shows_unchecked_context_on_a_fact_row() -> None:
    base = _composition()
    unchecked_row = base.fact_rows[0].model_copy(update={"context_unchecked": True})
    composition = base.model_copy(update={"fact_rows": [unchecked_row, base.fact_rows[1]]})
    report = render_written_report(composition)
    assert "actual (unchecked context)" in report


def test_the_report_shows_an_unattributed_row_reading() -> None:
    base = _composition()
    unattributed_row = base.fact_rows[1].model_copy(update={"attribution": "unattributed", "relay_host": None})
    composition = base.model_copy(update={"fact_rows": [base.fact_rows[0], unattributed_row]})
    report = render_written_report(composition)
    assert "U.S. Energy Information Administration (source does not attribute it)" in report
