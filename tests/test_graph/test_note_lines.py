"""The reader's notes in the bottom line (notes-progress-report spec §7.2, §7.5; AC23, AC34).

At publication each active note gets one line, stamped from its terminal outcome:
a research note points at its topic's kept line or names its outcome; a steering
note says how the report treated it; a mixed note shows both halves. Marks are
✓ (covered), ✗ (not found or not followed) and none (not checked).
"""

from __future__ import annotations

import pytest

from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report import render_written_report
from deep_research.agents.report_reviewer import composition_semantic_fingerprint
from deep_research.graph.nodes import _terminal_artifacts
from deep_research.graph.note_outcomes import report_note_lines
from deep_research.utils.types import (
    BottomLineLayout,
    BottomLineTopic,
    EvidenceTarget,
    FindingVerification,
    NotFoundTarget,
    ReportComposition,
    ReportNoteLine,
    ReportPoint,
    ReportQualitySnapshot,
    ReportSection,
    ReportStatement,
    ResearchState,
    SubTopic,
)
from tests.evidence_fakes import make_finding, make_read
from tests.graph_fakes import fake_reader_note, fake_report_review


def _finding(n: int):
    read = make_read(f"Body {n}.", url=f"https://s{n}.example.test/page", title=f"Page {n}")
    return make_finding(read, f"Body {n}.").model_copy(
        update={"verification": FindingVerification(status="verified")})


def _point(statement_id: str, text: str, finding) -> ReportPoint:
    return ReportPoint(text=text, source_urls=[finding.source_url], statement=ReportStatement(
        statement_id=statement_id, text=text, finding_ids=[finding_fingerprint(finding)]))


def _topic(coverage_id: str, title: str) -> SubTopic:
    return SubTopic(
        coverage_id=coverage_id, title=title, rationale="r", search_queries=["q"],
        success_criteria=["c"], priority=1,
        evidence_targets=[EvidenceTarget(target_id=f"{coverage_id}-target-01", coverage_id=coverage_id,
                                         question="q", required=True, measure="m")],
    )


PASTRIES = fake_reader_note("n1", kinds=["new_angle"], restatement="pastries at the cafés", short="pastries")
CLOSED = fake_reader_note("n2", kinds=["exclude"], restatement="leave out cafés that might be closed",
                          short="open now")
SAFETY = fake_reader_note("n3", kinds=["emphasis"], restatement="more weight on fire-safety standards",
                          short="fire safety")
OLD = fake_reader_note("n4", kinds=["emphasis"], restatement="only downtown", short="downtown")
NEWER = fake_reader_note("n5", kinds=["scope"], restatement="only San Jose", short="San Jose", replaces="n4")
MIXED = fake_reader_note("n6", kinds=["new_angle", "exclude"],
                         restatement="pastries, but skip anything that might be closed", short="pastries")


def _state(*notes, answered=("note-n1-target-01",), verdicts=None, note_line=True,
           not_found=(), topic_notes=None) -> ResearchState:
    """``topic_notes``: the notes that own a ``note-{id}`` topic (default: the research
    notes). A steering note owns one when the run bought it a note pass (a ``no_evidence``
    verdict), so its topic can keep a bottom-line line too."""
    f = [_finding(n) for n in range(1, 4)]
    answer = _point("S001", "Agency One reports the answer.", f[0])
    line_one = _point("S002", "Agency One reports part one.", f[0])
    note_one = _point("S003", "Agency Three reports pastries.", f[2])
    owners = {n.note_id for n in notes if "new_angle" in n.kinds} if topic_notes is None else set(topic_notes)
    note_topic = next((n for n in notes if n.note_id in owners), None)
    sub_topics = [_topic("topic-01", "Part one")]
    if note_topic is not None:
        sub_topics.append(_topic(f"note-{note_topic.note_id}", f"Your note: {note_topic.restatement}"))
    topic_lines = [BottomLineTopic(coverage_id="topic-01", label="Part one", statement_id="S002")]
    summary = [answer, line_one]
    if note_topic is not None and note_line:
        topic_lines.append(BottomLineTopic(coverage_id=f"note-{note_topic.note_id}",
                                           label=f"Your note · {note_topic.short}", statement_id="S003"))
        summary.append(note_one)
    composition = ReportComposition(
        question="Where are the best lattes?", session_id="s1", as_of="2026-09-30T00:00:00+00:00",
        findings=f, sub_topics=sub_topics, summary=summary,
        sections=[ReportSection(title="Part one in full", short_title="Part one", coverage_id="topic-01",
                                points=[_point("S004", "Agency Two reports more on part one.", f[1])])],
        bottom_line=BottomLineLayout(answer_ids=["S001"], topic_lines=topic_lines),
        not_found=[NotFoundTarget(target_id=t, question="q", searched=True) for t in not_found],
    )
    return ResearchState(
        session_id="s1", original_question="Where are the best lattes?", sub_topics=sub_topics,
        reader_notes=list(notes), composition=composition,
        quality=ReportQualitySnapshot(answered_target_ids=list(answered)),
        report_review=fake_report_review(note_dispositions=verdicts or {}),
    )


def _bottom_line(markdown: str) -> str:
    return markdown.split("## Bottom line\n\n", 1)[1].split("\n\n## ", 1)[0]


def test_note_lines_stamped_at_publication() -> None:
    state = _state(PASTRIES, CLOSED, SAFETY, OLD, NEWER, verdicts={"n2": "honoured"})

    reader, _, _, finalized = _terminal_artifacts(state, "accepted")

    assert [(line.note_id, line.outcome, line.statement_id, line.text) for line in finalized.reader_note_lines] == [
        ("n1", "covered", "S003", ""),
        ("n2", "covered", None, "Followed: leave out cafés that might be closed"),
        ("n3", "not_checked", None, "Not checked: more weight on fire-safety standards"),
        ("n5", "not_checked", None, "Not checked: only San Jose"),
    ]
    assert _bottom_line(reader) == (
        "Agency One reports the answer [1].\n\n"
        "- **Part one:** Agency One reports part one [1].\n"
        "- **Your note · pastries:** ✓ Agency Three reports pastries [2].\n"
        "- **Your note · open now:** ✓ Followed: leave out cafés that might be closed\n"
        "- **Your note · fire safety:** Not checked: more weight on fire-safety standards\n"
        "- **Your note · San Jose:** Not checked: only San Jose"
    )


_ROW_CASES = [
    # (id, note, state fields, whether the note's `note-{id}` topic kept a bottom-line line)
    ("research-with-line", PASTRIES, {}, True),
    ("research-without-line", PASTRIES,
     {"answered": (), "note_line": False, "not_found": ("note-n1-target-01",)}, False),
    ("steering-with-line", CLOSED, {"verdicts": {"n2": "no_evidence"}, "topic_notes": ("n2",)}, True),
    ("steering-honoured-with-line", CLOSED, {"verdicts": {"n2": "honoured"}, "topic_notes": ("n2",)}, True),
    ("steering-without-line", CLOSED, {"verdicts": {"n2": "no_evidence"}}, False),
    ("mixed-with-line", MIXED,
     {"verdicts": {"n6": "ignored_with_evidence"}, "answered": ("note-n6-target-01",)}, True),
    ("mixed-without-line", MIXED, {"verdicts": {"n6": "honoured"}, "answered": (), "note_line": False}, False),
]


@pytest.mark.parametrize(("note", "fields", "kept"), [case[1:] for case in _ROW_CASES],
                         ids=[case[0] for case in _ROW_CASES])
def test_every_active_note_prints_exactly_one_bottom_line_row(note, fields, kept) -> None:
    """Final review P2-2 (spec §7.2 "one line per note"): whatever the note's kind, and whether
    or not its `note-{id}` topic kept a line, the published bottom line holds one row under
    the note's label, and the topic line's sentence is printed once, inside that row."""
    state = _state(note, **fields)

    reader, _, _, finalized = _terminal_artifacts(state, "accepted")

    rows = [row for row in _bottom_line(reader).splitlines() if row.startswith("- **")]
    label = f"- **Your note \u00b7 {note.short}:**"
    assert len([row for row in rows if row.startswith(label)]) == 1, rows
    assert len(rows) == 2, rows  # "Part one", and the note
    assert _bottom_line(reader).count("Agency Three reports pastries") == (1 if kept else 0)
    [stamped] = finalized.reader_note_lines
    assert (stamped.statement_id is not None) is kept


def test_a_steering_note_that_bought_a_note_pass_prints_its_topic_line_in_its_own_row() -> None:
    """Final review P2-2: the steering note's row is its kept topic line, then its code text,
    under one label -- not a topic row and a second row with the same label."""
    state = _state(CLOSED, verdicts={"n2": "no_evidence"}, topic_notes=("n2",))

    [line] = report_note_lines(state, state.composition)

    assert (line.outcome, line.statement_id) == ("not_found", "S003")
    assert line.text == "No source we could check covers this: leave out cafés that might be closed"
    reader, _, _, _ = _terminal_artifacts(state, "accepted")
    assert _bottom_line(reader) == (
        "Agency One reports the answer [1].\n\n"
        "- **Part one:** Agency One reports part one [1].\n"
        "- **Your note \u00b7 open now:** \u2717 Agency Three reports pastries [2]. "
        "No source we could check covers this: leave out cafés that might be closed"
    )


def test_the_writer_render_prints_a_note_topic_line_as_a_plain_topic_line() -> None:
    """Spec §7.5 item 4: before publication stamps the note lines -- the render the
    reviewer reads -- a note's topic line prints with its label and no mark."""
    state = _state(PASTRIES, CLOSED)
    assert _bottom_line(render_written_report(state.composition)) == (
        "Agency One reports the answer [1].\n\n"
        "- **Part one:** Agency One reports part one [1].\n"
        "- **Your note · pastries:** Agency Three reports pastries [2]."
    )


def test_mixed_note_line_both_results() -> None:
    """D20 (AC23, AC34): a mixed note's line is its topic line, then a sentence for
    its steering half; ✗ when either half is not found or not followed."""
    state = _state(MIXED, verdicts={"n6": "ignored_with_evidence"}, answered=("note-n6-target-01",))

    [line] = report_note_lines(state, state.composition)

    assert (line.outcome, line.steering_outcome, line.statement_id) == ("covered", "not_addressed", "S003")
    assert line.text == "The rest of your note was not followed in this report."
    reader, _, _, _ = _terminal_artifacts(state, "accepted")
    assert _bottom_line(reader).splitlines()[-1] == (
        "- **Your note · pastries:** ✗ Agency Three reports pastries [2]. "
        "The rest of your note was not followed in this report."
    )
    honoured = _state(MIXED, verdicts={"n6": "honoured"}, answered=("note-n6-target-01",))
    assert _bottom_line(_terminal_artifacts(honoured, "accepted")[0]).splitlines()[-1].startswith(
        "- **Your note · pastries:** ✓ Agency Three reports pastries [2]."
    )


def test_a_research_note_without_a_kept_line_names_its_outcome() -> None:
    not_found = _state(PASTRIES, answered=(), note_line=False, not_found=("note-n1-target-01",))
    [line] = report_note_lines(not_found, not_found.composition)
    assert (line.outcome, line.statement_id, line.text) == ("not_found", None, "No source we could check covers this.")
    assert _bottom_line(_terminal_artifacts(not_found, "partial")[0]).splitlines()[-1] == (
        "- **Your note · pastries:** ✗ No source we could check covers this."
    )
    unresearched = _state(PASTRIES, answered=(), note_line=False)
    unresearched = unresearched.model_copy(update={"sub_topics": unresearched.sub_topics[:1]})
    [line] = report_note_lines(unresearched, unresearched.composition)
    assert (line.outcome, line.text) == ("not_checked", "Not researched.")
    assert _bottom_line(_terminal_artifacts(unresearched, "partial")[0]).splitlines()[-1] == (
        "- **Your note · pastries:** Not researched."
    )


def test_steering_note_lines_follow_the_reviews_verdicts() -> None:
    texts = {}
    for verdict in ("honoured", "ignored_with_evidence", "no_evidence", None):
        state = _state(CLOSED, verdicts={"n2": verdict} if verdict else {})
        [line] = report_note_lines(state, state.composition)
        texts[verdict] = (line.outcome, line.text)
    assert texts == {
        "honoured": ("covered", "Followed: leave out cafés that might be closed"),
        "ignored_with_evidence": ("not_addressed", "Not followed in this report: leave out cafés that might be closed"),
        "no_evidence": ("not_found", "No source we could check covers this: leave out cafés that might be closed"),
        None: ("not_checked", "Not checked: leave out cafés that might be closed"),
    }


def test_a_composition_without_a_layout_still_prints_its_note_lines() -> None:
    state = _state(CLOSED, verdicts={"n2": "no_evidence"})
    legacy = state.composition.model_copy(update={"bottom_line": None})
    reader, _, _, _ = _terminal_artifacts(state.model_copy(update={"composition": legacy}), "partial")
    assert _bottom_line(reader) == (
        "Agency One reports the answer [1]. Agency One reports part one [1].\n\n"
        "- **Your note · open now:** ✗ No source we could check covers this: "
        "leave out cafés that might be closed"
    )


def test_fingerprint_ignores_note_lines() -> None:
    """Spec §7.2: the bottom line's layout, the note lines and the reader's answers
    never enter the review's fingerprint -- stamping them at publication cannot
    invalidate the judgement made of the same content."""
    composition = _state(PASTRIES).composition
    stamped = composition.model_copy(update={
        "reader_note_lines": [ReportNoteLine(note_id="n1", label="Your note · pastries", outcome="covered",
                                             statement_id="S003")],
        "bottom_line": None,
        "reader_answers": ["San Jose"],
    })
    assert composition_semantic_fingerprint(stamped) == composition_semantic_fingerprint(composition)
