"""Adapt typed long-term memory onto the tools' JSON memory protocol.

``tools.memory_tools`` declares a structural ``LongTermMemory`` protocol —
``save(content, metadata) -> entry_id`` and ``query(query, top_k, filters)``
— because a tool's arguments come from a model and are plain JSON. The
storage layer declares a typed one: ``save(MemoryEntry) -> bool`` and
``query(text, entry_type=..., where=...) -> list[MemoryQueryResult]``.
Neither is wrong and neither should bend, so the translation lives here.

Nothing from ``deep_research.tools`` is imported: the protocol is
structural, and keeping the import out means the memory layer never grows a
dependency on the tool layer.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from pydantic import JsonValue, ValidationError

from deep_research.memory.entries import MemoryEntry
from deep_research.memory.long_term import LongTermMemory

# What a tool-driven save means when the model did not say. A tool call
# that reaches this bridge is an agent keeping a finding for later; nothing
# else is written through a tool.
DEFAULT_BRIDGE_ENTRY_TYPE = "finding"
DEFAULT_BRIDGE_AGENT_ID = "research_agent"

# Metadata keys that map onto ``MemoryEntry`` fields rather than onto its
# free-form ``attributes``. Mirrors ``entries._RESERVED_METADATA_KEYS``.
_ENTRY_FIELD_KEYS = (
    "entry_type",
    "session_id",
    "agent_id",
    "confidence",
    "source_url",
    "source_title",
    "timestamp",
)


def _scalar_attributes(metadata: Mapping[str, JsonValue]) -> dict[str, Any]:
    """Keep only the flat scalars a vector store can hold as metadata."""
    return {
        key: value
        for key, value in metadata.items()
        if key not in _ENTRY_FIELD_KEYS
        and isinstance(value, (str, int, float, bool))
    }


class LongTermMemoryBridge:
    """The tool-facing view of one session's long-term memory."""

    def __init__(self, memory: LongTermMemory, *, session_id: str) -> None:
        if not session_id.strip():
            raise ValueError("session_id must not be blank")
        self._memory = memory
        self._session_id = session_id.strip()

    async def save(
        self,
        content: str,
        metadata: Mapping[str, JsonValue],
    ) -> str:
        """Store one tool-supplied finding and return its entry id.

        Raises rather than returning a sentinel: ``BaseTool.execute`` turns
        any exception into a failed ``ToolResult``, which is what an agent
        must see when a write did not land.
        """
        payload: dict[str, Any] = {
            "entry_id": uuid4().hex,
            "content": content,
            "entry_type": metadata.get("entry_type", DEFAULT_BRIDGE_ENTRY_TYPE),
            "session_id": metadata.get("session_id", self._session_id),
            "agent_id": metadata.get("agent_id", DEFAULT_BRIDGE_AGENT_ID),
            "attributes": _scalar_attributes(metadata),
        }
        for key in ("confidence", "source_url", "source_title", "timestamp"):
            value = metadata.get(key)
            if value is not None:
                payload[key] = value

        try:
            entry = MemoryEntry.model_validate(payload)
        except ValidationError as error:
            raise ValueError(
                "the finding could not be stored in long-term memory "
                "because its metadata was rejected"
            ) from error

        if not await self._memory.save(entry):
            raise RuntimeError(
                "long-term memory is unavailable, so the finding was not "
                "stored"
            )
        return entry.entry_id

    async def query(
        self,
        query: str,
        *,
        top_k: int = 5,
        filters: Mapping[str, JsonValue] | None = None,
    ) -> list[dict[str, JsonValue]]:
        """Search long-term memory and render hits as JSON mappings.

        A non-string ``entry_type`` filter is rejected rather than dropped:
        a model that emits one is misbehaving, and silently broadening the
        query would hand it results it did not ask for. ``BaseTool.execute``
        turns the ``ValueError`` into a failed ``ToolResult`` the agent can
        see.
        """
        where = dict(filters or {})
        entry_type = where.pop("entry_type", None)
        if entry_type is not None and not isinstance(entry_type, str):
            raise ValueError(
                "the memory query was rejected because its entry_type "
                f"filter is {type(entry_type).__name__}, not a string"
            )
        results = await self._memory.query(
            query,
            top_k=top_k,
            entry_type=entry_type,
            where=where or None,
        )
        return [
            {
                "content": result.entry.content,
                "entry_type": result.entry.entry_type,
                "session_id": result.entry.session_id,
                "agent_id": result.entry.agent_id,
                "confidence": result.entry.confidence,
                "source_url": result.entry.source_url,
                "source_title": result.entry.source_title,
                "timestamp": result.entry.timestamp,
                "relevance": round(result.relevance, 4),
                "attributes": dict(result.entry.attributes),
            }
            for result in results
        ]
