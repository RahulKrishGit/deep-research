# Base Agent ReAct Runtime Design

Date: 2026-07-25
Status: Draft for review
Parent Spec: 2026-07-25-agentic-deep-research-design.md

## Purpose

Create the shared agent base class and bounded ReAct runtime used by iterative agents.

## Scope

This feature adds:

- `BaseAgent`
- Agent configuration models
- ReAct step models
- Generic ReAct loop helper
- Prompt rendering boundary
- Tool selection and execution path

## Non-Goals

- No concrete Planner, Researcher, evaluator, fact-checker, synthesizer, or critic behavior.
- No LangGraph graph.
- No UI or API behavior.

## Design

`BaseAgent` owns:

- Agent name.
- OpenAI provider.
- Tool registry.
- Scratchpad memory.
- Observability tracker.
- Agent-specific config.

The ReAct runtime provides:

```text
prepare context -> think -> choose action -> execute tool -> observe -> update state/scratchpad -> stop or continue
```

Stop conditions:

- Agent-specific sufficiency check passes.
- Max ReAct iterations reached.
- Tool budget exhausted.
- Non-recoverable configuration or provider error.

Concrete agents provide:

- Prompt templates.
- Allowed tools.
- State read/write behavior.
- Sufficiency criteria.
- Structured output schema.

## Observability

Each ReAct iteration emits:

- Agent name.
- Iteration number.
- Thought summary.
- Tool selected.
- Observation summary.
- Stop reason.
- Token and latency metrics.

## Error Handling

Tool failures are observations unless they prevent the agent from continuing. Provider failures may retry once according to provider config. Invalid agent output triggers one structured repair attempt.

## Testing

Tests should use fake providers and fake tools to cover:

- Successful one-step ReAct loop.
- Multi-step loop.
- Max iteration stop.
- Tool failure as observation.
- Invalid action handling.
- Scratchpad updates.
- Observability calls.

## Acceptance Criteria

- Concrete agents can be built by implementing narrow hooks.
- ReAct loops are bounded and testable.
- Agent runtime does not depend on LangGraph.
- Tool and provider failures have predictable behavior.
