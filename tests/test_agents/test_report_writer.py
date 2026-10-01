"""Spec §6: the parallel Report Writer -- one call per plan part, each part's
Statement Check pipelined off its own draft, a bottom line written last from
the checked section statements, and a redraft that re-asks only the parts a
defect names (§6.9). Every mechanical rule §6.4 keeps is judged here; every
other question about a sentence's wording is the Statement Check's job."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report import render_finding_log, render_written_report
from deep_research.agents.report_writer import (
    BOTTOM_LINE_INSTRUCTION,
    BOTTOM_LINE_SYSTEM_PROMPT,
    CONTEXT_ONLY_RELEVANCE,
    MAX_ANSWER_SENTENCES,
    MAX_POINT_CHARS,
    REPORT_WRITER_NAME,
    SECTION_INSTRUCTION,
    SECTION_SYSTEM_PROMPT,
    PartJob,
    ReportWriterAgent,
    ReportWriterTask,
    WrittenReport,
    bottom_line_messages,
    compose_written_report,
    finding_registry,
    is_context_only,
    material_defects,
    registry_lines,
    report_parts,
    section_messages,
    sources_by_url,
    statement_passages,
)
from deep_research.agents.sources import normalize_source_url
from deep_research.graph.live import bind_live_sink
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import TokenUsage, Tracker
from deep_research.providers import ProviderOutputLimitError, ProviderResponseError, ProviderResponseTelemetry
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    AcquisitionState,
    AnswerContract,
    BottomLineDraft,
    CandidateRecord,
    FindingVerification,
    ItemMark,
    ItemMarkDraft,
    ReportSection,
    ReportStatement,
    ResearchEvent,
    ResearchState,
    ReviewDefect,
    ScoredSource,
    SectionDraft,
    SourceTemporal,
    SubTopic,
    WriterPointDraft,
)
from tests.agent_fakes import ScriptedCompleter
from tests.evidence_fakes import figure, make_finding, make_read, make_target
from tests.research_fakes import report_writer_tools


EIA = "U.S. Energy Information Administration"


def _topic(coverage_id: str, title: str, targets: list, priority: int = 1) -> SubTopic:
    return SubTopic(
        coverage_id=coverage_id, title=title, rationale="Why this matters.",
        search_queries=["query"], success_criteria=["criterion"],
        priority=priority, evidence_targets=targets,
    )


def _checked(url, text, value, unit, *, organisation, attribution="own", kind="actual",
             period="2024", target_ids=("topic-01-target-01",), related_sub_topic=None, **fields):
    read = make_read(text, url=url, title=f"{organisation} page")
    finding = make_finding(read, text, figures=[figure(value, unit, period, kind)],
                           target_ids=list(target_ids), **fields)
    if related_sub_topic is not None:
        finding = finding.model_copy(update={"related_sub_topic": related_sub_topic})
    result = FigureResultFor(finding, organisation=organisation, attribution=attribution,
                             kind=kind, period=period)
    return finding.model_copy(update={"verification": FindingVerification(status="verified", figure_results=[result])})


def FigureResultFor(finding, *, organisation, attribution, kind, period):
    from deep_research.utils.types import FigureContext, FigureResult
    return FigureResult(
        figure=finding.figures[0],
        matched=True,
        context=FigureContext(attribution=attribution, organisation=organisation,
                              kind=kind, period=period),
    )


def _statement_finding(url, text, *, target_ids=(), related_sub_topic=None, **fields):
    """A citable, no-figure finding (a plain statement), verified whole."""
    read = make_read(text, url=url, title="A page")
    finding = make_finding(read, text, target_ids=list(target_ids), **fields)
    if related_sub_topic is not None:
        finding = finding.model_copy(update={"related_sub_topic": related_sub_topic})
    return finding.model_copy(update={"verification": FindingVerification(status="verified", figure_results=[])})


def _low_relevance_source(url: str, *, low_confidence: bool = False, relevance: float = 0.2) -> ScoredSource:
    return ScoredSource(
        url=url, title="A page", authority_score=0.8, recency_score=0.8,
        relevance_score=relevance, overall_score=0.5,
        rationale="Weakly related to the sub-topic.", low_confidence=low_confidence,
    )


def _authority_source(url: str, *, authority: float, low_confidence: bool = False,
                      relevance: float = 0.8) -> ScoredSource:
    return ScoredSource(
        url=url, title="A page", authority_score=authority, recency_score=0.8,
        relevance_score=relevance, overall_score=0.5,
        rationale="Scored for authority.", low_confidence=low_confidence,
    )


# --- report_parts: the §6.1 partition -----------------------------------


def test_report_parts_places_a_finding_by_its_explicit_required_binding():
    t1 = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    t2 = make_target("topic-02-target-01", coverage_id="topic-02", required=True,
                     question="What capacity did the EIA report for 2024?")
    finding = _checked("https://a.test/1", "The EIA reported 10.4 GW in 2024.", "10.4", "GW",
                       organisation=EIA, target_ids=["topic-02-target-01"])
    sub_topics = [_topic("topic-01", "First", [t1]), _topic("topic-02", "Second", [t2])]

    parts, unplaced = report_parts([finding], [t1, t2], sub_topics)

    assert [p.coverage_id for p in parts] == ["topic-01", "topic-02"]
    assert parts[0].findings == []
    assert [finding_fingerprint(f) for f in parts[1].findings] == [finding_fingerprint(finding)]
    assert unplaced == []


def test_report_parts_prefers_an_answered_required_target_over_an_earlier_bind_only_target():
    """Category 1 (answers and binds) beats category 5 (binds without answering),
    even when the binds-only target comes first in plan order."""
    bind_only = make_target("topic-01-target-01", coverage_id="topic-01", required=True,
                            unit_dimension="count",
                            question="How many storage projects were cancelled?")
    answered = make_target("topic-02-target-01", coverage_id="topic-02", required=True,
                           question="What capacity did the EIA report for 2024?")
    finding = _checked("https://a.test/1", "The EIA reported 10.4 GW in 2024.", "10.4", "GW",
                       organisation=EIA, target_ids=["topic-01-target-01", "topic-02-target-01"])
    sub_topics = [_topic("topic-01", "First", [bind_only]), _topic("topic-02", "Second", [answered])]

    parts, unplaced = report_parts([finding], [bind_only, answered], sub_topics)

    assert parts[0].findings == []
    assert len(parts[1].findings) == 1
    assert unplaced == []


def test_report_parts_places_a_fallback_answer_by_its_own_sub_topic():
    """Category 2: no explicit binding, but the content states the target's
    own sub-topic's obligation, so the fallback resolution places it there."""
    target = make_target("topic-03-target-01", coverage_id="topic-03", required=True,
                         question="How much battery storage capacity was added in the United States in 2024?")
    read = make_read("Storage capacity grew by 10.4 GW in the United States in 2024.",
                     url="https://a.test/1")
    finding = make_finding(read, "Storage capacity grew by 10.4 GW in the United States in 2024.",
                           figures=[figure("10.4", "GW", "2024", "actual")], target_ids=[])
    finding = finding.model_copy(update={"related_sub_topic": "Battery capacity added"})
    result = FigureResultFor(finding, organisation=EIA, attribution="own", kind="actual", period="2024")
    finding = finding.model_copy(update={"verification": FindingVerification(status="verified", figure_results=[result])})
    sub_topics = [_topic("topic-03", "Battery capacity added", [target])]

    parts, unplaced = report_parts([finding], [target], sub_topics)

    assert len(parts[0].findings) == 1
    assert unplaced == []


def test_report_parts_places_a_finding_that_binds_a_target_without_answering_it():
    """Category 5: the finding names the target but does not actually answer it
    (a qualitative target whose organisation the finding never names)."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True,
                         unit_dimension=None, organisation="A Named Body",
                         question="What does A Named Body say about the rule?")
    finding = _statement_finding("https://a.test/1", "An unrelated page mentions the rule in passing.",
                                 target_ids=["topic-01-target-01"])
    sub_topics = [_topic("topic-01", "Rule", [target])]

    parts, unplaced = report_parts([finding], [target], sub_topics)

    assert len(parts[0].findings) == 1
    assert unplaced == []


def test_report_parts_places_a_finding_by_related_sub_topic_name_alone():
    """Category 6: no binding and no target the finding actually answers, but
    the finding names the sub-topic itself."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True,
                         question="What is the capital of the example country?")
    finding = _statement_finding("https://a.test/1", "This page is about something else entirely.",
                                 target_ids=[], related_sub_topic="Example sub-topic")
    sub_topics = [_topic("topic-01", "Example sub-topic", [target])]

    parts, unplaced = report_parts([finding], [target], sub_topics)

    assert len(parts[0].findings) == 1
    assert unplaced == []


def test_report_parts_leaves_a_wholly_unrelated_finding_unplaced():
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    finding = _statement_finding("https://a.test/1", "This page is about something else entirely.",
                                 target_ids=[], related_sub_topic="A different sub-topic entirely")
    sub_topics = [_topic("topic-01", "Example sub-topic", [target])]

    parts, unplaced = report_parts([finding], [target], sub_topics)

    assert parts[0].findings == []
    assert [finding_fingerprint(f) for f in unplaced] == [finding_fingerprint(finding)]


def test_report_parts_places_every_citable_finding_exactly_once():
    t1 = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    t2 = make_target("topic-02-target-01", coverage_id="topic-02", required=False)
    bound = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW",
                     organisation=EIA, target_ids=["topic-01-target-01"])
    optional = _checked("https://a.test/2", "5.0 GW forecast.", "5.0", "GW",
                        organisation=EIA, target_ids=["topic-02-target-01"], kind="forecast")
    stray = _statement_finding("https://a.test/3", "Unrelated content.", target_ids=[],
                               related_sub_topic="Nowhere")
    sub_topics = [_topic("topic-01", "First", [t1]), _topic("topic-02", "Second", [t2])]
    findings = [bound, optional, stray]

    parts, unplaced = report_parts(findings, [t1, t2], sub_topics)

    placed_ids = [finding_fingerprint(f) for part in parts for f in part.findings]
    assert len(placed_ids) == len(set(placed_ids))
    all_ids = {finding_fingerprint(f) for f in findings}
    assert set(placed_ids) | {finding_fingerprint(f) for f in unplaced} == all_ids
    assert len(placed_ids) + len(unplaced) == len(findings)


def test_report_parts_returns_empty_parts_in_plan_order_for_sub_topics_with_no_findings():
    t1 = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    t2 = make_target("topic-02-target-01", coverage_id="topic-02", required=True)
    sub_topics = [_topic("topic-01", "First", [t1]), _topic("topic-02", "Second", [t2])]

    parts, unplaced = report_parts([], [t1, t2], sub_topics)

    assert [(p.coverage_id, p.findings) for p in parts] == [("topic-01", []), ("topic-02", [])]
    assert unplaced == []


# --- is_context_only (D16) -------------------------------------------------


def test_is_context_only_for_an_unbound_low_relevance_source():
    finding = _statement_finding("https://a.test/1", "Background text.", target_ids=[])
    source = _low_relevance_source("https://a.test/1", relevance=0.2)
    assert is_context_only(finding, sources_by_url([source])) is True


def test_is_context_only_false_for_a_bound_finding_even_at_low_relevance():
    finding = _statement_finding("https://a.test/1", "Background text.",
                                 target_ids=["topic-01-target-01"])
    source = _low_relevance_source("https://a.test/1", relevance=0.2)
    assert is_context_only(finding, sources_by_url([source])) is False


def test_is_context_only_false_at_or_above_the_relevance_threshold():
    finding = _statement_finding("https://a.test/1", "Background text.", target_ids=[])
    source = _low_relevance_source("https://a.test/1", relevance=CONTEXT_ONLY_RELEVANCE)
    assert is_context_only(finding, sources_by_url([source])) is False


def test_is_context_only_true_for_a_low_confidence_source_regardless_of_relevance():
    finding = _statement_finding("https://a.test/1", "Background text.", target_ids=[])
    source = _low_relevance_source("https://a.test/1", relevance=0.9, low_confidence=True)
    assert is_context_only(finding, sources_by_url([source])) is True


@pytest.mark.asyncio
async def test_a_weak_sources_distinct_fact_stays_citable_beside_a_strong_answer(writer, checker) -> None:
    """Y2.1 regression: a weak source's own distinct fact is still written
    beside a stronger finding's answer to the same required target,
    rather than being hidden as context-only."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True,
                         unit_dimension=None)
    weak = _statement_finding("https://weak.test/1",
                              "According to the source, a weak source's own distinct detail about the topic.",
                              target_ids=["topic-01-target-01"])
    strong = _statement_finding("https://strong.test/1", "According to the source, a strong claim about the topic.",
                                target_ids=["topic-01-target-01"])
    weak_source = _authority_source("https://weak.test/1", authority=0.2)
    strong_source = _authority_source("https://strong.test/1", authority=0.9)
    topic = _topic("topic-01", "Capacity added", [target])
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=[topic],
                          verified_findings=[weak, strong],
                          evaluated_sources=[weak_source, strong_source])
    task = writer.build_task(state)
    label_by_url = {f.source_url: label for label, f in task.registry}
    writer.provider._outputs.extend([
        SectionDraft(title="Capacity added", points=[
            WriterPointDraft(text="According to the source, a weak source's own distinct detail about the topic.",
                             finding_labels=[label_by_url[weak.source_url]]),
            WriterPointDraft(text="According to the source, a strong claim about the topic.",
                             finding_labels=[label_by_url[strong.source_url]]),
        ]),
        BottomLineDraft(sentences=[WriterPointDraft(
            text="According to the source, a strong claim about the topic.",
            finding_labels=[label_by_url[strong.source_url]])]),
    ])

    composition = await compose_written_report(task, provider=writer.provider, section_concurrency=7)

    section_texts = [point.text for section in composition.sections for point in section.points]
    bottom_line_texts = [point.text for point in composition.summary]
    assert "According to the source, a weak source's own distinct detail about the topic." in section_texts
    assert "According to the source, a strong claim about the topic." in section_texts + bottom_line_texts
    assert composition.rejected_points == []


@pytest.mark.asyncio
async def test_bottom_line_withholds_a_statement_resting_only_on_a_derivative_source(
    writer, checker, monkeypatch,
) -> None:
    """Run-8 D1: a statement citing only a source whose read declares
    itself derivative or teaching content is withheld from the bottom
    line's candidate pool whenever another statement meets the floor --
    it stays in its section."""
    import deep_research.agents.document_kind as document_kind
    monkeypatch.setattr(
        document_kind, "derivative_self_description",
        lambda read: (
            "This role-play was written for educational purposes."
            if read.requested_url == "https://derivative.test/1" else None
        ),
    )
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True, unit_dimension=None)
    derivative_read = make_read("A claim from a role-play.", url="https://derivative.test/1", title="A page")
    derivative = _statement_finding("https://derivative.test/1", "According to the source, a claim from a role-play.",
                                    target_ids=["topic-01-target-01"])
    derivative = derivative.model_copy(update={"read_id": derivative_read.read_id})
    strong = _statement_finding("https://strong.test/1", "According to the source, a claim from a strong source.",
                                target_ids=["topic-01-target-01"])
    derivative_source = _authority_source("https://derivative.test/1", authority=0.9)
    strong_source = _authority_source("https://strong.test/1", authority=0.9)
    topic = _topic("topic-01", "Capacity added", [target])
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=[topic],
                          verified_findings=[derivative, strong],
                          evaluated_sources=[derivative_source, strong_source],
                          read_records={derivative_read.read_id: derivative_read})
    task = writer.build_task(state)
    assert task.self_descriptions
    label_by_url = {f.source_url: label for label, f in task.registry}
    writer.provider._outputs.extend([
        SectionDraft(title="Capacity added", points=[
            WriterPointDraft(text="According to the source, a claim from a role-play.",
                             finding_labels=[label_by_url[derivative.source_url]]),
            WriterPointDraft(text="According to the source, a claim from a strong source.",
                             finding_labels=[label_by_url[strong.source_url]]),
        ]),
        BottomLineDraft(sentences=[WriterPointDraft(
            text="According to the source, a claim from a role-play.",
            finding_labels=[label_by_url[derivative.source_url]])]),
    ])

    composition = await compose_written_report(task, provider=writer.provider, section_concurrency=7)

    section_texts = [point.text for section in composition.sections for point in section.points]
    assert "According to the source, a claim from a role-play." in section_texts
    assert any(
        r.reason == "cites a finding no checked section statement cites"
        for r in composition.rejected_points
    )


@pytest.mark.asyncio
async def test_bottom_line_keeps_a_statement_citing_a_derivative_source_beside_a_strong_one(
    writer, checker, monkeypatch,
) -> None:
    """The other branch: a statement citing the derivative source
    together with one above the floor is kept -- the floor tests the
    statement's own citable sources, not any single one among them."""
    import deep_research.agents.document_kind as document_kind
    monkeypatch.setattr(
        document_kind, "derivative_self_description",
        lambda read: (
            "This role-play was written for educational purposes."
            if read.requested_url == "https://derivative.test/1" else None
        ),
    )
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True, unit_dimension=None)
    derivative_read = make_read("A claim from a role-play.", url="https://derivative.test/1", title="A page")
    derivative = _statement_finding("https://derivative.test/1", "A claim from a role-play.",
                                    target_ids=["topic-01-target-01"])
    derivative = derivative.model_copy(update={"read_id": derivative_read.read_id})
    strong = _statement_finding("https://strong.test/1", "a claim from a strong source",
                                target_ids=["topic-01-target-01"])
    derivative_source = _authority_source("https://derivative.test/1", authority=0.9)
    strong_source = _authority_source("https://strong.test/1", authority=0.9)
    topic = _topic("topic-01", "Capacity added", [target])
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=[topic],
                          verified_findings=[derivative, strong],
                          evaluated_sources=[derivative_source, strong_source],
                          read_records={derivative_read.read_id: derivative_read})
    task = writer.build_task(state)
    assert task.self_descriptions
    label_by_url = {f.source_url: label for label, f in task.registry}
    combined_text = "According to the source, a claim from a role-play and a claim from a strong source."
    writer.provider._outputs.extend([
        SectionDraft(title="Capacity added", points=[
            WriterPointDraft(
                text=combined_text,
                finding_labels=[label_by_url[derivative.source_url], label_by_url[strong.source_url]],
            ),
        ]),
        BottomLineDraft(sentences=[WriterPointDraft(
            text=combined_text,
            finding_labels=[label_by_url[derivative.source_url], label_by_url[strong.source_url]])]),
    ])

    composition = await compose_written_report(task, provider=writer.provider, section_concurrency=7)

    assert combined_text in [p.text for p in composition.summary]


@pytest.mark.asyncio
async def test_bottom_line_lets_everything_through_when_no_statement_meets_the_floor(
    writer, checker, monkeypatch,
) -> None:
    """The existing behaviour: with no statement above the floor at all
    (here every source is below it, one for being derivative and one on
    its own authority score), the withholding never applies."""
    import deep_research.agents.document_kind as document_kind
    monkeypatch.setattr(
        document_kind, "derivative_self_description",
        lambda read: (
            "This role-play was written for educational purposes."
            if read.requested_url == "https://derivative.test/1" else None
        ),
    )
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True, unit_dimension=None)
    derivative_read = make_read("A claim from a role-play.", url="https://derivative.test/1", title="A page")
    derivative = _statement_finding("https://derivative.test/1", "According to the source, a claim from a role-play.",
                                    target_ids=["topic-01-target-01"])
    derivative = derivative.model_copy(update={"read_id": derivative_read.read_id})
    weak = _statement_finding("https://weak.test/1", "According to the source, a claim from a weak source.",
                              target_ids=["topic-01-target-01"])
    derivative_source = _authority_source("https://derivative.test/1", authority=0.9)
    weak_source = _authority_source("https://weak.test/1", authority=0.2)
    topic = _topic("topic-01", "Capacity added", [target])
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=[topic],
                          verified_findings=[derivative, weak],
                          evaluated_sources=[derivative_source, weak_source],
                          read_records={derivative_read.read_id: derivative_read})
    task = writer.build_task(state)
    assert task.self_descriptions
    label_by_url = {f.source_url: label for label, f in task.registry}
    writer.provider._outputs.extend([
        SectionDraft(title="Capacity added", points=[
            WriterPointDraft(text="According to the source, a claim from a role-play.",
                             finding_labels=[label_by_url[derivative.source_url]]),
            WriterPointDraft(text="According to the source, a claim from a weak source.",
                             finding_labels=[label_by_url[weak.source_url]]),
        ]),
        BottomLineDraft(sentences=[WriterPointDraft(
            text="According to the source, a claim from a role-play.",
            finding_labels=[label_by_url[derivative.source_url]])]),
    ])

    composition = await compose_written_report(task, provider=writer.provider, section_concurrency=7)

    assert "According to the source, a claim from a role-play." in [p.text for p in composition.summary]
    assert composition.rejected_points == []


# --- finding_registry: required-first, then authority (D1, D3) -------------


def test_finding_registry_orders_a_required_targets_answers_by_authority():
    """D1, D3: within the required-target-answering group, the stronger
    source's finding gets the lower label, not merely the one that
    extracted first -- so the strongest sources of a target get the
    first labels. ``unit_dimension=None`` so both findings actually
    reach group 0 (a figure-dimensioned target needs a figure to be
    recognised as answering it, which a no-figure statement finding
    never has); the scores still differ (0.3 vs 0.8), so this proves
    authority order, not merely the group-0/1 split or the tiebreak."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True,
                         unit_dimension=None)
    weak = _statement_finding("https://weak.test/1", "A weak claim.", target_ids=["topic-01-target-01"])
    strong = _statement_finding("https://strong.test/1", "A strong claim.", target_ids=["topic-01-target-01"])
    weak_source = _authority_source("https://weak.test/1", authority=0.3)
    strong_source = _authority_source("https://strong.test/1", authority=0.8)

    registry = finding_registry([weak, strong], [target], sources=[weak_source, strong_source])

    assert [f.source_url for _, f in registry] == ["https://strong.test/1", "https://weak.test/1"]


def test_finding_registry_breaks_a_tie_by_the_targets_plan_order():
    """RevV2 P2: within a tie (missing or equal authority), the required
    group still lists a target's answers in the targets' own plan
    order, the tiebreak the pre-authority registry used -- not merely
    input/citable order, which interleaves two required targets'
    answers when authority is absent or equal."""
    target_1 = make_target("topic-01-target-01", coverage_id="topic-01", required=True,
                           unit_dimension=None)
    target_2 = make_target("topic-01-target-02", coverage_id="topic-01", required=True,
                           unit_dimension=None)
    a = _statement_finding("https://a.test/1", "A claim.", target_ids=["topic-01-target-02"])
    b = _statement_finding("https://b.test/1", "B claim.", target_ids=["topic-01-target-01"])

    registry = finding_registry([a, b], [target_1, target_2])

    assert [f.source_url for _, f in registry] == ["https://b.test/1", "https://a.test/1"]


def test_finding_registry_puts_a_required_targets_answer_first_over_a_stronger_optional_one():
    """RevV2 P3: the required-group beats authority, not merely orders
    within it -- a weaker source answering the required target still
    outranks a stronger source that only answers an optional sibling."""
    required = make_target("topic-01-target-01", coverage_id="topic-01", required=True,
                           unit_dimension=None)
    optional = make_target("topic-01-target-02", coverage_id="topic-01", required=False,
                           unit_dimension=None)
    strong_optional = _statement_finding("https://strong.test/1", "An optional claim.",
                                         target_ids=["topic-01-target-02"])
    weak_required = _statement_finding("https://weak.test/1", "A required claim.",
                                       target_ids=["topic-01-target-01"])
    strong_source = _authority_source("https://strong.test/1", authority=0.9)
    weak_source = _authority_source("https://weak.test/1", authority=0.3)

    registry = finding_registry([strong_optional, weak_required], [required, optional],
                                sources=[strong_source, weak_source])

    assert [label for label, _ in registry] == ["F01", "F02"]
    assert registry[0][1].source_url == "https://weak.test/1"


# --- registry_lines: D5's content: and passage: lines ----------------------


def test_registry_lines_carry_the_findings_content_line():
    finding = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation=EIA,
                       content="The EIA's own page states 10.4 GW in 2024.")
    text = "\n".join(registry_lines("F01", finding))
    assert "content: The EIA's own page states 10.4 GW in 2024." in text


def test_registry_lines_print_disputes_yes_for_a_disputing_finding():
    """Z1/Z2 shared contract (audit D2, CODE 1): the registry line prints
    ``disputes: yes`` for a finding the dissent re-ask returned."""
    finding = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation=EIA)
    finding = finding.model_copy(update={"disputes": True})
    text = "\n".join(registry_lines("F01", finding))
    assert "disputes: yes" in text


def test_registry_lines_omit_disputes_for_a_finding_that_does_not_dispute():
    finding = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation=EIA)
    text = "\n".join(registry_lines("F01", finding))
    assert "disputes:" not in text


def test_registry_lines_carry_the_no_figure_findings_passage():
    finding = _statement_finding("https://a.test/1", "it is the one to beat for the price.",
                                 content="Model B is the one to beat for the price.")
    passages = {finding_fingerprint(finding): "Model B, a compact model, is the one to beat for the price."}
    text = "\n".join(registry_lines("F01", finding, passages))
    assert "passage: Model B, a compact model, is the one to beat for the price." in text


def test_registry_lines_omit_a_passage_line_for_a_kept_figure():
    finding = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation=EIA)
    text = "\n".join(registry_lines("F01", finding, {finding_fingerprint(finding): "unused"}))
    assert "passage:" not in text


def test_registry_lines_carry_the_sources_rationale_first_sentence():
    """D8/D9: the registry line states the Source Evaluator's own
    rationale, first sentence, as "source: ..."."""
    finding = _statement_finding("https://a.test/1", "A claim.", target_ids=[])
    source = ScoredSource(
        url="https://a.test/1", title="A page", authority_score=0.5, recency_score=0.5,
        relevance_score=0.8, overall_score=0.5,
        rationale="A student paper hosted at a university. It was not peer reviewed.",
    )
    text = "\n".join(registry_lines("F01", finding, sources=sources_by_url([source])))
    assert "source: A student paper hosted at a university." in text
    assert "It was not peer reviewed." not in text


def test_registry_lines_truncate_a_rationale_with_no_word_boundary_at_120_characters():
    finding = _statement_finding("https://a.test/1", "A claim.", target_ids=[])
    source = ScoredSource(
        url="https://a.test/1", title="A page", authority_score=0.5, recency_score=0.5,
        relevance_score=0.8, overall_score=0.5, rationale="A" * 200,
    )
    text = "\n".join(registry_lines("F01", finding, sources=sources_by_url([source])))
    line = next(l for l in text.splitlines() if l.startswith("source: "))
    assert line[len("source: "):] == "A" * 120 + "…"


def test_registry_lines_truncate_a_long_rationale_at_a_word_boundary_with_an_ellipsis():
    finding = _statement_finding("https://a.test/1", "A claim.", target_ids=[])
    long_rationale = ("An enthusiast site with no named author, no editorial review and no "
                      "institutional backing for any of its historical claims about the topic")
    source = ScoredSource(
        url="https://a.test/1", title="A page", authority_score=0.5, recency_score=0.5,
        relevance_score=0.8, overall_score=0.5, rationale=long_rationale,
    )
    text = "\n".join(registry_lines("F01", finding, sources=sources_by_url([source])))
    line = next(l for l in text.splitlines() if l.startswith("source: "))
    expected = ("An enthusiast site with no named author, no editorial review and no "
               "institutional backing for any of its historical")
    assert line[len("source: "):] == expected + "…"


def test_registry_lines_do_not_split_the_rationale_at_an_abbreviation():
    """P3-2: 'U.S. Department' is not two sentences."""
    finding = _statement_finding("https://a.test/1", "A claim.", target_ids=[])
    source = ScoredSource(
        url="https://a.test/1", title="A page", authority_score=0.5, recency_score=0.5,
        relevance_score=0.8, overall_score=0.5,
        rationale="Published by the U.S. Department of Energy. A government statistical release.",
    )
    text = "\n".join(registry_lines("F01", finding, sources=sources_by_url([source])))
    assert "source: Published by the U.S. Department of Energy." in text


def test_registry_lines_omit_the_source_line_for_a_pipeline_generated_rationale():
    """P3-2: a "Cited for: ..." rationale carries no source-evaluator
    judgement, so it never reaches the writer as the page's kind."""
    finding = _statement_finding("https://a.test/1", "A claim.", target_ids=[])
    source = ScoredSource(
        url="https://a.test/1", title="A page", authority_score=0.5, recency_score=0.5,
        relevance_score=0.8, overall_score=0.5,
        rationale="Cited for: battery storage capacity.",
    )
    text = "\n".join(registry_lines("F01", finding, sources=sources_by_url([source])))
    assert "source:" not in text


def test_registry_lines_omit_the_source_line_when_no_sources_are_given():
    finding = _statement_finding("https://a.test/1", "A claim.", target_ids=[])
    text = "\n".join(registry_lines("F01", finding))
    assert "source:" not in text


def test_registry_lines_print_a_self_description_when_the_read_declares_one():
    """Run-8 D1: a finding whose read has a derivative self-description
    prints "self-description: <sentence>" on its registry block, so the
    writer sees what the document says about itself before it credits it."""
    finding = _statement_finding("https://a.test/1", "A claim.", target_ids=[])
    self_descriptions = {finding.read_id: "This role-play was written for educational purposes."}
    text = "\n".join(registry_lines("F01", finding, self_descriptions=self_descriptions))
    assert "self-description: This role-play was written for educational purposes." in text


def test_registry_lines_omit_self_description_when_the_read_declares_none():
    finding = _statement_finding("https://a.test/1", "A claim.", target_ids=[])
    text = "\n".join(registry_lines("F01", finding))
    assert "self-description:" not in text


def test_section_messages_carry_the_finding_sources_rationale():
    task = _one_target_task()
    source = ScoredSource(
        url=task.findings[0].source_url, title="A page", authority_score=0.9,
        recency_score=0.8, relevance_score=0.8, overall_score=0.8,
        rationale="A peer-reviewed statistical release.",
    )
    task = task.model_copy(update={"sources": [source]})
    job = PartJob(coverage_id="topic-01", sub_topic_title="Capacity added", order=0,
                 targets=task.targets, findings=task.findings, context_findings=[],
                 previous=None, defects=[], redraft=True)

    body = section_messages(task, job)[-1].content

    assert "source: A peer-reviewed statistical release." in body


# --- section_messages: part scoping, answer form, redraft context ---------


def _one_target_task(*, defects=(), previous=None) -> ReportWriterTask:
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    finding = _checked("https://a.test/1", "The EIA reported 10.4 GW in 2024.", "10.4", "GW", organisation=EIA)
    return ReportWriterTask(
        instruction="Q?", session_id="s1", question="Q?", as_of="2026-08-01",
        sub_topics=[_topic("topic-01", "Capacity added", [target])],
        targets=[target], findings=[finding], sources=[],
        registry=finding_registry([finding], [target]),
        answered={target.target_id: [finding_fingerprint(finding)]},
        not_found=[], defects=list(defects), previous=previous,
    )


def test_section_messages_list_this_parts_registry_and_the_answer_form():
    task = _one_target_task()
    job = PartJob(coverage_id="topic-01", sub_topic_title="Capacity added", order=0,
                 targets=task.targets, findings=task.findings, context_findings=[],
                 previous=None, defects=[], redraft=True)

    body = section_messages(task, job)[-1].content

    assert "# Answer form" in body
    assert "# This part of the question" in body
    assert "F01" in body
    assert "# Defects to fix" not in body
    assert "# Your previous section" not in body


def test_section_messages_never_lists_a_different_parts_finding():
    other_target = make_target("topic-02-target-01", coverage_id="topic-02", required=True)
    other_finding = _checked("https://a.test/9", "5 GW in 2025.", "5", "GW", organisation=EIA,
                             target_ids=["topic-02-target-01"])
    task = _one_target_task()
    task = task.model_copy(update={
        "sub_topics": [*task.sub_topics, _topic("topic-02", "Other", [other_target])],
        "targets": [*task.targets, other_target],
        "findings": [*task.findings, other_finding],
        "registry": finding_registry([*task.findings, other_finding], [*task.targets, other_target]),
    })
    job = PartJob(coverage_id="topic-01", sub_topic_title="Capacity added", order=0,
                 targets=[t for t in task.targets if t.coverage_id == "topic-01"],
                 findings=[f for f in task.findings if finding_fingerprint(f) != finding_fingerprint(other_finding)],
                 context_findings=[], previous=None, defects=[], redraft=True)

    body = section_messages(task, job)[-1].content

    assert "5 GW in 2025" not in body


def test_a_redraft_shows_the_previous_section_and_its_defects():
    defect = ReviewDefect(defect_id="review-01", kind="missing_support", severity="major",
                          target_ids=["topic-01-target-01"], problem="The figure needs its date.")
    previous = ReportSection(title="Capacity added", coverage_id="topic-01", points=[])
    task = _one_target_task(defects=[defect])
    job = PartJob(coverage_id="topic-01", sub_topic_title="Capacity added", order=0,
                 targets=task.targets, findings=task.findings, context_findings=[],
                 previous=previous, defects=[defect], redraft=True)

    body = section_messages(task, job)[-1].content

    assert "# Your previous section" in body
    assert "# Defects to fix" in body
    assert "The figure needs its date." in body


def test_section_messages_list_context_only_findings_under_their_own_heading():
    task = _one_target_task()
    context_finding = _statement_finding("https://a.test/7", "Background chatter.", target_ids=[])
    job = PartJob(coverage_id="topic-01", sub_topic_title="Capacity added", order=0,
                 targets=task.targets, findings=task.findings, context_findings=[context_finding],
                 previous=None, defects=[], redraft=True)
    task = task.model_copy(update={
        "registry": [*task.registry, ("F02", context_finding)],
    })

    body = section_messages(task, job)[-1].content

    assert "# Context only" in body
    assert "Background chatter." in body


def test_section_messages_state_a_point_budget_for_this_part():
    """D11: the reader-length budget divides ``target_words`` by this
    part's weighted share (Y2.2), never fewer than 3 or fewer than the
    part's own required-target count."""
    task = _one_target_task()
    task = task.model_copy(update={"target_words": 900})
    job = PartJob(coverage_id="topic-01", sub_topic_title="Capacity added", order=0,
                 targets=task.targets, findings=task.findings, context_findings=[],
                 previous=None, defects=[], redraft=True, part_weight_sum=4)

    body = section_messages(task, job)[-1].content

    assert "# Point budget" in body
    assert "Write at most 15 points for this part" in body
    assert "about 675 words in total" in body
    assert "The evidence log keeps every finding." in body


def test_point_and_word_budget_weight_a_required_part_three_to_one():
    """Y2.2 (audit D2, CODE 2): with 7 parts and 1 required, the required
    part's weight (3) is three quarters of the total weight (3 + 6 x 1 =
    9), so it gets about 667 of a 2000-word budget and about 15 points;
    each optional part gets about 222 words and about 5 points."""
    from deep_research.agents.report_writer import _point_budget, _word_budget
    targets = [make_target("topic-01-target-01", coverage_id="topic-01", required=True)]
    findings = [_statement_finding("https://a.test/1", "Fact.", target_ids=["topic-01-target-01"])]
    task = _one_target_task()
    task = task.model_copy(update={
        "targets": targets, "findings": findings, "target_words": 2000,
        "answered": {"topic-01-target-01": [finding_fingerprint(findings[0])]},
    })
    own_finding_ids = {finding_fingerprint(f) for f in findings}
    required_job = PartJob(coverage_id="topic-01", sub_topic_title="Required part", order=0,
                           targets=targets, findings=findings, context_findings=[],
                           previous=None, defects=[], redraft=True, part_weight_sum=9)
    optional_job = PartJob(coverage_id="topic-02", sub_topic_title="Optional part", order=1,
                           targets=[], findings=[], context_findings=[],
                           previous=None, defects=[], redraft=True, part_weight_sum=9)

    assert _word_budget(task, required_job, own_finding_ids) == 667
    assert _point_budget(task, required_job, own_finding_ids) == 15
    assert _word_budget(task, optional_job, set()) == 222
    assert _point_budget(task, optional_job, set()) == 5


def test_point_budget_never_drops_below_three():
    from deep_research.agents.report_writer import _point_budget
    task = _one_target_task()
    task = task.model_copy(update={"target_words": 100, "answered": {}})
    job = PartJob(coverage_id="topic-01", sub_topic_title="Capacity added", order=0,
                 targets=[], findings=[], context_findings=[],
                 previous=None, defects=[], redraft=True, part_weight_sum=10)

    assert _point_budget(task, job, set()) == 3


def test_point_budget_uses_the_required_targets_floor_when_it_exceeds_the_length_budget():
    from deep_research.agents.report_writer import _point_budget
    targets = [make_target(f"topic-01-target-{n:02d}", coverage_id="topic-01", required=True)
              for n in range(1, 6)]
    findings = [_statement_finding(f"https://a.test/{n}", f"Fact {n}.", target_ids=[t.target_id])
               for n, t in enumerate(targets, start=1)]
    task = _one_target_task()
    task = task.model_copy(update={
        "targets": targets, "findings": findings, "target_words": 45,
        "answered": {t.target_id: [finding_fingerprint(f)] for t, f in zip(targets, findings)},
    })
    job = PartJob(coverage_id="topic-01", sub_topic_title="Capacity added", order=0,
                 targets=targets, findings=findings, context_findings=[],
                 previous=None, defects=[], redraft=True)
    own_finding_ids = {finding_fingerprint(f) for f in findings}

    assert _point_budget(task, job, own_finding_ids) == 5


def test_word_budget_divides_by_the_parts_weighted_share():
    from deep_research.agents.report_writer import _word_budget
    task = _one_target_task()
    task = task.model_copy(update={"target_words": 900})
    job = PartJob(coverage_id="topic-01", sub_topic_title="Capacity added", order=0,
                 targets=task.targets, findings=task.findings, context_findings=[],
                 previous=None, defects=[], redraft=True, part_weight_sum=9)
    own_finding_ids = {finding_fingerprint(f) for f in task.findings}

    assert _word_budget(task, job, own_finding_ids) == 300


def test_word_budget_never_drops_below_sixty_times_required_targets_answered():
    from deep_research.agents.report_writer import _word_budget
    targets = [make_target(f"topic-01-target-{n:02d}", coverage_id="topic-01", required=True)
              for n in range(1, 4)]
    findings = [_statement_finding(f"https://a.test/{n}", f"Fact {n}.", target_ids=[t.target_id])
               for n, t in enumerate(targets, start=1)]
    task = _one_target_task()
    task = task.model_copy(update={
        "targets": targets, "findings": findings, "target_words": 30,
        "answered": {t.target_id: [finding_fingerprint(f)] for t, f in zip(targets, findings)},
    })
    job = PartJob(coverage_id="topic-01", sub_topic_title="Capacity added", order=0,
                 targets=targets, findings=findings, context_findings=[],
                 previous=None, defects=[], redraft=True)
    own_finding_ids = {finding_fingerprint(f) for f in findings}

    assert _word_budget(task, job, own_finding_ids) == 180


def test_section_messages_state_the_computed_word_budget():
    from deep_research.agents.report_writer import _word_budget
    task = _one_target_task()
    task = task.model_copy(update={"target_words": 900})
    job = PartJob(coverage_id="topic-01", sub_topic_title="Capacity added", order=0,
                 targets=task.targets, findings=task.findings, context_findings=[],
                 previous=None, defects=[], redraft=True, part_weight_sum=9)
    own_finding_ids = {finding_fingerprint(f) for f in task.findings}
    expected = _word_budget(task, job, own_finding_ids)

    body = section_messages(task, job)[-1].content

    assert str(expected) in body


# --- bottom_line_messages ---------------------------------------------------


def test_bottom_line_messages_group_checked_statements_under_their_part_title():
    task = _one_target_task()
    statement = ReportStatement(statement_id="S001", text="The EIA reported 10.4 GW in 2024.",
                                finding_ids=[finding_fingerprint(task.findings[0])],
                                target_ids=["topic-01-target-01"])
    section = ReportSection(title="Capacity added", coverage_id="topic-01",
                            points=[ReportPointFor("The EIA reported 10.4 GW in 2024.", statement)])

    body = bottom_line_messages(task, [section])[-1].content

    assert "# Checked statements" in body
    assert "Capacity added" in body
    assert "The EIA reported 10.4 GW in 2024." in body
    assert "F01" in body


def ReportPointFor(text, statement):
    from deep_research.utils.types import ReportPoint
    return ReportPoint(text=text, statement=statement)


def test_a_bottom_line_redraft_shows_its_previous_text():
    task = _one_target_task()
    previous_point = ReportPointFor("Old bottom line.", ReportStatement(
        statement_id="S001", text="Old bottom line.", finding_ids=[], target_ids=[]))

    body = bottom_line_messages(task, [], previous=[previous_point])[-1].content

    assert "# Your previous bottom line" in body
    assert "Old bottom line." in body


def test_section_messages_carry_the_own_voice_and_actual_outcome_rules():
    """Fable prompt review (Required 1, Recommended 5): both new rules reach
    the model, not just the constant."""
    task = _one_target_task()
    job = PartJob(coverage_id="topic-01", sub_topic_title="Capacity added", order=0,
                 targets=task.targets, findings=task.findings, context_findings=[],
                 previous=None, defects=[], redraft=True)

    body = section_messages(task, job)[-1].content

    assert "never a pick, ranking, verdict or criterion of your own" in body
    assert ("an actual as a reported outcome with its period, never with a "
           "forecast verb" in body)


def test_section_messages_explain_the_sub_topic_only_suffix():
    """Fable prompt review (Recommended 4)."""
    task = _one_target_task()
    job = PartJob(coverage_id="topic-01", sub_topic_title="Capacity added", order=0,
                 targets=task.targets, findings=task.findings, context_findings=[],
                 previous=None, defects=[], redraft=True)

    body = section_messages(task, job)[-1].content

    assert ("answered by a finding matched to its sub-topic, not bound to that "
           "target explicitly" in body)


def test_bottom_line_messages_carry_the_figure_credit_and_page_date_rules():
    """Fable prompt review (Required 2, Recommended 3): both new rules reach
    the model, not just the constant."""
    task = _one_target_task()

    body = bottom_line_messages(task, [])[-1].content

    assert ("Credit every figure, judgement, pick or ranking to its source "
           "exactly as the statement credits it, keeping \"according to "
           "<organisation>, as reported by <site>\" where the statement has it." in body)
    assert ("Never state a page's own date (the sources list prints it); a "
           "forecast's release is not a page date and stays." in body)


def test_answer_form_line_does_not_double_its_own_label():
    """Fable prompt review (item 6): _ANSWER_FORM_REQUIREMENTS' values start
    with their own literal "answer form: " prefix (for planner.py's inline
    use); printed under this module's own "# Answer form" heading unchanged,
    that doubled the label."""
    task = _one_target_task()
    task = task.model_copy(update={"answer_kind": "factual"})
    job = PartJob(coverage_id="topic-01", sub_topic_title="Capacity added", order=0,
                 targets=task.targets, findings=task.findings, context_findings=[],
                 previous=None, defects=[], redraft=True)

    body = section_messages(task, job)[-1].content
    answer_form_section = body.split("# Answer form\n", 1)[1].split("\n# ", 1)[0].rstrip("\n")

    assert answer_form_section == "the specific thing asked for, in the form its evidence takes \u2014 a figure with its value, unit and date; items with their attributes; dated events; reasons; a rule's provisions"
    assert "answer form:" not in answer_form_section.lower()


# --- prompt content: WRI-2/WRI-5 directions ---------------------------------


def test_section_instruction_states_the_point_length_bound():
    assert f"under {MAX_POINT_CHARS} characters" in SECTION_INSTRUCTION


def test_section_instruction_drops_the_section_count_cap_and_additive_rule():
    assert "at most four sections" not in SECTION_INSTRUCTION
    assert "adds something the executive summary does not carry" not in SECTION_INSTRUCTION


def test_section_instruction_names_the_new_target_answering_rule():
    assert "for this part's targets" in SECTION_INSTRUCTION
    assert "a point never announces an absence" in SECTION_INSTRUCTION


def test_section_instruction_states_the_unnamed_subject_guard():
    assert "bare pronoun" in SECTION_INSTRUCTION


def test_section_instruction_forbids_resting_only_on_context_only_findings():
    assert '"# Context only"' in SECTION_INSTRUCTION


def test_section_instruction_describes_the_option_marks():
    assert "picked" in SECTION_INSTRUCTION and "items" in SECTION_INSTRUCTION


def test_section_instruction_defines_picked_and_by_for_a_relayed_recommendation():
    """Whole-branch review P1-1: a relayed pick (a body the page reports as
    recommending an option, not the page's own voice) is not ambiguous, and
    `by` names the finding's *label* -- a body name in `by` gets the mark
    dropped by `_apply_marks` as citing no finding."""
    assert ("picked true when the finding reports a recommendation, pick or "
           "first-place ranking by the body it attributes" in SECTION_INSTRUCTION)
    assert ("by the label of the finding whose page reports the recommendation"
           in SECTION_INSTRUCTION)


def test_section_instruction_keeps_the_criterion_in_the_verdict_span():
    """Whole-branch review P2-2: the "shortest span" rule must not cut the
    criterion the sentence states the verdict by, and states the
    `_MARK_SPAN_CHARS` cap that would otherwise silently drop the mark."""
    from deep_research.agents.report_writer import _MARK_SPAN_CHARS
    assert "including the criterion the sentence states it by" in SECTION_INSTRUCTION
    assert (f"at most {_MARK_SPAN_CHARS} characters (a longer span drops the mark)"
           in SECTION_INSTRUCTION)



def test_section_instruction_describes_the_redraft_rule():
    assert "only the edits" in SECTION_INSTRUCTION


def test_bottom_line_system_prompt_states_the_sentence_bound():
    assert "one or two sentences" in BOTTOM_LINE_SYSTEM_PROMPT
    assert MAX_ANSWER_SENTENCES == 2


def test_bottom_line_instruction_forbids_a_pick_of_its_own():
    assert "never a pick, ranking or criterion of your own" in BOTTOM_LINE_INSTRUCTION


def test_bottom_line_instruction_forbids_stating_a_pages_own_date():
    assert "Never state a page's own date" in BOTTOM_LINE_INSTRUCTION
    assert "a forecast's release is not a page date and stays" in BOTTOM_LINE_INSTRUCTION


# --- generality (D10): no domain or probe wording in model-read text -------


_FORBIDDEN_WORDS = ("headphone", "battery", "electoral", "kettle", "sony", "eia",
                    "buds", "earbud", "headset", "valorant", "semaglutide")


@pytest.mark.parametrize("text", [SECTION_SYSTEM_PROMPT, SECTION_INSTRUCTION,
                                  BOTTOM_LINE_SYSTEM_PROMPT, BOTTOM_LINE_INSTRUCTION])
def test_model_read_prompt_text_names_no_domain_word(text: str) -> None:
    lowered = text.lower()
    for word in _FORBIDDEN_WORDS:
        assert word not in lowered, word


# --- fixtures ----------------------------------------------------------------


@dataclass
class _FakeStatementCheckItem:
    label: str
    text: str
    findings: list = field(default_factory=list)
    labels: list = field(default_factory=list)
    passages: dict = field(default_factory=dict)
    source_lines: dict = field(default_factory=dict)


@dataclass
class _FakeVerdict:
    label: str
    verdict: str = "consistent"
    corrected_text: str = ""
    reason: str = "consistent with its findings"


def _verdict(verdict: str, *, corrected_text: str = "", reason: str | None = None) -> _FakeVerdict:
    return _FakeVerdict(label="", verdict=verdict, corrected_text=corrected_text,
                        reason=reason or "consistent with its findings")


def _output_limit_error() -> ProviderOutputLimitError:
    return ProviderOutputLimitError(
        ProviderResponseTelemetry(
            finish_reason_category="length",
            configured_max_tokens=4096,
            usage=TokenUsage(input_tokens=5, output_tokens=4096),
            request_attempt=1,
        )
    )


class _FakeChecker:
    """Monkeypatched over ``evidence_verifier.check_statements``. Records the
    ``gate`` every call receives, so the pipelined-sharing acceptance test can
    assert every part's batches and the bottom line's share one semaphore."""

    def __init__(self) -> None:
        self.verdicts: dict[str, _FakeVerdict] = {}
        self.errors: list = []
        self.calls: list[list[_FakeStatementCheckItem]] = []
        self.gates: list[object] = []
        self.fail_all: bool = False
        """When set, every batch returns no verdicts at all (a Statement Check
        outage, D8): every item is left ``None`` -- "unchecked" -- exactly as
        a real batch failure leaves it, rather than this fake's own default
        of ``consistent``."""

    async def __call__(self, provider, items, *, question, fingerprint=None,
                       batch_size=None, concurrency=None, gate=None):
        del provider, question, fingerprint, batch_size, concurrency
        self.gates.append(gate)
        batch = list(items)
        self.calls.append(batch)
        if self.fail_all:
            return {}, list(self.errors)
        result = {}
        for item in batch:
            verdict = self.verdicts.get(item.label)
            if verdict is not None:
                result[item.label] = _FakeVerdict(label=item.label, verdict=verdict.verdict,
                                                  corrected_text=verdict.corrected_text, reason=verdict.reason)
            else:
                result[item.label] = _FakeVerdict(label=item.label)
        return result, list(self.errors)


@pytest.fixture
def checker(monkeypatch) -> _FakeChecker:
    fake = _FakeChecker()
    monkeypatch.setattr("deep_research.agents.evidence_verifier.StatementCheckItem",
                        _FakeStatementCheckItem, raising=False)
    monkeypatch.setattr("deep_research.agents.evidence_verifier.check_statements", fake, raising=False)
    return fake


@pytest.fixture
def tracker() -> Tracker:
    from deep_research.observability import LangSmithRuntimeConfig
    return Tracker(LangSmithRuntimeConfig(tracing_enabled=False))


def _writer(tracker: Tracker, completer, tools: list[BaseTool] | None = None,
           **overrides: object) -> ReportWriterAgent:
    return ReportWriterAgent(
        provider=completer, tracker=tracker,
        scratchpad=ScratchpadMemory(session_id="session-1", agent_name=REPORT_WRITER_NAME, max_entries=20),
        tools=tools or [], config=overrides.pop("config", AgentRuntimeConfig(max_iterations=2, tool_budget=0)),
        **overrides,
    )


@pytest.fixture
def writer(tracker: Tracker, tmp_path: Path) -> ReportWriterAgent:
    return _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))


def _one_part_state() -> ResearchState:
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    finding = _checked("https://a.test/1", "The EIA reported 10.4 GW in 2024.", "10.4", "GW", organisation=EIA)
    topic = _topic("topic-01", "Capacity added", [target])
    return ResearchState(session_id="s1", original_question="How much capacity was added?",
                         sub_topics=[topic], verified_findings=[finding])


# --- orchestration: one call per part, pipelined checks, redraft, failure --


@pytest.mark.asyncio
async def test_one_non_empty_part_makes_one_section_call_and_one_bottom_line_call(writer, checker) -> None:
    state = _one_part_state()
    task = writer.build_task(state)
    writer.provider._outputs.extend([
        SectionDraft(title="Capacity added",
                    points=[WriterPointDraft(text="The EIA reported 10.4 GW in 2024.", finding_labels=["F01"])]),
        BottomLineDraft(sentences=[WriterPointDraft(text="The EIA reported 10.4 GW in 2024.", finding_labels=["F01"])]),
    ])

    composition = await compose_written_report(task, provider=writer.provider, section_concurrency=7)

    schemas = [call[0] for call in writer.provider.calls]
    assert schemas == ["SectionDraft", "BottomLineDraft"]
    assert len(composition.sections) == 1
    assert composition.sections[0].coverage_id == "topic-01"
    assert len(composition.summary) == 1
    assert [p.status for p in composition.parts] == ["written"]


@pytest.mark.asyncio
async def test_a_findings_source_line_reaches_the_statement_check(writer, checker) -> None:
    """W2: the Statement Check and the reviewer must see the same
    ``source:`` line the writer's own registry prints, so a sentence naming
    a weak page's kind can be judged against what it was shown, not
    invented against a block that never carried it."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    finding = _checked("https://a.test/1", "The EIA reported 10.4 GW in 2024.", "10.4", "GW",
                       organisation=EIA)
    source = ScoredSource(
        url="https://a.test/1", title="A page", authority_score=0.5, recency_score=0.5,
        relevance_score=0.8, overall_score=0.5,
        rationale="A student paper hosted at a university.",
    )
    topic = _topic("topic-01", "Capacity added", [target])
    state = ResearchState(session_id="s1", original_question="How much capacity was added?",
                          sub_topics=[topic], verified_findings=[finding], evaluated_sources=[source])
    task = writer.build_task(state)
    writer.provider._outputs.extend([
        SectionDraft(title="Capacity added",
                    points=[WriterPointDraft(text="According to the source, 10.4 GW in 2024.",
                                             finding_labels=["F01"])]),
        BottomLineDraft(sentences=[WriterPointDraft(text="According to the source, 10.4 GW in 2024.",
                                                    finding_labels=["F01"])]),
    ])

    await compose_written_report(task, provider=writer.provider, section_concurrency=7)

    finding_id = finding_fingerprint(finding)
    assert len(checker.calls) == 2
    for batch in checker.calls:
        assert batch[0].source_lines[finding_id] == "A student paper hosted at a university."


@pytest.mark.asyncio
async def test_a_point_resting_only_on_context_only_findings_is_refused(writer, checker) -> None:
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    context_finding = _statement_finding("https://a.test/9", "Background chatter about the topic.",
                                         target_ids=[], related_sub_topic="Capacity added")
    source = _low_relevance_source("https://a.test/9", relevance=0.2)
    topic = _topic("topic-01", "Capacity added", [target])
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=[topic],
                          verified_findings=[context_finding], evaluated_sources=[source])
    task = writer.build_task(state)
    writer.provider._outputs.extend([
        SectionDraft(title="Capacity added",
                    points=[WriterPointDraft(text="Background chatter about the topic.",
                                             finding_labels=["F01"])]),
        BottomLineDraft(sentences=[]),
    ])

    composition = await compose_written_report(task, provider=writer.provider, section_concurrency=7)

    assert composition.sections == []
    assert composition.rejected_points[0].reason == "rests only on context-only findings"
    assert checker.calls == []


@pytest.mark.asyncio
async def test_the_bottom_line_only_sees_above_floor_statements_when_one_exists(
    tracker: Tracker, tmp_path: Path, checker,
) -> None:
    """D6/D7 bullet 3: once a checked statement citing a finding at or
    above the authority floor exists, a statement resting only on
    below-floor findings is withheld from the bottom line's own candidate
    pool -- it stays printed in its own section."""
    t1 = make_target("topic-01-target-01", coverage_id="topic-01", required=True, unit_dimension=None)
    t2 = make_target("topic-01-target-02", coverage_id="topic-01", required=True, unit_dimension=None)
    weak = _statement_finding("https://weak.test/1", "According to the source, a weak claim about part one.",
                              target_ids=["topic-01-target-01"])
    strong = _statement_finding("https://strong.test/1", "According to the source, a strong claim about part two.",
                                target_ids=["topic-01-target-02"])
    weak_source = _authority_source("https://weak.test/1", authority=0.2)
    strong_source = _authority_source("https://strong.test/1", authority=0.9)
    topic = _topic("topic-01", "Capacity added", [t1, t2])
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=[topic],
                          verified_findings=[weak, strong],
                          evaluated_sources=[weak_source, strong_source])
    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)
    label_by_url = {f.source_url: label for label, f in task.registry}
    captured: dict[str, str] = {}

    def route(messages, schema):
        if schema is BottomLineDraft:
            captured["text"] = messages[-1].content
            return BottomLineDraft(sentences=[WriterPointDraft(
                text="According to the source, a strong claim about part two.",
                finding_labels=[label_by_url[strong.source_url]])])
        return SectionDraft(title="Capacity added", points=[
            WriterPointDraft(text="According to the source, a weak claim about part one.",
                             finding_labels=[label_by_url[weak.source_url]]),
            WriterPointDraft(text="According to the source, a strong claim about part two.",
                             finding_labels=[label_by_url[strong.source_url]]),
        ])

    completer = ScriptedCompleter(outputs=[route, route])

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert "According to the source, a strong claim about part two." in captured["text"]
    assert "According to the source, a weak claim about part one." not in captured["text"]
    section_texts = [point.text for section in composition.sections for point in section.points]
    assert "According to the source, a weak claim about part one." in section_texts


@pytest.mark.asyncio
async def test_the_bottom_line_sees_every_checked_statement_when_none_reaches_the_floor(
    tracker: Tracker, tmp_path: Path, checker,
) -> None:
    """D6/D7 bullet 3: when no checked statement cites a finding at or
    above the floor, every one reaches the bottom line unchanged."""
    t1 = make_target("topic-01-target-01", coverage_id="topic-01", required=True, unit_dimension=None)
    t2 = make_target("topic-01-target-02", coverage_id="topic-01", required=True, unit_dimension=None)
    weak1 = _statement_finding("https://weak1.test/1", "According to the source, a weak claim about part one.",
                               target_ids=["topic-01-target-01"])
    weak2 = _statement_finding("https://weak2.test/1", "According to the source, a weak claim about part two.",
                               target_ids=["topic-01-target-02"])
    weak1_source = _authority_source("https://weak1.test/1", authority=0.2)
    weak2_source = _authority_source("https://weak2.test/1", authority=0.1)
    topic = _topic("topic-01", "Capacity added", [t1, t2])
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=[topic],
                          verified_findings=[weak1, weak2],
                          evaluated_sources=[weak1_source, weak2_source])
    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)
    label_by_url = {f.source_url: label for label, f in task.registry}
    captured: dict[str, str] = {}

    def route(messages, schema):
        if schema is BottomLineDraft:
            captured["text"] = messages[-1].content
            return BottomLineDraft(sentences=[])
        return SectionDraft(title="Capacity added", points=[
            WriterPointDraft(text="According to the source, a weak claim about part one.",
                             finding_labels=[label_by_url[weak1.source_url]]),
            WriterPointDraft(text="According to the source, a weak claim about part two.",
                             finding_labels=[label_by_url[weak2.source_url]]),
        ])

    completer = ScriptedCompleter(outputs=[route, route])

    await compose_written_report(task, provider=completer, section_concurrency=7)

    assert "According to the source, a weak claim about part one." in captured["text"]
    assert "According to the source, a weak claim about part two." in captured["text"]


@pytest.mark.asyncio
async def test_one_failing_part_does_not_lose_the_rest(checker, tracker: Tracker, tmp_path: Path) -> None:
    t1 = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    t2 = make_target("topic-02-target-01", coverage_id="topic-02", required=True)
    f1 = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation=EIA,
                 target_ids=["topic-01-target-01"])
    f2 = _checked("https://a.test/2", "5 GW in 2025.", "5", "GW", organisation=EIA,
                 target_ids=["topic-02-target-01"], kind="forecast", period="2025")
    topics = [_topic("topic-01", "First", [t1]), _topic("topic-02", "Second", [t2])]
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=topics,
                          verified_findings=[f1, f2])

    def route(messages, schema):
        body = messages[-1].content
        if schema.__name__ == "BottomLineDraft":
            return BottomLineDraft(sentences=[WriterPointDraft(text="According to the source, 5 GW in 2025.", finding_labels=["F02"])])
        if "First" in body.split("# This part of the question")[1][:40]:
            raise ProviderResponseError("provider returned an HTTP error", retryable=True,
                                        failure_category="http", http_status_code=503, failure_origin="sdk")
        return SectionDraft(title="Second",
                            points=[WriterPointDraft(text="According to the source, 5 GW in 2025.", finding_labels=["F02"])])

    completer = ScriptedCompleter(outputs=[route, route, route])
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    statuses = {p.coverage_id: p.status for p in composition.parts}
    assert statuses["topic-01"] == "failed"
    assert statuses["topic-02"] == "written"
    assert len(composition.sections) == 1
    assert any(e.error_type == "report_writer_section_failed" for e in composition.errors)


@pytest.mark.asyncio
async def test_a_redraft_re_asks_only_the_parts_a_defect_names(checker, tracker: Tracker, tmp_path: Path) -> None:
    t1 = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    t2 = make_target("topic-02-target-01", coverage_id="topic-02", required=True)
    f1 = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation=EIA,
                 target_ids=["topic-01-target-01"])
    f2 = _checked("https://a.test/2", "5 GW in 2025.", "5", "GW", organisation=EIA,
                 target_ids=["topic-02-target-01"], kind="forecast", period="2025")
    topics = [_topic("topic-01", "First", [t1]), _topic("topic-02", "Second", [t2])]

    previous_statement_1 = ReportStatement(statement_id="S001", text="10.4 GW in 2024.",
                                           finding_ids=[finding_fingerprint(f1)], target_ids=["topic-01-target-01"])
    previous_statement_2 = ReportStatement(statement_id="S002", text="5 GW in 2025.",
                                           finding_ids=[finding_fingerprint(f2)], target_ids=["topic-02-target-01"])
    previous_section_1 = ReportSection(title="First", coverage_id="topic-01",
                                       points=[ReportPointFor("10.4 GW in 2024.", previous_statement_1)])
    previous_section_2 = ReportSection(title="Second", coverage_id="topic-02",
                                       points=[ReportPointFor("5 GW in 2025.", previous_statement_2)])
    from deep_research.utils.types import ReportComposition
    previous = ReportComposition(question="Q?", session_id="s1", sections=[previous_section_1, previous_section_2],
                                 summary=[], sub_topics=topics)
    defect = ReviewDefect(defect_id="review-01", kind="missing_support", severity="major",
                          target_ids=["topic-01-target-01"], problem="Needs the release date.")
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=topics,
                          verified_findings=[f1, f2], composition=previous, report_review=_scored_review([defect]))

    calls: list[str] = []

    def route(messages, schema):
        body = messages[-1].content
        if schema.__name__ == "BottomLineDraft":
            calls.append("bottom_line")
            return BottomLineDraft(sentences=[])
        if "topic-01-target-01" in body or "10.4 GW" in body:
            calls.append("topic-01")
            return SectionDraft(title="First",
                                points=[WriterPointDraft(text="According to the source, 10.4 GW in 2024, corrected.", finding_labels=["F01"])])
        calls.append("unexpected")
        return SectionDraft(title="Second", points=[])

    completer = ScriptedCompleter(outputs=[route, route])
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert "unexpected" not in calls
    assert calls.count("topic-01") == 1
    assert calls.count("bottom_line") == 1
    statuses = {p.coverage_id: p.status for p in composition.parts}
    assert statuses["topic-01"] == "written"
    assert statuses["topic-02"] == "carried_over"
    carried = next(s for s in composition.sections if s.coverage_id == "topic-02")
    assert carried.points[0].text == "5 GW in 2025."


def _scored_review(defects):
    from deep_research.utils.types import REVIEW_DIMENSIONS, ReportReview
    dimensions = {dimension: 0.9 for dimension in REVIEW_DIMENSIONS}
    return ReportReview(status="scored", dimensions=dimensions, defects=defects,
                        input_fingerprint="fp-in", composition_fingerprint="fp-out")


@pytest.mark.asyncio
async def test_a_new_iteration_drafts_every_part_fresh_not_as_a_redraft(
    checker, tracker: Tracker, tmp_path: Path,
) -> None:
    """P0-1: an extra research pass is a new iteration, not a writer
    redraft, even though the prior iteration's review still has a material
    defect on file. Carrying that review's defects/previous section into a
    new iteration would silently carry a part over unchanged and drop the
    pass's own new evidence -- only a true redraft hop (the same iteration
    re-run after review) carries them."""
    t1 = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    t2 = make_target("topic-02-target-01", coverage_id="topic-02", required=True)
    f1 = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation=EIA,
                 target_ids=["topic-01-target-01"])
    f2 = _checked("https://a.test/2", "5 GW in 2025.", "5", "GW", organisation=EIA,
                 target_ids=["topic-02-target-01"], kind="forecast", period="2025")
    # The extra pass's own new evidence, not present in the pass-0 review.
    f3 = _checked("https://a.test/3", "12.1 GW in 2024, revised.", "12.1", "GW",
                 organisation=EIA, target_ids=["topic-01-target-01"])
    topics = [_topic("topic-01", "First", [t1]), _topic("topic-02", "Second", [t2])]

    previous_statement_1 = ReportStatement(statement_id="S001", text="10.4 GW in 2024.",
                                           finding_ids=[finding_fingerprint(f1)], target_ids=["topic-01-target-01"])
    previous_section_1 = ReportSection(title="First", coverage_id="topic-01",
                                       points=[ReportPointFor("10.4 GW in 2024.", previous_statement_1)])
    from deep_research.utils.types import ReportComposition
    previous = ReportComposition(question="Q?", session_id="s1", sections=[previous_section_1],
                                 summary=[], sub_topics=topics, iteration=0)
    defect = ReviewDefect(defect_id="review-01", kind="missing_support", severity="major",
                          target_ids=["topic-01-target-01"], problem="Needs the release date.")
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=topics, iteration=1,
                          verified_findings=[f1, f2, f3], composition=previous,
                          report_review=_scored_review([defect]))

    calls: list[str] = []

    def _label_for(body: str, content: str) -> str:
        header = body[: body.index(f"content: {content}")].rsplit("## ", 1)[-1]
        return header.split(":", 1)[0]

    def route(messages, schema):
        body = messages[-1].content
        if schema.__name__ == "BottomLineDraft":
            calls.append("bottom_line")
            return BottomLineDraft(sentences=[])
        if "12.1 GW in 2024, revised." in body:
            calls.append("topic-01")
            label = _label_for(body, "12.1 GW in 2024, revised.")
            return SectionDraft(title="First",
                                points=[WriterPointDraft(text="According to the source, 12.1 GW in 2024, revised.",
                                                        finding_labels=[label])])
        calls.append("topic-02")
        label = _label_for(body, "5 GW in 2025.")
        return SectionDraft(title="Second",
                            points=[WriterPointDraft(text="According to the source, 5 GW in 2025.", finding_labels=[label])])

    completer = ScriptedCompleter(outputs=[route, route, route])
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)

    assert task.defects == []
    assert task.previous is None

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert calls.count("topic-01") == 1
    assert calls.count("topic-02") == 1
    statuses = {p.coverage_id: p.status for p in composition.parts}
    assert statuses["topic-01"] == "written"
    assert statuses["topic-02"] == "written"



@pytest.mark.asyncio
async def test_the_output_is_identical_whatever_order_the_calls_complete_in(checker, tracker: Tracker, tmp_path: Path) -> None:
    t1 = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    t2 = make_target("topic-02-target-01", coverage_id="topic-02", required=True)
    f1 = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation=EIA,
                 target_ids=["topic-01-target-01"])
    f2 = _checked("https://a.test/2", "5 GW in 2025.", "5", "GW", organisation=EIA,
                 target_ids=["topic-02-target-01"], kind="forecast", period="2025")
    topics = [_topic("topic-01", "First", [t1]), _topic("topic-02", "Second", [t2])]
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=topics,
                          verified_findings=[f1, f2])

    async def build(delays: dict[str, float]) -> object:
        async def route(messages, schema):
            body = messages[-1].content
            if schema is BottomLineDraft:
                return BottomLineDraft(sentences=[])
            key = "topic-01" if "10.4 GW" in body else "topic-02"
            await asyncio.sleep(delays[key])
            text = "10.4 GW in 2024." if key == "topic-01" else "5 GW in 2025."
            label = "F01" if key == "topic-01" else "F02"
            title = "First" if key == "topic-01" else "Second"
            return SectionDraft(title=title, points=[WriterPointDraft(text=text, finding_labels=[label])])

        class _AsyncRoutingCompleter:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str | None, list]] = []

            async def complete_react(self, messages, tools, *, agent_name=None, max_tokens=None):
                raise AssertionError("the writer runs no ReAct loop")

            async def complete_structured(self, messages, schema, *, agent_name=None,
                                          max_tokens=None, reasoning_effort=None):
                self.calls.append((schema.__name__, agent_name, list(messages)))
                return await route(messages, schema)

        completer = _AsyncRoutingCompleter()
        agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))
        task = agent.build_task(state)
        return await compose_written_report(task, provider=completer, section_concurrency=7)

    fast_first = await build({"topic-01": 0.0, "topic-02": 0.02})
    slow_first = await build({"topic-01": 0.02, "topic-02": 0.0})

    assert [s.coverage_id for s in fast_first.sections] == [s.coverage_id for s in slow_first.sections]
    assert [p.statement_id for s in fast_first.sections for p in s.points] == \
        [p.statement_id for s in slow_first.sections for p in s.points]
    assert [p.text for s in fast_first.sections for p in s.points] == \
        [p.text for s in slow_first.sections for p in s.points]


@pytest.mark.asyncio
async def test_writer_section_concurrency_bounds_the_drafts_in_flight(checker, tracker: Tracker, tmp_path: Path) -> None:
    targets = [make_target(f"topic-0{n}-target-01", coverage_id=f"topic-0{n}", required=True)
              for n in range(1, 4)]
    findings = [_checked(f"https://a.test/{n}", f"{n} GW in 202{n}.", str(n), "GW", organisation=EIA,
                        target_ids=[f"topic-0{n}-target-01"], period=f"202{n}")
               for n in range(1, 4)]
    topics = [_topic(f"topic-0{n}", f"Part {n}", [targets[n - 1]]) for n in range(1, 4)]
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=topics, verified_findings=findings)

    in_flight = 0
    peak = 0

    class _ConcurrencyProbeCompleter:
        async def complete_react(self, messages, tools, *, agent_name=None, max_tokens=None):
            raise AssertionError("the writer runs no ReAct loop")

        async def complete_structured(self, messages, schema, *, agent_name=None,
                                      max_tokens=None, reasoning_effort=None):
            nonlocal in_flight, peak
            if schema is BottomLineDraft:
                return BottomLineDraft(sentences=[])
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return SectionDraft(title="Part", points=[])

    completer = _ConcurrencyProbeCompleter()
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)

    await compose_written_report(task, provider=completer, section_concurrency=2)

    assert peak <= 2


@pytest.mark.asyncio
async def test_the_statement_check_gate_is_shared_across_every_part_and_the_bottom_line(writer, checker) -> None:
    state = _one_part_state()
    task = writer.build_task(state)
    writer.provider._outputs.extend([
        SectionDraft(title="Capacity added",
                    points=[WriterPointDraft(text="The EIA reported 10.4 GW in 2024.", finding_labels=["F01"])]),
        BottomLineDraft(sentences=[WriterPointDraft(text="The EIA reported 10.4 GW in 2024.", finding_labels=["F01"])]),
    ])

    await compose_written_report(task, provider=writer.provider, concurrency=3, section_concurrency=7)

    assert len(checker.gates) == 2
    assert checker.gates[0] is checker.gates[1]
    assert isinstance(checker.gates[0], asyncio.Semaphore)


# --- D1 fix (a): a point must credit the source it rests on ----------------


def test_consider_section_point_refuses_a_point_that_credits_no_source():
    """Audit D1 fix (a): a bare fact naming neither a credit verb nor a
    name from its cited finding is refused -- run 7's S043, stated as
    settled fact one line after its own refutation, credited to nobody."""
    from deep_research.agents.report_writer import _consider_section_point
    finding = _statement_finding("https://a.test/1", "A claim.", target_ids=[])
    part_labels = {"F01": finding}
    point = WriterPointDraft(text="A bare fact with nobody behind it.", finding_labels=["F01"])
    rejected: list = []

    candidates = _consider_section_point(
        point, "section[topic-01].points[0]", part_labels=part_labels, all_labels=part_labels,
        context_only_labels=set(), numbers=iter(range(1, 100)), rejected=rejected,
        key_prefix="P01.",
    )

    assert candidates == []
    assert rejected[0].reason == "states a fact without crediting the source that states it"


def test_consider_section_point_keeps_a_credited_point_with_the_same_facts():
    """The other branch: the same fact, credited, is kept."""
    from deep_research.agents.report_writer import _consider_section_point
    finding = _statement_finding("https://a.test/1", "A claim.", target_ids=[])
    part_labels = {"F01": finding}
    point = WriterPointDraft(text="According to the source, a bare fact with nobody behind it.",
                             finding_labels=["F01"])
    rejected: list = []

    candidates = _consider_section_point(
        point, "section[topic-01].points[0]", part_labels=part_labels, all_labels=part_labels,
        context_only_labels=set(), numbers=iter(range(1, 100)), rejected=rejected,
        key_prefix="P01.",
    )

    assert len(candidates) == 1
    assert candidates[0].text == "According to the source, a bare fact with nobody behind it."
    assert rejected == []


def test_consider_section_point_refuses_a_point_sharing_only_topic_words_with_the_title():
    """RevZ2 P1-a: run 7's S043 shape -- a page's title always shares the
    subject's own words with a point about it (Fable's audit found the
    old token-overlap guard refused 0 of 46 real statements on exactly
    this: a shared word like "roman" or "bce" used to count as crediting).
    Sharing topic words is not naming a source; the point still credits
    nobody."""
    from deep_research.agents.report_writer import _consider_section_point
    read = make_read("Economic policy changed after new regional reform activity increased.",
                     url="https://a.test/1", title="Economic reform and regional policy change")
    finding = make_finding(read, "Economic policy changed after new regional reform activity increased.",
                           target_ids=[])
    finding = finding.model_copy(update={"verification": FindingVerification(status="verified", figure_results=[])})
    part_labels = {"F01": finding}
    point = WriterPointDraft(
        text="Economic policy changed after new regional reform activity increased.",
        finding_labels=["F01"],
    )
    rejected: list = []

    candidates = _consider_section_point(
        point, "section[topic-01].points[0]", part_labels=part_labels, all_labels=part_labels,
        context_only_labels=set(), numbers=iter(range(1, 100)), rejected=rejected,
        key_prefix="P01.",
    )

    assert candidates == []
    assert rejected[0].reason == "states a fact without crediting the source that states it"


def test_names_a_source_accepts_past_tense_and_per_forms():
    """RevZ2 P2-a: past-tense and "per" credit verbs, added after the
    audit found "The EIA reported ...", "The EIA said ...", "The EIA
    counted ..." and "..., per the EIA." all wrongly refused by the
    present-tense-only list."""
    from deep_research.agents.report_writer import _names_a_source
    assert _names_a_source("The EIA reported 10.4 GW in 2024.", [])
    assert _names_a_source("The EIA said capacity grew by 10.4 GW.", [])
    assert _names_a_source("The EIA counted 10.4 GW of additions.", [])
    assert _names_a_source("Capacity grew by 10.4 GW, per the EIA.", [])


def test_names_a_source_accepts_the_kept_figures_organisation():
    """RevZ2 P2-a: a kept figure's own organisation (the registry line's
    own "organisation ..." clause) names its source, even with no credit
    verb and no title or host overlap."""
    from deep_research.agents.report_writer import _names_a_source
    finding = _checked("https://a.test/1", "A claim.", "10.4", "GW", organisation="Example Institute")
    assert _names_a_source("Example Institute's own figure was 10.4 GW.", [finding])


def test_names_a_source_accepts_the_titles_own_author_work_segment():
    """RevZ2 S005 / ReRevZ2 P1: run 7's exact shape -- a primary-source
    page's title follows the "Author, Work" convention and carries no
    ``attributed_issuer`` of its own, and the point uses the title's
    author segment directly before an authorial verb ("Sallust traced
    the civil war to ..." against the title "Sallust, Catiline's War
    5-16", here with a neutral stand-in)."""
    from deep_research.agents.report_writer import _names_a_source
    read = make_read("A field account.", url="https://a.test/1",
                     title="Jordan Vale, Field Notes on Coastal Erosion")
    finding = make_finding(read, "A field account.", target_ids=[])
    finding = finding.model_copy(update={"verification": FindingVerification(status="verified", figure_results=[])})
    assert _names_a_source(
        "Jordan Vale traced the erosion pattern to a decade of coastal storms.",
        [finding],
    )


def test_names_a_source_refuses_a_titles_lower_case_opening_word_as_an_author():
    """RevZ2 S005 guard: a first segment of one lower-case word (an
    ordinary title's own opening clause, not a name) never counts, the
    same way run 7's S043 stayed refused."""
    from deep_research.agents.report_writer import _names_a_source
    read = make_read("A summary.", url="https://a.test/1",
                     title="overview, a summary of regional findings")
    finding = make_finding(read, "A summary.", target_ids=[])
    finding = finding.model_copy(update={"verification": FindingVerification(status="verified", figure_results=[])})
    assert not _names_a_source(
        "The overview reached no firm conclusion about the trend.", [finding],
    )


def test_names_a_source_refuses_a_title_authors_bare_appearance_without_a_construction():
    """ReRevZ2 P1: a "Surname, Forenames" title (a reference work's own
    entry format, "Vale, Jordan (1901-1960)") makes "Vale" name-shaped,
    but the point must still use it as an author, not merely contain
    it -- run 7's S043 shape again, this time through the title-author
    path P1-a's own fix closed the door on."""
    from deep_research.agents.report_writer import _names_a_source
    read = make_read("A biography.", url="https://a.test/1",
                     title="Vale, Jordan (1901-1960)")
    finding = make_finding(read, "A biography.", target_ids=[])
    finding = finding.model_copy(update={"verification": FindingVerification(status="verified", figure_results=[])})
    assert not _names_a_source("Jordan Vale abolished the fee in 1931.", [finding])


def test_names_a_source_refuses_a_titles_first_word_as_an_unused_topic_name():
    """ReRevZ2 P1: "Climate, the great challenge of our age" is exactly
    as name-shaped as "Sallust, Catiline's War" ("Climate" is one
    capitalised word before the comma), but a point that merely shares
    that topic word, never using it as an author, credits nobody."""
    from deep_research.agents.report_writer import _names_a_source
    read = make_read("A commentary.", url="https://a.test/1",
                     title="Climate, the great challenge of our age")
    finding = make_finding(read, "A commentary.", target_ids=[])
    finding = finding.model_copy(update={"verification": FindingVerification(status="verified", figure_results=[])})
    assert not _names_a_source("Climate policy shifted sharply after the vote.", [finding])


def test_consider_section_point_refuses_a_point_naming_only_a_determiner_adjacent_verb():
    """ReRevZ2 P2: a determiner immediately before the matched word
    makes it the noun, not the verb -- "the dates" is a topic, not a
    source, the same way "the state" and "the report" are; nobody is
    credited."""
    from deep_research.agents.report_writer import _consider_section_point
    finding = _statement_finding("https://a.test/1", "A claim.", target_ids=[])
    part_labels = {"F01": finding}
    point = WriterPointDraft(text="The dates of the change are uncertain.", finding_labels=["F01"])
    rejected: list = []

    candidates = _consider_section_point(
        point, "section[topic-01].points[0]", part_labels=part_labels, all_labels=part_labels,
        context_only_labels=set(), numbers=iter(range(1, 100)), rejected=rejected,
        key_prefix="P01.",
    )

    assert candidates == []
    assert rejected[0].reason == "states a fact without crediting the source that states it"


def test_consider_section_point_refuses_a_point_naming_only_the_united_states():
    """RevZ2 P2-b: case-insensitive matching let "States" inside "United
    States" pass as the verb "states"; case-sensitive matching (plus the
    explicit exclusion) refuses a point that names no real source."""
    from deep_research.agents.report_writer import _consider_section_point
    finding = _statement_finding("https://a.test/1", "A claim.", target_ids=[])
    part_labels = {"F01": finding}
    point = WriterPointDraft(text="The United States added 10 units in 2024.", finding_labels=["F01"])
    rejected: list = []

    candidates = _consider_section_point(
        point, "section[topic-01].points[0]", part_labels=part_labels, all_labels=part_labels,
        context_only_labels=set(), numbers=iter(range(1, 100)), rejected=rejected,
        key_prefix="P01.",
    )

    assert candidates == []
    assert rejected[0].reason == "states a fact without crediting the source that states it"


def test_consider_section_point_refuses_a_sentence_initial_capitalised_common_noun():
    """RevZ2 P2-b: case-insensitive matching also let a sentence-initial
    capitalised common noun ("Records show ...") pass as the credit verb
    "records"; case-sensitive matching refuses it, since nobody is
    actually credited."""
    from deep_research.agents.report_writer import _consider_section_point
    finding = _statement_finding("https://a.test/1", "A claim.", target_ids=[])
    part_labels = {"F01": finding}
    point = WriterPointDraft(text="Records show output rose by 10 units in 2024.", finding_labels=["F01"])
    rejected: list = []

    candidates = _consider_section_point(
        point, "section[topic-01].points[0]", part_labels=part_labels, all_labels=part_labels,
        context_only_labels=set(), numbers=iter(range(1, 100)), rejected=rejected,
        key_prefix="P01.",
    )

    assert candidates == []
    assert rejected[0].reason == "states a fact without crediting the source that states it"


def test_consider_section_point_refuses_a_point_naming_only_the_political_state():
    """Found by the run-7 probe (a follow-on to RevZ2 P1-a/P2-b): "state"
    is the writer's own primary crediting verb ("Wikipedia states the
    crisis was..."), so it must stay in the verb list, but a determiner
    never sits directly in front of a verb -- "the state" is always the
    geopolitical noun, credited to nobody, run 7's S043 shape exactly
    ("... had to be paid by the Roman state.")."""
    from deep_research.agents.report_writer import _consider_section_point
    finding = _statement_finding("https://a.test/1", "A claim.", target_ids=[])
    part_labels = {"F01": finding}
    point = WriterPointDraft(text="Citizens were paid a stipend by the state.", finding_labels=["F01"])
    rejected: list = []

    candidates = _consider_section_point(
        point, "section[topic-01].points[0]", part_labels=part_labels, all_labels=part_labels,
        context_only_labels=set(), numbers=iter(range(1, 100)), rejected=rejected,
        key_prefix="P01.",
    )

    assert candidates == []
    assert rejected[0].reason == "states a fact without crediting the source that states it"


def test_names_a_source_still_accepts_a_named_source_using_states():
    """The generalised exclusion above must not swallow the genuine verb
    usage it sits beside: a named source followed directly by "states"
    is still credited."""
    from deep_research.agents.report_writer import _names_a_source
    assert _names_a_source("Example Institute states the total was 10.4 GW.", [])


def test_names_a_source_accepts_fables_probe_rates():
    """ReRevZ2 R1 (Fable's probe): "rates" is a real credit verb, not
    only the writer's own established list -- a finding whose title has
    no separator and whose host does not name the subject at all, so
    only the verb can credit it."""
    from deep_research.agents.report_writer import _names_a_source
    read = make_read("A review.", url="https://a.test/1", title="A field report")
    finding = make_finding(read, "A review.", target_ids=[])
    finding = finding.model_copy(update={"verification": FindingVerification(status="verified", figure_results=[])})
    assert _names_a_source("Example Tester rates Model A 4.5 out of 5 for noise.", [finding])


def test_names_a_source_accepts_fables_probe_measured():
    """ReRevZ2 R1 (Fable's probe): "measured" credits too."""
    from deep_research.agents.report_writer import _names_a_source
    read = make_read("A review.", url="https://a.test/1", title="A field report")
    finding = make_finding(read, "A review.", target_ids=[])
    finding = finding.model_copy(update={"verification": FindingVerification(status="verified", figure_results=[])})
    assert _names_a_source(
        "Example Institute measured a rise in compliance costs after a 2019 regulation.", [finding],
    )


def test_names_a_source_accepts_fables_probe_attributes():
    """ReRevZ2 R1 (Fable's probe): "attributes" credits too, not only
    inside the title-author construction."""
    from deep_research.agents.report_writer import _names_a_source
    read = make_read("A review.", url="https://a.test/1", title="A field report")
    finding = make_finding(read, "A review.", target_ids=[])
    finding = finding.model_copy(update={"verification": FindingVerification(status="verified", figure_results=[])})
    assert _names_a_source("Example Institute attributes the cost rise to a 2019 regulation.", [finding])


def test_names_a_source_accepts_fables_probe_names():
    """ReRevZ2 R1 (Fable's probe): "names" credits too -- and, matched
    case-sensitively like the rest of the list, never collides with the
    plural noun "names" the way "States"/"Records" would capitalised."""
    from deep_research.agents.report_writer import _names_a_source
    read = make_read("A review.", url="https://a.test/1", title="A field report")
    finding = make_finding(read, "A review.", target_ids=[])
    finding = finding.model_copy(update={"verification": FindingVerification(status="verified", figure_results=[])})
    assert _names_a_source("Example Register names Model B the one to beat for the price.", [finding])


def test_names_a_source_matches_a_hyphenated_host_with_its_spaces_restored():
    """ReRevZ2 R1: "example-institute.test" also yields "example
    institute" -- prose credits an organisation by its own name, never
    by its domain's own hyphenation, so a host-only match needs the
    space back. No credit verb in the point text, so only the host
    match can be keeping it."""
    from deep_research.agents.report_writer import _names_a_source
    read = make_read("A review.", url="https://example-institute.test/report", title="A field report")
    finding = make_finding(read, "A review.", target_ids=[])
    finding = finding.model_copy(update={"verification": FindingVerification(status="verified", figure_results=[])})
    assert _names_a_source("Example Institute's own figure was 10.4 GW.", [finding])


def test_names_a_source_refuses_an_action_verb_with_an_ordinary_subject():
    """ReRevZ2b P1 on R1: the R1 action verbs (rates, names, measured,
    ...) are ordinary action verbs any subject can take -- unlike the
    established reporting verbs, "isn't a bare determiner" let ordinary
    prose through uncredited. None of these names anyone."""
    from deep_research.agents.report_writer import _names_a_source
    assert not _names_a_source("The data shows a steady increase.", [])
    assert not _names_a_source("Rome holds the record for the longest aqueduct.", [])
    assert not _names_a_source("Output measures rose sharply after the reform.", [])
    assert not _names_a_source("Marius claims land for his veterans.", [])
    assert not _names_a_source("The report lists three main causes.", [])


def test_names_a_source_accepts_an_action_verb_with_a_name_shaped_subject():
    """The other branch: a name-shaped subject before an action verb
    credits -- an all-caps acronym ("EIA"), or two or more consecutive
    capitalised words even when not the sentence's own first word
    ("In 2024, Example Institute measured ...")."""
    from deep_research.agents.report_writer import _names_a_source
    assert _names_a_source("The EIA measured a rise in storage capacity.", [])
    assert _names_a_source("In 2024, Example Institute measured a rise in storage capacity.", [])


# --- D12: no finding labels leak into point text ----------------------------


def test_consider_section_point_strips_a_parenthesised_label_group_without_refusing():
    """D12: a leaked label group is stripped, together with the space
    before it, and the point survives -- never refused for it."""
    from deep_research.agents.report_writer import _consider_section_point
    finding_18 = _statement_finding("https://a.test/18", "Pressure one.", target_ids=[])
    finding_28 = _statement_finding("https://a.test/28", "Pressure two.", target_ids=[])
    part_labels = {"F18": finding_18, "F28": finding_28}
    point = WriterPointDraft(
        text="According to the source, structural pressures built over decades (F18, F28). "
             "Next sentence continues.",
        finding_labels=["F18", "F28"],
    )
    rejected: list = []

    candidates = _consider_section_point(
        point, "section[topic-01].points[0]", part_labels=part_labels, all_labels=part_labels,
        context_only_labels=set(), numbers=iter(range(1, 100)), rejected=rejected,
        key_prefix="P01.",
    )

    assert len(candidates) == 1
    assert candidates[0].text == ("According to the source, structural pressures built over decades. "
                                  "Next sentence continues.")
    assert candidates[0].had_label_group is True
    assert rejected == []


def test_consider_section_point_strips_a_three_digit_label_group():
    from deep_research.agents.report_writer import _consider_section_point
    finding = _statement_finding("https://a.test/161", "A claim.", target_ids=[])
    part_labels = {"F161": finding}
    point = WriterPointDraft(text="According to the source, a claim about the topic (F161).",
                             finding_labels=["F161"])
    rejected: list = []

    candidates = _consider_section_point(
        point, "section[topic-01].points[0]", part_labels=part_labels, all_labels=part_labels,
        context_only_labels=set(), numbers=iter(range(1, 100)), rejected=rejected,
        key_prefix="P01.",
    )

    assert candidates[0].text == "According to the source, a claim about the topic."
    assert candidates[0].had_label_group is True


def test_consider_bottom_line_point_strips_a_parenthesised_label_group_without_refusing():
    from deep_research.agents.report_writer import _consider_bottom_line_point
    finding = _statement_finding("https://a.test/18", "Pressure one.", target_ids=[])
    cited_by_sections = {"F18": finding}
    point = WriterPointDraft(
        text="Structural pressures built over decades (F18). Next clause continues.",
        finding_labels=["F18"],
    )
    rejected: list = []

    candidates = _consider_bottom_line_point(
        point, "bottom_line[0]", cited_by_sections=cited_by_sections, disputed_labels=set(),
        numbers=iter(range(1, 100)), rejected=rejected, key_prefix="B",
    )

    assert len(candidates) == 1
    assert candidates[0].text == "Structural pressures built over decades. Next clause continues."
    assert candidates[0].had_label_group is True
    assert rejected == []


# --- Z1/Z2 item 4: the disputed-target guard --------------------------------


def test_consider_bottom_line_point_refuses_a_disputed_label_with_no_difference_marker():
    """Audit D1 fix (b), ReRevZ2 C7: a sentence that cites a finding a
    marked point disputes, with no marker of difference, is refused --
    run 7's D1 (the disputed step carried into the bottom line as
    settled fact). Label-scoped, not target-scoped."""
    from deep_research.agents.report_writer import _consider_bottom_line_point
    finding = _statement_finding("https://a.test/1", "A claim.", target_ids=["topic-01-target-01"])
    cited_by_sections = {"F01": finding}
    point = WriterPointDraft(text="According to the source, the step happened.",
                             finding_labels=["F01"])
    rejected: list = []

    candidates = _consider_bottom_line_point(
        point, "bottom_line[0]", cited_by_sections=cited_by_sections,
        disputed_labels={"F01"},
        numbers=iter(range(1, 100)), rejected=rejected, key_prefix="B",
    )

    assert candidates == []
    assert rejected[0].reason == "states a disputed step without its dispute"


def test_consider_bottom_line_point_keeps_a_disputed_label_sentence_with_a_difference_marker():
    """The other branch: the same disputed label, with a difference
    marker, is kept."""
    from deep_research.agents.report_writer import _consider_bottom_line_point
    finding = _statement_finding("https://a.test/1", "A claim.", target_ids=["topic-01-target-01"])
    cited_by_sections = {"F01": finding}
    point = WriterPointDraft(
        text="According to the source, the step happened, though others dispute it.",
        finding_labels=["F01"],
    )
    rejected: list = []

    candidates = _consider_bottom_line_point(
        point, "bottom_line[0]", cited_by_sections=cited_by_sections,
        disputed_labels={"F01"},
        numbers=iter(range(1, 100)), rejected=rejected, key_prefix="B",
    )

    assert len(candidates) == 1
    assert rejected == []


def test_consider_bottom_line_point_keeps_a_disputed_label_sentence_using_disputes():
    """RevZ2 P1-b: the marker regex matches stems, so "disputes" (not
    only bare "dispute") counts as a difference marker."""
    from deep_research.agents.report_writer import _consider_bottom_line_point
    finding = _statement_finding("https://a.test/1", "A claim.", target_ids=["topic-01-target-01"])
    cited_by_sections = {"F01": finding}
    point = WriterPointDraft(
        text="According to the source, a later account disputes the earlier figure of 10.4 GW.",
        finding_labels=["F01"],
    )
    rejected: list = []

    candidates = _consider_bottom_line_point(
        point, "bottom_line[0]", cited_by_sections=cited_by_sections,
        disputed_labels={"F01"},
        numbers=iter(range(1, 100)), rejected=rejected, key_prefix="B",
    )

    assert len(candidates) == 1
    assert rejected == []


def test_consider_bottom_line_point_keeps_a_disputed_label_sentence_using_differently():
    """RevZ2 P1-b: "differently" (not only bare "differ") counts too."""
    from deep_research.agents.report_writer import _consider_bottom_line_point
    finding = _statement_finding("https://a.test/1", "A claim.", target_ids=["topic-01-target-01"])
    cited_by_sections = {"F01": finding}
    point = WriterPointDraft(
        text="According to the source, accounts date the event differently.",
        finding_labels=["F01"],
    )
    rejected: list = []

    candidates = _consider_bottom_line_point(
        point, "bottom_line[0]", cited_by_sections=cited_by_sections,
        disputed_labels={"F01"},
        numbers=iter(range(1, 100)), rejected=rejected, key_prefix="B",
    )

    assert len(candidates) == 1
    assert rejected == []


def test_bottom_line_messages_lists_a_marked_statement_under_disputed_first():
    """Audit D1 fix (b), ReRevZ2 C7: a checked statement the writer
    marked ``disputes: true`` is listed again under "Disputed", first,
    as the dispute itself -- so the bottom line can state both sides or
    leave the step out."""
    task = _one_target_task()
    statement = ReportStatement(statement_id="S001", text="According to the source, 10.4 GW in 2024.",
                                finding_ids=[finding_fingerprint(task.findings[0])],
                                target_ids=["topic-01-target-01"])
    section = ReportSection(title="Capacity added", coverage_id="topic-01",
                            points=[ReportPointFor("According to the source, 10.4 GW in 2024.", statement)])

    body = bottom_line_messages(task, [section], disputed_statement_ids=frozenset({"S001"}))[-1].content

    assert "# Disputed: state both sides, or leave the disputed step, figure or provision out" in body
    assert "The dispute, as a section point states it:" in body
    assert "According to the source, 10.4 GW in 2024." in body.split("# Disputed:")[1]


def test_bottom_line_messages_omit_the_disputed_heading_when_nothing_disputes():
    task = _one_target_task()
    statement = ReportStatement(statement_id="S001", text="According to the source, 10.4 GW in 2024.",
                                finding_ids=[finding_fingerprint(task.findings[0])],
                                target_ids=["topic-01-target-01"])
    section = ReportSection(title="Capacity added", coverage_id="topic-01",
                            points=[ReportPointFor("According to the source, 10.4 GW in 2024.", statement)])

    body = bottom_line_messages(task, [section])[-1].content

    assert "# Disputed:" not in body



def test_bottom_line_messages_lists_a_marked_statement_under_outcome():
    """Run-8 D4/D5 (c): a checked statement the writer marked
    ``outcome: true`` is listed under "# Outcome" so the bottom line can
    end on it, credited and dated as it states it."""
    task = _one_target_task()
    statement = ReportStatement(statement_id="S001", text="According to the source, 10.4 GW in 2024.",
                                finding_ids=[finding_fingerprint(task.findings[0])],
                                target_ids=["topic-01-target-01"])
    section = ReportSection(title="Capacity added", coverage_id="topic-01",
                            points=[ReportPointFor("According to the source, 10.4 GW in 2024.", statement)])

    body = bottom_line_messages(task, [section], outcome_statement_ids=frozenset({"S001"}))[-1].content

    assert "# Outcome\n" in body
    assert "According to the source, 10.4 GW in 2024." in body.split("# Outcome\n")[1]


def test_bottom_line_dispute_scope_is_the_labels_the_marked_point_cites_not_its_target():
    """ReRevZ2 C7's own proving test: a part with a marked point citing
    F01+F02 and an undisputed dating point citing F03 on the same
    target. A bottom-line sentence citing F03 alone is kept without a
    marker; one citing F01 without a marker is refused; and the block
    lists the marked point first, with F03 nowhere in it -- target
    scoping would have listed F03 too and refused a sentence stating it
    alone (run 7's topic-01 wrote 13 points on one target)."""
    from deep_research.agents.report_writer import _consider_bottom_line_point
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    finding_1 = _statement_finding("https://a.test/1", "The annual assessment states enrollment fell steeply.",
                                   target_ids=["topic-01-target-01"])
    finding_2 = _statement_finding(
        "https://b.test/1",
        "Field Review states later field surveys found little evidence for that decline.",
        target_ids=["topic-01-target-01"],
    )
    finding_3 = _statement_finding("https://c.test/1", "Example Register records that the transport subsidy ended in March 2016.",
                                   target_ids=["topic-01-target-01"])
    task = ReportWriterTask(
        instruction="Q?", session_id="s1", question="Q?", as_of="2026-08-01",
        sub_topics=[_topic("topic-01", "Outreach", [target])],
        targets=[target], findings=[finding_1, finding_2, finding_3], sources=[],
        registry=finding_registry([finding_1, finding_2, finding_3], [target]),
        answered={target.target_id: [finding_fingerprint(finding_1)]},
        not_found=[], defects=[], previous=None,
    )
    cited_by_sections = {"F01": finding_1, "F02": finding_2, "F03": finding_3}
    disputed_labels = {"F01", "F02"}

    kept_rejected: list = []
    kept = _consider_bottom_line_point(
        WriterPointDraft(
            text="Example Register records that the transport subsidy ended in March 2016.",
            finding_labels=["F03"],
        ),
        "bottom_line[0]", cited_by_sections=cited_by_sections, disputed_labels=disputed_labels,
        numbers=iter(range(1, 100)), rejected=kept_rejected, key_prefix="B",
    )
    assert len(kept) == 1
    assert kept_rejected == []

    refused_rejected: list = []
    refused = _consider_bottom_line_point(
        WriterPointDraft(text="According to the source, enrollment fell steeply.", finding_labels=["F01"]),
        "bottom_line[0]", cited_by_sections=cited_by_sections, disputed_labels=disputed_labels,
        numbers=iter(range(1, 100)), rejected=refused_rejected, key_prefix="B",
    )
    assert refused == []
    assert refused_rejected[0].reason == "states a disputed step without its dispute"

    marked_text = (
        "The annual assessment states enrollment fell steeply, while Field Review "
        "states later field surveys found little evidence for that decline."
    )
    dating_text = "Example Register records that the transport subsidy ended in March 2016."
    marked_statement = ReportStatement(
        statement_id="S001", text=marked_text,
        finding_ids=[finding_fingerprint(finding_1), finding_fingerprint(finding_2)],
        target_ids=["topic-01-target-01"],
    )
    dating_statement = ReportStatement(
        statement_id="S002", text=dating_text, finding_ids=[finding_fingerprint(finding_3)],
        target_ids=["topic-01-target-01"],
    )
    section = ReportSection(title="Outreach", coverage_id="topic-01", points=[
        ReportPointFor(marked_text, marked_statement),
        ReportPointFor(dating_text, dating_statement),
    ])

    body = bottom_line_messages(task, [section], disputed_statement_ids=frozenset({"S001"}))[-1].content
    dispute_block = body.split("# Disputed:")[1]
    dispute_group = dispute_block.split("The dispute, as a section point states it:")[1]
    assert marked_text in dispute_group.split("Statements that cite")[0]
    assert dating_text not in dispute_block


def test_bottom_line_disputed_labels_is_the_exclusive_hop_not_the_plain_one():
    """ReRevZ2 C11 (Fable's second pass): the one-hop expansion must be
    exclusive -- a sharing statement's own label joins ``disputed_labels``
    only when no *other* checked statement (neither a marked point nor
    itself a sharing statement) also cites it, or the guard reaches one
    hop further than the "Disputed" block itself lists. Statements:
    marked S001 (F01+F02); sharing S004 (F01+F03), where F03 is also
    cited by the non-sharing S002 alone; sharing S006 (F01+F06), which
    nothing else cites. A bottom-line sentence citing F03 alone is kept
    (F03 sits outside the dispute); one citing F06 alone is refused;
    one citing F01 is refused; one citing F05 (cited nowhere) is kept.
    The block lists S001, S004 and S006, never S002."""
    from deep_research.agents.report_writer import _bottom_line_disputed_labels, _consider_bottom_line_point
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    finding_1 = _statement_finding("https://a.test/1", "The annual assessment states enrollment fell steeply.",
                                   target_ids=["topic-01-target-01"])
    finding_2 = _statement_finding(
        "https://b.test/1",
        "Field Review states later field surveys found little evidence for that decline.",
        target_ids=["topic-01-target-01"],
    )
    finding_3 = _statement_finding("https://c.test/1", "Example Register records that the transport subsidy ended in March 2016.",
                                   target_ids=["topic-01-target-01"])
    finding_4 = _statement_finding("https://d.test/1", "A newsletter dates the programme's launch to 2015.",
                                   target_ids=["topic-01-target-01"])
    finding_5 = _statement_finding("https://e.test/1", "A separate report notes staffing levels rose in 2018.",
                                   target_ids=["topic-01-target-01"])
    task = ReportWriterTask(
        instruction="Q?", session_id="s1", question="Q?", as_of="2026-08-01",
        sub_topics=[_topic("topic-01", "Outreach", [target])],
        targets=[target], findings=[finding_1, finding_2, finding_3, finding_4, finding_5], sources=[],
        registry=finding_registry([finding_1, finding_2, finding_3, finding_4, finding_5], [target]),
        answered={target.target_id: [finding_fingerprint(finding_1)]},
        not_found=[], defects=[], previous=None,
    )
    label_by_finding_id = {
        finding_fingerprint(f): label for label, f in task.registry
    }
    cited_by_sections = dict(task.registry)

    marked_text = (
        "The annual assessment states enrollment fell steeply, while Field Review "
        "states later field surveys found little evidence for that decline."
    )
    non_sharing_text = "Example Register records that the transport subsidy ended in March 2016."
    sharing_one_text = (
        "Example Register also records that the transport subsidy overlapped with "
        "when enrollment fell steeply, according to the annual assessment."
    )
    sharing_two_text = (
        "A newsletter dates the programme's launch to 2015, according to the "
        "annual assessment."
    )
    marked_statement = ReportStatement(
        statement_id="S001", text=marked_text,
        finding_ids=[finding_fingerprint(finding_1), finding_fingerprint(finding_2)],
        target_ids=["topic-01-target-01"],
    )
    non_sharing_statement = ReportStatement(
        statement_id="S002", text=non_sharing_text, finding_ids=[finding_fingerprint(finding_3)],
        target_ids=["topic-01-target-01"],
    )
    sharing_one_statement = ReportStatement(
        statement_id="S004", text=sharing_one_text,
        finding_ids=[finding_fingerprint(finding_1), finding_fingerprint(finding_3)],
        target_ids=["topic-01-target-01"],
    )
    sharing_two_statement = ReportStatement(
        statement_id="S006", text=sharing_two_text,
        finding_ids=[finding_fingerprint(finding_1), finding_fingerprint(finding_4)],
        target_ids=["topic-01-target-01"],
    )
    section = ReportSection(title="Outreach", coverage_id="topic-01", points=[
        ReportPointFor(marked_text, marked_statement),
        ReportPointFor(non_sharing_text, non_sharing_statement),
        ReportPointFor(sharing_one_text, sharing_one_statement),
        ReportPointFor(sharing_two_text, sharing_two_statement),
    ])

    disputed_labels = _bottom_line_disputed_labels(
        [section], label_by_finding_id, marked_labels=frozenset({"F01", "F02"}),
        marked_statement_ids=frozenset({"S001"}),
    )
    assert disputed_labels == frozenset({"F01", "F02", "F04"})

    def _try(text: str, labels: list[str]) -> tuple[list, list]:
        rejected: list = []
        candidates = _consider_bottom_line_point(
            WriterPointDraft(text=text, finding_labels=labels),
            "bottom_line[0]", cited_by_sections=cited_by_sections, disputed_labels=disputed_labels,
            numbers=iter(range(1, 100)), rejected=rejected, key_prefix="B",
        )
        return candidates, rejected

    kept_f03, rejected_f03 = _try(non_sharing_text, ["F03"])
    assert len(kept_f03) == 1 and rejected_f03 == []

    kept_f06, rejected_f06 = _try("A newsletter dates the programme's launch to 2015.", ["F04"])
    assert kept_f06 == []
    assert rejected_f06[0].reason == "states a disputed step without its dispute"

    kept_f01, rejected_f01 = _try("According to the source, enrollment fell steeply.", ["F01"])
    assert kept_f01 == []
    assert rejected_f01[0].reason == "states a disputed step without its dispute"

    kept_f05, rejected_f05 = _try(
        "A separate report notes staffing levels rose in 2018.", ["F05"],
    )
    assert len(kept_f05) == 1 and rejected_f05 == []

    marked_statement_ids = frozenset({"S001"})
    body = bottom_line_messages(task, [section], disputed_statement_ids=marked_statement_ids)[-1].content
    dispute_block = body.split("# Disputed:")[1]
    dispute_group = dispute_block.split("The dispute, as a section point states it:")[1]
    lead = dispute_group.split("Statements that cite")[0]
    assert marked_text in lead
    rest = dispute_block.split("Statements that cite")[1]
    assert sharing_one_text in rest and sharing_two_text in rest
    assert non_sharing_text not in dispute_block


@pytest.mark.asyncio
async def test_a_writer_marked_disputing_point_guards_the_bottom_line(writer, checker) -> None:
    """Z1/Z2 items 3-4 end to end, and RevZ2 P1-c: a section point the
    writer marks ``disputes: true`` makes its own target's bottom-line
    sentence need a difference marker; a first draft with none is
    refused, which triggers the one re-ask, and the re-ask's own marked
    sentence is adopted (the same loss D1/D2's re-ask was built to
    prevent, this time from the disputed-step guard rather than the
    Statement Check)."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    finding = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation=EIA,
                       target_ids=["topic-01-target-01"])
    topic = _topic("topic-01", "Capacity added", [target])
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=[topic],
                          verified_findings=[finding])
    task = writer.build_task(state)
    assert finding.disputes is False
    writer.provider._outputs.extend([
        SectionDraft(title="Capacity added", points=[
            WriterPointDraft(text="According to the source, sources disagree about the figure.",
                             finding_labels=["F01"], disputes=True),
        ]),
        BottomLineDraft(sentences=[WriterPointDraft(
            text="According to the source, the figure was 10.4 GW in 2024.", finding_labels=["F01"])]),
        BottomLineDraft(sentences=[WriterPointDraft(
            text="According to the source, the figure differs from an earlier account, at 10.4 GW in 2024.",
            finding_labels=["F01"])]),
    ])

    composition = await compose_written_report(task, provider=writer.provider, section_concurrency=7)

    bottom_line_texts = [point.text for point in composition.summary]
    assert "According to the source, the figure was 10.4 GW in 2024." not in bottom_line_texts
    assert ("According to the source, the figure differs from an earlier account, at 10.4 GW in 2024."
           in bottom_line_texts)
    assert any(
        r.reason == "states a disputed step without its dispute" for r in composition.rejected_points
    )


def test_finalize_candidate_never_records_a_strip_note_for_a_refused_point():
    """P3-3: a refused candidate's key never reaches a printed S-id, so its
    strip note must never be recorded -- an unconditional note would orphan
    itself under the evidence log's dropped-marks heading."""
    from deep_research.agents.report_writer import _Candidate, _finalize_candidate
    finding = _statement_finding("https://a.test/1", "A claim.", target_ids=[])
    candidate = _Candidate(key="B01", where="bottom_line[0]", text="A claim.",
                           finding_labels=["F01"], findings=[finding], items=[],
                           had_label_group=True)
    dropped_marks: list = []
    verdicts = {"B01": _verdict("inconsistent", reason="not supported")}

    point, _ = _finalize_candidate(
        candidate, verdicts, stated_rows=set(), task_facts=[], task_targets=[],
        label_urls={}, label_finding_ids={}, dropped_marks=dropped_marks, rejected=[],
    )

    assert point is None
    assert dropped_marks == []


def test_finalize_candidate_records_the_strip_note_for_a_kept_point():
    from deep_research.agents.report_writer import _Candidate, _finalize_candidate
    finding = _statement_finding("https://a.test/1", "A claim.", target_ids=[])
    candidate = _Candidate(key="B01", where="bottom_line[0]", text="A claim.",
                           finding_labels=["F01"], findings=[finding], items=[],
                           had_label_group=True)
    dropped_marks: list = []
    verdicts = {"B01": _verdict("consistent")}

    point, _ = _finalize_candidate(
        candidate, verdicts, stated_rows=set(), task_facts=[], task_targets=[],
        label_urls={}, label_finding_ids={}, dropped_marks=dropped_marks, rejected=[],
    )

    assert point is not None
    assert dropped_marks == [("B01", "stripped a finding label from the point's text")]


@pytest.mark.asyncio
async def test_a_leaked_label_group_is_stripped_from_the_printed_point(writer, checker) -> None:
    """D12: the compose pipeline strips a leaked label group from a
    point's printed text end to end, without refusing the point."""
    state = _one_part_state()
    task = writer.build_task(state)
    writer.provider._outputs.extend([
        SectionDraft(title="Capacity added", points=[
            WriterPointDraft(text="According to the source, capacity grew steadily across the period (F01).",
                             finding_labels=["F01"]),
        ]),
        BottomLineDraft(sentences=[]),
    ])

    composition = await compose_written_report(task, provider=writer.provider, section_concurrency=7)

    section_texts = [point.text for section in composition.sections for point in section.points]
    bottom_line_texts = [point.text for point in composition.summary]
    printed = (section_texts + bottom_line_texts)[0]
    assert printed == "According to the source, capacity grew steadily across the period."
    assert composition.dropped_marks != []


# --- verdict application, still per-part -----------------------------------


@pytest.mark.asyncio
async def test_a_corrected_verdict_replaces_the_sentence(writer, checker) -> None:
    state = _one_part_state()
    task = writer.build_task(state)
    checker.verdicts["P01.01"] = _verdict("corrected", corrected_text="10.4 GW, corrected.")
    writer.provider._outputs.extend([
        SectionDraft(title="Capacity added",
                    points=[WriterPointDraft(text="According to the source, 10.4 GW in 2024.", finding_labels=["F01"])]),
        BottomLineDraft(sentences=[WriterPointDraft(text="10.4 GW, corrected.", finding_labels=["F01"])]),
    ])

    composition = await compose_written_report(task, provider=writer.provider)

    assert composition.sections[0].points[0].text == "10.4 GW, corrected."


@pytest.mark.asyncio
async def test_a_title_with_a_digit_falls_back_to_the_sub_topic_title(writer, checker) -> None:
    """Whole-branch review P2-1: a drafted title prints raw and becomes an
    options-table column header, so a quantity in it falls back to the
    sub-topic's own title instead of printing unchecked."""
    state = _one_part_state()
    task = writer.build_task(state)
    writer.provider._outputs.extend([
        SectionDraft(title="65 GW by 2027",
                    points=[WriterPointDraft(text="According to the source, 10.4 GW in 2024.", finding_labels=["F01"])]),
        BottomLineDraft(sentences=[WriterPointDraft(text="According to the source, 10.4 GW in 2024.", finding_labels=["F01"])]),
    ])

    composition = await compose_written_report(task, provider=writer.provider)

    assert composition.sections[0].title == "Capacity added"


@pytest.mark.asyncio
async def test_a_title_with_a_verdict_word_falls_back_to_the_sub_topic_title(writer, checker) -> None:
    """Whole-branch review P2-1: a verdict word in a drafted title (a small
    general lexicon) falls back the same way a quantity does."""
    state = _one_part_state()
    task = writer.build_task(state)
    writer.provider._outputs.extend([
        SectionDraft(title="The Best Option",
                    points=[WriterPointDraft(text="According to the source, 10.4 GW in 2024.", finding_labels=["F01"])]),
        BottomLineDraft(sentences=[WriterPointDraft(text="According to the source, 10.4 GW in 2024.", finding_labels=["F01"])]),
    ])

    composition = await compose_written_report(task, provider=writer.provider)

    assert composition.sections[0].title == "Capacity added"


def test_section_title_cuts_at_a_word_boundary_not_mid_word():
    """Whole-branch review P2-1: spec §6.4 rule 7's 80-character bound is a
    word-boundary cut, never a mid-word one."""
    from deep_research.agents.report_writer import _section_title
    long_title = ("A" * 75) + " " + ("B" * 20)

    title = _section_title(long_title, "Fallback title")

    assert title == "A" * 75



@pytest.mark.asyncio
async def test_an_inconsistent_verdict_refuses_the_point(writer, checker) -> None:
    state = _one_part_state()
    task = writer.build_task(state)
    checker.verdicts["P01.01"] = _verdict("inconsistent", reason="not in the findings")
    writer.provider._outputs.extend([
        SectionDraft(title="Capacity added",
                    points=[WriterPointDraft(text="According to the source, 10.4 GW in 2024.", finding_labels=["F01"])]),
        BottomLineDraft(sentences=[]),
    ])

    composition = await compose_written_report(task, provider=writer.provider)

    assert composition.sections == []
    assert composition.rejected_points[0].reason == "not in the findings"
    # P1-3: a part whose every drafted point was refused is undrafted, not
    # silently "written" with nothing to show -- the renderer's per-part and
    # every-part-failed disclosures both key off this.
    assert composition.parts[0].status == "failed"


@pytest.mark.asyncio
async def test_a_report_with_only_refused_points_discloses_it_not_the_no_source_fallback(
    writer, checker,
) -> None:
    """P1-3 repro (b): with a verified finding on file but every drafted
    point refused, the report must say its sections could not be written --
    never the §10 "no source we could check answers this question" sentence,
    which is reserved for a pass that cites nothing at all."""
    state = _one_part_state()
    task = writer.build_task(state)
    checker.verdicts["P01.01"] = _verdict("inconsistent", reason="not in the findings")
    writer.provider._outputs.extend([
        SectionDraft(title="Capacity added",
                    points=[WriterPointDraft(text="According to the source, 10.4 GW in 2024.", finding_labels=["F01"])]),
        BottomLineDraft(sentences=[]),
    ])

    composition = await compose_written_report(task, provider=writer.provider)
    markdown = render_written_report(composition)

    assert "No source we could check answers this question." not in markdown
    assert "could not be written this time" in markdown


@pytest.mark.asyncio
async def test_all_parts_refused_is_recoverable_and_the_run_still_finishes(
    writer, checker, tracker: Tracker,
) -> None:
    """R-5: every drafted point being refused is a content outcome, not a
    provider failure -- the composition's error must be accurately named and
    recoverable, and the synthetic ReActRun must not report
    stop_reason='provider_error' (agents.steps.ReActRun.succeeded reads that
    exact value as a non-recoverable provider failure)."""
    state = _one_part_state()
    checker.verdicts["P01.01"] = _verdict("inconsistent", reason="not in the findings")
    writer.provider._outputs.extend([
        SectionDraft(title="Capacity added",
                    points=[WriterPointDraft(text="According to the source, 10.4 GW in 2024.", finding_labels=["F01"])]),
        BottomLineDraft(sentences=[]),
    ])

    async with tracker.session_span(state.session_id, state.original_question):
        run = await writer.run(state)

    error_types = [e.error_type for e in run.result.composition.errors]
    assert "report_writer_provider_error" not in error_types
    assert "report_writer_all_parts_refused" in error_types
    refused_error = next(
        e for e in run.result.composition.errors if e.error_type == "report_writer_all_parts_refused"
    )
    assert refused_error.recoverable is True
    assert run.react.stop_reason == "finished"
    assert run.react.succeeded


@pytest.mark.asyncio
async def test_a_genuine_provider_failure_on_every_part_keeps_the_provider_error(
    writer, checker, tracker: Tracker, tmp_path: Path,
) -> None:
    """R-5: a real provider/draft failure on every part must still be the
    non-recoverable report_writer_provider_error, with stop_reason
    'provider_error' -- only the refused-content case changes."""
    def route(messages, schema):
        del messages, schema
        raise ProviderResponseError("provider returned an HTTP error", retryable=True,
                                    failure_category="http", http_status_code=503, failure_origin="sdk")

    state = _one_part_state()
    completer = ScriptedCompleter(outputs=[route] * 10)
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))

    async with tracker.session_span(state.session_id, state.original_question):
        run = await agent.run(state)

    error_types = [e.error_type for e in run.result.composition.errors]
    assert "report_writer_all_parts_refused" not in error_types
    assert "report_writer_provider_error" in error_types
    provider_error = next(
        e for e in run.result.composition.errors if e.error_type == "report_writer_provider_error"
    )
    assert provider_error.recoverable is False
    assert run.react.stop_reason == "provider_error"
    assert not run.react.succeeded


@pytest.mark.asyncio
async def test_a_point_citing_no_known_label_is_refused_without_calling_the_checker(writer, checker) -> None:
    state = _one_part_state()
    task = writer.build_task(state)
    writer.provider._outputs.extend([
        SectionDraft(title="Capacity added",
                    points=[WriterPointDraft(text="According to the source, 10.4 GW in 2024.", finding_labels=[])]),
        BottomLineDraft(sentences=[]),
    ])

    composition = await compose_written_report(task, provider=writer.provider)

    assert composition.rejected_points[0].reason == "cites no checked finding"
    assert checker.calls == []


@pytest.mark.asyncio
async def test_statement_target_ids_exclude_a_fallback_only_answer(tmp_path: Path) -> None:
    """Spec §6.13: a statement's target_ids are explicit bindings only."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True,
                         question="How much battery storage capacity was added in the United States in 2024?")
    read = make_read("The EIA reported that storage capacity grew by 10.4 GW in the United States in 2024.",
                     url="https://a.test/1")
    finding = make_finding(read, "The EIA reported that storage capacity grew by 10.4 GW in the United States in 2024.",
                           figures=[figure("10.4", "GW", "2024", "actual")], target_ids=[])
    finding = finding.model_copy(update={"related_sub_topic": "Capacity added"})
    result = FigureResultFor(finding, organisation=EIA, attribution="own", kind="actual", period="2024")
    finding = finding.model_copy(update={"verification": FindingVerification(status="verified", figure_results=[result])})
    topic = _topic("topic-01", "Capacity added", [target])
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=[topic], verified_findings=[finding])
    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Capacity added",
                    points=[WriterPointDraft(
                        text="The EIA reported that storage capacity grew by 10.4 GW in the United States in 2024.",
                        finding_labels=["F01"])]),
        BottomLineDraft(sentences=[WriterPointDraft(
            text="The EIA reported that storage capacity grew by 10.4 GW in the United States in 2024.",
            finding_labels=["F01"])]),
    ])
    from deep_research.agents.evidence_verifier import StatementCheckItem
    import deep_research.agents.evidence_verifier as ev

    async def fake_check(provider, items, *, question, fingerprint=None, batch_size=None, concurrency=None, gate=None):
        return {item.label: _verdict("consistent") for item in items}, []

    original = ev.check_statements
    ev.check_statements = fake_check
    try:
        tracker = Tracker(_offline_langsmith())
        agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))
        task = agent.build_task(state)
        composition = await compose_written_report(task, provider=completer)
    finally:
        ev.check_statements = original

    assert composition.sections[0].points[0].statement.target_ids == []


def _offline_langsmith():
    from deep_research.observability import LangSmithRuntimeConfig
    return LangSmithRuntimeConfig(tracing_enabled=False)


@pytest.mark.asyncio
async def test_build_task_excludes_a_context_only_answer_from_the_not_found_computation(checker, tmp_path: Path) -> None:
    """Spec §6.13: a required target answered only by a context-only finding
    is listed under What we couldn't confirm, though the gate's own answered
    set still includes it."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    # This finding's content must actually state the target's own words to
    # answer it through the sub-topic fallback (D9's content-states-target gate).
    read = make_read("How much battery storage capacity was added in the United States in 2024?",
                     url="https://a.test/9")
    context_finding = make_finding(
        read, "How much battery storage capacity was added in the United States in 2024?",
        target_ids=[],
    )
    context_finding = context_finding.model_copy(update={"related_sub_topic": "Capacity added"})
    context_finding = context_finding.model_copy(
        update={"verification": FindingVerification(status="verified", figure_results=[])})
    source = _low_relevance_source("https://a.test/9", relevance=0.2)
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True,
                         question="How much battery storage capacity was added in the United States in 2024?",
                         unit_dimension=None)
    topic = _topic("topic-01", "Capacity added", [target])
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=[topic],
                          verified_findings=[context_finding], evaluated_sources=[source])

    completer = ScriptedCompleter()
    tracker = Tracker(_offline_langsmith())
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))

    task = agent.build_task(state)

    assert target.target_id in task.answered
    assert any(nf.target_id == target.target_id for nf in task.not_found)


def test_build_task_resolves_the_reader_length_from_the_config_default(writer) -> None:
    """D11: with no frozen word limit, the config default feeds the point budget."""
    state = _one_part_state()

    task = writer.build_task(state)

    assert task.target_words == 2000


def test_build_task_resolves_the_reader_length_from_the_contracts_word_limit(
    tracker: Tracker, tmp_path: Path,
) -> None:
    """D11: a frozen requested word limit overrides the config default."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    finding = _checked("https://a.test/1", "The EIA reported 10.4 GW in 2024.", "10.4", "GW",
                       organisation=EIA)
    topic = _topic("topic-01", "Capacity added", [target])
    contract = AnswerContract(
        question="Q?", scope_statement="Answered for the United States as of 2026-09-24.",
        geographic_scope="United States", as_of_date="2026-09-24",
        evidence_period_requirement="the period the question names",
        assumptions=[], answer_kind="factual", requested_word_limit=500,
    )
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=[topic],
                          verified_findings=[finding], answer_contract=contract)
    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))

    task = agent.build_task(state)

    assert task.target_words == 500


# --- T4 compose (spec §6.7): the table, page credits, unreachable ----------


def test_page_credit_prefers_a_later_updated_date_over_the_published_date():
    """D8: when a page's own updated date is later than its published date,
    credit the updated date, rendered as such."""
    from deep_research.agents.report_writer import _page_credit
    read = make_read("Text.", url="https://a.test/1")
    read = read.model_copy(update={"page_published": "2001-09-26", "page_updated": "2026-08-11"})
    finding = _statement_finding("https://a.test/1", "Text.", target_ids=["topic-01-target-01"])
    finding = finding.model_copy(update={"read_id": read.read_id})
    normalized = normalize_source_url("https://a.test/1")

    credit = _page_credit(normalized, findings_by_url={normalized: finding},
                          reads={read.read_id: read}, sources=[])

    assert credit.date == "2026-08-11"
    assert credit.date_kind == "updated"


def test_page_credit_keeps_the_published_date_when_it_is_the_later_one():
    """D8: the existing precedence stays when the published date is not
    older than the updated date."""
    from deep_research.agents.report_writer import _page_credit
    read = make_read("Text.", url="https://a.test/1")
    read = read.model_copy(update={"page_published": "2026-01-05", "page_updated": "2020-01-01"})
    finding = _statement_finding("https://a.test/1", "Text.", target_ids=["topic-01-target-01"])
    finding = finding.model_copy(update={"read_id": read.read_id})
    normalized = normalize_source_url("https://a.test/1")

    credit = _page_credit(normalized, findings_by_url={normalized: finding},
                          reads={read.read_id: read}, sources=[])

    assert credit.date == "2026-01-05"
    assert credit.date_kind == "published"



@pytest.mark.asyncio
async def test_a_composed_report_carries_its_table_page_credits_and_unreachable_page_through_to_rendering(
    checker, tracker: Tracker, tmp_path: Path,
) -> None:
    """Spec §6.7: build_table, page_credits (a published date and an
    updated-only date) and unreachable all reach the composition and render."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    published_read = make_read("10.4 GW in 2024.", url="https://a.test/1", title="Org One page")
    f1 = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation="Org One",
                 target_ids=["topic-01-target-01"])
    f1 = f1.model_copy(update={"read_id": published_read.read_id})
    f2 = _checked("https://a.test/2", "5 GW in 2025.", "5", "GW", organisation="Org Two",
                 target_ids=["topic-01-target-01"], kind="forecast", period="2025")
    published_source = ScoredSource(
        url="https://a.test/1", title="Org One page", authority_score=0.8, recency_score=0.8,
        relevance_score=0.8, overall_score=0.8, rationale="Directly on point.",
        temporal=SourceTemporal(publication_date="2026-01-05"),
    )
    updated_read = make_read("5 GW in 2025.", url="https://a.test/2", title="Org Two page")
    updated_read = updated_read.model_copy(update={"page_updated": "2026-02-10"})
    f2 = f2.model_copy(update={"read_id": updated_read.read_id})
    topic = _topic("topic-01", "Capacity added", [target])
    denied_candidate = CandidateRecord(candidate_id="c1", url="https://a.test/denied",
                                       title="A denied page", discovered_via="search",
                                       status="denied", denial_reason="access_denied")
    acquisition = AcquisitionState(denied_urls=["https://a.test/denied"],
                                   candidate_records={"https://a.test/denied": denied_candidate})
    state = ResearchState(
        session_id="s1", original_question="Q?", sub_topics=[topic],
        verified_findings=[f1, f2], evaluated_sources=[published_source],
        read_records={updated_read.read_id: updated_read, published_read.read_id: published_read},
        acquisition_state_by_target={"topic-01": acquisition},
    )
    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Capacity added", points=[
            WriterPointDraft(text="According to the source, 10.4 GW in 2024.", finding_labels=["F01"]),
            WriterPointDraft(text="According to the source, 5 GW in 2025.", finding_labels=["F02"]),
        ]),
        BottomLineDraft(sentences=[]),
    ])
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert composition.table is not None
    assert composition.table.shape == "findings"
    assert len(composition.table.rows) == 2
    credit_1 = composition.page_credits[normalize_source_url("https://a.test/1")]
    assert credit_1.date == "2026-01-05" and credit_1.date_kind == "published"
    credit_2 = composition.page_credits[normalize_source_url("https://a.test/2")]
    assert credit_2.date == "2026-02-10" and credit_2.date_kind == "updated"
    assert [page.url for page in composition.unreachable] == ["https://a.test/denied"]
    assert composition.unreachable[0].title == "A denied page"
    assert composition.unreachable[0].reason == "access_denied"

    markdown = render_written_report(composition)
    evidence = render_finding_log(composition)
    assert "| What was measured | Result |" in markdown
    assert "(2026-01-05)" in markdown
    assert "(updated 2026-02-10)" in markdown
    assert "A denied page" in markdown
    assert "access was denied" in markdown
    assert "A denied page" in evidence


@pytest.mark.asyncio
async def test_the_pages_own_metadata_date_outranks_the_evaluators_admitted_date(
    checker, tracker: Tracker, tmp_path: Path,
) -> None:
    """P1-2: the Source Evaluator sees only excerpts and can admit a content
    date (a date the page's own text merely mentions) as the publication
    date; the page's own read metadata, when the page carries one, is never
    second-guessed by it."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    read = make_read("10.4 GW in 2024.", url="https://a.test/1", title="Org One page")
    read = read.model_copy(update={"page_published": "2026-09-17"})
    f1 = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation="Org One",
                 target_ids=["topic-01-target-01"])
    f1 = f1.model_copy(update={"read_id": read.read_id})
    f2 = _checked("https://a.test/2", "5 GW in 2025.", "5", "GW", organisation="Org Two",
                 target_ids=["topic-01-target-01"], kind="forecast", period="2025")
    source = ScoredSource(
        url="https://a.test/1", title="Org One page", authority_score=0.8, recency_score=0.8,
        relevance_score=0.8, overall_score=0.8, rationale="Directly on point.",
        temporal=SourceTemporal(publication_date="2025-05-15"),
    )
    topic = _topic("topic-01", "Capacity added", [target])
    state = ResearchState(
        session_id="s1", original_question="Q?", sub_topics=[topic],
        verified_findings=[f1, f2], evaluated_sources=[source],
        read_records={read.read_id: read},
    )
    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Capacity added", points=[
            WriterPointDraft(text="According to the source, 10.4 GW in 2024.", finding_labels=["F01"]),
            WriterPointDraft(text="According to the source, 5 GW in 2025.", finding_labels=["F02"]),
        ]),
        BottomLineDraft(sentences=[]),
    ])
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    credit = composition.page_credits[normalize_source_url("https://a.test/1")]
    assert credit.date == "2026-09-17"
    assert credit.date_kind == "published"


@pytest.mark.asyncio
async def test_the_table_is_built_after_page_credits_exist_not_before(
    checker, tracker: Tracker, tmp_path: Path,
) -> None:
    """Spec §6.7: page credits must exist before the table is built, so a
    relayed row's Who cell names the page's own credited publisher
    ("Utility Dive"), never the raw host ("utilitydive.com") that page
    credits has not been filled in yet would fall back to."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    relay_url = "https://www.utilitydive.com/news/storage-2025"
    relay_read = make_read("Wood Mackenzie reports 16 GW of storage in 2025.",
                           url=relay_url, title="Storage in 2025 | Utility Dive")
    f1 = _checked(relay_url, "Wood Mackenzie reports 16 GW of storage in 2025.", "16", "GW",
                 organisation="Wood Mackenzie", attribution="relayed",
                 target_ids=["topic-01-target-01"])
    f1 = f1.model_copy(update={"read_id": relay_read.read_id})
    f2 = _checked("https://a.test/2", "5 GW in 2025.", "5", "GW", organisation="Org Two",
                 target_ids=["topic-01-target-01"], kind="forecast", period="2025")
    topic = _topic("topic-01", "Capacity added", [target])
    state = ResearchState(
        session_id="s1", original_question="Q?", sub_topics=[topic],
        verified_findings=[f1, f2], read_records={relay_read.read_id: relay_read},
    )
    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Capacity added", points=[
            WriterPointDraft(text="Wood Mackenzie reports 16 GW of storage in 2025.",
                             finding_labels=["F01"]),
            WriterPointDraft(text="According to the source, 5 GW in 2025.", finding_labels=["F02"]),
        ]),
        BottomLineDraft(sentences=[WriterPointDraft(
            text="Wood Mackenzie reports 16 GW of storage in 2025.", finding_labels=["F01"])]),
    ])
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert composition.table is not None
    who_cell = next(
        row[2].text for row in composition.table.rows if "Wood Mackenzie" in row[2].text
    )
    assert "reported by Utility Dive" in who_cell
    assert "utilitydive.com" not in who_cell


@pytest.mark.asyncio
async def test_a_relayed_figures_attribution_survives_into_the_bottom_line(
    checker, tracker: Tracker, tmp_path: Path,
) -> None:
    """Fable prompt review: SECTION_INSTRUCTION's relay-credit phrase
    ("according to <organisation>, as reported by <site>") reaches the
    bottom line's own request unchanged (the checked-statements block copies
    a kept point's text verbatim) and survives the Statement Check and
    restatement dedup to print in the final bottom line unchanged."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    relay_url = "https://www.utilitydive.com/news/storage-2025"
    relay_read = make_read("Wood Mackenzie reports 16 GW of storage in 2025.",
                           url=relay_url, title="Storage in 2025 | Utility Dive")
    finding = _checked(relay_url, "Wood Mackenzie reports 16 GW of storage in 2025.", "16", "GW",
                       organisation="Wood Mackenzie", attribution="relayed",
                       target_ids=["topic-01-target-01"])
    finding = finding.model_copy(update={"read_id": relay_read.read_id})
    topic = _topic("topic-01", "Storage capacity", [target])
    state = ResearchState(
        session_id="s1", original_question="Q?", sub_topics=[topic],
        verified_findings=[finding], read_records={relay_read.read_id: relay_read},
    )
    credited_sentence = ("According to Wood Mackenzie, as reported by Utility Dive, "
                        "storage capacity reached 16 GW in 2025.")
    captured_bottom_line_body = {}

    def route(messages, schema):
        if schema is BottomLineDraft:
            captured_bottom_line_body["text"] = messages[-1].content
            return BottomLineDraft(sentences=[WriterPointDraft(
                text=credited_sentence, finding_labels=["F01"])])
        return SectionDraft(title="Storage capacity",
                            points=[WriterPointDraft(text=credited_sentence, finding_labels=["F01"])])

    completer = ScriptedCompleter(outputs=[route, route])
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert credited_sentence in captured_bottom_line_body["text"]
    assert [point.text for point in composition.summary] == [credited_sentence]



@pytest.mark.asyncio
async def test_a_figures_statement_date_never_becomes_a_page_credits_date(
    checker, tracker: Tracker, tmp_path: Path,
) -> None:
    """Spec §6.7/§8: never a figure's statement_date or its vintage -- those
    are the figure's dates, not the page's."""
    finding = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation="Org One",
                       target_ids=["topic-01-target-01"], statement_date="2020-06-15")
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    topic = _topic("topic-01", "Capacity added", [target])
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=[topic],
                          verified_findings=[finding])
    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Capacity added",
                    points=[WriterPointDraft(text="According to the source, 10.4 GW in 2024.", finding_labels=["F01"])]),
        BottomLineDraft(sentences=[]),
    ])
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    credit = composition.page_credits[normalize_source_url("https://a.test/1")]
    assert credit.date != "2020-06-15"
    assert credit.date is None
    assert credit.date_kind is None


def test_the_statement_check_correction_cap_follows_max_point_chars() -> None:
    """The Statement Check's corrected_text cap must equal MAX_POINT_CHARS,
    interpolated, never a separate literal that could drift from it."""
    from deep_research.agents.evidence_verifier import STATEMENT_CHECK_INSTRUCTION

    assert f"more than {MAX_POINT_CHARS} characters" in STATEMENT_CHECK_INSTRUCTION
    assert "600 characters" not in STATEMENT_CHECK_INSTRUCTION


# --- review round (RevFormatT1T2/RevFormatT4) -------------------------------


def test_apply_marks_drops_a_mark_whose_by_the_point_does_not_cite() -> None:
    """P0: ``by`` must resolve only among the point's own cited labels, never
    the whole registry -- otherwise a mark can credit a page the sentence and
    its Statement Check never rested on."""
    from deep_research.agents.report_writer import _apply_marks

    dropped: list[tuple[str, str]] = []
    kept = _apply_marks(
        [ItemMarkDraft(name="Model A", verdict="a score of 9", picked=True, by="F03")],
        final_text="One gives Model A a score of 9 and Two gives Model B a score of 8.",
        labels=["F01", "F02"],
        label_urls={"F01": "https://one.test/1", "F02": "https://two.test/1", "F03": "https://three.test/1"},
        label_finding_ids={"F01": "finding-01", "F02": "finding-02", "F03": "finding-03"},
        dropped=dropped, statement_key="S001",
    )

    assert kept == []
    assert dropped == [("S001", "names no finding this sentence cites")]


def test_apply_marks_still_resolves_by_when_it_is_one_of_the_points_own_labels() -> None:
    from deep_research.agents.report_writer import _apply_marks

    dropped: list[tuple[str, str]] = []
    kept = _apply_marks(
        [ItemMarkDraft(name="Model A", verdict="a score of 9", picked=True, by="F01")],
        final_text="One gives Model A a score of 9 and Two gives Model B a score of 8.",
        labels=["F01", "F02"],
        label_urls={"F01": "https://one.test/1", "F02": "https://two.test/1"},
        label_finding_ids={"F01": "finding-01", "F02": "finding-02"},
        dropped=dropped, statement_key="S001",
    )

    assert dropped == []
    assert kept == [ItemMark(name="Model A", verdict="a score of 9", picked=True,
                             source_url="https://one.test/1", finding_id="finding-01")]


def test_apply_marks_resolves_finding_id_from_by_even_when_findings_share_one_page() -> None:
    """R-2: the table decides whether a pick is relayed from the mark's own
    finding, never from whichever finding happens to come last on a shared
    page -- so a mark's ``finding_id`` is resolved from its own ``by``, even
    when two cited findings share one URL."""
    from deep_research.agents.report_writer import _apply_marks

    dropped: list[tuple[str, str]] = []
    kept = _apply_marks(
        [ItemMarkDraft(name="Model A", verdict="a score of 9", picked=True, by="F02")],
        final_text="One gives Model A a score of 9 and Two gives Model A a score of 9, relayed.",
        labels=["F01", "F02"],
        label_urls={"F01": "https://shared.test/1", "F02": "https://shared.test/1"},
        label_finding_ids={"F01": "finding-own", "F02": "finding-relayed"},
        dropped=dropped, statement_key="S001",
    )

    assert dropped == []
    assert kept[0].source_url == "https://shared.test/1"
    assert kept[0].finding_id == "finding-relayed"


@pytest.mark.asyncio
async def test_bottom_line_fallback_prefers_the_parts_own_marked_disputing_point(
    checker, tracker: Tracker, tmp_path: Path,
) -> None:
    """RevZ2 P1-c, ReRevZ2 C7: the naive first-kept-point pick the
    fallback used to make could itself be silent about a dispute one of
    its own cited findings carries; when it cites one of the marked
    point's own labels but is not itself the part's marked point, the
    marked point is preferred instead, so the fallback never prints a
    disputed step as settled. Label-scoped: the naive pick and the
    marked point share a finding (F01), not merely a target."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    finding_a = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation=EIA,
                        target_ids=["topic-01-target-01"])
    finding_b = _checked("https://b.test/1", "11 GW in 2024, revised.", "11", "GW",
                        organisation="Example Institute", target_ids=["topic-01-target-01"])
    topic = _topic("topic-01", "Capacity added", [target])
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=[topic],
                          verified_findings=[finding_a, finding_b])
    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Capacity added", points=[
            WriterPointDraft(text="The EIA reported 10.4 GW in 2024.", finding_labels=["F01"]),
            WriterPointDraft(
                text="According to the source, a later account revises the earlier EIA figure to 11 GW.",
                finding_labels=["F01", "F02"], disputes=True,
            ),
        ]),
        _output_limit_error(), _output_limit_error(),
    ])
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert len(composition.summary) == 1
    assert composition.summary[0].text == (
        "According to the source, a later account revises the earlier EIA figure to 11 GW."
    )


@pytest.mark.asyncio
async def test_a_bottom_line_fallback_gives_each_point_its_own_id_and_real_verdict(
    checker, tracker: Tracker, tmp_path: Path,
) -> None:
    """P1-a: the fallback must not reuse a section's own flight key or
    hard-code 'consistent'; the source section must not print it again."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    finding = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation=EIA,
                       target_ids=["topic-01-target-01"])
    topic = _topic("topic-01", "Capacity added", [target])
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=[topic],
                          verified_findings=[finding])
    checker.verdicts["P01.01"] = _verdict("corrected", corrected_text="10.4 GW, corrected.")
    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Capacity added",
                    points=[WriterPointDraft(text="According to the source, 10.4 GW in 2024.", finding_labels=["F01"])]),
        _output_limit_error(), _output_limit_error(),
    ])
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert len(composition.summary) == 1
    assert composition.summary[0].text == "10.4 GW, corrected."
    assert composition.sections == []
    printed_ids = {p.statement_id for p in composition.summary} | {
        p.statement_id for section in composition.sections for p in section.points
    }
    assert printed_ids <= set(composition.statement_verdicts)
    assert composition.statement_verdicts[composition.summary[0].statement_id] == "corrected"


@pytest.mark.asyncio
async def test_a_statement_check_outage_leaves_a_recoverable_error_not_a_false_every_part_failed(
    checker, tracker: Tracker, tmp_path: Path,
) -> None:
    """P1-b(i): a Statement Check outage marks the part 'written' (its draft
    succeeded), so the composition must not report a non-recoverable 'every
    part failed' error, and the bottom line must not print the §10 fallback
    sentence while the (unchecked) section still stands."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    finding = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation=EIA,
                       target_ids=["topic-01-target-01"])
    topic = _topic("topic-01", "Capacity added", [target])
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=[topic],
                          verified_findings=[finding])
    checker.fail_all = True
    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Capacity added",
                    points=[WriterPointDraft(text="According to the source, 10.4 GW in 2024.", finding_labels=["F01"])]),
        BottomLineDraft(sentences=[]),
    ])
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert [p.status for p in composition.parts] == ["written"]
    assert len(composition.sections) == 1
    assert composition.summary == []
    error_types = [e.error_type for e in composition.errors]
    assert "report_writer_provider_error" not in error_types
    assert error_types and all(
        e.recoverable for e in composition.errors if e.error_type != "evidence_verifier_statement_check_failed"
    )


@pytest.mark.asyncio
async def test_a_bottom_line_re_ask_adopts_a_fully_passing_retry(
    checker, tracker: Tracker, tmp_path: Path,
) -> None:
    """D1/D2: the Statement Check refusing a bottom-line sentence buys one
    re-ask carrying the refusal reason; its own result replaces the
    refused attempt when every one of its sentences passes, and (P2-1)
    attempt 1's own refusal record stays in the evidence log."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    finding = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation=EIA,
                       target_ids=["topic-01-target-01"])
    topic = _topic("topic-01", "Capacity added", [target])
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=[topic],
                          verified_findings=[finding])
    checker.verdicts["B01"] = _verdict("inconsistent", reason="No cited finding supports this claim.")
    checker.verdicts["R01"] = _verdict("consistent")
    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Capacity added",
                    points=[WriterPointDraft(text="According to the source, 10.4 GW in 2024.", finding_labels=["F01"])]),
        BottomLineDraft(sentences=[WriterPointDraft(text="A wrong claim.", finding_labels=["F01"])]),
        BottomLineDraft(sentences=[WriterPointDraft(text="10.4 GW in 2024, restated.",
                                                    finding_labels=["F01"])]),
    ])
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert [p.text for p in composition.summary] == ["10.4 GW in 2024, restated."]
    assert any(r.text == "A wrong claim." for r in composition.rejected_points)


@pytest.mark.asyncio
async def test_a_re_ask_whose_check_fails_does_not_replace_a_checked_bottom_line(
    tracker: Tracker, tmp_path: Path,
) -> None:
    """P1-2: when the re-ask's own Statement Check fails (a provider outage
    returns no verdicts at all, exactly as ``_check`` leaves every
    candidate on a real failure), the re-ask is never adopted -- a checked
    bottom line is never swapped for unchecked text."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    finding = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation=EIA,
                       target_ids=["topic-01-target-01"])
    topic = _topic("topic-01", "Capacity added", [target])
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=[topic],
                          verified_findings=[finding])
    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Capacity added",
                    points=[WriterPointDraft(text="According to the source, 10.4 GW in 2024.", finding_labels=["F01"])]),
        BottomLineDraft(sentences=[WriterPointDraft(text="A wrong claim.", finding_labels=["F01"])]),
        BottomLineDraft(sentences=[WriterPointDraft(text="Unchecked claim.", finding_labels=["F01"])]),
    ])

    import deep_research.agents.evidence_verifier as ev

    calls = {"n": 0}

    async def flaky_check(provider, items, *, question, fingerprint=None, batch_size=None, concurrency=None, gate=None):
        calls["n"] += 1
        if calls["n"] == 2:
            # Attempt 1's own bottom-line check: refuse it to buy a re-ask.
            return {item.label: _verdict("inconsistent", reason="No cited finding supports this claim.")
                    for item in items}, []
        if calls["n"] == 3:
            # The re-ask's own check: a provider outage, no verdicts at all.
            return {}, []
        return {item.label: _verdict("consistent") for item in items}, []

    original = ev.check_statements
    ev.check_statements = flaky_check
    try:
        agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))
        task = agent.build_task(state)
        composition = await compose_written_report(task, provider=completer, section_concurrency=7)
    finally:
        ev.check_statements = original

    assert [p.text for p in composition.summary] == ["According to the source, 10.4 GW in 2024."]


@pytest.mark.asyncio
async def test_a_mechanism_bottom_line_without_the_outcome_re_asks_and_adopts_it(
    checker, tracker: Tracker, tmp_path: Path,
) -> None:
    """Run-8 D4/D5 (c): a mechanism answer whose kept section carries a
    point marked ``outcome: true`` buys one re-ask when attempt 1's
    bottom line cites no label of that outcome statement -- the same
    one re-ask the dispute guard uses. The re-ask's own outcome
    sentence is adopted when it is fully checked."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    cause = _statement_finding("https://a.test/1", "A funding cut reduced the budget in 2018.",
                               target_ids=["topic-01-target-01"])
    outcome = _statement_finding("https://a.test/2", "The programme closed in 2020.",
                                 target_ids=["topic-01-target-01"])
    topic = _topic("topic-01", "Programme closure", [target])
    contract = AnswerContract(
        question="Why did the programme close?",
        scope_statement="Answered as of 2026-09-24.", geographic_scope="worldwide",
        as_of_date="2026-09-24", evidence_period_requirement="the period the question names",
        assumptions=[], answer_kind="explanation", requested_word_limit=500,
    )
    state = ResearchState(session_id="s1", original_question="Why did the programme close?",
                          sub_topics=[topic], verified_findings=[cause, outcome],
                          answer_contract=contract)
    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Programme closure", points=[
            WriterPointDraft(text="According to the source, a funding cut reduced the budget in 2018.",
                             finding_labels=["F01"]),
            WriterPointDraft(text="According to the source, the programme closed in 2020.",
                             finding_labels=["F02"], outcome=True),
        ]),
        BottomLineDraft(sentences=[
            WriterPointDraft(text="A funding cut reduced the budget in 2018, restated.",
                             finding_labels=["F01"]),
        ]),
        BottomLineDraft(sentences=[
            WriterPointDraft(text="A funding cut reduced the budget in 2018, restated.",
                             finding_labels=["F01"]),
            WriterPointDraft(text="The programme closed in 2020, restated.",
                             finding_labels=["F02"]),
        ]),
    ])
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert "The programme closed in 2020, restated." in [p.text for p in composition.summary]


@pytest.mark.asyncio
async def test_a_non_mechanism_bottom_line_without_the_outcome_never_re_asks(
    checker, tracker: Tracker, tmp_path: Path,
) -> None:
    """Run-8 D4/D5 (c): the same missing-outcome bottom line on a
    non-mechanism answer form never triggers the re-ask -- only two
    scripted replies are queued, so a wrongly triggered re-ask fails
    loudly on a missing scripted response."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    cause = _statement_finding("https://a.test/1", "A funding cut reduced the budget in 2018.",
                               target_ids=["topic-01-target-01"])
    outcome = _statement_finding("https://a.test/2", "The programme closed in 2020.",
                                 target_ids=["topic-01-target-01"])
    topic = _topic("topic-01", "Programme closure", [target])
    contract = AnswerContract(
        question="What happened to the programme?",
        scope_statement="Answered as of 2026-09-24.", geographic_scope="worldwide",
        as_of_date="2026-09-24", evidence_period_requirement="the period the question names",
        assumptions=[], answer_kind="factual", requested_word_limit=500,
    )
    state = ResearchState(session_id="s1", original_question="What happened to the programme?",
                          sub_topics=[topic], verified_findings=[cause, outcome],
                          answer_contract=contract)
    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Programme closure", points=[
            WriterPointDraft(text="According to the source, a funding cut reduced the budget in 2018.",
                             finding_labels=["F01"]),
            WriterPointDraft(text="According to the source, the programme closed in 2020.",
                             finding_labels=["F02"], outcome=True),
        ]),
        BottomLineDraft(sentences=[
            WriterPointDraft(text="A funding cut reduced the budget in 2018, restated.",
                             finding_labels=["F01"]),
        ]),
    ])
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert [p.text for p in composition.summary] == ["A funding cut reduced the budget in 2018, restated."]


@pytest.mark.asyncio
async def test_a_mechanism_bottom_line_that_already_cites_the_outcome_never_re_asks(
    checker, tracker: Tracker, tmp_path: Path,
) -> None:
    """Run-8 D4/D5 (c): a mechanism bottom line whose attempt 1 already
    cites a label of the outcome-marked statement never re-asks for it
    -- only two scripted replies are queued, so a wrongly triggered
    re-ask fails loudly on a missing scripted response."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    cause = _statement_finding("https://a.test/1", "A funding cut reduced the budget in 2018.",
                               target_ids=["topic-01-target-01"])
    outcome = _statement_finding("https://a.test/2", "The programme closed in 2020.",
                                 target_ids=["topic-01-target-01"])
    topic = _topic("topic-01", "Programme closure", [target])
    contract = AnswerContract(
        question="Why did the programme close?",
        scope_statement="Answered as of 2026-09-24.", geographic_scope="worldwide",
        as_of_date="2026-09-24", evidence_period_requirement="the period the question names",
        assumptions=[], answer_kind="explanation", requested_word_limit=500,
    )
    state = ResearchState(session_id="s1", original_question="Why did the programme close?",
                          sub_topics=[topic], verified_findings=[cause, outcome],
                          answer_contract=contract)
    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Programme closure", points=[
            WriterPointDraft(text="According to the source, a funding cut reduced the budget in 2018.",
                             finding_labels=["F01"]),
            WriterPointDraft(text="According to the source, the programme closed in 2020.",
                             finding_labels=["F02"], outcome=True),
        ]),
        BottomLineDraft(sentences=[
            WriterPointDraft(text="A funding cut reduced the budget in 2018, restated.",
                             finding_labels=["F01"]),
            WriterPointDraft(text="The programme closed in 2020, restated.",
                             finding_labels=["F02"]),
        ]),
    ])
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert [p.text for p in composition.summary] == [
        "A funding cut reduced the budget in 2018, restated.",
        "The programme closed in 2020, restated.",
    ]


@pytest.mark.asyncio
async def test_a_mechanism_bottom_line_never_re_asks_when_its_only_outcome_is_below_the_floor(
    checker, tracker: Tracker, tmp_path: Path,
) -> None:
    """RevV2 P1: an outcome-marked point resting only on a sub-floor
    source is withheld from the bottom line's own candidate pool the
    same way any below-floor statement is (item 2) -- so its label
    never reaches ``outcome_labels``, and the guard must gate on that
    filtered set, not the writer's raw marks, or it demands a citation
    to a "# Outcome" block that is never printed. Only two scripted
    replies are queued: a wrongly triggered re-ask fails loudly on a
    missing scripted response."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    cause = _statement_finding("https://strong.test/1", "A funding cut reduced the budget in 2018.",
                               target_ids=["topic-01-target-01"])
    outcome = _statement_finding("https://weak.test/1", "The programme closed in 2020.",
                                 target_ids=["topic-01-target-01"])
    strong_source = _authority_source("https://strong.test/1", authority=0.9)
    weak_source = _authority_source("https://weak.test/1", authority=0.1)
    topic = _topic("topic-01", "Programme closure", [target])
    contract = AnswerContract(
        question="Why did the programme close?",
        scope_statement="Answered as of 2026-09-24.", geographic_scope="worldwide",
        as_of_date="2026-09-24", evidence_period_requirement="the period the question names",
        assumptions=[], answer_kind="explanation", requested_word_limit=500,
    )
    state = ResearchState(session_id="s1", original_question="Why did the programme close?",
                          sub_topics=[topic], verified_findings=[cause, outcome],
                          evaluated_sources=[strong_source, weak_source],
                          answer_contract=contract)
    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Programme closure", points=[
            WriterPointDraft(text="According to the source, a funding cut reduced the budget in 2018.",
                             finding_labels=["F01"]),
            WriterPointDraft(text="According to the source, the programme closed in 2020.",
                             finding_labels=["F02"], outcome=True),
        ]),
        BottomLineDraft(sentences=[
            WriterPointDraft(text="A funding cut reduced the budget in 2018, restated.",
                             finding_labels=["F01"]),
        ]),
    ])
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert [p.text for p in composition.summary] == ["A funding cut reduced the budget in 2018, restated."]


@pytest.mark.asyncio
async def test_a_bottom_line_with_every_sentence_refused_falls_back_to_checked_section_points(
    checker, tracker: Tracker, tmp_path: Path,
) -> None:
    """P1-b(ii): a drafted-but-empty-after-refusal bottom line still gets the
    §6.8 fallback, not a silent empty summary -- even after the D1/D2
    re-ask, when the re-ask's own sentence is refused too."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    finding = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation=EIA,
                       target_ids=["topic-01-target-01"])
    topic = _topic("topic-01", "Capacity added", [target])
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=[topic],
                          verified_findings=[finding])
    checker.verdicts["B01"] = _verdict("inconsistent", reason="not supported")
    checker.verdicts["R01"] = _verdict("inconsistent", reason="still not supported")
    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Capacity added",
                    points=[WriterPointDraft(text="According to the source, 10.4 GW in 2024.", finding_labels=["F01"])]),
        BottomLineDraft(sentences=[WriterPointDraft(text="10.4 GW in 2024, allegedly.", finding_labels=["F01"])]),
        BottomLineDraft(sentences=[WriterPointDraft(text="10.4 GW in 2024, still allegedly.",
                                                    finding_labels=["F01"])]),
    ])
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert len(composition.summary) == 1
    assert composition.summary[0].text == "According to the source, 10.4 GW in 2024."
    assert composition.sections == []


@pytest.mark.asyncio
async def test_a_redraft_still_drafts_a_part_that_has_findings_but_no_previous_section(
    checker, tracker: Tracker, tmp_path: Path,
) -> None:
    """P2-1: a part that failed (or was fully refused) last pass, and that no
    defect routes to on this redraft, must still be drafted -- not silently
    relabelled 'empty', losing its required-target answer."""
    t1 = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    t2 = make_target("topic-02-target-01", coverage_id="topic-02", required=True)
    f1 = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation=EIA,
                 target_ids=["topic-01-target-01"])
    f2 = _checked("https://a.test/2", "5 GW in 2025.", "5", "GW", organisation=EIA,
                 target_ids=["topic-02-target-01"], kind="forecast", period="2025")
    topics = [_topic("topic-01", "First", [t1]), _topic("topic-02", "Second", [t2])]
    previous_statement_1 = ReportStatement(statement_id="S001", text="10.4 GW in 2024.",
                                           finding_ids=[finding_fingerprint(f1)], target_ids=["topic-01-target-01"])
    previous_section_1 = ReportSection(title="First", coverage_id="topic-01",
                                       points=[ReportPointFor("10.4 GW in 2024.", previous_statement_1)])
    from deep_research.utils.types import ReportComposition, ReportPart
    previous = ReportComposition(
        question="Q?", session_id="s1", sections=[previous_section_1], summary=[], sub_topics=topics,
        parts=[
            ReportPart(coverage_id="topic-01", sub_topic_title="First",
                      finding_ids=[finding_fingerprint(f1)], status="written"),
            ReportPart(coverage_id="topic-02", sub_topic_title="Second",
                      finding_ids=[finding_fingerprint(f2)], status="failed"),
        ],
    )
    defect = ReviewDefect(defect_id="review-01", kind="missing_support", severity="major",
                          target_ids=["topic-01-target-01"], problem="Needs the release date.")
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=topics,
                          verified_findings=[f1, f2], composition=previous, report_review=_scored_review([defect]))

    def route(messages, schema):
        body = messages[-1].content
        if schema is BottomLineDraft:
            return BottomLineDraft(sentences=[WriterPointDraft(text="According to the source, 10.4 GW in 2024, corrected.",
                                                                finding_labels=["F01"])])
        if "topic-02" in body.split("# This part of the question")[1][:20]:
            return SectionDraft(title="Second",
                                points=[WriterPointDraft(text="According to the source, 5 GW in 2025.", finding_labels=["F02"])])
        return SectionDraft(title="First",
                            points=[WriterPointDraft(text="According to the source, 10.4 GW in 2024, corrected.", finding_labels=["F01"])])

    completer = ScriptedCompleter(outputs=[route, route, route])
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    statuses = {p.coverage_id: p.status for p in composition.parts}
    assert statuses["topic-02"] != "empty"
    assert any(s.coverage_id == "topic-02" for s in composition.sections)


def test_target_line_does_not_credit_a_label_placed_in_another_part() -> None:
    """P2-2: a target's answering labels must be filtered to the part's own
    (non-context) findings, never the whole registry."""
    from deep_research.agents.report_writer import _target_line

    target = make_target("topic-02-target-01", coverage_id="topic-02", required=True,
                         question="How much capacity was added?")
    finding = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation=EIA,
                       target_ids=["topic-02-target-01"])
    finding_id = finding_fingerprint(finding)

    line = _target_line(
        target, answered={target.target_id: [finding_id]}, label_by_id={finding_id: "F01"},
        findings_by_id={finding_id: finding}, sub_topics=[], own_finding_ids=set(),
    )

    assert "no listed finding answers it" in line
    assert "F01" not in line


def test_target_line_credits_a_label_that_is_one_of_the_parts_own_findings() -> None:
    from deep_research.agents.report_writer import _target_line

    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    finding = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation=EIA,
                       target_ids=["topic-01-target-01"])
    finding_id = finding_fingerprint(finding)

    line = _target_line(
        target, answered={target.target_id: [finding_id]}, label_by_id={finding_id: "F01"},
        findings_by_id={finding_id: finding}, sub_topics=[], own_finding_ids={finding_id},
    )

    assert "answered by F01" in line


@pytest.mark.asyncio
async def test_the_report_written_event_is_published_live(
    checker, tracker: Tracker, tmp_path: Path,
) -> None:
    """live-briefs spec E3: report.written, live, as the object returned."""
    def route(messages, schema):
        del messages, schema
        raise ProviderResponseError(
            "provider returned an HTTP error", retryable=True,
            failure_category="http", http_status_code=503, failure_origin="sdk",
        )

    state = _one_part_state()
    agent = _writer(tracker, ScriptedCompleter(outputs=[route] * 10), report_writer_tools(tracker, output_root=tmp_path))
    received: list[ResearchEvent] = []

    async with tracker.session_span(state.session_id, state.original_question):
        with bind_live_sink(received.append):
            run = await agent.run(state)

    [written] = run.state_update["events"]
    assert written.event_type == "report_writer.report.written"
    assert [event.event_id for event in received] == [written.event_id]


@pytest.mark.asyncio
async def test_writer_carries_parts_after_note_pass(checker, tracker: Tracker, tmp_path: Path) -> None:
    """notes-progress-report spec §5.4, D4, AC6: after a note pass the writer drafts the notes'
    own parts, any part with no previous section (P2-1) and the bottom line — fresh, with no
    defect fed back — and carries every other part over unchanged, with its verdicts."""
    t1 = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    t2 = make_target("topic-02-target-01", coverage_id="topic-02", required=True)
    tn = make_target("note-n1-target-01", coverage_id="note-n1", required=True,
                     question="How much battery capacity was recycled in 2024?")
    f1 = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation=EIA,
                 target_ids=["topic-01-target-01"])
    f2 = _checked("https://a.test/2", "5 GW in 2025.", "5", "GW", organisation=EIA,
                 target_ids=["topic-02-target-01"], kind="forecast", period="2025")
    fn = _checked("https://a.test/3", "3 GW were recycled in 2024.", "3", "GW", organisation=EIA,
                 target_ids=["note-n1-target-01"])
    topics = [_topic("topic-01", "First", [t1]), _topic("topic-02", "Second", [t2]),
              _topic("note-n1", "Your note: how much was recycled", [tn], priority=2)]
    previous_statement = ReportStatement(statement_id="S001", text="10.4 GW in 2024.",
                                         finding_ids=[finding_fingerprint(f1)], target_ids=["topic-01-target-01"])
    previous_section = ReportSection(title="First", coverage_id="topic-01",
                                     points=[ReportPointFor("10.4 GW in 2024.", previous_statement)])
    # The note's own part already holds a section, so only the coverage-id rule (not the
    # no-previous-section rule, P2-1) can make the writer redraft it.
    previous_note_section = ReportSection(title="Your note: how much was recycled", coverage_id="note-n1",
                                          points=[])
    from deep_research.utils.types import ReportComposition
    previous = ReportComposition(question="Q?", session_id="s1",
                                 sections=[previous_section, previous_note_section], summary=[],
                                 sub_topics=topics, statement_verdicts={"S001": "corrected"})
    marker = ResearchEvent(event_type="graph.note_pass.started", source="graph", message="Note pass started.",
                           metadata={"iteration": 0, "note_passes": 1, "note_ids": ["n1"],
                                     "targets": ["note-n1-target-01"]})
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=topics,
                          verified_findings=[f1, f2, fn], composition=previous,
                          report_review=_scored_review([]), events=[marker])
    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)
    label_by_url = {finding.source_url: label for label, finding in task.registry}
    calls: list[str] = []

    def route(messages, schema):
        body = messages[-1].content
        if schema.__name__ == "BottomLineDraft":
            calls.append("bottom_line")
            # A kept sentence, so no fallback moves a carried point out of its section.
            return BottomLineDraft(sentences=[WriterPointDraft(
                text="According to the source, 3 GW were recycled in 2024.",
                finding_labels=[label_by_url["https://a.test/3"]])])
        if "3 GW were recycled in 2024." in body:
            calls.append("note-n1")
            return SectionDraft(title="Your note: how much was recycled", points=[WriterPointDraft(
                text="According to the source, 3 GW were recycled in 2024.",
                finding_labels=[label_by_url["https://a.test/3"]])])
        if "5 GW in 2025." in body:
            calls.append("topic-02")
            return SectionDraft(title="Second", points=[WriterPointDraft(
                text="According to the source, 5 GW in 2025.", finding_labels=[label_by_url["https://a.test/2"]])])
        calls.append("topic-01")
        return SectionDraft(title="First", points=[])

    completer = ScriptedCompleter(outputs=[route, route, route])

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert (task.note_pass_coverage_ids, task.defects, task.previous) == (["note-n1"], [], previous)
    assert sorted(calls) == ["bottom_line", "note-n1", "topic-02"]
    statuses = {part.coverage_id: part.status for part in composition.parts}
    assert statuses == {"topic-01": "carried_over", "topic-02": "written", "note-n1": "written"}
    [carried] = [section for section in composition.sections if section.coverage_id == "topic-01"]
    assert (carried.title, [point.text for point in carried.points]) == ("First", ["10.4 GW in 2024."])
    assert carried.points[0].statement.finding_ids == [finding_fingerprint(f1)]
    assert composition.statement_verdicts[carried.points[0].statement.statement_id] == "corrected"
    from deep_research.graph.nodes import _arrived_via_redraft_hop
    assert _arrived_via_redraft_hop([marker]) is False  # the review after a note pass is a full one
