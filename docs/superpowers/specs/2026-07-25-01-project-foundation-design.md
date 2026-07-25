# Project Foundation Design

Date: 2026-07-25
Status: Draft for review
Parent Spec: 2026-07-25-agentic-deep-research-design.md

## Purpose

Create the baseline Python project structure for the deep research agent so later features have a stable package, configuration, dependency, and test layout to build on.

## Scope

This feature creates only the non-agent foundation:

- `pyproject.toml`
- `.env.example`
- `config.yaml`
- `src/deep_research/` package skeleton
- `api/`, `ui/`, `tests/`, `output/`, and runtime `memory/` directory conventions
- README setup notes
- `.gitignore` updates for runtime artifacts

## Non-Goals

- No real agents.
- No LangGraph graph.
- No OpenAI calls.
- No LangSmith tracing logic.
- No working CLI beyond importable package entry points.

## Design

Use a standard `src` package layout:

```text
src/deep_research/
â”œâ”€â”€ __init__.py
â”œâ”€â”€ __main__.py
â”œâ”€â”€ main.py
â”œâ”€â”€ graph/
â”œâ”€â”€ agents/
â”œâ”€â”€ memory/
â”œâ”€â”€ tools/
â”œâ”€â”€ providers/
â”œâ”€â”€ observability/
â””â”€â”€ utils/
```

The package should expose a stub `run_research()` interface in `main.py` with the final expected shape documented, but not implemented beyond raising `NotImplementedError`.

`config.yaml` defines default runtime configuration keys without requiring API keys to be committed.

`.env.example` documents required secrets:

- `OPENAI_API_KEY`
- `LANGSMITH_API_KEY`
- `LANGSMITH_PROJECT`
- `TAVILY_API_KEY`

Runtime data directories are not committed:

- `memory/`
- `output/`

## Dependencies

Initial runtime dependencies:

- `pydantic`
- `pyyaml`

Initial development dependencies:

- `pytest`
- `ruff`

Later feature specs add LangGraph, LangSmith, OpenAI, ChromaDB, Tavily, scraping, API, and UI dependencies when they are needed.

## Error Handling

Configuration loading should distinguish:

- Missing config file.
- Invalid YAML.
- Missing required environment variables when strict validation is requested.

The foundation should not validate all provider secrets at import time. Validation happens when a runtime entry point is invoked.

## Testing

Tests should cover:

- Package imports.
- Config file loading.
- Default config values.
- Required environment validation in strict mode.

## Acceptance Criteria

- The package imports successfully.
- `pytest` can discover tests.
- Config loading works from `config.yaml`.
- `.env.example` lists all known first-build secrets.
- Runtime `memory/` and `output/` paths are ignored by git.

