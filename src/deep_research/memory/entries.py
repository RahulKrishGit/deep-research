"""Typed memory records shared by the scratchpad, vector, and strategy layers."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from math import isfinite
from typing import Annotated, Literal, TypeAlias
from uuid import uuid4

from pydantic import AfterValidator, Field, JsonValue, model_validator

from deep_research.utils.types import (
    AwareISOString,
    ContractModel,
    UnitScore,
    _utc_now_iso,
    _validate_finite_json,
)

MemoryEntryType: TypeAlias = Literal[
    "finding",
    "source_reputation",
    "report_summary",
    "failed_strategy",
]
ScratchpadEntryKind: TypeAlias = Literal[
    "thought",
    "observation",
    "decision",
    "summary",
]


_ScalarValue: TypeAlias = str | int | float | bool


def _reject_non_finite_scalar(value: _ScalarValue) -> _ScalarValue:
    """Reject ``nan``/``inf`` floats so metadata survives a JSON round-trip.

    A non-finite float serializes to JSON ``null``, which is not a valid
    ``MetadataValue`` member, so re-validating stored JSON would otherwise
    fail with a confusing error far from the value that caused it.
    """
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("metadata values must be finite")
    return value


MetadataValue: TypeAlias = Annotated[
    _ScalarValue,
    AfterValidator(_reject_non_finite_scalar),
]


_FiniteJsonValue: TypeAlias = Annotated[
    JsonValue, AfterValidator(_validate_finite_json)
]

_RESERVED_METADATA_KEYS = frozenset(
    {
        "agent_id",
        "confidence",
        "entry_type",
        "session_id",
        "source_title",
        "source_url",
        "timestamp",
    }
)


def source_reputation_entry_id(url: str) -> str:
    """Return the stable storage id for one source reputation record."""
    normalized = url.strip()
    if not normalized:
        raise ValueError("source reputation url must not be blank")
    digest = sha256(normalized.encode("utf-8")).hexdigest()
    return f"source_reputation:{digest}"


class MemoryEntry(ContractModel):
    """One durable record stored in long-term semantic memory."""

    entry_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    entry_type: MemoryEntryType
    content: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    confidence: UnitScore = 1.0
    source_url: str | None = Field(default=None, min_length=1)
    source_title: str | None = Field(default=None, min_length=1)
    timestamp: AwareISOString = Field(default_factory=_utc_now_iso)
    attributes: dict[str, MetadataValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_reserved_attributes(self) -> "MemoryEntry":
        collisions = _RESERVED_METADATA_KEYS.intersection(self.attributes)
        if collisions:
            names = ", ".join(sorted(collisions))
            raise ValueError(
                f"attributes may not reuse reserved metadata keys: {names}"
            )
        return self

    def to_metadata(self) -> dict[str, MetadataValue]:
        """Project this entry onto the flat scalar metadata a vector store accepts."""
        metadata: dict[str, MetadataValue] = {
            "entry_type": self.entry_type,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
        }
        if self.source_url is not None:
            metadata["source_url"] = self.source_url
        if self.source_title is not None:
            metadata["source_title"] = self.source_title
        metadata.update(self.attributes)
        return metadata

    @classmethod
    def from_storage(
        cls,
        *,
        entry_id: str,
        document: str,
        metadata: Mapping[str, MetadataValue],
    ) -> "MemoryEntry":
        """Rebuild an entry from a stored document and its flat metadata.

        Reads back through ``model_validate`` rather than subscripting
        ``metadata`` directly, so a malformed or truncated storage record
        (e.g. a backend that dropped a field) raises a catchable
        ``ValidationError`` instead of an uncaught ``KeyError``.
        """
        attributes = {
            key: value
            for key, value in metadata.items()
            if key not in _RESERVED_METADATA_KEYS
        }
        payload: dict[str, object] = {
            "entry_id": entry_id,
            "content": document,
            "attributes": attributes,
        }
        for key in ("entry_type", "session_id", "agent_id", "timestamp"):
            if key in metadata:
                payload[key] = metadata[key]
        if "confidence" in metadata:
            payload["confidence"] = metadata["confidence"]
        if "source_url" in metadata:
            payload["source_url"] = metadata["source_url"]
        if "source_title" in metadata:
            payload["source_title"] = metadata["source_title"]
        return cls.model_validate(payload)


class MemoryQueryResult(ContractModel):
    """One semantic search hit with its raw backend distance."""

    entry: MemoryEntry
    distance: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)

    @property
    def relevance(self) -> float:
        """Map a finite non-negative distance onto ``[0.0, 1.0]``.

        A missing distance is treated as the *lowest* relevance rather than
        a perfect match: this value feeds retrieval ranking, and silently
        scoring an un-distanced hit as an exact match would let a backend
        that omits distances outrank everything that reported one honestly.
        """
        if self.distance is None:
            return 0.0
        return 1.0 / (1.0 + self.distance)


class SourceReputation(ContractModel):
    """A running judgement about how much one source can be trusted."""

    url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    reputation_score: UnitScore
    observations: int = Field(default=1, ge=1)
    notes: str = ""
    last_updated: AwareISOString = Field(default_factory=_utc_now_iso)

    def to_entry(self, *, session_id: str, agent_id: str) -> MemoryEntry:
        attributes: dict[str, MetadataValue] = {
            "reputation_score": self.reputation_score,
            "observations": self.observations,
        }
        content = f"{self.title} ({self.url})"
        if self.notes:
            attributes["notes"] = self.notes
            content = f"{content}: {self.notes}"
        return MemoryEntry(
            entry_id=source_reputation_entry_id(self.url),
            entry_type="source_reputation",
            content=content,
            session_id=session_id,
            agent_id=agent_id,
            confidence=self.reputation_score,
            source_url=self.url,
            source_title=self.title,
            timestamp=self.last_updated,
            attributes=attributes,
        )

    @classmethod
    def from_entry(cls, entry: MemoryEntry) -> "SourceReputation":
        if entry.entry_type != "source_reputation":
            raise ValueError("entry_type must be source_reputation")
        if entry.source_url is None:
            raise ValueError("source_reputation entries require source_url")
        try:
            reputation_score = float(
                entry.attributes.get("reputation_score", entry.confidence)
            )
            observations = int(entry.attributes.get("observations", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "source_reputation entry has malformed reputation attributes"
            ) from exc
        return cls(
            url=entry.source_url,
            title=entry.source_title or entry.source_url,
            reputation_score=reputation_score,
            observations=observations,
            notes=str(entry.attributes.get("notes", "")),
            last_updated=entry.timestamp,
        )


class ScratchpadEntry(ContractModel):
    """One bounded in-session note written by a single agent."""

    agent_name: str = Field(min_length=1)
    kind: ScratchpadEntryKind = "observation"
    content: str = Field(min_length=1)
    timestamp: AwareISOString = Field(default_factory=_utc_now_iso)
    metadata: dict[str, _FiniteJsonValue] = Field(default_factory=dict)


class StrategyRecord(ContractModel):
    """Accumulated outcomes for one topic type in procedural memory."""

    topic_type: str = Field(min_length=1)
    query_templates: list[str] = Field(default_factory=list)
    trusted_source_patterns: list[str] = Field(default_factory=list)
    sessions: int = Field(default=0, ge=0)
    successes: int = Field(default=0, ge=0)
    total_iterations: int = Field(default=0, ge=0)
    notes: list[str] = Field(default_factory=list)
    updated_at: AwareISOString = Field(default_factory=_utc_now_iso)

    @model_validator(mode="after")
    def validate_counts(self) -> "StrategyRecord":
        if self.successes > self.sessions:
            raise ValueError("successes cannot exceed sessions")
        return self

    @property
    def success_rate(self) -> float:
        return self.successes / self.sessions if self.sessions else 0.0

    @property
    def average_iterations(self) -> float:
        return self.total_iterations / self.sessions if self.sessions else 0.0
