"""The Evidence Verifier (spec §5): Figure Match, then the Context Check.

Figure Match is code: the finding's snippet must be on its read, and each
structured figure must be in the snippet under the fixed normalisation of
``figures``. It never parses ``content`` and never searches a whole page for
a number.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from deep_research.agents.evidence import excerpt_matches
from deep_research.agents.figures import figure_in_text
from deep_research.utils.types import Finding, ReadRecord

EVIDENCE_VERIFIER_NAME = "evidence_verifier"


@dataclass(frozen=True)
class FigureMatch:
    """Spec §5.1's two results for one finding."""

    read_found: bool
    snippet_on_page: bool
    matched: tuple[bool, ...]


def read_text(read: ReadRecord) -> str:
    """The read's stored text: every passage, in document order."""
    return " ".join(read.passages.values())


def figure_match(finding: Finding, reads: Mapping[str, ReadRecord]) -> FigureMatch:
    """Is the snippet on its page, and is each figure in the snippet?"""
    unmatched = tuple(False for _ in finding.figures)
    read = reads.get(finding.read_id or "")
    if read is None:
        return FigureMatch(read_found=False, snippet_on_page=False, matched=unmatched)
    if not finding.snippet or not excerpt_matches(read_text(read), finding.snippet):
        return FigureMatch(read_found=True, snippet_on_page=False, matched=unmatched)
    return FigureMatch(
        read_found=True,
        snippet_on_page=True,
        matched=tuple(
            figure_in_text(item.value, item.unit, finding.snippet)
            for item in finding.figures
        ),
    )
