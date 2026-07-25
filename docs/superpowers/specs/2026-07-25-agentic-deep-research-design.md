# Agentic Deep Research Design

Date: 2026-07-25
Status: Approved design
Repository: deep-research

## Summary

Build a Python-based multi-agent deep research system that accepts a user question, plans a research strategy, gathers and evaluates evidence, verifies claims, synthesizes a report, critiques the result, and refines the research through bounded ReAct cycles.

The initial implementation will use OpenAI models only to reduce complexity, while keeping narrow provider interfaces where they clarify boundaries. The orchestration layer will use LangGraph, observability will use LangSmith, long-term memory will use ChromaDB, and user access will be provided through a CLI, FastAPI API, and Streamlit web UI.

## Goals

- Support a granular multi-agent research pipeline with Planner, Researcher, Source Evaluator, Fact Checker, Synthesizer, and Critic agents.
- Support micro-level ReAct loops inside agents that need iterative tool use.
- Support a macro-level graph loop where the Critic can send the system back to research gaps before finalizing.
- Provide short-term, long-term, and procedural memory.
- Provide LangSmith tracing for graph execution, agent steps, tool calls, token usage, costs, errors, and quality metrics.
- Provide tools for web search, web scraping, document reading, memory read/write, and report writing.
- Keep the first build practical by implementing OpenAI models only.
- Expose the same research engine through CLI, HTTP API, and Streamlit UI.

## Non-Goals For The First Build

- Multi-provider LLM support beyond OpenAI adapters.
- Distributed agent services or external message queues.
- Production authentication, billing, or multi-tenant isolation.
- Advanced browser automation beyond the scraping needed for research pages.
- Autonomous learning that changes prompts without explicit persisted strategy updates.

## Architecture

The system uses a hybrid architecture:

- LangGraph owns orchestration, graph state, conditional routing, checkpointing, and LangSmith trace integration.
- Each agent is a standalone class with a clear interface, injected tools, an OpenAI-backed LLM provider, prompt configuration, and per-session scratchpad memory.
- Shared graph state carries the research question, plan, findings, source evaluations, verified claims, report, critique, iteration counts, and recalled memory context.
- Tool classes encapsulate external capabilities and are instrumented for LangSmith.
- Memory classes provide separate short-term, long-term, and procedural memory behavior.

High-level flow:

```text
User Interface
    |
    v
Research Orchestrator (LangGraph)
    |
    v
Planner -> Researcher -> Source Evaluator -> Fact Checker -> Synthesizer -> Critic
              ^                                                   |
              |                                                   |
              +---------------- if gaps or low score -------------+
```

The Critic is the macro-level ReAct decision point. If the report quality is acceptable, the graph ends. If the report has material gaps, unsupported claims, weak sources, or poor coverage, the graph cycles back to the Researcher with specific feedback.

## Shared Research State

The graph passes a structured `ResearchState` object between nodes. The implementation should use a typed model, either a `TypedDict` compatible with LangGraph or a Pydantic model converted at graph boundaries.

Required state fields:

- `session_id`: Stable identifier for the research session.
- `original_question`: User-provided research question.
- `sub_topics`: Planned sub-topics, research strategies, and success criteria.
- `raw_findings`: Findings collected from web search, scraping, and document reading.
- `evaluated_sources`: Sources scored for authority, recency, relevance, and corroboration.
- `verified_claims`: Claims extracted from findings with verification verdicts.
- `report`: Current synthesized report text.
- `critique`: Critic feedback, quality score, gap list, and routing decision.
- `iteration`: Current macro ReAct iteration.
- `max_iterations`: Maximum allowed macro refinement loops.
- `memory_context`: Findings, source reputations, and strategies recalled at session start.
- `events`: Structured progress events emitted by agents and tools.
- `errors`: Recoverable errors encountered during the session.

Core data types:

- `SubTopic`: title, rationale, search_queries, success_criteria, priority.
- `Finding`: content, source_url, source_title, extracted_at, confidence, related_sub_topic.
- `ScoredSource`: url, title, authority_score, recency_score, relevance_score, corroboration_score, overall_score, rationale.
- `Claim`: text, source_urls, verdict, confidence, evidence, contradictions.
- `Critique`: score, gaps, unsupported_claims, recommended_queries, should_continue, rationale.
- `MemorySnapshot`: similar_findings, known_source_reputations, suggested_strategies.

## Agents

All agents implement a common interface:

```python
class BaseAgent:
    name: str

    async def run(self, state: ResearchState) -> ResearchState:
        ...
```

Agents that require iterative tool use also implement an internal bounded ReAct loop:

```text
Think -> Act with tool -> Observe result -> Update scratchpad/state -> Decide whether to continue
```

Each ReAct loop must have explicit stop conditions:

- The agent's sufficiency criteria are met.
- The configured per-agent iteration limit is reached.
- A non-recoverable error occurs.
- The graph-level cancellation or timeout is reached.

### Planner

Purpose: Convert the original question into a structured research plan.

Tools:

- Query Memory
- Web Search for lightweight topic scoping

Behavior:

- Recall similar prior findings and procedural strategies.
- Identify 3 to 7 sub-topics that collectively answer the question.
- Generate search queries and success criteria for each sub-topic.
- Prioritize sub-topics by importance to the final answer.

Output:

- `state.sub_topics`
- Planning events in `state.events`

Sufficiency:

- The plan covers the original question without redundant sub-topics.

### Researcher

Purpose: Gather evidence for each sub-topic.

Tools:

- Web Search
- Web Scraper
- Document Reader
- Query Memory
- Save To Memory

Behavior:

- Run an internal ReAct loop for each sub-topic.
- Search, scrape, and read documents until success criteria are met or limits are reached.
- Use recalled memory to avoid redundant research.
- Save high-value findings to long-term memory with source metadata.
- When invoked after Critic feedback, focus on the requested gaps and recommended queries.

Output:

- `state.raw_findings`
- Updated memory entries
- Tool events

Sufficiency:

- Each high-priority sub-topic has enough relevant findings, or the agent has exhausted its configured attempts.

### Source Evaluator

Purpose: Score source quality before claims are trusted.

Tools:

- Query Memory for known source reputation
- Web Search for source metadata when needed
- Document Reader for source content checks

Behavior:

- Score each source on authority, recency, relevance, and corroboration.
- Reuse known source reputations from memory when available.
- Flag weak, stale, promotional, or unsupported sources.
- Persist source reputation updates to long-term memory.

Output:

- `state.evaluated_sources`

Sufficiency:

- Every source used by raw findings has an evaluation or an explicit low-confidence flag.

### Fact Checker

Purpose: Verify major factual claims before synthesis.

Tools:

- Web Search
- Web Scraper
- Document Reader
- Query Memory

Behavior:

- Extract major claims from raw findings.
- Cross-reference claims against multiple independent sources when possible.
- Mark claims as verified, unverified, contradicted, or insufficient evidence.
- Preserve evidence links for citations.

Output:

- `state.verified_claims`

Sufficiency:

- All major claims likely to appear in the report have verification verdicts.

### Synthesizer

Purpose: Produce the research report.

Tools:

- Write Document
- Save To Memory

Behavior:

- Convert verified claims and evaluated sources into a structured report.
- Include citations or source references for factual claims.
- Separate strong findings from uncertain or conflicting evidence.
- Include a source appendix with credibility notes.
- Save final high-confidence findings to long-term memory.

Output:

- `state.report`
- Generated report artifact under `output/`

Sufficiency:

- The report answers the original question with source-backed findings and clear uncertainty handling.

### Critic

Purpose: Decide whether the research is complete or should be refined.

Tools:

- Web Search for spot-checking suspected gaps
- Query Memory for quality comparisons with prior research

Behavior:

- Evaluate report completeness, accuracy, source diversity, balance, and citation coverage.
- Produce a quality score from 1 to 10.
- Identify missing sub-topics, unsupported claims, weak sources, and specific follow-up queries.
- Route the graph to either finish or return to Researcher.

Output:

- `state.critique`
- Routing decision

Sufficiency:

- Always sufficient as a routing node.

Routing rule:

- End when score is at least 7, no critical gaps remain, and no major unsupported claims are present.
- Continue when score is below 7, critical gaps exist, major unsupported claims exist, or source diversity is too weak.
- Force end when `state.iteration >= state.max_iterations`, with final report clearly noting remaining limitations.

## Memory Architecture

The system has three memory layers.

### Scratchpad Memory

Scope: Short-term per-agent memory for a single session.

Stores:

- Internal ReAct thoughts as structured summaries.
- Tool call observations.
- Partial findings and decisions.

Rules:

- Scratchpads are not persisted after the session.
- Scratchpads use a sliding window and summarization when they exceed limits.
- Scratchpad entries are included in LangSmith traces when safe and useful.

### Long-Term Memory

Scope: Cross-session semantic memory stored in ChromaDB.

Stores:

- Verified findings.
- Source reputation records.
- Final report summaries.
- Useful failed searches or avoided sources when they explain future behavior.

Each entry includes:

- Text content.
- Embedding.
- Source metadata.
- Confidence.
- Timestamp.
- Session ID.
- Agent ID.
- Finding type.

Default implementation:

- ChromaDB stored under root-level `memory/chroma/`.
- OpenAI embeddings.
- Root-level `memory/` is runtime data and should be gitignored.

### Procedural Memory

Scope: Strategy memory stored in `memory/strategies.json`.

Stores:

- Topic type.
- Effective query templates.
- Trusted source patterns.
- Average iteration count.
- Success rate.
- Notes on failed strategies.

Update rule:

- After each completed session, the system records which strategies were used and whether they contributed to final quality.
- Strategy updates are explicit structured writes, not automatic prompt mutation.

## Tool Architecture

All tools use a common async interface:

```python
class BaseTool:
    name: str
    description: str
    input_schema: dict
    output_schema: dict

    async def execute(self, **kwargs) -> ToolResult:
        ...
```

Required tools:

- `web_search`: Uses Tavily initially. Returns title, URL, snippet, rank, and provider score.
- `web_scrape`: Uses `httpx` and BeautifulSoup for ordinary pages, with Playwright available for JavaScript-heavy pages.
- `document_read`: Supports PDF, CSV, JSON, Markdown, and plain text from local paths or URLs.
- `save_to_memory`: Embeds and stores findings or source reputation records in ChromaDB.
- `query_memory`: Performs semantic search over long-term memory.
- `write_document`: Writes Markdown in the first build. HTML and PDF export are out of scope until the Markdown path is stable.

Tool requirements:

- Tools return structured results instead of raw strings.
- Tools capture latency, success/failure, retry count, and error details for observability.
- Network tools enforce timeouts, basic rate limits, and domain-level retry controls.
- Scraping respects robots.txt where practical and records when content cannot be accessed.
- Document reading chunks large documents and preserves page or row references when available.

## OpenAI Model Layer

The first build uses OpenAI only.

Required configuration:

- `OPENAI_API_KEY`
- `LANGSMITH_API_KEY`
- `LANGSMITH_PROJECT`
- `TAVILY_API_KEY`

Recommended default model mapping:

- Planner: strong reasoning OpenAI chat model.
- Researcher: fast tool-capable OpenAI chat model.
- Source Evaluator: strong reasoning OpenAI chat model.
- Fact Checker: strong reasoning OpenAI chat model.
- Synthesizer: strong writing OpenAI chat model.
- Critic: strong reasoning OpenAI chat model.

The code may define a narrow `LLMProvider` interface if it reduces coupling, but only `OpenAIProvider` is implemented in the first build.

The code may define a narrow `EmbeddingProvider` interface if it reduces coupling, but only OpenAI embeddings are implemented in the first build.

## LangSmith Observability

LangSmith is required for observability.

Trace levels:

- Session trace for the entire research run.
- Graph node spans for each LangGraph node.
- Agent ReAct iteration spans.
- LLM spans for prompt, response, token usage, and model.
- Tool spans for inputs, outputs, latency, errors, and retries.

Tracked metrics:

- Total duration.
- Total macro iterations.
- Per-agent ReAct step counts.
- Tool call counts by tool.
- Token usage by agent and model.
- Source count and average source score.
- Claim verification counts by verdict.
- Critic score per iteration.
- Final report path.
- LangSmith trace URL.

Post-session evaluation:

- Citation coverage.
- Source diversity.
- Claim verification rate.
- Topic coverage against the plan.
- Improvement per critique cycle.
- Remaining limitations when max iterations are reached.

These evaluations should feed procedural memory in a structured way.

## User Interfaces

All interfaces call the same core `run_research()` function.

### CLI

Commands:

```bash
python -m deep_research "What are the security implications of quantum computing?"
python -m deep_research "AI in healthcare" --max-iterations 5 --output-format markdown --verbose
python -m deep_research --interactive
python -m deep_research --resume <session_id>
```

CLI behavior:

- Print high-level progress events.
- Show current agent, sub-topic progress, macro iteration count, and final report path.
- In verbose mode, show tool calls and LangSmith trace URL.

### FastAPI API

Required endpoints:

- `POST /research`: Start a research session.
- `GET /research/{session_id}/status`: Return current status.
- `GET /research/{session_id}/stream`: Stream progress events with server-sent events.
- `GET /research/{session_id}/report`: Return the final report.
- `GET /research/{session_id}/trace`: Return LangSmith trace URL and execution summary.

### Streamlit UI

Required views:

- Research input form with question, max iterations, output format, and start button.
- Live progress dashboard with current agent, sub-topic progress, tool call log, token count, and macro iteration count.
- Report viewer with rendered Markdown, source credibility scores, fact-check summary, export controls, session history, and LangSmith trace link.

The UI should be operational rather than promotional. The first screen should let the user start and monitor research directly.

## Project Structure

Proposed repository layout:

```text
deep-research/
|-- config.yaml
|-- pyproject.toml
|-- .env.example
|-- README.md
|-- src/
|   `-- deep_research/
|       |-- __init__.py
|       |-- __main__.py
|       |-- main.py
|       |-- graph/
|       |   |-- __init__.py
|       |   |-- state.py
|       |   `-- orchestrator.py
|       |-- agents/
|       |   |-- __init__.py
|       |   |-- base.py
|       |   |-- planner.py
|       |   |-- researcher.py
|       |   |-- source_evaluator.py
|       |   |-- fact_checker.py
|       |   |-- synthesizer.py
|       |   `-- critic.py
|       |-- memory/
|       |   |-- __init__.py
|       |   |-- scratchpad.py
|       |   |-- long_term.py
|       |   `-- procedural.py
|       |-- tools/
|       |   |-- __init__.py
|       |   |-- base.py
|       |   |-- web_search.py
|       |   |-- web_scraper.py
|       |   |-- document_reader.py
|       |   |-- memory_tools.py
|       |   `-- write_document.py
|       |-- providers/
|       |   |-- __init__.py
|       |   `-- openai_provider.py
|       |-- observability/
|       |   |-- __init__.py
|       |   |-- tracker.py
|       |   `-- evaluator.py
|       `-- utils/
|           |-- __init__.py
|           |-- config.py
|           `-- types.py
|-- api/
|   |-- __init__.py
|   `-- server.py
|-- ui/
|   |-- app.py
|   `-- components/
|-- memory/
|-- output/
`-- tests/
    |-- test_agents/
    |-- test_tools/
    |-- test_memory/
    `-- test_graph/`n```

Runtime directories `memory/` and `output/` should be gitignored.

## Dependencies

Initial dependency set:

```toml
dependencies = [
    "langgraph",
    "langchain-openai",
    "langsmith",
    "chromadb",
    "tavily-python",
    "playwright",
    "beautifulsoup4",
    "pdfplumber",
    "fastapi",
    "uvicorn",
    "streamlit",
    "pydantic",
    "pyyaml",
    "httpx",
]
```

Test and development dependencies:

```toml
optional-dependencies.dev = [
    "pytest",
    "pytest-asyncio",
    "ruff",
]
```

## Error Handling

Expected recoverable errors:

- Search provider timeout or rate limit.
- Scraper cannot access a page.
- Document extraction fails for a specific file.
- LLM structured output fails validation.
- ChromaDB read or write failure.
- LangSmith tracing failure.

Handling rules:

- Recoverable tool failures are recorded in `state.errors` and LangSmith, then the agent continues when another source or strategy is available.
- Structured output validation failures trigger one repair attempt with a stricter prompt.
- Graph execution stops only for non-recoverable configuration errors, repeated critical infrastructure failures, or explicit cancellation.
- If max iterations are reached, the system returns the best available report with a limitations section.
- Missing API keys fail fast at startup with a clear configuration error.

## Testing Strategy

Unit tests:

- State model validation.
- Agent stop conditions and routing decisions.
- Tool result schema validation.
- Memory save/query behavior with a local test ChromaDB directory.
- Config loading and required environment validation.

Integration tests:

- LangGraph route from Planner through Critic using mocked tools and mocked OpenAI responses.
- Critic loop-back behavior when quality score is low.
- Forced ending when max iterations are reached.
- API session lifecycle with mocked research execution.

Contract tests:

- Tool inputs and outputs remain stable for agent use.
- Agent `.run()` methods preserve required state fields.
- Report includes citations for verified claims.

Manual verification:

- Run one CLI research session with real API keys.
- Confirm LangSmith trace includes graph nodes, tool calls, and token usage.
- Confirm Streamlit UI shows progress and final report.

## Implementation Phasing

Phase 1: Core package foundation

- Add project metadata, config loading, shared types, and state models.
- Implement OpenAI provider and LangSmith setup.

Phase 2: Memory and tools

- Implement scratchpad, ChromaDB long-term memory, procedural memory, web search, scraping, document reading, and report writing.

Phase 3: Agents and graph

- Implement the six agents and LangGraph orchestration with Critic loop-back routing.

Phase 4: Interfaces

- Add CLI, FastAPI API, and Streamlit UI around the same research runner.

Phase 5: Tests and verification

- Add unit tests, integration tests with mocked providers, and manual verification documentation.

## Acceptance Criteria

- A user can run a research query from the CLI and receive a Markdown report.
- The graph uses Planner, Researcher, Source Evaluator, Fact Checker, Synthesizer, and Critic nodes.
- At least Researcher and Fact Checker use bounded internal ReAct loops.
- The Critic can route the graph back to Researcher and can end the graph when quality criteria are met.
- The system stores and retrieves long-term memory with ChromaDB.
- The system records procedural strategy outcomes.
- LangSmith shows a session trace with graph nodes, LLM calls, tool calls, token usage, and key metrics.
- FastAPI exposes session start, status, stream, report, and trace endpoints.
- Streamlit provides research input, live progress, and report viewing.
- Tests cover state validation, routing, memory behavior, tool contracts, and mocked end-to-end graph execution.

