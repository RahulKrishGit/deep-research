"""Spec §6.1: the report's shape, its reader labels, and the evidence log."""

from __future__ import annotations

import re

from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report import (
    _FACTS_HEADER,
    _point_labels,
    _row_label,
    figure_label,
    render_finding_log,
    render_written_report,
    report_scope,
    written_citations,
)
from deep_research.utils.types import (
    EarlierEdition,
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
    SubTopic,
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
    assert "No checked finding answers it: 1 search made, 1 page read." in report
    assert not VERDICT_WORDS.search(report)


def _plain_composition(**overrides: object) -> ReportComposition:
    """The fixture without its plan vocabulary or its Not found trail."""
    return _composition().model_copy(update=overrides)


def test_the_header_names_the_plan_without_its_identifiers() -> None:
    """The scope lists the planned questions' titles, not the plan's own ids (ev-1 audit A6).

    The reader met "3 planned sub-topic(s), in priority order: topic-01 …", which
    is the plan's bookkeeping rather than the scope the report assumes.
    """
    topics = [SubTopic(coverage_id="topic-01", title="Measured sound-quality scores",
                       rationale="r", search_queries=["q"], success_criteria=["c"],
                       priority=1, evidence_targets=[make_target()]),
              SubTopic(coverage_id="topic-02", title="Microphone recording quality",
                       rationale="r", search_queries=["q"], success_criteria=["c"],
                       priority=2, evidence_targets=[make_target("topic-02-target-01")])]
    report = render_written_report(_plain_composition(scope=report_scope(topics)))

    header = report.splitlines()[2]
    assert "Measured sound-quality scores" in header and "Microphone recording quality" in header
    assert "topic-01" not in header and "topic-02" not in header
    assert "planned sub-topic" not in header and "required target" not in header


def test_the_header_counts_unanswered_planned_questions_and_points_to_not_found() -> None:
    """A reader is told how many planned questions are open and where they are listed."""
    unanswered = _plain_composition(
        not_found=[NotFoundTarget(target_id="topic-01-target-01", question="Which one ranks first?"),
                   NotFoundTarget(target_id="topic-02-target-01", question="What does the lab measure?")],
    )
    assert ("2 planned questions unanswered, listed under Not found."
            in render_written_report(unanswered).splitlines()[2])

    one = _plain_composition(
        not_found=[NotFoundTarget(target_id="topic-01-target-01", question="Which one ranks first?")],
    )
    assert ("1 planned question unanswered, listed under Not found."
            in render_written_report(one).splitlines()[2])

    answered = _plain_composition(not_found=[])
    assert "every planned question is answered." in render_written_report(answered).splitlines()[2]


def test_a_not_found_entry_states_the_search_without_dumping_it() -> None:
    """Each entry is the planned question and how the search went, not its log (ev-1 audit A6).

    The ev-1 report's entries were 730-803-character lines inlining every query
    string and every page URL, off-topic hosts included.
    """
    searched = render_written_report(_plain_composition())
    assert "No checked finding answers it: 1 search made, 1 page read." in searched
    assert "BloombergNEF 2025 US storage forecast" not in searched
    assert "about.bnef.com" not in searched
    assert "recorded in full in the evidence log" in searched

    unsearched = _plain_composition(
        not_found=[NotFoundTarget(target_id="topic-03-target-01", question="What does the lab project?")],
    )
    assert "No checked finding answers it: not searched in this run." in render_written_report(unsearched)


def test_the_evidence_log_keeps_the_search_trail_the_reader_no_longer_carries() -> None:
    """What the reader report drops, the ledger keeps: every query and every page read."""
    log = render_finding_log(_composition())

    assert "BloombergNEF 2025 US storage forecast" in log
    assert "https://about.bnef.com/x" in log
    assert "What does BloombergNEF project for 2025?" in log


def test_a_key_facts_row_that_answers_no_planned_target_is_not_printed() -> None:
    """The table carries the question's facts, not every figure the run verified (ev-1 audit A6).

    The ev-1 table's five rows were four copies of a site-wide page counter that
    answers no planned question. A figure that answers nothing stays citable in
    prose and stays in the evidence log.
    """
    base = _composition()
    answering, unplanned = base.fact_rows
    assert answering.target_ids  # the fixture's rows answer their planned targets

    only_unplanned = _plain_composition(
        fact_rows=[unplanned.model_copy(update={"target_ids": []})],
    )
    report = render_written_report(only_unplanned)
    assert "## Key facts" not in report
    assert f"| {unplanned.value} |" not in report  # a cell, so never the summary's prose

    both = render_written_report(base.model_copy(update={
        "fact_rows": [answering, unplanned.model_copy(update={"target_ids": []})]}))
    assert f"| {answering.value} |" in both and f"| {unplanned.value} |" not in both


def test_a_pass_with_no_verified_figure_still_says_so() -> None:
    """Nothing verified is a fact about the pass, not a row that answers nothing."""
    report = render_written_report(_plain_composition(fact_rows=[]))

    assert "## Key facts" in report
    assert "No figure passed the Evidence Verifier." in report


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


def test_an_undated_pass_says_so_instead_of_reading_a_clock() -> None:
    """No recorded timestamp prints as "not recorded", never as a date.

    ``as_of`` is the newest timestamp the *evidence* carries, and a pass whose
    evidence carries none has an empty one. The header must say so: a report
    that printed a date there would be asserting currency the evidence never
    stated, and the README promises the undated session says so instead.
    """
    composition = _composition().model_copy(update={"as_of": ""})

    header = render_written_report(composition).splitlines()[2]

    assert "As of not recorded." in header
    assert re.search(r"\d{4}-\d{2}-\d{2}", header) is None


def test_the_header_never_prints_two_full_stops_in_a_row() -> None:
    """The scope text ends with its own full stop, so the header adds none of its own.

    The third pre-flight run's report (line 3) read "... state is assumed..
    5 sources cited; ..." -- the sentence's own stop plus the header's. A scope
    the plan did not punctuate still gets one.
    """
    base = _composition()
    planned = report_scope([SubTopic(coverage_id="topic-01", title="2024 capacity additions",
                                     rationale="r", search_queries=["q"],
                                     success_criteria=["c"], priority=1)])
    assert planned.endswith(".")  # the plan's own scope text, as the report prints it

    terminated = render_written_report(
        base.model_copy(update={"scope": planned})
    ).splitlines()[2]
    unterminated = render_written_report(
        base.model_copy(update={"scope": "United States"})
    ).splitlines()[2]

    assert ".." not in terminated and ".." not in unterminated
    assert re.search(r"is assumed\. \d+ sources cited", terminated)
    assert re.search(r"Scope: United States\. \d+ sources cited", unterminated)


def test_a_sub_topic_title_ending_in_a_full_stop_keeps_one_stop() -> None:
    """The scope punctuates the list it names, and a title may end its own sentence.

    ``report_scope`` appends the assumed-scope note after the listed sub-topics,
    so a plan whose title ends with a full stop ("Capacity in the U.S.") printed
    "U.S.. No geography, ..." in every header of that session's report. The
    scope's own sentence ends once, whatever its titles end with.
    """
    scope = report_scope([SubTopic(coverage_id="topic-01", title="Capacity in the U.S.",
                                   rationale="r", search_queries=["q"],
                                   success_criteria=["c"], priority=1)])
    assert "U.S.." not in scope

    header = render_written_report(
        _composition().model_copy(update={"scope": scope})
    ).splitlines()[2]

    assert ".." not in header
    assert "Capacity in the U.S. No geography" in header


def test_the_header_prints_a_recorded_stamp_as_a_utc_time() -> None:
    """The reader meets a UTC minute, not the raw ISO stamp of the third pre-flight run.

    The record keeps its own precision -- the quality JSON prints ``as_of``
    exactly as recorded -- and only the rendering loses it. The minute is
    truncated, never rounded, so no stamp moves to another day; a value that
    names no zone, or no instant at all, is printed as it was recorded rather
    than given a zone the evidence never stated.
    """
    base = _composition()

    def header(as_of: str) -> str:
        return render_written_report(base.model_copy(update={"as_of": as_of})).splitlines()[2]

    assert header("2026-09-25T12:44:37.098843+00:00").startswith("*As of 2026-09-25 12:44 UTC.")
    assert header("2026-09-25T23:59:59.999999+00:00").startswith("*As of 2026-09-25 23:59 UTC.")
    assert header("2026-09-25T14:44:37+02:00").startswith("*As of 2026-09-25 12:44 UTC.")
    assert header("2026-09-25").startswith("*As of 2026-09-25.")
    assert header("2026-09-25T12:44:37").startswith("*As of 2026-09-25T12:44:37.")
    assert header("sometime in 2026").startswith("*As of sometime in 2026.")
    assert header("").startswith("*As of not recorded.")


def test_a_resolved_period_is_named_on_the_label() -> None:
    assert figure_label(organisation="Example Statistical Agency", attribution="own",
                        relay_host=None, kind="actual", release=None, unchecked=False,
                        period_resolved_from="2026-02-20") == (
        "Example Statistical Agency's own figure; actual; "
        "period resolved from the page date 2026-02-20"
    )


def test_the_subject_column_appears_only_when_a_row_has_a_subject() -> None:
    base = _composition()
    assert _FACTS_HEADER in render_written_report(base)
    named = base.fact_rows[0].model_copy(update={"subject": "Model A"})
    report = render_written_report(base.model_copy(update={"fact_rows": [named, *base.fact_rows[1:]]}))
    assert "| Organisation | Subject | Measure |" in report and "| Model A |" in report


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
    """Fix round 1 (Important 2): the give-away "a" is required as the whole name, not alone.

    "Model A" against "Model B" leaves just the "a", which any sentence may
    carry ("... scored a 4.5 out of 5"); matched alone it would hand Model B's
    sentence Model A's label, so the subject's full run ("model a") is required.
    """
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
    """One page stating one value twice: a 2024 actual and a 2025 forecast.

    Two periods of one value are two facts (PD-9), so the page earns two Key
    facts rows, K001 and K002, and either sentence cites the same finding.
    """
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
    """The period a sentence states picks its row: two rows of one value are not interchangeable.

    With the value alone both rows match every sentence that states it, so the
    2024 sentence took the 2025 forecast's label too (a kind the sentence never
    states) and the 2025 sentence was refused as restating both.
    """
    composition = _two_period_composition()
    current, forecast = composition.summary
    assert _point_labels(current, composition) == [_row_label(composition.fact_rows[0])]
    assert _point_labels(forecast, composition) == [_row_label(composition.fact_rows[1])]


def test_an_earlier_editions_value_cites_its_own_page() -> None:
    """A row printing an earlier edition's value must carry that page's citation (PD-9).

    ``earlier`` names the finding the value came from; the reader cannot trace
    "18.2 GW" to any page unless the row's source markers and the Sources list
    carry it beside the row's own.
    """
    latest = _finding("https://agency.example.test/outlook", "19.6 GW is expected in 2026.",
                      "19.6", "GW", organisation="Example Agency", kind="forecast", period="2026")
    earlier = _finding("https://agency.example.test/outlook-jan", "18.2 GW was expected.",
                       "18.2", "GW", organisation="Example Agency", kind="forecast", period="2025")
    composition = ReportComposition(
        question="What capacity is expected?", session_id="s", as_of="2026-09-24T00:00:00+00:00",
        scope="Example", findings=[latest, earlier],
        fact_rows=[FactRow(row_id="K001", organisation="Example Agency", attribution="own",
                           measure="capacity", period="2026", value="19.6 GW", kind="forecast",
                           release="released 2025-06-14", finding_id=finding_fingerprint(latest),
                           target_ids=["topic-01-target-01"],
                           earlier=[EarlierEdition(value="18.2 GW", release="released 2025-01-14",
                                                   finding_id=finding_fingerprint(earlier))])],
    )
    assert [citation.url for citation in written_citations(composition)] == [
        latest.source_url, earlier.source_url]
    report = render_written_report(composition)
    row = next(line for line in report.splitlines() if line.startswith("| Example Agency"))
    assert row.rstrip().endswith("| [1][2] |")
    assert earlier.source_url in report


def test_a_sentence_citing_a_relay_copy_carries_the_relays_label() -> None:
    """D13: the label beside a sentence is its own cited page's, not the row primary's.

    One fact, two pages: the organisation's own page (the row's primary, hence
    the row's own label) and a relay of it. A sentence that cites the relay
    must not be labelled "Example Agency's own figure" -- its marker points at
    the relay, and a relay is never presented as the issuer.
    """
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
