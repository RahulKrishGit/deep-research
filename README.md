# Deep Research

Multi-agent deep research system using LangGraph, OpenAI, ChromaDB, and LangSmith.

## Project Status

Foundation phase — package skeleton, typed configuration/state, and the LangSmith observability foundation.

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

## Observability

Set `LANGSMITH_TRACING=false` to keep tracing fully local for tests and offline
development. Set it to `true` and provide non-empty `LANGSMITH_API_KEY` and
`LANGSMITH_PROJECT` values to mirror the same spans to LangSmith.

Application code imports only the project tracker:

```python
from deep_research.observability import Tracker
from deep_research.utils.config import load_config

settings = load_config("config.yaml")
tracker = Tracker.from_config(settings.langsmith)

async with tracker.session_span("session-123", "Why is the sky blue?"):
    async with tracker.agent_span("planner"):
        async with tracker.tool_span(
            "web_search",
            {"query": "Rayleigh scattering"},
        ):
            results = await search_client.search("Rayleigh scattering")
```

The documentation snippet uses a named `search_client` placeholder only as an example dependency; the observability implementation does not create that client.

Each completed span appends a typed metric and structured event. A LangSmith
transport failure appends a recoverable `ResearchError` and research work
continues locally.

## Core Tools

Core tools run inside an active tracker session. They return failures as a
`ToolResult` instead of raising operational errors, so callers can retain
partial research progress. Network clients and memory backends are injectable,
which keeps tool tests deterministic without live services. The later memory
stack implements the exported `LongTermMemory` protocol.

```python
import os

from deep_research.observability import Tracker
from deep_research.tools import WebSearchTool, WriteDocumentTool
from deep_research.utils.config import load_config

settings = load_config("config.yaml", strict=True)
tracker = Tracker.from_config(settings.langsmith)

async with tracker.session_span("session-123", "research question"):
    search = WebSearchTool(
        tracker,
        api_key=os.environ["TAVILY_API_KEY"],
        search_depth=settings.tavily.search_depth,
        max_results=settings.tavily.max_results,
    )
    search_result = await search.execute(query="research question")

    writer = WriteDocumentTool(tracker, settings.output.directory)
    report_result = await writer.execute(
        filename="session-report.md",
        content="# Research report\n\n...",
    )
```

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
