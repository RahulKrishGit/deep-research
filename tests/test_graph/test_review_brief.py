"""What Reviewing's brief reads from one review."""

from __future__ import annotations

from deep_research.agents.events import agent_event
from deep_research.agents.reader_notes import note_sub_topic
from deep_research.graph.review_brief import (
    CRITERIA,
    CRITERION_FOR_KIND,
    review_criteria,
    review_note_results,
)
from deep_research.utils.types import (
    GAP_KINDS,
    ReportQualitySnapshot,
    ReviewDefect,
)
from tests.graph_fakes import fake_reader_note, fake_report_review, fake_research_state


def _defect(n: int, kind: str, *, severity: str = "major") -> ReviewDefect:
    return ReviewDefect(defect_id=f"review-{n:02d}", kind=kind, severity=severity, problem=f"SECRET problem {n}")


def test_reviewed_event_five_criteria_mapping() -> None:
    """Five criteria in DIMENSION_GUIDANCE order; every defect kind maps to
    one; a material defect marks its criterion not met with its kind, a minor
    one does not; an unscored review marks none."""
    assert CRITERIA == ("completeness", "evidence_quality", "attribution", "uncertainty", "readability")
    assert set(CRITERION_FOR_KIND) == set(GAP_KINDS)
    assert set(CRITERION_FOR_KIND.values()) == set(CRITERIA)
    review = fake_report_review(defects=[
        _defect(1, "coverage"), _defect(2, "missing_support"), _defect(3, "freshness"),
        _defect(4, "identity"), _defect(5, "presentation", severity="minor"),
    ])

    criteria = review_criteria(review)

    assert criteria == [
        {"dimension": "completeness", "met": False, "kinds": ["coverage"]},
        {"dimension": "evidence_quality", "met": False, "kinds": ["missing_support", "freshness"]},
        {"dimension": "attribution", "met": False, "kinds": ["identity"]},
        {"dimension": "uncertainty", "met": True, "kinds": []},
        {"dimension": "readability", "met": True, "kinds": []},
    ]
    assert "SECRET" not in str(criteria)
    assert [c["met"] for c in review_criteria(fake_report_review(status="provider_failed"))] == [None] * 5


def _completed(coverage_id: str, stop_reason: str):
    return agent_event(
        agent_name="researcher", event_type="researcher.sub_topic.completed", message="m",
        metadata={"coverage_id": coverage_id, "stop_reason": stop_reason},
    )


def test_reviewed_event_mixed_note_steering() -> None:
    """A research note's result comes from its own targets, a steering note's
    from the review, a mixed note both; a research note with no researched
    topic is owed its pass, unless that pass is spent (``passed``, the
    unfunded refusal); a replaced note is not listed."""
    covered = fake_reader_note("n1", kinds=["new_angle"], restatement="pastries at the cafés")
    mixed = fake_reader_note("n2", kinds=["new_angle", "exclude"], restatement="pastries, not closed cafés")
    honoured = fake_reader_note("n3", kinds=["emphasis"])
    unjudged = fake_reader_note("n4", kinds=["scope"])
    spent = fake_reader_note("n5", kinds=["new_angle"], restatement="opening hours", passed=True)
    searched = fake_reader_note("n6", kinds=["new_angle"], restatement="seating")
    replaced = fake_reader_note("n7", kinds=["emphasis"])
    replacing = fake_reader_note("n8", kinds=["emphasis"], replaces="n7")
    notes = [covered, mixed, honoured, unjudged, spent, searched, replaced, replacing]
    topics = [note_sub_topic(note, priority=2, reason="reader_note") for note in (covered, mixed, spent, searched)]
    state = fake_research_state(
        reader_notes=notes,
        sub_topics=topics,
        quality=ReportQualitySnapshot(answered_target_ids=["note-n1-target-01"]),
        events=[_completed("note-n1", "finished"), _completed("note-n5", "provider_error"),
                _completed("note-n6", "finished")],
    )
    review = fake_report_review(note_dispositions={
        "n2": "ignored_with_evidence", "n3": "honoured", "n8": "no_evidence",
    })

    assert review_note_results(state, review) == [
        {"note_id": "n1", "result": "met", "reason": "covered"},
        {"note_id": "n2", "result": "pending", "reason": "to_research",
         "steering": {"result": "not_met", "reason": "ignored_with_evidence"}},
        {"note_id": "n3", "result": "met", "reason": "honoured"},
        {"note_id": "n4", "result": "not_checked", "reason": "not_judged"},
        {"note_id": "n5", "result": "not_met", "reason": "not_found"},
        {"note_id": "n6", "result": "not_met", "reason": "not_found"},
        {"note_id": "n8", "result": "not_met", "reason": "no_evidence"},
    ]
