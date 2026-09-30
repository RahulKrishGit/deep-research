"""The reader's notes in the writer's drafts and in the report (live-briefs spec §4.6)."""

from __future__ import annotations

from deep_research.agents.report import answered_not_stated_targets, render_written_report
from deep_research.agents.report_writer import _is_redraft_hop
from deep_research.graph.nodes import note_sub_topic
from deep_research.utils.types import ResearchEvent
from tests.graph_fakes import (
    fake_reader_note,
    fake_research_state,
    fake_sub_topic,
    fake_target,
    fake_writer_composition,
    verified_pass,
)

# --- writing and the report ----------------------------------------------------------


def _event(event_type: str) -> ResearchEvent:
    return ResearchEvent(event_type=event_type, source="graph", message="A loop marker.")


def test_a_note_loop_makes_the_next_draft_a_fresh_one() -> None:
    """spec §4.6: after a note pass or a note redraft the writer drafts afresh, while the
    review's own redraft still patches only the parts its defects name."""
    base = fake_research_state()
    drafted = base.model_copy(update={"composition": fake_writer_composition(base)})

    def hop(*markers: str) -> bool:
        return _is_redraft_hop(drafted.model_copy(update={"events": [_event(m) for m in markers]}))

    assert hop() is True
    assert hop("graph.report.redraft_requested") is True
    assert hop("graph.report.redraft_requested", "graph.note_pass.started") is False
    assert hop("graph.note_redraft.requested") is False
    assert hop("graph.note_redraft.requested", "graph.report.redraft_requested") is True
    assert _is_redraft_hop(base) is False


def test_the_report_names_a_note_it_found_no_evidence_for() -> None:
    """spec §4.6: "A note that is still uncovered after its pass ends in the report as
    'Couldn't find evidence for your note: …'" — listed by the note, never by its questions."""
    angled = fake_reader_note(
        "n2", kinds=["new_angle"], restatement="how battery cells are recycled",
        new_questions=["How are battery cells recycled at end of life?"],
    )
    state = fake_research_state(
        sub_topics=[fake_sub_topic(targets=[fake_target()]), note_sub_topic(angled, priority=2)],
    )

    report = render_written_report(fake_writer_composition(state))
    confirm = report[report.index("## What we couldn't confirm"):]

    assert confirm.startswith(
        "## What we couldn't confirm\n\n"
        "This run did not research:\n- What did battery storage additions reach?\n\n"
        "Couldn't find evidence for your note: how battery cells are recycled\n"
    )
    assert "How are battery cells recycled at end of life?" not in confirm


def test_a_note_answered_but_never_stated_is_named_by_the_note_too() -> None:
    """A note target a verified finding answers, but that no printed statement states, is
    disclosed by the note, never by the question the run derived from it."""
    angled = fake_reader_note(
        "n2", kinds=["new_angle"], restatement="how battery cells are recycled",
        new_questions=["How are battery cells recycled at end of life?"],
    )
    one = verified_pass(target_ids=["note-n2-target-01"])
    state = fake_research_state(
        sub_topics=[fake_sub_topic(targets=[fake_target()]), note_sub_topic(angled, priority=2)],
        verified_findings=[one.finding],
    )
    composition = fake_writer_composition(state).model_copy(update={"summary": []})

    report = render_written_report(composition)
    confirm = report[report.index("## What we couldn't confirm"):]

    assert answered_not_stated_targets(composition) == ["note-n2-target-01"]
    assert "We found sources on your note but could not state a checked answer: how battery cells are recycled\n" in confirm
    assert "We found sources on these but could not state a checked answer:" not in confirm
    assert "How are battery cells recycled at end of life?" not in confirm


def test_a_note_with_two_questions_is_named_once_by_the_first_group_that_holds_it() -> None:
    """A note with one unanswered target and one answered-but-unstated target is named once,
    as a note nothing was found for: the second group never names it again (P3-B)."""
    angled = fake_reader_note(
        "n2", kinds=["new_angle"], restatement="how battery cells are recycled",
        new_questions=[
            "How are battery cells recycled at end of life?",
            "Which recyclers operate at scale?",
        ],
    )
    one = verified_pass(target_ids=["note-n2-target-02"])
    state = fake_research_state(
        sub_topics=[fake_sub_topic(targets=[fake_target()]), note_sub_topic(angled, priority=2)],
        verified_findings=[one.finding],
    )
    composition = fake_writer_composition(state).model_copy(update={"summary": []})

    report = render_written_report(composition)
    confirm = report[report.index("## What we couldn't confirm"):]

    assert [row.target_id for row in composition.not_found] == ["topic-01-target-01", "note-n2-target-01"]
    assert answered_not_stated_targets(composition) == ["note-n2-target-02"]
    assert confirm.count("how battery cells are recycled") == 1
    assert "Couldn't find evidence for your note: how battery cells are recycled\n" in confirm
    assert "We found sources on your note" not in confirm
    assert "We found sources on these but could not state a checked answer:" not in confirm
    assert "recycled at end of life" not in confirm
    assert "Which recyclers operate at scale?" not in confirm
