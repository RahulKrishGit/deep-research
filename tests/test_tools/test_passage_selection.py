"""Query-aware deterministic passage selection."""

from __future__ import annotations

from deep_research.tools.passage_selection import select_relevant_passages


def test_selection_considers_every_locator_and_keeps_late_paraphrase() -> None:
    passages = {
        "page-1": "Contents and executive summary.",
        "page-2": "The project was commissioned after a queue delay.",
        "page-3": "Appendix and references.",
    }

    assert select_relevant_passages(
        passages, "interconnection waiting time project delivery", 1
    ) == ["page-2"]
