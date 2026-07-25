# Research State And Types Design

Date: 2026-07-25
Status: Draft for review
Parent Spec: 2026-07-25-agentic-deep-research-design.md

## Purpose

Define the shared state and domain contracts passed between tools, agents, memory, observability, and LangGraph.

## Scope

This feature defines:

- `ResearchState`
- Core domain models
- Event and error models
- Serialization rules
- State merge rules for graph updates

## Non-Goals

- No LangGraph orchestration.
- No agent implementations.
- No external provider calls.

## Design

Use Pydantic models for domain objects because validation and serialization matter across API, UI, memory, and traces. If LangGraph requires dict-compatible state, convert Pydantic models to dictionaries at graph boundaries.

Core models:

- `SubTopic`
- `Finding`
- `ScoredSource`
- `Claim`
- `Critique`
- `MemorySnapshot`
- `ResearchEvent`
- `ResearchError`
- `ResearchState`

`ResearchState` fields:

- `session_id`
- `original_question`
- `sub_topics`
- `raw_findings`
- `evaluated_sources`
- `verified_claims`
- `report`
- `critique`
- `iteration`
- `max_iterations`
- `memory_context`
- `events`
- `errors`

Model rules:

- Scores use floats from 0.0 to 1.0 except Critic score, which uses integer 1 to 10.
- Timestamps use timezone-aware ISO 8601 strings.
- URLs are stored as strings but validated when they are produced by network tools.
- Claims preserve evidence URLs and contradiction notes.
- Events use structured fields rather than free-form logs.

## State Updates

Agents should return partial state updates or a full updated state consistently. The chosen implementation should avoid in-place mutation leaks across tests by providing copy/update helpers.

Merge rules:

- Lists append by default.
- Scalar fields replace by default.
- `critique` replaces the previous critique.
- `report` replaces the previous report.
- `iteration` increments only through graph routing logic.

## Error Handling

Validation errors should fail fast in tests and typed boundaries. Runtime recoverable errors should be represented as `ResearchError` entries and included in state instead of raising unless the session cannot continue.

## Testing

Tests should cover:

- Model validation.
- Default state construction.
- Score bounds.
- Event serialization.
- Error serialization.
- State copy/update behavior.

## Acceptance Criteria

- All shared models are typed and serializable.
- Invalid scores and missing required fields fail validation.
- State can be converted to and from a dict without data loss.
- Tests document expected merge behavior.
