"""Build the memory snapshot one research session starts from.

The graph deliberately performs no recall — that touches ChromaDB and an
embedding provider, which orchestration has no business owning — so the
caller supplies ``ResearchState.memory_context``. This is that caller's
half of the contract.

Every failure here is silent by design: ``LongTermMemory`` already records
its own recoverable errors and returns empty results, and a session that
cannot remember anything is a worse session, not a failed one.
"""

from __future__ import annotations

from typing import Literal

from deep_research.memory.entries import MemoryEntry
from deep_research.memory.long_term import LongTermMemory
from deep_research.memory.procedural import ProceduralMemory
from deep_research.utils.text import unique_phrases
from deep_research.utils.types import Finding, MemorySnapshot

# What a recalled finding is filed under when the entry that produced it
# never recorded a sub-topic. ``Finding.related_sub_topic`` is a required
# non-blank string and inventing a plausible topic would be a lie.
RECALLED_SUB_TOPIC = "recalled from long-term memory"

DEFAULT_RECALL_TOP_K = 5
MAX_SUGGESTED_STRATEGIES = 10

# Which stage is asking. ``planning`` exists because the planner is the stage
# that decides what the run will try to establish, so remembered prose is
# exactly what must not reach it: a recalled finding is not evidence, and one
# quoted to the planner becomes a settled premise in the plan. Planning gets
# procedural guidance only, and the long-term store is not queried at all.
RecallPurpose = Literal["research", "planning"]


def _recalled_finding(entry: MemoryEntry) -> Finding | None:
    """Render one stored entry as a ``Finding``, or drop it.

    An entry with no source is dropped rather than given a placeholder URL:
    findings carry citations, and a citation nobody can follow is worse
    than one fewer recalled finding.
    """
    if entry.source_url is None:
        return None
    sub_topic = entry.attributes.get("related_sub_topic")
    try:
        return Finding(
            content=entry.content,
            source_url=entry.source_url,
            source_title=entry.source_title or entry.source_url,
            extracted_at=entry.timestamp,
            confidence=entry.confidence,
            related_sub_topic=(
                sub_topic if isinstance(sub_topic, str) and sub_topic.strip()
                else RECALLED_SUB_TOPIC
            ),
        )
    except ValueError:
        return None


def _recalled_strategies(procedural: ProceduralMemory | None) -> list[str]:
    """Every procedural query template the store holds, deduplicated.

    Deduplication is ``utils.text.unique_phrases`` — the same rule the planner
    applies to a target's dimensions, so "the same phrase" means one thing in
    this codebase. A strategy recorded twice, or recorded once by two sessions
    with different spacing, is one piece of guidance; handing the planner the
    same line five times spends its attention on nothing.
    """
    if procedural is None:
        return []
    templates: list[str] = []
    for record in procedural.strategies:
        templates.extend(record.query_templates)
    return unique_phrases(templates)[:MAX_SUGGESTED_STRATEGIES]


async def recall_memory_context(
    *,
    question: str,
    long_term: LongTermMemory | None,
    procedural: ProceduralMemory | None = None,
    top_k: int = DEFAULT_RECALL_TOP_K,
    purpose: RecallPurpose = "research",
) -> MemorySnapshot:
    """Recall prior findings, source reputations, and strategies.

    ``purpose`` is backward compatible: ``"research"`` is the default and
    behaves exactly as before, reading stored findings and their reputations.
    ``"planning"`` returns the available procedural guidance and nothing
    else — no finding query, no reputation lookup, and therefore no
    ``similar_findings`` — because the session's own startup recall is the
    planner's single procedural lookup, and remembered prose is not evidence.
    """
    findings: list[Finding] = []
    reputations: dict[str, float] = {}

    if long_term is not None and purpose == "research":
        results = await long_term.query(
            question, top_k=top_k, entry_type="finding"
        )
        for result in results:
            finding = _recalled_finding(result.entry)
            if finding is not None:
                findings.append(finding)

        for url in dict.fromkeys(finding.source_url for finding in findings):
            reputation = await long_term.get_source_reputation(url)
            if reputation is not None:
                reputations[url] = reputation.reputation_score

    return MemorySnapshot(
        similar_findings=findings,
        known_source_reputations=reputations,
        suggested_strategies=_recalled_strategies(procedural),
    )
