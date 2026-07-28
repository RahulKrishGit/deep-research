# Research State And Types Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build validated, serializable shared research domain models plus copy-safe state update helpers for later tools, agents, memory, observability, and LangGraph integration.

**Architecture:** Keep the stable public contract in `deep_research.utils.types`, matching the package layout promised by the parent design. Pydantic models own boundary validation and JSON-safe serialization, while pure helpers create new `ResearchState` instances for ordinary merges and graph-controlled iteration advances without mutating caller-owned state.

**Tech Stack:** Python 3.11+, Pydantic 2, pytest, Ruff

## Global Constraints

- Use Pydantic models for domain objects because validation and serialization matter across API, UI, memory, and traces.
- Scores use floats from 0.0 to 1.0 except Critic score, which uses integer 1 to 10.
- Timestamps use timezone-aware ISO 8601 strings.
- URLs are stored as strings but validated when they are produced by network tools.
- Claims preserve evidence URLs and contradiction notes.
- Events use structured fields rather than free-form logs.
- Lists append by default; scalar fields replace by default.
- `critique` replaces the previous critique; `report` replaces the previous report.
- `iteration` increments only through graph routing logic.
- Validation errors fail fast in tests and typed boundaries.
- Runtime recoverable errors are represented as `ResearchError` entries and included in state instead of raising unless the session cannot continue.
- Do not add LangGraph orchestration, agent implementations, or external provider calls.
- Keep `requires-python = ">=3.11"` and `pydantic>=2` unchanged.

---

## File Structure

- Create `src/deep_research/utils/types.py`: aliases, domain models, event/error models, `ResearchState`, partial updates, and merge helpers.
- Modify `src/deep_research/utils/__init__.py`: explicit public re-exports.
- Create `tests/test_types.py`: domain validation, score bounds, timestamps, URL storage, event/error serialization.
- Create `tests/test_state.py`: defaults, dict round-trip, merge rules, copy isolation, graph-only iteration changes.
- Modify `tests/test_imports.py`: package-level import smoke coverage.

### Task 1: Core Domain Contracts

**Files:**
- Create: `src/deep_research/utils/types.py`
- Create: `tests/test_types.py`

**Interfaces:**
- Consumes: Pydantic 2 `BaseModel`, `ConfigDict`, `Field`, and `AfterValidator`.
- Produces: `AwareISOString`, `UnitScore`, `CriticScore`, `ClaimVerdict`, `SubTopic`, `Finding`, `ScoredSource`, `Claim`, `Critique`, and `MemorySnapshot`.

- [ ] **Step 1: Write the failing domain-model tests**

Create `tests/test_types.py`:

```python
"""Tests for shared research domain contracts."""

import pytest
from pydantic import ValidationError

from deep_research.utils.types import (
    Claim,
    Critique,
    Finding,
    MemorySnapshot,
    ScoredSource,
    SubTopic,
)


def scored_source(**overrides: object) -> ScoredSource:
    values = {
        "url": "https://example.com/source",
        "title": "Example source",
        "authority_score": 0.8,
        "recency_score": 0.7,
        "relevance_score": 0.9,
        "corroboration_score": 0.6,
        "overall_score": 0.75,
        "rationale": "Relevant and independently supported.",
    }
    values.update(overrides)
    return ScoredSource.model_validate(values)


def critique(**overrides: object) -> Critique:
    values = {
        "score": 7,
        "gaps": [],
        "unsupported_claims": [],
        "recommended_queries": [],
        "should_continue": False,
        "rationale": "The report meets the threshold.",
    }
    values.update(overrides)
    return Critique.model_validate(values)


def test_domain_models_preserve_required_fields() -> None:
    topic = SubTopic(
        title="Adoption",
        rationale="Measure current adoption patterns.",
        search_queries=["enterprise AI adoption 2026"],
        success_criteria=["Find two independent estimates."],
        priority=1,
    )
    finding = Finding(
        content="Adoption increased year over year.",
        source_url="not-validated-at-this-boundary",
        source_title="Industry survey",
        extracted_at="2026-07-25T12:00:00+00:00",
        confidence=0.8,
        related_sub_topic="Adoption",
    )
    claim = Claim(
        text="Adoption increased year over year.",
        source_urls=["https://example.com/a", "https://example.org/b"],
        verdict="verified",
        confidence=0.9,
        evidence=["Two independent surveys report an increase."],
        contradictions=["One regional survey reported flat adoption."],
    )
    memory = MemorySnapshot(
        similar_findings=[finding],
        known_source_reputations={"example.com": 0.85},
        suggested_strategies=["Compare independent surveys."],
    )

    assert topic.priority == 1
    assert finding.source_url == "not-validated-at-this-boundary"
    assert claim.contradictions == ["One regional survey reported flat adoption."]
    assert memory.similar_findings == [finding]


@pytest.mark.parametrize("value", [-0.01, 1.01])
def test_unit_scores_reject_out_of_range_values(value: float) -> None:
    with pytest.raises(ValidationError):
        scored_source(authority_score=value)


@pytest.mark.parametrize("value", [0, 11])
def test_critic_score_rejects_out_of_range_values(value: int) -> None:
    with pytest.raises(ValidationError):
        critique(score=value)


def test_finding_rejects_timezone_naive_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        Finding(
            content="A finding.",
            source_url="https://example.com/source",
            source_title="Example source",
            extracted_at="2026-07-25T12:00:00",
            confidence=0.8,
            related_sub_topic="Adoption",
        )


def test_missing_required_field_fails_validation() -> None:
    with pytest.raises(ValidationError):
        SubTopic.model_validate(
            {
                "title": "Adoption",
                "rationale": "Measure adoption.",
                "search_queries": ["enterprise adoption"],
                "success_criteria": ["Find two estimates."],
            }
        )


def test_domain_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        scored_source(undocumented_score=0.5)
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `python -m pytest tests/test_types.py -v`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'deep_research.utils.types'`.

- [ ] **Step 3: Write the shared aliases and base model**

Create `src/deep_research/utils/types.py` with:

```python
"""Shared typed contracts for research state and domain data."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, TypeAlias

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def _validate_aware_iso8601(value: str) -> str:
    """Require an ISO 8601 timestamp with timezone information."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be a valid ISO 8601 string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


AwareISOString: TypeAlias = Annotated[str, AfterValidator(_validate_aware_iso8601)]
UnitScore: TypeAlias = Annotated[float, Field(ge=0.0, le=1.0)]
CriticScore: TypeAlias = Annotated[int, Field(ge=1, le=10)]
Priority: TypeAlias = Annotated[int, Field(ge=1)]
ClaimVerdict: TypeAlias = Literal[
    "verified",
    "unverified",
    "contradicted",
    "insufficient_evidence",
]


class ContractModel(BaseModel):
    """Base validation behavior shared by public research contracts."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
    )
```

- [ ] **Step 4: Add the six domain models**

Append to `src/deep_research/utils/types.py`:

```python
class SubTopic(ContractModel):
    title: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    search_queries: list[str] = Field(min_length=1)
    success_criteria: list[str] = Field(min_length=1)
    priority: Priority


class Finding(ContractModel):
    content: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    source_title: str = Field(min_length=1)
    extracted_at: AwareISOString
    confidence: UnitScore
    related_sub_topic: str = Field(min_length=1)


class ScoredSource(ContractModel):
    url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    authority_score: UnitScore
    recency_score: UnitScore
    relevance_score: UnitScore
    corroboration_score: UnitScore
    overall_score: UnitScore
    rationale: str = Field(min_length=1)


class Claim(ContractModel):
    text: str = Field(min_length=1)
    source_urls: list[str] = Field(min_length=1)
    verdict: ClaimVerdict
    confidence: UnitScore
    evidence: list[str]
    contradictions: list[str]


class Critique(ContractModel):
    score: CriticScore
    gaps: list[str]
    unsupported_claims: list[str]
    recommended_queries: list[str]
    should_continue: bool
    rationale: str = Field(min_length=1)


class MemorySnapshot(ContractModel):
    similar_findings: list[Finding] = Field(default_factory=list)
    known_source_reputations: dict[str, UnitScore] = Field(default_factory=dict)
    suggested_strategies: list[str] = Field(default_factory=list)
```

The integer `priority` is one-based: lower numbers are researched first. Do not validate URLs here; network-producing tools own URL validation in their later implementation.

- [ ] **Step 5: Run the domain tests**

Run: `python -m pytest tests/test_types.py -v`

Expected: PASS with 8 collected cases.

- [ ] **Step 6: Commit the domain contracts**

```text
git add src/deep_research/utils/types.py tests/test_types.py
git commit -m "feat: add research domain contracts"
```

### Task 2: Structured Events And Recoverable Errors

**Files:**
- Modify: `src/deep_research/utils/types.py`
- Modify: `tests/test_types.py`

**Interfaces:**
- Consumes: `AwareISOString` and `ContractModel` from Task 1; Pydantic `JsonValue` for JSON-safe metadata.
- Produces: `ResearchEvent(event_type, source, message, timestamp, metadata)` and `ResearchError(error_type, source, message, recoverable, timestamp, details)`.

- [ ] **Step 1: Add failing serialization tests**

Add `from datetime import datetime` with the standard-library imports, add `ResearchEvent` and `ResearchError` to the existing contract import block, then append:

```python
def test_research_event_serializes_and_round_trips() -> None:
    event = ResearchEvent(
        event_type="agent.started",
        source="planner",
        message="Planner started.",
        timestamp="2026-07-25T12:00:00+00:00",
        metadata={
            "iteration": 0,
            "queries": ["enterprise AI adoption"],
            "counts": {"sub_topics": 3},
        },
    )
    payload = event.model_dump(mode="json")

    assert payload == {
        "event_type": "agent.started",
        "source": "planner",
        "message": "Planner started.",
        "timestamp": "2026-07-25T12:00:00+00:00",
        "metadata": {
            "iteration": 0,
            "queries": ["enterprise AI adoption"],
            "counts": {"sub_topics": 3},
        },
    }
    assert ResearchEvent.model_validate(payload) == event


def test_research_error_serializes_and_round_trips() -> None:
    error = ResearchError(
        error_type="search_timeout",
        source="web_search",
        message="The search provider timed out.",
        recoverable=True,
        timestamp="2026-07-25T12:01:00Z",
        details={"retry_count": 2, "provider": "tavily"},
    )
    payload = error.model_dump(mode="json")

    assert payload["recoverable"] is True
    assert payload["timestamp"] == "2026-07-25T12:01:00Z"
    assert payload["details"] == {"retry_count": 2, "provider": "tavily"}
    assert ResearchError.model_validate(payload) == error


def test_event_and_error_defaults_are_json_safe_and_timezone_aware() -> None:
    event = ResearchEvent(
        event_type="session.started",
        source="orchestrator",
        message="Research session started.",
    )
    error = ResearchError(
        error_type="trace_failure",
        source="langsmith",
        message="Tracing failed; continuing locally.",
    )

    assert datetime.fromisoformat(event.timestamp).utcoffset() is not None
    assert datetime.fromisoformat(error.timestamp).utcoffset() is not None
    assert event.metadata == {}
    assert error.details == {}
    assert error.recoverable is True
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `python -m pytest tests/test_types.py -v`

Expected: FAIL during collection because `ResearchEvent` and `ResearchError` are undefined.

- [ ] **Step 3: Implement event and error contracts**

Change the imports in `src/deep_research/utils/types.py`:

```python
from datetime import datetime, timezone

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue
```

Add after `_validate_aware_iso8601`:

```python
def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
```

Append after `MemorySnapshot`:

```python
class ResearchEvent(ContractModel):
    event_type: str = Field(min_length=1)
    source: str = Field(min_length=1)
    message: str = Field(min_length=1)
    timestamp: AwareISOString = Field(default_factory=_utc_now_iso)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ResearchError(ContractModel):
    error_type: str = Field(min_length=1)
    source: str = Field(min_length=1)
    message: str = Field(min_length=1)
    recoverable: bool = True
    timestamp: AwareISOString = Field(default_factory=_utc_now_iso)
    details: dict[str, JsonValue] = Field(default_factory=dict)
```

The message is never the only classification field: later consumers filter and route by `event_type`, `error_type`, `source`, and structured metadata/details.

- [ ] **Step 4: Run the event/error tests**

Run: `python -m pytest tests/test_types.py -v`

Expected: PASS with 11 collected cases.

- [ ] **Step 5: Commit structured events and errors**

```text
git add src/deep_research/utils/types.py tests/test_types.py
git commit -m "feat: add research events and errors"
```

### Task 3: Research State And Dict Round-Trip

**Files:**
- Modify: `src/deep_research/utils/types.py`
- Create: `tests/test_state.py`

**Interfaces:**
- Consumes: every model from Tasks 1 and 2.
- Produces: `ResearchState(session_id, original_question, sub_topics, raw_findings, evaluated_sources, verified_claims, report, critique, iteration, max_iterations, memory_context, events, errors)` with `iteration=0`, `max_iterations=3`, empty collection defaults, and nullable report/critique.

- [ ] **Step 1: Write state fixtures and the failing default test**

Create `tests/test_state.py`:

```python
"""Tests for research state construction, serialization, and updates."""

import pytest
from pydantic import ValidationError

from deep_research.utils.types import (
    Claim,
    Critique,
    Finding,
    MemorySnapshot,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ScoredSource,
    SubTopic,
)


def sub_topic(title: str = "Adoption", priority: int = 1) -> SubTopic:
    return SubTopic(
        title=title,
        rationale=f"Research {title.lower()}.",
        search_queries=[f"{title.lower()} evidence"],
        success_criteria=[f"Find evidence about {title.lower()}."],
        priority=priority,
    )


def finding(content: str = "Adoption increased.") -> Finding:
    return Finding(
        content=content,
        source_url="https://example.com/source",
        source_title="Example source",
        extracted_at="2026-07-25T12:00:00+00:00",
        confidence=0.8,
        related_sub_topic="Adoption",
    )


def source(title: str = "Example source") -> ScoredSource:
    return ScoredSource(
        url="https://example.com/source",
        title=title,
        authority_score=0.8,
        recency_score=0.7,
        relevance_score=0.9,
        corroboration_score=0.6,
        overall_score=0.75,
        rationale="Relevant and independently corroborated.",
    )


def claim(text: str = "Adoption increased.") -> Claim:
    return Claim(
        text=text,
        source_urls=["https://example.com/source"],
        verdict="verified",
        confidence=0.9,
        evidence=["The source reports a year-over-year increase."],
        contradictions=[],
    )


def critique(score: int = 8) -> Critique:
    return Critique(
        score=score,
        gaps=[],
        unsupported_claims=[],
        recommended_queries=[],
        should_continue=False,
        rationale="The report is complete.",
    )


def test_default_state_construction_uses_independent_values() -> None:
    first = ResearchState(session_id="session-1", original_question="Question one?")
    second = ResearchState(session_id="session-2", original_question="Question two?")

    first.sub_topics.append(sub_topic())
    first.memory_context.suggested_strategies.append("Compare surveys.")

    assert first.iteration == 0
    assert first.max_iterations == 3
    assert first.report is None
    assert first.critique is None
    assert second.sub_topics == []
    assert second.memory_context == MemorySnapshot()
    assert second.events == []
    assert second.errors == []
```

- [ ] **Step 2: Add failing round-trip and validation tests**

Append to `tests/test_state.py`:

```python
def test_state_round_trips_through_json_compatible_dict() -> None:
    state = ResearchState(
        session_id="session-1",
        original_question="How is enterprise AI adoption changing?",
        sub_topics=[sub_topic()],
        raw_findings=[finding()],
        evaluated_sources=[source()],
        verified_claims=[claim()],
        report="# Research report",
        critique=critique(),
        iteration=1,
        max_iterations=3,
        memory_context=MemorySnapshot(
            similar_findings=[finding("Prior adoption also increased.")],
            known_source_reputations={"example.com": 0.85},
            suggested_strategies=["Compare independent surveys."],
        ),
        events=[
            ResearchEvent(
                event_type="agent.completed",
                source="planner",
                message="Planner completed.",
                timestamp="2026-07-25T12:01:00+00:00",
                metadata={"sub_topic_count": 1},
            )
        ],
        errors=[
            ResearchError(
                error_type="search_timeout",
                source="web_search",
                message="One search request timed out.",
                timestamp="2026-07-25T12:02:00+00:00",
                details={"retry_count": 2},
            )
        ],
    )

    payload = state.model_dump(mode="json")
    restored = ResearchState.model_validate(payload)

    assert restored == state
    assert restored.model_dump(mode="json") == payload


@pytest.mark.parametrize(
    ("session_id", "question"),
    [("", "A question?"), ("session-1", "")],
)
def test_state_rejects_empty_identity_fields(
    session_id: str,
    question: str,
) -> None:
    with pytest.raises(ValidationError):
        ResearchState(session_id=session_id, original_question=question)


def test_state_rejects_iteration_above_maximum() -> None:
    with pytest.raises(ValidationError, match="iteration cannot exceed max_iterations"):
        ResearchState(
            session_id="session-1",
            original_question="A question?",
            iteration=4,
            max_iterations=3,
        )
```

- [ ] **Step 3: Run the focused state tests and verify failure**

Run: `python -m pytest tests/test_state.py -v`

Expected: FAIL during collection because `ResearchState` is undefined.

- [ ] **Step 4: Implement the complete state model**

Add `model_validator` to the Pydantic imports, then append to `src/deep_research/utils/types.py`:

```python
class ResearchState(ContractModel):
    session_id: str = Field(min_length=1)
    original_question: str = Field(min_length=1)
    sub_topics: list[SubTopic] = Field(default_factory=list)
    raw_findings: list[Finding] = Field(default_factory=list)
    evaluated_sources: list[ScoredSource] = Field(default_factory=list)
    verified_claims: list[Claim] = Field(default_factory=list)
    report: str | None = None
    critique: Critique | None = None
    iteration: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=3, ge=1)
    memory_context: MemorySnapshot = Field(default_factory=MemorySnapshot)
    events: list[ResearchEvent] = Field(default_factory=list)
    errors: list[ResearchError] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_iteration_bounds(self) -> ResearchState:
        if self.iteration > self.max_iterations:
            raise ValueError("iteration cannot exceed max_iterations")
        return self
```

Pydantic's `model_dump(mode="json")` and `ResearchState.model_validate(payload)` are the graph/API serialization boundary. Do not add handwritten serializers.

- [ ] **Step 5: Run state and domain tests**

Run: `python -m pytest tests/test_state.py tests/test_types.py -v`

Expected: PASS with 16 collected cases.

- [ ] **Step 6: Commit the research state model**

```text
git add src/deep_research/utils/types.py tests/test_state.py
git commit -m "feat: add serializable research state"
```

### Task 4: Copy-Safe Merge Rules And Public Exports

**Files:**
- Modify: `src/deep_research/utils/types.py`
- Modify: `src/deep_research/utils/__init__.py:1`
- Modify: `tests/test_state.py`
- Modify: `tests/test_imports.py`

**Interfaces:**
- Consumes: `ResearchState` and every nested contract from Tasks 1 through 3.
- Produces: `ResearchStateUpdate`, `merge_research_state(state, update) -> ResearchState`, `advance_research_iteration(state) -> ResearchState`, and package-level re-exports.

- [ ] **Step 1: Write failing append-versus-replace tests**

Add `advance_research_iteration` and `merge_research_state` to the import block in `tests/test_state.py`, then append:

```python
@pytest.mark.parametrize(
    ("field_name", "item"),
    [
        ("sub_topics", sub_topic()),
        ("raw_findings", finding()),
        ("evaluated_sources", source()),
        ("verified_claims", claim()),
        (
            "events",
            ResearchEvent(
                event_type="agent.started",
                source="researcher",
                message="Researcher started.",
            ),
        ),
        (
            "errors",
            ResearchError(
                error_type="search_timeout",
                source="web_search",
                message="Search timed out.",
            ),
        ),
    ],
)
def test_merge_appends_lists_without_mutating_original(
    field_name: str,
    item: object,
) -> None:
    state = ResearchState(session_id="session-1", original_question="A question?")

    merged = merge_research_state(state, {field_name: [item]})

    assert getattr(merged, field_name) == [item]
    assert getattr(state, field_name) == []


def test_merge_replaces_scalars_critique_report_and_memory() -> None:
    old_critique = critique(score=6).model_copy(update={"should_continue": True})
    new_critique = critique(score=9)
    new_memory = MemorySnapshot(
        similar_findings=[finding("A recalled finding.")],
        known_source_reputations={"example.com": 0.9},
        suggested_strategies=["Prefer primary sources."],
    )
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        report="Old report",
        critique=old_critique,
        memory_context=MemorySnapshot(suggested_strategies=["Old strategy."]),
    )

    merged = merge_research_state(
        state,
        {
            "report": "New report",
            "critique": new_critique,
            "max_iterations": 5,
            "memory_context": new_memory,
        },
    )

    assert merged.report == "New report"
    assert merged.critique == new_critique
    assert merged.max_iterations == 5
    assert merged.memory_context == new_memory
    assert state.report == "Old report"
    assert state.critique == old_critique


def test_merge_deep_copies_unchanged_nested_values() -> None:
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        memory_context=MemorySnapshot(similar_findings=[finding()]),
    )

    merged = merge_research_state(state, {"report": "Draft report"})
    merged.memory_context.similar_findings.append(finding("A new finding."))

    assert len(merged.memory_context.similar_findings) == 2
    assert len(state.memory_context.similar_findings) == 1


def test_merge_deep_copies_supplied_nested_values() -> None:
    replacement = MemorySnapshot(similar_findings=[finding()])
    state = ResearchState(session_id="session-1", original_question="A question?")

    merged = merge_research_state(state, {"memory_context": replacement})
    merged.memory_context.similar_findings.append(finding("A new finding."))

    assert len(merged.memory_context.similar_findings) == 2
    assert len(replacement.similar_findings) == 1
```

- [ ] **Step 2: Write failing merge-guard and graph-iteration tests**

Append to `tests/test_state.py`:

```python
def test_merge_rejects_unknown_fields() -> None:
    state = ResearchState(session_id="session-1", original_question="A question?")

    with pytest.raises(ValueError, match="unknown ResearchState fields"):
        merge_research_state(state, {"unknown": "value"})


def test_merge_rejects_iteration_changes() -> None:
    state = ResearchState(session_id="session-1", original_question="A question?")

    with pytest.raises(ValueError, match="advance_research_iteration"):
        merge_research_state(state, {"iteration": 1})


def test_graph_iteration_advance_returns_a_new_state() -> None:
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        iteration=1,
        max_iterations=3,
    )

    advanced = advance_research_iteration(state)

    assert advanced.iteration == 2
    assert state.iteration == 1


def test_graph_iteration_cannot_advance_past_maximum() -> None:
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        iteration=3,
        max_iterations=3,
    )

    with pytest.raises(ValueError, match="max_iterations"):
        advance_research_iteration(state)
```

- [ ] **Step 3: Add a failing public-export smoke test**

Append to `tests/test_imports.py`:

```python
def test_shared_research_types_import_from_utils_package() -> None:
    from deep_research.utils import (  # noqa: F401
        Claim,
        Critique,
        Finding,
        MemorySnapshot,
        ResearchError,
        ResearchEvent,
        ResearchState,
        ScoredSource,
        SubTopic,
        advance_research_iteration,
        merge_research_state,
    )
```

- [ ] **Step 4: Run the focused tests and verify failure**

Run: `python -m pytest tests/test_state.py tests/test_imports.py -v`

Expected: FAIL during collection because the merge helpers are undefined.

- [ ] **Step 5: Implement the typed partial-update contract**

Add `deepcopy` and `TypedDict` to the imports, then append after `ResearchState`:

```python
from copy import deepcopy
from typing import Annotated, Literal, TypeAlias, TypedDict
```

```python
class ResearchStateUpdate(TypedDict, total=False):
    session_id: str
    original_question: str
    sub_topics: list[SubTopic]
    raw_findings: list[Finding]
    evaluated_sources: list[ScoredSource]
    verified_claims: list[Claim]
    report: str | None
    critique: Critique | None
    max_iterations: int
    memory_context: MemorySnapshot
    events: list[ResearchEvent]
    errors: list[ResearchError]


_APPEND_STATE_FIELDS = frozenset(
    {
        "sub_topics",
        "raw_findings",
        "evaluated_sources",
        "verified_claims",
        "events",
        "errors",
    }
)
```

`ResearchStateUpdate` intentionally omits `iteration`; ordinary agent updates cannot advance the graph loop.

- [ ] **Step 6: Implement pure merge and iteration helpers**

Append to `src/deep_research/utils/types.py`:

```python
def merge_research_state(
    state: ResearchState,
    update: ResearchStateUpdate,
) -> ResearchState:
    unknown_fields = set(update).difference(ResearchState.model_fields)
    if unknown_fields:
        names = ", ".join(sorted(unknown_fields))
        raise ValueError(f"unknown ResearchState fields: {names}")
    if "iteration" in update:
        raise ValueError("use advance_research_iteration to change iteration")

    payload = state.model_dump(mode="python")
    for field_name, value in update.items():
        if field_name in _APPEND_STATE_FIELDS:
            if not isinstance(value, list):
                raise TypeError(f"{field_name} update must be a list")
            payload[field_name] = [*payload[field_name], *deepcopy(value)]
        else:
            payload[field_name] = deepcopy(value)

    return ResearchState.model_validate(payload)


def advance_research_iteration(state: ResearchState) -> ResearchState:
    if state.iteration >= state.max_iterations:
        raise ValueError("cannot advance iteration beyond max_iterations")

    payload = state.model_dump(mode="python")
    payload["iteration"] = state.iteration + 1
    return ResearchState.model_validate(payload)
```

The dump-and-validate cycle gives callers a fresh object graph, validates every nested update, appends list fields, and replaces all other supplied fields.

- [ ] **Step 7: Replace the utils stub with explicit exports**

Replace `src/deep_research/utils/__init__.py`:

```python
"""Shared utilities and typed research contracts."""

from deep_research.utils.types import (
    AwareISOString,
    Claim,
    ClaimVerdict,
    CriticScore,
    Critique,
    Finding,
    MemorySnapshot,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    ScoredSource,
    SubTopic,
    UnitScore,
    advance_research_iteration,
    merge_research_state,
)

__all__ = [
    "AwareISOString",
    "Claim",
    "ClaimVerdict",
    "CriticScore",
    "Critique",
    "Finding",
    "MemorySnapshot",
    "ResearchError",
    "ResearchEvent",
    "ResearchState",
    "ResearchStateUpdate",
    "ScoredSource",
    "SubTopic",
    "UnitScore",
    "advance_research_iteration",
    "merge_research_state",
]
```

- [ ] **Step 8: Run focused and full verification**

Run:

```text
python -m pytest tests/test_state.py tests/test_imports.py -v
python -m pytest
python -m ruff check src tests
```

Expected: focused tests PASS; the full suite PASS; Ruff prints `All checks passed!`.

- [ ] **Step 9: Commit the merge API and exports**

```text
git add src/deep_research/utils/types.py src/deep_research/utils/__init__.py tests/test_state.py tests/test_imports.py
git commit -m "feat: add research state merge helpers"
```

- [ ] **Step 10: Verify the implementation branch is clean**

Run: `git status --short`

Expected: no output.
