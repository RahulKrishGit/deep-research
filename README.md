# Deep Research

Multi-agent deep research system using LangGraph, OpenAI, ChromaDB, and LangSmith.

## Project Status

Foundation phase — package skeleton, typed configuration/state, the LangSmith observability foundation, OpenAI chat/embedding providers, core tools, and the three-layer memory stack.

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

   `load_config("config.yaml")` automatically loads the `.env` file beside
   `config.yaml`. Values already set by the shell, CI, a container, or the
   deployment platform take precedence over `.env`, so the same loader is safe
   for local development and deployed environments. Keep personal keys only in
   `.env`; it is ignored by Git.

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

See `config.yaml` for default non-secret settings. Copy `.env.example` to `.env`
and add personal API keys for local runs; `load_config("config.yaml")` loads that
sibling file automatically. Shell and CI environment variables take precedence.
Configuration overrides use uppercase full-path names, such as `LLM_MODEL` and
`MEMORY_LONG_TERM_PERSIST_DIRECTORY`. API keys remain environment-only and are
not included in typed settings or telemetry.

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

## OpenAI Providers

Set `OPENAI_API_KEY` in the process environment or the repository-root `.env`.
Chat and embedding defaults live under `llm` in `config.yaml`; `LLM_MODEL`,
`LLM_EMBEDDING_MODEL`, `LLM_TIMEOUT`, and `LLM_RETRY_COUNT` override the
corresponding YAML values.

Chat callers use project-owned messages and results, not OpenAI SDK types:

```python
from deep_research.providers import ChatMessage, OpenAIChatProvider

settings = load_config("config.yaml")
chat = OpenAIChatProvider(settings.llm, tracker)

async with tracker.session_span("session-123", "Why is the sky blue?"):
    result = await chat.complete(
        [ChatMessage(role="user", content="Why is the sky blue?")],
        agent_name="researcher",
    )
```

Use `complete_structured(messages, Schema)` for validated Pydantic output. The
provider performs one repair request if validation fails. `OpenAIEmbeddingProvider`
is the synchronous embedding client memory uses — see `embed_query(...)` and
`embed_documents(...)` below.

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

## Memory

Three layers, each independently usable:

- `ScratchpadMemory` — synchronous, bounded, per-agent and per-session. Never
  persisted. Optional summarization hook compacts the window instead of
  silently dropping the oldest notes.
- `LongTermMemory` — async, ChromaDB-backed semantic recall over verified
  findings, source reputations, report summaries, and notable failed
  strategies. Persists under `<memory.long_term.persist_directory>/chroma/`.
- `ProceduralMemory` — async, JSON-backed strategy registry at
  `memory/strategies.json`.

```python
from deep_research.memory import LongTermMemory, ProceduralMemory, ScratchpadMemory
from deep_research.providers import OpenAIEmbeddingProvider
from deep_research.utils.config import load_config
from deep_research.utils.types import merge_research_state

settings = load_config("config.yaml")

pad = ScratchpadMemory.from_config(
    settings.memory.short_term, session_id="session-123", agent_name="researcher"
)
pad.add("Tavily returned 5 results.", kind="observation")

long_term = LongTermMemory.from_config(
    settings.memory.long_term, embeddings=OpenAIEmbeddingProvider()
)
hits = await long_term.query("quantum error correction", top_k=5)

procedural = ProceduralMemory.from_config(settings.memory.procedural)
await procedural.load()
await procedural.record_session_outcome(
    topic_type="technology", succeeded=True, iterations=3
)

state = merge_research_state(state, {"errors": long_term.drain_errors()})
```

Memory failures are recoverable. A long-term write returns `False`, a query
returns `[]`, and each failure appends a recoverable `ResearchError` that
`drain_errors()` hands back for merging into `ResearchState.errors` — agents
continue with short-term state. Only startup problems raise
`MemoryInitializationError`. A corrupt `memory/strategies.json` is renamed to
`memory/strategies.json.corrupt-<timestamp>.bak` and the registry restarts
empty.

Long-term and procedural operations emit a `MemoryMetric` (operation, layer,
entry type, top-k, result count, latency, error type) whenever a tracker and an
active session span are available.

## Development

```bash
# Run tests
pytest

# Lint
ruff check src/
```

## Phases

- Phase 1: Core package foundation, config, types, providers
- Phase 2: Memory and tools ← current (complete)
- Phase 3: Agents and LangGraph orchestration
- Phase 4: CLI, API, and UI interfaces
- Phase 5: Tests and verification
