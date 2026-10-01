"""The reader notes' routes: one targeted pass and one redraft per note (live-briefs spec §4.6,
D11, D11a; AC17, AC18, AC20)."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Sequence

import pytest

from deep_research.agents.planner import ResearchPlan
from deep_research.agents.reader_notes import (
    NOTES_WAIT_S,
    note_sub_topic,
    research_notes_without_a_topic,
)
from deep_research.agents.report import render_finding_log, render_written_report
from deep_research.agents.researcher import ResearcherAgent, select_sub_topics
from deep_research.api.notes import note_outcome
from deep_research.graph.nodes import (
    _arrived_via_redraft_hop,
    note_pass_node,
    report_reviewer_node,
    writer_redraft_node,
)
from deep_research.graph.orchestrator import compile_research_graph, run_research_graph
from deep_research.graph.state import (
    MAX_WRITER_REDRAFTS,
    NOTE_PASS_NODE,
    ROUTE_END,
    ROUTE_EXTRA_PASS,
    ROUTE_FINALIZE,
    ROUTE_NOTE_PASS,
    ROUTE_REDRAFT,
    dump_state,
    extra_pass_target_ids,
    graph_recursion_limit,
    graph_route,
    graph_status,
    is_halted,
    load_state,
    notes_due_a_pass,
    notes_due_a_redraft,
    researched_note_topic_ids,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.runtime.notes import NoteBoard, bind_note_board
from deep_research.utils.config import AgentRuntimeConfig, HitlConfig
from deep_research.utils.types import (
    MAX_NOTES_PER_RUN,
    AcquisitionState,
    NoteDisposition,
    NotFoundTarget,
    ReaderNote,
    ReportComposition,
    ReportQualitySnapshot,
    ReportReview,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    ReviewDefect,
    merge_research_state,
)
from tests.agent_fakes import ScriptedCompleter, TargetKeyedCompleter
from tests.graph_fakes import (
    FakeAgent,
    FakeReviewer,
    fake_quality,
    fake_reader_note,
    fake_report_review,
    fake_research_agents,
    fake_research_state,
    fake_scored_source,
    fake_sub_topic,
    fake_target,
    fake_writer_composition,
    fake_writer_update,
    halting_error,
    verified_pass,
)
from tests.research_fakes import research_tools
from tests.test_agents.test_planner import _planner

QUESTION = "How mature is quantum error correction?"
AT = "2026-09-29T10:00:00.000+00:00"


def _judged(*notes: ReaderNote, verdicts: dict[str, str], **overrides: object) -> ResearchState:
    return fake_research_state(
        reader_notes=list(notes),
        report_review=fake_report_review(note_dispositions=verdicts),
        **overrides,
    )


def _material() -> ReviewDefect:
    return ReviewDefect(
        defect_id="review-01", kind="contradiction", severity="major",
        statement_ids=["S001"], problem="The summary contradicts the findings.",
    )


# --- graph_route: order and per-note guards ------------------------------------


def test_a_note_without_evidence_buys_its_pass_before_anything_but_a_halt() -> None:
    due = fake_research_state(
        reader_notes=[fake_reader_note(reviewed=True)],
        report_review=fake_report_review(
            note_dispositions={"n1": "no_evidence"},
            missing_required_target_ids=["topic-01-target-02"],
            defects=[_material()],
        ),
    )

    assert graph_route(due) == (ROUTE_NOTE_PASS, "note_pass_requested")
    assert graph_status(due) == "incomplete"
    halted = due.model_copy(update={"errors": [halting_error()]})
    assert graph_route(halted) == (ROUTE_END, "halted")


def test_each_note_buys_one_pass_and_one_redraft_and_the_pass_comes_first() -> None:
    fresh = fake_reader_note("n1", reviewed=True)
    late = fake_reader_note("n2")  # arrived after the review input was built

    both = _judged(fresh, late, verdicts={"n1": "no_evidence"})
    assert graph_route(both) == (ROUTE_NOTE_PASS, "note_pass_requested")
    assert [note.note_id for note in notes_due_a_pass(both)] == ["n1"]
    assert [note.note_id for note in notes_due_a_redraft(both)] == ["n2"]

    passed = _judged(fresh.model_copy(update={"passed": True}), late, verdicts={"n1": "no_evidence"})
    assert graph_route(passed) == (ROUTE_REDRAFT, "note_redraft_requested")
    assert graph_status(passed) == "incomplete"

    done = _judged(
        fresh.model_copy(update={"passed": True}), late.model_copy(update={"redrafted": True, "reviewed": True}),
        verdicts={"n1": "no_evidence", "n2": "honoured"}, quality=fake_quality(),
    )
    assert graph_route(done) == (ROUTE_FINALIZE, "report_accepted")


def test_a_report_that_ignores_a_note_buys_its_redraft_even_with_the_reviews_own_rerun_spent() -> None:
    ignored = fake_reader_note(reviewed=True)
    spent = _judged(ignored, verdicts={"n1": "ignored_with_evidence"}, writer_redrafts=MAX_WRITER_REDRAFTS)

    assert graph_route(spent) == (ROUTE_REDRAFT, "note_redraft_requested")
    redrafted = _judged(
        ignored.model_copy(update={"redrafted": True}), verdicts={"n1": "ignored_with_evidence"},
    ).model_copy(update={"report_review": fake_report_review(
        note_dispositions={"n1": "ignored_with_evidence"}, defects=[_material()],
    )})
    # The note's redraft spent nothing: the review's own re-run is still there.
    assert redrafted.writer_redrafts == 0
    assert graph_route(redrafted) == (ROUTE_REDRAFT, "redraft_requested")


def test_a_replaced_or_honoured_note_routes_nowhere() -> None:
    first = fake_reader_note("n1", reviewed=True)
    second = fake_reader_note("n2", reviewed=True, replaces="n1")
    state = _judged(first, second, verdicts={"n1": "no_evidence", "n2": "honoured"}, quality=fake_quality())

    assert notes_due_a_pass(state) == [] and notes_due_a_redraft(state) == []
    assert graph_route(state) == (ROUTE_FINALIZE, "report_accepted")


def test_a_notes_own_targets_never_buy_or_exhaust_an_extra_pass() -> None:
    topic = note_sub_topic(fake_reader_note(), priority=2, reason="no_evidence")
    state = fake_research_state(
        sub_topics=[fake_sub_topic(targets=[fake_target()]), topic],
        report_review=fake_report_review(missing_required_target_ids=["note-n1-target-01"]),
    )

    assert extra_pass_target_ids(state) == []
    assert graph_route(state)[0] != ROUTE_EXTRA_PASS


# --- the note_pass node ---------------------------------------------------------


def test_a_notes_sub_topic_carries_its_questions_scope_and_required_targets() -> None:
    angled = fake_reader_note(
        "n3", kinds=["new_angle", "scope"], restatement="recycling at end of life",
        new_questions=["How are battery cells recycled?", "What does recycling cost?"],
        scope={"geography": "European Union", "period": "since 2023"},
    )

    topic = note_sub_topic(angled, priority=4, reason="reader_note")

    assert (topic.coverage_id, topic.title, topic.priority) == ("note-n3", "Your note: recycling at end of life", 4)
    assert topic.search_queries == ["How are battery cells recycled?", "What does recycling cost?"]
    assert [
        (t.target_id, t.coverage_id, t.question, t.required, t.geography, t.period) for t in topic.evidence_targets
    ] == [
        ("note-n3-target-01", "note-n3", "How are battery cells recycled?", True, "European Union", "since 2023"),
        ("note-n3-target-02", "note-n3", "What does recycling cost?", True, "European Union", "since 2023"),
    ]
    plain = note_sub_topic(fake_reader_note("n1"), priority=2, reason="no_evidence")
    assert [t.question for t in plain.evidence_targets] == ["more weight on grid storage (n1)"]
    assert plain.evidence_targets[0].geography is None


@pytest.mark.asyncio
async def test_the_note_pass_opens_one_pass_for_every_note_that_owes_one() -> None:
    state = _judged(
        fake_reader_note("n1", reviewed=True), fake_reader_note("n2", reviewed=True),
        fake_reader_note("n3", reviewed=True, passed=True),
        verdicts={"n1": "no_evidence", "n2": "honoured", "n3": "no_evidence"},
        sub_topics=[fake_sub_topic(targets=[fake_target()])], iteration=1,
    )

    opened = load_state(await note_pass_node(dump_state(state)))

    assert [topic.coverage_id for topic in opened.sub_topics] == ["topic-01", "note-n1"]
    assert opened.sub_topics[-1].priority == state.sub_topics[0].priority + 1
    assert opened.extra_pass_target_ids == ["note-n1-target-01"]
    assert [topic.coverage_id for topic in select_sub_topics(opened)] == ["note-n1"]
    assert [(n.note_id, n.passed) for n in opened.reader_notes] == [("n1", True), ("n2", False), ("n3", True)]
    assert (opened.note_passes, opened.iteration) == (1, 1)
    started = [event for event in opened.events if event.event_type == "graph.note_pass.started"]
    assert [event.metadata for event in started] == [
        {"iteration": 1, "note_passes": 1, "note_ids": ["n1"], "targets": ["note-n1-target-01"]}
    ]
    assert [event.event_type for event in opened.events][-3:] == [
        "graph.node.started", "graph.note_pass.started", "graph.node.completed",
    ]


@pytest.mark.asyncio
async def test_the_note_pass_refuses_to_run_for_no_note_and_skips_a_halted_run() -> None:
    idle = load_state(await note_pass_node(dump_state(fake_research_state())))
    halted = load_state(await note_pass_node(dump_state(fake_research_state(errors=[halting_error()]))))

    assert [error.error_type for error in idle.errors] == ["graph_invalid_route"]
    assert is_halted(idle) and idle.note_passes == 0
    assert [event.event_type for event in halted.events][-1] == "graph.node.skipped"
    assert halted.note_passes == 0


# --- the redraft hop and the review node ------------------------------------------


@pytest.mark.asyncio
async def test_the_note_redraft_flags_its_notes_and_spends_no_rerun() -> None:
    state = _judged(
        fake_reader_note("n1", reviewed=True), fake_reader_note("n2"),
        verdicts={"n1": "ignored_with_evidence"}, writer_redrafts=MAX_WRITER_REDRAFTS,
    )

    hop = load_state(await writer_redraft_node(dump_state(state)))

    assert not is_halted(hop)
    assert hop.writer_redrafts == MAX_WRITER_REDRAFTS
    assert [(n.note_id, n.redrafted) for n in hop.reader_notes] == [("n1", True), ("n2", True)]
    assert [event.event_type for event in hop.events][-3:] == [
        "graph.node.started", "graph.note_redraft.requested", "graph.node.completed",
    ]
    assert hop.events[-2].metadata == {"iteration": 0, "note_ids": ["n1", "n2"]}
    assert _arrived_via_redraft_hop(hop.events) is False


def _drafted_state() -> ResearchState:
    """A run that has drafted its report and is ready for review."""
    one = verified_pass()
    state = fake_research_state(
        sub_topics=[fake_sub_topic(targets=[fake_target()])],
        raw_findings=[one.finding], verified_findings=[one.finding],
        read_records={one.read.read_id: one.read}, evaluated_sources=[fake_scored_source()],
    )
    composition = fake_writer_composition(state)
    return state.model_copy(update={
        "composition": composition,
        "report": render_written_report(composition),
        "report_evidence": render_finding_log(composition),
    })


@pytest.mark.asyncio
async def test_the_review_marks_what_it_read_and_waits_for_a_note_still_being_read() -> None:
    """spec §4.8 "A note arrives during Reviewing": the node waits for the note, then
    takes it in unreviewed, which buys it a redraft."""
    board = NoteBoard()
    board.receive("first", received_at=AT, received_during="report_writer")
    board.add(fake_reader_note("n1"))
    board.receive("second", received_at=AT, received_during="report_reviewer")
    reviewer = FakeReviewer()

    async def interpret_later() -> None:
        await asyncio.sleep(0.05)
        board.add(fake_reader_note("n2", received_during="report_reviewer"))

    with bind_note_board(board):
        reading = asyncio.create_task(interpret_later())
        result = load_state(await report_reviewer_node(reviewer)(dump_state(_drafted_state())))
        await reading

    assert [note.note_id for note in reviewer.packets[0].reader_notes] == ["n1"]
    assert [(n.note_id, n.reviewed) for n in result.reader_notes] == [("n1", True), ("n2", False)]
    decided = [event for event in result.events if event.event_type == "graph.route.decided"]
    assert decided[-1].metadata["reason"] == "note_redraft_requested"
    assert decided[-1].metadata["destination"] == ROUTE_REDRAFT


@pytest.mark.asyncio
async def test_the_review_waits_for_a_reading_no_longer_than_its_own_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reading that never ends holds the route for ``NOTES_WAIT_S`` at most, however
    long the interpreter's own timeout is: the note stays on the board, out of this
    decision, and the route is read without it."""
    assert NOTES_WAIT_S == 30.0
    assert HitlConfig().note_interpret_timeout_s < NOTES_WAIT_S
    monkeypatch.setattr("deep_research.graph.nodes.NOTES_WAIT_S", 0.05)
    board = NoteBoard()
    board.receive("first", received_at=AT, received_during="report_writer")
    board.add(fake_reader_note("n1"))
    board.receive("never read", received_at=AT, received_during="report_reviewer")

    with bind_note_board(board):
        result = load_state(
            await asyncio.wait_for(report_reviewer_node(FakeReviewer())(dump_state(_drafted_state())), timeout=5)
        )

    assert board.pending == ("n2",)
    assert [(n.note_id, n.reviewed) for n in result.reader_notes] == [("n1", True)]
    # Read without the note: none is due, so the review's own verdict decides.
    decided = [event for event in result.events if event.event_type == "graph.route.decided"]
    assert (decided[-1].metadata["destination"], decided[-1].metadata["reason"]) == (
        ROUTE_FINALIZE, "report_not_accepted",
    )


# --- the compiled graph: AC17, AC18, AC20 ------------------------------------------


class NoteJudge(FakeReviewer):
    """A reviewer double that judges every note its packet carries (spec §4.6).

    A note is ``first`` the first time a review reads it and ``later`` after that.
    ``arrivals`` land on the board one per review, while that review runs, and
    only once every note already read is honoured — so each arrives alone.
    """

    def __init__(
        self,
        *,
        first: str,
        later: str,
        board: NoteBoard | None = None,
        arrivals: Sequence[ReaderNote] = (),
    ) -> None:
        super().__init__()
        self.first, self.later = first, later
        self.board = board
        self.arrivals = list(arrivals)
        self.seen: Counter[str] = Counter()

    async def review(self, packet: object, *, previous: ReportReview | None = None) -> ReportReview:
        review = await super().review(packet, previous=previous)
        verdicts: dict[str, str] = {}
        for note in getattr(packet, "reader_notes", []):
            self.seen[note.note_id] += 1
            verdicts[note.note_id] = self.first if self.seen[note.note_id] == 1 else self.later
        if self.arrivals and self.board is not None and all(v == "honoured" for v in verdicts.values()):
            note = self.arrivals.pop(0)
            self.board.receive(note.text, received_at=AT, received_during="report_reviewer")
            self.board.add(note)
        return review.model_copy(update={"note_dispositions": [
            NoteDisposition(note_id=note_id, status=verdict) for note_id, verdict in verdicts.items()  # type: ignore[arg-type]
        ]})


def _board(*notes: ReaderNote) -> NoteBoard:
    board = NoteBoard()
    for note in notes:
        board.receive(note.text, received_at=AT, received_during="planner")
        board.add(note)
    return board


def _types(events: Sequence[ResearchEvent], prefix: str) -> list[str]:
    return [event.event_type for event in events if event.event_type.startswith(prefix)]


def _reasons(state: ResearchState) -> list[str]:
    return [e.metadata["reason"] for e in state.events if e.event_type == "graph.route.decided"]


@pytest.mark.asyncio
async def test_ac17_a_note_without_evidence_gets_exactly_one_targeted_pass(tracker: Tracker) -> None:
    board = _board(fake_reader_note("n1", received_during="planner"))
    agents = fake_research_agents(report_reviewer=NoteJudge(first="no_evidence", later="no_evidence"))

    with bind_note_board(board):
        run = await run_research_graph(
            graph=compile_research_graph(agents), tracker=tracker, session_id="session-1", question=QUESTION,
        )

    state = run.state
    assert _reasons(state) == ["note_pass_requested", "report_accepted"]
    assert _types(state.events, "graph.note_pass") == ["graph.note_pass.started"]
    assert (state.iteration, state.note_passes) == (0, 1)
    second = agents.researcher.calls[1]
    assert second.extra_pass_target_ids == ["note-n1-target-01"]
    assert [topic.coverage_id for topic in select_sub_topics(second)] == ["note-n1"]
    assert len(agents.researcher.calls) == 2
    assert [(n.note_id, n.reviewed, n.passed, n.redrafted) for n in state.reader_notes] == [("n1", True, True, False)]
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_ac18_a_note_the_report_ignores_gets_exactly_one_redraft(tracker: Tracker) -> None:
    board = _board(fake_reader_note("n1", received_during="planner"))
    agents = fake_research_agents(report_reviewer=NoteJudge(first="ignored_with_evidence", later="honoured"))

    with bind_note_board(board):
        run = await run_research_graph(
            graph=compile_research_graph(agents), tracker=tracker, session_id="session-1", question=QUESTION,
        )

    state = run.state
    assert _reasons(state) == ["note_redraft_requested", "report_accepted"]
    assert _types(state.events, "graph.note_redraft") == ["graph.note_redraft.requested"]
    assert "graph.report.redraft_requested" not in [event.event_type for event in state.events]
    assert state.writer_redrafts == 0
    assert len(agents.report_writer.calls) == 2 and len(agents.researcher.calls) == 1
    assert [(n.note_id, n.redrafted) for n in state.reader_notes] == [("n1", True)]
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_ac20_ten_notes_each_buy_one_pass_and_one_redraft_within_the_recursion_limit(
    tracker: Tracker,
) -> None:
    """D11a's worst case: every note arrives alone, during a review, and is judged
    without evidence the first time — so each buys its own redraft, then its own pass."""
    board = NoteBoard()
    arrivals = [fake_reader_note(f"n{number}") for number in range(1, MAX_NOTES_PER_RUN + 1)]
    judge = NoteJudge(first="no_evidence", later="honoured", board=board, arrivals=arrivals)
    agents = fake_research_agents(report_reviewer=judge)

    with bind_note_board(board):
        run = await run_research_graph(
            graph=compile_research_graph(agents), tracker=tracker, session_id="session-1", question=QUESTION,
        )

    state = run.state
    assert run.status == "completed"
    assert not is_halted(state)
    assert len(state.reader_notes) == MAX_NOTES_PER_RUN == 10
    assert all(n.reviewed and n.passed and n.redrafted for n in state.reader_notes)
    assert state.note_passes == 10 and state.iteration == 0 and state.writer_redrafts == 0
    assert _types(state.events, "graph.note_pass") == ["graph.note_pass.started"] * 10
    assert _types(state.events, "graph.note_redraft") == ["graph.note_redraft.requested"] * 10
    assert _reasons(state) == ["note_redraft_requested", "note_pass_requested"] * 10 + ["report_accepted"]
    supersteps = sum(1 for e in state.events if e.event_type == "graph.node.started")
    assert supersteps < graph_recursion_limit(state.max_extra_passes)
    assert NOTE_PASS_NODE in {e.metadata["node"] for e in state.events if e.event_type == "graph.node.started"}


# --- notes-progress-report spec §5.4: the pass and redraft rules (D4, D5, D20, D22) -------


def _completed(coverage_id: str, stop_reason: str) -> ResearchEvent:
    return ResearchEvent(
        event_type="researcher.sub_topic.completed", source="researcher", message="Sub-topic complete.",
        metadata={"coverage_id": coverage_id, "stop_reason": stop_reason},
    )


@pytest.mark.parametrize("review_status", [None, "scored", "incomplete", "provider_failed"])
def test_notes_due_a_pass_research_notes_any_review_status(review_status: str | None) -> None:
    """§5.4, AC5 (D5): a research note with no researched topic owes its one pass whatever the
    review's status — none at all, or a provider failure — and the route takes it before
    review_unavailable; once its topic has done its research it owes none."""
    angle = fake_reader_note("n1", kinds=["new_angle"], reviewed=True)
    review = None if review_status is None else fake_report_review(status=review_status)
    owed = fake_research_state(
        reader_notes=[angle], report_review=review, sub_topics=[fake_sub_topic(targets=[fake_target()])],
    )

    assert notes_due_a_pass(owed) == [angle]
    assert graph_route(owed) == (ROUTE_NOTE_PASS, "note_pass_requested")
    topic = note_sub_topic(angle, priority=2, reason="reader_note")
    researched = owed.model_copy(update={
        "sub_topics": [*owed.sub_topics, topic], "events": [_completed("note-n1", "finished")],
    })
    assert researched_note_topic_ids(researched) == {"note-n1"}
    assert notes_due_a_pass(researched) == []


@pytest.mark.asyncio
async def test_research_notes_never_redraft() -> None:
    """§5.4, AC5, AC7 (D20, D22): only a note with a steering kind buys a redraft. A note whose
    only kind is new_angle never does, whatever the review said of it or whether it read it; a
    mixed note's steering half does — on ignored_with_evidence, or when no review input carried
    it — after the note's pass, and that redraft spends none of the review's own re-run. A mixed
    note whose steering half was judged no_evidence buys no pass: its topic researched it."""
    angle_unread = fake_reader_note("n1", kinds=["new_angle"])
    angle_ignored = fake_reader_note("n2", kinds=["new_angle"], reviewed=True)
    mixed_ignored = fake_reader_note("n3", kinds=["new_angle", "exclude"], reviewed=True)
    mixed_unread = fake_reader_note("n4", kinds=["new_angle", "exclude"])
    mixed_honoured = fake_reader_note("n5", kinds=["new_angle", "scope"], reviewed=True)
    state = _judged(
        angle_unread, angle_ignored, mixed_ignored, mixed_unread, mixed_honoured,
        verdicts={"n2": "ignored_with_evidence", "n3": "ignored_with_evidence", "n5": "honoured"},
        writer_redrafts=MAX_WRITER_REDRAFTS,
    )

    assert [note.note_id for note in notes_due_a_redraft(state)] == ["n3", "n4"]
    assert [note.note_id for note in notes_due_a_pass(state)] == ["n1", "n2", "n3", "n4", "n5"]
    assert graph_route(state) == (ROUTE_NOTE_PASS, "note_pass_requested")
    passed = state.model_copy(update={
        "reader_notes": [note.model_copy(update={"passed": True}) for note in state.reader_notes],
    })
    assert graph_route(passed) == (ROUTE_REDRAFT, "note_redraft_requested")
    hop = load_state(await writer_redraft_node(dump_state(passed)))
    assert [(note.note_id, note.redrafted) for note in hop.reader_notes] == [
        ("n1", False), ("n2", False), ("n3", True), ("n4", True), ("n5", False),
    ]
    assert hop.writer_redrafts == MAX_WRITER_REDRAFTS
    researched = _judged(
        mixed_honoured, verdicts={"n5": "no_evidence"},
        sub_topics=[note_sub_topic(mixed_honoured, priority=2, reason="reader_note")],
        events=[_completed("note-n5", "finished")],
    )
    assert notes_due_a_pass(researched) == []


@pytest.mark.asyncio
async def test_failed_note_thread_owes_one_pass(tracker: Tracker) -> None:
    """AC35 (review I5; review 2, I-1 and M-2): a research note whose thread ended in a
    provider failure, or whose planning-time topic stop left unstarted, owes its one note pass
    unless a finding answered one of its targets; the pass reuses its topic and confines the
    researcher to it; and when that topic has spent its acquisition budget, the pass is refused
    as unfunded and opens no loop, and the note stays passed and reads not_found."""
    angle = fake_reader_note("n1", kinds=["new_angle"], reviewed=True)
    topic = note_sub_topic(angle, priority=2, reason="reader_note")
    failed = fake_research_state(
        reader_notes=[angle], sub_topics=[fake_sub_topic(targets=[fake_target()]), topic],
        report_review=fake_report_review(),
        events=[_completed("topic-01", "finished"), _completed("note-n1", "provider_error")],
    )
    unstarted = failed.model_copy(update={"events": [_completed("topic-01", "finished")]})
    answered = failed.model_copy(update={
        "quality": ReportQualitySnapshot(answered_target_ids=["note-n1-target-01"]),
    })

    for owed in (failed, unstarted):
        assert researched_note_topic_ids(owed) == set()
        assert notes_due_a_pass(owed) == [angle]
        opened = load_state(await note_pass_node(dump_state(owed)))
        assert [sub_topic.coverage_id for sub_topic in opened.sub_topics] == ["topic-01", "note-n1"]
        assert opened.extra_pass_target_ids == ["note-n1-target-01"]
        assert [(note.note_id, note.passed) for note in opened.reader_notes] == [("n1", True)]
        assert notes_due_a_pass(opened) == []
    assert researched_note_topic_ids(answered) == {"note-n1"}
    assert notes_due_a_pass(answered) == []
    assert note_outcome("n1", answered, terminal=True) == "covered"

    opened = load_state(await note_pass_node(dump_state(failed)))
    spent = opened.model_copy(update={
        "acquisition_state_by_target": {"note-n1": AcquisitionState(target_id="note-n1", remaining_calls=0)},
    })
    completer = TargetKeyedCompleter()
    agent = ResearcherAgent(
        provider=completer, tracker=tracker,
        scratchpad=ScratchpadMemory(session_id="session-1", agent_name="researcher", max_entries=20),
        tools=research_tools(tracker), config=AgentRuntimeConfig(max_iterations=4, tool_budget=4),
    )
    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(spent)

    assert [error.error_type for error in outcome.errors] == ["researcher_extra_pass_unfunded"]
    assert completer.react_calls == []
    after = merge_research_state(spent, outcome.state_update).model_copy(update={
        "composition": ReportComposition(question=QUESTION, session_id="session-1", not_found=[
            NotFoundTarget(target_id="note-n1-target-01", question=angle.restatement, searched=True),
        ]),
    })
    assert notes_due_a_pass(after) == []
    assert note_outcome("n1", after, terminal=True) == "not_found"


@pytest.mark.asyncio
async def test_replaced_note_before_topic_gets_none(tracker: Tracker) -> None:
    """§5.8: a research note a later note replaced before it had a topic never gets one — not
    from the planner, not from the researcher's dispatcher, not from a note pass — and the later
    note is judged on its own kinds."""
    angle = fake_reader_note("n1", kinds=["new_angle"])
    later = fake_reader_note("n2", kinds=["exclude"], replaces="n1")
    planner = _planner(tracker, ScriptedCompleter())
    planner._reader_notes = (angle, later)
    plan = ResearchPlan(sub_topics=[fake_sub_topic(targets=[fake_target()])])

    assert planner._note_topics_for(fake_research_state(), plan) == []
    assert research_notes_without_a_topic([angle, later], set()) == []
    state = _judged(
        angle.model_copy(update={"reviewed": True}), later.model_copy(update={"reviewed": True}),
        verdicts={"n2": "no_evidence"},
    )
    assert [note.note_id for note in notes_due_a_pass(state)] == ["n2"]
    opened = load_state(await note_pass_node(dump_state(state)))
    assert [topic.coverage_id for topic in opened.sub_topics] == ["note-n2"]
    assert opened.sub_topics[0].rationale.endswith("and the review found no evidence for it yet.")


# --- AC10: the two worst cases ten notes allow, on the compiled graph ---------------------


class StagedNoteJudge(FakeReviewer):
    """A reviewer double that judges every note its packet carries by how many reviews have
    read it: ``verdicts[k]`` the (k+1)-th time, ``final`` after that."""

    def __init__(self, *, verdicts: Sequence[str], final: str) -> None:
        super().__init__()
        self.verdicts, self.final = list(verdicts), final
        self.seen: Counter[str] = Counter()

    async def review(self, packet: object, *, previous: ReportReview | None = None) -> ReportReview:
        review = await super().review(packet, previous=previous)
        judged: dict[str, str] = {}
        for note in getattr(packet, "reader_notes", []):
            count = self.seen[note.note_id]
            self.seen[note.note_id] += 1
            judged[note.note_id] = self.verdicts[count] if count < len(self.verdicts) else self.final
        return review.model_copy(update={"note_dispositions": [
            NoteDisposition(note_id=note_id, status=verdict) for note_id, verdict in judged.items()  # type: ignore[arg-type]
        ]})


def _writer_with_arrivals(board: NoteBoard, arrivals: list[ReaderNote]) -> FakeAgent:
    """A writer double that, each time it runs with every note so far passed and redrafted,
    lets the next note arrive — received and read while this Writing step runs."""

    def write(state: ResearchState) -> ResearchStateUpdate:
        if arrivals and all(note.passed and note.redrafted for note in state.reader_notes):
            note = arrivals.pop(0)
            board.receive(note.text, received_at=AT, received_during="report_writer")
            board.add(note)
        return fake_writer_update(state)

    return FakeAgent("report_writer", [], update_factory=write)


async def _ten_notes_during_writing(
    tracker: Tracker, kinds: list[str], verdicts: Sequence[str],
) -> ResearchState:
    board = NoteBoard()
    arrivals = [
        fake_reader_note(f"n{number}", kinds=kinds, received_during="report_writer")
        for number in range(1, MAX_NOTES_PER_RUN + 1)
    ]
    agents = fake_research_agents(
        report_writer=_writer_with_arrivals(board, arrivals),
        report_reviewer=StagedNoteJudge(verdicts=verdicts, final="honoured"),
    )
    with bind_note_board(board):
        run = await run_research_graph(
            graph=compile_research_graph(agents), tracker=tracker, session_id="session-1", question=QUESTION,
        )
    assert run.status == "completed"
    return run.state


def _ten_passes_and_ten_redrafts(state: ResearchState) -> None:
    assert not is_halted(state)
    assert len(state.reader_notes) == MAX_NOTES_PER_RUN == 10
    assert all(note.passed and note.redrafted for note in state.reader_notes)
    assert (state.note_passes, state.iteration, state.writer_redrafts) == (10, 0, 0)
    assert _types(state.events, "graph.note_pass") == ["graph.note_pass.started"] * 10
    assert _types(state.events, "graph.note_redraft") == ["graph.note_redraft.requested"] * 10
    assert _reasons(state) == ["note_pass_requested", "note_redraft_requested"] * 10 + ["report_accepted"]
    assert graph_recursion_limit(state.max_extra_passes) == graph_recursion_limit(1) == 160
    supersteps = sum(1 for event in state.events if event.event_type == "graph.node.started")
    assert supersteps < graph_recursion_limit(state.max_extra_passes)


@pytest.mark.asyncio
async def test_recursion_limit_steering_notes_pass_then_redraft(tracker: Tracker) -> None:
    """AC10, case 1 (review I1): ten steering notes, each arriving during a different Writing
    step, each judged no_evidence (it buys its pass) and then ignored_with_evidence (it buys its
    redraft): ten passes and ten redrafts, under the unchanged recursion limit."""
    state = await _ten_notes_during_writing(tracker, ["emphasis"], ["no_evidence", "ignored_with_evidence"])

    _ten_passes_and_ten_redrafts(state)
    assert all(topic.rationale.endswith("no evidence for it yet.") for topic in state.sub_topics[1:])


@pytest.mark.asyncio
async def test_recursion_limit_mixed_notes_during_writing(tracker: Tracker) -> None:
    """AC10, case 2: ten mixed notes, each arriving during a different Writing step, each
    buying its pass (its new_angle half has no topic) and then its redraft (its steering half
    judged ignored_with_evidence): ten passes and ten redrafts, under the unchanged limit."""
    state = await _ten_notes_during_writing(
        tracker, ["new_angle", "exclude"], ["ignored_with_evidence", "ignored_with_evidence"],
    )

    _ten_passes_and_ten_redrafts(state)
    assert [topic.coverage_id for topic in state.sub_topics[1:]] == [f"note-n{number}" for number in range(1, 11)]
    assert all(topic.rationale == "The reader asked for this in a note." for topic in state.sub_topics[1:])
