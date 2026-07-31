"""Bounded, in-memory, per-agent working memory for a single session.

Scratchpads are never persisted. They hold ReAct summaries, tool observations,
and decisions for one agent inside one session, and they are the fallback
context when long-term memory is unavailable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from pydantic import JsonValue

from deep_research.memory.entries import ScratchpadEntry, ScratchpadEntryKind
from deep_research.memory.errors import MemoryErrorLog
from deep_research.utils.config import ShortTermMemoryConfig
from deep_research.utils.types import ResearchError

#: Note: a summary entry produced by this hook can itself be evicted and
#: re-fed into the summarizer on a later compaction (it is a plain
#: ``ScratchpadEntry`` like any other), so summarizers should tolerate
#: receiving their own prior output.
Summarizer = Callable[[Sequence[ScratchpadEntry]], str]

_DEFAULT_MAX_ENTRIES = 20


class ScratchpadMemory:
    """A sliding window of agent notes with an optional summarization hook."""

    def __init__(
        self,
        *,
        session_id: str,
        agent_name: str,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        summarizer: Summarizer | None = None,
    ) -> None:
        if not session_id.strip():
            raise ValueError("session_id must not be blank")
        if not agent_name.strip():
            raise ValueError("agent_name must not be blank")
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self.session_id = session_id.strip()
        self.agent_name = agent_name.strip()
        self.max_entries = max_entries
        self._summarizer = summarizer
        self._entries: list[ScratchpadEntry] = []
        self._error_log = MemoryErrorLog("scratchpad_memory")

    @classmethod
    def from_config(
        cls,
        config: ShortTermMemoryConfig,
        *,
        session_id: str,
        agent_name: str,
        summarizer: Summarizer | None = None,
    ) -> "ScratchpadMemory":
        return cls(
            session_id=session_id,
            agent_name=agent_name,
            max_entries=config.max_turns,
            summarizer=summarizer,
        )

    @property
    def entries(self) -> tuple[ScratchpadEntry, ...]:
        return tuple(self._entries)

    @property
    def errors(self) -> Sequence[ResearchError]:
        return self._error_log.errors

    def drain_errors(self) -> list[ResearchError]:
        """Return and clear recoverable errors for merging into research state."""
        return self._error_log.drain()

    def __len__(self) -> int:
        return len(self._entries)

    def add(
        self,
        content: str,
        *,
        kind: ScratchpadEntryKind = "observation",
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> ScratchpadEntry:
        """Append one note, compacting the window first when it is full."""
        entry = ScratchpadEntry(
            agent_name=self.agent_name,
            kind=kind,
            content=content,
            metadata=dict(metadata or {}),
        )
        if len(self._entries) >= self.max_entries:
            self._compact()
        self._entries.append(entry)
        self._enforce_bound()
        return entry

    def recent(self, count: int | None = None) -> tuple[ScratchpadEntry, ...]:
        """Return the newest ``count`` entries, oldest first."""
        if count is None:
            return tuple(self._entries)
        if count < 0:
            raise ValueError("count must not be negative")
        if count == 0:
            return ()
        return tuple(self._entries[-count:])

    def clear(self) -> None:
        self._entries.clear()

    def _compact(self) -> None:
        # When a summarizer is configured, compaction must free room for two
        # things: the inserted summary entry, plus the new entry `add()` is
        # about to append. Evicting only one slot would let `_enforce_bound`
        # immediately delete the just-inserted summary from the front.
        min_evict = 2 if self._summarizer is not None else 1
        evict_count = min(
            self.max_entries, max(min_evict, self.max_entries // 2)
        )
        evicted = self._entries[:evict_count]
        self._entries = self._entries[evict_count:]
        if self._summarizer is None or not evicted:
            return
        try:
            summary = self._summarizer(tuple(evicted))
        except Exception as error:
            self._error_log.record(
                error_type="scratchpad_summarization_failed",
                message=(
                    "Scratchpad summarization failed; evicted entries were dropped."
                ),
                error=error,
                details={
                    "session_id": self.session_id,
                    "agent_name": self.agent_name,
                    "evicted_entries": len(evicted),
                },
            )
            return
        # This guard (not the .strip() below) is what actually matters: it
        # keeps a whitespace-only summary from reaching ScratchpadEntry,
        # which would otherwise raise ValidationError outside this try
        # block. The .strip() passed to `content=` below is redundant with
        # it in practice, since ContractModel strips content itself, but is
        # kept for clarity at the call site.
        if not isinstance(summary, str) or not summary.strip():
            return
        self._entries.insert(
            0,
            ScratchpadEntry(
                agent_name=self.agent_name,
                kind="summary",
                content=summary.strip(),
                metadata={"summarized_entries": len(evicted)},
            ),
        )

    def _enforce_bound(self) -> None:
        overflow = len(self._entries) - self.max_entries
        if overflow > 0:
            del self._entries[:overflow]
