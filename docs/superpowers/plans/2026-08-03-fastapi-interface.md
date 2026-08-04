# FastAPI Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an in-process FastAPI interface that starts research sessions, reports non-blocking status, streams typed progress over SSE, and exposes Markdown reports and trace metadata.

**Architecture:** Extend the shared async `run_research(...)` entry point with validated per-run configuration overrides and an optional synchronous progress callback. A process-local `SessionStore` owns background tasks and immutable API snapshots; FastAPI routes remain thin adapters around that store. API requests use the existing observability tracker with session and route trace metadata, while all client-visible and recorded errors use safe enumerated messages.

**Tech Stack:** Python 3.11+, FastAPI, Starlette `StreamingResponse`, Pydantic v2, asyncio, existing LangGraph runtime, existing observability tracker, pytest, pytest-asyncio, httpx/TestClient, Ruff.

## Global Constraints

- Preserve `requires-python = ">=3.11"`.
- Add exactly one runtime dependency: `fastapi>=0.115`; do not add an SSE package or deployment server.
- Keep `deep_research.main.run_research(...)` as the shared async entry point; `run_research_sync(...)` remains the CLI-only adapter.
- Only `output_format="markdown"` is accepted; report responses use `text/markdown`.
- Use process-local background tasks and memory only. No authentication, multi-tenant authorization, database queue, persistence, or deployment configuration.
- POST configuration overrides are JSON-safe, nested, validated against `ConfigSettings`, and applied with precedence YAML < environment < request override.
- Missing or invalid service configuration returns a safe HTTP 500 without file contents, secret values, provider text, or traceback data.
- Invalid request bodies and invalid override shapes return 422; unknown session IDs return 404.
- Recorded `ResearchEvent.metadata` and `ResearchError.details` contain identifiers, counts, routes, status codes, and enumerated reasons only—never exception messages, request bodies, report text, or secrets.
- Tests must not call OpenAI, Tavily, LangSmith, ChromaDB, or any live network service.
- Background-task cancellation during application shutdown must propagate as cancellation, not become a failed research session.

---

## Concrete File Map

| File | Change | Responsibility |
|---|---|---|
| `pyproject.toml` | Modify | Add `fastapi>=0.115`. |
| `src/deep_research/utils/config.py` | Modify | Validate and deep-merge JSON-safe per-run configuration overrides. |
| `src/deep_research/main.py` | Modify | Accept overrides and a typed progress handler; expose configuration preflight. |
| `src/deep_research/graph/orchestrator.py` | Modify | Use LangGraph value streaming when a progress handler is supplied and publish each new `ResearchEvent` once. |
| `src/deep_research/observability/context.py` | Modify | Add optional API route metadata to `TraceContext`. |
| `src/deep_research/observability/metrics.py` | Modify | Add `ApiMetric`. |
| `src/deep_research/observability/tracker.py` | Modify | Add `api_request_span(...)` and public typed-event recording. |
| `src/deep_research/observability/__init__.py` | Modify | Export `ApiMetric`. |
| `src/deep_research/api/__init__.py` | Create | Export `app` and `create_app`. |
| `src/deep_research/api/models.py` | Create | Define request, session, trace, validation-issue, and API-error response models. |
| `src/deep_research/api/events.py` | Create | Build safe API events and serialize `ResearchEvent` records as SSE frames. |
| `src/deep_research/api/sessions.py` | Create | Own process-local sessions, background tasks, event publication, snapshots, and shutdown. |
| `src/deep_research/api/app.py` | Create | Build the FastAPI app, trace dependencies, exception handlers, and five routes. |
| `tests/test_api/__init__.py` | Create | Mark API tests as a package. |
| `tests/test_api/fakes.py` | Create | Provide offline outcomes, scripted runners, and gated runners. |
| `tests/test_api/test_sessions.py` | Create | Test non-blocking lifecycle, progress updates, completion, and safe failures. |
| `tests/test_api/test_app.py` | Create | Test POST, status, 422, 404, safe 500, and API observability. |
| `tests/test_api/test_stream_and_artifacts.py` | Create | Test SSE, Markdown report, trace response, and unfinished-report conflict. |
| `tests/test_config.py` | Modify | Test nested overrides, precedence, rejection, and immutability. |
| `tests/test_runtime/test_run_research.py` | Modify | Test override forwarding and live progress callbacks. |
| `tests/test_observability_context.py` | Modify | Test route metadata in trace context. |
| `tests/test_observability_metrics.py` | Modify | Test `ApiMetric` serialization and union membership. |
| `tests/test_observability_tracker.py` | Modify | Test API request spans and public event recording. |
| `README.md` | Modify | Document request/response contracts, status values, SSE framing, errors, and in-process limitations. |

---

### Task 1: Per-run configuration overrides

**Files:**
- Modify: `src/deep_research/utils/config.py`
- Modify: `src/deep_research/main.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_runtime/test_run_research.py`

**Interfaces:**
- Produces: `apply_config_overrides(settings: ConfigSettings, overrides: Mapping[str, JsonValue]) -> ConfigSettings`
- Produces: `load_config(config_path: str, strict: bool = False, *, overrides: Mapping[str, JsonValue] | None = None) -> ConfigSettings`
- Produces: `prepare_research_settings(*, config_path: str, output_format: str | None, config_overrides: Mapping[str, JsonValue] | None = None) -> ConfigSettings`
- Changes: `run_research(..., config_overrides: Mapping[str, JsonValue] | None = None, ...) -> ResearchOutcome`
- Preserves: all current callers that omit `config_overrides`.

- [ ] **Step 1: Write the failing override tests**

Add to `tests/test_config.py`:

```python
def test_request_overrides_deep_merge_after_environment(
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GRAPH_MAX_ITERATIONS", "4")

    settings = load_config(
        str(config_path),
        overrides={
            "graph": {"max_iterations": 2},
            "llm": {"model_overrides": {"critic": "gpt-4.1-mini"}},
        },
    )

    assert settings.graph.max_iterations == 2
    assert settings.llm.model == "gpt-4o"
    assert settings.llm.model_overrides == {
        "planner": "gpt-4o-mini",
        "critic": "gpt-4.1-mini",
    }


def test_unknown_config_override_is_rejected(config_path: Path) -> None:
    with pytest.raises(ValueError, match=r"graph\.iteration_limit"):
        load_config(
            str(config_path),
            overrides={"graph": {"iteration_limit": 5}},
        )


def test_config_override_does_not_mutate_the_original_settings() -> None:
    original = ConfigSettings()
    overridden = apply_config_overrides(
        original,
        {"output": {"directory": "api-output/"}},
    )

    assert original.output.directory == "output/"
    assert overridden.output.directory == "api-output/"
```

Add to `tests/test_runtime/test_run_research.py`:

```python
@pytest.mark.asyncio
async def test_run_research_applies_config_overrides(
    config_file,
    tracker,
) -> None:
    observed = {}

    async def builder(settings, *, session_id, **_ignored):
        observed["directory"] = settings.output.directory
        return await fake_builder(tracker)(settings, session_id=session_id)

    await run_research(
        QUESTION,
        config_path=config_file,
        config_overrides={"output": {"directory": "request-output/"}},
        runtime_builder=builder,
    )

    assert observed == {"directory": "request-output/"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m pytest tests/test_config.py::test_request_overrides_deep_merge_after_environment tests/test_config.py::test_unknown_config_override_is_rejected tests/test_config.py::test_config_override_does_not_mutate_the_original_settings tests/test_runtime/test_run_research.py::test_run_research_applies_config_overrides -v
```

Expected: FAIL because `load_config`, `run_research`, and `apply_config_overrides` do not yet accept or implement overrides.

- [ ] **Step 3: Implement validated deep merging**

In `utils/config.py`, import `deepcopy`, `Mapping`, and `JsonValue`, then add:

```python
_FREE_FORM_MAPPING_PATHS = frozenset({("llm", "model_overrides")})


def _merge_override_payload(
    target: dict[str, Any],
    overrides: Mapping[str, JsonValue],
    *,
    path: tuple[str, ...] = (),
) -> None:
    for key, value in overrides.items():
        current_path = (*path, key)
        if key not in target:
            raise ValueError(
                f"unknown config override: {'.'.join(current_path)}"
            )

        current = target[key]
        if current_path in _FREE_FORM_MAPPING_PATHS:
            if isinstance(current, dict) and isinstance(value, Mapping):
                current.update(deepcopy(dict(value)))
            else:
                target[key] = deepcopy(value)
        elif isinstance(current, dict) and isinstance(value, Mapping):
            _merge_override_payload(current, value, path=current_path)
        else:
            target[key] = deepcopy(value)


def apply_config_overrides(
    settings: ConfigSettings,
    overrides: Mapping[str, JsonValue],
) -> ConfigSettings:
    payload = settings.model_dump(mode="python")
    _merge_override_payload(payload, overrides)
    return ConfigSettings.model_validate(payload)
```

Change `load_config` so environment overrides are parsed first, request overrides are applied second, and strict secret validation observes the final settings:

```python
def load_config(
    config_path: str,
    strict: bool = False,
    *,
    overrides: Mapping[str, JsonValue] | None = None,
) -> ConfigSettings:
    # Existing file, dotenv, YAML, and environment handling remains.
    settings = ConfigSettings.model_validate(raw_config)
    if overrides:
        settings = apply_config_overrides(settings, overrides)
    if strict:
        _validate_runtime_secrets(
            tracing_enabled=settings.langsmith.tracing_enabled
        )
    return settings
```

In `main.py`, add:

```python
ProgressHandler: TypeAlias = Callable[[ResearchEvent], None]


def prepare_research_settings(
    *,
    config_path: str,
    output_format: str | None,
    config_overrides: Mapping[str, JsonValue] | None = None,
) -> ConfigSettings:
    settings = load_settings(
        config_path,
        config_overrides=config_overrides,
    )
    resolve_output_format(
        output_format,
        configured=settings.output.default_format,
    )
    return settings
```

Change `load_settings(...)` to forward `overrides=config_overrides`, and change `run_research(...)` to call `prepare_research_settings(...)`. Do not alter `run_research_sync`.

- [ ] **Step 4: Run focused and compatibility tests**

Run:

```bash
python -m pytest tests/test_config.py tests/test_runtime/test_run_research.py tests/test_cli -q
```

Expected: PASS; CLI tests prove omitted overrides remain backward compatible.

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/utils/config.py src/deep_research/main.py tests/test_config.py tests/test_runtime/test_run_research.py
git commit -m "feat: support per-run config overrides"
```

---

### Task 2: Live typed progress publication

**Files:**
- Modify: `src/deep_research/graph/orchestrator.py`
- Modify: `src/deep_research/main.py`
- Modify: `tests/test_runtime/test_run_research.py`
- Modify: `tests/test_graph/test_session.py`

**Interfaces:**
- Consumes: `ProgressHandler = Callable[[ResearchEvent], None]`
- Changes: `run_research_graph(..., event_handler: ProgressHandler | None = None) -> GraphRun`
- Changes: `resume_research_graph(..., event_handler: ProgressHandler | None = None) -> GraphRun`
- Changes: `run_research(..., event_handler: ProgressHandler | None = None, ...) -> ResearchOutcome`
- Guarantee: each cumulative state event is delivered once, in state order; `graph.session.completed` is delivered last.
- Guarantee: when `event_handler is None`, retain the current `graph.ainvoke(...)` execution path.
- Acceptance criterion: resuming a terminal checkpoint with an `event_handler` succeeds when `graph.astream(...)` yields no snapshots. It must not rerun graph nodes; it must publish checkpointed events exactly once in state order, append and publish exactly one `graph.session.completed` event last, and return that terminal state. A zero-snapshot fresh run or nonterminal resume must still raise `RuntimeError("research graph produced no state")`.

- [ ] **Step 1: Write the failing progress test**

Add to `tests/test_runtime/test_run_research.py`:

```python
@pytest.mark.asyncio
async def test_run_research_publishes_typed_progress_in_order(
    config_file,
    tracker,
) -> None:
    received = []

    outcome = await run_research(
        QUESTION,
        config_path=config_file,
        runtime_builder=fake_builder(tracker),
        event_handler=received.append,
    )

    assert received
    assert all(isinstance(event, ResearchEvent) for event in received)
    assert received[0].event_type == "graph.session.started"
    assert any(
        event.event_type == "graph.node.started"
        and event.metadata["node"] == "planner"
        for event in received
    )
    assert received[-1].event_type == "graph.session.completed"
    assert received == outcome.state.events
```

Import `ResearchEvent` from `deep_research.utils.types`.

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
python -m pytest tests/test_runtime/test_run_research.py::test_run_research_publishes_typed_progress_in_order -v
```

Expected: FAIL because `run_research` does not accept `event_handler`.

- [ ] **Step 3: Implement event-aware graph execution**

In `graph/orchestrator.py`, define the same callable alias locally to avoid importing `main.py`:

```python
from collections.abc import Callable

from deep_research.utils.types import ResearchEvent

ProgressHandler = Callable[[ResearchEvent], None]
```

Add a helper:

```python
async def _stream_graph_result(
    *,
    graph: Any,
    channel: ResearchGraphState | None,
    config: dict[str, Any],
    event_handler: ProgressHandler,
) -> ResearchGraphState:
    latest: ResearchGraphState | None = None
    published = 0

    if channel is not None:
        initial = load_state(channel)
        for event in initial.events:
            event_handler(event)
        published = len(initial.events)

    async for snapshot in graph.astream(
        channel,
        config,
        stream_mode="values",
    ):
        latest = snapshot
        state = load_state(snapshot)
        for event in state.events[published:]:
            event_handler(event)
        published = len(state.events)

    if latest is None:
        raise RuntimeError("research graph produced no state")
    return latest
```

Change `_invoke(...)`:

```python
config = session_config(session_id, max_iterations=max_iterations)
if event_handler is None:
    result = await graph.ainvoke(channel, config)
else:
    result = await _stream_graph_result(
        graph=graph,
        channel=channel,
        config=config,
        event_handler=event_handler,
    )
```

After appending `session_completed_event`, call `event_handler(final.events[-1])` once. Thread the parameter through `run_research_graph`, `resume_research_graph`, and `main.run_research`.

- [ ] **Step 4: Run graph and runtime tests**

Run:

```bash
python -m pytest tests/test_runtime/test_run_research.py tests/test_graph -q
```

Expected: PASS, including existing graph behavior without a handler.

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/graph/orchestrator.py src/deep_research/main.py tests/test_runtime/test_run_research.py
git commit -m "feat: publish live research progress events"
```

---

### Task 3: Typed API models and in-memory session lifecycle

**Files:**
- Create: `src/deep_research/api/models.py`
- Create: `src/deep_research/api/sessions.py`
- Create: `tests/test_api/__init__.py`
- Create: `tests/test_api/fakes.py`
- Create: `tests/test_api/test_sessions.py`

**Interfaces:**
- Consumes: async runners compatible with `run_research(...)`, including `session_id`, `config_overrides`, and `event_handler`.
- Produces: `ResearchRequest`
- Produces: `ResearchSessionResponse`
- Produces: `TraceResponse`
- Produces: `SessionStore.start(...) -> ResearchSession`
- Produces: `SessionStore.require(session_id: str) -> ResearchSession`
- Produces: `SessionStore.iter_events(session_id: str) -> AsyncIterator[ResearchEvent]`
- Produces: `SessionStore.close() -> Awaitable[None]`
- Status type: `Literal["running", "completed", "max_iterations", "incomplete", "failed"]`
- Acceptance criterion: when a running session task is cancelled, `asyncio.CancelledError` still propagates, the session status remains `running` because cancellation has no public status value, and `finished_at` is set; `iter_events(...)` must drain stored events and terminate once cancellation has completed (`finished_at` is set and the task is done) instead of waiting forever.

- [ ] **Step 1: Write offline runner fakes and failing lifecycle tests**

In `tests/test_api/fakes.py`, define `make_outcome`, `ScriptedRunner`, and `GateRunner`. All outcomes must construct real `ResearchOutcome`, `ResearchState`, and `TokenUsage` objects; no provider/runtime fakes are needed.

In `tests/test_api/test_sessions.py` add:

```python
@pytest.mark.asyncio
async def test_start_is_non_blocking_and_progress_updates_status() -> None:
    runner = GateRunner()
    store = SessionStore(runner=runner)

    session = store.start(
        session_id="session-1",
        query="How mature is quantum error correction?",
        max_iterations=2,
        output_format="markdown",
        config_overrides={"output": {"directory": "api-output/"}},
        config_path="config.yaml",
    )
    await runner.started.wait()

    assert session.status == "running"
    assert session.current_agent == "planner"
    assert session.iteration == 1
    assert runner.calls[0]["config_overrides"] == {
        "output": {"directory": "api-output/"}
    }

    runner.release.set()
    assert session.task is not None
    await session.task

    assert session.status == "completed"
    assert session.current_agent is None
    assert session.finished_at is not None
    assert session.report_path == "report-session-1.md"


@pytest.mark.asyncio
async def test_event_iterator_replays_events_and_stops_at_terminal_status() -> None:
    runner = ScriptedRunner(
        events=[
            ResearchEvent(
                event_type="graph.node.started",
                source="graph.planner",
                message="Node planner started.",
                metadata={"node": "planner", "iteration": 0},
            )
        ]
    )
    store = SessionStore(runner=runner)
    store.start(
        session_id="session-1",
        query="Question",
        max_iterations=None,
        output_format="markdown",
        config_overrides={},
        config_path="config.yaml",
    )

    received = [
        event async for event in store.iter_events("session-1")
    ]

    assert [event.event_type for event in received] == [
        "graph.node.started"
    ]
    assert store.require("session-1").status == "completed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m pytest tests/test_api/test_sessions.py -v
```

Expected: collection FAIL because `deep_research.api.models` and `deep_research.api.sessions` do not exist.

- [ ] **Step 3: Implement strict API models**

In `models.py`, use `ConfigDict(extra="forbid", str_strip_whitespace=True, validate_default=True)` for every request/response model.

Define:

```python
SessionStatus = Literal[
    "running",
    "completed",
    "max_iterations",
    "incomplete",
    "failed",
]


class ResearchRequest(ApiModel):
    query: str = Field(min_length=1)
    max_iterations: int | None = Field(default=None, ge=1)
    output_format: Literal["markdown"] = "markdown"
    config_overrides: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("config_overrides")
    @classmethod
    def validate_config_overrides(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        apply_config_overrides(ConfigSettings(), value)
        return value


class ResearchSessionResponse(ApiModel):
    session_id: str = Field(min_length=1)
    status: SessionStatus
    current_agent: str | None = None
    iteration: int = Field(ge=0)
    started_at: datetime
    finished_at: datetime | None = None
    report_path: str | None = None
    trace_url: str | None = None
    errors: list[ResearchError] = Field(default_factory=list)


class TraceMetadata(ApiModel):
    session_id: str = Field(min_length=1)
    route: str = Field(min_length=1)
    status: SessionStatus


class TraceResponse(ApiModel):
    session_id: str = Field(min_length=1)
    trace_url: str | None = None
    metadata: TraceMetadata
```

Also define strict `ValidationIssue`, `ApiErrorBody`, and `ApiErrorResponse`; validation issues expose only `location` and Pydantic’s enumerated `type`, never rejected input values.

- [ ] **Step 4: Implement the session store**

In `sessions.py`, define:

```python
TERMINAL_STATUSES = frozenset(
    {"completed", "max_iterations", "incomplete", "failed"}
)
ResearchRunner = Callable[..., Awaitable[ResearchOutcome]]


@dataclass(slots=True)
class ResearchSession:
    session_id: str
    query: str
    status: SessionStatus
    started_at: datetime
    current_agent: str | None = None
    iteration: int = 0
    finished_at: datetime | None = None
    report_path: str | None = None
    trace_url: str | None = None
    errors: list[ResearchError] = field(default_factory=list)
    events: list[ResearchEvent] = field(default_factory=list)
    outcome: ResearchOutcome | None = None
    task: asyncio.Task[None] | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def publish(self, event: ResearchEvent) -> None:
        self.events.append(event.model_copy(deep=True))
        node = event.metadata.get("node")
        iteration = event.metadata.get("iteration")
        if event.event_type == "graph.node.started" and isinstance(node, str):
            self.current_agent = node
        if isinstance(iteration, int):
            self.iteration = iteration
        self.changed.set()
```

`SessionStore.start(...)` must create a unique running record synchronously and assign `asyncio.create_task(self._run(...))`. `_run(...)` must:

1. Pass `event_handler=session.publish` to the runner.
2. Copy status, report path, trace URL, errors, and final iteration from `ResearchOutcome`.
3. Convert `ResearchConfigurationError` into a safe non-recoverable `ResearchError` and an `api.research.configuration_error` event containing only `reason`.
4. Convert unexpected `Exception` into `api.research.failed` with only `exception_type`.
5. Let `asyncio.CancelledError` propagate.
6. Set `finished_at`, clear `current_agent`, and signal `changed` in `finally`.

`iter_events(...)` must replay stored events from index zero, await `changed` while running, and exit only after all events have been yielded and status is terminal. `close()` cancels and gathers unfinished tasks with `return_exceptions=True`.

For the cancellation exception above, a finished cancelled task is an explicit iterator termination condition even though its status remains `running`.

- [ ] **Step 5: Run session tests**

Run:

```bash
python -m pytest tests/test_api/test_sessions.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/api/models.py src/deep_research/api/sessions.py tests/test_api/__init__.py tests/test_api/fakes.py tests/test_api/test_sessions.py
git commit -m "feat: add in-memory research session lifecycle"
```

---

### Task 4: FastAPI start/status routes, validation, and API tracing

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/deep_research/observability/context.py`
- Modify: `src/deep_research/observability/metrics.py`
- Modify: `src/deep_research/observability/tracker.py`
- Modify: `src/deep_research/observability/__init__.py`
- Create: `src/deep_research/api/events.py`
- Create: `src/deep_research/api/app.py`
- Create: `src/deep_research/api/__init__.py`
- Modify: `tests/test_observability_context.py`
- Modify: `tests/test_observability_metrics.py`
- Modify: `tests/test_observability_tracker.py`
- Create: `tests/test_api/test_app.py`

**Interfaces:**
- Produces: `TraceContext.route: str | None`
- Produces: `ApiMetric(session_id, route, method, latency_ms, success, error_type)`
- Produces: `Tracker.api_request_span(session_id: str, route: str, method: str) -> AbstractAsyncContextManager[SpanHandle]`
- Produces: `Tracker.record_event(event: ResearchEvent) -> None`
- Produces: `api_error_event(...) -> ResearchEvent`
- Produces: `create_app(*, runner=run_research, config_path=DEFAULT_CONFIG_PATH, preflight=prepare_research_settings, tracker: Tracker | None = None) -> FastAPI`
- Produces: module-level `app = create_app()`
- Routes: `POST /research` returns 202; `GET /research/{session_id}/status` returns 200.

- [ ] **Step 1: Add the dependency**

Add to `project.dependencies` in `pyproject.toml`:

```toml
"fastapi>=0.115",
```

Install the updated editable environment:

```bash
python -m pip install -e ".[dev]"
```

Expected: installation succeeds without adding `uvicorn` or an SSE library.

- [ ] **Step 2: Write failing observability tests**

Add:

```python
def test_trace_context_includes_api_route_metadata() -> None:
    context = TraceContext(
        session_id="session-1",
        route="/research/{session_id}/status",
    )

    assert build_trace_metadata(context)["route"] == (
        "/research/{session_id}/status"
    )
```

```python
def test_api_metric_serializes_route_and_method() -> None:
    metric = ApiMetric(
        session_id="session-1",
        route="/research",
        method="POST",
        latency_ms=2.5,
        success=True,
    )

    assert metric.metric_type == "api"
    assert metric.method == "POST"
```

```python
@pytest.mark.asyncio
async def test_api_request_span_binds_session_and_route(tracker) -> None:
    async with tracker.api_request_span(
        "session-1",
        "/research/{session_id}/status",
        "GET",
    ):
        context = current_trace_context()
        assert context is not None
        assert context.session_id == "session-1"
        assert context.route == "/research/{session_id}/status"

    assert isinstance(tracker.metrics[-1], ApiMetric)
    assert tracker.events[-1].metadata["route"] == (
        "/research/{session_id}/status"
    )
```

- [ ] **Step 3: Write failing HTTP tests**

In `tests/test_api/test_app.py` add tests for:

```python
def test_post_starts_a_session_and_forwards_every_request_field() -> None:
    runner = ScriptedRunner()
    app = create_app(runner=runner, preflight=valid_preflight)

    with TestClient(app) as client:
        response = client.post(
            "/research",
            json={
                "query": "Quantum error correction",
                "max_iterations": 2,
                "output_format": "markdown",
                "config_overrides": {
                    "output": {"directory": "api-output/"}
                },
            },
        )

    assert response.status_code == 202
    body = response.json()
    assert body["session_id"]
    assert body["status"] == "running"
    assert body["iteration"] == 0
```

```python
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"query": "   "},
        {"query": "Question", "max_iterations": 0},
        {"query": "Question", "output_format": "pdf"},
        {
            "query": "Question",
            "config_overrides": {"graph": {"iteration_limit": 2}},
        },
    ],
)
def test_invalid_requests_return_safe_422(payload) -> None:
    app = create_app(
        runner=ScriptedRunner(),
        preflight=valid_preflight,
    )

    with TestClient(app) as client:
        response = client.post("/research", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "input" not in response.text
```

```python
def test_missing_configuration_returns_safe_500_and_records_an_event() -> None:
    def failing_preflight(**_kwargs):
        raise configuration_error(
            reason="missing_secrets",
            message="secret value sk-never-return-this",
        )

    app = create_app(
        runner=ScriptedRunner(),
        preflight=failing_preflight,
    )

    with TestClient(app) as client:
        response = client.post(
            "/research",
            json={"query": "Question"},
        )

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "configuration_error",
        "message": "Research service configuration is unavailable.",
        "reason": "missing_secrets",
        "issues": [],
    }
    assert "sk-never-return-this" not in response.text
    error_event = next(
        event
        for event in app.state.api_tracker.events
        if event.event_type == "api.request.error"
    )
    assert error_event.metadata["status_code"] == 500
    assert error_event.metadata["reason"] == "missing_secrets"
```

- [ ] **Step 4: Verify initial failures**

Run:

```bash
python -m pytest tests/test_observability_context.py tests/test_observability_metrics.py tests/test_observability_tracker.py tests/test_api/test_app.py -v
```

Expected: FAIL because route context, `ApiMetric`, API spans, and the app do not exist.

- [ ] **Step 5: Extend observability**

Add `route: str | None = Field(default=None, min_length=1)` to `TraceContext`.

Add:

```python
class ApiMetric(OutcomeMetric):
    metric_type: Literal["api"] = "api"
    session_id: str = Field(min_length=1)
    route: str = Field(min_length=1)
    method: str = Field(min_length=1)
```

Include `ApiMetric` in `MetricRecord` and observability exports. Add `"api"` to `SpanKind`.

Implement:

```python
def record_event(self, event: ResearchEvent) -> None:
    self._events.append(event.model_copy(deep=True))


def api_request_span(
    self,
    session_id: str,
    route: str,
    method: str,
) -> AbstractAsyncContextManager[SpanHandle]:
    context = TraceContext(session_id=session_id, route=route)
    normalized_method = _validate_non_empty_string(method).upper()

    def metric_factory(
        ctx,
        latency,
        success,
        error_type,
        trace_url,
        handle,
    ):
        del trace_url, handle
        return ApiMetric(
            session_id=ctx.session_id,
            route=route,
            method=normalized_method,
            latency_ms=latency,
            success=success,
            error_type=error_type,
        )

    return self._span(
        kind="api",
        name=f"api.{normalized_method.casefold()}",
        run_type="chain",
        context=context,
        inputs={},
        metric_factory=metric_factory,
    )
```

- [ ] **Step 6: Implement safe events and the first routes**

In `api/events.py`, implement `api_error_event(...)` with:

- `event_type="api.request.error"`
- `source="api"`
- `message="API request failed."`
- metadata: `session_id`, route template, method, status code, error code, and optional enumerated reason.

In `api/app.py`:

1. Create the default local-only API tracker with `LangSmithRuntimeConfig(tracing_enabled=False, project="deep-research-api", api_key=None)`.
2. Add a yield dependency that chooses the path `session_id` or generates one for POST, reads `request.scope["route"].path`, stores both on `request.state`, and wraps the route in `tracker.api_request_span(...)`.
3. Add custom handlers for `RequestValidationError` and an internal `ApiProblem`; both call `tracker.record_event(api_error_event(...))`.
4. Validation responses include only issue locations and error types.
5. POST calls `preflight(...)` before storing or scheduling a session.
6. POST returns `ResearchSessionResponse` with status code 202.
7. Status lookup returns 404 through `ApiProblem` when absent.
8. Add a FastAPI lifespan that calls `await store.close()` on shutdown.
9. Assign `app.state.session_store` and `app.state.api_tracker` for inspection.

Export `app` and `create_app` from `api/__init__.py`.

- [ ] **Step 7: Run focused tests**

Run:

```bash
python -m pytest tests/test_observability_context.py tests/test_observability_metrics.py tests/test_observability_tracker.py tests/test_api/test_app.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/deep_research/observability src/deep_research/api/events.py src/deep_research/api/app.py src/deep_research/api/__init__.py tests/test_observability_context.py tests/test_observability_metrics.py tests/test_observability_tracker.py tests/test_api/test_app.py
git commit -m "feat: expose research start and status API"
```

---

### Task 5: SSE, report, trace, documentation, and full verification

**Files:**
- Modify: `src/deep_research/api/events.py`
- Modify: `src/deep_research/api/app.py`
- Create: `tests/test_api/test_stream_and_artifacts.py`
- Modify: `tests/test_api/test_app.py`
- Modify: `README.md`

**Interfaces:**
- Produces: `encode_sse(event: ResearchEvent, *, event_id: int) -> str`
- Route: `GET /research/{session_id}/stream` returns `text/event-stream`
- Route: `GET /research/{session_id}/report` returns Markdown or 409 while unfinished
- Route: `GET /research/{session_id}/trace` returns `TraceResponse`
- Guarantee: a late SSE subscriber receives retained events from event zero and exits after terminal completion.
- Guarantee: all four GET endpoints return the same safe 404 for unknown sessions.

- [ ] **Step 1: Write failing SSE and artifact tests**

In `tests/test_api/test_stream_and_artifacts.py` add:

```python
def test_stream_returns_typed_progress_as_sse() -> None:
    event = ResearchEvent(
        event_type="graph.node.started",
        source="graph.planner",
        message="Node planner started.",
        metadata={"node": "planner", "iteration": 0},
    )
    app = create_app(
        runner=ScriptedRunner(events=[event]),
        preflight=valid_preflight,
    )

    with TestClient(app) as client:
        created = client.post(
            "/research",
            json={"query": "Question"},
        ).json()
        with client.stream(
            "GET",
            f"/research/{created['session_id']}/stream",
        ) as response:
            body = "".join(response.iter_text())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "text/event-stream"
    )
    assert "id: 1\n" in body
    assert "event: graph.node.started\n" in body
    data_line = next(
        line for line in body.splitlines() if line.startswith("data: ")
    )
    payload = json.loads(data_line.removeprefix("data: "))
    assert ResearchEvent.model_validate(payload) == event
```

```python
def test_report_returns_authoritative_markdown() -> None:
    app = create_app(
        runner=ScriptedRunner(report="# Final report\n\nEvidence."),
        preflight=valid_preflight,
    )

    with TestClient(app) as client:
        session_id = client.post(
            "/research",
            json={"query": "Question"},
        ).json()["session_id"]
        wait_until_terminal(client, session_id)
        response = client.get(f"/research/{session_id}/report")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.text == "# Final report\n\nEvidence."
```

```python
def test_trace_returns_url_and_route_metadata() -> None:
    app = create_app(
        runner=ScriptedRunner(
            trace_url="https://smith.example/r/session-1"
        ),
        preflight=valid_preflight,
    )

    with TestClient(app) as client:
        session_id = client.post(
            "/research",
            json={"query": "Question"},
        ).json()["session_id"]
        wait_until_terminal(client, session_id)
        response = client.get(f"/research/{session_id}/trace")

    assert response.status_code == 200
    assert response.json() == {
        "session_id": session_id,
        "trace_url": "https://smith.example/r/session-1",
        "metadata": {
            "session_id": session_id,
            "route": "/research/{session_id}/trace",
            "status": "completed",
        },
    }
```

Add a parametrized unknown-session test to `test_app.py` covering `/status`, `/stream`, `/report`, and `/trace`, each expecting 404 and `error.code == "session_not_found"`.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m pytest tests/test_api/test_stream_and_artifacts.py tests/test_api/test_app.py -v
```

Expected: FAIL with 404 for the three unimplemented routes and missing `encode_sse`.

- [ ] **Step 3: Implement SSE framing**

In `events.py`:

```python
def encode_sse(event: ResearchEvent, *, event_id: int) -> str:
    if event_id < 1:
        raise ValueError("event_id must be at least 1")
    return (
        f"id: {event_id}\n"
        f"event: {event.event_type}\n"
        f"data: {event.model_dump_json()}\n\n"
    )
```

In `app.py`, implement stream response:

```python
async def body():
    event_id = 0
    async for event in store.iter_events(session_id):
        event_id += 1
        yield encode_sse(event, event_id=event_id)

return StreamingResponse(
    body(),
    media_type="text/event-stream",
    headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    },
)
```

Do not emit report text, exception text, or secret-bearing request data in SSE metadata.

- [ ] **Step 4: Implement report and trace routes**

Report behavior:

- Unknown session: 404.
- `outcome is None`: 409 with `code="session_not_complete"`.
- Finished outcome with `report is None`: 409 with `code="report_unavailable"`.
- Otherwise return `Response(outcome.report, media_type="text/markdown")`.

Trace behavior:

- Return 200 for known sessions, including running sessions with `trace_url=null`.
- Include `session_id`, current status, and the route template `/research/{session_id}/trace`.

- [ ] **Step 5: Document the API**

Update `README.md` with:

- The five endpoint methods and paths.
- A complete POST JSON example using `query`, `max_iterations`, `output_format`, and `config_overrides`.
- The 202 response fields.
- Status values and the non-blocking running behavior.
- SSE framing using `ResearchEvent` JSON.
- Markdown report media type.
- Trace response fields.
- 422, 404, 409, and safe 500 semantics.
- The process-local limitation: sessions, tasks, and event history disappear when the process exits.
- Explicit statements that authentication, durable queues, databases, and deployment setup remain out of scope.
- Update the phase summary to mark the FastAPI API complete while leaving Streamlit next.

- [ ] **Step 6: Run focused API tests**

Run:

```bash
python -m pytest tests/test_api -q
```

Expected: PASS with no provider or network calls.

- [ ] **Step 7: Run the complete verification suite**

Run:

```bash
python -m pytest -q
python -m ruff check src tests
```

Expected: all tests pass and Ruff reports no errors.

Also verify dependency and import contracts:

```bash
python -c "from deep_research.api import app; print(type(app).__name__)"
python -c "from deep_research.main import run_research_sync; print(callable(run_research_sync))"
```

Expected output includes `FastAPI` and `True`.

- [ ] **Step 8: Commit**

```bash
git add src/deep_research/api/events.py src/deep_research/api/app.py tests/test_api/test_stream_and_artifacts.py tests/test_api/test_app.py README.md
git commit -m "feat: stream progress and expose research artifacts"
```

---

## Dependency Changes

Add exactly:

```toml
"fastapi>=0.115",
```

No additions for:

- `uvicorn`—deployment is out of scope.
- `sse-starlette`—Starlette’s existing `StreamingResponse` is sufficient.
- Test HTTP clients—`httpx>=0.27` already exists.
- Async testing—`pytest-asyncio>=0.23` already exists.

Refresh the editable environment with:

```bash
python -m pip install -e ".[dev]"
```

## Exact Testing Commands

```bash
python -m pytest tests/test_config.py tests/test_runtime/test_run_research.py -q
python -m pytest tests/test_graph -q
python -m pytest tests/test_observability_context.py tests/test_observability_metrics.py tests/test_observability_tracker.py -q
python -m pytest tests/test_api -q
python -m pytest tests/test_cli -q
python -m pytest -q
python -m ruff check src tests
```

## Final Spec Coverage Self-Review

| Requirement | Coverage |
|---|---|
| FastAPI app | Task 4, `api/app.py` and exported module-level `app` |
| `POST /research` | Task 4 |
| Query, max iterations, output format, config overrides | Tasks 1, 3, 4 |
| In-process background execution | Task 3 |
| Non-blocking session status | Tasks 3 and 4 |
| Session ID, status, current agent, iteration, timestamps, report path, trace URL, errors | Task 3 models and lifecycle |
| SSE progress from typed `ResearchEvent` | Tasks 2, 3, 5 |
| Report endpoint and Markdown-only output | Task 5 |
| Trace endpoint | Task 5 |
| 422 validation | Tasks 3 and 4 |
| 404 unknown session | Tasks 4 and 5 |
| Safe 500 configuration failure | Task 4 |
| Structured API error events | Tasks 3 and 4 |
| Session and route trace metadata | Task 4 |
| No live providers in tests | Global Constraints and all API fakes |
| No auth, database queue, or deployment scope | Global Constraints and README |
| Reports retrievable by session ID | Task 5 |
| Tests for start, status, stream, report, unknown session, and validation | Tasks 3–5 |

No specification gap remains. A 409 response for unfinished or unavailable reports is an explicit boundary needed to avoid treating a known running session as unknown.

## Type Consistency Self-Review

- `config_overrides` is consistently `Mapping[str, JsonValue] | None` in configuration/runtime code and `dict[str, JsonValue]` in the validated HTTP model.
- `event_handler` is consistently a synchronous `Callable[[ResearchEvent], None]`; this permits graph callbacks to update the process-local session without awaiting inside LangGraph iteration.
- Graph and API status values use the existing final statuses plus only the API-owned transient `"running"` value.
- `ResearchSession.outcome`, report responses, trace URLs, errors, and event streams all derive from the existing `ResearchOutcome`, `ResearchState`, `ResearchError`, and `ResearchEvent` contracts.
- API timestamps are timezone-aware `datetime` values; FastAPI serializes them as ISO 8601.
- SSE payloads use `ResearchEvent.model_dump_json()` and therefore round-trip through `ResearchEvent.model_validate(...)`.
- `run_research_sync(...)` remains signature-compatible because it forwards keyword arguments and existing CLI callers do not supply the new optional parameters.
- `ApiMetric` is added to `MetricRecord`, while `ResearchOutcome` token/tool aggregation continues to ignore unrelated API metrics by type.
