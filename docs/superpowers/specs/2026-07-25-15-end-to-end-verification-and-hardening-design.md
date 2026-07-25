# End-To-End Verification And Hardening Design

Date: 2026-07-25
Status: Draft for review
Parent Spec: 2026-07-25-agentic-deep-research-design.md

## Purpose

Verify the complete deep research system and harden behavior around failures, limits, and user-facing workflows.

## Scope

This feature adds:

- End-to-end mocked graph tests.
- One documented real-provider smoke test.
- Error-path coverage across tools, agents, graph, CLI, API, and UI.
- Runtime limit checks.
- Documentation for setup and operation.

## Non-Goals

- No new major product features.
- No provider expansion.
- No production deployment.

## Design

Verification layers:

- Unit tests for isolated components.
- Integration tests for graph flow with mocked agents or mocked providers.
- Contract tests for tool and agent state shapes.
- API tests for session lifecycle.
- CLI tests for command behavior.
- Manual UI verification.
- Manual real-provider smoke test when API keys are available.

Hardening focus:

- Timeouts.
- Retry limits.
- Max ReAct iterations.
- Max macro iterations.
- Missing API keys.
- Rate limits.
- Partial report generation.
- Trace failure fallback.
- Memory unavailable fallback.

Documentation:

- Setup instructions.
- Required environment variables.
- Running CLI, API, and UI.
- Running tests.
- Reading LangSmith traces.
- Known limitations.

## Observability

End-to-end verification should confirm LangSmith captures:

- Session trace.
- Graph nodes.
- Agent spans.
- ReAct iterations.
- Tool calls.
- Token usage.
- Final route decision.

## Testing

Tests should cover:

- Successful mocked end-to-end run.
- Critic loop-back run.
- Max iteration stop.
- Search failure fallback.
- Memory failure fallback.
- LangSmith disabled mode.
- CLI and API behavior around failed sessions.

## Acceptance Criteria

- The full mocked test suite passes.
- A real-provider CLI smoke test is documented and can be run manually.
- README explains setup and operation.
- Known limitations are explicit.
- The system fails predictably when external services are unavailable.
