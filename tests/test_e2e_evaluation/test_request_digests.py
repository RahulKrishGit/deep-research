"""Every provider request and every event of every replay row, pinned
(latency plan, Task 1).

The latency work moves when requests start, never what they say, and never
which events a run records or in what order. These pins are the offline proof
of that for the whole real-agent matrix. ``PINNED_REQUEST_DIGESTS`` holds each
row's (full, timing_free, outside_research, count) request digests and
``PINNED_EVENT_DIGESTS`` its (event types in order, count) digest
(``tests/replay_digests.py``), as they stood at the start of the latency plan.
A change that alters one byte of one request, or adds or drops one, moves
full; timing_free moves only when something other than a live
acquisition-state snapshot changed; outside_research moves only when a
request of the planner, source evaluator, verifier, writer or reviewer
changed. A task that moves a pin on purpose re-pins that row in its own commit
and says why in a comment above the entry or the dictionary.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from deep_research.e2e_evaluation.replay_matrix import REPLAY_CASE_MANIFEST
from tests.replay_digests import (
    event_digest,
    event_types,
    replay_run,
    request_digests,
    request_lines,
    timing_free_lines,
)

# Latency plan Task 12 (audit O4): research loops no longer wait for each
# other's page fetches, so a page extraction's request can catch its loop's
# acquisition state a step earlier or later. In these rows only ``full``
# moved; ``timing_free`` and ``outside_research`` did not:
# blocked-html-pdf-fallback
# broad-constraints
# comparative-conflict
# decision-context-late-candidate
# evidence-words-not-on-page-rejected
# extra-pass-finds-nothing
# extra-pass-recovers-missing-target
# extra-pass-redrafts-the-gaining-part
# figure-not-on-page-dropped
# forecast-versus-actual-kept-apart
# memory-is-not-read
# missing-target-triggers-one-extra-pass
# non-constraint-answer
# report-scope-corrected-to-all-segments
# review-unavailable
# revision-noted
# same-work-mirror
# scoped-redraft-after-a-named-defect
# scoped-review-invalid-reply-falls-back
# statement-check-failure-keeps-sentences
# unsupported-mechanism
# validated-cache-reuse
PINNED_REQUEST_DIGESTS: dict[str, tuple[str, str, str, int]] = {
    "blocked-html-pdf-fallback": ("f67809c5da955cb1", "db1f65725d21a6ff", "e6da9e506e3611cf", 36),
    "broad-constraints": ("da37e3288a6854dc", "e8b4c8b3530c2c76", "7284e3fb492f1474", 58),
    "comparative-conflict": ("53934ee9cd2b8cdd", "ee30a028a71b6872", "705ad82f8c2ca49b", 33),
    "comparison-target-names-both-products": ("495f338140060ea6", "7eb867a075de19a1", "dfcf5b3f4a03d8a9", 16),
    "count-unit-period": ("add7bdaa7da74844", "eea8daec2e8872a2", "318c0f2e714a81e2", 39),
    "decision-context-late-candidate": ("331426ac8912ed59", "97cdc51975b1f775", "e656d712a7307628", 37),
    "empty-but-clean": ("1c1c315f7aa46046", "698388ce5a7c44f7", "6b6d55286aa22e4b", 40),
    "evidence-words-not-on-page-rejected": ("1c696a07ce9f9fbc", "b6bd4390da7a6726", "cefccba119b6b89a", 33),
    "extra-pass-finds-nothing": ("87ef8cb4f6baefd7", "18ebb999bde7e55c", "f38d7b18ebc53dda", 37),
    # Latency plan Task 8 (audit O9): this row's script calls a tool on its
    # forced last turn, which a sub-topic loop no longer asks; the
    # obeying-model test below shows a model that answers as told loses
    # only that turn's own requests. Was ("cc4d4c93c0dcd75f", "f263e41636a8452e", "e3cd657e27a7db9a", 48).
    "extra-pass-recovers-missing-target": ("745c7250a6b64e0b", "55ef472efae92f1f", "3350579877a260f7", 48),
    # Latency plan Task 8 (audit O9): this row's script calls a tool on its
    # forced last turn, which a sub-topic loop no longer asks; the
    # obeying-model test below shows a model that answers as told loses
    # only that turn's own requests. Was ("472eb4eb9a52e922", "7cb5411d8b2d9a81", "83358bd57ccf2f08", 41).
    "extra-pass-redrafts-the-gaining-part": ("0ab8e3f254351883", "a4ae483223fdbe9a", "46412a78f67230c0", 41),
    "figure-not-on-page-dropped": ("41f42a244f5ead0d", "ce1b5fe9cb957870", "a47f0478ca0589f8", 33),
    "forecast-versus-actual-kept-apart": ("5d5fbec6536597fc", "c0652aaf18123aba", "e4f8dcaf15866edd", 41),
    "maker-notes-vs-relay": ("7c773b2b6a776b8c", "3e530c1246b93fbf", "e468184a2274cd6a", 20),
    "memory-is-not-read": ("26b89a7c329cc560", "3fd0f5bee828865f", "d8153bf008a4e5a9", 34),
    # Latency plan Task 8 (audit O9): this row's script calls a tool on its
    # forced last turn, which a sub-topic loop no longer asks; the
    # obeying-model test below shows a model that answers as told loses
    # only that turn's own requests. Was ("271c9e9baa621249", "1f24dc4136dc4d03", "622613a384191f12", 46).
    "missing-target-triggers-one-extra-pass": ("1dc87d451e2898d8", "fed5317482a6c3f7", "0c0026e85f61a62a", 46),
    "non-constraint-answer": ("53f5e44a2e96739c", "662309350f5085db", "180551d103074c1e", 33),
    "one-part-question": ("e4ef67c0f0d63d60", "fcb7ce1f08a13ba6", "74a3042d8bb99af5", 16),
    "prose-only-question": ("215be06f95f0b548", "5a84a902de80cd04", "d8c01755432a06a0", 25),
    "purchase-year-empty-period": ("66284e512d5fd16f", "d7ca14f09571703f", "6c567a743690ba09", 14),
    "relative-period-resolved": ("ebf6a018b3b304eb", "1fd4167209b021d1", "ab8fee3beda50e6f", 18),
    "report-relay-labelled-as-relay": ("8395dd5c4ef5bd72", "4434860a7419dc67", "f2670489eb870342", 32),
    "report-scope-corrected-to-all-segments": ("769a8fa789c361a8", "2d48770a346902a9", "5e2ad1b4ba74638d", 30),
    "review-unavailable": ("9e1b457c18cdabe2", "a17682fd1439a8e6", "d380ab2212280e0b", 33),
    "revision-noted": ("8d83d2109dc384a3", "4f33ac8e9a1c0090", "a6962fe58fdf8b41", 33),
    "same-work-mirror": ("24dace347c766918", "d42d4539e5aaf8da", "9f15e252e6d56266", 33),
    "scoped-redraft-after-a-named-defect": ("9b014525db79e956", "f8638fbcac6b57fc", "ca638df6049f7fee", 29),
    "scoped-review-invalid-reply-falls-back": ("851d7281ecb20832", "158a5ad023ee7078", "84dd2132c562fdcd", 30),
    "single-subject-spellings": ("1c10afdb9fe40305", "96477bef674181a1", "5380145e2ad7a771", 18),
    "statement-check-failure-keeps-sentences": ("846fc45df027eebb", "85e4f30db40c90c5", "2c6b8dcc792f4316", 31),
    "two-subjects-one-value": ("c5d33fb3ed1c96a0", "19fa8302aefe7794", "6894ad7112662bed", 16),
    "two-versions-one-target": ("2da292adfbf6f3a5", "99e09c68b45beb38", "276e8d5713259403", 16),
    "unattributed-relay-prose": ("1d61a04946b21368", "65734e85e6a8427a", "4c4cdb2f0d4eec05", 16),
    "unsupported-mechanism": ("69fb17fb2d8718c1", "984ef9c189dfa288", "01c45530b86c2b50", 33),
    "validated-cache-reuse": ("eb6bfcf937be5fb7", "b3454fd3a120c614", "0c47717eadc38841", 28),
}

PINNED_EVENT_DIGESTS: dict[str, tuple[str, int]] = {
    "blocked-html-pdf-fallback": ("1cd71cdaa7b0b75c", 45),
    "broad-constraints": ("13c97d5b450f42d9", 58),
    "comparative-conflict": ("4300aed5bf981011", 43),
    "comparison-target-names-both-products": ("3ec645826328d887", 33),
    "count-unit-period": ("98ee4d9face9dfed", 64),
    "decision-context-late-candidate": ("1cd71cdaa7b0b75c", 45),
    "empty-but-clean": ("4c04aa44cdae0546", 67),
    "evidence-words-not-on-page-rejected": ("4300aed5bf981011", 43),
    "extra-pass-finds-nothing": ("441cde5a6daccc68", 65),
    # Latency plan Task 8 (audit O9): the read this row's script made on
    # its forced last turn is made in the extra pass instead, so one
    # researcher.tool_call moves; the count and every other event keep
    # their place. Was ("9e9e0f6bedf8126d", 73).
    "extra-pass-recovers-missing-target": ("35b2c5d90b741aae", 73),
    # Latency plan Task 8 (audit O9): the read this row's script made on
    # its forced last turn is made in the extra pass instead, so one
    # researcher.tool_call moves; the count and every other event keep
    # their place. Was ("cc140d8a6608095f", 68).
    "extra-pass-redrafts-the-gaining-part": ("8dc9953c25609e20", 68),
    "figure-not-on-page-dropped": ("4300aed5bf981011", 43),
    "forecast-versus-actual-kept-apart": ("441cde5a6daccc68", 65),
    "maker-notes-vs-relay": ("6fb5d6e45c766113", 36),
    "memory-is-not-read": ("2f97159ff35b43a3", 64),
    # Latency plan Task 8 (audit O9): the read this row's script made on
    # its forced last turn is made in the extra pass instead, so one
    # researcher.tool_call moves; the count and every other event keep
    # their place. Was ("44a14f886a7379ff", 72).
    "missing-target-triggers-one-extra-pass": ("694cfaf0bf69d89e", 72),
    "non-constraint-answer": ("4300aed5bf981011", 43),
    "one-part-question": ("3ec645826328d887", 33),
    "prose-only-question": ("74b50a3718c617d5", 40),
    "purchase-year-empty-period": ("e64b199e8ba9f0b8", 32),
    "relative-period-resolved": ("6fb5d6e45c766113", 36),
    "report-relay-labelled-as-relay": ("4390264a9c4745ea", 44),
    "report-scope-corrected-to-all-segments": ("3fd0a0e4474ecc7a", 42),
    "review-unavailable": ("4300aed5bf981011", 43),
    "revision-noted": ("4300aed5bf981011", 43),
    "same-work-mirror": ("4300aed5bf981011", 43),
    "scoped-redraft-after-a-named-defect": ("32e7f824f1a0c23c", 49),
    "scoped-review-invalid-reply-falls-back": ("32e7f824f1a0c23c", 49),
    "single-subject-spellings": ("9ea917f9bf5ae908", 34),
    "statement-check-failure-keeps-sentences": ("4300aed5bf981011", 43),
    "two-subjects-one-value": ("3ec645826328d887", 33),
    "two-versions-one-target": ("3ec645826328d887", 33),
    "unattributed-relay-prose": ("3ec645826328d887", 33),
    "unsupported-mechanism": ("4300aed5bf981011", 43),
    "validated-cache-reuse": ("a0eccf4e4860d113", 42),
}


def test_the_pins_cover_every_row_of_the_manifest() -> None:
    rows = {entry.case_id for entry in REPLAY_CASE_MANIFEST}
    assert set(PINNED_REQUEST_DIGESTS) == rows
    assert set(PINNED_EVENT_DIGESTS) == rows


@pytest.mark.parametrize("case_id", sorted(PINNED_REQUEST_DIGESTS))
def test_every_request_and_event_of_every_replay_row_is_pinned(
    tmp_path: Path, case_id: str
) -> None:
    run = replay_run(case_id, tmp_path)

    assert (
        request_digests(list(run.replay.completer.packet_sequence))
        == PINNED_REQUEST_DIGESTS[case_id]
    )
    assert event_digest(run) == PINNED_EVENT_DIGESTS[case_id]


def test_skipping_the_forced_final_turn_drops_only_that_turns_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Latency audit O9, on every row. With a scripted model that obeys the
    last-turn instruction -- it answers without a tool, as the prompt tells
    it to -- a row that skips its forced turns asks everything it asked with
    them, less the forced turns' own requests: every request outside research
    byte for byte, every research request up to its live acquisition-state
    snapshot; and it records the same events in the same order. (The replay's
    own script ignores that instruction and reads on its forced turn in three
    extra-pass rows; their pins moved in Task 8.)"""
    import deep_research.agents.researcher as researcher_module
    from deep_research.e2e_evaluation.replay import ReplayCompleter

    scripted = ReplayCompleter._researcher_turn

    def obeying(self: ReplayCompleter, text: str):  # type: ignore[no-untyped-def]
        if "This is the last iteration" in text:
            return self._final("The reads for this topic are complete.")
        return scripted(self, text)

    monkeypatch.setattr(ReplayCompleter, "_researcher_turn", obeying)
    reached: list[str] = []
    for entry in REPLAY_CASE_MANIFEST:
        monkeypatch.setattr(researcher_module, "SKIP_FINAL_ANSWER_TURN", False)
        asked_run = replay_run(entry.case_id, tmp_path / entry.case_id / "asked")
        monkeypatch.setattr(researcher_module, "SKIP_FINAL_ANSWER_TURN", True)
        skipped_run = replay_run(entry.case_id, tmp_path / entry.case_id / "skipped")
        asked = list(asked_run.replay.completer.packet_sequence)
        skipped = list(skipped_run.replay.completer.packet_sequence)
        forced = [
            (key, text) for key, text in asked if "This is the last iteration" in text
        ]
        if forced:
            reached.append(entry.case_id)
        outside = [
            request_lines([(key, text) for key, text in run if not key.startswith("researcher:")])
            for run in (asked, skipped)
        ]
        assert outside[0] == outside[1], entry.case_id
        assert Counter(timing_free_lines(asked)) - Counter(timing_free_lines(skipped)) == Counter(
            timing_free_lines(forced)
        ), entry.case_id
        assert not Counter(timing_free_lines(skipped)) - Counter(timing_free_lines(asked)), entry.case_id
        assert event_types(asked_run) == event_types(skipped_run), entry.case_id
    # Not vacuous: these rows do reach their forced turn.
    assert sorted(reached) == [
        "extra-pass-recovers-missing-target",
        "extra-pass-redrafts-the-gaining-part",
        "missing-target-triggers-one-extra-pass",
    ]
