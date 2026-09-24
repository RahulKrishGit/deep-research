"""Spec §6.1-6.2: the Report Writer writes from verified findings only."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from deep_research.agents.identity import finding_fingerprint
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



