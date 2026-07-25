# CLI Interface Design

Date: 2026-07-25
Status: Draft for review
Parent Spec: 2026-07-25-agentic-deep-research-design.md

## Purpose

Provide the first user-facing way to run the research agent locally.

## Scope

This feature adds:

- `python -m deep_research` entry point.
- Positional research question argument.
- Interactive mode.
- Resume option.
- Progress output.
- Final report path display.

## Non-Goals

- No API server.
- No Streamlit UI.
- No rich terminal dependency unless needed.

## Design

Commands:

```bash
python -m deep_research "What are the security implications of quantum computing?"
python -m deep_research "AI in healthcare" --max-iterations 5 --output-format markdown --verbose
python -m deep_research --interactive
python -m deep_research --resume <session_id>
```

CLI options:

- `question`
- `--interactive`
- `--resume`
- `--max-iterations`
- `--output-format`
- `--config`
- `--verbose`

The CLI calls the shared `run_research()` function and subscribes to structured progress events.

## Observability

Verbose mode prints:

- Session ID.
- LangSmith trace URL when available.
- Current agent.
- Tool call summaries.
- Token totals when available.

## Error Handling

CLI exits with non-zero status for configuration failures and graph failures. Recoverable research errors are printed as warnings and included in the report limitations.

## Testing

Tests should cover:

- Argument parsing.
- Interactive input path.
- Successful mocked run.
- Configuration failure output.
- Resume argument handling.

## Acceptance Criteria

- A user can start a research run from the command line.
- Progress is visible without reading logs.
- Final report path and trace URL are printed when available.
- CLI behavior can be tested with mocked `run_research()`.
