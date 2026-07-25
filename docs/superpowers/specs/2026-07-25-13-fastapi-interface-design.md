# FastAPI Interface Design

Date: 2026-07-25
Status: Draft for review
Parent Spec: 2026-07-25-agentic-deep-research-design.md

## Purpose

Expose the research engine over HTTP for programmatic use and for future UI integration.

## Scope

This feature adds:

- FastAPI app.
- Research session creation.
- Status endpoint.
- Progress stream endpoint.
- Report endpoint.
- Trace endpoint.

## Non-Goals

- No authentication.
- No multi-tenant authorization.
- No database-backed job queue.
- No deployment configuration.

## Design

Endpoints:

- `POST /research`
- `GET /research/{session_id}/status`
- `GET /research/{session_id}/stream`
- `GET /research/{session_id}/report`
- `GET /research/{session_id}/trace`

Request model:

- `query`
- `max_iterations`
- `output_format`
- optional config overrides

Session model:

- `session_id`
- `status`
- `current_agent`
- `iteration`
- `started_at`
- `finished_at`
- `report_path`
- `trace_url`
- `errors`

Use in-process background tasks for the first build.

## Observability

API requests include session ID and route metadata in trace context. API errors are logged as structured events without leaking secrets.

## Error Handling

Invalid requests return 422. Missing configuration returns 500 with a clear safe message. Unknown sessions return 404. Running sessions return status without blocking.

## Testing

Tests should cover:

- Start session.
- Get status.
- Stream progress with mocked events.
- Get report.
- Unknown session 404.
- Validation errors.

## Acceptance Criteria

- Programmatic clients can start and monitor research sessions.
- Reports can be retrieved by session ID.
- Trace metadata is available through the API.
- Tests do not require real OpenAI, Tavily, or LangSmith calls.
