"""The review's verdict on each reader note (live-briefs spec §4.6 "Reviewing")."""

from __future__ import annotations

import json

import pytest

from deep_research.agents.prompts import STRUCTURED_REQUEST_END
from deep_research.agents.reader_notes import REVIEW_NOTES, render_reader_notes
from deep_research.agents.report_reviewer import (
    REVIEW_DIMENSIONS,
    NoteDispositionDraft,
    PreviousDefectResolutionDraft,
    ReportReviewer,
    ReportReviewNotesDraft,
    ReviewDimensionScores,
    ScopedReportReviewNotesDraft,
    StatementDispositionDraft,
    build_report_review_input,
    build_scoped_report_review_input,
    remap_review_for_redraft,
    review_messages,
    scoped_review_messages,
)
from deep_research.utils.types import NoteDisposition
from tests.agent_fakes import ScriptedCompleter
from tests.graph_fakes import fake_reader_note
from tests.test_agents.test_report_reviewer import _draft as review_draft
from tests.test_agents.test_report_reviewer import _redraft_fixture as redraft_fixture
from tests.test_agents.test_report_reviewer import _redraft_state as redraft_state
from tests.test_agents.test_report_reviewer import state_with_written_report
from tests.test_agents.test_tool_free_prompts import (
    _labelled_examples as labelled_examples,
)
from tests.test_agents.test_tool_free_prompts import (
    _request_envelope as request_envelope,
)

NOTES = [
    fake_reader_note("n1", restatement="more weight on fire-safety standards"),
    fake_reader_note("n2", kinds=["scope"], restatement="only the United States", scope={"geography": "United States"}),
]


# --- reviewing ------------------------------------------------------------------------


def _notes_draft(*verdicts: tuple[str, str]) -> ReportReviewNotesDraft:
    plain = review_draft()
    return ReportReviewNotesDraft(
        **plain.model_dump(),
        note_dispositions=[NoteDispositionDraft(note_id=n, status=s) for n, s in verdicts],
    )


def test_the_review_lists_each_note_with_its_id_and_asks_for_one_verdict_each() -> None:
    plain = build_report_review_input(state_with_written_report())
    noted = build_report_review_input(state_with_written_report(reader_notes=NOTES))
    body = review_messages(noted)[1].content
    block = render_reader_notes(noted.reader_notes, instruction=REVIEW_NOTES, with_ids=True)

    assert [(view.note_id, view.restatement, view.kinds) for view in noted.reader_notes] == [
        ("n1", "more weight on fire-safety standards", ["emphasis"]),
        ("n2", "only the United States", ["scope"]),
    ]
    assert body.index("# Answer contract\n") < body.index("# Reader notes\n") < body.index("# Reader content")
    assert f"# Reader notes\n{block}\n" in body
    assert "- n1: more weight on fire-safety standards (emphasis)" in block
    reply_format = body[body.index("# Reply format"):body.index("\n# ", body.index("# Reply format"))]
    assert '"note_dispositions"' in reply_format
    assert "Reader notes" not in review_messages(plain)[1].content
    assert '"note_dispositions"' not in review_messages(plain)[1].content
    assert plain.reader_notes == [] and plain.fingerprint != noted.fingerprint


def test_a_replaced_note_is_not_put_to_the_review() -> None:
    replacing = fake_reader_note("n3", restatement="only the European Union", replaces="n2", kinds=["scope"])
    noted = build_report_review_input(state_with_written_report(reader_notes=[*NOTES, replacing]))

    assert [view.note_id for view in noted.reader_notes] == ["n1", "n3"]


@pytest.mark.asyncio
async def test_each_notes_verdict_is_kept_once_and_an_unknown_one_is_dropped() -> None:
    completer = ScriptedCompleter(outputs=[_notes_draft(
        ("n1", "honoured"), ("n1", "no_evidence"), ("n2", "maybe"),
        ("n2", "ignored_with_evidence"), ("n9", "honoured"),
    )])
    noted = build_report_review_input(state_with_written_report(reader_notes=NOTES))

    review = await ReportReviewer(provider=completer).review(noted, previous=None)

    assert completer.calls[0][0] == "ReportReviewNotesDraft"
    assert review.status == "scored"
    assert [(d.note_id, d.status) for d in review.note_dispositions] == [
        ("n1", "honoured"), ("n2", "ignored_with_evidence"),
    ]


@pytest.mark.asyncio
async def test_a_review_without_notes_asks_the_schema_it_always_asked() -> None:
    completer = ScriptedCompleter(outputs=[review_draft()])

    review = await ReportReviewer(provider=completer).review(
        build_report_review_input(state_with_written_report()), previous=None
    )

    assert completer.calls[0][0] == "ReportReviewDraft"
    assert review.note_dispositions == []


@pytest.mark.asyncio
async def test_a_scoped_rereview_keeps_the_note_verdicts_it_did_not_judge_again() -> None:
    old, new, previous_review = redraft_fixture()
    judged = previous_review.model_copy(update={"note_dispositions": [
        NoteDisposition(note_id="n1", status="honoured"),
        NoteDisposition(note_id="n2", status="ignored_with_evidence"),
    ]})
    remapped = remap_review_for_redraft(judged, previous_composition=old, composition=new)
    assert remapped is not None
    scoped = build_scoped_report_review_input(
        redraft_state(new).model_copy(update={"reader_notes": NOTES}), previous_review=remapped
    )
    assert scoped is not None
    assert [d.note_id for d in scoped.carried_note_dispositions] == ["n1", "n2"]
    assert "# Reader notes\n" in scoped_review_messages(scoped)[1].content
    reply = ScopedReportReviewNotesDraft(
        dimensions=ReviewDimensionScores(**{name: 1.0 for name in REVIEW_DIMENSIONS}),
        statement_dispositions=[
            StatementDispositionDraft(statement_id="S010", disposition="supported"),
            StatementDispositionDraft(statement_id="S012", disposition="supported"),
        ],
        previous_defect_resolutions=[PreviousDefectResolutionDraft(defect_id="review-01", resolved=True, note="Fixed.")],
        new_defects=[],
        rationale="Re-checked the report as it now stands.",
        note_dispositions=[NoteDispositionDraft(note_id="n2", status="honoured")],
    )
    completer = ScriptedCompleter(outputs=[reply])

    review = await ReportReviewer(provider=completer).review_scoped(scoped)

    assert completer.calls[0][0] == "ScopedReportReviewNotesDraft"
    assert [(d.note_id, d.status) for d in review.note_dispositions] == [("n1", "honoured"), ("n2", "honoured")]


def test_the_notes_review_requests_keep_the_shared_reply_conventions() -> None:
    """The matrix in ``test_tool_free_prompts`` holds for the notes schemas too: one
    reply format, static sections first, every field in it, every example valid."""
    old, new, previous_review = redraft_fixture()
    remapped = remap_review_for_redraft(previous_review, previous_composition=old, composition=new)
    assert remapped is not None
    state = redraft_state(new)
    requests = []
    for notes in ([], NOTES):
        full = build_report_review_input(state.model_copy(update={"reader_notes": notes}))
        scoped = build_scoped_report_review_input(
            state.model_copy(update={"reader_notes": notes}), previous_review=remapped
        )
        assert scoped is not None
        requests.append((review_messages(full)[1].content, scoped_review_messages(scoped)[1].content))
    (plain_full, plain_scoped), (noted_full, noted_scoped) = requests

    for plain, noted, schema in (
        (plain_full, noted_full, ReportReviewNotesDraft),
        (plain_scoped, noted_scoped, ScopedReportReviewNotesDraft),
    ):
        envelope = request_envelope(noted)
        assert envelope.count("# Reply format") == 1 and envelope.count("JSON object") == 1
        assert envelope.rstrip().splitlines()[-1] == STRUCTURED_REQUEST_END

        def static(body: str) -> list[str]:
            sections = [line for line in request_envelope(body).splitlines() if line.startswith("# ")]
            return sections[: sections.index("# Reply format")]

        assert static(noted) == static(plain)
        start = envelope.index("# Reply format")
        reply_format = envelope[start : envelope.index("\n# ", start)]
        for field in schema.model_fields:
            assert f'"{field}"' in reply_format, field
        examples = labelled_examples(noted)
        assert len(examples) == 1
        schema.model_validate(json.loads(examples[0][1]))
