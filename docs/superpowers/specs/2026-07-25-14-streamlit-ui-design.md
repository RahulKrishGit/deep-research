# Streamlit UI Design

Date: 2026-07-25
Status: Draft for review
Parent Spec: 2026-07-25-agentic-deep-research-design.md

## Purpose

Provide a local web UI for starting research, watching progress, and reading reports.

## Scope

This feature adds:

- Streamlit app.
- Research input view.
- Live progress view.
- Report viewer.
- Session history view backed by local session metadata.

## Non-Goals

- No production frontend framework.
- No authentication.
- No collaborative multi-user UI.
- No advanced report editor.

## Design

The first screen is operational:

- Research question input.
- Max iterations control.
- Output format display.
- Start button.
- Current session status.

Progress view:

- Current agent.
- Macro iteration.
- Sub-topic progress.
- Tool call summaries.
- Token usage when available.
- LangSmith trace link when available.

Report view:

- Rendered Markdown report.
- Source credibility summary.
- Fact-check summary.
- Report file path.
- Session errors and limitations.

## Observability

The UI displays trace URLs but does not implement tracing itself. Research execution remains instrumented in the shared engine.

## Error Handling

Configuration errors are shown in the UI without exposing secrets. Failed sessions show error summaries and any partial report path.

## Testing

Testing should focus on:

- Pure helper functions.
- Session state transformations.
- API or runner adapter with mocked responses.

Manual verification should confirm the UI can start a mocked or real local session.

## Acceptance Criteria

- A user can start research from Streamlit.
- Progress updates are visible.
- Final report renders in the UI.
- LangSmith trace link is visible when available.
