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
    MAX_BOTTOM_LINE_SENTENCES,
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
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import TokenUsage, Tracker
from deep_research.providers import ProviderOutputLimitError, ProviderResponseError, ProviderResponseTelemetry
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    AcquisitionState,
    BottomLineDraft,
    CandidateRecord,
    FindingVerification,
    ItemMark,
    ItemMarkDraft,
    ReportSection,
    ReportStatement,
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


# --- registry_lines: D5's content: and passage: lines ----------------------


def test_registry_lines_carry_the_findings_content_line():
    finding = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation=EIA,
                       content="The EIA's own page states 10.4 GW in 2024.")
    text = "\n".join(registry_lines("F01", finding))
    assert "content: The EIA's own page states 10.4 GW in 2024." in text


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


def test_section_instruction_describes_the_redraft_rule():
    assert "only the edits" in SECTION_INSTRUCTION


def test_bottom_line_system_prompt_states_the_sentence_bound():
    assert "two to four sentences" in BOTTOM_LINE_SYSTEM_PROMPT


def test_bottom_line_instruction_forbids_a_pick_of_its_own():
    assert "never a pick, ranking or criterion of your own" in BOTTOM_LINE_INSTRUCTION


def test_bottom_line_instruction_forbids_stating_a_sources_date():
    assert "Never state a source's date" in BOTTOM_LINE_INSTRUCTION


# --- generality (D10): no domain or probe wording in model-read text -------


_FORBIDDEN_WORDS = ("headphone", "battery", "electoral", "kettle", "sony", "eia")


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
            return BottomLineDraft(sentences=[WriterPointDraft(text="5 GW in 2025.", finding_labels=["F02"])])
        if "First" in body.split("# This part of the question")[1][:40]:
            raise ProviderResponseError("provider returned an HTTP error", retryable=True,
                                        failure_category="http", http_status_code=503, failure_origin="sdk")
        return SectionDraft(title="Second",
                            points=[WriterPointDraft(text="5 GW in 2025.", finding_labels=["F02"])])

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
                                points=[WriterPointDraft(text="10.4 GW in 2024, corrected.", finding_labels=["F01"])])
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


# --- verdict application, still per-part -----------------------------------


@pytest.mark.asyncio
async def test_a_corrected_verdict_replaces_the_sentence(writer, checker) -> None:
    state = _one_part_state()
    task = writer.build_task(state)
    checker.verdicts["P01.01"] = _verdict("corrected", corrected_text="10.4 GW, corrected.")
    writer.provider._outputs.extend([
        SectionDraft(title="Capacity added",
                    points=[WriterPointDraft(text="10.4 GW in 2024.", finding_labels=["F01"])]),
        BottomLineDraft(sentences=[WriterPointDraft(text="10.4 GW, corrected.", finding_labels=["F01"])]),
    ])

    composition = await compose_written_report(task, provider=writer.provider)

    assert composition.sections[0].points[0].text == "10.4 GW, corrected."


@pytest.mark.asyncio
async def test_an_inconsistent_verdict_refuses_the_point(writer, checker) -> None:
    state = _one_part_state()
    task = writer.build_task(state)
    checker.verdicts["P01.01"] = _verdict("inconsistent", reason="not in the findings")
    writer.provider._outputs.extend([
        SectionDraft(title="Capacity added",
                    points=[WriterPointDraft(text="10.4 GW in 2024.", finding_labels=["F01"])]),
        BottomLineDraft(sentences=[]),
    ])

    composition = await compose_written_report(task, provider=writer.provider)

    assert composition.sections == []
    assert composition.rejected_points[0].reason == "not in the findings"


@pytest.mark.asyncio
async def test_a_point_citing_no_known_label_is_refused_without_calling_the_checker(writer, checker) -> None:
    state = _one_part_state()
    task = writer.build_task(state)
    writer.provider._outputs.extend([
        SectionDraft(title="Capacity added",
                    points=[WriterPointDraft(text="10.4 GW in 2024.", finding_labels=[])]),
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
    read = make_read("Storage capacity grew by 10.4 GW in the United States in 2024.", url="https://a.test/1")
    finding = make_finding(read, "Storage capacity grew by 10.4 GW in the United States in 2024.",
                           figures=[figure("10.4", "GW", "2024", "actual")], target_ids=[])
    finding = finding.model_copy(update={"related_sub_topic": "Capacity added"})
    result = FigureResultFor(finding, organisation=EIA, attribution="own", kind="actual", period="2024")
    finding = finding.model_copy(update={"verification": FindingVerification(status="verified", figure_results=[result])})
    topic = _topic("topic-01", "Capacity added", [target])
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=[topic], verified_findings=[finding])
    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Capacity added",
                    points=[WriterPointDraft(text="Storage capacity grew by 10.4 GW in the United States in 2024.",
                                             finding_labels=["F01"])]),
        BottomLineDraft(sentences=[WriterPointDraft(
            text="Storage capacity grew by 10.4 GW in the United States in 2024.", finding_labels=["F01"])]),
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


# --- T4 compose (spec §6.7): the table, page credits, unreachable ----------


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
            WriterPointDraft(text="10.4 GW in 2024.", finding_labels=["F01"]),
            WriterPointDraft(text="5 GW in 2025.", finding_labels=["F02"]),
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
            WriterPointDraft(text="5 GW in 2025.", finding_labels=["F02"]),
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
                    points=[WriterPointDraft(text="10.4 GW in 2024.", finding_labels=["F01"])]),
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
        dropped=dropped, statement_key="S001",
    )

    assert dropped == []
    assert kept == [ItemMark(name="Model A", verdict="a score of 9", picked=True, source_url="https://one.test/1")]


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
                    points=[WriterPointDraft(text="10.4 GW in 2024.", finding_labels=["F01"])]),
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
                    points=[WriterPointDraft(text="10.4 GW in 2024.", finding_labels=["F01"])]),
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
async def test_a_bottom_line_with_every_sentence_refused_falls_back_to_checked_section_points(
    checker, tracker: Tracker, tmp_path: Path,
) -> None:
    """P1-b(ii): a drafted-but-empty-after-refusal bottom line still gets the
    §6.8 fallback, not a silent empty summary."""
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    finding = _checked("https://a.test/1", "10.4 GW in 2024.", "10.4", "GW", organisation=EIA,
                       target_ids=["topic-01-target-01"])
    topic = _topic("topic-01", "Capacity added", [target])
    state = ResearchState(session_id="s1", original_question="Q?", sub_topics=[topic],
                          verified_findings=[finding])
    checker.verdicts["B01"] = _verdict("inconsistent", reason="not supported")
    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Capacity added",
                    points=[WriterPointDraft(text="10.4 GW in 2024.", finding_labels=["F01"])]),
        BottomLineDraft(sentences=[WriterPointDraft(text="10.4 GW in 2024, allegedly.", finding_labels=["F01"])]),
    ])
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(state)

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert len(composition.summary) == 1
    assert composition.summary[0].text == "10.4 GW in 2024."
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
            return BottomLineDraft(sentences=[WriterPointDraft(text="10.4 GW in 2024, corrected.",
                                                                finding_labels=["F01"])])
        if "topic-02" in body.split("# This part of the question")[1][:20]:
            return SectionDraft(title="Second",
                                points=[WriterPointDraft(text="5 GW in 2025.", finding_labels=["F02"])])
        return SectionDraft(title="First",
                            points=[WriterPointDraft(text="10.4 GW in 2024, corrected.", finding_labels=["F01"])])

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
