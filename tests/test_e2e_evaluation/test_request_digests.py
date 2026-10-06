"""Every provider request and every event of every replay row, pinned.

Changing when requests start must never change what they say, which events a
run records, or in what order. These pins are the offline proof of that for
the whole real-agent matrix. ``PINNED_REQUEST_DIGESTS`` holds each row's
(full, timing_free, outside_research, count) request digests and
``PINNED_EVENT_DIGESTS`` its (event types in order, count) digest
(``tests/replay_digests.py``).
A change that alters one byte of one request, or adds or drops one, moves
full; timing_free moves only when something other than a live
acquisition-state snapshot changed; outside_research moves only when a
request of the planner, source evaluator, verifier, writer or reviewer
changed. Re-pin a moved row deliberately with
``python -m tests.replay_digests --pin``.
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

PINNED_REQUEST_DIGESTS: dict[str, tuple[str, str, str, int]] = {
    "blocked-html-pdf-fallback": ("007666cb31f5212e", "899aacdb97e7a617", "250262dd58e797c6", 36),
    "broad-constraints": ("a111a2648e94cca4", "33624bd297ab2166", "83527eb9236455ae", 59),
    "comparative-conflict": ("7555bdbc114239a7", "ee04298f16d6b28b", "7dccd31e5b040067", 33),
    "comparison-target-names-both-products": ("8cbb60c01660add5", "d7ed8bcb74b01e48", "9fa96fa5dd76de44", 16),
    "count-unit-period": ("8dac99777a633d9a", "15197a4b59f8f19a", "ed1efe647b4a8f92", 39),
    "decision-context-late-candidate": ("230314c195310eb5", "802e7eb80ba650c8", "ee83579ed3b734e1", 37),
    "empty-but-clean": ("d02228cc8855fe88", "aef6caa41fa79668", "29fd24cea08b7d2f", 40),
    "evidence-words-not-on-page-rejected": ("a3ba22adc19a91a7", "8512bffb0b583ab8", "ce02b1541e0533e5", 33),
    "extra-pass-finds-nothing": ("e30dc98c33aff5e3", "05722cd095cfc654", "07765d9b14cebcd3", 37),
    # This row's script calls a tool on its forced last turn, which a
    # sub-topic loop never asks; a model that answers as told loses only that
    # turn's own requests (see the obeying-model test below).
    "extra-pass-recovers-missing-target": ("f52091c37449783b", "0326f95dbec579bf", "3d329de1d1d19c72", 48),
    # This row's script calls a tool on its forced last turn, which a
    # sub-topic loop never asks; a model that answers as told loses only that
    # turn's own requests (see the obeying-model test below).
    "extra-pass-redrafts-the-gaining-part": ("405bc78441fbdcac", "bc0a68ee8bf6a8b1", "38b615bc05fd169a", 41),
    "figure-not-on-page-dropped": ("3c7dc8ce949021fc", "3bfac55074629d62", "e04709adc1e21392", 33),
    "forecast-versus-actual-kept-apart": ("f632bdf6f4ab7125", "4ad3ee88d833f1fa", "8e1cb9a396e4dca9", 41),
    "maker-notes-vs-relay": ("e8ac145c1910a2d2", "454e28d55e0eb077", "8c9479eef7a31dd6", 20),
    "memory-is-not-read": ("ff79f833c218bedc", "a2bc3bdebb97bf24", "b0ee9daa3f3c06bb", 34),
    # This row's script calls a tool on its forced last turn, which a
    # sub-topic loop never asks; a model that answers as told loses only that
    # turn's own requests (see the obeying-model test below).
    "missing-target-triggers-one-extra-pass": ("d75c5a90c152a830", "b80c35c7c2af39a4", "388fdb5b29b26938", 46),
    "non-constraint-answer": ("10c6a71ce1bc6fa0", "07d53e7979077394", "8b3e1975ab5195c4", 33),
    "one-part-question": ("907557702b4faf46", "b942154e38eb83ef", "1ed4931a212c59db", 16),
    "prose-only-question": ("946def9068481038", "fc5944591ff1764a", "feceb235e3dd446a", 25),
    "purchase-year-empty-period": ("5d95223ba4d9ad7b", "b391464597bc859c", "9efa4eccbb549709", 14),
    "relative-period-resolved": ("7ade928fea14cbb6", "42cc83ecb85fc585", "3d2c2f7b78253ff8", 18),
    "report-relay-labelled-as-relay": ("4dacac70bcd3e69b", "81a85835bbcc9b1f", "06ab2a93beb4ee80", 32),
    "report-scope-corrected-to-all-segments": ("4a3fa338683482c2", "247a6cf829421cec", "19e8645552f0ad74", 30),
    "review-unavailable": ("a2f8d153230e4d74", "b5da0ffee0dddda0", "2afc4e75220f4b29", 33),
    "revision-noted": ("8a7f338be36e9a8a", "f9c42425ec2f5932", "421a7c6d7a99f78e", 33),
    "same-work-mirror": ("57e4d294f39d8002", "9bd74bf24f924710", "489706afd35bb388", 33),
    "scoped-redraft-after-a-named-defect": ("e24fb268a6f246db", "bd2f3a1a936bac7f", "126aa2d7cbbef83e", 29),
    "scoped-review-invalid-reply-falls-back": ("37b292249be5fd7a", "6d1825e7b049a3d2", "60f50a23a460e4db", 30),
    "single-subject-spellings": ("16536d5c509af416", "7d37df75f6be1412", "e2b18b4fa2c843d4", 18),
    "statement-check-failure-keeps-sentences": ("4218c948ec8b73a6", "cb586914df711d1c", "ac8672b5735ade97", 31),
    "two-subjects-one-value": ("9965f56586420ea6", "f864e02a6beea058", "0a9926b705ca7232", 16),
    "two-versions-one-target": ("290c9cb58f2d7619", "9b469dabcb7b7df6", "249a41ee3e685a00", 16),
    "unattributed-relay-prose": ("5962091717fdfd90", "5f1119c7995c420f", "3d9f3d2435df6a6f", 16),
    "unsupported-mechanism": ("6ee2f13de7243f61", "e11f85e4649ce755", "3db97d2e6100f225", 33),
    "validated-cache-reuse": ("b3e4d61078ef9e41", "a000bdf6623b0641", "aa22ef8759903050", 28),
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
    # The read this row's script makes on its forced last turn happens in the
    # extra pass instead, so one researcher.tool_call sits later in the order.
    "extra-pass-recovers-missing-target": ("35b2c5d90b741aae", 73),
    # The read this row's script makes on its forced last turn happens in the
    # extra pass instead, so one researcher.tool_call sits later in the order.
    "extra-pass-redrafts-the-gaining-part": ("8dc9953c25609e20", 68),
    "figure-not-on-page-dropped": ("4300aed5bf981011", 43),
    "forecast-versus-actual-kept-apart": ("441cde5a6daccc68", 65),
    "maker-notes-vs-relay": ("6fb5d6e45c766113", 36),
    "memory-is-not-read": ("2f97159ff35b43a3", 64),
    # The read this row's script makes on its forced last turn happens in the
    # extra pass instead, so one researcher.tool_call sits later in the order.
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
    """With a scripted model that obeys the
    last-turn instruction -- it answers without a tool, as the prompt tells
    it to -- a row that skips its forced turns asks everything it asked with
    them, less the forced turns' own requests: every request outside research
    byte for byte, every research request up to its live acquisition-state
    snapshot; and it records the same events in the same order."""
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
