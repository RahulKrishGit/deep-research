# LangGraph Orchestration Design

Date: 2026-07-25
Status: Draft for review
Parent Spec: 2026-07-25-agentic-deep-research-design.md

## Purpose

Wire the implemented agents into a LangGraph state graph with bounded macro-level refinement cycles.

## Scope

This feature adds:

- `orchestrator.py`
- Graph construction function
- Node wrappers for each agent
- Conditional routing after Critic
- Checkpoint and resume hooks
- Graph-level tests with mocked agents

## Non-Goals

- No new agent behavior.
- No UI or API.
- No production distributed execution.

## Design

Graph order:

```text
Planner -> Researcher -> Source Evaluator -> Fact Checker -> Synthesizer -> Critic
```

Conditional route:

```text
Critic -> END when should_continue is false
Critic -> Researcher when should_continue is true and iteration < max_iterations
```

Graph responsibilities:

- Initialize `ResearchState`.
- Invoke each agent node.
- Apply state merge rules.
- Increment macro iteration when routing from Critic back to Researcher.
- Record graph-level events.
- Surface LangSmith trace metadata.
- Support resume by `session_id` when checkpointing is enabled.

## Observability

LangGraph node execution should be visible in LangSmith as separate spans. The orchestrator attaches session metadata, graph route decisions, and final status to the trace.

## Error Handling

The graph stops on non-recoverable configuration errors. Recoverable agent and tool errors remain in state. If an agent returns invalid state, the graph records an error and stops with failure status.

## Testing

Tests should cover:

- Happy path from Planner to END.
- Critic loop-back to Researcher.
- Max iteration force end.
- Agent failure handling.
- State merge behavior across nodes.
- Resume hook behavior with mocked checkpointing.

## Acceptance Criteria

- The full agent sequence runs through LangGraph.
- Critic can trigger at least one refinement loop.
- Iteration bounds prevent infinite loops.
- Graph route decisions are observable.
