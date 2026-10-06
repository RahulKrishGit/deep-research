"""The reader report's structure.

The Markdown prints ``# question``, the evidence line, ``## Bottom line`` (the
answer, then one line per topic), one ``##`` per topic section, ``## Key figures``
or ``## Options compared``, ``## What we couldn't confirm``, ``## Sources`` and the
evidence-log link; ``report_outline`` names the same ``##`` headings, in order.
"""

from __future__ import annotations

from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report import render_written_report, report_outline
from deep_research.utils.types import (
    BottomLineLayout,
    BottomLineTopic,
    FindingVerification,
    NotFoundTarget,
    ReportComposition,
    ReportPoint,
    ReportSection,
    ReportStatement,
    ReportTable,
    TableCell,
)
from tests.evidence_fakes import make_finding, make_read

ASSEMBLED = "*Assembled from the sections below; the summary could not be written this time.*"
NO_ANSWER = "*The direct answer could not be checked this time; each topic's checked line follows.*"


def _finding(n: int):
    read = make_read(f"Body {n}.", url=f"https://s{n}.example.test/page", title=f"Page {n}")
    finding = make_finding(read, f"Body {n}.")
    return finding.model_copy(update={"verification": FindingVerification(status="verified")})


def _point(statement_id: str, text: str, finding) -> ReportPoint:
    return ReportPoint(text=text, source_urls=[finding.source_url], statement=ReportStatement(
        statement_id=statement_id, text=text, finding_ids=[finding_fingerprint(finding)]))


def _composition(*, table_shape: str | None = "findings", layout: bool = True, assembled: bool = False,
                 note_topic: bool = False, emptied: bool = False, answers: list[str] | None = None) -> ReportComposition:
    f = [_finding(n) for n in range(1, 6)]
    answer = _point("S001", "Agency One reports the answer.", f[0])
    line_one = _point("S002", "Agency One reports part one.", f[0])
    line_two = _point("S003", "Agency Two reports part two.", f[1])
    sections = [
        ReportSection(title="Part one in full", short_title="Part one", coverage_id="topic-01",
                      points=[_point("S004", "Agency Three reports more on part one.", f[2])]),
        ReportSection(title="Part two in full", short_title="Part two", coverage_id="topic-02",
                      points=[] if emptied else [_point("S005", "Agency Four reports more on part two.", f[3])]),
    ]
    if note_topic:
        sections.append(ReportSection(title="Your note: pastries at the cafés", short_title="Pastries",
                                      coverage_id="note-n2",
                                      points=[_point("S006", "Agency Four reports pastries.", f[3])]))
    table = None
    if table_shape is not None:
        table = ReportTable(shape=table_shape, columns=["What", "Figure", "Source"],
                            rows=[[TableCell(text="A · measure"), TableCell(text="1"),
                                   TableCell(text="Agency Five", finding_ids=[finding_fingerprint(f[4])])]])
    summary = [answer, line_one, line_two]
    bottom_line = None
    if layout:
        bottom_line = BottomLineLayout(
            answer_ids=[] if assembled else ["S001"],
            topic_lines=[BottomLineTopic(coverage_id="topic-01", label="Part one", statement_id="S002"),
                         BottomLineTopic(coverage_id="topic-02", label="Part two", statement_id="S003")],
            assembled=assembled,
        )
        if assembled:
            summary = [line_one, line_two]
    return ReportComposition(
        question="What is the answer?", session_id="s1", as_of="2026-09-30T00:00:00+00:00",
        findings=f, summary=summary, sections=sections, table=table, bottom_line=bottom_line,
        not_found=[NotFoundTarget(target_id="topic-03-target-01", question="What about part three?", searched=True)],
        reader_answers=answers or [],
    )


def _headings(markdown: str) -> list[str]:
    return [line for line in markdown.splitlines() if line.startswith("## ")]


def _bottom_line(markdown: str) -> str:
    return markdown.split("## Bottom line\n\n", 1)[1].split("\n\n## ", 1)[0]


def test_markdown_heading_order() -> None:
    markdown = render_written_report(_composition())
    assert markdown.splitlines()[0] == "# What is the answer?"
    assert _headings(markdown) == [
        "## Bottom line", "## Part one in full", "## Part two in full", "## Key figures",
        "## What we couldn't confirm", "## Sources",
    ]
    assert markdown.rstrip().splitlines()[-1].startswith("How this was researched: [evidence log](")
    options = render_written_report(_composition(table_shape="options"))
    assert "## Options compared" in _headings(options) and "## Key figures" not in _headings(options)
    assert "## Key figures" not in render_written_report(_composition(table_shape=None))


def test_report_outline_matches_headings() -> None:
    for composition in (
        _composition(), _composition(table_shape="options"), _composition(table_shape=None),
        _composition(layout=False), _composition(note_topic=True), _composition(emptied=True),
        ReportComposition(question="q", session_id="s"),
    ):
        outline = report_outline(composition)
        assert [f"## {entry.heading}" for entry in outline] == _headings(render_written_report(composition))


def test_report_outline_labels_and_numbers_the_topics() -> None:
    outline = report_outline(_composition(note_topic=True))
    assert [(e.kind, e.label, e.topic_index, e.topic_count, e.note_id) for e in outline] == [
        ("bottom_line", "Bottom line", None, None, None),
        ("topic", "Part one", 1, 3, None),
        ("topic", "Part two", 2, 3, None),
        ("topic", "Pastries (your note)", 3, 3, "n2"),
        ("key_figures", "Key figures", None, None, None),
        ("not_confirmed", "Not confirmed", None, None, None),
        ("sources", "Sources", None, None, None),
    ]


def test_the_outline_numbers_only_the_topic_sections_that_remain() -> None:
    """A section the fallback emptied prints no heading and takes no number."""
    topics = [e for e in report_outline(_composition(emptied=True)) if e.kind == "topic"]
    assert [(e.heading, e.topic_index, e.topic_count) for e in topics] == [("Part one in full", 1, 1)]


def test_bottom_line_layout_and_markdown() -> None:
    body = _bottom_line(render_written_report(_composition()))
    assert body == (
        "Agency One reports the answer [1].\n\n"
        "- **Part one:** Agency One reports part one [1].\n"
        "- **Part two:** Agency Two reports part two [2]."
    )


def test_an_assembled_bottom_line_says_so_above_its_topic_lines() -> None:
    body = _bottom_line(render_written_report(_composition(assembled=True)))
    assert body.splitlines()[0] == ASSEMBLED
    assert body.splitlines()[2:] == [
        "- **Part one:** Agency One reports part one [1].",
        "- **Part two:** Agency Two reports part two [2].",
    ]


def test_a_layout_with_no_printable_answer_says_so_above_its_topic_lines() -> None:
    """No answer line printed (none named, or none found in the summary) and not
    assembled -- the topic lines follow one italic disclosure line, never a silent gap."""
    topic_lines = [
        "- **Part one:** Agency One reports part one [1].",
        "- **Part two:** Agency Two reports part two [2].",
    ]
    for answer_ids in ([], ["S999"]):
        composition = _composition()
        layout = composition.bottom_line.model_copy(update={"answer_ids": answer_ids})
        body = _bottom_line(render_written_report(composition.model_copy(update={"bottom_line": layout})))
        assert body.splitlines()[0] == NO_ANSWER
        assert body.splitlines()[2:] == topic_lines


def test_a_composition_without_a_layout_renders_its_bottom_line_as_one_paragraph() -> None:
    body = _bottom_line(render_written_report(_composition(layout=False)))
    assert body == (
        "Agency One reports the answer [1]. Agency One reports part one [1]. "
        "Agency Two reports part two [2]."
    )


def test_the_evidence_line_carries_the_readers_answers() -> None:
    markdown = render_written_report(_composition(answers=["San Jose plus nearby South Bay cities", "Top 3-5 standout spots"]))
    assert markdown.splitlines()[2] == (
        "Evidence as of 2026-09-30 \u00b7 5 sources \u00b7 San Jose plus nearby South Bay cities "
        "\u00b7 Top 3-5 standout spots"
    )
    undated = _composition(answers=["San Jose"]).model_copy(update={"as_of": ""})
    assert render_written_report(undated).splitlines()[2] == "No source could be checked."


def test_citations_are_numbered_bottom_line_then_sections_then_table() -> None:
    markdown = render_written_report(_composition())
    sources = markdown.split("## Sources\n\n", 1)[1].splitlines()
    assert [line.split(" \u2014 ")[0] for line in sources[:5]] == [
        "1. s1.example.test", "2. s2.example.test", "3. s3.example.test", "4. s4.example.test",
        "5. s5.example.test",
    ]
