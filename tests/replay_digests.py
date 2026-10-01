"""The request and event digests of one replay run (latency plan, Task 1).

A replay's scripted completer records every provider request as ``(agent:schema,
text)``. A digest is sha256[:16] over the sorted ``"agent:schema sha256[:16]"``
lines of a run's requests, joined by newlines. Sorting makes it
order-insensitive: a change that only moves a request earlier or later keeps
it, and a change to one byte of one request, or one request more or fewer,
moves it. Three request digests are kept per run:

* ``full`` -- every request, byte for byte;
* ``timing_free`` -- every request with its acquisition-state snapshot lines
  left out: the lines of a researcher request that print the run's live
  acquisition state (queue, attempted and read URLs, counters, candidate and
  read rows, evidence rows, recorded findings) as it stands at the moment the
  request is built. Which state a request catches depends on how the loops of
  a run happen to interleave, not on what any agent decided;
* ``outside_research`` -- every request not made by the researcher, byte for
  byte: what the planner, source evaluator, verifier, writer and reviewer
  were asked.

The event digest is sha256[:16] over the run's event types, newline-joined,
in the order its final state records them -- the names and the order a reader
of the run's events (the web app's run state) depends on.

The one wall-clock value a request carries is a seeded memory entry's own
``timestamp`` inside a ``query_memory`` observation (the ``memory-is-not-read``
row seeds its entries at run time); it is replaced by a placeholder first, so
a digest is about the run and not the instant it ran.

``python -m tests.replay_digests --pin`` (from the repository root, with
``PYTHONPATH=src``) re-pins after a merge: it runs every row, rewrites the
values of both pin dictionaries in ``tests/test_e2e_evaluation/
test_request_digests.py`` and prints, per row, which of ``full``,
``timing_free``, ``outside_research``, ``count`` and ``events`` moved. The
comment that says why is the re-pinner's to write.

Not collected by pytest: the filename does not match ``test_*.py``.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path

from deep_research.e2e_evaluation.replay import (
    ReplayRun,
    network_denied,
    run_replay_scenario,
)
from deep_research.e2e_evaluation.replay_matrix import scenario_by_id

_WALL_CLOCK = re.compile(r'"timestamp": "\d{4}-\d{2}-\d{2}T[0-9:.]+\+00:00"')
_ACQUISITION_SNAPSHOT = re.compile(
    r"^- (?:next_action=|pending_passage_ids=|candidate_urls=|attempted_urls="
    r"|denied_urls=|read_urls=|consecutive_searches=|next_required_support_type="
    r"|candidate_id=|read_id=|evidence_id=|recorded finding |passage read_id=)"
)


def _line(key: str, text: str) -> str:
    return f"{key} {hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}"


def _clock_free(text: str) -> str:
    return _WALL_CLOCK.sub('"timestamp": "<wall clock>"', text)


def request_lines(sequence: Sequence[tuple[str, str]]) -> list[str]:
    """One sorted ``"agent:schema sha256[:16]"`` line per request, byte for byte."""
    return sorted(_line(key, _clock_free(text)) for key, text in sequence)


def timing_free_lines(sequence: Sequence[tuple[str, str]]) -> list[str]:
    """``request_lines`` with each request's acquisition-state snapshot left out."""
    return sorted(
        _line(
            key,
            "\n".join(
                line
                for line in _clock_free(text).splitlines()
                if not _ACQUISITION_SNAPSHOT.match(line)
            ),
        )
        for key, text in sequence
    )


def _digest(lines: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]


def request_digests(sequence: Sequence[tuple[str, str]]) -> tuple[str, str, str, int]:
    """``(full, timing_free, outside_research, request count)`` of one run."""
    outside = [(key, text) for key, text in sequence if not key.startswith("researcher:")]
    return (
        _digest(request_lines(sequence)),
        _digest(timing_free_lines(sequence)),
        _digest(request_lines(outside)),
        len(sequence),
    )


def event_types(run: ReplayRun) -> list[str]:
    """The run's event types, in the order its final state records them."""
    return [event.event_type for event in run.state.events]


def event_digest(run: ReplayRun) -> tuple[str, int]:
    """``(digest, event count)`` of the run's event types, in order."""
    types = event_types(run)
    return _digest(types), len(types)


def replay_run(case_id: str, root: Path) -> ReplayRun:
    """One repetition of ``case_id``, network denied.

    The session id is the suite's own first repetition's, so a request that
    names its session names the same one the suite's artifact records.
    """
    with network_denied() as attempts:
        run = run_replay_scenario(
            scenario_by_id(case_id), root=root, session_id=f"replay-{case_id}-r1"
        )
    assert attempts == [], attempts
    return run


def replay_requests(case_id: str, root: Path) -> list[tuple[str, str]]:
    """Every request one repetition of ``case_id`` sent, network denied."""
    return list(replay_run(case_id, root).replay.completer.packet_sequence)


_PINS = Path(__file__).parent / "test_e2e_evaluation" / "test_request_digests.py"
_FIELDS = ("full", "timing_free", "outside_research", "count")


def _value(values: Sequence[object]) -> str:
    return "(" + ", ".join(f'"{v}"' if isinstance(v, str) else str(v) for v in values) + ")"


def repin(root: Path) -> tuple[list[str], int]:
    """Rewrite both pin dictionaries from a fresh run of every row.

    Returns one line per row that moved, naming what moved, and the number of
    rows. Only pinned values change; every comment stays as it was.
    """
    from tests.test_e2e_evaluation.test_request_digests import (
        PINNED_EVENT_DIGESTS,
        PINNED_REQUEST_DIGESTS,
    )

    text = _PINS.read_text(encoding="utf-8")
    report: list[str] = []
    for case_id in sorted(PINNED_REQUEST_DIGESTS):
        run = replay_run(case_id, root / case_id)
        requests = request_digests(list(run.replay.completer.packet_sequence))
        events = event_digest(run)
        old_requests, old_events = PINNED_REQUEST_DIGESTS[case_id], PINNED_EVENT_DIGESTS[case_id]
        moved = [name for name, a, b in zip(_FIELDS, old_requests, requests) if a != b]
        if old_events != events:
            moved.append("events")
        if not moved:
            continue
        report.append(f"{case_id}: {', '.join(moved)}")
        for old, new in ((old_requests, requests), (old_events, events)):
            if old != new:
                line = f'    "{case_id}": {_value(old)},\n'
                assert text.count(line) == 1, case_id
                text = text.replace(line, f'    "{case_id}": {_value(new)},\n')
    _PINS.write_text(text, encoding="utf-8")
    return report, len(PINNED_REQUEST_DIGESTS)


if __name__ == "__main__":
    import sys
    import tempfile

    if sys.argv[1:] != ["--pin"]:
        raise SystemExit("usage: python -m tests.replay_digests --pin")
    with tempfile.TemporaryDirectory() as directory:
        moved, rows = repin(Path(directory))
    print("\n".join(moved) if moved else "no row moved")
    print(f"{len(moved)} of {rows} rows moved")
