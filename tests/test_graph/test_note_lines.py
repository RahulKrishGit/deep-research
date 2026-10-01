"""The reader's notes in the bottom line (notes-progress-report spec §7.2, §7.5; AC23, AC34).

At publication each active note gets one line, stamped from its terminal outcome:
a research note points at its topic's kept line or names its outcome; a steering
note says how the report treated it; a mixed note shows both halves. Marks are
✓ (covered), ✗ (not found or not followed) and none (not checked).
"""

from __future__ import annotations

import json

import pytest

from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report import render_written_report
from deep_research.agents.report_reviewer import composition_semantic_fingerprint
from deep_research.api.notes import note_records
from deep_research.graph import orchestrator
from deep_research.graph.nodes import _closed_notes_update, _terminal_artifacts
from deep_research.graph.note_outcomes import report_note_lines
from deep_research.graph.orchestrator import compile_research_graph, run_research_graph
from deep_research.graph.state import graph_route
from deep_research.observability import Tracker
from deep_research.runtime.notes import NoteBoard, bind_note_board
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
    merge_research_state,
)
from tests.evidence_fakes import make_finding, make_read
from tests.graph_fakes import (
    FakePublisher,
    FakeReviewer,
    fake_reader_note,
    fake_report_review,
    fake_research_agents,
)


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


# --- the finalizer reads the board: every note the run read gets its line (owner decision O1) ---

AT = "2026-10-01T10:00:00.000+00:00"
LATE_STEERING = fake_reader_note("n1", kinds=["emphasis"], restatement="more weight on fire-safety standards",
                                 short="fire safety")
LATE_RESEARCH = fake_reader_note("n1", kinds=["new_angle"], restatement="pastries at the cafés",
                                 short="pastries", new_questions=["Which cafés serve pastries?"])


async def _published_run(
    monkeypatch: pytest.MonkeyPatch, tracker: Tracker, note, *, read_before_publishing: bool, earlier=()
):
    """One real graph run whose reader sends ``note`` while the review runs.

    The review's wait for a note still being read runs out (``NOTES_WAIT_S`` is made
    negligible), so the reviewer's last board merge finds the note unread. Then the
    route out of the review is read, and with ``read_before_publishing`` the reading
    ends right there: after that merge, before ``finalize_report`` starts -- the one
    window in which a note the API accepted can be read and still miss the report.
    """
    board = NoteBoard()
    for first in earlier:  # read before the run starts, so every node holds it
        board.receive(first.text, received_at=AT, received_during="planner")
        board.add(first)
    publisher = FakePublisher()

    class SentDuringReview(FakeReviewer):
        async def review(self, packet, *, previous=None):
            if len(board.received()) == len(earlier):
                board.receive(note.text, received_at=AT, received_during="report_reviewer")
            return await super().review(packet, previous=previous)

    route_after_review = orchestrator.route_after_review

    def route_then_read(channel):
        destination = route_after_review(channel)
        if read_before_publishing and board.pending:
            board.add(note)
        return destination

    monkeypatch.setattr("deep_research.graph.nodes.NOTES_WAIT_S", 0.01)
    monkeypatch.setattr(orchestrator, "route_after_review", route_then_read)
    agents = fake_research_agents(publisher=publisher, report_reviewer=SentDuringReview())
    with bind_note_board(board):
        run = await run_research_graph(
            graph=compile_research_graph(agents), tracker=tracker, session_id="session-1",
            question="Where are the best lattes?",
        )
    return board, publisher, run


@pytest.mark.parametrize(("note", "line"), [
    (LATE_STEERING, "- **Your note \u00b7 fire safety:** Not checked: more weight on fire-safety standards"),
    (LATE_RESEARCH, "- **Your note \u00b7 pastries:** Not researched."),
], ids=["steering", "research"])
@pytest.mark.asyncio
async def test_a_note_read_after_the_reviews_last_merge_still_gets_its_line_in_the_report(
    monkeypatch: pytest.MonkeyPatch, tracker: Tracker, note, line
) -> None:
    """O1: the finalizer merges the board's notes before it stamps the note lines, so a note the
    run read after the review's last merge gets the line spec §7.2 words for a note no pass took
    up, in the report it published and in the state it kept -- and /status says the same."""
    board, publisher, run = await _published_run(monkeypatch, tracker, note, read_before_publishing=True)

    assert run.status == "completed"
    assert board.pending == ()
    state = run.state
    # Taken in as closed: the run had decided to publish, so the note is owed no pass and no
    # redraft, and it cannot turn the accepted report's status into a note route's.
    assert [(held.note_id, held.reviewed, held.passed, held.redrafted) for held in state.reader_notes] == [
        ("n1", True, True, True),
    ]
    assert (run.status, state.composition.quality_status, graph_route(state)) == (
        "completed", "accepted", ("finalize", "report_accepted"),
    )
    assert json.loads(next(text for name, text, _ in publisher.documents if name.endswith(".json")))[
        "quality_status"
    ] == "accepted"
    # An accepted report still saves its cited findings to memory: the late note did not demote it.
    [published] = [event for event in state.events if event.event_type == "graph.report.published"]
    assert published.metadata["quality_status"] == "accepted"
    assert published.metadata["memory_writes"] == publisher.memory_writes == len(publisher.saved_findings) == 1
    [stamped] = state.composition.reader_note_lines
    assert (stamped.note_id, stamped.outcome, stamped.statement_id) == ("n1", "not_checked", None)
    assert _bottom_line(state.report).splitlines()[-1] == line
    [report_text] = [text for name, text, ok in publisher.documents if ok and not name.endswith(("-evidence.md", ".json"))]
    assert _bottom_line(report_text).splitlines()[-1] == line
    # /status and the report agree: the run read the note, and it ended not_checked.
    [record] = note_records(board, state, terminal=True)
    assert (record.restatement, record.outcome, record.steering_outcome) == (note.restatement, "not_checked", None)
    assert record.outcome == stamped.outcome


@pytest.mark.parametrize("note", [LATE_STEERING, LATE_RESEARCH], ids=["steering", "research"])
@pytest.mark.asyncio
async def test_a_note_the_run_never_read_adds_no_line_to_the_report(
    monkeypatch: pytest.MonkeyPatch, tracker: Tracker, note
) -> None:
    """O1's counter-case: a note still being read when the run publishes has no restatement, so it
    has no line -- as today -- while /status still lists it, not checked, with no restatement."""
    board, publisher, run = await _published_run(monkeypatch, tracker, note, read_before_publishing=False)

    assert run.status == "completed"
    assert board.pending == ("n1",)
    state = run.state
    assert state.reader_notes == [] and state.composition.reader_note_lines == []
    [report_text] = [text for name, text, ok in publisher.documents if ok and not name.endswith(("-evidence.md", ".json"))]
    assert "Your note" not in state.report and "Your note" not in report_text
    [record] = note_records(board, state, terminal=True)
    assert (record.restatement, record.outcome, record.steering_outcome) == (None, "not_checked", None)


@pytest.mark.asyncio
async def test_a_late_note_that_names_an_earlier_one_replaces_nothing(
    monkeypatch: pytest.MonkeyPatch, tracker: Tracker
) -> None:
    """O1 fix round 2 (P3-1): a note read after the run decided to publish retires no note. The
    report was drafted and reviewed with the earlier note still active, so the earlier note keeps
    its line and its outcome, the late one reads "Not checked", and /status says the same."""
    first = fake_reader_note("n1", kinds=["emphasis"], restatement="only downtown", short="downtown")
    late = fake_reader_note("n2", kinds=["scope"], restatement="only San Jose", short="San Jose", replaces="n1")
    board, _, run = await _published_run(
        monkeypatch, tracker, late, read_before_publishing=True, earlier=(first,)
    )

    state = run.state
    assert [(note.note_id, note.reviewed, note.replaces) for note in state.reader_notes] == [
        ("n1", True, None), ("n2", True, None),
    ]
    assert [(line.note_id, line.outcome) for line in state.composition.reader_note_lines] == [
        ("n1", "not_checked"), ("n2", "not_checked"),
    ]
    assert _bottom_line(state.report).splitlines()[-2:] == [
        "- **Your note \u00b7 downtown:** Not checked: only downtown",
        "- **Your note \u00b7 San Jose:** Not checked: only San Jose",
    ]
    assert [(record.received.note_id, record.outcome) for record in note_records(board, state, terminal=True)] == [
        ("n1", "not_checked"), ("n2", "not_checked"),
    ]


@pytest.mark.asyncio
async def test_a_late_note_does_not_retire_a_note_that_owns_a_kept_topic_line() -> None:
    """O1 fix round 2 (P3-1): n1 owns ``note-n1`` and its kept topic line; a late n2 names it in
    ``replaces``. Taken in at finalize, n2 retires nothing: n1 keeps its one "✓" row (not an
    unmarked row for a replaced note), n2 reads "Not checked", and /status agrees."""
    late = fake_reader_note("n2", kinds=["scope"], restatement="only San Jose", short="San Jose", replaces="n1")
    state = _state(PASTRIES)
    board = NoteBoard()
    for note in (PASTRIES, late):
        board.receive(note.text, received_at=AT, received_during="planner")
        board.add(note)

    with bind_note_board(board):
        merged = merge_research_state(state, _closed_notes_update(state))

    assert [(note.note_id, note.replaces) for note in merged.reader_notes] == [("n1", None), ("n2", None)]
    reader, _, _, finalized = _terminal_artifacts(merged, "accepted")
    assert [(line.note_id, line.outcome, line.statement_id) for line in finalized.reader_note_lines] == [
        ("n1", "covered", "S003"), ("n2", "not_checked", None),
    ]
    assert _bottom_line(reader) == (
        "Agency One reports the answer [1].\n\n"
        "- **Part one:** Agency One reports part one [1].\n"
        "- **Your note \u00b7 pastries:** \u2713 Agency Three reports pastries [2].\n"
        "- **Your note \u00b7 San Jose:** Not checked: only San Jose"
    )
    assert [(record.received.note_id, record.outcome) for record in note_records(board, merged, terminal=True)] == [
        ("n1", "covered"), ("n2", "not_checked"),
    ]
