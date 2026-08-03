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

from deep_research.memory.long_term import LongTermMemory
from deep_research.memory.procedural import ProceduralMemory
from deep_research.utils.types import Finding, MemorySnapshot

# What a recalled finding is filed under when the entry that produced it
# never recorded a sub-topic. ``Finding.related_sub_topic`` is a required
# non-blank string and inventing a plausible topic would be a lie.
RECALLED_SUB_TOPIC = "recalled from long-term memory"

DEFAULT_RECALL_TOP_K = 5
MAX_SUGGESTED_STRATEGIES = 10


def _recalled_finding(entry) -> Finding | None:  # noqa: ANN001 - MemoryEntry
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


async def recall_memory_context(
    *,
    question: str,
    long_term: LongTermMemory | None,
    procedural: ProceduralMemory | None = None,
    top_k: int = DEFAULT_RECALL_TOP_K,
) -> MemorySnapshot:
    """Recall prior findings, source reputations, and strategies."""
    findings: list[Finding] = []
    reputations: dict[str, float] = {}

    if long_term is not None:
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

    strategies: list[str] = []
    if procedural is not None:
        for record in procedural.strategies:
            for template in record.query_templates:
                if template not in strategies:
                    strategies.append(template)
                if len(strategies) >= MAX_SUGGESTED_STRATEGIES:
                    break
            if len(strategies) >= MAX_SUGGESTED_STRATEGIES:
                break

    return MemorySnapshot(
        similar_findings=findings,
        known_source_reputations=reputations,
        suggested_strategies=strategies,
    )
