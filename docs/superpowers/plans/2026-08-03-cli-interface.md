# CLI Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `python -m deep_research "<question>"` run a real research session end-to-end by wiring `run_research()` to the existing LangGraph orchestrator, and print progress, warnings, the final report path, and the LangSmith trace URL.

**Architecture:** A new `deep_research.runtime` sub-package owns everything between loaded configuration and a compiled graph: the long-term-memory-to-tool protocol bridge, session-start memory recall, the provider/tool/agent/graph assembly, and the run outcome. `main.py` becomes a thin `run_research()` that loads config, builds a runtime, drives `run_research_graph()` / `resume_research_graph()`, and returns a `ResearchOutcome`. `cli.py` is stdlib `argparse` plus `print`: it parses arguments, calls `run_research()`, renders the recorded event log as a progress summary, and maps outcomes onto exit codes. Nothing in `deep_research.graph` changes.

**Tech Stack:** Python 3.11+, stdlib `argparse` / `asyncio` / `uuid`, Pydantic v2, LangGraph (already wired), pytest + pytest-asyncio. No new dependencies.

## Global Constraints

- No new third-party dependency. `argparse` and `print` only — no `click`, `typer`, or `rich` (spec 12 Non-Goals: "No rich terminal dependency unless needed").
- No FastAPI and no Streamlit (spec 12 Non-Goals). Those are specs 13 and 14.
- Only `markdown` is a valid `--output-format`. The parent spec pins `write_document`: "Writes Markdown in the first build. HTML and PDF export are out of scope until the Markdown path is stable."
- Do not implement durable or cross-process checkpointing. `build_checkpointer` returns an `InMemorySaver`; that is the whole of what this build supports.
- Do not change the signature or behaviour of anything in `src/deep_research/graph/`. It is finished and covered by `tests/test_graph/`.
- `load_config(path, strict=True)` is mandatory at CLI startup. Parent spec Error Handling: "Missing API keys fail fast at startup with a clear configuration error."
- No provider text, no `str(exception)`, and no report bodies in `ResearchEvent.metadata` or `ResearchError.details`. Record enumerated reasons, counts, and identifiers. This is enforced by convention throughout `graph/errors.py`, `graph/events.py`, and `agents/errors.py`; new code follows it.
- Python 3.11 union syntax (`X | None`), `from __future__ import annotations` at the top of every new module, and `ruff` lint rules `E`, `F`, `I` (import sorting) must pass.
- Tests are `pytest` with explicit `@pytest.mark.asyncio` markers. `asyncio_mode` is **not** set to `auto` in `pyproject.toml`; an async test without the marker silently does not run.

---

## File Structure

**New sub-package `src/deep_research/runtime/`** — everything that stands between a loaded `ConfigSettings` and a running graph. It is a sub-package rather than one flat `assembly.py` for two reasons: the repo's established pattern is one sub-package per responsibility with an `__init__.py` re-export surface (`graph/`, `agents/`, `memory/`, `tools/`, `providers/`, `observability/`), and the four concerns below are independently reviewable — a reviewer can reject the memory bridge while accepting the outcome model.

| File | Responsibility |
| --- | --- |
| `src/deep_research/runtime/__init__.py` | Public re-export surface for the sub-package. |
| `src/deep_research/runtime/errors.py` | `ResearchConfigurationError` and the enumerated hints attached to it. Mirrors `graph/errors.py`. |
| `src/deep_research/runtime/memory_bridge.py` | `LongTermMemoryBridge`: adapts `memory.LongTermMemory` (entry-typed) onto the `save(content, metadata)` / `query(query, top_k, filters)` protocol `SaveToMemoryTool` and `QueryMemoryTool` require. **This adapter does not exist today and nothing works without it** — see "Gap found during exploration" below. |
| `src/deep_research/runtime/recall.py` | `recall_memory_context()`: builds the `MemorySnapshot` the graph refuses to build for itself (`graph/state.py`: "The graph performs no recall of its own"). |
| `src/deep_research/runtime/assembly.py` | `build_tools`, `build_agents`, `build_runtime`, `ResearchRuntime`. The wiring root. |
| `src/deep_research/runtime/outcome.py` | `ResearchOutcome`, `ToolCallSummary`, and the pure functions that derive the report path, tool-call summary, and token totals from a finished run. |

**Modified at the package root:**

| File | Change |
| --- | --- |
| `src/deep_research/main.py` | Replace the `NotImplementedError` stub with the real `run_research()` / `run_research_sync()` and output-format resolution. |
| `src/deep_research/cli.py` | New. Argument parsing, rendering, exit codes. Sits next to `main.py` as the parent spec's Project Structure implies (root-level `__main__.py` and `main.py`, no `cli/` package). |
| `src/deep_research/__main__.py` | Replace the stub with a two-line delegation to `cli.main()`. |
| `README.md` | New "## Command Line Interface" section; phase list updated. |

**Tests** follow the existing per-module layout (`tests/test_graph/`, `tests/test_agents/`, `tests/test_tools/` are packages; single-module areas like `tests/test_config.py` are flat). Two new packages:

| File | Covers |
| --- | --- |
| `tests/test_runtime/__init__.py` | package marker |
| `tests/test_runtime/conftest.py` | shared offline `tracker` fixture (copied shape from `tests/test_graph/conftest.py`) |
| `tests/test_runtime/test_errors.py` | Task 1 |
| `tests/test_runtime/test_memory_bridge.py` | Task 2 |
| `tests/test_runtime/test_recall.py` | Task 3 |
| `tests/test_runtime/test_outcome.py` | Task 4 |
| `tests/test_runtime/test_assembly.py` | Tasks 5, 6 |
| `tests/test_runtime/test_run_research.py` | Task 7 |
| `tests/test_cli/__init__.py` | package marker |
| `tests/test_cli/test_arguments.py` | Task 8 |
| `tests/test_cli/test_render.py` | Task 9 |
| `tests/test_cli/test_entrypoint.py` | Task 10 |

Existing shared fakes are reused, not duplicated: `tests/memory_fakes.py` (`FakeEmbeddings`, `FakeCollection`), `tests/graph_fakes.py` (`FakeAgent`, `fake_research_agents`, `fake_critique`), `tests/research_fakes.py` (`FakeSearchClient`, `FakeMemory`).

---

## Gap found during exploration (read this before Task 2)

`deep_research.tools.memory_tools` declares its own structural protocol:

```python
class LongTermMemory(Protocol):
    async def save(self, content: str, metadata: Mapping[str, JsonValue]) -> str: ...
    async def query(
        self, query: str, *, top_k: int = 5,
        filters: Mapping[str, JsonValue] | None = None,
    ) -> Sequence[Mapping[str, JsonValue]]: ...
```

`deep_research.memory.long_term.LongTermMemory` does **not** satisfy it:

```python
async def save(self, entry: MemoryEntry) -> bool: ...
async def query(self, text: str, *, top_k: int = DEFAULT_TOP_K,
                entry_type: MemoryEntryType | None = None,
                where: Mapping[str, Any] | None = None) -> list[MemoryQueryResult]: ...
```

Every test in the repo passes `tests/research_fakes.py::FakeMemory` to the memory tools, so this mismatch has never been exercised. Passing the real memory class straight to `SaveToMemoryTool` would fail at the first tool call with a `TypeError` surfaced as a failed `ToolResult` — silently degrading every agent's memory access instead of crashing. Task 2 closes the gap with an explicit adapter. Do **not** "fix" it by changing either protocol: `memory_tools`' protocol is the tool-facing JSON contract and `memory.LongTermMemory`'s is the typed storage contract, and both are covered by existing tests.

The same is true of `SourceEvaluatorAgent`'s `reputation` kwarg, but in the other direction: its `ReputationSource` protocol is `async def get_source_reputation(self, url) -> SourceReputation | None`, which `memory.LongTermMemory` **does** satisfy exactly. Pass the memory object itself for `reputation`, not the bridge.

---

## Design decisions locked in by this plan

1. **`run_research()` returns `ResearchOutcome`, not `GraphRun` and not `dict`.** `GraphRun` already carries `session_id`, `state`, `status`, `trace_url`; `ResearchOutcome` wraps it and adds the two things every front-end needs and neither the graph nor the state records in a reachable place: `report_path` (only present in the `synthesizer.synthesis.completed` event's `output_path` metadata) and `token_usage` (only derivable from `Tracker.metrics`). Deriving those once, here, keeps the CLI, and later the API and UI, from each re-implementing the same event archaeology.
2. **Resume across processes does not work, and the CLI says so.** `build_checkpointer` returns `InMemorySaver`, which dies with the process. `--resume` therefore builds a fresh graph, calls `resume_research_graph`, and gets `GraphResumeError` — either "resuming a session requires a graph compiled with a checkpointer" (when `checkpointing_enabled` is false) or "no checkpoint was recorded for session X" (when it is true). Both are converted into a `ResearchConfigurationError` carrying the enumerated `no_checkpoint` hint and exit 1. Durable checkpointing is explicitly out of scope for spec 12.
3. **Progress is a post-run log, not a live stream.** `run_research_graph` calls `graph.ainvoke(...)` and returns one `GraphRun`; there is no callback, generator, or `astream` hook anywhere in the orchestrator. Spec 12's acceptance criterion is "Progress is visible without reading logs", which the recorded `state.events` list satisfies. Building a streaming architecture would mean changing `graph/orchestrator.py`, which this plan is forbidden from doing and which spec 13's SSE endpoint is the right place for. The CLI prints "Researching..." before invoking so the user knows work is happening.
4. **`--interactive` prompts once, runs once, exits.** Spec 12's Testing section says "Interactive input path", singular. No REPL.
5. **`--output-format` accepts only `markdown`.** Validated in `main.py` (not via argparse `choices`) so that the API and UI get the same validation and the same error message, and so the failure exits 1 like every other configuration failure rather than argparse's 2.
6. **Verbose output reads `Tracker.metrics`, not events, for tool and token summaries.** `ToolMetric` and `TokenUsageMetric` are typed records the tracker already accumulates for every span. Event-log archaeology would be less precise and would double-count.
7. **Exit codes:** `0` success (including `max_iterations` and `incomplete` — a report still exists), `1` configuration failure, `2` argparse usage error, `3` graph failure (`status == "failed"`), `130` keyboard interrupt. Unexpected exceptions are deliberately **not** caught: an unhandled exception is a defect, not a research outcome, exactly as `graph/nodes.py` documents.

---

## Task 1: Runtime configuration errors

**Files:**
- Create: `src/deep_research/runtime/__init__.py`
- Create: `src/deep_research/runtime/errors.py`
- Create: `tests/test_runtime/__init__.py`
- Create: `tests/test_runtime/conftest.py`
- Test: `tests/test_runtime/test_errors.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `CONFIGURATION_HINTS: dict[str, str]`
  - `class ResearchConfigurationError(Exception)` with attributes `reason: str` and `hint: str`
  - `def configuration_error(*, reason: str, message: str) -> ResearchConfigurationError`

- [ ] **Step 1: Write the failing test**

Create `tests/test_runtime/__init__.py` as an empty file.

Create `tests/test_runtime/conftest.py`:

```python
import pytest

from deep_research.observability import LangSmithRuntimeConfig, Tracker


@pytest.fixture
def tracker() -> Tracker:
    return Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False,
            project="runtime-tests",
            api_key=None,
        )
    )
```

Create `tests/test_runtime/test_errors.py`:

```python
"""Tests for the enumerated runtime configuration failure type."""

from __future__ import annotations

import pytest

from deep_research.runtime.errors import (
    CONFIGURATION_HINTS,
    ResearchConfigurationError,
    configuration_error,
)


def test_configuration_error_carries_its_enumerated_hint() -> None:
    error = configuration_error(
        reason="missing_secrets",
        message="Missing required environment variables: OPENAI_API_KEY",
    )

    assert isinstance(error, ResearchConfigurationError)
    assert error.reason == "missing_secrets"
    assert error.hint == CONFIGURATION_HINTS["missing_secrets"]
    assert "OPENAI_API_KEY" in str(error)


def test_an_unenumerated_reason_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown configuration reason"):
        configuration_error(reason="something_new", message="nope")


def test_every_hint_is_a_non_blank_sentence() -> None:
    assert CONFIGURATION_HINTS
    for reason, hint in CONFIGURATION_HINTS.items():
        assert reason.strip() == reason
        assert hint.strip()
        assert hint.endswith(".")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runtime/test_errors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_research.runtime'`

- [ ] **Step 3: Write minimal implementation**

Create `src/deep_research/runtime/errors.py`:

```python
"""The one failure type the CLI, the API, and the UI all render.

The runtime mirror of ``graph.errors``. Every reason a research run can
refuse to start is enumerated here with the corrective hint that goes with
it, so a front-end never has to invent advice and a message never has to
carry a stack trace.
"""

from __future__ import annotations

# Enumerated, project-generated hints. Never provider text and never
# ``str(exception)`` beyond the message the caller passes deliberately.
CONFIGURATION_HINTS = {
    "config_file_missing": (
        "Pass --config with the path to a config.yaml file."
    ),
    "config_invalid": (
        "Check config.yaml against the settings documented in README.md."
    ),
    "missing_secrets": (
        "Set OPENAI_API_KEY and TAVILY_API_KEY in the environment or in a "
        ".env file next to config.yaml."
    ),
    "provider_unconfigured": (
        "Set OPENAI_API_KEY in the environment or in a .env file next to "
        "config.yaml."
    ),
    "memory_unavailable": (
        "Check that the memory directory is writable and that chromadb is "
        "installed."
    ),
    "agents_misconfigured": (
        "This is a wiring defect rather than a setup problem; report it "
        "with the command that produced it."
    ),
    "unsupported_output_format": (
        "Only markdown is supported in this build."
    ),
    "no_question": (
        "Pass a research question, or use --interactive."
    ),
    "no_checkpoint": (
        "Resume only works inside the process that started the session; "
        "in-memory checkpoints do not survive a new command."
    ),
}


class ResearchConfigurationError(Exception):
    """A research run could not start. Never raised once the graph runs."""

    def __init__(self, message: str, *, reason: str, hint: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.hint = hint


def configuration_error(
    *,
    reason: str,
    message: str,
) -> ResearchConfigurationError:
    """Build one enumerated configuration failure.

    The hint comes from the enumeration, so a failure the project never
    named cannot be raised and no caller can invent its own advice.
    """
    hint = CONFIGURATION_HINTS.get(reason)
    if hint is None:
        raise ValueError(f"unknown configuration reason: {reason}")
    return ResearchConfigurationError(message, reason=reason, hint=hint)
```

Create `src/deep_research/runtime/__init__.py`:

```python
"""Assembly of a runnable research session from loaded configuration."""

from deep_research.runtime.errors import (
    CONFIGURATION_HINTS,
    ResearchConfigurationError,
    configuration_error,
)

__all__ = [
    "CONFIGURATION_HINTS",
    "ResearchConfigurationError",
    "configuration_error",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_runtime/test_errors.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/runtime tests/test_runtime
git commit -m "feat: add enumerated runtime configuration errors"
```

---

## Task 2: Long-term memory tool bridge

**Files:**
- Create: `src/deep_research/runtime/memory_bridge.py`
- Modify: `src/deep_research/runtime/__init__.py`
- Test: `tests/test_runtime/test_memory_bridge.py`

**Interfaces:**
- Consumes: `deep_research.memory.LongTermMemory`, `deep_research.memory.entries.MemoryEntry`.
- Produces:
  - `DEFAULT_BRIDGE_ENTRY_TYPE: str` (`"finding"`)
  - `DEFAULT_BRIDGE_AGENT_ID: str` (`"research_agent"`)
  - `class LongTermMemoryBridge` with `__init__(self, memory: LongTermMemory, *, session_id: str)`, `async def save(self, content: str, metadata: Mapping[str, JsonValue]) -> str`, `async def query(self, query: str, *, top_k: int = 5, filters: Mapping[str, JsonValue] | None = None) -> list[dict[str, JsonValue]]`.

Structural typing only: this class deliberately imports nothing from `deep_research.tools`, so the memory layer keeps no dependency on the tool layer. It satisfies `tools.memory_tools.LongTermMemory` by shape.

- [ ] **Step 1: Write the failing test**

Create `tests/test_runtime/test_memory_bridge.py`:

```python
"""Tests for the long-term-memory-to-tool-protocol bridge."""

from __future__ import annotations

import pytest

from deep_research.memory.entries import MemoryEntry
from deep_research.memory.long_term import LongTermMemory
from deep_research.runtime.memory_bridge import (
    DEFAULT_BRIDGE_AGENT_ID,
    DEFAULT_BRIDGE_ENTRY_TYPE,
    LongTermMemoryBridge,
)
from deep_research.tools.memory_tools import QueryMemoryTool, SaveToMemoryTool
from tests.memory_fakes import FakeCollection, FakeEmbeddings


def build_memory() -> tuple[LongTermMemory, FakeCollection]:
    collection = FakeCollection()
    return (
        LongTermMemory(collection=collection, embeddings=FakeEmbeddings()),
        collection,
    )


@pytest.mark.asyncio
async def test_save_stores_a_finding_and_returns_its_entry_id() -> None:
    memory, collection = build_memory()
    bridge = LongTermMemoryBridge(memory, session_id="session-1")

    entry_id = await bridge.save(
        "Break-even was reached in 2025.",
        {
            "entry_type": "finding",
            "agent_id": "synthesizer",
            "confidence": 0.9,
            "source_url": "https://example.org/a",
            "verdict": "verified",
        },
    )

    assert entry_id in collection.records
    stored = collection.records[entry_id]
    assert stored["document"] == "Break-even was reached in 2025."
    assert stored["metadata"]["session_id"] == "session-1"
    assert stored["metadata"]["agent_id"] == "synthesizer"
    assert stored["metadata"]["verdict"] == "verified"


@pytest.mark.asyncio
async def test_save_fills_in_the_defaults_a_model_forgot() -> None:
    memory, collection = build_memory()
    bridge = LongTermMemoryBridge(memory, session_id="session-1")

    entry_id = await bridge.save("A bare finding.", {})

    metadata = collection.records[entry_id]["metadata"]
    assert metadata["entry_type"] == DEFAULT_BRIDGE_ENTRY_TYPE
    assert metadata["agent_id"] == DEFAULT_BRIDGE_AGENT_ID
    assert metadata["session_id"] == "session-1"


@pytest.mark.asyncio
async def test_save_drops_metadata_a_vector_store_cannot_hold() -> None:
    memory, collection = build_memory()
    bridge = LongTermMemoryBridge(memory, session_id="session-1")

    entry_id = await bridge.save(
        "A finding.",
        {"nested": {"a": 1}, "listed": [1, 2], "missing": None, "kept": "yes"},
    )

    metadata = collection.records[entry_id]["metadata"]
    assert metadata["kept"] == "yes"
    assert "nested" not in metadata
    assert "listed" not in metadata
    assert "missing" not in metadata


@pytest.mark.asyncio
async def test_save_raises_when_the_backend_rejects_the_write() -> None:
    memory, collection = build_memory()
    collection.fail_on.add("upsert")
    bridge = LongTermMemoryBridge(memory, session_id="session-1")

    with pytest.raises(RuntimeError, match="long-term memory"):
        await bridge.save("A finding.", {})


@pytest.mark.asyncio
async def test_query_returns_json_safe_mappings() -> None:
    memory, _ = build_memory()
    bridge = LongTermMemoryBridge(memory, session_id="session-1")
    await memory.save(
        MemoryEntry(
            entry_type="finding",
            content="Break-even was reached in 2025.",
            session_id="session-0",
            agent_id="researcher",
            confidence=0.8,
            source_url="https://example.org/a",
            source_title="QEC 2025",
            attributes={"related_sub_topic": "Error correction"},
        )
    )

    matches = await bridge.query("break-even", top_k=3)

    assert len(matches) == 1
    match = matches[0]
    assert match["content"] == "Break-even was reached in 2025."
    assert match["entry_type"] == "finding"
    assert match["source_url"] == "https://example.org/a"
    assert match["source_title"] == "QEC 2025"
    assert match["agent_id"] == "researcher"
    assert match["attributes"] == {"related_sub_topic": "Error correction"}
    assert 0.0 <= float(match["relevance"]) <= 1.0


@pytest.mark.asyncio
async def test_query_filters_by_entry_type() -> None:
    memory, _ = build_memory()
    bridge = LongTermMemoryBridge(memory, session_id="session-1")
    for entry_type, content in (
        ("finding", "A finding about error correction."),
        ("report_summary", "A summary about error correction."),
    ):
        await memory.save(
            MemoryEntry(
                entry_type=entry_type,
                content=content,
                session_id="session-0",
                agent_id="researcher",
            )
        )

    matches = await bridge.query(
        "error correction", top_k=5, filters={"entry_type": "report_summary"}
    )

    assert [match["entry_type"] for match in matches] == ["report_summary"]


@pytest.mark.asyncio
async def test_the_bridge_satisfies_the_memory_tools(tracker) -> None:
    """The bridge is accepted by the real tools, not just by a protocol."""
    memory, _ = build_memory()
    bridge = LongTermMemoryBridge(memory, session_id="session-1")

    async with tracker.session_span("session-1", "a question"):
        saved = await SaveToMemoryTool(tracker, bridge).execute(
            content="Break-even was reached in 2025.",
            metadata={"entry_type": "finding", "agent_id": "researcher"},
        )
        queried = await QueryMemoryTool(tracker, bridge).execute(
            query="break-even", top_k=3
        )

    assert saved.success, saved.error
    assert queried.success, queried.error
    assert queried.data["matches"][0]["content"] == (
        "Break-even was reached in 2025."
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runtime/test_memory_bridge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_research.runtime.memory_bridge'`

- [ ] **Step 3: Write minimal implementation**

Create `src/deep_research/runtime/memory_bridge.py`:

```python
"""Adapt typed long-term memory onto the tools' JSON memory protocol.

``tools.memory_tools`` declares a structural ``LongTermMemory`` protocol —
``save(content, metadata) -> entry_id`` and ``query(query, top_k, filters)``
— because a tool's arguments come from a model and are plain JSON. The
storage layer declares a typed one: ``save(MemoryEntry) -> bool`` and
``query(text, entry_type=..., where=...) -> list[MemoryQueryResult]``.
Neither is wrong and neither should bend, so the translation lives here.

Nothing from ``deep_research.tools`` is imported: the protocol is
structural, and keeping the import out means the memory layer never grows a
dependency on the tool layer.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from pydantic import JsonValue, ValidationError

from deep_research.memory.entries import MemoryEntry
from deep_research.memory.long_term import LongTermMemory

# What a tool-driven save means when the model did not say. A tool call
# that reaches this bridge is an agent keeping a finding for later; nothing
# else is written through a tool.
DEFAULT_BRIDGE_ENTRY_TYPE = "finding"
DEFAULT_BRIDGE_AGENT_ID = "research_agent"

# Metadata keys that map onto ``MemoryEntry`` fields rather than onto its
# free-form ``attributes``. Mirrors ``entries._RESERVED_METADATA_KEYS``.
_ENTRY_FIELD_KEYS = (
    "entry_type",
    "session_id",
    "agent_id",
    "confidence",
    "source_url",
    "source_title",
    "timestamp",
)


def _scalar_attributes(metadata: Mapping[str, JsonValue]) -> dict[str, Any]:
    """Keep only the flat scalars a vector store can hold as metadata."""
    return {
        key: value
        for key, value in metadata.items()
        if key not in _ENTRY_FIELD_KEYS
        and isinstance(value, (str, int, float, bool))
    }


class LongTermMemoryBridge:
    """The tool-facing view of one session's long-term memory."""

    def __init__(self, memory: LongTermMemory, *, session_id: str) -> None:
        if not session_id.strip():
            raise ValueError("session_id must not be blank")
        self._memory = memory
        self._session_id = session_id.strip()

    async def save(
        self,
        content: str,
        metadata: Mapping[str, JsonValue],
    ) -> str:
        """Store one tool-supplied finding and return its entry id.

        Raises rather than returning a sentinel: ``BaseTool.execute`` turns
        any exception into a failed ``ToolResult``, which is what an agent
        must see when a write did not land.
        """
        payload: dict[str, Any] = {
            "entry_id": uuid4().hex,
            "content": content,
            "entry_type": metadata.get("entry_type", DEFAULT_BRIDGE_ENTRY_TYPE),
            "session_id": metadata.get("session_id", self._session_id),
            "agent_id": metadata.get("agent_id", DEFAULT_BRIDGE_AGENT_ID),
            "attributes": _scalar_attributes(metadata),
        }
        for key in ("confidence", "source_url", "source_title", "timestamp"):
            value = metadata.get(key)
            if value is not None:
                payload[key] = value

        try:
            entry = MemoryEntry.model_validate(payload)
        except ValidationError as error:
            raise ValueError(
                "the finding could not be stored in long-term memory "
                "because its metadata was rejected"
            ) from error

        if not await self._memory.save(entry):
            raise RuntimeError(
                "long-term memory is unavailable, so the finding was not "
                "stored"
            )
        return entry.entry_id

    async def query(
        self,
        query: str,
        *,
        top_k: int = 5,
        filters: Mapping[str, JsonValue] | None = None,
    ) -> list[dict[str, JsonValue]]:
        """Search long-term memory and render hits as JSON mappings."""
        where = dict(filters or {})
        entry_type = where.pop("entry_type", None)
        results = await self._memory.query(
            query,
            top_k=top_k,
            entry_type=entry_type if isinstance(entry_type, str) else None,
            where=where or None,
        )
        return [
            {
                "content": result.entry.content,
                "entry_type": result.entry.entry_type,
                "session_id": result.entry.session_id,
                "agent_id": result.entry.agent_id,
                "confidence": result.entry.confidence,
                "source_url": result.entry.source_url,
                "source_title": result.entry.source_title,
                "timestamp": result.entry.timestamp,
                "relevance": round(result.relevance, 4),
                "attributes": dict(result.entry.attributes),
            }
            for result in results
        ]
```

Extend `src/deep_research/runtime/__init__.py` — add to the imports and to `__all__`:

```python
from deep_research.runtime.memory_bridge import (
    DEFAULT_BRIDGE_AGENT_ID,
    DEFAULT_BRIDGE_ENTRY_TYPE,
    LongTermMemoryBridge,
)
```

`__all__` gains `"DEFAULT_BRIDGE_AGENT_ID"`, `"DEFAULT_BRIDGE_ENTRY_TYPE"`, `"LongTermMemoryBridge"` (keep the list sorted: uppercase constants first, then classes, then functions — the convention every other `__init__.py` in this repo follows).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_runtime/test_memory_bridge.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/runtime tests/test_runtime
git commit -m "feat: bridge typed long-term memory onto the tool memory protocol"
```

---

## Task 3: Session-start memory recall

**Files:**
- Create: `src/deep_research/runtime/recall.py`
- Modify: `src/deep_research/runtime/__init__.py`
- Test: `tests/test_runtime/test_recall.py`

**Interfaces:**
- Consumes: `deep_research.memory.LongTermMemory`, `deep_research.memory.ProceduralMemory`, `deep_research.utils.types.MemorySnapshot`.
- Produces:
  - `RECALLED_SUB_TOPIC: str`
  - `DEFAULT_RECALL_TOP_K: int` (`5`)
  - `MAX_SUGGESTED_STRATEGIES: int` (`10`)
  - `async def recall_memory_context(*, question: str, long_term: LongTermMemory | None, procedural: ProceduralMemory | None = None, top_k: int = DEFAULT_RECALL_TOP_K) -> MemorySnapshot`

Why this exists: `graph/state.py::initial_graph_state` documents that "``memory_context`` is supplied by the caller. The graph performs no recall of its own." Without this function the Planner's `memory_recalled_event` always reports zeros and the parent spec's "Recall similar prior findings and procedural strategies" is never satisfied.

- [ ] **Step 1: Write the failing test**

Create `tests/test_runtime/test_recall.py`:

```python
"""Tests for the memory snapshot one research session starts from."""

from __future__ import annotations

import pytest

from deep_research.memory.entries import MemoryEntry, SourceReputation
from deep_research.memory.long_term import LongTermMemory
from deep_research.memory.procedural import ProceduralMemory
from deep_research.runtime.recall import (
    RECALLED_SUB_TOPIC,
    recall_memory_context,
)
from deep_research.utils.types import MemorySnapshot
from tests.memory_fakes import FakeCollection, FakeEmbeddings

QUESTION = "How mature is quantum error correction?"


def build_memory() -> LongTermMemory:
    return LongTermMemory(collection=FakeCollection(), embeddings=FakeEmbeddings())


@pytest.mark.asyncio
async def test_recall_returns_an_empty_snapshot_without_memory() -> None:
    snapshot = await recall_memory_context(question=QUESTION, long_term=None)

    assert snapshot == MemorySnapshot()


@pytest.mark.asyncio
async def test_recall_turns_stored_findings_into_findings() -> None:
    memory = build_memory()
    await memory.save(
        MemoryEntry(
            entry_type="finding",
            content="Break-even was reached in 2025.",
            session_id="session-0",
            agent_id="researcher",
            confidence=0.8,
            source_url="https://example.org/a",
            source_title="QEC 2025",
            attributes={"related_sub_topic": "Error correction"},
        )
    )

    snapshot = await recall_memory_context(question=QUESTION, long_term=memory)

    assert len(snapshot.similar_findings) == 1
    finding = snapshot.similar_findings[0]
    assert finding.content == "Break-even was reached in 2025."
    assert finding.source_url == "https://example.org/a"
    assert finding.related_sub_topic == "Error correction"
    assert finding.confidence == 0.8


@pytest.mark.asyncio
async def test_a_finding_without_a_sub_topic_gets_the_recall_placeholder() -> None:
    memory = build_memory()
    await memory.save(
        MemoryEntry(
            entry_type="finding",
            content="Break-even was reached in 2025.",
            session_id="session-0",
            agent_id="researcher",
            source_url="https://example.org/a",
            source_title="QEC 2025",
        )
    )

    snapshot = await recall_memory_context(question=QUESTION, long_term=memory)

    assert snapshot.similar_findings[0].related_sub_topic == RECALLED_SUB_TOPIC


@pytest.mark.asyncio
async def test_a_finding_with_no_source_is_skipped_rather_than_faked() -> None:
    memory = build_memory()
    await memory.save(
        MemoryEntry(
            entry_type="finding",
            content="An unattributed note.",
            session_id="session-0",
            agent_id="researcher",
        )
    )

    snapshot = await recall_memory_context(question=QUESTION, long_term=memory)

    assert snapshot.similar_findings == []


@pytest.mark.asyncio
async def test_recall_collects_reputations_for_the_recalled_sources() -> None:
    memory = build_memory()
    await memory.save(
        MemoryEntry(
            entry_type="finding",
            content="Break-even was reached in 2025.",
            session_id="session-0",
            agent_id="researcher",
            source_url="https://example.org/a",
            source_title="QEC 2025",
        )
    )
    await memory.save(
        SourceReputation(
            url="https://example.org/a",
            title="QEC 2025",
            reputation_score=0.75,
        ).to_entry(session_id="session-0", agent_id="source_evaluator")
    )

    snapshot = await recall_memory_context(question=QUESTION, long_term=memory)

    assert snapshot.known_source_reputations == {"https://example.org/a": 0.75}


@pytest.mark.asyncio
async def test_recall_reads_query_templates_out_of_procedural_memory(
    tmp_path,
) -> None:
    procedural = ProceduralMemory(tmp_path / "strategies.json")
    await procedural.load()
    await procedural.record_session_outcome(
        topic_type="technology",
        succeeded=True,
        iterations=2,
        query_templates=["{topic} 2025 benchmark", "{topic} limitations"],
    )

    snapshot = await recall_memory_context(
        question=QUESTION, long_term=None, procedural=procedural
    )

    assert snapshot.suggested_strategies == [
        "{topic} 2025 benchmark",
        "{topic} limitations",
    ]


@pytest.mark.asyncio
async def test_recall_survives_a_dead_backend() -> None:
    collection = FakeCollection()
    collection.fail_on.add("query")
    memory = LongTermMemory(collection=collection, embeddings=FakeEmbeddings())

    snapshot = await recall_memory_context(question=QUESTION, long_term=memory)

    assert snapshot == MemorySnapshot()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runtime/test_recall.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_research.runtime.recall'`

- [ ] **Step 3: Write minimal implementation**

Create `src/deep_research/runtime/recall.py`:

```python
"""Build the memory snapshot one research session starts from.

The graph deliberately performs no recall — that touches ChromaDB and an
embedding provider, which orchestration has no business owning — so the
caller supplies ``ResearchState.memory_context``. This is that caller's
half of the contract.

Every failure here is silent by design: ``LongTermMemory`` already records
its own recoverable errors and returns empty results, and a session that
cannot remember anything is a worse session, not a failed one.
"""

from __future__ import annotations

from deep_research.memory.long_term import LongTermMemory
from deep_research.memory.procedural import ProceduralMemory
from deep_research.utils.types import Finding, MemorySnapshot

# What a recalled finding is filed under when the entry that produced it
# never recorded a sub-topic. ``Finding.related_sub_topic`` is a required
# non-blank string and inventing a plausible topic would be a lie.
RECALLED_SUB_TOPIC = "recalled from long-term memory"

DEFAULT_RECALL_TOP_K = 5
MAX_SUGGESTED_STRATEGIES = 10


def _recalled_finding(entry) -> Finding | None:  # noqa: ANN001 - MemoryEntry
    """Render one stored entry as a ``Finding``, or drop it.

    An entry with no source is dropped rather than given a placeholder URL:
    findings carry citations, and a citation nobody can follow is worse
    than one fewer recalled finding.
    """
    if entry.source_url is None:
        return None
    sub_topic = entry.attributes.get("related_sub_topic")
    try:
        return Finding(
            content=entry.content,
            source_url=entry.source_url,
            source_title=entry.source_title or entry.source_url,
            extracted_at=entry.timestamp,
            confidence=entry.confidence,
            related_sub_topic=(
                sub_topic if isinstance(sub_topic, str) and sub_topic.strip()
                else RECALLED_SUB_TOPIC
            ),
        )
    except ValueError:
        return None


async def recall_memory_context(
    *,
    question: str,
    long_term: LongTermMemory | None,
    procedural: ProceduralMemory | None = None,
    top_k: int = DEFAULT_RECALL_TOP_K,
) -> MemorySnapshot:
    """Recall prior findings, source reputations, and strategies."""
    findings: list[Finding] = []
    reputations: dict[str, float] = {}

    if long_term is not None:
        results = await long_term.query(
            question, top_k=top_k, entry_type="finding"
        )
        for result in results:
            finding = _recalled_finding(result.entry)
            if finding is not None:
                findings.append(finding)

        for url in dict.fromkeys(finding.source_url for finding in findings):
            reputation = await long_term.get_source_reputation(url)
            if reputation is not None:
                reputations[url] = reputation.reputation_score

    strategies: list[str] = []
    if procedural is not None:
        for record in procedural.strategies:
            for template in record.query_templates:
                if template not in strategies:
                    strategies.append(template)
                if len(strategies) >= MAX_SUGGESTED_STRATEGIES:
                    break
            if len(strategies) >= MAX_SUGGESTED_STRATEGIES:
                break

    return MemorySnapshot(
        similar_findings=findings,
        known_source_reputations=reputations,
        suggested_strategies=strategies,
    )
```

Extend `src/deep_research/runtime/__init__.py` with `DEFAULT_RECALL_TOP_K`, `MAX_SUGGESTED_STRATEGIES`, `RECALLED_SUB_TOPIC`, and `recall_memory_context`, keeping `__all__` sorted.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_runtime/test_recall.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/runtime tests/test_runtime
git commit -m "feat: recall the memory snapshot a research session starts from"
```

---

## Task 4: Research outcome and run summaries

**Files:**
- Create: `src/deep_research/runtime/outcome.py`
- Modify: `src/deep_research/runtime/__init__.py`
- Test: `tests/test_runtime/test_outcome.py`

**Interfaces:**
- Consumes: `deep_research.graph.GraphRun`, `deep_research.observability.MetricRecord` / `ToolMetric` / `TokenUsageMetric` / `TokenUsage`, `deep_research.utils.types.ResearchState`.
- Produces:
  - `REPORT_WRITTEN_EVENT: str` (`"synthesizer.synthesis.completed"`)
  - `@dataclass(frozen=True) class ToolCallSummary` with `tool_name: str`, `calls: int`, `failures: int`
  - `def report_path_from_state(state: ResearchState) -> str | None`
  - `def tool_call_summaries(metrics: Sequence[MetricRecord]) -> list[ToolCallSummary]`
  - `def total_token_usage(metrics: Sequence[MetricRecord]) -> TokenUsage`
  - `@dataclass(frozen=True) class ResearchOutcome` with fields `session_id: str`, `question: str`, `status: str`, `state: ResearchState`, `trace_url: str | None`, `report_path: str | None`, `token_usage: TokenUsage`, `tool_calls: tuple[ToolCallSummary, ...]`, and properties `report: str | None`, `errors: tuple[ResearchError, ...]`, `failed: bool`
  - `def build_outcome(run: GraphRun, *, metrics: Sequence[MetricRecord]) -> ResearchOutcome`

- [ ] **Step 1: Write the failing test**

Create `tests/test_runtime/test_outcome.py`:

```python
"""Tests for the finished-run summary every front-end reads."""

from __future__ import annotations

from deep_research.agents.events import agent_event
from deep_research.graph.orchestrator import GraphRun
from deep_research.observability import TokenUsageMetric, ToolMetric
from deep_research.runtime.outcome import (
    ResearchOutcome,
    ToolCallSummary,
    build_outcome,
    report_path_from_state,
    tool_call_summaries,
    total_token_usage,
)
from deep_research.utils.types import ResearchError, ResearchState

QUESTION = "How mature is quantum error correction?"


def base_state(**overrides: object) -> ResearchState:
    payload: dict[str, object] = {
        "session_id": "session-1",
        "original_question": QUESTION,
    }
    payload.update(overrides)
    return ResearchState.model_validate(payload)


def synthesis_event(path: str | None) -> object:
    return agent_event(
        agent_name="synthesizer",
        event_type="synthesizer.synthesis.completed",
        message="Report synthesis complete.",
        metadata={"output_path": path, "section_count": 3},
    )


def test_report_path_reads_the_last_synthesis_event() -> None:
    state = base_state(
        events=[
            synthesis_event("report-session-1-0.md"),
            synthesis_event("report-session-1-1.md"),
        ]
    )

    assert report_path_from_state(state) == "report-session-1-1.md"


def test_report_path_is_none_when_no_report_was_written() -> None:
    assert report_path_from_state(base_state()) is None
    assert report_path_from_state(base_state(events=[synthesis_event(None)])) is None


def test_tool_call_summaries_group_by_tool_and_count_failures() -> None:
    metrics = [
        ToolMetric(
            session_id="session-1",
            tool_name="web_search",
            latency_ms=1.0,
            success=True,
        ),
        ToolMetric(
            session_id="session-1",
            tool_name="web_search",
            latency_ms=1.0,
            success=False,
            error_type="ProviderTimeoutError",
        ),
        ToolMetric(
            session_id="session-1",
            tool_name="query_memory",
            latency_ms=1.0,
            success=True,
        ),
    ]

    assert tool_call_summaries(metrics) == [
        ToolCallSummary(tool_name="query_memory", calls=1, failures=0),
        ToolCallSummary(tool_name="web_search", calls=2, failures=1),
    ]


def test_total_token_usage_sums_every_llm_span() -> None:
    metrics = [
        TokenUsageMetric(
            session_id="session-1",
            model="gpt-4o",
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            latency_ms=1.0,
            success=True,
        ),
        TokenUsageMetric(
            session_id="session-1",
            model="gpt-4o",
            input_tokens=5,
            output_tokens=1,
            total_tokens=6,
            latency_ms=1.0,
            success=True,
        ),
    ]

    usage = total_token_usage(metrics)

    assert usage.input_tokens == 105
    assert usage.output_tokens == 21
    assert usage.total_tokens == 126


def test_total_token_usage_is_zero_when_nothing_reported() -> None:
    assert total_token_usage([]).total_tokens == 0


def test_build_outcome_carries_everything_a_front_end_needs() -> None:
    state = base_state(
        report="# Research report",
        events=[synthesis_event("report-session-1-0.md")],
        errors=[
            ResearchError(
                error_type="web_search_failed",
                source="tools.web_search",
                message="The search provider timed out.",
            )
        ],
    )
    run = GraphRun(
        session_id="session-1",
        state=state,
        status="completed",
        trace_url="https://smith.example/run/1",
    )

    outcome = build_outcome(run, metrics=[])

    assert isinstance(outcome, ResearchOutcome)
    assert outcome.session_id == "session-1"
    assert outcome.question == QUESTION
    assert outcome.status == "completed"
    assert outcome.trace_url == "https://smith.example/run/1"
    assert outcome.report_path == "report-session-1-0.md"
    assert outcome.report == "# Research report"
    assert len(outcome.errors) == 1
    assert outcome.failed is False


def test_a_failed_run_is_reported_as_failed() -> None:
    run = GraphRun(
        session_id="session-1",
        state=base_state(),
        status="failed",
        trace_url=None,
    )

    assert build_outcome(run, metrics=[]).failed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runtime/test_outcome.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_research.runtime.outcome'`

- [ ] **Step 3: Write minimal implementation**

Create `src/deep_research/runtime/outcome.py`:

```python
"""What one finished research session produced, in one object.

``GraphRun`` already carries the session id, the state, the status, and the
trace URL. Two things every front-end needs are recorded somewhere less
convenient: the report's path lives only in the Synthesizer's completion
event, and token totals live only in the tracker's metric records. Deriving
both once here keeps the CLI, the API, and the UI from re-implementing the
same archaeology three times.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from deep_research.graph.orchestrator import GraphRun
from deep_research.observability import (
    MetricRecord,
    TokenUsage,
    TokenUsageMetric,
    ToolMetric,
)
from deep_research.utils.types import ResearchError, ResearchState

# The only event that records where a report was written. Emitted by
# ``agents.synthesizer.synthesis_completed_event``.
REPORT_WRITTEN_EVENT = "synthesizer.synthesis.completed"


@dataclass(frozen=True, slots=True)
class ToolCallSummary:
    """How often one tool was called during a session, and how often it failed."""

    tool_name: str
    calls: int
    failures: int


def report_path_from_state(state: ResearchState) -> str | None:
    """The path of the most recently written report, if one was written.

    The *last* synthesis event wins: a refinement pass rewrites the report
    under a new filename, and the newest file is the one that matches
    ``state.report``.
    """
    path: str | None = None
    for event in state.events:
        if event.event_type != REPORT_WRITTEN_EVENT:
            continue
        candidate = event.metadata.get("output_path")
        if isinstance(candidate, str) and candidate:
            path = candidate
    return path


def tool_call_summaries(
    metrics: Sequence[MetricRecord],
) -> list[ToolCallSummary]:
    """Group the run's tool spans by tool name, alphabetically."""
    counts: dict[str, list[int]] = {}
    for metric in metrics:
        if not isinstance(metric, ToolMetric):
            continue
        entry = counts.setdefault(metric.tool_name, [0, 0])
        entry[0] += 1
        if not metric.success:
            entry[1] += 1
    return [
        ToolCallSummary(tool_name=name, calls=calls, failures=failures)
        for name, (calls, failures) in sorted(counts.items())
    ]


def total_token_usage(metrics: Sequence[MetricRecord]) -> TokenUsage:
    """Sum every LLM span's token usage.

    Zero totals mean "no provider reported usage", which is exactly what a
    fully mocked run produces — callers render that as "not available"
    rather than as "zero tokens".
    """
    input_tokens = 0
    output_tokens = 0
    for metric in metrics:
        if isinstance(metric, TokenUsageMetric):
            input_tokens += metric.input_tokens
            output_tokens += metric.output_tokens
    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)


@dataclass(frozen=True, slots=True)
class ResearchOutcome:
    """Everything one research session produced, ready to render."""

    session_id: str
    question: str
    status: str
    state: ResearchState
    trace_url: str | None
    report_path: str | None
    token_usage: TokenUsage
    tool_calls: tuple[ToolCallSummary, ...]

    @property
    def report(self) -> str | None:
        """The report Markdown, authoritative whether or not it was written."""
        return self.state.report

    @property
    def errors(self) -> tuple[ResearchError, ...]:
        """Recoverable errors recorded during the session."""
        return tuple(self.state.errors)

    @property
    def failed(self) -> bool:
        """True when the graph halted on a non-recoverable failure."""
        return self.status == "failed"


def build_outcome(
    run: GraphRun,
    *,
    metrics: Sequence[MetricRecord],
) -> ResearchOutcome:
    """Fold one graph run and the tracker's metrics into an outcome."""
    return ResearchOutcome(
        session_id=run.session_id,
        question=run.state.original_question,
        status=run.status,
        state=run.state,
        trace_url=run.trace_url,
        report_path=report_path_from_state(run.state),
        token_usage=total_token_usage(metrics),
        tool_calls=tuple(tool_call_summaries(metrics)),
    )
```

Extend `src/deep_research/runtime/__init__.py` with `REPORT_WRITTEN_EVENT`, `ResearchOutcome`, `ToolCallSummary`, `build_outcome`, `report_path_from_state`, `tool_call_summaries`, `total_token_usage`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_runtime/test_outcome.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/runtime tests/test_runtime
git commit -m "feat: summarize a finished research run as a ResearchOutcome"
```

---

## Task 5: Tool construction from configuration

**Files:**
- Create: `src/deep_research/runtime/assembly.py`
- Modify: `src/deep_research/runtime/__init__.py`
- Test: `tests/test_runtime/test_assembly.py`

**Interfaces:**
- Consumes: `deep_research.utils.config.ConfigSettings`, `deep_research.observability.Tracker`, `LongTermMemoryBridge` (Task 2), `deep_research.tools.*`.
- Produces:
  - `def build_tools(settings: ConfigSettings, *, tracker: Tracker, memory: LongTermMemoryBridge, tavily_api_key: str | None = None, search_client: Any | None = None, http_client: Any | None = None) -> list[BaseTool]`

Every agent receives the whole tool list. `AgentToolset` selects only the names in the agent's `allowed_tools` and ignores the rest, so there is no per-agent tool subsetting to maintain — but it raises `AgentConfigurationError` if a declared tool is *missing*, which is exactly the wiring guard we want.

- [ ] **Step 1: Write the failing test**

Create `tests/test_runtime/test_assembly.py`:

```python
"""Tests for assembling a runnable research session from configuration."""

from __future__ import annotations

import pytest

from deep_research.memory.long_term import LongTermMemory
from deep_research.runtime.assembly import build_tools
from deep_research.runtime.memory_bridge import LongTermMemoryBridge
from deep_research.utils.config import ConfigSettings
from tests.memory_fakes import FakeCollection, FakeEmbeddings
from tests.research_fakes import FakeSearchClient, search_response

EXPECTED_TOOL_NAMES = {
    "web_search",
    "web_scraper",
    "document_reader",
    "query_memory",
    "save_to_memory",
    "write_document",
}


def build_bridge() -> LongTermMemoryBridge:
    memory = LongTermMemory(
        collection=FakeCollection(), embeddings=FakeEmbeddings()
    )
    return LongTermMemoryBridge(memory, session_id="session-1")


def test_build_tools_covers_every_tool_the_agents_declare(tracker) -> None:
    tools = build_tools(
        ConfigSettings(),
        tracker=tracker,
        memory=build_bridge(),
        search_client=FakeSearchClient(),
    )

    assert {tool.name for tool in tools} == EXPECTED_TOOL_NAMES


def test_build_tools_covers_the_union_of_every_agent_allowlist(tracker) -> None:
    """No agent may declare a tool this assembly does not build."""
    from deep_research.agents import (
        CriticAgent,
        FactCheckerAgent,
        PlannerAgent,
        ResearcherAgent,
        SourceEvaluatorAgent,
        SynthesizerAgent,
    )

    declared = {
        name
        for agent in (
            PlannerAgent,
            ResearcherAgent,
            SourceEvaluatorAgent,
            FactCheckerAgent,
            SynthesizerAgent,
            CriticAgent,
        )
        for name in agent.allowed_tools
    }
    tools = build_tools(
        ConfigSettings(),
        tracker=tracker,
        memory=build_bridge(),
        search_client=FakeSearchClient(),
    )

    assert declared <= {tool.name for tool in tools}


@pytest.mark.asyncio
async def test_build_tools_applies_the_tavily_settings(tracker) -> None:
    settings = ConfigSettings.model_validate(
        {"tavily": {"search_depth": "advanced", "max_results": 9}}
    )
    client = FakeSearchClient(responses=[search_response()])

    tools = build_tools(
        settings,
        tracker=tracker,
        memory=build_bridge(),
        search_client=client,
    )
    search = next(tool for tool in tools if tool.name == "web_search")
    async with tracker.session_span("session-1", "a question"):
        result = await search.execute(query="quantum error correction")

    assert result.success, result.error
    assert client.calls == [
        {
            "query": "quantum error correction",
            "search_depth": "advanced",
            "max_results": 9,
        }
    ]


@pytest.mark.asyncio
async def test_build_tools_writes_reports_under_the_configured_directory(
    tracker, tmp_path
) -> None:
    settings = ConfigSettings.model_validate(
        {"output": {"directory": str(tmp_path)}}
    )

    tools = build_tools(
        settings,
        tracker=tracker,
        memory=build_bridge(),
        search_client=FakeSearchClient(),
    )
    writer = next(tool for tool in tools if tool.name == "write_document")
    async with tracker.session_span("session-1", "a question"):
        result = await writer.execute(filename="a-report.md", content="# Hi")

    assert result.success, result.error
    assert (tmp_path / "a-report.md").read_text(encoding="utf-8") == "# Hi"


@pytest.mark.asyncio
async def test_the_memory_tools_are_wired_to_the_bridge(tracker) -> None:
    bridge = build_bridge()
    tools = build_tools(
        ConfigSettings(),
        tracker=tracker,
        memory=bridge,
        search_client=FakeSearchClient(),
    )
    save = next(tool for tool in tools if tool.name == "save_to_memory")

    async with tracker.session_span("session-1", "a question"):
        result = await save.execute(
            content="Break-even was reached in 2025.",
            metadata={"agent_id": "researcher"},
        )

    assert result.success, result.error
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runtime/test_assembly.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_research.runtime.assembly'`

- [ ] **Step 3: Write minimal implementation**

Create `src/deep_research/runtime/assembly.py` (Task 6 adds to the same file):

```python
"""Assemble the providers, tools, agents, and graph one session runs on.

The wiring root. Every collaborator a research session needs is constructed
here from a loaded ``ConfigSettings``, and every external client is
injectable so this module can be tested without an API key or a network.
"""

from __future__ import annotations

import os
from typing import Any

from deep_research.observability import Tracker
from deep_research.runtime.memory_bridge import LongTermMemoryBridge
from deep_research.tools.base import BaseTool
from deep_research.tools.document_reader import DocumentReaderTool
from deep_research.tools.memory_tools import QueryMemoryTool, SaveToMemoryTool
from deep_research.tools.web_scraper import WebScraperTool
from deep_research.tools.web_search import WebSearchTool
from deep_research.tools.write_document import WriteDocumentTool
from deep_research.utils.config import ConfigSettings

TAVILY_API_KEY_VARIABLE = "TAVILY_API_KEY"


def build_tools(
    settings: ConfigSettings,
    *,
    tracker: Tracker,
    memory: LongTermMemoryBridge,
    tavily_api_key: str | None = None,
    search_client: Any | None = None,
    http_client: Any | None = None,
) -> list[BaseTool]:
    """Build every tool any agent declares, in one shared registry.

    One registry for all six agents rather than a per-agent subset:
    ``AgentToolset`` already selects the names an agent declares and
    ignores the rest, and it raises ``AgentConfigurationError`` when a
    declared tool was never injected — so the wiring guard is kept without
    six lists to keep in step.
    """
    return [
        WebSearchTool(
            tracker,
            api_key=tavily_api_key or os.getenv(TAVILY_API_KEY_VARIABLE),
            client=search_client,
            search_depth=settings.tavily.search_depth,
            max_results=settings.tavily.max_results,
        ),
        WebScraperTool(tracker, client=http_client),
        DocumentReaderTool(tracker, client=http_client),
        QueryMemoryTool(tracker, memory),
        SaveToMemoryTool(tracker, memory),
        WriteDocumentTool(tracker, settings.output.directory),
    ]
```

Extend `src/deep_research/runtime/__init__.py` with `TAVILY_API_KEY_VARIABLE` and `build_tools`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_runtime/test_assembly.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/runtime tests/test_runtime
git commit -m "feat: build the research tool registry from configuration"
```

---

## Task 6: Agent and graph assembly

**Files:**
- Modify: `src/deep_research/runtime/assembly.py`
- Modify: `src/deep_research/runtime/__init__.py`
- Test: `tests/test_runtime/test_assembly.py` (append)

**Interfaces:**
- Consumes: `build_tools` (Task 5), `ResearchConfigurationError` / `configuration_error` (Task 1), `LongTermMemoryBridge` (Task 2), `deep_research.agents.*`, `deep_research.graph.{ResearchAgents, build_checkpointer, compile_research_graph}`, `deep_research.memory.{LongTermMemory, ProceduralMemory, ScratchpadMemory}`, `deep_research.providers.{OpenAIChatProvider, OpenAIEmbeddingProvider}`.
- Produces:
  - `AGENT_NAMES: tuple[str, ...]`
  - `def build_agents(settings: ConfigSettings, *, tracker: Tracker, provider: StructuredCompleter, tools: Sequence[BaseTool], session_id: str, reputation: ReputationSource | None) -> ResearchAgents`
  - `@dataclass(frozen=True) class ResearchRuntime` with fields `session_id: str`, `settings: ConfigSettings`, `tracker: Tracker`, `graph: Any`, `long_term: LongTermMemory | None`, `procedural: ProceduralMemory | None`
  - `async def build_runtime(settings: ConfigSettings, *, session_id: str, tracker: Tracker | None = None, chat_provider: StructuredCompleter | None = None, long_term: LongTermMemory | None = None, procedural: ProceduralMemory | None = None, tavily_api_key: str | None = None, search_client: Any | None = None, http_client: Any | None = None) -> ResearchRuntime`

Constructor facts verified against source — do not guess these:
- Every agent takes keyword-only `provider`, `tracker`, `scratchpad`, `tools`, `config`. Each has extra optional kwargs; leave them all at their defaults.
- `ScratchpadMemory.agent_name` must equal the agent class's `name`, or `BaseAgent.__init__` raises `AgentConfigurationError`. One scratchpad per agent, all sharing the session id.
- `SourceEvaluatorAgent` alone takes `reputation: ReputationSource | None`. `memory.LongTermMemory` satisfies that protocol exactly (`async def get_source_reputation(url) -> SourceReputation | None`). Pass the memory object, **not** the bridge.
- Agent `name` class attributes are `"planner"`, `"researcher"`, `"source_evaluator"`, `"fact_checker"`, `"synthesizer"`, `"critic"` — identical to `graph.state.NODE_NAMES[:6]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runtime/test_assembly.py`:

```python
from deep_research.graph.orchestrator import ResearchAgents
from deep_research.memory.procedural import ProceduralMemory
from deep_research.runtime.assembly import (
    AGENT_NAMES,
    ResearchRuntime,
    build_agents,
    build_runtime,
)
from deep_research.runtime.errors import ResearchConfigurationError


class RecordingProvider:
    """A structured completer that is never called during assembly."""

    def __init__(self) -> None:
        self.calls: list[object] = []

    async def complete_structured(self, messages, schema, *, agent_name=None):
        self.calls.append((messages, schema, agent_name))
        raise AssertionError("assembly must not call the provider")


def test_build_agents_fills_every_slot_with_the_right_agent(tracker) -> None:
    tools = build_tools(
        ConfigSettings(),
        tracker=tracker,
        memory=build_bridge(),
        search_client=FakeSearchClient(),
    )

    agents = build_agents(
        ConfigSettings(),
        tracker=tracker,
        provider=RecordingProvider(),
        tools=tools,
        session_id="session-1",
        reputation=None,
    )

    assert isinstance(agents, ResearchAgents)
    assert AGENT_NAMES == (
        "planner",
        "researcher",
        "source_evaluator",
        "fact_checker",
        "synthesizer",
        "critic",
    )
    assert [
        agents.planner.name,
        agents.researcher.name,
        agents.source_evaluator.name,
        agents.fact_checker.name,
        agents.synthesizer.name,
        agents.critic.name,
    ] == list(AGENT_NAMES)


def test_every_agent_gets_its_own_scratchpad_on_the_shared_session(
    tracker,
) -> None:
    tools = build_tools(
        ConfigSettings(),
        tracker=tracker,
        memory=build_bridge(),
        search_client=FakeSearchClient(),
    )

    agents = build_agents(
        ConfigSettings(),
        tracker=tracker,
        provider=RecordingProvider(),
        tools=tools,
        session_id="session-1",
        reputation=None,
    )

    pads = [
        agents.planner.scratchpad,
        agents.researcher.scratchpad,
        agents.source_evaluator.scratchpad,
        agents.fact_checker.scratchpad,
        agents.synthesizer.scratchpad,
        agents.critic.scratchpad,
    ]
    assert {pad.session_id for pad in pads} == {"session-1"}
    assert [pad.agent_name for pad in pads] == list(AGENT_NAMES)
    assert len({id(pad) for pad in pads}) == 6


def test_build_agents_reports_a_missing_tool_as_a_configuration_failure(
    tracker,
) -> None:
    with pytest.raises(ResearchConfigurationError) as caught:
        build_agents(
            ConfigSettings(),
            tracker=tracker,
            provider=RecordingProvider(),
            tools=[],
            session_id="session-1",
            reputation=None,
        )

    assert caught.value.reason == "agents_misconfigured"


@pytest.mark.asyncio
async def test_build_runtime_compiles_a_graph_from_injected_collaborators(
    tracker, tmp_path
) -> None:
    memory = LongTermMemory(
        collection=FakeCollection(), embeddings=FakeEmbeddings()
    )
    procedural = ProceduralMemory(tmp_path / "strategies.json")
    settings = ConfigSettings.model_validate(
        {"output": {"directory": str(tmp_path)}}
    )

    runtime = await build_runtime(
        settings,
        session_id="session-1",
        tracker=tracker,
        chat_provider=RecordingProvider(),
        long_term=memory,
        procedural=procedural,
        search_client=FakeSearchClient(),
    )

    assert isinstance(runtime, ResearchRuntime)
    assert runtime.session_id == "session-1"
    assert runtime.tracker is tracker
    assert runtime.long_term is memory
    assert runtime.procedural is procedural
    assert procedural.loaded is True
    assert runtime.graph is not None


@pytest.mark.asyncio
async def test_build_runtime_honours_the_checkpointing_setting(
    tracker, tmp_path
) -> None:
    settings = ConfigSettings.model_validate(
        {
            "graph": {"checkpointing_enabled": True},
            "output": {"directory": str(tmp_path)},
        }
    )

    runtime = await build_runtime(
        settings,
        session_id="session-1",
        tracker=tracker,
        chat_provider=RecordingProvider(),
        long_term=LongTermMemory(
            collection=FakeCollection(), embeddings=FakeEmbeddings()
        ),
        procedural=ProceduralMemory(tmp_path / "strategies.json"),
        search_client=FakeSearchClient(),
    )

    assert runtime.graph.checkpointer is not None


@pytest.mark.asyncio
async def test_build_runtime_reports_a_missing_openai_key_cleanly(
    tracker, tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = ConfigSettings.model_validate(
        {"output": {"directory": str(tmp_path)}}
    )

    with pytest.raises(ResearchConfigurationError) as caught:
        await build_runtime(
            settings,
            session_id="session-1",
            tracker=tracker,
            long_term=LongTermMemory(
                collection=FakeCollection(), embeddings=FakeEmbeddings()
            ),
            procedural=ProceduralMemory(tmp_path / "strategies.json"),
            search_client=FakeSearchClient(),
        )

    assert caught.value.reason == "provider_unconfigured"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runtime/test_assembly.py -v`
Expected: FAIL with `ImportError: cannot import name 'AGENT_NAMES' from 'deep_research.runtime.assembly'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/deep_research/runtime/assembly.py` (and add the new imports at the top of the file):

```python
from collections.abc import Sequence
from dataclasses import dataclass

from deep_research.agents.base import StructuredCompleter
from deep_research.agents.critic import CriticAgent
from deep_research.agents.errors import AgentConfigurationError
from deep_research.agents.fact_checker import FactCheckerAgent
from deep_research.agents.planner import PlannerAgent
from deep_research.agents.researcher import ResearcherAgent
from deep_research.agents.source_evaluator import (
    ReputationSource,
    SourceEvaluatorAgent,
)
from deep_research.agents.synthesizer import SynthesizerAgent
from deep_research.graph.orchestrator import (
    ResearchAgents,
    build_checkpointer,
    compile_research_graph,
)
from deep_research.memory.errors import MemoryInitializationError
from deep_research.memory.long_term import LongTermMemory
from deep_research.memory.procedural import ProceduralMemory
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.providers import (
    OpenAIChatProvider,
    OpenAIEmbeddingProvider,
    ProviderConfigurationError,
)
from deep_research.runtime.errors import configuration_error

# The six agents, in graph order. Equal to ``graph.state.NODE_NAMES[:6]``
# by construction: node names deliberately equal agent names.
AGENT_NAMES = (
    "planner",
    "researcher",
    "source_evaluator",
    "fact_checker",
    "synthesizer",
    "critic",
)


def _scratchpad(
    settings: ConfigSettings,
    *,
    session_id: str,
    agent_name: str,
) -> ScratchpadMemory:
    return ScratchpadMemory.from_config(
        settings.memory.short_term,
        session_id=session_id,
        agent_name=agent_name,
    )


def build_agents(
    settings: ConfigSettings,
    *,
    tracker: Tracker,
    provider: StructuredCompleter,
    tools: Sequence[BaseTool],
    session_id: str,
    reputation: ReputationSource | None,
) -> ResearchAgents:
    """Construct the six agents one graph runs.

    A tool an agent declares but nobody injected is an
    ``AgentConfigurationError`` raised here at construction, not a failure
    deferred to the first tool call. That is converted into a
    ``ResearchConfigurationError`` so the CLI can print it without a
    traceback.
    """
    shared = {
        "provider": provider,
        "tracker": tracker,
        "tools": tools,
        "config": settings.agents,
    }
    try:
        return ResearchAgents(
            planner=PlannerAgent(
                scratchpad=_scratchpad(
                    settings, session_id=session_id, agent_name="planner"
                ),
                **shared,
            ),
            researcher=ResearcherAgent(
                scratchpad=_scratchpad(
                    settings, session_id=session_id, agent_name="researcher"
                ),
                **shared,
            ),
            source_evaluator=SourceEvaluatorAgent(
                scratchpad=_scratchpad(
                    settings,
                    session_id=session_id,
                    agent_name="source_evaluator",
                ),
                reputation=reputation,
                **shared,
            ),
            fact_checker=FactCheckerAgent(
                scratchpad=_scratchpad(
                    settings, session_id=session_id, agent_name="fact_checker"
                ),
                **shared,
            ),
            synthesizer=SynthesizerAgent(
                scratchpad=_scratchpad(
                    settings, session_id=session_id, agent_name="synthesizer"
                ),
                **shared,
            ),
            critic=CriticAgent(
                scratchpad=_scratchpad(
                    settings, session_id=session_id, agent_name="critic"
                ),
                **shared,
            ),
        )
    except AgentConfigurationError as error:
        raise configuration_error(
            reason="agents_misconfigured",
            message=f"The research agents could not be assembled: {error}",
        ) from error


@dataclass(frozen=True, slots=True)
class ResearchRuntime:
    """One session's compiled graph and the collaborators that outlive it."""

    session_id: str
    settings: ConfigSettings
    tracker: Tracker
    graph: Any
    long_term: LongTermMemory | None
    procedural: ProceduralMemory | None


async def build_runtime(
    settings: ConfigSettings,
    *,
    session_id: str,
    tracker: Tracker | None = None,
    chat_provider: StructuredCompleter | None = None,
    long_term: LongTermMemory | None = None,
    procedural: ProceduralMemory | None = None,
    tavily_api_key: str | None = None,
    search_client: Any | None = None,
    http_client: Any | None = None,
) -> ResearchRuntime:
    """Build everything one research session needs, or fail cleanly.

    Every external collaborator is injectable so this whole path is
    testable without an API key, a network, or ChromaDB. Anything that can
    only go wrong at setup time — a missing key, an unopenable vector
    store, a tool an agent declares but nobody built — becomes a
    ``ResearchConfigurationError`` here rather than an exception the user
    sees as a traceback.
    """
    tracker = tracker or Tracker.from_config(settings.langsmith)

    try:
        if long_term is None:
            long_term = LongTermMemory.from_config(
                settings.memory.long_term,
                embeddings=OpenAIEmbeddingProvider(
                    model=settings.llm.embedding_model
                ),
                tracker=tracker,
            )
        if procedural is None:
            procedural = ProceduralMemory.from_config(
                settings.memory.procedural, tracker=tracker
            )
    except MemoryInitializationError as error:
        raise configuration_error(
            reason="memory_unavailable",
            message=f"Long-term memory could not be opened: {error}",
        ) from error

    await procedural.load()

    try:
        provider = chat_provider or OpenAIChatProvider(settings.llm, tracker)
    except ProviderConfigurationError as error:
        raise configuration_error(
            reason="provider_unconfigured",
            message=f"The OpenAI provider is not configured: {error}",
        ) from error

    bridge = LongTermMemoryBridge(long_term, session_id=session_id)
    tools = build_tools(
        settings,
        tracker=tracker,
        memory=bridge,
        tavily_api_key=tavily_api_key,
        search_client=search_client,
        http_client=http_client,
    )
    agents = build_agents(
        settings,
        tracker=tracker,
        provider=provider,
        tools=tools,
        session_id=session_id,
        reputation=long_term,
    )
    graph = compile_research_graph(
        agents,
        checkpointer=build_checkpointer(
            enabled=settings.graph.checkpointing_enabled
        ),
    )
    return ResearchRuntime(
        session_id=session_id,
        settings=settings,
        tracker=tracker,
        graph=graph,
        long_term=long_term,
        procedural=procedural,
    )
```

Extend `src/deep_research/runtime/__init__.py` with `AGENT_NAMES`, `ResearchRuntime`, `build_agents`, and `build_runtime`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_runtime/test_assembly.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/runtime tests/test_runtime
git commit -m "feat: assemble the six agents and the compiled graph from config"
```

---

## Task 7: `run_research()` — the shared entry point

**Files:**
- Modify: `src/deep_research/main.py` (replace the whole file)
- Test: `tests/test_runtime/test_run_research.py`

**Interfaces:**
- Consumes: `build_runtime` / `ResearchRuntime` (Task 6), `build_outcome` / `ResearchOutcome` (Task 4), `recall_memory_context` (Task 3), `configuration_error` (Task 1), `deep_research.graph.{run_research_graph, resume_research_graph, GraphResumeError}`, `deep_research.utils.config.load_config`.
- Produces:
  - `DEFAULT_CONFIG_PATH: str` (`"config.yaml"`)
  - `SUPPORTED_OUTPUT_FORMATS: tuple[str, ...]` (`("markdown",)`)
  - `def resolve_output_format(requested: str | None, *, configured: str) -> str`
  - `def load_settings(config_path: str) -> ConfigSettings`
  - `def new_session_id() -> str`
  - `RuntimeBuilder: TypeAlias` — the awaitable factory `run_research` uses to obtain a `ResearchRuntime`
  - `async def run_research(question: str | None = None, *, session_id: str | None = None, resume_session_id: str | None = None, config_path: str = DEFAULT_CONFIG_PATH, max_iterations: int | None = None, output_format: str | None = None, runtime_builder: RuntimeBuilder = build_runtime) -> ResearchOutcome`
  - `def run_research_sync(**kwargs: Any) -> ResearchOutcome`

- [ ] **Step 1: Write the failing test**

Create `tests/test_runtime/test_run_research.py`. Note that `ResearchRuntime` lives in `deep_research.runtime.assembly`, not in `graph.orchestrator`:

```python
"""Tests for the shared run_research entry point every front-end calls."""

from __future__ import annotations

import pytest
import yaml

from deep_research.graph.orchestrator import compile_research_graph
from deep_research.main import (
    DEFAULT_CONFIG_PATH,
    SUPPORTED_OUTPUT_FORMATS,
    resolve_output_format,
    run_research,
    run_research_sync,
)
from deep_research.runtime.assembly import ResearchRuntime
from deep_research.runtime.errors import ResearchConfigurationError
from deep_research.runtime.outcome import ResearchOutcome
from tests.graph_fakes import FakeAgent, fake_critique, fake_research_agents

QUESTION = "How mature is quantum error correction?"


@pytest.fixture
def config_file(tmp_path, monkeypatch) -> str:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    payload = {
        "graph": {"max_iterations": 2, "checkpointing_enabled": False},
        "output": {"directory": str(tmp_path / "output"), "default_format": "markdown"},
        "memory": {
            "long_term": {"persist_directory": str(tmp_path / "memory")},
            "procedural": {"strategies_path": str(tmp_path / "strategies.json")},
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return str(path)


def fake_builder(tracker, *, agents=None, checkpointer=None):
    """Return a runtime_builder that skips providers, memory, and tools."""

    async def build(settings, *, session_id, **_ignored):
        return ResearchRuntime(
            session_id=session_id,
            settings=settings,
            tracker=tracker,
            graph=compile_research_graph(
                agents or fake_research_agents(), checkpointer=checkpointer
            ),
            long_term=None,
            procedural=None,
        )

    return build


def test_the_default_config_path_is_the_repository_config() -> None:
    assert DEFAULT_CONFIG_PATH == "config.yaml"


def test_markdown_is_the_only_supported_output_format() -> None:
    assert SUPPORTED_OUTPUT_FORMATS == ("markdown",)


def test_resolve_output_format_falls_back_to_the_configured_default() -> None:
    assert resolve_output_format(None, configured="markdown") == "markdown"
    assert resolve_output_format("markdown", configured="markdown") == "markdown"


def test_resolve_output_format_rejects_an_unsupported_format() -> None:
    with pytest.raises(ResearchConfigurationError) as caught:
        resolve_output_format("pdf", configured="markdown")

    assert caught.value.reason == "unsupported_output_format"
    assert "pdf" in str(caught.value)


@pytest.mark.asyncio
async def test_a_successful_run_returns_an_outcome(config_file, tracker) -> None:
    outcome = await run_research(
        QUESTION,
        config_path=config_file,
        runtime_builder=fake_builder(tracker),
    )

    assert isinstance(outcome, ResearchOutcome)
    assert outcome.question == QUESTION
    assert outcome.status == "completed"
    assert outcome.report == "# Research report: pass 1"
    assert outcome.session_id


@pytest.mark.asyncio
async def test_the_supplied_session_id_is_used(config_file, tracker) -> None:
    outcome = await run_research(
        QUESTION,
        session_id="session-fixed",
        config_path=config_file,
        runtime_builder=fake_builder(tracker),
    )

    assert outcome.session_id == "session-fixed"
    assert outcome.state.session_id == "session-fixed"


@pytest.mark.asyncio
async def test_generated_session_ids_are_unique(config_file, tracker) -> None:
    first = await run_research(
        QUESTION, config_path=config_file, runtime_builder=fake_builder(tracker)
    )
    second = await run_research(
        QUESTION, config_path=config_file, runtime_builder=fake_builder(tracker)
    )

    assert first.session_id != second.session_id


@pytest.mark.asyncio
async def test_max_iterations_overrides_the_configured_budget(
    config_file, tracker
) -> None:
    agents = fake_research_agents(
        critic=FakeAgent(
            "critic", [{"critique": fake_critique(should_continue=True)}]
        )
    )

    outcome = await run_research(
        QUESTION,
        config_path=config_file,
        max_iterations=1,
        runtime_builder=fake_builder(tracker, agents=agents),
    )

    assert outcome.status == "max_iterations"
    assert outcome.state.max_iterations == 1


@pytest.mark.asyncio
async def test_the_configured_budget_is_used_when_none_is_passed(
    config_file, tracker
) -> None:
    outcome = await run_research(
        QUESTION, config_path=config_file, runtime_builder=fake_builder(tracker)
    )

    assert outcome.state.max_iterations == 2


@pytest.mark.asyncio
async def test_a_missing_config_file_is_a_configuration_failure(tracker) -> None:
    with pytest.raises(ResearchConfigurationError) as caught:
        await run_research(
            QUESTION,
            config_path="no-such-config.yaml",
            runtime_builder=fake_builder(tracker),
        )

    assert caught.value.reason == "config_file_missing"


@pytest.mark.asyncio
async def test_missing_api_keys_fail_fast(tmp_path, monkeypatch, tracker) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({}), encoding="utf-8")

    with pytest.raises(ResearchConfigurationError) as caught:
        await run_research(
            QUESTION,
            config_path=str(path),
            runtime_builder=fake_builder(tracker),
        )

    assert caught.value.reason == "missing_secrets"
    assert "OPENAI_API_KEY" in str(caught.value)


@pytest.mark.asyncio
async def test_no_question_and_no_resume_is_a_configuration_failure(
    config_file, tracker
) -> None:
    with pytest.raises(ResearchConfigurationError) as caught:
        await run_research(
            config_path=config_file, runtime_builder=fake_builder(tracker)
        )

    assert caught.value.reason == "no_question"


@pytest.mark.asyncio
async def test_resume_without_a_checkpoint_reports_the_known_limitation(
    config_file, tracker
) -> None:
    with pytest.raises(ResearchConfigurationError) as caught:
        await run_research(
            resume_session_id="session-gone",
            config_path=config_file,
            runtime_builder=fake_builder(tracker),
        )

    assert caught.value.reason == "no_checkpoint"
    assert "session-gone" in str(caught.value)


@pytest.mark.asyncio
async def test_resume_works_against_a_live_checkpoint(config_file, tracker) -> None:
    """Resume is real; only its cross-process durability is missing."""
    from deep_research.graph.orchestrator import build_checkpointer

    builder = fake_builder(tracker, checkpointer=build_checkpointer(enabled=True))
    shared: dict[str, object] = {}

    async def remembering_builder(settings, *, session_id, **kwargs):
        runtime = shared.get("runtime")
        if runtime is None:
            runtime = await builder(settings, session_id=session_id, **kwargs)
            shared["runtime"] = runtime
        return runtime

    first = await run_research(
        QUESTION,
        session_id="session-1",
        config_path=config_file,
        runtime_builder=remembering_builder,
    )
    resumed = await run_research(
        resume_session_id="session-1",
        config_path=config_file,
        runtime_builder=remembering_builder,
    )

    assert first.session_id == "session-1"
    assert resumed.session_id == "session-1"
    assert resumed.question == QUESTION


def test_run_research_sync_drives_the_async_entry_point(
    config_file, tracker
) -> None:
    outcome = run_research_sync(
        question=QUESTION,
        config_path=config_file,
        runtime_builder=fake_builder(tracker),
    )

    assert outcome.status == "completed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runtime/test_run_research.py -v`
Expected: FAIL with `ImportError: cannot import name 'DEFAULT_CONFIG_PATH' from 'deep_research.main'`

- [ ] **Step 3: Write minimal implementation**

Replace `src/deep_research/main.py` entirely:

```python
"""The one research entry point every front-end calls.

The CLI, the HTTP API, and the UI all go through ``run_research``. It owns
the session lifecycle — load configuration, build a runtime, recall memory,
drive the graph, summarize the result — and nothing else. Everything it
composes lives in ``deep_research.runtime`` and ``deep_research.graph``.

Failures split cleanly in two. Anything that can go wrong before the graph
starts is a ``ResearchConfigurationError`` with an enumerated hint. Once the
graph runs, failure is a *status*, not an exception: the graph records a
halt in state and returns everything it collected. Any other exception is a
defect and is deliberately allowed to propagate.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias
from uuid import uuid4

from deep_research.graph.errors import GraphResumeError
from deep_research.graph.orchestrator import (
    resume_research_graph,
    run_research_graph,
)
from deep_research.runtime.assembly import ResearchRuntime, build_runtime
from deep_research.runtime.errors import configuration_error
from deep_research.runtime.outcome import ResearchOutcome, build_outcome
from deep_research.runtime.recall import recall_memory_context
from deep_research.utils.config import ConfigSettings, load_config

DEFAULT_CONFIG_PATH = "config.yaml"

# Only Markdown is written in this build. The parent design pins this:
# "Writes Markdown in the first build. HTML and PDF export are out of scope
# until the Markdown path is stable."
SUPPORTED_OUTPUT_FORMATS = ("markdown",)

RuntimeBuilder: TypeAlias = Callable[..., Awaitable[ResearchRuntime]]


def new_session_id() -> str:
    """Return a fresh session identifier for one research run."""
    return uuid4().hex


def resolve_output_format(requested: str | None, *, configured: str) -> str:
    """Return the report format this run will write, or refuse to start."""
    chosen = (requested or configured or "").strip().casefold()
    if chosen not in SUPPORTED_OUTPUT_FORMATS:
        supported = ", ".join(SUPPORTED_OUTPUT_FORMATS)
        raise configuration_error(
            reason="unsupported_output_format",
            message=(
                f"Unsupported output format {chosen or '(blank)'!r}; "
                f"supported formats: {supported}"
            ),
        )
    return chosen


def load_settings(config_path: str) -> ConfigSettings:
    """Load configuration in strict mode, or refuse to start.

    Strict mode is not optional: the parent design requires missing API
    keys to fail fast at startup with a clear configuration error rather
    than surfacing as a provider failure inside a research pass.
    """
    try:
        return load_config(config_path, strict=True)
    except FileNotFoundError as error:
        raise configuration_error(
            reason="config_file_missing", message=str(error)
        ) from error
    except ValueError as error:
        message = str(error)
        reason = (
            "missing_secrets"
            if "environment variables" in message
            else "config_invalid"
        )
        raise configuration_error(reason=reason, message=message) from error


async def run_research(
    question: str | None = None,
    *,
    session_id: str | None = None,
    resume_session_id: str | None = None,
    config_path: str = DEFAULT_CONFIG_PATH,
    max_iterations: int | None = None,
    output_format: str | None = None,
    runtime_builder: RuntimeBuilder = build_runtime,
) -> ResearchOutcome:
    """Run one research session, or continue a checkpointed one.

    ``runtime_builder`` is injected rather than imported at the call site so
    a test can drive the real graph with scripted agents and no provider.
    """
    if question is None and resume_session_id is None:
        raise configuration_error(
            reason="no_question",
            message="No research question was supplied.",
        )
    if question is not None and resume_session_id is not None:
        raise configuration_error(
            reason="no_question",
            message=(
                "A research question and a resumed session cannot be "
                "combined; a resumed session already has its question."
            ),
        )

    settings = load_settings(config_path)
    resolve_output_format(
        output_format, configured=settings.output.default_format
    )

    effective_session_id = resume_session_id or session_id or new_session_id()
    runtime = await runtime_builder(
        settings, session_id=effective_session_id
    )

    if resume_session_id is not None:
        try:
            run = await resume_research_graph(
                graph=runtime.graph,
                tracker=runtime.tracker,
                session_id=resume_session_id,
                max_iterations=max_iterations,
            )
        except GraphResumeError as error:
            raise configuration_error(
                reason="no_checkpoint",
                message=(
                    f"Session {resume_session_id} cannot be resumed: {error}"
                ),
            ) from error
    else:
        assert question is not None  # narrowed by the guards above
        memory_context = await recall_memory_context(
            question=question,
            long_term=runtime.long_term,
            procedural=runtime.procedural,
        )
        run = await run_research_graph(
            graph=runtime.graph,
            tracker=runtime.tracker,
            session_id=effective_session_id,
            question=question,
            max_iterations=(
                settings.graph.max_iterations
                if max_iterations is None
                else max_iterations
            ),
            memory_context=memory_context,
        )

    return build_outcome(run, metrics=runtime.tracker.metrics)


def run_research_sync(**kwargs: Any) -> ResearchOutcome:
    """Run one research session from synchronous code.

    Keyword-only so a caller cannot silently pass a question positionally
    into ``asyncio.run``'s argument list.
    """
    return asyncio.run(run_research(**kwargs))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_runtime/ -v`
Expected: PASS (all runtime tests, including the 15 in `test_run_research.py`)

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/main.py tests/test_runtime
git commit -m "feat: wire run_research to the research graph"
```

---

## Task 8: CLI argument parsing

**Files:**
- Create: `src/deep_research/cli.py`
- Test: `tests/test_cli/__init__.py`, `tests/test_cli/test_arguments.py`

**Interfaces:**
- Consumes: `DEFAULT_CONFIG_PATH` (Task 7).
- Produces:
  - `PROGRAM_NAME: str` (`"python -m deep_research"`)
  - `@dataclass(frozen=True) class CliOptions` with fields `question: str | None`, `interactive: bool`, `resume: str | None`, `max_iterations: int | None`, `output_format: str | None`, `config: str`, `verbose: bool`
  - `def build_parser() -> argparse.ArgumentParser`
  - `def parse_arguments(argv: Sequence[str] | None = None) -> CliOptions`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli/__init__.py` as an empty file.

Create `tests/test_cli/test_arguments.py`:

```python
"""Tests for the CLI's argument surface."""

from __future__ import annotations

import pytest

from deep_research.cli import CliOptions, build_parser, parse_arguments
from deep_research.main import DEFAULT_CONFIG_PATH

QUESTION = "What are the security implications of quantum computing?"


def test_a_bare_question_is_the_whole_command() -> None:
    options = parse_arguments([QUESTION])

    assert options == CliOptions(
        question=QUESTION,
        interactive=False,
        resume=None,
        max_iterations=None,
        output_format=None,
        config=DEFAULT_CONFIG_PATH,
        verbose=False,
    )


def test_every_documented_option_parses() -> None:
    options = parse_arguments(
        [
            "AI in healthcare",
            "--max-iterations",
            "5",
            "--output-format",
            "markdown",
            "--config",
            "custom.yaml",
            "--verbose",
        ]
    )

    assert options.question == "AI in healthcare"
    assert options.max_iterations == 5
    assert options.output_format == "markdown"
    assert options.config == "custom.yaml"
    assert options.verbose is True


def test_interactive_takes_no_question() -> None:
    options = parse_arguments(["--interactive"])

    assert options.interactive is True
    assert options.question is None


def test_resume_takes_a_session_id() -> None:
    options = parse_arguments(["--resume", "session-1"])

    assert options.resume == "session-1"
    assert options.question is None


def test_a_question_and_interactive_together_are_a_usage_error() -> None:
    with pytest.raises(SystemExit) as caught:
        parse_arguments([QUESTION, "--interactive"])

    assert caught.value.code == 2


def test_a_question_and_resume_together_are_a_usage_error() -> None:
    with pytest.raises(SystemExit) as caught:
        parse_arguments([QUESTION, "--resume", "session-1"])

    assert caught.value.code == 2


def test_interactive_and_resume_together_are_a_usage_error() -> None:
    with pytest.raises(SystemExit) as caught:
        parse_arguments(["--interactive", "--resume", "session-1"])

    assert caught.value.code == 2


def test_no_arguments_at_all_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as caught:
        parse_arguments([])

    assert caught.value.code == 2


def test_a_non_positive_iteration_budget_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as caught:
        parse_arguments([QUESTION, "--max-iterations", "0"])

    assert caught.value.code == 2


def test_the_help_text_names_every_documented_option(capsys) -> None:
    build_parser().print_help()

    help_text = capsys.readouterr().out
    for flag in (
        "--interactive",
        "--resume",
        "--max-iterations",
        "--output-format",
        "--config",
        "--verbose",
    ):
        assert flag in help_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli/test_arguments.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_research.cli'`

- [ ] **Step 3: Write minimal implementation**

Create `src/deep_research/cli.py`:

```python
"""The command line front-end for ``python -m deep_research``.

Stdlib ``argparse`` and ``print``: the design's Non-Goals rule out a rich
terminal dependency, and nothing here needs one. Everything this module
does is parse arguments, call ``run_research``, render what came back, and
choose an exit code.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass

from deep_research.main import DEFAULT_CONFIG_PATH, SUPPORTED_OUTPUT_FORMATS

PROGRAM_NAME = "python -m deep_research"

_DESCRIPTION = (
    "Run a multi-agent deep research session and write a Markdown report."
)


@dataclass(frozen=True, slots=True)
class CliOptions:
    """One parsed, validated command line."""

    question: str | None
    interactive: bool
    resume: str | None
    max_iterations: int | None
    output_format: str | None
    config: str
    verbose: bool


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a whole number"
        ) from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser, including its usage examples."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM_NAME,
        description=_DESCRIPTION,
        epilog=(
            "examples:\n"
            f'  {PROGRAM_NAME} "What are the security implications of '
            'quantum computing?"\n'
            f'  {PROGRAM_NAME} "AI in healthcare" --max-iterations 5 '
            "--output-format markdown --verbose\n"
            f"  {PROGRAM_NAME} --interactive\n"
            f"  {PROGRAM_NAME} --resume <session_id>"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "question",
        nargs="?",
        default=None,
        help="the research question to investigate",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="prompt for the research question instead of passing it",
    )
    parser.add_argument(
        "--resume",
        metavar="SESSION_ID",
        default=None,
        help=(
            "continue a checkpointed session (only works inside the process "
            "that started it; see README)"
        ),
    )
    parser.add_argument(
        "--max-iterations",
        type=_positive_int,
        default=None,
        help="macro refinement passes the critic may request",
    )
    parser.add_argument(
        "--output-format",
        default=None,
        help=(
            "report format; supported: "
            f"{', '.join(SUPPORTED_OUTPUT_FORMATS)}"
        ),
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help=f"path to the YAML config file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print tool calls, token totals, and the full progress log",
    )
    return parser


def parse_arguments(argv: Sequence[str] | None = None) -> CliOptions:
    """Parse one command line, or exit 2 with a usage error.

    The three ways to name a session — a positional question,
    ``--interactive``, and ``--resume`` — are checked here rather than
    through a mutually exclusive group, because a positional with
    ``nargs="?"`` cannot join one and still produce a readable message.
    """
    parser = build_parser()
    namespace = parser.parse_args(argv)

    chosen = sum(
        (
            namespace.question is not None,
            bool(namespace.interactive),
            namespace.resume is not None,
        )
    )
    if chosen == 0:
        parser.error(
            "pass a research question, or use --interactive, or --resume"
        )
    if chosen > 1:
        parser.error(
            "a question, --interactive, and --resume are mutually exclusive"
        )

    return CliOptions(
        question=namespace.question,
        interactive=bool(namespace.interactive),
        resume=namespace.resume,
        max_iterations=namespace.max_iterations,
        output_format=namespace.output_format,
        config=namespace.config,
        verbose=bool(namespace.verbose),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli/test_arguments.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/cli.py tests/test_cli
git commit -m "feat: parse the deep research command line"
```

---

## Task 9: CLI rendering — progress log, warnings, summary

**Files:**
- Modify: `src/deep_research/cli.py`
- Test: `tests/test_cli/test_render.py`

**Interfaces:**
- Consumes: `ResearchOutcome`, `ToolCallSummary` (Task 4).
- Produces:
  - `PROGRESS_EVENT_TYPES: tuple[str, ...]`
  - `SPAN_EVENT_PREFIX: str` (`"observability.span."`)
  - `STATUS_NOTES: dict[str, str]`
  - `def render_progress(outcome: ResearchOutcome, *, verbose: bool) -> list[str]`
  - `def render_warnings(outcome: ResearchOutcome) -> list[str]`
  - `def render_summary(outcome: ResearchOutcome, *, verbose: bool) -> list[str]`

Rendering rules, fixed here so the tests and the implementation cannot drift:

- **Non-verbose progress** shows only the events in `PROGRESS_EVENT_TYPES`: `graph.session.started`, `graph.node.started`, `graph.refinement.started`, `graph.route.decided`, `graph.session.completed`. Each renders as `"  [<iteration>] <message>"`.
- **Verbose progress** shows every event in `state.events` except those whose `event_type` starts with `observability.span.` — those are span bookkeeping the tracker writes for itself and would drown everything else.
- `graph.node.started` is what "current agent" means: its `metadata["node"]` is the agent that is running.
- **Warnings** render one line per `state.errors` entry as `"warning: [<error_type>] <message>"`. The parent spec's other half — "included in the report limitations" — is already satisfied by `agents.synthesizer.limitation_reasons`, which appends `errors_recorded` whenever `state.errors` is non-empty. No new work.
- **Summary** always prints session id, status (plus the `STATUS_NOTES` sentence when there is one), and the report path or an explicit "not written to disk" line. It prints the trace URL only when there is one. Verbose additionally prints tool call summaries and token totals, rendering zero totals as `not available`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli/test_render.py`:

```python
"""Tests for what the CLI prints."""

from __future__ import annotations

from deep_research.cli import render_progress, render_summary, render_warnings
from deep_research.graph.events import (
    node_started_event,
    refinement_started_event,
    session_completed_event,
    session_started_event,
)
from deep_research.observability import TokenUsage
from deep_research.runtime.outcome import ResearchOutcome, ToolCallSummary
from deep_research.utils.types import (
    ResearchError,
    ResearchEvent,
    ResearchState,
)

QUESTION = "How mature is quantum error correction?"


def build_outcome(**overrides) -> ResearchOutcome:
    state = overrides.pop("state", None) or ResearchState(
        session_id="session-1", original_question=QUESTION
    )
    defaults = {
        "session_id": "session-1",
        "question": QUESTION,
        "status": "completed",
        "state": state,
        "trace_url": None,
        "report_path": "report-session-1-0.md",
        "token_usage": TokenUsage(),
        "tool_calls": (),
    }
    defaults.update(overrides)
    return ResearchOutcome(**defaults)


def progress_state() -> ResearchState:
    return ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        events=[
            session_started_event(
                session_id="session-1", max_iterations=2, checkpointing=False
            ),
            node_started_event("planner", iteration=0),
            ResearchEvent(
                event_type="observability.span.started",
                source="observability",
                message="agent.planner started.",
            ),
            node_started_event("researcher", iteration=0),
            refinement_started_event(iteration=1, max_iterations=2),
            session_completed_event(
                status="completed", iteration=1, error_count=0, has_report=True
            ),
        ],
    )


def test_progress_shows_each_agent_without_the_span_noise() -> None:
    lines = render_progress(build_outcome(state=progress_state()), verbose=False)

    joined = "\n".join(lines)
    assert "Node planner started." in joined
    assert "Node researcher started." in joined
    assert "Refinement pass 1 started." in joined
    assert "observability" not in joined


def test_verbose_progress_keeps_every_event_but_the_spans() -> None:
    state = progress_state()

    lines = render_progress(build_outcome(state=state), verbose=True)

    assert len(lines) == len(
        [
            event
            for event in state.events
            if not event.event_type.startswith("observability.span.")
        ]
    ) + 1  # the heading


def test_warnings_render_one_line_per_recorded_error() -> None:
    state = ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        errors=[
            ResearchError(
                error_type="web_search_failed",
                source="tools.web_search",
                message="The search provider timed out.",
            )
        ],
    )

    lines = render_warnings(build_outcome(state=state))

    assert lines == [
        "warning: [web_search_failed] The search provider timed out."
    ]


def test_no_errors_means_no_warnings() -> None:
    assert render_warnings(build_outcome()) == []


def test_the_summary_names_the_session_status_and_report() -> None:
    lines = render_summary(build_outcome(), verbose=False)

    joined = "\n".join(lines)
    assert "Session ID: session-1" in joined
    assert "Status: completed" in joined
    assert "Report: report-session-1-0.md" in joined
    assert "Trace" not in joined


def test_the_summary_prints_the_trace_url_when_there_is_one() -> None:
    lines = render_summary(
        build_outcome(trace_url="https://smith.example/run/1"), verbose=False
    )

    assert "Trace: https://smith.example/run/1" in "\n".join(lines)


def test_the_summary_is_explicit_when_no_report_reached_disk() -> None:
    lines = render_summary(build_outcome(report_path=None), verbose=False)

    assert "Report: not written to disk" in "\n".join(lines)


def test_a_limited_run_says_so_without_calling_itself_a_failure() -> None:
    lines = render_summary(build_outcome(status="max_iterations"), verbose=False)

    joined = "\n".join(lines)
    assert "Status: max_iterations" in joined
    assert "refinement budget" in joined


def test_verbose_summary_reports_tool_calls_and_tokens() -> None:
    outcome = build_outcome(
        token_usage=TokenUsage(input_tokens=900, output_tokens=100),
        tool_calls=(
            ToolCallSummary(tool_name="web_search", calls=4, failures=1),
            ToolCallSummary(tool_name="query_memory", calls=2, failures=0),
        ),
    )

    joined = "\n".join(render_summary(outcome, verbose=True))

    assert "web_search: 4 calls (1 failed)" in joined
    assert "query_memory: 2 calls" in joined
    assert "Tokens: 1000 total (900 in / 100 out)" in joined


def test_verbose_summary_says_tokens_are_unavailable_when_none_were_seen() -> None:
    joined = "\n".join(render_summary(build_outcome(), verbose=True))

    assert "Tokens: not available" in joined
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli/test_render.py -v`
Expected: FAIL with `ImportError: cannot import name 'render_progress' from 'deep_research.cli'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/deep_research/cli.py` (and extend its imports):

```python
from deep_research.runtime.outcome import ResearchOutcome

# The events a plain run shows: the session's boundaries, which agent is
# running, and every macro routing decision. Enough to see progress without
# reading a log, which is exactly what the design asks for.
PROGRESS_EVENT_TYPES = (
    "graph.session.started",
    "graph.node.started",
    "graph.refinement.started",
    "graph.route.decided",
    "graph.session.completed",
)

# Span lifecycle events the tracker writes for itself. Excluded from even
# the verbose log: there are two per span and they would bury everything.
SPAN_EVENT_PREFIX = "observability.span."

# What a non-failing but non-ideal ending means, in one sentence.
STATUS_NOTES = {
    "max_iterations": (
        "Research completed with limitations: the refinement budget was "
        "exhausted before the critic accepted the report."
    ),
    "incomplete": (
        "Research completed with limitations: the run ended without an "
        "accepted critique."
    ),
    "failed": (
        "The research run stopped on a non-recoverable failure; everything "
        "collected before it survives in the report state."
    ),
}


def render_progress(outcome: ResearchOutcome, *, verbose: bool) -> list[str]:
    """Render the recorded event log as a progress summary.

    A summary rather than a live stream: ``run_research_graph`` invokes the
    graph to completion and returns one result, so the events exist only
    once the run is over. Live streaming belongs to the API's SSE endpoint.
    """
    lines = ["Progress log:"]
    for event in outcome.state.events:
        if event.event_type.startswith(SPAN_EVENT_PREFIX):
            continue
        if not verbose and event.event_type not in PROGRESS_EVENT_TYPES:
            continue
        iteration = event.metadata.get("iteration", 0)
        lines.append(f"  [{iteration}] {event.message}")
    return lines


def render_warnings(outcome: ResearchOutcome) -> list[str]:
    """Render recoverable research errors as warnings.

    These are also disclosed inside the report itself:
    ``agents.synthesizer.limitation_reasons`` records ``errors_recorded``
    whenever state carries any error, and the report's Limitations section
    renders it.
    """
    return [
        f"warning: [{error.error_type}] {error.message}"
        for error in outcome.errors
    ]


def render_summary(outcome: ResearchOutcome, *, verbose: bool) -> list[str]:
    """Render the run's identity, outcome, artifacts, and (verbose) costs."""
    lines = [
        f"Session ID: {outcome.session_id}",
        f"Status: {outcome.status}",
    ]
    note = STATUS_NOTES.get(outcome.status)
    if note is not None:
        lines.append(note)

    if outcome.report_path is None:
        lines.append(
            "Report: not written to disk; the report text is in the session "
            "state only."
        )
    else:
        lines.append(f"Report: {outcome.report_path}")

    if outcome.trace_url is not None:
        lines.append(f"Trace: {outcome.trace_url}")

    if verbose:
        if outcome.tool_calls:
            lines.append("Tool calls:")
            for summary in outcome.tool_calls:
                failures = (
                    f" ({summary.failures} failed)" if summary.failures else ""
                )
                lines.append(
                    f"  {summary.tool_name}: {summary.calls} calls{failures}"
                )
        else:
            lines.append("Tool calls: none recorded")

        usage = outcome.token_usage
        total = usage.total_tokens or 0
        if total:
            lines.append(
                f"Tokens: {total} total ({usage.input_tokens} in / "
                f"{usage.output_tokens} out)"
            )
        else:
            lines.append("Tokens: not available")
    return lines
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli/test_render.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/cli.py tests/test_cli
git commit -m "feat: render research progress, warnings, and summary for the CLI"
```

---

## Task 10: CLI entry point, exit codes, and `python -m deep_research`

**Files:**
- Modify: `src/deep_research/cli.py`
- Modify: `src/deep_research/__main__.py` (replace the whole file)
- Test: `tests/test_cli/test_entrypoint.py`

**Interfaces:**
- Consumes: `parse_arguments` / `CliOptions` (Task 8), `render_progress` / `render_warnings` / `render_summary` (Task 9), `run_research_sync` (Task 7), `ResearchConfigurationError` (Task 1).
- Produces:
  - `EXIT_OK: int` (`0`), `EXIT_CONFIGURATION_ERROR: int` (`1`), `EXIT_GRAPH_FAILED: int` (`3`), `EXIT_INTERRUPTED: int` (`130`)
  - `INTERACTIVE_PROMPT: str`
  - `def resolve_question(options: CliOptions, *, prompt: Callable[[str], str]) -> str | None`
  - `def main(argv: Sequence[str] | None = None, *, runner: Callable[..., ResearchOutcome] = run_research_sync, prompt: Callable[[str], str] = input, stream: TextIO | None = None) -> int`
  - `src/deep_research/__main__.py` re-exports `main` and calls it under `__main__`.

Exit code table, documented in the module docstring and in the README:

| Code | Meaning |
| --- | --- |
| 0 | The run finished; a report exists (status `completed`, `max_iterations`, or `incomplete`) |
| 1 | Configuration failure — bad config path, missing API keys, unsupported format, no resumable checkpoint |
| 2 | Usage error (argparse) |
| 3 | Graph failure — status `failed` |
| 130 | Interrupted with Ctrl-C |

Unexpected exceptions are **not** caught. `graph/nodes.py` states the rule this follows: "An unhandled exception is a defect, not a research outcome, and converting it into a recorded error would hide it."

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli/test_entrypoint.py`:

```python
"""Tests for the CLI entry point: wiring, prompts, and exit codes."""

from __future__ import annotations

import io

import pytest

from deep_research.cli import (
    EXIT_CONFIGURATION_ERROR,
    EXIT_GRAPH_FAILED,
    EXIT_INTERRUPTED,
    EXIT_OK,
    main,
)
from deep_research.observability import TokenUsage
from deep_research.runtime.errors import configuration_error
from deep_research.runtime.outcome import ResearchOutcome
from deep_research.utils.types import ResearchError, ResearchState

QUESTION = "How mature is quantum error correction?"


def outcome(status: str = "completed", **overrides) -> ResearchOutcome:
    state = overrides.pop("state", None) or ResearchState(
        session_id="session-1", original_question=QUESTION
    )
    defaults = {
        "session_id": "session-1",
        "question": QUESTION,
        "status": status,
        "state": state,
        "trace_url": None,
        "report_path": "report-session-1-0.md",
        "token_usage": TokenUsage(),
        "tool_calls": (),
    }
    defaults.update(overrides)
    return ResearchOutcome(**defaults)


class RecordingRunner:
    """Capture the keyword arguments the CLI hands to run_research_sync."""

    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result if result is not None else outcome()
        self.error = error
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> ResearchOutcome:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def test_a_successful_run_exits_zero_and_prints_the_report_path() -> None:
    runner = RecordingRunner()
    stream = io.StringIO()

    code = main([QUESTION], runner=runner, stream=stream)

    assert code == EXIT_OK
    printed = stream.getvalue()
    assert "Session ID: session-1" in printed
    assert "Report: report-session-1-0.md" in printed


def test_the_cli_passes_every_option_through_to_run_research() -> None:
    runner = RecordingRunner()

    main(
        [
            QUESTION,
            "--max-iterations",
            "5",
            "--output-format",
            "markdown",
            "--config",
            "custom.yaml",
            "--verbose",
        ],
        runner=runner,
        stream=io.StringIO(),
    )

    assert runner.calls == [
        {
            "question": QUESTION,
            "resume_session_id": None,
            "config_path": "custom.yaml",
            "max_iterations": 5,
            "output_format": "markdown",
        }
    ]


def test_interactive_mode_prompts_once_and_runs_the_answer() -> None:
    runner = RecordingRunner()
    prompts: list[str] = []

    def prompt(message: str) -> str:
        prompts.append(message)
        return f"  {QUESTION}  "

    code = main(
        ["--interactive"], runner=runner, prompt=prompt, stream=io.StringIO()
    )

    assert code == EXIT_OK
    assert len(prompts) == 1
    assert runner.calls[0]["question"] == QUESTION


def test_an_empty_interactive_answer_is_a_configuration_failure() -> None:
    runner = RecordingRunner()
    stream = io.StringIO()

    code = main(
        ["--interactive"],
        runner=runner,
        prompt=lambda _message: "   ",
        stream=stream,
    )

    assert code == EXIT_CONFIGURATION_ERROR
    assert runner.calls == []
    assert "error:" in stream.getvalue()


def test_resume_passes_the_session_id_and_no_question() -> None:
    runner = RecordingRunner()

    main(["--resume", "session-1"], runner=runner, stream=io.StringIO())

    assert runner.calls[0]["question"] is None
    assert runner.calls[0]["resume_session_id"] == "session-1"


def test_a_configuration_failure_prints_its_hint_and_exits_one() -> None:
    runner = RecordingRunner(
        error=configuration_error(
            reason="missing_secrets",
            message="Missing required environment variables: OPENAI_API_KEY",
        )
    )
    stream = io.StringIO()

    code = main([QUESTION], runner=runner, stream=stream)

    assert code == EXIT_CONFIGURATION_ERROR
    printed = stream.getvalue()
    assert "error: Missing required environment variables" in printed
    assert "hint: Set OPENAI_API_KEY" in printed
    assert "Traceback" not in printed


def test_an_unresumable_session_exits_one_with_the_known_limitation() -> None:
    runner = RecordingRunner(
        error=configuration_error(
            reason="no_checkpoint",
            message="Session session-1 cannot be resumed: no checkpoint",
        )
    )
    stream = io.StringIO()

    code = main(["--resume", "session-1"], runner=runner, stream=stream)

    assert code == EXIT_CONFIGURATION_ERROR
    assert "in-memory checkpoints do not survive" in stream.getvalue()


def test_a_failed_graph_run_exits_three() -> None:
    runner = RecordingRunner(result=outcome(status="failed"))
    stream = io.StringIO()

    code = main([QUESTION], runner=runner, stream=stream)

    assert code == EXIT_GRAPH_FAILED
    assert "Status: failed" in stream.getvalue()


def test_a_limited_run_still_exits_zero() -> None:
    runner = RecordingRunner(result=outcome(status="max_iterations"))

    code = main([QUESTION], runner=runner, stream=io.StringIO())

    assert code == EXIT_OK


def test_recoverable_errors_are_printed_as_warnings_not_failures() -> None:
    state = ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        errors=[
            ResearchError(
                error_type="web_search_failed",
                source="tools.web_search",
                message="The search provider timed out.",
            )
        ],
    )
    runner = RecordingRunner(result=outcome(state=state))
    stream = io.StringIO()

    code = main([QUESTION], runner=runner, stream=stream)

    assert code == EXIT_OK
    assert "warning: [web_search_failed]" in stream.getvalue()


def test_a_keyboard_interrupt_exits_one_hundred_thirty() -> None:
    runner = RecordingRunner(error=KeyboardInterrupt())
    stream = io.StringIO()

    code = main([QUESTION], runner=runner, stream=stream)

    assert code == EXIT_INTERRUPTED
    assert "cancelled" in stream.getvalue()


def test_an_unexpected_exception_is_not_swallowed() -> None:
    runner = RecordingRunner(error=RuntimeError("a defect"))

    with pytest.raises(RuntimeError, match="a defect"):
        main([QUESTION], runner=runner, stream=io.StringIO())


def test_the_module_entry_point_exposes_main() -> None:
    from deep_research.__main__ import main as module_main

    assert module_main is main
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli/test_entrypoint.py -v`
Expected: FAIL with `ImportError: cannot import name 'EXIT_CONFIGURATION_ERROR' from 'deep_research.cli'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/deep_research/cli.py` (and extend its imports with `sys`, `Callable`, `TextIO`, `ResearchConfigurationError`, `run_research_sync`):

```python
import sys
from collections.abc import Callable
from typing import TextIO

from deep_research.main import run_research_sync
from deep_research.runtime.errors import (
    ResearchConfigurationError,
    configuration_error,
)

EXIT_OK = 0
EXIT_CONFIGURATION_ERROR = 1
# 2 is argparse's usage error and is never returned from here.
EXIT_GRAPH_FAILED = 3
EXIT_INTERRUPTED = 130

INTERACTIVE_PROMPT = "Research question: "

_STARTING_NOTICE = (
    "Researching. A full session runs the six agents and can take several "
    "minutes."
)


def resolve_question(
    options: CliOptions,
    *,
    prompt: Callable[[str], str],
) -> str | None:
    """Return the question this invocation researches, or ``None`` to resume.

    Interactive mode asks once and runs once. The design's Testing section
    names a single "interactive input path"; a REPL is not asked for and is
    not built.
    """
    if options.resume is not None:
        return None
    if not options.interactive:
        return options.question

    try:
        answer = prompt(INTERACTIVE_PROMPT)
    except EOFError as error:
        raise configuration_error(
            reason="no_question",
            message="No research question was entered.",
        ) from error
    answer = answer.strip()
    if not answer:
        raise configuration_error(
            reason="no_question",
            message="No research question was entered.",
        )
    return answer


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Callable[..., ResearchOutcome] = run_research_sync,
    prompt: Callable[[str], str] = input,
    stream: TextIO | None = None,
) -> int:
    """Run one command and return its exit code.

    Exit codes: 0 the run produced a report, 1 a configuration failure,
    2 a usage error (raised by argparse), 3 the graph failed, 130 the user
    interrupted. Any other exception propagates: an unhandled exception is
    a defect, not a research outcome.
    """
    out = stream if stream is not None else sys.stdout
    options = parse_arguments(argv)

    def emit(lines: Sequence[str]) -> None:
        for line in lines:
            print(line, file=out)

    try:
        question = resolve_question(options, prompt=prompt)
        print(_STARTING_NOTICE, file=out)
        outcome = runner(
            question=question,
            resume_session_id=options.resume,
            config_path=options.config,
            max_iterations=options.max_iterations,
            output_format=options.output_format,
        )
    except ResearchConfigurationError as error:
        print(f"error: {error}", file=out)
        print(f"hint: {error.hint}", file=out)
        return EXIT_CONFIGURATION_ERROR
    except KeyboardInterrupt:
        print("The research run was cancelled.", file=out)
        return EXIT_INTERRUPTED

    emit(render_progress(outcome, verbose=options.verbose))
    emit(render_warnings(outcome))
    emit(render_summary(outcome, verbose=options.verbose))
    return EXIT_GRAPH_FAILED if outcome.failed else EXIT_OK
```

Replace `src/deep_research/__main__.py` entirely:

```python
"""Entry point for ``python -m deep_research``."""

from deep_research.cli import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli/ -v`
Expected: PASS (34 passed across the three CLI test modules)

- [ ] **Step 5: Verify the real command runs**

Run: `python -m deep_research --help`
Expected: the usage text, including all six options and the four examples, exit code 0.

Run: `python -m deep_research "test" --config no-such-file.yaml`
Expected: two lines — `error: Config file not found: no-such-file.yaml` and `hint: Pass --config with the path to a config.yaml file.` — and exit code 1, with no traceback. Check with `echo $?` (bash) or `$LASTEXITCODE` (PowerShell).

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/cli.py src/deep_research/__main__.py tests/test_cli
git commit -m "feat: run research from the command line"
```

---

## Task 11: Import surface, documentation, and full-suite verification

**Files:**
- Modify: `tests/test_imports.py`
- Modify: `README.md`
- Test: `tests/test_imports.py`

**Interfaces:**
- Consumes: every name produced by Tasks 1-10.
- Produces: no new code. This task proves the package surface resolves and documents the CLI.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_imports.py`:

```python
def test_runtime_contracts_import_from_package() -> None:
    from deep_research.runtime import (  # noqa: F401
        AGENT_NAMES,
        CONFIGURATION_HINTS,
        DEFAULT_BRIDGE_AGENT_ID,
        DEFAULT_BRIDGE_ENTRY_TYPE,
        DEFAULT_RECALL_TOP_K,
        MAX_SUGGESTED_STRATEGIES,
        RECALLED_SUB_TOPIC,
        REPORT_WRITTEN_EVENT,
        TAVILY_API_KEY_VARIABLE,
        LongTermMemoryBridge,
        ResearchConfigurationError,
        ResearchOutcome,
        ResearchRuntime,
        ToolCallSummary,
        build_agents,
        build_outcome,
        build_runtime,
        build_tools,
        configuration_error,
        recall_memory_context,
        report_path_from_state,
        tool_call_summaries,
        total_token_usage,
    )


def test_runtime_all_surface_is_fully_covered() -> None:
    """Every name in ``deep_research.runtime.__all__`` must actually resolve."""
    import deep_research.runtime as runtime_pkg

    missing = [
        name for name in runtime_pkg.__all__ if not hasattr(runtime_pkg, name)
    ]
    assert not missing, f"__all__ entries missing from package: {missing}"


def test_the_research_entry_point_is_importable() -> None:
    from deep_research.main import (  # noqa: F401
        DEFAULT_CONFIG_PATH,
        SUPPORTED_OUTPUT_FORMATS,
        load_settings,
        new_session_id,
        resolve_output_format,
        run_research,
        run_research_sync,
    )


def test_the_cli_surface_is_importable() -> None:
    from deep_research.cli import (  # noqa: F401
        EXIT_CONFIGURATION_ERROR,
        EXIT_GRAPH_FAILED,
        EXIT_INTERRUPTED,
        EXIT_OK,
        INTERACTIVE_PROMPT,
        PROGRAM_NAME,
        PROGRESS_EVENT_TYPES,
        SPAN_EVENT_PREFIX,
        STATUS_NOTES,
        CliOptions,
        build_parser,
        main,
        parse_arguments,
        render_progress,
        render_summary,
        render_warnings,
        resolve_question,
    )


def test_the_agent_names_match_the_graph_node_names() -> None:
    from deep_research.graph import NODE_NAMES
    from deep_research.runtime import AGENT_NAMES

    assert AGENT_NAMES == NODE_NAMES[:6]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_imports.py -v`
Expected: FAIL — at least one `ImportError` for a name not yet re-exported from `deep_research.runtime.__init__`.

- [ ] **Step 3: Fix the export surface**

Bring `src/deep_research/runtime/__init__.py` to its final form — every public name from `errors.py`, `memory_bridge.py`, `recall.py`, `assembly.py`, and `outcome.py`, with `__all__` sorted the way the other packages sort it (uppercase constants first, then CamelCase classes, then lowercase functions):

```python
"""Assembly of a runnable research session from loaded configuration."""

from deep_research.runtime.assembly import (
    AGENT_NAMES,
    TAVILY_API_KEY_VARIABLE,
    ResearchRuntime,
    build_agents,
    build_runtime,
    build_tools,
)
from deep_research.runtime.errors import (
    CONFIGURATION_HINTS,
    ResearchConfigurationError,
    configuration_error,
)
from deep_research.runtime.memory_bridge import (
    DEFAULT_BRIDGE_AGENT_ID,
    DEFAULT_BRIDGE_ENTRY_TYPE,
    LongTermMemoryBridge,
)
from deep_research.runtime.outcome import (
    REPORT_WRITTEN_EVENT,
    ResearchOutcome,
    ToolCallSummary,
    build_outcome,
    report_path_from_state,
    tool_call_summaries,
    total_token_usage,
)
from deep_research.runtime.recall import (
    DEFAULT_RECALL_TOP_K,
    MAX_SUGGESTED_STRATEGIES,
    RECALLED_SUB_TOPIC,
    recall_memory_context,
)

__all__ = [
    "AGENT_NAMES",
    "CONFIGURATION_HINTS",
    "DEFAULT_BRIDGE_AGENT_ID",
    "DEFAULT_BRIDGE_ENTRY_TYPE",
    "DEFAULT_RECALL_TOP_K",
    "MAX_SUGGESTED_STRATEGIES",
    "RECALLED_SUB_TOPIC",
    "REPORT_WRITTEN_EVENT",
    "TAVILY_API_KEY_VARIABLE",
    "LongTermMemoryBridge",
    "ResearchConfigurationError",
    "ResearchOutcome",
    "ResearchRuntime",
    "ToolCallSummary",
    "build_agents",
    "build_outcome",
    "build_runtime",
    "build_tools",
    "configuration_error",
    "recall_memory_context",
    "report_path_from_state",
    "tool_call_summaries",
    "total_token_usage",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_imports.py -v`
Expected: PASS

- [ ] **Step 5: Document the CLI in the README**

Insert a `## Command Line Interface` section immediately before `## Development`:

````markdown
## Command Line Interface

```bash
python -m deep_research "What are the security implications of quantum computing?"
python -m deep_research "AI in healthcare" --max-iterations 5 --output-format markdown --verbose
python -m deep_research --interactive
python -m deep_research --resume <session_id>
```

| Option | Meaning |
| --- | --- |
| `question` | The research question. Mutually exclusive with `--interactive` and `--resume`. |
| `--interactive` | Prompt once for the question, run once, exit. |
| `--resume SESSION_ID` | Continue a checkpointed session. See the limitation below. |
| `--max-iterations N` | Macro refinement passes the critic may request. Defaults to `graph.max_iterations`. |
| `--output-format` | Report format. Only `markdown` is supported in this build. |
| `--config PATH` | YAML config file. Defaults to `config.yaml`. |
| `--verbose` | Print every progress event, tool call counts, and token totals. |

Every interface calls the same `deep_research.main.run_research()`, which loads
configuration in **strict** mode: `OPENAI_API_KEY` and `TAVILY_API_KEY` (plus
`LANGSMITH_API_KEY` and `LANGSMITH_PROJECT` when `langsmith.tracing_enabled` is
true) must be present in the environment or in a `.env` file next to
`config.yaml`, or the command exits 1 before any model is called.

| Exit code | Meaning |
| --- | --- |
| 0 | The run produced a report (`completed`, `max_iterations`, or `incomplete`) |
| 1 | Configuration failure — bad config path, missing keys, unsupported format, no resumable checkpoint |
| 2 | Usage error |
| 3 | The graph failed (`failed`) |
| 130 | Interrupted with Ctrl-C |

Recoverable research errors never fail the command. They are printed as
`warning:` lines and disclosed inside the report's Limitations section.

**Progress is a post-run log, not a live stream.** `run_research_graph` invokes
the graph to completion and returns one result, so the CLI prints
`ResearchState.events` once the run is over. Live progress arrives with the
API's server-sent-events endpoint.

**`--resume` only works inside one process.** `build_checkpointer` returns
LangGraph's `InMemorySaver`, which does not survive the process that created
it, so resuming from a new command exits 1 with a clear message rather than
pretending a checkpoint exists. A durable saver drops into
`compile_research_graph` without touching a node.
````

Update the phase list at the end of the README:

```markdown
- Phase 3: Agents and LangGraph orchestration ← complete (all six agents and the graph)
- Phase 4: CLI ← complete; FastAPI API and Streamlit UI next
- Phase 5: Tests and verification
```

- [ ] **Step 6: Run the whole suite and the linter**

Run: `pytest`
Expected: PASS, no regressions in `tests/test_graph/`, `tests/test_agents/`, or anywhere else.

Run: `ruff check src/ tests/`
Expected: no findings. If import order is flagged, let `ruff check --fix` sort them.

- [ ] **Step 7: Commit**

```bash
git add tests/test_imports.py README.md src/deep_research/runtime/__init__.py
git commit -m "docs: document the deep research command line interface"
```

---

## Manual verification (optional, needs real API keys)

Not a task — no automated test can cover it, and the parent design lists it
under Manual verification. Run it once before merging if keys are available:

```bash
export OPENAI_API_KEY=...   # or set them in .env next to config.yaml
export TAVILY_API_KEY=...
python -m deep_research "What are the security implications of quantum computing?" --max-iterations 1 --verbose
```

Confirm: a progress log naming each of the six agents, a `Report:` line, a file
under `output/`, and — with `LANGSMITH_TRACING=true` plus `LANGSMITH_API_KEY`
and `LANGSMITH_PROJECT` set — a `Trace:` URL that opens a session trace showing
graph nodes, agent spans, tool calls, and token usage.

---

## Self-Review

**1. Spec coverage**

| Spec 12 requirement | Task |
| --- | --- |
| `python -m deep_research` entry point | 10 |
| Positional research question argument | 8 |
| Interactive mode | 8 (parsing), 10 (prompt + run) |
| Resume option | 8 (parsing), 7 (mechanics + honest failure), 10 (exit code) |
| Progress output | 9 |
| Final report path display | 4 (derivation), 9 (rendering) |
| No API server / no Streamlit / no rich dependency | Global Constraints; nothing added to `pyproject.toml` |
| CLI options `question`, `--interactive`, `--resume`, `--max-iterations`, `--output-format`, `--config`, `--verbose` | 8 |
| "calls the shared `run_research()` function" | 7 (the function), 10 (the call) |
| "subscribes to structured progress events" | 9 — rendered from `ResearchState.events` after the run; the honest reading, documented as a limitation in the README, because no subscription mechanism exists in the orchestrator and this plan may not add one |
| Verbose: session ID, trace URL, current agent, tool call summaries, token totals | 9 (`render_summary` + `render_progress`), 4 (`tool_call_summaries`, `total_token_usage`) |
| Non-zero exit for configuration failures | 1, 7, 10 |
| Non-zero exit for graph failures | 10 (`EXIT_GRAPH_FAILED`) |
| Recoverable errors printed as warnings | 9 (`render_warnings`) |
| Recoverable errors in report limitations | Already implemented by `agents.synthesizer.limitation_reasons` (`errors_recorded`); noted in Task 9, no new work |
| Test: argument parsing | 8 |
| Test: interactive input path | 10 |
| Test: successful mocked run | 7 and 10 |
| Test: configuration failure output | 7 and 10 |
| Test: resume argument handling | 7, 8, 10 |
| AC: a user can start a research run from the command line | 7 + 10, verified by `python -m deep_research --help` and the manual smoke test |
| AC: progress visible without reading logs | 9 |
| AC: report path and trace URL printed when available | 9 |
| AC: testable with mocked `run_research()` | 10 — `main(runner=...)` |
| Parent spec: missing API keys fail fast | 7 (`load_settings` forces `strict=True`) |

**2. Placeholder scan** — every code step carries the actual code. No "TBD", no "add error handling", no "similar to Task N". The one narrative aside (the discarded guard import in Task 7 Step 1) is explicitly labelled illustrative and the real file follows in full.

**3. Type consistency**

- `ResearchOutcome` is constructed only in `build_outcome` (Task 4) and in test helpers, with the same eight fields everywhere: `session_id`, `question`, `status`, `state`, `trace_url`, `report_path`, `token_usage`, `tool_calls`.
- `ResearchRuntime` fields — `session_id`, `settings`, `tracker`, `graph`, `long_term`, `procedural` — match between Task 6's implementation, Task 6's tests, and Task 7's `fake_builder`.
- `runtime_builder` is called as `await runtime_builder(settings, session_id=...)` in Task 7 and every test double accepts exactly that plus `**kwargs`, matching `build_runtime`'s real signature.
- `configuration_error(reason=..., message=...)` and `ResearchConfigurationError.reason` / `.hint` are identical in Tasks 1, 6, 7, and 10.
- `LongTermMemoryBridge(memory, session_id=...)` is constructed the same way in Tasks 2, 5, and 6.
- `AGENT_NAMES` (Task 6) is asserted equal to `graph.NODE_NAMES[:6]` in Task 11, which is what keeps agent names, node names, and scratchpad names from drifting apart.
- The CLI's `runner(...)` keyword set — `question`, `resume_session_id`, `config_path`, `max_iterations`, `output_format` — is a subset of `run_research`'s keyword parameters, asserted exactly in Task 10's `test_the_cli_passes_every_option_through_to_run_research`.

**Known risks the implementer should watch**

- `ProceduralMemory.load()` is awaited inside `build_runtime`. If a corrupt `memory/strategies.json` is quarantined it emits a recoverable error into the memory's own error log, which nothing currently drains into `state.errors`. Out of scope for spec 12 (the errors are still traced), but worth a line in spec 15's hardening pass.
- `LongTermMemory.drain_errors()` is likewise never drained into `ResearchState.errors` by this plan. The tools already record their own failures through `ToolResult`, so no user-visible information is lost, but the same spec 15 note applies.
- `WebSearchTool` constructs `TavilyClient(api_key=...)` eagerly. With `load_config(strict=True)` the key is guaranteed present, so this cannot fail in the CLI path; a future non-strict caller would see a Tavily SDK exception rather than a `ResearchConfigurationError`.
