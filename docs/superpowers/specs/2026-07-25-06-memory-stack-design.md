# Memory Stack Design

Date: 2026-07-25
Status: Draft for review
Parent Spec: 2026-07-25-agentic-deep-research-design.md

## Purpose

Implement the agent memory system: short-term scratchpads, long-term vector memory, and procedural strategy memory.

## Scope

This feature adds:

- `ScratchpadMemory`
- `LongTermMemory`
- `ProceduralMemory`
- Memory entry models
- ChromaDB integration
- OpenAI embedding integration

## Non-Goals

- No autonomous prompt rewriting.
- No external vector databases beyond ChromaDB.
- No multi-user memory isolation beyond session and metadata fields.

## Design

Scratchpad memory:

- Per-agent and per-session.
- Stores bounded ReAct summaries and observations.
- Supports recent entry retrieval and summarization hook.
- Not persisted.

Long-term memory:

- ChromaDB-backed.
- Stores verified findings, source reputation records, report summaries, and notable failed strategies.
- Uses OpenAI embeddings.
- Supports semantic query with metadata filters.

Procedural memory:

- JSON-backed strategy registry at `memory/strategies.json`.
- Stores topic type, query templates, trusted source patterns, success rate, and notes.
- Updated after session evaluation.

## Observability

Memory operations emit:

- Operation name.
- Entry type.
- Query top-k.
- Result count.
- Latency.
- Storage errors.

## Error Handling

Memory failures should be recoverable unless startup initialization fails. If long-term memory is unavailable during research, agents continue with short-term state and record a `ResearchError`.

Procedural memory JSON corruption should preserve the corrupt file with a backup suffix and start with an empty registry.

## Testing

Tests should cover:

- Scratchpad bounds and retrieval.
- ChromaDB save/query using a temporary test directory.
- Metadata filtering.
- Source reputation save/update.
- Procedural strategy load/save/update.
- Corrupt procedural memory fallback.

## Acceptance Criteria

- Agents can save and query long-term memory.
- Procedural memory persists strategy outcomes.
- Memory tests do not depend on production runtime directories.
- Memory failures are represented as structured errors.
