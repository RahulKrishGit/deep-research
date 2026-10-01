"""The bottom line that answers first (notes-progress-report spec §7.1-§7.3; AC22-AC24, AC36).

The bottom-line request carries the reader's answers and one ``## {coverage_id} ·
{title}`` block per checked section; its reply is a direct answer of one or two
sentences, then one line per topic; the fallback builds the same shape from the
sections.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from deep_research.agents.evidence import cosmetic_text
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.prompts import render_structured_reply_format
from deep_research.agents.report_writer import (
    _BOTTOM_LINE_REPLY_EXAMPLES,
    _MARK_SPAN_CHARS,
    BOTTOM_LINE_INSTRUCTION,
    SECTION_INSTRUCTION,
    _section_short_title,
    bottom_line_messages,
    compose_written_report,
)
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.utils.types import (
    AnswerContract,
    BottomLineDraft,
    ReaderAnswer,
    ReportPoint,
    ReportSection,
    ReportStatement,
    ResearchState,
    SectionDraft,
    WriterPointDraft,
)
from tests.agent_fakes import ScriptedCompleter
from tests.evidence_fakes import make_target
from tests.research_fakes import report_writer_tools
from tests.test_agents.test_report_writer import (
    EIA,
    _checked,
    _FakeChecker,
    _FakeStatementCheckItem,
    _statement_finding,
    _topic,
    _writer,
)

ANSWERS = [
    ReaderAnswer(question_id="q1", dimension="scope", text="How many picks do you want?",
                 short="Picks", value="Just one", source="chosen"),
    ReaderAnswer(question_id="q2", dimension="geography", text="Which area?",
                 short="Area", value="San Jose", source="best_guess"),
]


@pytest.fixture
def checker(monkeypatch) -> _FakeChecker:
    fake = _FakeChecker()
    monkeypatch.setattr("deep_research.agents.evidence_verifier.StatementCheckItem",
                        _FakeStatementCheckItem, raising=False)
    monkeypatch.setattr("deep_research.agents.evidence_verifier.check_statements", fake, raising=False)
    return fake


@pytest.fixture
def tracker() -> Tracker:
    return Tracker(LangSmithRuntimeConfig(tracing_enabled=False))


def _state(*, reader_answers: list[ReaderAnswer] | None = None) -> ResearchState:
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    finding = _checked("https://a.test/1", "The EIA reported 10.4 GW in 2024.", "10.4", "GW", organisation=EIA)
    return ResearchState(session_id="s1", original_question="How much capacity was added?",
                         sub_topics=[_topic("topic-01", "Capacity added", [target])],
                         verified_findings=[finding], reader_answers=reader_answers or [])


def _section(task) -> ReportSection:
    finding = task.findings[0]
    statement = ReportStatement(statement_id="S001", text="The EIA reported 10.4 GW in 2024.",
                                finding_ids=[finding_fingerprint(finding)])
    return ReportSection(title="Capacity added", coverage_id="topic-01",
                         points=[ReportPoint(text=statement.text, statement=statement)])


# --- the request (AC22) ----------------------------------------------------------------


def test_bottom_line_request_reader_answers_and_ids(tracker, tmp_path: Path) -> None:
    writer = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = writer.build_task(_state(reader_answers=ANSWERS))
    assert task.reader_answers == ANSWERS

    body = bottom_line_messages(task, [_section(task)])[-1].content

    assert (
        "# Reader answers\n"
        "- How many picks do you want? Just one (the reader's answer)\n"
        "- Which area? San Jose (a best guess; the reader did not answer)\n\n"
        "# Checked statements\n"
    ) in body
    assert "Plan within these answers" not in body
    assert body.index("# Answer form\n") < body.index("# Reader answers\n") < body.index("# Checked statements\n")
    assert "## topic-01 \u00b7 Capacity added\n- The EIA reported 10.4 GW in 2024. (cites F01)" in body
    without = writer.build_task(_state())
    assert "\n# Reader answers\n" not in bottom_line_messages(without, [_section(without)])[-1].content


def test_a_bottom_line_redraft_prefixes_its_answer_and_topic_lines(tracker, tmp_path: Path) -> None:
    writer = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = writer.build_task(_state())
    answer = ReportPoint(text="Old answer.", statement=ReportStatement(statement_id="S001", text="Old answer."))
    line = ReportPoint(text="Old line.", statement=ReportStatement(statement_id="S002", text="Old line."))

    body = bottom_line_messages(task, [], previous=[answer, line], previous_topics={"S002": "topic-01"})[-1].content

    assert "# Your previous bottom line\n- answer: Old answer.\n- topic-01: Old line." in body


@pytest.mark.asyncio
async def test_answer_overflow_refused(checker, tracker, tmp_path: Path) -> None:
    writer = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = writer.build_task(_state())
    sentence = "The EIA reported 10.4 GW in 2024."
    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Capacity added", points=[WriterPointDraft(text=sentence, finding_labels=["F01"])]),
        # Three answer sentences that state no figure, so no restatement guard refuses
        # one: only the two-sentence cap can.
        BottomLineDraft(sentences=[
            WriterPointDraft(text="The EIA reports that capacity grew.", finding_labels=["F01"]),
            WriterPointDraft(text="The EIA counts the additions by year.", finding_labels=["F01"]),
            WriterPointDraft(text="The EIA tracks storage additions.", finding_labels=["F01"]),
        ]),
    ])

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert len(composition.summary) == 2
    [refused] = [r for r in composition.rejected_points if r.where.startswith("bottom_line")]
    assert (refused.where, refused.reason) == ("bottom_line[2]", "over the direct answer's two sentences")
    assert len(checker.calls[-1]) == 2  # the overflow never spends a check


@pytest.mark.asyncio
async def test_missing_outcome_reask_text(checker, tracker, tmp_path: Path) -> None:
    target = make_target("topic-01-target-01", coverage_id="topic-01", required=True)
    cause = _statement_finding("https://a.test/1", "A funding cut reduced the budget in 2018.",
                               target_ids=["topic-01-target-01"])
    outcome = _statement_finding("https://a.test/2", "The programme closed in 2020.",
                                 target_ids=["topic-01-target-01"])
    contract = AnswerContract(
        question="Why did the programme close?", scope_statement="Answered as of 2026-09-24.",
        geographic_scope="worldwide", as_of_date="2026-09-24",
        evidence_period_requirement="the period the question names", assumptions=[],
        answer_kind="explanation", requested_word_limit=500,
    )
    state = ResearchState(session_id="s1", original_question="Why did the programme close?",
                          sub_topics=[_topic("topic-01", "Programme closure", [target])],
                          verified_findings=[cause, outcome], answer_contract=contract)
    seen: list[str] = []

    def bottom_line(messages, schema):
        seen.append(messages[-1].content)
        return BottomLineDraft(sentences=[WriterPointDraft(
            text="A funding cut reduced the budget in 2018, restated.", finding_labels=["F01"])])

    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Programme closure", points=[
            WriterPointDraft(text="According to the source, a funding cut reduced the budget in 2018.",
                             finding_labels=["F01"]),
            WriterPointDraft(text="According to the source, the programme closed in 2020.",
                             finding_labels=["F02"], outcome=True),
        ]),
        bottom_line, bottom_line,
    ])
    agent = _writer(tracker, completer, report_writer_tools(tracker, output_root=tmp_path))

    await compose_written_report(agent.build_task(state), provider=completer, section_concurrency=7)

    assert len(seen) == 2
    assert (
        'The bottom line names no outcome. State the outcome the statements under "Outcome" '
        "state, credited and dated as they state it, within the answer's two sentences: fold "
        "it into the last answer sentence or replace one, never add a third."
    ) in seen[1]


# --- the prompts and the reply examples (AC36) -------------------------------------------


def test_bottom_line_examples_valid_and_name_topics() -> None:
    assert len(_BOTTOM_LINE_REPLY_EXAMPLES) == 2
    render_structured_reply_format(_BOTTOM_LINE_REPLY_EXAMPLES)  # one-line labels, JSON objects
    for label, payload in _BOTTOM_LINE_REPLY_EXAMPLES:
        draft = BottomLineDraft.model_validate_json(payload)
        assert 1 <= len(draft.sentences) <= 2
        assert draft.topics
        headed = set(re.findall(r"## (\S+) \u00b7 ", label))
        assert {line.topic for line in draft.topics} <= headed, label
        for point in [*draft.sentences, *draft.topics]:
            for mark in point.items:
                for span in (mark.name, mark.verdict):
                    if span:
                        assert len(span) <= _MARK_SPAN_CHARS
                        assert cosmetic_text(span) in cosmetic_text(point.text), (span, point.text)


def test_the_request_shows_both_examples_under_its_reply_format(tracker, tmp_path: Path) -> None:
    writer = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = writer.build_task(_state())
    body = bottom_line_messages(task, [_section(task)])[-1].content
    reply_format = body.split("# Reply format\n", 1)[1].split("\n# ", 1)[0]
    for label, payload in _BOTTOM_LINE_REPLY_EXAMPLES:
        compact = json.dumps(json.loads(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assert f"{label}\nExample JSON output:\n{compact}" in reply_format


def test_no_old_sentence_cap_name_remains() -> None:
    root = Path(__file__).resolve().parents[2]
    needle = re.compile(r"\bMAX_BOTTOM_LINE" + r"_SENTENCES\b")
    hits = [
        str(path.relative_to(root))
        for folder in ("src", "tests")
        for path in (root / folder).rglob("*.py")
        if needle.search(path.read_text(encoding="utf-8"))
    ]
    assert hits == []


def test_the_rules_ask_for_the_answer_then_one_line_per_topic() -> None:
    assert BOTTOM_LINE_INSTRUCTION.splitlines()[1].startswith(
        "- sentences: one or two sentences that answer the question directly"
    )
    assert BOTTOM_LINE_INSTRUCTION.splitlines()[2].startswith("- topics: one line per topic listed")
    assert "within the two to four sentences" not in BOTTOM_LINE_INSTRUCTION
    assert "- short_title names the same part in one to three words for a contents list" in SECTION_INSTRUCTION


# --- the section's short title (§7.2) ----------------------------------------------------


@pytest.mark.parametrize(
    ("drafted", "expected"),
    [
        ("Capacity", "Capacity"),
        ("  Opening\nhours ", "Opening hours"),
        ("Value for money", "Value for money"),
        ("Four words are many", "Capacity added in 2024"),
        ("Added in 2024", "Capacity added in 2024"),
        ("Best picks", "Capacity added in 2024"),
        ("Supercalifragilisticexpia", "Capacity added in 2024"),
        ("", "Capacity added in 2024"),
    ],
)
def test_a_short_title_keeps_one_to_three_plain_words(drafted: str, expected: str) -> None:
    assert _section_short_title(drafted, "Capacity added in 2024") == expected


@pytest.mark.asyncio
async def test_a_written_section_carries_its_short_title(checker, tracker, tmp_path: Path) -> None:
    writer = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = writer.build_task(_state())
    sentence = "The EIA reported 10.4 GW in 2024."
    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Capacity added", short_title="Capacity",
                     points=[WriterPointDraft(text=sentence, finding_labels=["F01"])]),
        BottomLineDraft(sentences=[WriterPointDraft(text=sentence, finding_labels=["F01"])]),
    ])

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert [(s.title, s.short_title) for s in composition.sections] == [("Capacity added", "Capacity")]
