# Deep Research

Multi-agent deep research system using LangGraph, OpenAI, ChromaDB, and LangSmith.

## Project Status

Foundation phase — package skeleton and configuration loading only.

## Setup

1. **Clone the repo**

   ```bash
   git clone <repo-url>
   cd deep-research
   ```

2. **Create a virtual environment**

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Linux/macOS
   .venv\Scripts\activate     # Windows
   ```

3. **Install dependencies**

   ```bash
   pip install -e ".[dev]"
   ```

4. **Configure environment**

   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   ```

   Copying `.env.example` creates a template only. The foundation does not load
   `.env` automatically, so export these values or inject them into the process
   environment before running the application.

5. **Verify setup**

   ```bash
   python -c "import deep_research; print(deep_research.__version__)"
   pytest
   ```

## Project Layout

```
src/deep_research/     # Package root
|-- main.py            # run_research() entry point
|-- graph/             # LangGraph orchestration (Phase 3)
|-- agents/            # Research agents (Phase 3)
|-- memory/            # Short-term, long-term, procedural memory (Phase 2)
|-- tools/             # Web search, scraping, etc. (Phase 2)
|-- providers/         # LLM providers (Phase 1)
|-- observability/     # LangSmith tracing (Phase 2)
|-- utils/             # Config, types, shared utilities
tests/                 # Test suite
config.yaml            # Default runtime configuration
memory/                # Long-term persistence (gitignored)
output/                # Generated reports (gitignored)
```

## Configuration

See `config.yaml` for default settings. Sensitive values are set via environment
variables (see `.env.example`). Configuration overrides use uppercase full-path
names, such as `LLM_MODEL` and `MEMORY_LONG_TERM_PERSIST_DIRECTORY`.

## Development

```bash
# Run tests
pytest

# Lint
ruff check src/
```

## Phases

- Phase 1: Core package foundation, config, types, providers ← current
- Phase 2: Memory and tools
- Phase 3: Agents and LangGraph orchestration
- Phase 4: CLI, API, and UI interfaces
- Phase 5: Tests and verification
