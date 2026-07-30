"""Thin long-term-memory adapters built on the shared tool lifecycle."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any, Protocol

from pydantic import JsonValue

from deep_research.observability import Tracker
from deep_research.tools.base import (
    BaseTool,
    ToolCallContext,
    ToolExecution,
    ToolExecutionError,
)


class LongTermMemory(Protocol):
    """Storage contract consumed by the long-term-memory tool adapters."""

    async def save(self, content: str, metadata: Mapping[str, JsonValue]) -> str: ...

    async def query(
        self,
        query: str,
        *,
        top_k: int = 5,
        filters: Mapping[str, JsonValue] | None = None,
    ) -> Sequence[Mapping[str, JsonValue]]: ...


class SaveToMemoryTool(BaseTool):
    """Persist a finding through an injected long-term-memory backend."""

    name = "save_to_memory"
    description = "Save a research finding to long-term memory."
    input_schema = {"content": "string", "metadata": "object"}
    output_schema = {"entry_id": "string"}

    def __init__(self, tracker: Tracker, *, memory: LongTermMemory) -> None:
        super().__init__(tracker)
        self._memory = memory

    def _observability_inputs(self, kwargs: dict[str, Any]) -> dict[str, JsonValue]:
        content = kwargs.get("content")
        return {
            "content_chars": len(content) if isinstance(content, str) else 0,
            "metadata": _json_safe_observability_value(
                kwargs.get("metadata", {})
            ),
        }

    async def _execute(
        self, context: ToolCallContext, **kwargs: Any
    ) -> ToolExecution:
        del context
        content = kwargs.get("content")
        metadata = kwargs.get("metadata", {})
        if not isinstance(content, str) or not content.strip():
            raise _validation_error("content must be a non-empty string")
        _validate_json_mapping(metadata, name="metadata")
        entry_id = await self._memory.save(content, _as_json_mapping(metadata))
        return ToolExecution(
            data={"entry_id": entry_id},
            output_summary={"entry_id": entry_id},
        )


class QueryMemoryTool(BaseTool):
    """Query an injected long-term-memory backend."""

    name = "query_memory"
    description = "Query long-term memory for relevant research findings."
    input_schema = {"query": "string", "top_k": "integer", "filters": "object"}
    output_schema = {"matches": "array"}

    def __init__(self, tracker: Tracker, *, memory: LongTermMemory) -> None:
        super().__init__(tracker)
        self._memory = memory

    def _observability_inputs(self, kwargs: dict[str, Any]) -> dict[str, JsonValue]:
        return {
            "query": _json_safe_observability_value(kwargs.get("query")),
            "top_k": _json_safe_observability_value(kwargs.get("top_k", 5)),
            "filters": _json_safe_observability_value(kwargs.get("filters")),
        }

    async def _execute(
        self, context: ToolCallContext, **kwargs: Any
    ) -> ToolExecution:
        del context
        query = kwargs.get("query")
        top_k = kwargs.get("top_k", 5)
        filters = kwargs.get("filters")
        if not isinstance(query, str) or not query.strip():
            raise _validation_error("query must be a non-empty string")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
            raise _validation_error("top_k must be a positive integer")
        if filters is not None:
            _validate_json_mapping(filters, name="filters")
        matches = await self._memory.query(
            query,
            top_k=top_k,
            filters=None if filters is None else _as_json_mapping(filters),
        )
        ordered_matches = [dict(match) for match in matches]
        return ToolExecution(
            data={"matches": ordered_matches},
            output_summary={"result_count": len(ordered_matches)},
            metadata={"result_count": len(ordered_matches)},
        )


def _validation_error(message: str) -> ToolExecutionError:
    return ToolExecutionError(message, error_type="ValidationError", recoverable=False)


def _validate_json_mapping(value: Any, *, name: str) -> None:
    if not isinstance(value, Mapping):
        raise _validation_error(f"{name} must be a JSON object")
    if not _is_json_safe(value):
        raise _validation_error(f"{name} must contain only JSON-safe values")


def _is_json_safe(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return isfinite(value)
    if isinstance(value, Mapping):
        return all(
            isinstance(key, str) and _is_json_safe(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return all(_is_json_safe(item) for item in value)
    return False


def _as_json_mapping(value: Mapping[str, Any]) -> Mapping[str, JsonValue]:
    return value  # type: ignore[return-value]


def _json_safe_observability_value(value: Any) -> JsonValue:
    if _is_json_safe(value):
        return value  # type: ignore[return-value]
    return "[INVALID_JSON_VALUE]"
