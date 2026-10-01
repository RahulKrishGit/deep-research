"""The note outcomes live below the API (notes-progress-report spec §5.6, §7.2).

The finalizer stamps the bottom line's note lines from ``note_outcome`` and
``note_steering_outcome``; ``graph`` cannot import ``api``, so the two live in
``graph/note_outcomes.py`` and ``api/notes.py`` re-exports them.
"""

from __future__ import annotations

import subprocess
import sys

from deep_research.api import notes as api_notes
from deep_research.graph import note_outcomes


def test_the_graph_reads_note_outcomes_without_importing_the_api() -> None:
    probe = (
        "import sys; import deep_research.graph.note_outcomes; "
        "print(any(name == 'deep_research.api' or name.startswith('deep_research.api.') "
        "for name in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"


def test_the_api_re_exports_the_moved_outcomes() -> None:
    assert api_notes.note_outcome is note_outcomes.note_outcome
    assert api_notes.note_steering_outcome is note_outcomes.note_steering_outcome
    assert api_notes.NoteOutcome is note_outcomes.NoteOutcome
