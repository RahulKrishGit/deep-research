# LangSmith Observability Foundation Design

Date: 2026-07-25
Status: Draft for review
Parent Spec: 2026-07-25-agentic-deep-research-design.md

## Purpose

Introduce observability early so every later feature can emit consistent LangSmith traces, structured events, metrics, and errors from the beginning.

## Scope

This feature adds:

- LangSmith configuration loading.
- Trace context utilities.
- Standard span wrappers for sessions, agents, ReAct iterations, LLM calls, and tools.
- Metric collection models.
- A no-op fallback when tracing is disabled.

## Non-Goals

- No LangGraph graph.
- No real agent behavior.
- No post-session quality evaluator yet.
- No dashboard customization beyond trace metadata.

## Design

Create `src/deep_research/observability/` with:

- `tracker.py`: public tracking API.
- `context.py`: trace context and session metadata helpers.
- `metrics.py`: metric models for session, agent, tool, and token usage.

The tracker API should be usable before LangGraph exists:

```python
async with tracker.session_span(session_id, question):
    async with tracker.agent_span("planner"):
        async with tracker.tool_span("web_search", inputs):
            ...
```

The tracker should support two modes:

- Enabled: emits LangSmith traces.
- Disabled: records local structured events only.

Configuration:

- `LANGSMITH_API_KEY`
- `LANGSMITH_PROJECT`
- `LANGSMITH_TRACING=true|false`

Tracked metadata:

- `session_id`
- `agent_name`
- `tool_name`
- `iteration`
- `model`
- `token_usage`
- `latency_ms`
- `success`
- `error_type`
- `trace_url` when available

## Error Handling

LangSmith failures must not break research execution. If trace emission fails, the tracker records a `ResearchError` and continues in no-op mode for that operation.

## Testing

Tests should use mocked LangSmith clients and cover:

- Enabled tracing path.
- Disabled tracing path.
- Error fallback path.
- Metric serialization.
- Nested span context behavior.

## Acceptance Criteria

- Later features can instrument work without importing LangSmith directly.
- Tracing can be disabled for tests.
- LangSmith failures do not crash a research session.
- Session, agent, tool, and metric metadata have stable schemas.
