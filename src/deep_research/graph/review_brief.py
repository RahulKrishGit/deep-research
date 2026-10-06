"""What Reviewing's brief reads from one review.

``graph.report.reviewed`` carries the five criteria a review can mark not met,
and what became of each of the reader's notes. Both are counts, ids and
enumerated values only: never the review's prose, a defect's ``problem`` text
or a score.

The notes' results are not ``graph/note_outcomes.py``'s outcomes, on
purpose. Those are a note's terminal outcome for the published report
(``covered`` / ``not_found`` / ``not_addressed`` / ``not_checked``). These are
Reviewing's view in the middle of a run: a research note with no
researched topic reads ``researched next``, because the route still owes it
its pass, and one whose pass is already spent (``passed``, the unfunded
refusal) reads ``not found``.
"""

from __future__ import annotations

from pydantic import JsonValue

from deep_research.agents.reader_notes import has_steering_kind, is_research_note
from deep_research.agents.report_reviewer import DIMENSION_GUIDANCE
from deep_research.graph.state import researched_note_topic_ids
from deep_research.utils.types import (
    NOTE_COVERAGE_PREFIX,
    GapKind,
    ReaderNote,
    ReportReview,
    ResearchState,
    active_reader_notes,
)

#: Prioritization and actionability have no defect kind, so no review can
#: mark them not met; Reviewing shows the other five, in ``DIMENSION_GUIDANCE`` order.
CRITERIA: tuple[str, ...] = tuple(
    name
    for name, _ in DIMENSION_GUIDANCE
    if name not in {"prioritization", "actionability"}
)

#: The criterion each defect kind counts against.
CRITERION_FOR_KIND: dict[GapKind, str] = {
    "coverage": "completeness",
    "mechanism": "completeness",
    "missing_support": "evidence_quality",
    "acquisition": "evidence_quality",
    "source_quality": "evidence_quality",
    "freshness": "evidence_quality",
    "identity": "attribution",
    "contradiction": "uncertainty",
    "semantic_duplicate": "readability",
    "presentation": "readability",
}


def review_criteria(review: ReportReview) -> list[dict[str, JsonValue]]:
    """The five criteria: ``met`` is ``None`` without a scored review,
    else ``False`` when a material defect maps to it; ``kinds`` lists the mapped
    kinds, one per material defect, in defect order."""
    scored = review.status == "scored"
    kinds: dict[str, list[JsonValue]] = {criterion: [] for criterion in CRITERIA}
    if scored:
        for defect in review.material_defects:
            kinds[CRITERION_FOR_KIND[defect.kind]].append(defect.kind)
    return [
        {
            "dimension": criterion,
            "met": (not kinds[criterion]) if scored else None,
            "kinds": kinds[criterion],
        }
        for criterion in CRITERIA
    ]


def _research_half(
    note: ReaderNote, state: ResearchState, researched: set[str], answered: set[str]
) -> dict[str, JsonValue]:
    """A research note's result from its own topic's targets.

    Covered when a verified finding answers one of its targets; not found
    when its topic was researched, or when its one note pass is already spent
    (``passed``: the unfunded-refusal corner); otherwise it is owed its
    pass and is researched next.
    """
    coverage_id = f"{NOTE_COVERAGE_PREFIX}{note.note_id}"
    targets = {
        target.target_id
        for topic in state.sub_topics
        if topic.coverage_id == coverage_id
        for target in topic.evidence_targets
    }
    if targets & answered:
        return {"result": "met", "reason": "covered"}
    if coverage_id in researched or note.passed:
        return {"result": "not_met", "reason": "not_found"}
    return {"result": "pending", "reason": "to_research"}


_STEERING_RESULT: dict[str, dict[str, JsonValue]] = {
    "honoured": {"result": "met", "reason": "honoured"},
    "ignored_with_evidence": {"result": "not_met", "reason": "ignored_with_evidence"},
    "no_evidence": {"result": "not_met", "reason": "no_evidence"},
}


def review_note_results(state: ResearchState, review: ReportReview) -> list[dict[str, JsonValue]]:
    """Each active note's result, in receipt order.

    A research note's result comes from its topic's targets, a steering note's
    from this review's disposition; a mixed note carries its research half in
    ``result``/``reason`` and its steering half in ``steering``.
    """
    answered = set(state.quality.answered_target_ids) if state.quality else set()
    researched = researched_note_topic_ids(state)
    verdicts = {entry.note_id: entry.status for entry in review.note_dispositions}
    results: list[dict[str, JsonValue]] = []
    for note in active_reader_notes(state.reader_notes):
        steering = _STEERING_RESULT.get(
            verdicts.get(note.note_id, ""), {"result": "not_checked", "reason": "not_judged"}
        )
        if not is_research_note(note):
            results.append({"note_id": note.note_id, **steering})
            continue
        entry: dict[str, JsonValue] = {
            "note_id": note.note_id,
            **_research_half(note, state, researched, answered),
        }
        if has_steering_kind(note):
            entry["steering"] = dict(steering)
        results.append(entry)
    return results
