# Planner And Researcher Agents Design

Date: 2026-07-25
Status: Draft for review
Parent Spec: 2026-07-25-agentic-deep-research-design.md

## Purpose

Implement the first research-producing agents: Planner and Researcher.

## Scope

This feature adds:

- `PlannerAgent`
- `ResearcherAgent`
- Planner prompts and structured outputs
- Researcher prompts and ReAct behavior
- Planner and Researcher tests with mocked tools and OpenAI provider

## Non-Goals

- No source evaluation.
- No fact checking.
- No final synthesis.
- No LangGraph orchestration beyond direct agent calls in tests.

## Design

Planner:

- Reads `original_question` and `memory_context`.
- Optionally uses `query_memory` and lightweight `web_search`.
- Produces 3 to 7 `SubTopic` entries.
- Each sub-topic includes rationale, search queries, success criteria, and priority.

Researcher:

- Reads `sub_topics`, `raw_findings`, `critique`, and `memory_context`.
- Runs a bounded ReAct loop for each high-priority sub-topic.
- Uses web search, web scraping, document reading, memory query, and memory save tools.
- If Critic feedback exists, prioritizes recommended queries and missing areas.
- Writes `Finding` entries to state.

## Observability

Planner emits:

- Planning start/end.
- Memory recall count.
- Sub-topic count.

Researcher emits:

- Sub-topic start/end.
- Tool calls.
- Findings count.
- Stop reason per sub-topic.

## Error Handling

Planner fails the session if it cannot produce a valid plan after one repair attempt.

Researcher records recoverable tool failures and continues with other queries or sources. If no findings are produced for a high-priority sub-topic, it records a structured warning in `state.errors`.

## Testing

Tests should cover:

- Planner creates valid sub-topics.
- Planner rejects redundant or empty plans.
- Researcher creates findings from mocked search and scrape results.
- Researcher respects max iterations.
- Researcher responds to Critic feedback.
- Tool failures do not crash the agent when alternatives exist.

## Acceptance Criteria

- A question can be converted into a validated research plan.
- Researcher can produce source-backed findings for planned sub-topics.
- Both agents emit observability events.
- Both agents can be tested without live network or OpenAI calls.
