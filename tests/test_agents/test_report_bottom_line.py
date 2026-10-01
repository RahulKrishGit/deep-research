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
from deep_research.graph.live import bind_live_sink
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.utils.types import (
    AnswerContract,
    BottomLineDraft,
    ReaderAnswer,
    ReportPoint,
    ReportSection,
    ReportStatement,
    ResearchEvent,
    ResearchState,
    SectionDraft,
    TopicLineDraft,
    WriterPointDraft,
)
from tests.agent_fakes import ScriptedCompleter
from tests.evidence_fakes import make_target
from tests.graph_fakes import fake_reader_note
from tests.research_fakes import report_writer_tools
from tests.test_agents.test_report_writer import (
    EIA,
    _checked,
    _FakeChecker,
    _FakeStatementCheckItem,
    _output_limit_error,
    _statement_finding,
    _topic,
    _verdict,
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
        # The two examples the prompt itself gives the model (§7.1): both must be kept, so a
        # model that follows the prompt gets its own label, not the section's full title.
        ("Published picks", "Published picks"),
        ("Opening hours", "Opening hours"),
        ("Four words are many", "Capacity added in 2024"),
        ("Added in 2024", "Capacity added in 2024"),
        ("Best picks", "Capacity added in 2024"),
        ("Top value", "Capacity added in 2024"),
        ("Worst case", "Capacity added in 2024"),
        ("Winners", "Capacity added in 2024"),
        ("Leading models", "Capacity added in 2024"),
        ("Recommended models", "Capacity added in 2024"),
        ("Supercalifragilisticexpia", "Capacity added in 2024"),
        ("", "Capacity added in 2024"),
    ],
)
def test_a_short_title_keeps_one_to_three_plain_words(drafted: str, expected: str) -> None:
    assert _section_short_title(drafted, "Capacity added in 2024") == expected


def test_the_prompts_short_title_examples_are_the_ones_the_check_keeps() -> None:
    """The parametrized examples above are the prompt's own two (§7.1): the prompt contains
    them, and the check keeps each one against a long section title, so they cannot drift."""
    assert '("Published picks", "Opening hours")' in SECTION_INSTRUCTION
    for name in ("Published picks", "Opening hours"):
        assert _section_short_title(name, "Capacity added in 2024 across the state") == name


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


# --- topic lines and the layout (§7.1, §7.2) -----------------------------------------------


def _two_part_state(*, notes=(), note_topic=None):
    t1 = make_target("topic-01-target-01", coverage_id="topic-01", required=True, unit_dimension=None)
    t2 = make_target("topic-02-target-01", coverage_id="topic-02", required=True, unit_dimension=None)
    f1 = _statement_finding("https://one.test/1", "According to the source, part one holds.",
                            target_ids=["topic-01-target-01"])
    f2 = _statement_finding("https://two.test/1", "According to the source, part two holds.",
                            target_ids=["topic-02-target-01"])
    topics = [_topic("topic-01", "Part one", [t1]), _topic("topic-02", "Part two", [t2])]
    findings = [f1, f2]
    if note_topic is not None:
        topic, finding = note_topic
        topics.append(topic)
        findings.append(finding)
    return ResearchState(session_id="s1", original_question="Q?", sub_topics=topics,
                         verified_findings=findings, reader_notes=list(notes),
                         reader_answers=ANSWERS)


def _labels(task) -> dict[str, str]:
    return {finding.source_url: label for label, finding in task.registry}


def _sections_route(task, extra=None):
    labels = _labels(task)

    def route(messages, schema):
        body = messages[-1].content
        part = re.search(r"# This part of the question\n(.+)", body).group(1)
        url = {"Part one": "https://one.test/1", "Part two": "https://two.test/1"}.get(part, "https://note.test/1")
        text = {"Part one": "According to the source, part one holds.",
                "Part two": "According to the source, part two holds."}.get(part, "According to the source, pastries are sold.")
        short = {"Part one": "One", "Part two": "Two"}.get(part, "Pastries")
        return SectionDraft(title=part, short_title=short,
                            points=[WriterPointDraft(text=text, finding_labels=[labels[url]])])

    return route


@pytest.mark.asyncio
async def test_bottom_line_topic_line_rules(checker, tracker, tmp_path: Path) -> None:
    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(_two_part_state())
    labels = _labels(task)
    one, two = labels["https://one.test/1"], labels["https://two.test/1"]
    draft = BottomLineDraft(
        sentences=[WriterPointDraft(text="According to the source, part one holds.", finding_labels=[one])],
        topics=[
            TopicLineDraft(topic="topic-09", text="According to the source, part one holds.", finding_labels=[one]),
            TopicLineDraft(topic="topic-01", text="According to the source, part one holds.", finding_labels=[one]),
            TopicLineDraft(topic="topic-01", text="According to the source, part one holds again.", finding_labels=[one]),
            TopicLineDraft(topic="topic-02", text="According to the source, part one holds here.", finding_labels=[one]),
        ],
    )
    route = _sections_route(task)
    completer = ScriptedCompleter(outputs=[route, route, draft])

    composition = await compose_written_report(task, provider=completer, section_concurrency=1)

    refused = {r.where: r.reason for r in composition.rejected_points}
    assert refused == {
        "bottom_line.topics[0]": "a line for a topic the request did not list",
        "bottom_line.topics[2]": "a second line for one topic",
        "bottom_line.topics[3]": "a topic line cites a finding its topic does not",
    }
    layout = composition.bottom_line
    assert layout is not None and layout.answer_ids == ["S001"]
    assert [(line.coverage_id, line.label) for line in layout.topic_lines] == [("topic-01", "One")]
    assert two  # the second part's own label exists; its topic simply has no kept line


@pytest.mark.asyncio
async def test_bottom_line_layout_and_order(checker, tracker, tmp_path: Path) -> None:
    """Spec §7.2: summary holds the answer, the plan's topic lines in plan order,
    then the notes' topic lines in receipt order -- whatever order the reply
    gave them -- and the layout labels a note's topic ``Your note · {short}``."""

    note = fake_reader_note("n2", kinds=["new_angle"], restatement="pastries at the cafés",
                            short="pastries")
    note_target = make_target("note-n2-target-01", coverage_id="note-n2", required=True, unit_dimension=None)
    note_finding = _statement_finding("https://note.test/1", "According to the source, pastries are sold.",
                                      target_ids=["note-n2-target-01"])
    note_topic = _topic("note-n2", "Your note: pastries at the cafés", [note_target], priority=3)
    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(_two_part_state(notes=[note], note_topic=(note_topic, note_finding)))
    assert task.note_labels == {"note-n2": "Your note \u00b7 pastries"}
    labels = _labels(task)
    one, two, three = (labels[u] for u in ("https://one.test/1", "https://two.test/1", "https://note.test/1"))
    draft = BottomLineDraft(
        sentences=[WriterPointDraft(text="According to the source, part one holds.", finding_labels=[one])],
        topics=[
            TopicLineDraft(topic="note-n2", text="According to the source, pastries are sold.", finding_labels=[three]),
            TopicLineDraft(topic="topic-02", text="According to the source, part two holds.", finding_labels=[two]),
            TopicLineDraft(topic="topic-01", text="According to the source, part one holds.", finding_labels=[one]),
        ],
    )
    route = _sections_route(task)
    completer = ScriptedCompleter(outputs=[route, route, route, draft])

    composition = await compose_written_report(task, provider=completer, section_concurrency=1)

    assert [p.text for p in composition.summary] == [
        "According to the source, part one holds.",
        "According to the source, part one holds.",
        "According to the source, part two holds.",
        "According to the source, pastries are sold.",
    ]
    layout = composition.bottom_line
    assert layout.answer_ids == ["S001"]
    assert [(t.coverage_id, t.label, t.statement_id) for t in layout.topic_lines] == [
        ("topic-01", "One", "S002"), ("topic-02", "Two", "S003"),
        ("note-n2", "Your note \u00b7 pastries", "S004"),
    ]
    assert composition.reader_answers == ["Just one", "San Jose"]


@pytest.mark.asyncio
async def test_a_topic_line_may_restate_the_answer_fact(checker, tracker, tmp_path: Path) -> None:
    writer = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = writer.build_task(_state())
    sentence = "The EIA reported 10.4 GW in 2024."
    completer = ScriptedCompleter(outputs=[
        SectionDraft(title="Capacity added", points=[WriterPointDraft(text=sentence, finding_labels=["F01"])]),
        BottomLineDraft(sentences=[WriterPointDraft(text=sentence, finding_labels=["F01"])],
                        topics=[TopicLineDraft(topic="topic-01", text=sentence, finding_labels=["F01"])]),
    ])

    composition = await compose_written_report(task, provider=completer, section_concurrency=7)

    assert [p.text for p in composition.summary] == [sentence, sentence]
    assert not [r for r in composition.rejected_points if r.reason.startswith("restates")]


# --- the fallback (§7.3, AC24) -------------------------------------------------------------


def _five_part_state() -> ResearchState:
    topics, findings = [], []
    for n in range(1, 6):
        cid = f"topic-{n:02d}"
        target = make_target(f"{cid}-target-01", coverage_id=cid, required=(n != 2), unit_dimension=None)
        topics.append(_topic(cid, f"Part {n}", [target], priority=n))
        findings.append(_statement_finding(f"https://p{n}.test/1", f"According to the source, part {n} holds.",
                                           target_ids=[target.target_id]))
        findings.append(_statement_finding(f"https://p{n}.test/2", f"According to the source, part {n} also holds.",
                                           target_ids=[target.target_id]))
    return ResearchState(session_id="s1", original_question="Q?", sub_topics=topics, verified_findings=findings)


def _five_part_route(task):
    labels = _labels(task)

    def route(messages, schema):
        part = re.search(r"# This part of the question\n(.+)", messages[-1].content).group(1)
        n = part.split()[-1]
        points = [WriterPointDraft(text=f"According to the source, part {n} holds.",
                                   finding_labels=[labels[f"https://p{n}.test/1"]])]
        if n != "5":  # part 5 keeps one point only, so the fallback's move empties it
            points.append(WriterPointDraft(text=f"According to the source, part {n} also holds.",
                                           finding_labels=[labels[f"https://p{n}.test/2"]]))
        return SectionDraft(title=part, short_title=f"P{'abcde'[int(n) - 1]}", points=points)

    return route


@pytest.mark.asyncio
async def test_fallback_one_line_per_topic(checker, tracker, tmp_path: Path) -> None:
    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(_five_part_state())
    route = _five_part_route(task)
    completer = ScriptedCompleter(outputs=[route] * 5 + [_output_limit_error(), _output_limit_error()])

    composition = await compose_written_report(task, provider=completer, section_concurrency=1)

    # Plan order, the optional part included, and no cap of four.
    assert [p.text for p in composition.summary] == [
        f"According to the source, part {n} holds." for n in range(1, 6)
    ]
    layout = composition.bottom_line
    assert layout.assembled and layout.answer_ids == []
    assert [(t.coverage_id, t.label) for t in layout.topic_lines] == [
        ("topic-01", "Pa"), ("topic-02", "Pb"), ("topic-03", "Pc"), ("topic-04", "Pd"), ("topic-05", "Pe"),
    ]
    assert "The bottom-line draft failed twice; one checked section point per topic stands in for it." in [
        e.message for e in composition.errors
    ]


@pytest.mark.asyncio
async def test_fallback_move_drops_emptied_section(checker, tracker, tmp_path: Path) -> None:
    """Review M9: a picked point leaves its section, so a part whose only kept
    point is picked loses its section; its line stays in the bottom line."""
    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(_five_part_state())
    route = _five_part_route(task)
    completer = ScriptedCompleter(outputs=[route] * 5 + [_output_limit_error(), _output_limit_error()])

    composition = await compose_written_report(task, provider=completer, section_concurrency=1)

    assert [s.coverage_id for s in composition.sections] == ["topic-01", "topic-02", "topic-03", "topic-04"]
    assert [p.text for s in composition.sections for p in s.points] == [
        f"According to the source, part {n} also holds." for n in range(1, 5)
    ]
    assert composition.bottom_line.topic_lines[-1].label == "Pe"


@pytest.mark.asyncio
async def test_every_sentence_refused_falls_back_to_one_line_per_topic(checker, tracker, tmp_path: Path) -> None:
    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(_two_part_state())
    route = _sections_route(task)
    refused = BottomLineDraft(sentences=[WriterPointDraft(text="An unlabelled claim.", finding_labels=["F99"])])
    completer = ScriptedCompleter(outputs=[route, route, refused])

    composition = await compose_written_report(task, provider=completer, section_concurrency=1)

    assert composition.bottom_line.assembled
    assert [t.coverage_id for t in composition.bottom_line.topic_lines] == ["topic-01", "topic-02"]
    assert (
        "Every drafted bottom-line sentence was refused; one checked section point per topic stands in for it."
        in [e.message for e in composition.errors]
    )


_NO_ANSWER_ERROR = "report_writer_bottom_line_no_answer"


@pytest.mark.asyncio
async def test_a_bottom_line_of_topic_lines_alone_records_that_it_has_no_answer(
    checker, tracker, tmp_path: Path,
) -> None:
    """Final wave (Phase C review P2-1, writer half): every answer sentence refused but the
    topic lines kept is published with ``answer_ids == []``; the writer records a recoverable
    error, so the missing direct answer is in the run's record and not only in the report."""
    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(_two_part_state())
    labels = _labels(task)
    one, two = labels["https://one.test/1"], labels["https://two.test/1"]
    route = _sections_route(task)
    draft = BottomLineDraft(
        sentences=[WriterPointDraft(text="An unlabelled claim.", finding_labels=["F99"])],
        topics=[
            TopicLineDraft(topic="topic-01", text="According to the source, part one holds.", finding_labels=[one]),
            TopicLineDraft(topic="topic-02", text="According to the source, part two holds.", finding_labels=[two]),
        ],
    )

    composition = await compose_written_report(
        task, provider=ScriptedCompleter(outputs=[route, route, draft]), section_concurrency=1,
    )

    layout = composition.bottom_line
    assert layout is not None and not layout.assembled
    assert layout.answer_ids == []
    assert [line.coverage_id for line in layout.topic_lines] == ["topic-01", "topic-02"]
    [error] = [e for e in composition.errors if e.error_type == _NO_ANSWER_ERROR]
    assert error.recoverable is True
    assert error.source == "agent.report_writer"
    assert error.details == {}
    assert error.message == (
        "No direct-answer sentence was kept after the check; the bottom line holds the topic lines alone."
    )


@pytest.mark.asyncio
async def test_a_bottom_line_with_a_kept_answer_records_no_missing_answer(
    checker, tracker, tmp_path: Path,
) -> None:
    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(_two_part_state())
    labels = _labels(task)
    one, two = labels["https://one.test/1"], labels["https://two.test/1"]
    route = _sections_route(task)
    draft = BottomLineDraft(
        sentences=[WriterPointDraft(text="According to the source, part one holds.", finding_labels=[one])],
        topics=[
            TopicLineDraft(topic="topic-01", text="According to the source, part one holds.", finding_labels=[one]),
            TopicLineDraft(topic="topic-02", text="According to the source, part two holds.", finding_labels=[two]),
        ],
    )

    composition = await compose_written_report(
        task, provider=ScriptedCompleter(outputs=[route, route, draft]), section_concurrency=1,
    )

    assert composition.bottom_line is not None and composition.bottom_line.answer_ids == ["S001"]
    assert _NO_ANSWER_ERROR not in {e.error_type for e in composition.errors}


async def _fraction_events(compose) -> list[float]:
    """Every ``report_writer.progress`` ``fraction`` a composition publishes live."""
    received: list[ResearchEvent] = []
    with bind_live_sink(received.append):
        await compose()
    return [e.metadata["fraction"] for e in received if e.event_type == "report_writer.progress"]


@pytest.mark.asyncio
async def test_writing_progress_ends_full_when_the_bottom_line_falls_back(checker, tracker, tmp_path: Path) -> None:
    """Review I1: a bottom line that failed twice never reaches the Statement Check, so
    its scope enters the counts only when the composition settles it; the bar ends
    at 1.0, never below, and never goes down."""
    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(_five_part_state())
    route = _five_part_route(task)
    completer = ScriptedCompleter(outputs=[route] * 5 + [_output_limit_error(), _output_limit_error()])

    fractions = await _fraction_events(
        lambda: compose_written_report(task, provider=completer, section_concurrency=1)
    )

    assert fractions == sorted(fractions) and fractions[-1] == 1.0


@pytest.mark.asyncio
async def test_writing_progress_ends_full_when_every_bottom_line_sentence_is_refused(
    checker, tracker, tmp_path: Path,
) -> None:
    """Review I1: every drafted bottom-line sentence refused before the check leaves
    the bottom line nothing to count; the bar still ends full."""
    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(_two_part_state())
    route = _sections_route(task)
    refused = BottomLineDraft(sentences=[WriterPointDraft(text="An unlabelled claim.", finding_labels=["F99"])])
    completer = ScriptedCompleter(outputs=[route, route, refused])

    fractions = await _fraction_events(
        lambda: compose_written_report(task, provider=completer, section_concurrency=1)
    )

    assert fractions == sorted(fractions) and fractions[-1] == 1.0


@pytest.mark.asyncio
async def test_a_reask_shows_its_previous_lines_by_topic(checker, tracker, tmp_path: Path) -> None:
    agent = _writer(tracker, ScriptedCompleter(), report_writer_tools(tracker, output_root=tmp_path))
    task = agent.build_task(_two_part_state())
    labels = _labels(task)
    one = labels["https://one.test/1"]
    checker.verdicts["BT02"] = _verdict_inconsistent()
    seen: list[str] = []
    first = BottomLineDraft(
        sentences=[WriterPointDraft(text="According to the source, part one holds.", finding_labels=[one])],
        topics=[TopicLineDraft(topic="topic-01", text="According to the source, part one holds.", finding_labels=[one]),
                TopicLineDraft(topic="topic-02", text="According to the source, part two is wrong.",
                               finding_labels=[labels["https://two.test/1"]])],
    )

    def reask(messages, schema):
        seen.append(messages[-1].content)
        return first

    route = _sections_route(task)
    completer = ScriptedCompleter(outputs=[route, route, first, reask])

    await compose_written_report(task, provider=completer, section_concurrency=1)

    assert (
        "# Your previous bottom line\n- answer: According to the source, part one holds.\n"
        "- topic-01: According to the source, part one holds."
    ) in seen[0]


def _verdict_inconsistent():
    return _verdict("inconsistent", reason="No cited finding supports this claim.")
