"""Spec §6.1-6.2: the Report Writer writes from verified findings only."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report import render_finding_log
from deep_research.agents.report_writer import (
    REPORT_WRITER_NAME,
    ReportWriterAgent,
    ReportWriterDraft,
    WriterPointDraft,
    check_point,
    compose_written_report,
    finding_registry,
    writer_messages,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import TokenUsage, Tracker
from deep_research.providers import (
    ProviderOutputLimitError,
    ProviderResponseError,
    ProviderResponseTelemetry,
)
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    FigureContext,
    FigureResult,
    FindingVerification,
    ResearchState,
    ScoredSource,
    SubTopic,
)
from tests.agent_fakes import ScriptedCompleter
from tests.evidence_fakes import figure, make_finding, make_read, make_target
from tests.research_fakes import synthesizer_tools

EIA = "U.S. Energy Information Administration"


def _checked(url, text, value, unit, *, organisation, attribution="own", kind="actual", period="2024",
             scope=None, target="topic-01-target-01", **fields):
    read = make_read(text, url=url, title=f"{organisation} page")
    finding = make_finding(read, text, figures=[figure(value, unit, period, kind)], target_ids=[target], **fields)
    result = FigureResult(figure=finding.figures[0], matched=True, evidence_words=text,
                          context=FigureContext(period=period, scope=scope, attribution=attribution,
                                                organisation=organisation, kind=kind))
    return finding.model_copy(update={"verification": FindingVerification(status="verified", figure_results=[result])})


def _checked_multi(url, text, specs, *, organisation, attribution="own", period="2025",
                   scope=None, target="topic-01-target-01", **fields):
    """Like ``_checked``, but with several figures: ``specs`` is a sequence
    of ``(value, unit, kind)``, one per figure the finding states.
    """
    read = make_read(text, url=url, title=f"{organisation} page")
    figs = [figure(value, unit, period, kind) for value, unit, kind in specs]
    finding = make_finding(read, text, figures=figs, target_ids=[target], **fields)
    results = [FigureResult(figure=figs[i], matched=True, evidence_words=text,
                            context=FigureContext(period=period, scope=scope, attribution=attribution,
                                                  organisation=organisation, kind=kind))
              for i, (_, _, kind) in enumerate(specs)]
    return finding.model_copy(update={"verification": FindingVerification(status="verified", figure_results=results)})


EIA_2024 = _checked("https://www.eia.gov/todayinenergy/detail.php?id=64705",
                    "Generators added 10.4 gigawatts (GW) of new battery storage capacity in 2024,",
                    "10.4", "GW", organisation=EIA, release_date="2025-03-12")
STEO = _checked("https://ent.news/2025/1/940.pdf",
                "Data source: U.S. Energy Information Administration, Short-Term Energy Outlook, January 2025. "
                "Battery storage capacity grows by 47% (14 GW) in 2025.",
                "14", "GW", organisation=EIA, attribution="relayed", kind="forecast", period="2025",
                target="topic-02-target-01", vintage="January 2025 STEO")
WOODMAC_ALL = _checked("https://www.woodmac.com/press-releases/2025-record",
                       "The U.S. energy storage market hit a record 18.9 gigawatts of battery energy storage system "
                       "installations in 2025 across all segments.",
                       "18.9", "gigawatts", organisation="Wood Mackenzie", period="2025", scope="all segments",
                       target="topic-04-target-01")


def _task_state():
    targets = [make_target(organisation="EIA"),
               make_target("topic-02-target-01", kind="forecast", period="2025", organisation="EIA"),
               make_target("topic-04-target-01", period="2025", required=False)]
    topics = [SubTopic(coverage_id=t.coverage_id, title=t.target_id, rationale="r", search_queries=["q"],
                       success_criteria=["c"], priority=n, evidence_targets=[t]) for n, t in enumerate(targets, 1)]
    return ResearchState(session_id="s", original_question="How much battery storage was added in 2024, and what is forecast for 2025?",
                         sub_topics=topics, verified_findings=[EIA_2024, STEO, WOODMAC_ALL])


def _labels(registry):
    return {finding.source_url.split("/")[2]: label for label, finding in registry}


def test_the_registry_labels_citable_findings_answers_first() -> None:
    state = _task_state()
    targets = [t for topic in state.sub_topics for t in topic.evidence_targets]
    registry = finding_registry(state.verified_findings, targets)
    assert [label for label, _ in registry] == ["F01", "F02", "F03"]
    assert registry[2][1] is WOODMAC_ALL   # answers only an optional target


def test_writer_messages_list_every_figure_in_the_fixed_format(writer) -> None:
    messages = writer_messages(writer.build_task(_task_state()))
    line = re.compile(r"^F\d{2} \| figure 1: 14 GW \| period 2025 \| kind forecast \| organisation " + re.escape(EIA)
                      + r" \| label: relayed by ent\.news from " + re.escape(EIA) + r"; forecast \(January 2025 STEO\)$", re.M)
    assert line.search(messages[-1].content)


def test_check_point_refuses_untraced_numbers_and_unattested_names() -> None:
    assert check_point("Generators added 12 GW in 2024.", [EIA_2024], geographies=["United States"]).reasons
    assert check_point("BloombergNEF reports 10.4 GW added in 2024.", [EIA_2024], geographies=[]).reasons
    assert check_point("Generators added 10.4 GW in the United States in 2024.", [EIA_2024],
                       geographies=["United States"]).reasons == ()


def test_grid_scale_wording_on_an_all_segment_figure_is_refused(writer) -> None:
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["woodmac.com"]
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(
        text="Wood Mackenzie reports 18.9 GW of grid-scale storage installed in 2025.", finding_labels=[label])], sections=[])
    composition = compose_written_report(task, draft)
    assert composition.summary == []
    [refused] = composition.rejected_points
    assert "grid-scale" in refused.reason and refused.finding_labels == [label]


def test_forecast_stated_as_fact_is_rewritten_once_and_kept(writer) -> None:
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["ent.news"]
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(
        text="EIA's outlook adds 14 GW in 2025.", finding_labels=[label])], sections=[])
    composition = compose_written_report(task, draft)
    [point] = composition.summary
    assert point.text == f"EIA's outlook adds 14 GW in 2025, according to {EIA}'s forecast."
    assert composition.rejected_points == []


def test_a_hardened_forecast_takes_the_pages_own_modal(writer) -> None:
    # F3: the 2026-09-24 pre-flight lost its forecasts to "will" where the page says "could"
    could = _checked("https://ent.news/2025/1/941.pdf",
                     "Data source: U.S. Energy Information Administration, Short-Term Energy Outlook, January 2025. "
                     "Battery storage capacity could grow by 14 GW in 2025.",
                     "14", "GW", organisation=EIA, attribution="relayed", kind="forecast", period="2025",
                     target="topic-02-target-01", vintage="January 2025 STEO")
    task = writer.build_task(_task_state().model_copy(update={"verified_findings": [EIA_2024, could]}))
    label = _labels(task.registry)["ent.news"]
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(
        text="EIA's STEO says battery capacity will grow by 14 GW in 2025.", finding_labels=[label])], sections=[])
    composition = compose_written_report(task, draft)
    [point] = composition.summary
    assert point.text == "EIA's STEO says battery capacity could grow by 14 GW in 2025."
    assert composition.rejected_points == []


def test_an_unchanged_rewrite_is_refused_not_published_as_an_actual(writer) -> None:
    """CRITICAL 1: when ``hedge_forecast`` cannot change anything -- an
    unrelated word ("project" in "project delays") already satisfies the
    page's own forecast-role check on a *different* clause -- the sentence
    must not be published unhedged.
    """
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["ent.news"]
    drafted = "Despite project delays, battery capacity grows by 14 GW in 2025."
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(text=drafted, finding_labels=[label])], sections=[])
    composition = compose_written_report(task, draft)
    assert composition.summary == []
    [refused] = composition.rejected_points
    assert refused.reason == "a forecast stated as fact"
    assert refused.text == drafted   # the drafted text, not a partial rewrite attempt


def test_a_hedge_on_an_unrelated_clause_still_refuses_the_quantitys_own_clause(writer) -> None:
    """CRITICAL 1: ``hedge_forecast``'s will/would replacement can land on a
    clause other than the forecast quantity's own ("more will follow" is a
    different, later assertion). The quantity's own clause ("14 GW was
    added") must still be checked on its own terms.
    """
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["ent.news"]
    drafted = "In 2025 14 GW was added, and more will follow."
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(text=drafted, finding_labels=[label])], sections=[])
    composition = compose_written_report(task, draft)
    assert composition.summary == []
    [refused] = composition.rejected_points
    assert refused.reason == "a forecast stated as fact"
    assert refused.text == drafted


def test_a_past_tense_verb_outside_the_shared_vocabulary_is_still_refused(writer) -> None:
    """The same defect with 'grew', a past-tense outcome verb the shared
    realised-outcome vocabulary does not list -- only the positive hedge
    check (not the realised-outcome exemption) catches this one.
    """
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["ent.news"]
    drafted = "Battery capacity grew by 14 GW in 2025, and more will follow."
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(text=drafted, finding_labels=[label])], sections=[])
    composition = compose_written_report(task, draft)
    assert composition.summary == []
    [refused] = composition.rejected_points
    assert refused.reason == "a forecast stated as fact"
    assert refused.text == drafted


def test_a_point_over_the_character_limit_is_refused_whole_not_cut(writer) -> None:
    """Important 2 (plan-mandated): a too-long point is refused, never cut;
    ``RejectedDraftPoint.text`` carries the whole whitespace-collapsed draft.
    """
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["eia.gov"]
    drafted = "Generators added 10.4 GW of battery storage in 2024. " * 15
    collapsed = " ".join(drafted.split())
    assert len(collapsed) > 600
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(text=drafted, finding_labels=[label])], sections=[])
    composition = compose_written_report(task, draft)
    assert composition.summary == []
    [refused] = composition.rejected_points
    assert refused.reason == "longer than 600 characters"
    assert refused.text == collapsed


def test_a_release_date_is_a_date_not_an_untraced_number(writer) -> None:
    # F2: "2025-03-12" is checked whole against the findings, never as the numbers 03 and 12
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["eia.gov"]
    kept = ReportWriterDraft(executive_summary=[WriterPointDraft(
        text="EIA reports that 10.4 GW was added in 2024 (released 2025-03-12).", finding_labels=[label])], sections=[])
    composition = compose_written_report(task, kept)
    assert len(composition.summary) == 1 and composition.rejected_points == []
    invented = ReportWriterDraft(executive_summary=[WriterPointDraft(
        text="EIA reports that 10.4 GW was added in 2024 (released 2025-04-30).", finding_labels=[label])], sections=[])
    [refused] = compose_written_report(task, invented).rejected_points
    assert "dates the cited findings do not carry: 2025-04-30" in refused.reason


def test_an_actual_stated_as_a_forecast_is_refused(writer) -> None:
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["eia.gov"]
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(
        text="EIA expects 10.4 GW to be added in 2024.", finding_labels=[label])], sections=[])
    assert compose_written_report(task, draft).summary == []


def test_a_summary_restatement_and_an_unknown_label_are_refused(writer) -> None:
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["eia.gov"]
    draft = ReportWriterDraft(executive_summary=[
        WriterPointDraft(text="Generators added 10.4 GW of battery storage in 2024.", finding_labels=[label]),
        WriterPointDraft(text="In 2024, 10.4 GW of battery storage was added.", finding_labels=[label]),
        WriterPointDraft(text="Generators added 10.4 GW in 2024.", finding_labels=["F99"]),
    ], sections=[])
    composition = compose_written_report(task, draft)
    assert [p.statement.statement_id for p in composition.summary] == ["S001"]
    assert [r.where for r in composition.rejected_points] == ["summary[1]", "summary[2]"]


@pytest.mark.asyncio
async def test_a_failed_draft_still_composes_the_key_facts(writer_failing: ReportWriterAgent, tracker: Tracker) -> None:
    async with tracker.session_span("session-1", "question"):
        run = await writer_failing.run(_task_state())
    assert "## Key facts" in run.result.markdown and "10.4 GW" in run.result.markdown
    assert any(e.error_type == "report_writer_provider_error" for e in run.errors)


@pytest.mark.asyncio
async def test_a_truncated_draft_is_asked_once_more_at_high_effort(writer_truncated_then_ok, tracker: Tracker) -> None:
    agent, completer = writer_truncated_then_ok
    async with tracker.session_span("session-1", "question"):
        run = await agent.run(_task_state())
    assert [name for name, _, _ in completer.calls] == ["ReportWriterDraft", "ReportWriterDraft"]
    assert completer.efforts == [None, "high"] and run.result.statement_count >= 1   # profile effort, then high (F10)


# --- R1: dropped and duplicate findings still reach the evidence log ----------


def test_dropped_and_duplicate_findings_still_reach_the_composition(writer) -> None:
    """R1: a dropped duplicate of a citable finding must still be in
    ``ReportComposition.findings``, so the evidence log lists it (spec 5.3).
    """
    dropped = EIA_2024.model_copy(update={
        "verification": FindingVerification(status="dropped", dropped_reason="all_figures_dropped"),
    })
    state = _task_state().model_copy(update={"verified_findings": [EIA_2024, STEO, WOODMAC_ALL, dropped]})
    task = writer.build_task(state)
    composition = compose_written_report(task, None)
    assert dropped in composition.findings
    assert len(composition.findings) == 4
    # The dropped duplicate never earns a citable label of its own.
    assert all(finding is not dropped for _, finding in task.registry)
    # m4: the evidence log must render it too, under its own label -- not
    # collapsed onto or dropping the citable edition's "F01" (R2).
    log = render_finding_log(composition)
    assert "dropped (all_figures_dropped)" in log
    assert log.count(f"### F01 — {EIA_2024.source_title}") == 1
    assert "### X01" in log


# --- R2: two revision editions sharing a fingerprint must not collapse --------


def test_two_revision_editions_sharing_a_fingerprint_both_label_the_target(writer) -> None:
    """R2: ``finding_fingerprint`` collides across two revision editions with
    the same URL, sub-topic and content but a different figure or release.
    A by-id lookup keyed on that fingerprint must not drop one edition's
    label; both must still answer the target by their own distinct label.
    """
    same_text = ("Data source: U.S. Energy Information Administration, Short-Term Energy Outlook, "
                "January 2025. Battery storage capacity grows by 40% (13 GW) in 2025.")
    edition_a = _checked("https://ent.news/2025/1/942.pdf", same_text, "13", "GW", organisation=EIA,
                        attribution="relayed", kind="forecast", period="2025", target="topic-02-target-01",
                        vintage="January 2025 STEO", release_date="2025-01-10")
    edition_b = _checked("https://ent.news/2025/1/942.pdf", same_text, "14", "GW", organisation=EIA,
                        attribution="relayed", kind="forecast", period="2025", target="topic-02-target-01",
                        vintage="January 2025 STEO", release_date="2025-02-14")
    assert finding_fingerprint(edition_a) == finding_fingerprint(edition_b)
    state = _task_state().model_copy(update={"verified_findings": [EIA_2024, edition_a, edition_b]})
    task = writer.build_task(state)
    labels = sorted(label for label, finding in task.registry if finding in (edition_a, edition_b))
    assert len(labels) == 2   # each edition kept its own distinct label

    messages = writer_messages(task)
    line = next(l for l in messages[-1].content.splitlines() if l.startswith("- topic-02-target-01"))
    for label in labels:
        assert label in line


# --- R4: grid-scale and utility-scale are the same segment --------------------


def test_grid_scale_wording_on_a_utility_scale_figure_is_accepted(writer) -> None:
    """R4: grid-scale and utility-scale name the same segment (as verified_facts
    treats them for target answering); a sentence in either spelling is
    supported by a finding stated in the other.
    """
    utility = _checked("https://ir.eia.gov/utility-scale-page",
                       "EIA reports 5 GW of utility-scale battery storage added in 2025.",
                       "5", "GW", organisation=EIA, period="2025", scope="utility-scale",
                       target="topic-04-target-01")
    state = _task_state().model_copy(update={"verified_findings": [EIA_2024, STEO, utility]})
    task = writer.build_task(state)
    label = _labels(task.registry)["ir.eia.gov"]
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(
        text="EIA reports 5 GW of grid-scale battery storage added in 2025.", finding_labels=[label])], sections=[])
    composition = compose_written_report(task, draft)
    assert composition.rejected_points == []
    [point] = composition.summary
    assert "grid-scale" in point.text


def test_utility_scale_wording_on_a_grid_scale_figure_is_accepted(writer) -> None:
    """The equivalence holds in the other spelling direction too."""
    grid = _checked("https://ir.eia.gov/grid-scale-page",
                    "EIA reports 5 GW of grid-scale battery storage added in 2025.",
                    "5", "GW", organisation=EIA, period="2025", scope="grid-scale",
                    target="topic-04-target-01")
    state = _task_state().model_copy(update={"verified_findings": [EIA_2024, STEO, grid]})
    task = writer.build_task(state)
    label = _labels(task.registry)["ir.eia.gov"]
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(
        text="EIA reports 5 GW of utility-scale battery storage added in 2025.", finding_labels=[label])], sections=[])
    composition = compose_written_report(task, draft)
    assert composition.rejected_points == []
    [point] = composition.summary
    assert "utility-scale" in point.text


# --- PD-25: an evaluated source's own identity anchors attest an issuer -------


def test_an_issuer_named_only_in_a_sources_identity_anchors_is_attested(writer) -> None:
    """PD-25: the Source Evaluator's validated issuer attests a name the
    drafted text carries, even when nothing on the page itself states it.
    """
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["eia.gov"]
    source = ScoredSource(
        url="https://eia.gov/todayinenergy/detail.php?id=64705",
        title="U.S. battery capacity increased 66% in 2024",
        rationale="scored by an earlier pass",
        evaluation_status="unscored_missing",
        identity_anchors={"issuer": "Energy Storage Association"},
    )
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(
        text="Energy Storage Association reports that generators added 10.4 GW in 2024.",
        finding_labels=[label])], sections=[])
    without_source = compose_written_report(task, draft)
    assert without_source.summary == []   # unattested without the source
    with_source = compose_written_report(task.model_copy(update={"sources": [source]}), draft)
    assert with_source.rejected_points == []
    [point] = with_source.summary
    assert "Energy Storage Association" in point.text


# --- Gate G3 live refusals: parenthetical/compound-unit clause governance ----


STEO_PAREN = _checked_multi(
    "https://ent.news/2025/1/940.pdf",
    "battery storage capacity growing by 47% (14 GW) in 2025",
    [("47", "%", "forecast"), ("14", "GW", "forecast")],
    organisation=EIA, attribution="relayed", target="topic-02-target-01", vintage="January 2025 STEO",
)
WOODMAC_Q1 = _checked_multi(
    "https://woodmac.com/press-releases/energy-storage-market-continues-strong-growth-in-q1-2025",
    "the report projects that 15 GW/49 GWh of energy storage capacity will be installed across all segments in 2025",
    [("15", "GW", "forecast"), ("49", "GWh", "forecast")],
    organisation="Wood Mackenzie", target="topic-04-target-01", scope="all segments", vintage="Q1 2025",
)


def test_a_parenthetical_restatement_of_the_governing_figure_is_accepted(writer) -> None:
    """Live G3 refusal 1: '(14 GW)' restates '47%', whose own clause reads
    as a forecast ('EIA projected ... growing by 47%'); ``clause_around``
    splits at the parenthesis, and the figure inside it must not lose the
    hedge that governs the figure it restates.
    """
    state = _task_state().model_copy(update={"verified_findings": [EIA_2024, STEO_PAREN]})
    task = writer.build_task(state)
    label = _labels(task.registry)["ent.news"]
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(
        text="In its January 2025 forecast, as reported by ent.news, EIA projected battery storage "
             "capacity growing by 47% (14 GW) in 2025.",
        finding_labels=[label])], sections=[])
    composition = compose_written_report(task, draft)
    assert composition.rejected_points == []
    assert len(composition.summary) == 1


def test_a_compound_units_second_half_shares_the_first_halfs_clause(writer) -> None:
    """Live G3 refusal 2's forecast-as-fact half: '15 GW/49 GWh' is one
    compound figure; the '/' must not clause-split '49 GWh' away from the
    'projects' that governs both halves.
    """
    state = _task_state().model_copy(update={"verified_findings": [EIA_2024, WOODMAC_Q1]})
    task = writer.build_task(state)
    label = _labels(task.registry)["woodmac.com"]
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(
        text="Wood Mackenzie's Q1 2025 forecast projects 15 GW/49 GWh of energy storage capacity "
             "installed across all segments in 2025.",
        finding_labels=[label])], sections=[])
    composition = compose_written_report(task, draft)
    assert composition.rejected_points == []
    assert len(composition.summary) == 1


def test_an_unattested_report_name_stays_refused_after_the_clause_fix(writer) -> None:
    """The clause-governance fix must not launder a name the cited finding
    genuinely does not carry: 'Monitor' is not in WOODMAC_Q1's own text
    (live G3 refusal 2's other half, confirmed honest against the real
    finding: the woodmac.com press release's own snippet never says
    "Monitor", and its evaluated source carries no issuer anchor for it).
    """
    state = _task_state().model_copy(update={"verified_findings": [EIA_2024, WOODMAC_Q1]})
    task = writer.build_task(state)
    label = _labels(task.registry)["woodmac.com"]
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(
        text="Wood Mackenzie's Q1 2025 U.S. Energy Storage Monitor forecast projects 15 GW/49 GWh of "
             "energy storage capacity installed across all segments in 2025.",
        finding_labels=[label])], sections=[])
    composition = compose_written_report(task, draft)
    assert composition.summary == []
    [refused] = composition.rejected_points
    assert "Monitor" in refused.reason
    assert "a forecast stated as fact" not in refused.reason


# --- Gate G3 live refusals: a bare month name restating the page's own date -


def test_a_bare_month_and_year_restating_the_pages_own_date_is_accepted(writer) -> None:
    """Live G3 refusal 4: 'February 2025' restates the page's own
    'stated 2025-02-24' at a coarser grain. ``dates_in`` requires a day (ISO
    or day-first) so it never sees a bare month name, and it fell through to
    an ordinary unattested-name refusal.
    """
    finding = _checked("https://utilitydive.com/news/x",
                       "EIA projects this growth could almost double to an addition of 18.2 GW in 2025.",
                       "18.2", "GW", organisation="Utility Dive", kind="forecast", period="2025",
                       target="topic-02-target-01", statement_date="2025-02-24")
    state = _task_state().model_copy(update={"verified_findings": [EIA_2024, finding]})
    task = writer.build_task(state)
    label = _labels(task.registry)["utilitydive.com"]
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(
        text="Utility Dive reported in February 2025 that EIA projected this growth could almost "
             "double to an addition of 18.2 GW in 2025.",
        finding_labels=[label])], sections=[])
    composition = compose_written_report(task, draft)
    assert composition.rejected_points == []
    assert len(composition.summary) == 1


def test_a_month_the_page_does_not_carry_is_still_refused(writer) -> None:
    """The month fix must stay honest: a month the page's own date does not
    name is still an invented date, not a coarser restatement of one.
    """
    finding = _checked("https://utilitydive.com/news/x",
                       "EIA projects this growth could almost double to an addition of 18.2 GW in 2025.",
                       "18.2", "GW", organisation="Utility Dive", kind="forecast", period="2025",
                       target="topic-02-target-01", statement_date="2025-02-24")
    state = _task_state().model_copy(update={"verified_findings": [EIA_2024, finding]})
    task = writer.build_task(state)
    label = _labels(task.registry)["utilitydive.com"]
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(
        text="Utility Dive reported in March 2025 that EIA projected this growth could almost "
             "double to an addition of 18.2 GW in 2025.",
        finding_labels=[label])], sections=[])
    composition = compose_written_report(task, draft)
    assert composition.summary == []
    [refused] = composition.rejected_points
    assert "March" in refused.reason


# --- Gate G3 live refusal 5: a multi-clause "expects...that...will...and ----
# --- that...will..." sentence is hedged clause-by-clause, all at once -------


def test_a_two_clause_expects_that_will_and_that_will_sentence_is_hedged_throughout(writer) -> None:
    """Live G3 refusal 5: 'EIA expects, as reported by Utility Dive in June
    2025, that domestic storage capacity will rise ..., and that battery
    storage in the commercial and industrial sectors will rise ...' bundles
    four figures across two 'will' clauses under one distant 'expects'.
    ``hedge_forecast``'s will/would replacement is a single global
    substitution (``re.sub``, not one match): it hedges every 'will' clause
    in one rewrite, not just the first, so both clauses -- and every figure
    in them -- pick up 'expected' at once. The positive rewrite check must
    accept a rewrite that reaches every clause it touched, not merely the
    first one it finds.
    """
    finding = _checked_multi(
        "https://utilitydive.com/news/y",
        "EIA said domestic storage capacity will rise from about 28 GW to 64.9 GW. Large-scale "
        "battery storage in the commercial and industrial sectors will rise from about 100 MW to "
        "about 300 MW.",
        [("28", "GW", "forecast"), ("64.9", "GW", "forecast"),
         ("100", "MW", "forecast"), ("300", "MW", "forecast")],
        organisation="Utility Dive", target="topic-02-target-01", period="2026", statement_date="2025-06-10",
    )
    state = _task_state().model_copy(update={"verified_findings": [EIA_2024, finding]})
    task = writer.build_task(state)
    label = _labels(task.registry)["utilitydive.com"]
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(
        text="EIA expects, as reported by Utility Dive in June 2025, that domestic storage capacity "
             "will rise from about 28 GW to 64.9 GW, and that battery storage in the commercial and "
             "industrial sectors will rise from about 100 MW to about 300 MW over the same period.",
        finding_labels=[label])], sections=[])
    composition = compose_written_report(task, draft)
    assert composition.rejected_points == []
    [point] = composition.summary
    assert "is expected to rise" in point.text and "are expected to rise" in point.text
    assert "June 2025" in point.text   # the attribution date is kept, checked and cleared, not stripped


# --- fixtures -------------------------------------------------------------


def _output_limit_error() -> ProviderOutputLimitError:
    return ProviderOutputLimitError(
        ProviderResponseTelemetry(
            finish_reason_category="length",
            configured_max_tokens=4096,
            usage=TokenUsage(input_tokens=5, output_tokens=4096),
            request_attempt=1,
        )
    )


def _writer(
    tracker: Tracker,
    completer: ScriptedCompleter,
    tools: list[BaseTool] | None = None,
    **overrides: object,
) -> ReportWriterAgent:
    return ReportWriterAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1",
            agent_name=REPORT_WRITER_NAME,
            max_entries=20,
        ),
        tools=tools or [],
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=0),
        **overrides,
    )


@pytest.fixture
def writer(tracker: Tracker, tmp_path: Path) -> ReportWriterAgent:
    return _writer(tracker, ScriptedCompleter(), synthesizer_tools(tracker, output_root=tmp_path))


@pytest.fixture
def writer_failing(tracker: Tracker, tmp_path: Path) -> ReportWriterAgent:
    return _writer(tracker, ScriptedCompleter(outputs=[
        ProviderResponseError(
            "provider returned an HTTP error", retryable=True, failure_category="http",
            http_status_code=503, failure_origin="sdk",
        ),
    ]), synthesizer_tools(tracker, output_root=tmp_path))


@pytest.fixture
def writer_truncated_then_ok(tracker: Tracker, tmp_path: Path) -> tuple[ReportWriterAgent, ScriptedCompleter]:
    completer = ScriptedCompleter(outputs=[
        _output_limit_error(),
        ReportWriterDraft(executive_summary=[WriterPointDraft(
            text="Generators added 10.4 GW of battery storage in 2024.", finding_labels=["F01"])], sections=[]),
    ])
    return _writer(tracker, completer, synthesizer_tools(tracker, output_root=tmp_path)), completer



