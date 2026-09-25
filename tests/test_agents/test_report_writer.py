"""Spec §6.1-6.2, §5.4: the Report Writer writes from verified findings only,
and the wording of every sentence is judged once by the Statement Check
(decision D8), not by code patterns."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from deep_research.agents.evidence_verifier import StatementCheckDraft, StatementVerdictDraft
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report import render_finding_log, render_written_report
from deep_research.agents.report_writer import (
    MAX_POINT_CHARS,
    REPORT_WRITER_INSTRUCTION,
    REPORT_WRITER_NAME,
    ReportWriterAgent,
    ReportWriterDraft,
    WriterPointDraft,
    WriterSectionDraft,
    compose_written_report,
    evidence_report_filename,
    finding_registry,
    quality_report_filename,
    registry_lines,
    report_filename,
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
    SubTopic,
)
from tests.agent_fakes import ScriptedCompleter
from tests.evidence_fakes import figure, make_finding, make_read, make_target
from tests.research_fakes import report_writer_tools

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


_LONG_SENTENCES = (
    "The agency's own release records the capacity added in the period it states, and it prints "
    "the figure beside the sample its laboratory measured for the market the release covers.",
    "The same release names the series the laboratory updates each year, and it states the period "
    "that series belongs to in the edition the agency published after the update.",
    "A second page in the same release repeats the measurement for the market the page covers, and "
    "it names the sample the laboratory recorded for the series the page updates.",
    "The release's own table lists the periods it covers and the market each of them belongs to, "
    "beside the series the laboratory measures for that market in every edition it publishes.",
    "The agency states the basis of that measurement on every page of the release, and it names "
    "the laboratory that recorded the sample the figure rests on for the market it covers.",
)


@pytest.mark.asyncio
async def test_an_oversize_point_is_split_at_a_sentence_boundary_keeping_its_citations(
    writer, checker
) -> None:
    """Improvement 2: an over-length drafted point is split, never silently dropped.

    The live run lost a whole verified obligation list to the length bound: the
    point was drafted, the bound refused it whole, and the reader never saw it.
    The split is verbatim -- the pieces joined are the drafted point -- so no
    word or citation is invented, and each piece is judged on its own.
    """
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["eia.gov"]
    drafted = " ".join(_LONG_SENTENCES)
    assert len(drafted) > MAX_POINT_CHARS

    draft = ReportWriterDraft(sections=[WriterSectionDraft(title="Basis", points=[
        WriterPointDraft(text=drafted, finding_labels=[label]),
    ])])
    checker.verdicts = {"S001": _verdict("consistent"), "S002": _verdict("consistent")}
    composition = await compose_written_report(task, draft, provider=writer.provider,
                                               fingerprint=writer.fingerprint_call)

    points = composition.sections[0].points
    assert len(points) > 1
    assert " ".join(point.text for point in points) == drafted
    assert all(len(point.text) <= MAX_POINT_CHARS for point in points)
    assert all(point.statement.finding_ids == [finding_fingerprint(EIA_2024)] for point in points)
    assert composition.rejected_points == []


@pytest.mark.asyncio
async def test_a_drafted_point_with_no_boundary_to_split_on_is_still_refused(writer, checker) -> None:
    """One clause over the bound cannot be split honestly, so it is refused as before."""
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["eia.gov"]
    drafted = "The agency's own release records the capacity " + "and the sample it measured " * 30
    assert len(drafted) > MAX_POINT_CHARS
    assert re.search(r"[.;!?]\s+\S", drafted) is None  # no boundary the splitter could cut on

    draft = ReportWriterDraft(sections=[WriterSectionDraft(title="Basis", points=[
        WriterPointDraft(text=drafted, finding_labels=[label]),
    ])])
    composition = await compose_written_report(task, draft, provider=writer.provider,
                                               fingerprint=writer.fingerprint_call)

    assert composition.sections == []
    assert [(r.where, r.reason) for r in composition.rejected_points] == [
        ("sections[0].points[0]", f"longer than {MAX_POINT_CHARS} characters"),
    ]


def test_the_writer_instruction_states_the_point_length_bound() -> None:
    """Improvement 2: the model is told the bound it is judged by, not left to discover it."""
    assert f"under {MAX_POINT_CHARS} characters" in REPORT_WRITER_INSTRUCTION


def test_the_writer_rules_keep_titles_in_the_cited_words_and_metadata_out_of_the_prose() -> None:
    """Improvement 11: an own-voice heading ("pending") and a page's own disclaimer are not the report.

    The live run printed a "pending amendments" heading no cited page supported
    and two filler bullets -- a document's own entry-into-force line and a site
    disclaimer.
    """
    assert "A section title names its subject in the cited findings' own words" in REPORT_WRITER_INSTRUCTION
    assert "never states the report's own judgement or status" in REPORT_WRITER_INSTRUCTION
    assert "Never print a page's own metadata or disclaimer as a point" in REPORT_WRITER_INSTRUCTION


def test_the_writer_rules_require_additive_sections_and_a_criterion_for_a_judgement() -> None:
    """Two reader-facing rules the ev-1 audit's A6 and A2/A3 findings earned.

    (a) that pass printed "Scope of the findings" and "Attribution" sections
    that only restated the summary; (b) its headline sentence kept the page's
    judgement ("pretty much unbeatable") while dropping the metric that
    measured it ("sound-per-pound value"), so the judgement read as a
    sound-quality ranking it was not. Both rules are general: they name no
    question, organisation or figure.
    """
    assert ("Every section adds something the executive summary does not carry: "
            "never write a section that only lists, restates or re-attributes "
            "the findings." in REPORT_WRITER_INSTRUCTION)
    assert ("Never state a judgement while dropping the criterion it is measured by: a "
            "judgement the finding measures by a criterion the snippet does not name is "
            "not an answer, so state the criterion with it or leave the judgement out."
            in REPORT_WRITER_INSTRUCTION)


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


# --- D8: code keeps only two mechanical rules; the checker judges wording ---


@pytest.mark.asyncio
async def test_a_point_citing_no_known_label_is_refused_without_calling_the_checker(writer, checker) -> None:
    task = writer.build_task(_task_state())
    draft = ReportWriterDraft(executive_summary=[
        WriterPointDraft(text="Generators added 10.4 GW of battery storage in 2024.", finding_labels=[]),
    ], sections=[])
    composition = await compose_written_report(task, draft, provider=writer.provider, fingerprint=writer.fingerprint_call)
    assert composition.summary == []
    [refused] = composition.rejected_points
    assert refused.reason == "cites no checked finding"
    assert checker.calls == []   # the mechanical rule never reaches the LLM


@pytest.mark.asyncio
async def test_an_unknown_label_is_refused_without_calling_the_checker(writer, checker) -> None:
    task = writer.build_task(_task_state())
    draft = ReportWriterDraft(executive_summary=[
        WriterPointDraft(text="Generators added 10.4 GW of battery storage in 2024.", finding_labels=["F99"]),
    ], sections=[])
    composition = await compose_written_report(task, draft, provider=writer.provider, fingerprint=writer.fingerprint_call)
    assert composition.summary == []
    [refused] = composition.rejected_points
    assert refused.reason == "unknown labels: F99"
    assert checker.calls == []


@pytest.mark.asyncio
async def test_a_point_whose_own_clause_is_over_the_limit_is_refused_whole(writer, checker) -> None:
    """A too-long point with no boundary to cut on is refused, never cut mid-sentence.

    Improvement 2 splits an over-length point at a sentence boundary; a point
    that is one clause already over the bound has no honest cut left inside it
    (prose is the model's to write), so it is refused whole.
    ``RejectedDraftPoint.text`` carries the whole whitespace-collapsed draft, and
    it never reaches the Statement Check (the length limit is still code's own
    job, §6.2).
    """
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["eia.gov"]
    drafted = "Generators added 10.4 GW of battery storage in 2024 " + "and the agency states the sample " * 20
    collapsed = " ".join(drafted.split())
    assert len(collapsed) > 600
    assert re.search(r"[.;!?]\s+\S", collapsed) is None  # no boundary the splitter could cut on
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(text=drafted, finding_labels=[label])], sections=[])
    composition = await compose_written_report(task, draft, provider=writer.provider, fingerprint=writer.fingerprint_call)
    assert composition.summary == []
    [refused] = composition.rejected_points
    assert refused.reason == "longer than 600 characters"
    assert refused.text == collapsed
    assert checker.calls == []


@pytest.mark.asyncio
async def test_a_consistent_verdict_keeps_the_sentence_unchanged(writer, checker) -> None:
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["eia.gov"]
    drafted = "Generators added 10.4 GW of battery storage in 2024."
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(text=drafted, finding_labels=[label])], sections=[])
    checker.verdicts = {"S001": _verdict("consistent")}
    composition = await compose_written_report(task, draft, provider=writer.provider, fingerprint=writer.fingerprint_call)
    assert composition.rejected_points == []
    [point] = composition.summary
    assert point.text == drafted
    assert point.statement.statement_id == "S001"


@pytest.mark.asyncio
async def test_a_corrected_verdict_replaces_the_sentence_with_corrected_text(writer, checker) -> None:
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["eia.gov"]
    drafted = "Generators added 10 GW of battery storage in 2024."
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(text=drafted, finding_labels=[label])], sections=[])
    checker.verdicts = {"S001": _verdict(
        "corrected", corrected_text="Generators added 10.4 GW of battery storage in 2024.",
        reason="the finding states 10.4 GW, not 10",
    )}
    composition = await compose_written_report(task, draft, provider=writer.provider, fingerprint=writer.fingerprint_call)
    assert composition.rejected_points == []
    [point] = composition.summary
    assert point.text == "Generators added 10.4 GW of battery storage in 2024."


@pytest.mark.asyncio
async def test_a_corrected_verdict_over_the_character_limit_is_refused_with_the_drafted_text(writer, checker) -> None:
    """§6.2's length limit is one of the two mechanical rules code keeps, and
    a model-authored correction is what reaches the reader: a collapsed
    correction over ``MAX_POINT_CHARS`` is refused whole, publishing the
    drafted text -- never the over-long rewrite, and never a cut-off one.
    """
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["eia.gov"]
    drafted = "Generators added 10 GW of battery storage in 2024."
    over_long = "Generators added 10.4 GW of new battery storage capacity in 2024. " * 12
    assert len(" ".join(over_long.split())) > MAX_POINT_CHARS
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(text=drafted, finding_labels=[label])], sections=[])
    checker.verdicts = {"S001": _verdict(
        "corrected", corrected_text=over_long, reason="the finding states 10.4 GW, not 10")}
    composition = await compose_written_report(task, draft, provider=writer.provider, fingerprint=writer.fingerprint_call)
    assert composition.summary == []
    [refused] = composition.rejected_points
    assert refused.text == drafted
    assert refused.reason == f"corrected text longer than {MAX_POINT_CHARS} characters"


@pytest.mark.asyncio
async def test_a_corrected_verdict_at_the_character_limit_is_still_applied(writer, checker) -> None:
    """The other side of the same limit: a correction of exactly
    ``MAX_POINT_CHARS`` collapsed characters is applied, because the rule
    refuses only what exceeds it -- and it measures the collapsed text, not
    the reply's own spacing.
    """
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["eia.gov"]
    drafted = "Generators added 10 GW of battery storage in 2024."
    base = "Generators added 10.4 GW of new battery storage capacity in 2024."
    correction = " ".join([base] * 10)[:MAX_POINT_CHARS]
    assert len(correction) == MAX_POINT_CHARS and correction == " ".join(correction.split())
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(text=drafted, finding_labels=[label])], sections=[])
    checker.verdicts = {"S001": _verdict(
        "corrected", corrected_text=f"  {correction}  ", reason="the finding states 10.4 GW, not 10")}
    composition = await compose_written_report(task, draft, provider=writer.provider, fingerprint=writer.fingerprint_call)
    assert composition.rejected_points == []
    [point] = composition.summary
    assert point.text == correction


@pytest.mark.asyncio
async def test_a_corrected_verdict_with_blank_corrected_text_is_treated_as_inconsistent(writer, checker) -> None:
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["eia.gov"]
    drafted = "Generators added 10 GW of battery storage in 2024."
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(text=drafted, finding_labels=[label])], sections=[])
    checker.verdicts = {"S001": _verdict("corrected", corrected_text="   ", reason="no safe rewording exists")}
    composition = await compose_written_report(task, draft, provider=writer.provider, fingerprint=writer.fingerprint_call)
    assert composition.summary == []
    [refused] = composition.rejected_points
    assert refused.reason == "no safe rewording exists"
    assert refused.text == drafted


@pytest.mark.asyncio
async def test_an_inconsistent_verdict_refuses_with_its_reason(writer, checker) -> None:
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["eia.gov"]
    drafted = "Generators added 12 GW of battery storage in 2024."
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(text=drafted, finding_labels=[label])], sections=[])
    checker.verdicts = {"S001": _verdict("inconsistent", reason="the finding states 10.4 GW, not 12 GW")}
    composition = await compose_written_report(task, draft, provider=writer.provider, fingerprint=writer.fingerprint_call)
    assert composition.summary == []
    [refused] = composition.rejected_points
    assert refused.reason == "the finding states 10.4 GW, not 12 GW"
    assert refused.text == drafted


@pytest.mark.asyncio
async def test_a_failed_batch_keeps_the_sentence_and_records_the_error(writer, checker) -> None:
    """§5.4: a failed batch keeps its sentences, carrying the code-built
    label; the run is never stopped, and the error is recorded."""
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["eia.gov"]
    drafted = "Generators added 10.4 GW of battery storage in 2024."
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(text=drafted, finding_labels=[label])], sections=[])
    checker.verdicts = {}   # "S001" missing: its batch failed
    checker.errors = [_error("evidence_verifier_statement_check_failed", "batch 1 failed")]
    composition = await compose_written_report(task, draft, provider=writer.provider, fingerprint=writer.fingerprint_call)
    assert composition.rejected_points == []
    [point] = composition.summary
    assert point.text == drafted
    assert [e.error_type for e in composition.errors] == ["evidence_verifier_statement_check_failed"]


@pytest.mark.asyncio
async def test_every_kept_sentence_records_its_statement_check_verdict(writer, checker) -> None:
    """Spec §6.4 (Task 4.3): ``statement_verdicts`` carries the check's own
    outcome for every sentence this pass kept -- "consistent", or "corrected"
    when the correction is the wording the reader gets. A refused sentence is
    not kept and has no entry, which is what keeps the map a record of the
    report rather than of the draft."""
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["eia.gov"]
    consistent = "Generators added 10.4 GW of battery storage in 2024."
    corrected = "Generators added 10 GW of battery storage in 2024."
    refused = "Generators added 12 GW of battery storage in 2024."
    draft = ReportWriterDraft(
        executive_summary=[WriterPointDraft(text=consistent, finding_labels=[label])],
        sections=[WriterSectionDraft(title="Detail", points=[
            WriterPointDraft(text=corrected, finding_labels=[label]),
            WriterPointDraft(text=refused, finding_labels=[label]),
        ])],
    )
    checker.verdicts = {
        "S001": _verdict("consistent"),
        "S002": _verdict("corrected", corrected_text=consistent,
                         reason="the finding states 10.4 GW, not 10"),
        "S003": _verdict("inconsistent", reason="the finding states 10.4 GW, not 12 GW"),
    }
    composition = await compose_written_report(task, draft, provider=writer.provider, fingerprint=writer.fingerprint_call)
    assert composition.statement_verdicts == {"S001": "consistent", "S002": "corrected"}
    assert [p.statement.statement_id for p in composition.sections[0].points] == ["S002"]
    assert [r.where for r in composition.rejected_points] == ["sections[0].points[1]"]


@pytest.mark.asyncio
async def test_a_sentence_kept_after_a_failed_batch_records_an_unchecked_verdict(writer, checker) -> None:
    """Task 4.3, §6.4: a sentence whose batch failed is kept and its
    "unchecked" outcome is recorded, which is what tells the quality gate its
    missing verdict is accounted for rather than unjudged."""
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["eia.gov"]
    drafted = "Generators added 10.4 GW of battery storage in 2024."
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(text=drafted, finding_labels=[label])], sections=[])
    checker.verdicts = {}
    checker.errors = [_error("evidence_verifier_statement_check_failed", "batch 1 failed")]
    composition = await compose_written_report(task, draft, provider=writer.provider, fingerprint=writer.fingerprint_call)
    assert composition.statement_verdicts == {"S001": "unchecked"}


@pytest.mark.asyncio
async def test_a_point_in_a_dropped_section_records_no_verdict(writer, checker) -> None:
    """Task 4.3 review P3: a section with a blank title is dropped whole, so
    its points never reach the reader. ``statement_verdicts`` is a record of
    the report's own sentences, so a point that was finalized but never
    printed does not appear in it."""
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["eia.gov"]
    drafted = "Generators added 10.4 GW of battery storage in 2024."
    draft = ReportWriterDraft(
        executive_summary=[WriterPointDraft(text=drafted, finding_labels=[label])],
        sections=[WriterSectionDraft(title="   ", points=[
            WriterPointDraft(text=drafted, finding_labels=[label]),
        ])],
    )
    checker.verdicts = {"S001": _verdict("consistent")}
    composition = await compose_written_report(task, draft, provider=writer.provider, fingerprint=writer.fingerprint_call)
    assert composition.sections == []
    assert composition.statement_verdicts == {"S001": "consistent"}


@pytest.mark.asyncio
async def test_a_summary_restatement_is_refused(writer, checker) -> None:
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["eia.gov"]
    draft = ReportWriterDraft(executive_summary=[
        WriterPointDraft(text="Generators added 10.4 GW of battery storage in 2024.", finding_labels=[label]),
        WriterPointDraft(text="In 2024, 10.4 GW of battery storage was added.", finding_labels=[label]),
    ], sections=[])
    composition = await compose_written_report(task, draft, provider=writer.provider, fingerprint=writer.fingerprint_call)
    assert [p.statement.statement_id for p in composition.summary] == ["S001"]
    assert [r.where for r in composition.rejected_points] == ["summary[1]"]
    assert composition.rejected_points[0].reason.startswith("restates ")


@pytest.mark.asyncio
async def test_the_checker_receives_each_candidates_cited_findings_and_code_built_labels(writer, checker) -> None:
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["eia.gov"]
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(
        text="Generators added 10.4 GW of battery storage in 2024.", finding_labels=[label])], sections=[])
    await compose_written_report(task, draft, provider=writer.provider, fingerprint=writer.fingerprint_call)
    [batch] = checker.calls
    [item] = batch
    assert item.label == "S001"
    assert item.text == "Generators added 10.4 GW of battery storage in 2024."
    assert item.findings == [EIA_2024]
    assert item.labels == [f"{EIA}'s own figure; actual; released 2025-03-12"]


@pytest.mark.asyncio
async def test_every_kept_sentence_still_ends_with_its_figures_code_built_label(writer, checker) -> None:
    """§6.2: the Context Check has already verified each figure's fields,
    and code attaches those as the reader label on every figure -- the
    label is rendered from the fact rows regardless of what the Statement
    Check judged, so it carries the verified provenance whatever the
    sentence's wording ends up being.
    """
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["eia.gov"]
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(
        text="Generators added 10.4 GW of battery storage in 2024.", finding_labels=[label])], sections=[])
    composition = await compose_written_report(task, draft, provider=writer.provider, fingerprint=writer.fingerprint_call)
    rendered = render_written_report(composition)
    line = next(l for l in rendered.splitlines() if l.startswith("- Generators added"))
    assert line.rstrip().endswith(f"*{EIA}'s own figure; actual; released 2025-03-12*")


@pytest.mark.asyncio
async def test_a_failed_draft_still_composes_the_key_facts(writer_failing: ReportWriterAgent, tracker: Tracker, checker) -> None:
    async with tracker.session_span("session-1", "question"):
        run = await writer_failing.run(_task_state())
    assert "## Key facts" in run.result.markdown and "10.4 GW" in run.result.markdown
    assert any(e.error_type == "report_writer_provider_error" for e in run.errors)
    assert checker.calls == []   # nothing was drafted, so nothing reached the checker


@pytest.mark.asyncio
async def test_a_truncated_draft_is_asked_once_more_at_high_effort(writer_truncated_then_ok, tracker: Tracker, checker) -> None:
    agent, completer = writer_truncated_then_ok
    async with tracker.session_span("session-1", "question"):
        run = await agent.run(_task_state())
    assert [name for name, _, _ in completer.calls] == ["ReportWriterDraft", "ReportWriterDraft"]
    assert completer.efforts == [None, "high"] and run.result.statement_count >= 1   # profile effort, then high (F10)


# --- R1: dropped and duplicate findings still reach the evidence log ----------


@pytest.mark.asyncio
async def test_dropped_and_duplicate_findings_still_reach_the_composition(writer, checker) -> None:
    """R1: a dropped duplicate of a citable finding must still be in
    ``ReportComposition.findings``, so the evidence log lists it (spec 5.3).
    """
    dropped = EIA_2024.model_copy(update={
        "verification": FindingVerification(status="dropped", dropped_reason="all_figures_dropped"),
    })
    state = _task_state().model_copy(update={"verified_findings": [EIA_2024, STEO, WOODMAC_ALL, dropped]})
    task = writer.build_task(state)
    composition = await compose_written_report(task, None, provider=writer.provider, fingerprint=writer.fingerprint_call)
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


# --- D8: the eight live G3/round-2 sentences the code checks used to refuse -


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
UTILITY_DIVE_FEB = _checked(
    "https://utilitydive.com/news/x",
    "EIA projects this growth could almost double to an addition of 18.2 GW in 2025.",
    "18.2", "GW", organisation="Utility Dive", kind="forecast", period="2025",
    target="topic-02-target-01", statement_date="2025-02-24",
)
UTILITY_DIVE_JUNE = _checked_multi(
    "https://utilitydive.com/news/y",
    "EIA said domestic storage capacity will rise from about 28 GW to 64.9 GW. Large-scale "
    "battery storage in the commercial and industrial sectors will rise from about 100 MW to "
    "about 300 MW.",
    [("28", "GW", "forecast"), ("64.9", "GW", "forecast"),
     ("100", "MW", "forecast"), ("300", "MW", "forecast")],
    organisation="Utility Dive", target="topic-02-target-01", period="2026", statement_date="2025-06-10",
)
ENERKNOL = _checked(
    "https://enerknol.com/news/z",
    "EnerKnol reported that battery storage accounts for two percent of total U.S. power capacity.",
    "2", "%", organisation="EnerKnol", period="2025", target="topic-04-target-01", statement_date="2025-03-13",
)


def _confirm_statement_reply(messages: list, schema: type) -> StatementCheckDraft:
    """Answer every statement in the request with a plain 'consistent' verdict.

    Reads the batch's own labels back out of the request body, so it answers
    correctly whichever batch the real ``check_statements`` hands it.
    """
    del schema
    return StatementCheckDraft(statements=[
        StatementVerdictDraft(label=label, verdict="consistent", reason="Matches the findings.")
        for label in re.findall(r"## (S\d+)", messages[1].content)
    ])


@pytest.mark.asyncio
async def test_the_eight_live_g3_sentences_are_kept_when_the_checker_says_consistent(writer) -> None:
    """The five Gate G3 live refusals and the three round-2 refusals were
    all real, honest sentences the old code-pattern checks (clause
    governance, name attestation, bare month-year attestation) wrongly
    refused. Under D8, code no longer judges wording at all: every one of
    them reaches the Statement Check and is kept exactly as drafted when
    the checker says consistent.

    Three of the eight (1, 5, 8) restate the same STEO 47%/14 GW figures,
    and two (3, 7) restate the same Utility Dive 18.2 GW figure -- each was
    a separate live incident in a separate report, never drafted together.
    Placed in one section rather than the summary, so the unrelated (and
    unchanged) same-figure restatement guard -- summary-only by design --
    is not what this test is exercising.
    """
    state = _task_state().model_copy(update={"verified_findings": [
        EIA_2024, STEO_PAREN, WOODMAC_Q1, UTILITY_DIVE_FEB, UTILITY_DIVE_JUNE, ENERKNOL,
    ]})
    task = writer.build_task(state)
    labels = _labels(task.registry)
    steo_label, woodmac_label = labels["ent.news"], labels["woodmac.com"]
    utility_label, enerknol_label = labels["utilitydive.com"], labels["enerknol.com"]
    sentences = [
        # Live G3 refusal 1: a parenthetical restatement of the governing figure.
        ("In its January 2025 forecast, as reported by ent.news, EIA projected battery storage "
         "capacity growing by 47% (14 GW) in 2025.", [steo_label]),
        # Live G3 refusal 2: a compound-unit figure sharing one governing clause.
        ("Wood Mackenzie's Q1 2025 forecast projects 15 GW/49 GWh of energy storage capacity "
         "across all segments in 2025.", [woodmac_label]),
        # Live G3 refusal 4: a bare month and year restating the page's own date.
        ("Utility Dive reported in February 2025 that EIA projected this growth could almost "
         "double to an addition of 18.2 GW in 2025.", [utility_label]),
        # Live G3 refusal 5: a two-clause "will ... and ... will ..." forecast.
        ("EIA expects, as reported by Utility Dive in June 2025, that domestic storage capacity "
         "will rise from about 28 GW to 64.9 GW, and that battery storage in the commercial and "
         "industrial sectors will rise from about 100 MW to about 300 MW over the same period.",
         [utility_label]),
        # Round 2 refusal: an appositive restatement, "47%, or 14 GW,".
        ("For 2025, the U.S. Energy Information Administration forecast battery storage capacity "
         "growing by 47%, or 14 GW, in its January 2025 Short-Term Energy Outlook as reported by "
         "ent.news.", [steo_label]),
        # Round 2 refusal: a host name the old check misread as an unattested name.
        ("EnerKnol reported that battery storage accounts for two percent of total U.S. power "
         "capacity.", [enerknol_label]),
        # Round 2 refusal: the full organisation name where only "EIA" appears nearby.
        ("Utility Dive reported that the U.S. Energy Information Administration projected this "
         "growth could almost double to an addition of 18.2 GW in 2025.", [utility_label]),
        # A realised-outcome verb next to a forecast figure, the honesty-regression fixture.
        ("EIA projected growth of 47% (14 GW was installed) in 2025.", [steo_label]),
    ]
    draft = ReportWriterDraft(
        executive_summary=[],
        sections=[WriterSectionDraft(
            title="Live refusals",
            points=[WriterPointDraft(text=text, finding_labels=labels_) for text, labels_ in sentences],
        )],
    )
    # The real ``check_statements`` (not a stand-in) answers these: it batches
    # at ``CONTEXT_CHECK_BATCH_SIZE`` (5), so the eight candidates go out as
    # two provider calls, five then three, and every candidate is checked
    # exactly once. A single call over all eight would be a batching
    # regression the writer's own fake could never show.
    completer = ScriptedCompleter(outputs=[_confirm_statement_reply] * 2)
    composition = await compose_written_report(task, draft, provider=completer, fingerprint=writer.fingerprint_call)
    assert composition.rejected_points == []
    [section] = composition.sections
    assert [p.text for p in section.points] == [text for text, _ in sentences]
    batches = [re.findall(r"## (S\d+)", messages[-1].content) for schema, _, messages in completer.calls]
    assert [len(batch) for batch in batches] == [5, 3]
    assert sorted(label for batch in batches for label in batch) == [f"S{n:03d}" for n in range(1, len(sentences) + 1)]


# --- fixtures -------------------------------------------------------------


@dataclass
class _FakeStatementCheckItem:
    """Mirrors ``evidence_verifier.StatementCheckItem`` (D8) by field name
    only; constructed by ``compose_written_report`` and read by the fake
    checker below, never by ``isinstance``."""

    label: str
    text: str
    findings: list = field(default_factory=list)
    labels: list = field(default_factory=list)
    passages: dict = field(default_factory=dict)


@dataclass
class _FakeVerdict:
    label: str
    verdict: str
    corrected_text: str = ""
    reason: str = "consistent with its findings"


def _verdict(verdict: str, *, corrected_text: str = "", reason: str | None = None) -> _FakeVerdict:
    return _FakeVerdict(label="", verdict=verdict, corrected_text=corrected_text,
                        reason=reason or "consistent with its findings")


def _error(error_type: str, message: str):
    from deep_research.agents.errors import agent_error
    return agent_error(agent_name="evidence_verifier", error_type=error_type, message=message)


class _FakeChecker:
    """Monkeypatched over ``evidence_verifier.check_statements`` (D8) to script
    one verdict per candidate label: the fake reads the batch it is handed and
    answers from ``verdicts``, so a test can drive one verdict per key without
    a provider call. The deferred import inside ``compose_written_report`` is
    what makes this substitution visible at all, and the real checker's own
    batching is what the eight-sentence test above exercises instead. Every
    candidate defaults to "consistent" unless ``verdicts`` names its label
    explicitly.
    """

    def __init__(self) -> None:
        self.verdicts: dict[str, _FakeVerdict] = {}
        self.errors: list = []
        self.calls: list[list[_FakeStatementCheckItem]] = []
        self.bounds: list[tuple[int | None, int | None]] = []

    async def __call__(
        self, provider, items, *, question, fingerprint=None,
        batch_size=None, concurrency=None,
    ):
        # The two bounds are part of the call the real checker accepts (PD-12):
        # a stand-in that refused them would fail on a signature the call site
        # is required to use. This fake scripts one verdict per label and does
        # not batch, so it records them and answers from ``verdicts``.
        del provider, question, fingerprint
        self.bounds.append((batch_size, concurrency))
        batch = list(items)
        self.calls.append(batch)
        result = {}
        for item in batch:
            verdict = self.verdicts.get(item.label)
            if verdict is not None:
                result[item.label] = _FakeVerdict(label=item.label, verdict=verdict.verdict,
                                                   corrected_text=verdict.corrected_text, reason=verdict.reason)
        return result, list(self.errors)


@pytest.fixture
def checker(monkeypatch) -> _FakeChecker:
    fake = _FakeChecker()
    monkeypatch.setattr("deep_research.agents.evidence_verifier.StatementCheckItem",
                        _FakeStatementCheckItem, raising=False)
    monkeypatch.setattr("deep_research.agents.evidence_verifier.check_statements", fake, raising=False)
    return fake


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
    return _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))


@pytest.fixture
def writer_failing(tracker: Tracker, tmp_path: Path) -> ReportWriterAgent:
    return _writer(tracker, ScriptedCompleter(outputs=[
        ProviderResponseError(
            "provider returned an HTTP error", retryable=True, failure_category="http",
            http_status_code=503, failure_origin="sdk",
        ),
    ]), report_writer_tools(tracker, output_root=tmp_path))


@pytest.fixture
def writer_truncated_then_ok(tracker: Tracker, tmp_path: Path) -> tuple[ReportWriterAgent, ScriptedCompleter]:
    completer = ScriptedCompleter(outputs=[
        _output_limit_error(),
        ReportWriterDraft(executive_summary=[WriterPointDraft(
            text="Generators added 10.4 GW of battery storage in 2024.", finding_labels=["F01"])], sections=[]),
    ])
    return _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path)), completer


@pytest.mark.parametrize(
    ("session_id", "iteration", "expected"),
    [
        ("session-1", 0, "report-session-1-0.md"),
        ("Session_42", 2, "report-session-42-2.md"),
        ("../../etc/passwd", 1, "report-etc-passwd-1.md"),
        ("   ", 0, "report-session-0.md"),
    ],
)
def test_report_filenames_are_slugged_and_traversal_free(
    session_id: str, iteration: int, expected: str
) -> None:
    assert report_filename(session_id=session_id, iteration=iteration) == expected


def test_report_filename_rejects_a_negative_iteration() -> None:
    with pytest.raises(ValueError, match="iteration"):
        report_filename(session_id="session-1", iteration=-1)


def test_the_evidence_filename_derives_from_the_reader_report() -> None:
    assert (
        evidence_report_filename(session_id="Session_42", iteration=2)
        == "report-session-42-2-evidence.md"
    )
    with pytest.raises(ValueError, match="iteration"):
        evidence_report_filename(session_id="session-1", iteration=-1)


def test_the_quality_filename_derives_from_the_reader_report() -> None:
    assert (
        quality_report_filename(session_id="Session_42", iteration=2)
        == "report-session-42-2-quality.json"
    )
    with pytest.raises(ValueError, match="iteration"):
        quality_report_filename(session_id="session-1", iteration=-1)


def test_a_finding_with_no_figure_is_listed_with_its_attribution_and_its_role() -> None:
    """D10 gap 4: the writer sees whose statement a prose finding is, and whether it forecasts."""
    text = "The Example Institute forecasts that rents will keep rising next year."
    finding = make_finding(
        make_read(text, url="https://gazette.example.test/rents", title="Rents"), text,
        attributed_issuer="Example Institute",
        attribution_quote="The Example Institute forecasts",
    ).model_copy(update={"verification": FindingVerification(status="verified")})
    assert registry_lines("F07", finding) == [
        "## F07: Rents (gazette.example.test)",
        f"snippet: {text}",
        "F07 | statement | attributed to Example Institute | forecast",
    ]


# --- D11: subjects and page-dated periods in the registry and the guard ---


def _about(url, text, specs, *, target_ids=("topic-01-target-01",)):
    """A verified finding whose kept figures carry ``specs``: ``(value, unit, context fields)``."""
    figs = [figure(value, unit, "2024", "actual") for value, unit, _ in specs]
    finding = make_finding(make_read(text, url=url, title="Example page"), text, figures=figs,
                           target_ids=list(target_ids))
    base = {"period": "2024", "attribution": "own", "organisation": "Example Test Lab", "kind": "actual"}
    results = [FigureResult(figure=fig, matched=True, evidence_words=text,
                            context=FigureContext(**(base | fields)))
               for fig, (_, _, fields) in zip(figs, specs)]
    return finding.model_copy(update={"verification": FindingVerification(status="verified", figure_results=results)})


def test_a_figure_line_names_its_subject() -> None:
    finding = _about("https://lab.example.test/kettles", "Model B scored 4.5 out of 5 for noise.",
                     [("4.5", "out of 5", {"subject": "Model B"})])
    [line] = [line for line in registry_lines("F01", finding) if "| figure 1:" in line]
    assert "| figure 1: 4.5 out of 5 | subject Model B | period " in line


def test_a_figure_line_labels_a_period_resolved_from_the_page_date() -> None:
    """D11: the writer sees that the year is the page's date, not the page's words."""
    finding = _about("https://operators.example.test/report", "Operators installed 4 GW this year.",
                     [("4", "GW", {"period": "2026", "period_resolved_from": "2026-02-20"})])
    [line] = [line for line in registry_lines("F01", finding) if "| figure 1:" in line]
    assert line.endswith("; period resolved from the page date 2026-02-20")


def test_a_statement_line_ends_with_the_date_the_finding_carries() -> None:
    """The statement's own date first, then its release, then the period its data cover."""
    text = "The Example Institute forecasts that rents will keep rising next year."

    def statement_line(**dates: str) -> str:
        finding = make_finding(
            make_read(text, url="https://gazette.example.test/rents", title="Rents"), text,
            attributed_issuer="Example Institute", **dates,
        ).model_copy(update={"verification": FindingVerification(status="verified")})
        return registry_lines("F07", finding)[-1]

    assert statement_line(statement_date="2025-12-31", release_date="2026-01-15", data_period="2025") == (
        "F07 | statement | attributed to Example Institute | forecast | dated 2025-12-31")
    assert statement_line(release_date="2026-01-15", data_period="2025").endswith(" | dated 2026-01-15")
    assert statement_line(data_period="2025").endswith(" | dated 2025")


def test_a_finding_answering_only_an_optional_sibling_is_not_ranked_as_a_required_answer() -> None:
    """F11: the registry's answers are resolved against the whole plan, then kept for required targets.

    The first finding's figure is about Italy, whose target is optional, yet it
    names the required Spain target too. Only with the optional sibling in view
    does the subject rule refuse it Spain's target; the Spain finding is then
    the one required answer, and it is labelled first.
    """
    spain = make_target("topic-01-target-01", question="How much capacity was added in Spain in 2024?",
                        geography="Spain")
    italy = make_target("topic-02-target-01", question="How much capacity was added in Italy in 2024?",
                        geography="Italy", required=False)
    italian = _about("https://grid.example.test/italy", "Italy added 4 GW of capacity in 2024.",
                     [("4", "GW", {"subject": "Italy"})], target_ids=(spain.target_id, italy.target_id))
    spanish = _about("https://grid.example.test/spain", "Spain added 5 GW of capacity in 2024.",
                     [("5", "GW", {"subject": "Spain"})], target_ids=(spain.target_id,))
    registry = finding_registry([italian, spanish], [spain, italy])
    assert [finding.source_url for _, finding in registry] == [spanish.source_url, italian.source_url]


def _two_subject_state() -> ResearchState:
    """One review page rating two products with the same value (Fable §8.8's two-subjects-one-value)."""
    target = make_target()
    topic = SubTopic(coverage_id=target.coverage_id, title="Ratings", rationale="r", search_queries=["q"],
                     success_criteria=["c"], priority=1, evidence_targets=[target])
    finding = _about("https://lab.example.test/storage",
                     "Model A added 4 GW in 2024, and Model B also added 4 GW in 2024.",
                     [("4", "GW", {"subject": "Model A"}), ("4", "GW", {"subject": "Model B"})])
    return ResearchState(session_id="s", original_question="How much did each model add in 2024?",
                         sub_topics=[topic], verified_findings=[finding])


@pytest.mark.asyncio
async def test_a_summary_point_about_another_subject_is_not_a_restatement(writer, checker) -> None:
    """D11: a restatement is counted only for the subject the sentence names."""
    task = writer.build_task(_two_subject_state())
    assert [row.subject for row in task.facts] == ["Model A", "Model B"]
    [(label, _)] = task.registry
    draft = ReportWriterDraft(executive_summary=[
        WriterPointDraft(text="Model A added 4 GW in 2024.", finding_labels=[label]),
        WriterPointDraft(text="Model B added 4 GW in 2024.", finding_labels=[label]),
        WriterPointDraft(text="In 2024, Model B added 4 GW.", finding_labels=[label]),
    ], sections=[])
    composition = await compose_written_report(task, draft, provider=writer.provider, fingerprint=writer.fingerprint_call)
    assert [point.text for point in composition.summary] == [
        "Model A added 4 GW in 2024.", "Model B added 4 GW in 2024."]
    assert [(r.where, r.reason) for r in composition.rejected_points] == [("summary[2]", "restates K002")]


def _comparison_state(left: str = "Kettle K1", right: str = "Kettle K2") -> ResearchState:
    """One page reporting two named products adding the same capacity (Task 5.6c's comparison target).

    The question names **both** products, which is what strips either subject's
    words from the context the guard tests a sentence against.
    """
    target = make_target(question=(f"How do the {left} and the {right} compare on battery "
                                   "storage capacity added in 2024?"))
    topic = SubTopic(coverage_id=target.coverage_id, title="Capacity", rationale="r", search_queries=["q"],
                     success_criteria=["c"], priority=1, evidence_targets=[target])
    finding = _about("https://grid.example.test/kettles",
                     "The first product added 4 GW of capacity in 2024, and the second 4 GW too.",
                     [("4", "GW", {"subject": left}), ("4", "GW", {"subject": right})])
    return ResearchState(session_id="s", original_question=target.question,
                         sub_topics=[topic], verified_findings=[finding])


@pytest.mark.asyncio
async def test_a_comparison_target_counts_a_restatement_only_for_the_subject_it_names(
    writer, checker
) -> None:
    """Task 5.6c: the guard follows the distinguishing words when the target names both options.

    The question names the Kettle K1 and the Kettle K2, so ``subject_context``
    strips either product's words and the subject test alone counts both rows
    as restated by either sentence: the second sentence is refused and the
    reader loses a product. A sentence counts for a row only when it states what
    tells that row from its rival.
    """
    task = writer.build_task(_comparison_state())
    assert [row.subject for row in task.facts] == ["Kettle K1", "Kettle K2"]
    [(label, _)] = task.registry
    draft = ReportWriterDraft(executive_summary=[
        WriterPointDraft(text="The Kettle K1 added 4 GW of capacity in 2024.", finding_labels=[label]),
        WriterPointDraft(text="The Kettle K2 added 4 GW of capacity in 2024.", finding_labels=[label]),
        WriterPointDraft(text="For 2024, the Kettle K2 added 4 GW.", finding_labels=[label]),
    ], sections=[])
    composition = await compose_written_report(task, draft, provider=writer.provider,
                                               fingerprint=writer.fingerprint_call)
    assert [point.text for point in composition.summary] == [
        "The Kettle K1 added 4 GW of capacity in 2024.",
        "The Kettle K2 added 4 GW of capacity in 2024."]
    assert [(r.where, r.reason) for r in composition.rejected_points] == [("summary[2]", "restates K002")]


@pytest.mark.asyncio
async def test_a_comparison_target_does_not_let_an_article_carry_a_restatement(writer, checker) -> None:
    """Fix round 1 (Important 2): "Model B added a record 4 GW" is not Model A's restatement.

    The give-away between "Model A" and "Model B" is the article "a" alone, and
    any sentence may carry one: matched alone it makes the Model B sentence
    count for Model A's row, and the guard then refuses the genuine Model A
    sentence that follows it.
    """
    task = writer.build_task(_comparison_state("Model A", "Model B"))
    assert [row.subject for row in task.facts] == ["Model A", "Model B"]
    [(label, _)] = task.registry
    draft = ReportWriterDraft(executive_summary=[
        WriterPointDraft(text="Model B added a record 4 GW of capacity in 2024.", finding_labels=[label]),
        WriterPointDraft(text="Model A added 4 GW of capacity in 2024.", finding_labels=[label]),
    ], sections=[])
    composition = await compose_written_report(task, draft, provider=writer.provider,
                                               fingerprint=writer.fingerprint_call)
    assert [point.text for point in composition.summary] == [
        "Model B added a record 4 GW of capacity in 2024.",
        "Model A added 4 GW of capacity in 2024."]
    assert composition.rejected_points == []


def _two_period_state() -> ResearchState:
    """One page stating one value twice: a 2024 actual and a 2025 forecast (PD-9).

    Two periods of one value are two facts, so the page earns two rows whose
    values are equal and whose periods are not.
    """
    target = make_target(measure="sales growth", unit_dimension="percent", period=None, organisation=None)
    topic = SubTopic(coverage_id=target.coverage_id, title="Sales", rationale="r", search_queries=["q"],
                     success_criteria=["c"], priority=1, evidence_targets=[target])
    text = "Sales grew 12 percent in 2024, and the agency expects growth of 12 percent in 2025."
    read = make_read(text, url="https://agency.example.test/outlook", title="Outlook")
    figures = [figure("12", "%", "2024", "actual"), figure("12", "%", "2025", "forecast")]
    finding = make_finding(read, text, figures=figures, target_ids=[target.target_id])
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
    return ResearchState(session_id="s", original_question=target.question,
                         sub_topics=[topic], verified_findings=[finding])


@pytest.mark.asyncio
async def test_two_periods_of_one_value_are_two_facts_not_a_restatement(writer, checker) -> None:
    """PD-9: an actual and a later forecast of the same value are two facts.

    Both sentences cite the one finding, and its value alone matches both rows,
    so the 2024 sentence counted as having stated the 2025 row too and the 2025
    forecast -- a distinct fact -- was refused as "restates K001, K002" and lost
    from the reader's summary.
    """
    task = writer.build_task(_two_period_state())
    assert [(row.row_id, row.period) for row in task.facts] == [("K001", "2024"), ("K002", "2025")]
    [(label, _)] = task.registry
    draft = ReportWriterDraft(executive_summary=[
        WriterPointDraft(text="Sales grew 12 percent in 2024.", finding_labels=[label]),
        WriterPointDraft(text="The agency expects growth of 12 percent in 2025.", finding_labels=[label]),
    ], sections=[])
    checker.verdicts = {"S001": _verdict("consistent"), "S002": _verdict("consistent")}
    composition = await compose_written_report(task, draft, provider=writer.provider,
                                               fingerprint=writer.fingerprint_call)
    assert [point.text for point in composition.summary] == [
        "Sales grew 12 percent in 2024.",
        "The agency expects growth of 12 percent in 2025.",
    ]
    assert composition.rejected_points == []


@pytest.mark.asyncio
async def test_the_statement_check_is_handed_the_cited_findings_bounded_passage(
    writer, checker
) -> None:
    """Improvement 8: the checker judges a snippet cut at its passage's boundary
    against the bounded passage of its own page, which is where the condition or
    exception the draft dropped lives."""
    snippet = "The grant covers travel when the visit is approved"
    page = snippet + " in advance. It does not cover stays longer than five days."
    read = make_read(page, url="https://example.test/grant", title="Example Lab page")
    finding = make_finding(read, snippet, figures=[figure("5", "days", "2025", "actual")],
                           target_ids=["topic-01-target-01"])
    finding = finding.model_copy(update={"verification": FindingVerification(
        status="verified",
        figure_results=[FigureResult(
            figure=finding.figures[0], matched=True, evidence_words=snippet,
            context=FigureContext(period="2025", scope=None, attribution="own",
                                  organisation="Example Lab", kind="actual"))])})
    state = _task_state().model_copy(update={
        "verified_findings": [finding], "read_records": {read.read_id: read}})

    task = writer.build_task(state)
    label = _labels(task.registry)["example.test"]

    assert task.reads == {read.read_id: read}
    # A task with no reads in hand carries no passages: the block is the snippet
    # alone, exactly as before the caller had one to give.
    assert writer.build_task(_task_state()).reads == {}

    draft = ReportWriterDraft(
        executive_summary=[WriterPointDraft(text="The grant covers travel.",
                                            finding_labels=[label])], sections=[])
    await compose_written_report(task, draft, provider=writer.provider, fingerprint=writer.fingerprint_call)

    item = checker.calls[0][0]
    passage = item.passages[finding_fingerprint(finding)]
    assert "It does not cover stays longer than five days." in passage
    assert passage.startswith(snippet)
