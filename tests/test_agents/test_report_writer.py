"""Spec §6.1-6.2, §5.4: the Report Writer writes from verified findings only,
and the wording of every sentence is judged once by the Statement Check
(decision D8), not by code patterns."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report import render_finding_log, render_written_report
from deep_research.agents.report_writer import (
    REPORT_WRITER_NAME,
    ReportWriterAgent,
    ReportWriterDraft,
    WriterPointDraft,
    WriterSectionDraft,
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
async def test_a_point_over_the_character_limit_is_refused_whole_not_cut(writer, checker) -> None:
    """A too-long point is refused, never cut; ``RejectedDraftPoint.text``
    carries the whole whitespace-collapsed draft, and never reaches the
    Statement Check (the length limit is still code's own job, §6.2).
    """
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["eia.gov"]
    drafted = "Generators added 10.4 GW of battery storage in 2024. " * 15
    collapsed = " ".join(drafted.split())
    assert len(collapsed) > 600
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


@pytest.mark.asyncio
async def test_the_eight_live_g3_sentences_are_kept_when_the_checker_says_consistent(writer, checker) -> None:
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
    checker.verdicts = {f"S{n:03d}": _verdict("consistent") for n in range(1, len(sentences) + 1)}
    composition = await compose_written_report(task, draft, provider=writer.provider, fingerprint=writer.fingerprint_call)
    assert composition.rejected_points == []
    [section] = composition.sections
    assert [p.text for p in section.points] == [text for text, _ in sentences]
    assert len(checker.calls[0]) == len(sentences)   # one batch call, every candidate together


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
    """Monkeypatched over ``evidence_verifier.check_statements`` (D8):
    ``evidence_verifier.py`` does not define the real one in this tree yet
    (T2_1 lands it separately), so this is what the contract's own guidance
    calls for -- code against the contract with a fake, monkeypatched in
    tests. Every candidate defaults to "consistent" unless ``verdicts``
    names its label explicitly.
    """

    def __init__(self) -> None:
        self.verdicts: dict[str, _FakeVerdict] = {}
        self.errors: list = []
        self.calls: list[list[_FakeStatementCheckItem]] = []

    async def __call__(self, provider, items, *, question, fingerprint=None):
        del provider, question
        batch = list(items)
        self.calls.append(batch)
        if fingerprint is not None:
            fingerprint("StatementCheckDraft")
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
