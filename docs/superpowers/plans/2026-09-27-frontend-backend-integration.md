# Front end to back end — Next.js console on the FastAPI service — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the approved front-end design to the running service: a minimal API slice (start command with an offline **replay mode**, the E1 evidence endpoint, the `query` echo, `GET /research`, the `/status.iteration` fix, a mode header) and the real Next.js 16 console in `web/` that renders the design from the live API through a same-origin streaming proxy — proven end to end for free in replay mode, then once for real, off-peak.

**Architecture:** The Python side grows inside `src/deep_research/api/` only: `sessions.py` and `models.py` gain the small additive changes, `app.py` gains a mode header middleware and two routes, `evidence.py` builds the E1 JSON from the run's own `ReportComposition` using the evidence log's helpers, `replay.py` wraps the e2e replay harness into an async runner that satisfies `SessionStore`'s runner contract (paced through a queue on the server's one event loop) plus a pure-ASGI middleware that reads `X-Replay-Case` and rewrites the request's `query` to the case's own question, and `__main__.py` is the start command that composes one app and, in replay mode, wraps the whole server process in the harness's `offline_credentials()` and `network_denied()` guards. The web side is one Next.js App Router app: the prototype's CSS moved verbatim, its event core ported unchanged into `lib/run-state.ts`, a `fetch`-based SSE reader, a typed client, a streaming proxy route handler, and one client component per prototype surface driven by `/status` and the stream. Tests run at four layers (pytest, Vitest, Testing Library, Playwright against the API in replay mode) with four full-page visual checkpoints, then one live run.

**Tech Stack:** Python 3.12 (`.venv`), FastAPI 0.141 / Starlette 1.3 / uvicorn 0.52 (uvicorn newly declared), pydantic 2, pytest + pytest-asyncio; Node v24.13.1, npm 11.8.0, Next.js 16.3.x App Router + TypeScript, React 19, `react-markdown` 10 + `remark-gfm` 4, Vitest 5 + Testing Library, Playwright (Chromium). Windows workstation; every check is a Node script or a pytest/Vitest/Playwright command, never `bash file.sh`.

**Spec:** `docs/superpowers/specs/2026-09-27-frontend-backend-integration-design.md` (§2 decisions Q1–Q4 and §2.1 rulings R1–R5 are closed — do not reopen them). Read it alongside this plan; section numbers below (§3.3, §4.2 A2, §4.5 T-A2b, AC5, …) are the spec's unless prefixed `DESIGN.md` or `index.html`. This plan is `docs/superpowers/plans/2026-09-27-frontend-backend-integration.md`.

## Global Constraints

- Worktree: `C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.worktrees/frontend-backend`, branch `feat/frontend-backend-integration` (cut from `origin/main` `4e10823`). Every path below is relative to that root; every command runs from it unless a step says `cd web`. Commit after every task. Tasks run **one after another**, never two at once: they share one worktree and one `.git/index.lock`.
- The venv interpreter is `C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe` (the main checkout's venv; the worktree has none). Every Python command below uses it **by absolute path** with `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1` in front. In shell blocks it is the variable `PY`, set at the top of the block to that absolute path.
- Never run the live CLI (`python -m deep_research "…"`), never call a model provider, never read `.env`. Every Python run below is either pytest or the API in `--mode replay`, which is scripted and network-denied. The only live-provider run is Task 21, once, under its own rules.
- Scope (spec §1): `src/deep_research/api/` (new `__main__.py`, `replay.py`, `evidence.py`; edited `app.py`, `models.py`, `sessions.py`), `pyproject.toml` (`uvicorn>=0.30`), `tests/test_api/` (new tests; the five `query=` edits in `test_sessions.py`), `web/` (new), `.gitignore`, `README.md`, `docs/design/api-gaps.md`, `docs/design/DESIGN.md`. **No file under `src/deep_research/agents/`, `graph/`, `runtime/` or `utils/` changes** (R2; AC5's empty-diff check). Nothing else under `docs/design/` changes (AC20).
- API contract (spec §4.2): `query: str` on `ResearchSessionResponse`; `GET /research?limit=` (default 20, `ge=1`, `le=200`) → `{"sessions": [...]}` newest first; `GET /research/{id}/evidence?format=json|markdown` (404 / 409 `session_not_complete` / 409 `evidence_unavailable` / 422); `X-Deep-Research-Mode: live|replay` on every response; `publish` updates `iteration` only for `graph.*` events; an unknown `X-Replay-Case` fails the session through `configuration_error(reason="config_invalid", …)` (R1).
- Replay mode (spec §4.2 A1–A2): `python -m deep_research.api --mode replay [--host 127.0.0.1] [--port 8000] [--replay-case missing-target-triggers-one-extra-pass] [--replay-delay-ms 150]`; the two guards wrap the whole `serve(...)` call; the host is resolved to a numeric address before the guards; `timeout_graceful_shutdown=5` in both modes; the temp root is created and removed by `main`; the runner passes `scenario.question` and `scenario.max_extra_passes` to `run_research`; one `asyncio.Lock` around `build_replay_runtime`; the pacer drains on return or error and is **cancelled** on `CancelledError`.
- Web (spec §4.3): runtime deps exactly `next`, `react`, `react-dom`, `react-markdown`, `remark-gfm`; dev deps exactly `typescript`, `vitest`, `@testing-library/react`, `@testing-library/dom`, `@playwright/test`, `@types/react`, `@types/react-dom`, `@types/node`, `jsdom` (AC19). No Tailwind, no component/CSS library, no `unist-util-visit`. `next.config.ts`: `compress: false`, `reactStrictMode: true`, no `rewrites`. `app/globals.css` begins with `docs/design/prototype/index.html:7-1137` line for line (CRLF-normalised), then at most one block under `/* ═══ 2026-09-27: app-only additions ═══ */` with no new colour literal (AC10). `lib/run-state.ts` handlers are the prototype's (`index.html:2925-3019`) with types added and nothing else changed (AC11).
- Governing UI rule (spec §4.4): an unavailable value is muted text — `not yet`, `not reached`, `not measured`, `not scored`, `Not recorded`, `Not published`, `Not available while running`, `settings as submitted: not recorded` — never `0`, `—`, `null`, a placeholder or a disabled control. The app never retries a `POST`.
- Copy marked (I) in the spec is used verbatim: the S4 sentences (`Research service not reachable at {target}`, `This session isn't in the service's memory — sessions are lost when the API restarts.`, `The service stopped while this run was in progress. Nothing was published.`), the sidebar footer (`Sessions are held in the service process's memory; this list empties when the service restarts.`), `settings as submitted: not recorded`, `loading evidence log`, `The service rejected the request: …`, `Service configuration error · {reason}`.
- Engine truth the app shows as-is (spec §3.3–§3.4): a finding the report never registered is labelled `X01`, `X02`…; not-found target ids read `topic-01-target-01`; replay `duration_seconds` is 0.09–0.22 s so the report head bar reads `0m 00s` in replay mode; the five replay cases end `completed/1`, `completed/0`, `incomplete/0` (`provider_failed`), `max_iterations/1` (three not-found targets), `completed/1` (one dropped finding) for `missing-target-triggers-one-extra-pass`, `scoped-redraft-after-a-named-defect`, `review-unavailable`, `empty-but-clean`, `extra-pass-finds-nothing`.
- Viewports: `1252 × 853` desktop, `390 × 844` phone. Visual captures are always `fullPage: true`.
- Never name a model or provider-model id as a workflow role in code, docs or commits; `deepseek-flash` etc. appear only as product data (the composer's model buttons and the strip's chips, as the design specifies).
- DeepSeek peak windows for Task 21 only: 01:00–04:00 and 06:00–10:00 UTC Monday–Friday; never start within 90 minutes of a window; a run takes 70–110 minutes; never stop a run that has started.

### Conventions every task uses

- **Shell.** Every shell block runs in the **agent's bash tool** with the worktree root as the tool's `cwd` (the tool's default cwd is the main checkout, which is on an old `main`; `PYTHONPATH=src` and `git commit` there would hit the wrong tree) — a POSIX-style shell where `node`, `npm`, `npx`, `git`, `grep`, `sed`, `wc`, `sha256sum` and quoted heredocs work. `bash <file>` resolves to WSL without a distribution on this machine, so **no step runs a shell script by `bash file.sh`**; multi-line checks are Node scripts run with `node` or pytest/Vitest files. Windows paths contain spaces: every path is quoted. **The harness keeps no shell state between calls**: `cd`, variables and `$!` are gone at the next call, so every block sets `PY` itself, every `web/` block starts with `cd web`, and no PID or path is carried in a variable across steps (PIDs live in the launcher's `.launch-<name>.pid` files). Each task's first block runs `git rev-parse --abbrev-ref HEAD` and expects `feat/frontend-backend-integration`.
- **Python.** `PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"` then `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest <file> -q` (pytest's own `pythonpath=["src"]` is set too; the env var is kept for uniformity). A scoped run is the proof for a task; the **whole** suite runs once, in Task 19.
- **Background servers, same call.** When one step needs the API for its own duration, it starts it as a **simple** background command (never a subshell — `( … ) &` leaves `$!` empty in this harness) and stops it in the same call: `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m deep_research.api --mode replay --port 8010 --replay-delay-ms 0 & API_PID=$!` … `kill $API_PID` (the venv launcher takes its interpreter child down with it; the reviewer verified both). Readiness: `node -e "fetch('http://127.0.0.1:8010/research').then(r=>{console.log(r.status)})"` prints `200` (retry until it does, up to 60 s).
- **Node in `web/`.** `cd web && npx vitest run <file>`; `cd web && npx playwright test <spec>`; `cd web && npm run build`. Every `web/` step starts with `cd web` and the block ends there (the next block starts from the root again).
- **Edits by anchor.** Line numbers quoted as `:N` are today's (`4e10823`); each edit also quotes the first line of the region so it can be found after earlier edits moved it.
- **Every step shows its final content.** There is no errata section: if a step here disagrees with an earlier draft of this plan, this text wins.
- **Playwright runs.** `playwright.config.ts` requires the venv interpreter inside a worktree, so every `web/` Playwright command (`npm run test:e2e`, `npx playwright test …`) runs with `DEEP_RESEARCH_PYTHON="$PY"` in front, after `PY=…` is set in the same block; the blocks below show it. `npm run test:e2e` runs the `chromium` project only; the `visual` project runs only through `npm run capture:visual` / `npx playwright test --project visual`.
- **Background servers across calls — the launcher.** A server that must outlive a call (checkpoint C1's app, and both processes of the live run, whose start and stop are separate steps) is started and stopped only through `web/scripts/launch.mjs` (Task 14 step 8): `start <name> <port> <ready-path> "<command>"` refuses if the port already answers, spawns the command `detached: true, windowsHide: true` (a non-detached child is killed with its parent's job object and its grandchildren are orphaned — the reviewer reproduced it; `npm run start` runs through `cmd.exe`, so a shell `$!` would be the wrapper), waits for HTTP 200 and writes `.launch-<name>.pid`; `stop <name> <port>` runs `taskkill /PID <pid> /T /F` and then **polls the port until it refuses** (10 s), exiting 1 loudly if anything still answers. Nothing is stopped by a bare `kill` across calls.
- **TDD boundary.** Unit and component tests (pytest, Vitest) are written first and shown failing. The Playwright specs are end-to-end verification written after the code they exercise; their steps are titled *Verification spec* and their expected output is the passing count.
- **Commits.** `git add <paths> && git commit -m "<type>: <what>"` after every task; never `git add -A` (the `web/visual/`, `web/.e2e-tmp/`, `web/test-results/` trees and the `.launch-*.pid` files are gitignored in Task 9 before they can exist; `.env`, `/output/`, `/memory/` already are, `.gitignore:151`, `:221-222`).

## Review Focus

1. **A `POST` that never reaches the API must not be retried.** A fetch that fails after the request left the browser (proxy up, API down mid-request) would, with a naive retry, start a second paid live run. → pinned in Task 12 step 4 (`startResearch` makes exactly one `fetch` on `ApiUnreachableError`) and Task 15 step 8 (T-E8: a second click shows the banner again and the API receives no second `POST`).
2. **Two replay sessions at once.** The build lock guards `compile_research_graph`; a second `POST` while one replay runs must complete, not fail with `ReplayContractError`. → pinned in Task 7 step 10 (T-A2d).
3. **A stream that ends while the session is still `running`** (the reconnect ladder) must rebuild the same screen, never a doubled counter. → pinned in Task 16 step 9 (T-E7 reload) and Task 11 step 5 (T-W1(a): the state after the last frame agrees with the server's recorded `/status`).
4. **An `evidence_counts: null` or `semantic_review_score: null` report** (a `failed`-adjacent partial) must read `not measured` / `not scored`, never `NaN` or `0`. → pinned in Task 17 step 6 (`ReportRail` with nulls) and Task 17 step 9 (T-E4 `review-unavailable`).
5. **A finding without a `ScoredSource`** (the `source` block with every score `null`) and a dropped figure (`context` null) must serialise without a `ValidationError`. → pinned in Task 5 step 6 (`build_evidence_response` on a composition whose source list is emptied) and Task 5 step 8 (T-A3a dropped figure fields).

---

## File map

| File | Responsibility after this plan | Tasks |
|---|---|---|
| `src/deep_research/api/sessions.py` | `publish` moves `iteration` only for `graph.*` events; `SessionStore.list_sessions(limit)` | 1, 3 |
| `src/deep_research/api/models.py` | `query` on the response; `SessionListResponse`; `EvidenceModel` + six `Evidence*Response` | 2, 3, 5 |
| `src/deep_research/api/app.py` | `create_app(mode=)`, `ModeHeaderMiddleware`, `GET /research`, `GET /research/{id}/evidence`, `evidence_unavailable` | 2, 3, 4, 6 |
| `src/deep_research/api/evidence.py` | `build_evidence_response(outcome)` — E1 JSON from the composition, reusing `_finding_registry_pairs`, `_figure_value_text` | 5 |
| `src/deep_research/api/replay.py` | `ReplayRunner`, `ReplayCaseMiddleware`, `requested_case`, `REPLAY_CASE_HEADER` | 7 |
| `src/deep_research/api/__main__.py` | the start command: `build_parser`, `parse_args`, `build_app`, `main` | 8 |
| `pyproject.toml` | `uvicorn>=0.30` | 8 |
| `tests/test_api/test_sessions.py`, `test_app.py`, `test_evidence.py`, `test_replay.py`, `test_main.py`, `replay_support.py` | the pytest layer (T-A1…T-A8) | 1–8 |
| `web/package.json`, `next.config.ts`, `tsconfig.json`, `vitest.config.ts`, `playwright.config.ts`, `.gitignore` (root) | the app's toolchain | 9, 15 |
| `web/app/globals.css`, `web/scripts/check-css-verbatim.mjs` | the prototype's CSS, verbatim, with its check | 9 |
| `web/app/layout.tsx`, `app/page.tsx`, `app/research/[id]/page.tsx`, `app/api/[...path]/route.ts` | the shell, the two pages, the streaming proxy | 9, 13, 14, 15 |
| `web/lib/format.ts`, `run-state.ts`, `stream.ts`, `api.ts`, `session-store.ts` | the pure modules and the client | 10, 11, 12, 14 |
| `web/scripts/capture-replay-events.mjs`, `web/test/fixtures/events/*.json` | the two real replay captures the event-core tests run on | 11 |
| `web/components/*.tsx` | one client component per surface | 14–18 |
| `web/test/**` | Vitest + Testing Library (T-W1…T-W5) | 10–14, 16–18 |
| `web/e2e/**` | Playwright (T-E1…T-E13), `visual.spec.ts` | 15–19 |
| `README.md`, `docs/design/api-gaps.md`, `docs/design/DESIGN.md`, `web/README.md` | the docs of spec §4.5 | 20 |

---

### Task 1: `/status.iteration` moves only on graph events (spec §4.2 A6; T-A6)

**Files:**
- Modify: `src/deep_research/api/sessions.py:61-70` (`def publish(self, event: ResearchEvent) -> None:`)
- Test: `tests/test_api/test_sessions.py` (append at the end of the file)

**Interfaces:**
- Consumes: `ResearchSession.publish` (`sessions.py:61`), `ResearchEvent` (`utils/types.py:1337`).
- Produces: unchanged signatures; the behaviour every later task relies on — `session.iteration` follows `graph.*` events only.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api/test_sessions.py` (the file already imports `datetime`, `timezone`, `ResearchEvent`; add `ResearchSession` to the existing `from deep_research.api.sessions import SessionStore, outcome_response_fields` line):

```python
# --- iteration follows graph events only (spec A6) -------------------------


def _graph_or_tool_event(event_type: str, **metadata: object) -> ResearchEvent:
    return ResearchEvent(
        event_type=event_type, source="graph", message="event", metadata=metadata
    )


def test_publish_moves_iteration_only_for_graph_events() -> None:
    session = ResearchSession(
        session_id="session-1",
        query="Question",
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    session.publish(_graph_or_tool_event("graph.node.started", node="planner", iteration=0))
    assert session.iteration == 0
    # the researcher's ReAct step index must never read as the pass
    session.publish(_graph_or_tool_event("researcher.tool_call", tool="web_search", iteration=7))
    assert session.iteration == 0
    session.publish(_graph_or_tool_event("graph.extra_pass.started", iteration=1, max_extra_passes=1, targets=[]))
    assert session.iteration == 1
    session.publish(_graph_or_tool_event("graph.node.completed", node="researcher"))
    assert session.iteration == 1
```

- [ ] **Step 2: Run it to verify it fails**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api/test_sessions.py -q -k iteration_only_for_graph
```
Expected: `1 failed` — `assert session.iteration == 0` fails with `7 == 0` after the tool-call event.

- [ ] **Step 3: Make `publish` graph-only**

In `src/deep_research/api/sessions.py`, replace the two lines at `:68-69`

```python
        if isinstance(iteration, int):
            self.iteration = iteration
```

with

```python
        # Only the graph's own events carry the pass: researcher.tool_call also
        # carries an ``iteration``, but that is the ReAct step index (A6).
        if event.event_type.startswith("graph.") and isinstance(iteration, int):
            self.iteration = iteration
```

- [ ] **Step 4: Run the sessions tests**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api/test_sessions.py -q
```
Expected: all pass (`… passed`, `0 failed`).

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/api/sessions.py tests/test_api/test_sessions.py && git commit -m "fix(api): /status.iteration follows graph events only"
```

---

### Task 2: `query` on every session response (spec §4.2 A4; T-A4)

**Files:**
- Modify: `src/deep_research/api/models.py:114-164` (`class ResearchSessionResponse(ApiModel):`), `src/deep_research/api/app.py:114-127` (`def _session_response(session: ResearchSession) -> ResearchSessionResponse:`)
- Modify: `tests/test_api/test_sessions.py:328`, `:339`, `:370`, `:397`, `:436` (the five `ResearchSessionResponse(` constructions)
- Test: `tests/test_api/test_app.py` (append), `tests/test_api/test_sessions.py` (append)

**Interfaces:**
- Produces: `ResearchSessionResponse.query: str` (required, `min_length=1`); every route that returns the model carries it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api/test_app.py`:

```python
def test_every_session_response_echoes_the_stripped_query() -> None:
    app = create_app(runner=ScriptedRunner(), preflight=valid_preflight)
    with TestClient(app) as client:
        posted = client.post("/research", json={"query": "  How mature is quantum error correction?  "})
        assert posted.status_code == 202
        assert posted.json()["query"] == "How mature is quantum error correction?"
        session_id = posted.json()["session_id"]
        status = client.get(f"/research/{session_id}/status").json()
    assert status["query"] == "How mature is quantum error correction?"
```

Append to `tests/test_api/test_sessions.py`:

```python
def test_session_response_requires_query() -> None:
    with pytest.raises(ValidationError):
        ResearchSessionResponse(
            session_id="session-1",
            status="running",
            iteration=0,
            started_at=datetime.now(timezone.utc),
        )
```

- [ ] **Step 2: Run them to verify they fail**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api/test_app.py tests/test_api/test_sessions.py -q -k "echoes_the_stripped_query or requires_query"
```
Expected: `2 failed` (`KeyError: 'query'`; `DID NOT RAISE`).

- [ ] **Step 3: Add the field and pass it**

In `src/deep_research/api/models.py`, inside `class ResearchSessionResponse(ApiModel):`, directly after `session_id: str = Field(min_length=1)` add:

```python
    query: str = Field(min_length=1)
    """The research question the session runs — the request's ``query``, stripped."""
```

In `src/deep_research/api/app.py`, in `_session_response`, add `query=session.query,` on the line after `session_id=session.session_id,`.

- [ ] **Step 4: Update the five existing constructions (contract-test update, spec §1)**

In `tests/test_api/test_sessions.py`, at each of the five `response = ResearchSessionResponse(` calls (today `:328`, `:339`, `:370`, `:397`, `:436`), insert `query="Question",` as the line after `session_id="session-1",`.

- [ ] **Step 5: Run the API tests**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api -q
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/api/models.py src/deep_research/api/app.py tests/test_api/test_app.py tests/test_api/test_sessions.py && git commit -m "feat(api): echo query on every session response"
```

---

### Task 3: `GET /research?limit=` (spec §4.2 A5; T-A5)

**Files:**
- Modify: `src/deep_research/api/sessions.py:131-137` (`class SessionStore:` — add `list_sessions`), `src/deep_research/api/models.py` (add `SessionListResponse` after `ResearchSessionResponse`), `src/deep_research/api/app.py:157-158` (after `router = APIRouter(...)`, before the `POST` route; also the imports)
- Test: `tests/test_api/test_app.py` (append)

**Interfaces:**
- Produces: `SessionStore.list_sessions(limit: int) -> list[ResearchSession]` (newest first); `SessionListResponse(sessions: list[ResearchSessionResponse])`; route `GET /research` with `limit: int = Query(20, ge=1, le=200)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api/test_app.py`:

```python
def test_list_returns_sessions_newest_first_with_a_bounded_limit() -> None:
    app = create_app(runner=ScriptedRunner(), preflight=valid_preflight)
    with TestClient(app) as client:
        assert client.get("/research").json() == {"sessions": []}
        ids = [client.post("/research", json={"query": f"Question {n}"}).json()["session_id"] for n in range(3)]
        for session_id in ids:
            wait_until_terminal(client, session_id)
        listed = client.get("/research").json()["sessions"]
        assert [item["session_id"] for item in listed] == list(reversed(ids))
        assert [item["query"] for item in listed] == ["Question 2", "Question 1", "Question 0"]
        for item in listed:
            assert item == client.get(f"/research/{item['session_id']}/status").json()
        assert len(client.get("/research?limit=2").json()["sessions"]) == 2
        assert client.get("/research?limit=0").status_code == 422
        assert client.get("/research?limit=201").status_code == 422
```

- [ ] **Step 2: Run it to verify it fails**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api/test_app.py -q -k newest_first
```
Expected: `1 failed` — `GET /research` answers 405 or 404, not `{"sessions": []}`.

- [ ] **Step 3: Implement the store method, the model and the route**

`src/deep_research/api/sessions.py`, inside `class SessionStore`, after `require`:

```python
    def list_sessions(self, limit: int) -> list[ResearchSession]:
        """The newest ``limit`` sessions: ``started_at`` descending, ties newest-registered first."""
        newest_registered_first = list(reversed(list(self._sessions.values())))
        ordered = sorted(newest_registered_first, key=lambda s: s.started_at, reverse=True)
        return ordered[:limit]
```

`src/deep_research/api/models.py`, after `class ResearchSessionResponse`:

```python
class SessionListResponse(ApiModel):
    """The process's sessions, newest first; each item is that session's status response."""

    sessions: list[ResearchSessionResponse] = Field(default_factory=list)
```

`src/deep_research/api/app.py`: add `Query` to the `fastapi` import (`from fastapi import APIRouter, Depends, FastAPI, Query, Request`), add `SessionListResponse` to the `deep_research.api.models` import, and add this route right after `router = APIRouter(dependencies=[Depends(_trace_request)])`:

```python
    @router.get("/research", response_model=SessionListResponse)
    async def list_research(
        request: Request,
        limit: int = Query(default=20, ge=1, le=200),
    ) -> SessionListResponse:
        """The newest sessions this process holds — memory only, empty after a restart."""
        del request
        return SessionListResponse(
            sessions=[_session_response(session) for session in store.list_sessions(limit)]
        )
```

- [ ] **Step 4: Run the API tests**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api -q
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/api/sessions.py src/deep_research/api/models.py src/deep_research/api/app.py tests/test_api/test_app.py && git commit -m "feat(api): GET /research lists sessions newest first"
```

---

### Task 4: `X-Deep-Research-Mode` on every response (spec §4.2 "The mode header"; T-A7)

**Files:**
- Modify: `src/deep_research/api/app.py:130-136` (`def create_app(` signature), `:146-155` (after `app = FastAPI(lifespan=lifespan)`), and a new class above `create_app`
- Test: `tests/test_api/test_app.py` (append)

**Interfaces:**
- Produces: `create_app(*, runner=…, config_path=…, preflight=…, tracker=None, mode: Literal["live", "replay"] = "live")`; `app.state.mode`; `ModeHeaderMiddleware(app, *, mode: str)` (pure ASGI).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api/test_app.py`:

```python
@pytest.mark.parametrize("mode", ["live", "replay"])
def test_every_response_carries_the_mode_header(mode: str) -> None:
    app = create_app(runner=ScriptedRunner(report="# q\n\nbody\n"), preflight=valid_preflight, mode=mode)
    with TestClient(app) as client:
        posted = client.post("/research", json={"query": "Question"})
        assert posted.headers["x-deep-research-mode"] == mode
        session_id = posted.json()["session_id"]
        wait_until_terminal(client, session_id)
        for path, expected in (
            (f"/research/{session_id}/status", 200),
            (f"/research/{session_id}/report", 200),
            (f"/research/{session_id}/stream", 200),
            ("/research/nope/status", 404),
        ):
            response = client.get(path)
            assert response.status_code == expected
            assert response.headers["x-deep-research-mode"] == mode
        invalid = client.post("/research", json={})
        assert invalid.status_code == 422
        assert invalid.headers["x-deep-research-mode"] == mode
    assert create_app(runner=ScriptedRunner(), preflight=valid_preflight).state.mode == "live"
```

- [ ] **Step 2: Run it to verify it fails**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api/test_app.py -q -k mode_header
```
Expected: `2 failed` — `TypeError: create_app() got an unexpected keyword argument 'mode'`.

- [ ] **Step 3: Add the middleware and the parameter**

In `src/deep_research/api/app.py` add to the imports `from typing import Literal, TypeAlias` (replacing `from typing import TypeAlias`), `from starlette.datastructures import MutableHeaders` and `from starlette.types import ASGIApp, Message, Receive, Scope, Send`; then add above `def create_app(`:

```python
class ModeHeaderMiddleware:
    """Stamp ``X-Deep-Research-Mode`` on every HTTP response, streams and errors included.

    Pure ASGI on purpose: it must sit outside Starlette's exception middleware
    so 4xx/5xx bodies carry the header too, and it must never hop the request
    into another task (the replay-case ContextVar rides the same task).
    """

    def __init__(self, app: ASGIApp, *, mode: str) -> None:
        self.app = app
        self.mode = mode

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_mode(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Deep-Research-Mode"] = self.mode
            await send(message)

        await self.app(scope, receive, send_with_mode)
```

Change the signature to

```python
def create_app(
    *,
    runner: ResearchRunner = run_research,
    config_path: str = DEFAULT_CONFIG_PATH,
    preflight: PreflightHandler = prepare_research_settings,
    tracker: Tracker | None = None,
    mode: Literal["live", "replay"] = "live",
) -> FastAPI:
```

and after `app.state.api_tracker = tracker` add

```python
    app.state.mode = mode
    app.add_middleware(ModeHeaderMiddleware, mode=mode)
```

- [ ] **Step 4: Run the API tests**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api -q
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/api/app.py tests/test_api/test_app.py && git commit -m "feat(api): X-Deep-Research-Mode header and create_app(mode=)"
```

---

### Task 5: E1 models and the evidence builder (spec §4.2 A3; T-A3a; Review Focus 5)

**Files:**
- Modify: `src/deep_research/api/models.py` (imports; new `EvidenceModel` base and six models after `SessionListResponse`)
- Create: `src/deep_research/api/evidence.py`
- Create: `tests/test_api/replay_support.py`, `tests/test_api/test_evidence.py`

**Interfaces:**
- Consumes: `_finding_registry_pairs`, `_figure_value_text` (`agents/report.py:1612`, `:1697`), `finding_fingerprint` (`agents/identity.py:79`), `latest_scored_sources`, `normalize_source_url`, `publisher_identity` (`agents/sources.py:60`, `:28`, `:81`), `release_text` (`agents/verified_facts.py:1122`), `run_replay_scenario`, `offline_credentials`, `network_denied`, `production_config_path` (`e2e_evaluation/replay.py`), `scenario_by_id` (`replay_matrix.py:3588`). **No file under `agents/` is edited** (R2).
- Produces: `build_evidence_response(outcome: ResearchOutcome) -> EvidenceResponse` (raises `ValueError` when `outcome.composition is None`); `evidence_from_composition(composition: ReportComposition) -> EvidenceResponse`; models `EvidenceModel`, `EvidenceSourceResponse`, `EvidenceFigureResponse`, `EvidenceFindingResponse`, `EvidenceNotFoundResponse`, `EvidenceRefusedResponse`, `EvidenceResponse`; test helpers `tests/test_api/replay_support.py`: `guarded()` (both harness guards as one context manager) and `replay_outcome(case_id: str, root: Path) -> ResearchOutcome`.

- [ ] **Step 1: Write the replay helper the evidence and replay tests share**

`tests/test_api/replay_support.py`:

```python
"""Offline replay outcomes for API tests: the real graph, scripted, network denied."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from deep_research.e2e_evaluation.replay import (
    network_denied,
    offline_credentials,
    run_replay_scenario,
)
from deep_research.e2e_evaluation.replay_matrix import scenario_by_id
from deep_research.runtime.outcome import ResearchOutcome

EXTRA_PASS_CASE = "missing-target-triggers-one-extra-pass"
DROPPED_FINDING_CASE = "extra-pass-finds-nothing"
REDRAFT_CASE = "scoped-redraft-after-a-named-defect"
REVIEW_UNAVAILABLE_CASE = "review-unavailable"


@contextmanager
def guarded() -> Iterator[list[str]]:
    """Both harness guards, exactly as the replay-mode server holds them."""
    with offline_credentials(), network_denied() as attempts:
        yield attempts


def replay_outcome(case_id: str, root: Path) -> ResearchOutcome:
    """Run one case through the CLI harness and return its ``ResearchOutcome``."""
    with guarded() as attempts:
        run = run_replay_scenario(scenario_by_id(case_id), root=root)
    assert attempts == [], attempts
    return run.graph_run
```

- [ ] **Step 2: Write the failing tests**

`tests/test_api/test_evidence.py`:

```python
"""E1: the evidence JSON agrees with the evidence log the same run composed."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from deep_research.api.evidence import build_evidence_response, evidence_from_composition
from deep_research.api.models import EvidenceFindingResponse, EvidenceResponse
from deep_research.runtime.outcome import ResearchOutcome
from tests.test_api.replay_support import DROPPED_FINDING_CASE, EXTRA_PASS_CASE, replay_outcome

HEADING = re.compile(r"^### (\S+) — ", re.MULTILINE)


@pytest.fixture(scope="module", params=[EXTRA_PASS_CASE, DROPPED_FINDING_CASE])
def outcome(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> ResearchOutcome:
    return replay_outcome(request.param, tmp_path_factory.mktemp(request.param))


def test_labels_follow_the_evidence_log_headings(outcome: ResearchOutcome) -> None:
    evidence = build_evidence_response(outcome)
    assert outcome.state.report_evidence is not None
    assert [f.label for f in evidence.findings] == HEADING.findall(outcome.state.report_evidence)
    if any(f.status == "dropped" for f in evidence.findings):
        assert evidence.findings[0].label == "X01"


def test_counts_agree_with_the_outcome(outcome: ResearchOutcome) -> None:
    evidence = build_evidence_response(outcome)
    counts = outcome.evidence_counts
    assert counts is not None
    assert sum(f.cited for f in evidence.findings) == counts.cited_findings
    by_status = {s: sum(1 for f in evidence.findings if f.status == s) for s in ("verified", "verified_corrected", "quoted", "dropped")}
    assert by_status == {
        "verified": counts.verified_findings,
        "verified_corrected": counts.corrected_findings,
        "quoted": counts.quoted_findings,
        "dropped": counts.dropped_findings,
    }
    composition = outcome.composition
    assert composition is not None
    assert len(evidence.not_found) == len(composition.not_found)
    assert len(evidence.refused) == len(composition.rejected_points)
    assert evidence.session_id == composition.session_id
    assert evidence.iteration == composition.iteration


def test_kept_figures_carry_context_and_dropped_ones_do_not(outcome: ResearchOutcome) -> None:
    evidence = build_evidence_response(outcome)
    figures = [fig for f in evidence.findings for fig in f.figures]
    assert figures, "the cases carry figures"
    for fig in figures:
        if fig.kept:
            assert fig.organisation and fig.attribution and fig.kind
        else:
            assert fig.dropped_reason is not None
            assert (fig.period, fig.scope, fig.organisation, fig.attribution, fig.kind, fig.release) == (None,) * 6


def test_a_finding_without_a_scored_source_serialises_with_null_scores(outcome: ResearchOutcome) -> None:
    composition = outcome.composition
    assert composition is not None
    evidence = evidence_from_composition(composition.model_copy(update={"sources": []}))
    for f in evidence.findings:
        assert f.source.evaluation_status is None
        assert f.source.low_confidence is False
        assert (f.source.authority_score, f.source.recency_score, f.source.relevance_score, f.source.overall_score) == (None,) * 4
        assert f.source.organisation
    EvidenceResponse.model_validate(evidence.model_dump(mode="json"))


def test_response_models_keep_verbatim_whitespace() -> None:
    finding = EvidenceFindingResponse.model_validate(
        {
            "label": "F01", "status": None, "dropped_reason": None, "context_unchecked": False, "cited": False,
            "target_ids": [], "content": "c", "snippet": "  two spaces either side  ", "passage": "\tpassage\n",
            "source": {"url": "https://example.org/", "title": "t", "organisation": "o", "evaluation_status": None,
                       "low_confidence": False, "authority_score": None, "recency_score": None,
                       "relevance_score": None, "overall_score": None},
            "figures": [],
        }
    )
    again = EvidenceFindingResponse.model_validate(finding.model_dump(mode="json"))
    assert again.snippet == "  two spaces either side  "
    assert again.passage == "\tpassage\n"
```

- [ ] **Step 3: Run them to verify they fail**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api/test_evidence.py -q
```
Expected: collection error — `ModuleNotFoundError: No module named 'deep_research.api.evidence'`.

- [ ] **Step 4: Add the models**

In `src/deep_research/api/models.py` extend the `deep_research.utils.types` import to

```python
from deep_research.utils.types import (
    FigureAttribution,
    FigureDropReason,
    FigureKind,
    FindingDropReason,
    FindingStatus,
    ResearchError,
    SourceEvaluationStatus,
)
```

and append after `SessionListResponse`:

```python
class EvidenceModel(BaseModel):
    """Base for the evidence view's models: strict shape, verbatim text.

    Unlike ``ApiModel`` it does not strip strings: ``content``, ``snippet``,
    ``passage`` and ``evidence_words`` are verbatim page text and must reach
    the client exactly as the run recorded them.
    """

    model_config = ConfigDict(extra="forbid", validate_default=True)


class EvidenceSourceResponse(EvidenceModel):
    url: str
    title: str
    organisation: str
    evaluation_status: SourceEvaluationStatus | None = None
    """``None`` when the composition holds no ``ScoredSource`` for this URL."""
    low_confidence: bool = False
    authority_score: float | None = None
    recency_score: float | None = None
    relevance_score: float | None = None
    overall_score: float | None = None


class EvidenceFigureResponse(EvidenceModel):
    value: str
    kept: bool
    period: str | None
    scope: str | None
    organisation: str | None
    attribution: FigureAttribution | None
    kind: FigureKind | None
    release: str | None
    evidence_words: str | None
    corrected: bool
    dropped_reason: FigureDropReason | None
    reason: str | None


class EvidenceFindingResponse(EvidenceModel):
    label: str
    status: FindingStatus | None
    dropped_reason: FindingDropReason | None
    context_unchecked: bool
    cited: bool
    target_ids: list[str]
    content: str
    snippet: str | None
    passage: str | None
    source: EvidenceSourceResponse
    figures: list[EvidenceFigureResponse]


class EvidenceNotFoundResponse(EvidenceModel):
    target_id: str
    question: str
    queries: list[str]
    pages_read: list[str]
    searched: bool


class EvidenceRefusedResponse(EvidenceModel):
    where: str
    text: str
    reason: str
    finding_labels: list[str]


class EvidenceResponse(EvidenceModel):
    """E1: every finding with its verification and source, the not-found targets, the refused sentences."""

    session_id: str
    iteration: int
    findings: list[EvidenceFindingResponse]
    not_found: list[EvidenceNotFoundResponse]
    refused: list[EvidenceRefusedResponse]
```

- [ ] **Step 5: Write the builder**

`src/deep_research/api/evidence.py`:

```python
"""E1: the evidence view's JSON, built from the run's own ``ReportComposition``.

Every derivation reuses the evidence log's own helpers — the label pairing,
the figure text, the release, the page owner — so the JSON can never
disagree with the Markdown the same run composed. The two private helpers
are imported the way ``agents/report_reviewer.py`` imports them.
"""

from __future__ import annotations

from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report import _figure_value_text, _finding_registry_pairs
from deep_research.agents.sources import (
    latest_scored_sources,
    normalize_source_url,
    publisher_identity,
)
from deep_research.agents.verified_facts import release_text
from deep_research.api.models import (
    EvidenceFigureResponse,
    EvidenceFindingResponse,
    EvidenceNotFoundResponse,
    EvidenceRefusedResponse,
    EvidenceResponse,
    EvidenceSourceResponse,
)
from deep_research.runtime.outcome import ResearchOutcome
from deep_research.utils.types import (
    FigureResult,
    Finding,
    ReportComposition,
    ScoredSource,
)


def build_evidence_response(outcome: ResearchOutcome) -> EvidenceResponse:
    """The E1 body for a finished run; raises when the run composed nothing."""
    composition = outcome.composition
    if composition is None:
        raise ValueError("the outcome carries no composition")
    return evidence_from_composition(composition)


def evidence_from_composition(composition: ReportComposition) -> EvidenceResponse:
    # The cited set, exactly as ``compute_report_quality`` sums it (quality.py:95-97, :155).
    points = [*composition.summary, *(p for s in composition.sections for p in s.points)]
    cited = {i for p in points if p.statement for i in p.statement.finding_ids}
    # Last append-ordered score per normalised URL, as the report reads sources.
    sources = {
        normalize_source_url(s.url): s for s in latest_scored_sources(composition.sources)
    }
    # ``release`` as the log prints it: the finding's own, else its fact row's (report.py:1912-1913, :1962).
    row_release = {
        fid: row.release
        for row in composition.fact_rows
        for fid in (row.finding_id, *row.duplicate_finding_ids)
    }
    findings: list[EvidenceFindingResponse] = []
    unlabelled = 0
    for label, finding in _finding_registry_pairs(composition):
        finding_id = finding_fingerprint(finding)
        if label is None:
            unlabelled += 1
            label = f"X{unlabelled:02d}"
        findings.append(
            _finding_response(
                label,
                finding,
                cited=finding_id in cited,
                source=sources.get(normalize_source_url(finding.source_url)),
                passage=composition.statement_passages.get(finding_id),
                release=release_text(finding) or row_release.get(finding_id),
            )
        )
    return EvidenceResponse(
        session_id=composition.session_id,
        iteration=composition.iteration,
        findings=findings,
        not_found=[
            EvidenceNotFoundResponse(
                target_id=t.target_id,
                question=t.question,
                queries=list(t.queries),
                pages_read=list(t.pages_read),
                searched=t.searched,
            )
            for t in composition.not_found
        ],
        refused=[
            EvidenceRefusedResponse(
                where=r.where, text=r.text, reason=r.reason, finding_labels=list(r.finding_labels)
            )
            for r in composition.rejected_points
        ],
    )


def _finding_response(
    label: str,
    finding: Finding,
    *,
    cited: bool,
    source: ScoredSource | None,
    passage: str | None,
    release: str | None,
) -> EvidenceFindingResponse:
    verification = finding.verification
    results: list[FigureResult] = list(verification.figure_results) if verification else []
    kept_contexts = [r.context for r in results if r.kept and r.context is not None]
    organisation = (
        kept_contexts[0].organisation if kept_contexts else publisher_identity(finding.source_url)
    )
    return EvidenceFindingResponse(
        label=label,
        status=verification.status if verification else None,
        dropped_reason=verification.dropped_reason if verification else None,
        context_unchecked=verification.context_unchecked if verification else False,
        cited=cited,
        target_ids=list(finding.target_ids),
        content=finding.content,
        snippet=finding.snippet,
        passage=passage,
        source=EvidenceSourceResponse(
            url=finding.source_url,
            title=finding.source_title,
            organisation=organisation,
            evaluation_status=source.evaluation_status if source else None,
            low_confidence=source.low_confidence if source else False,
            authority_score=source.authority_score if source else None,
            recency_score=source.recency_score if source else None,
            relevance_score=source.relevance_score if source else None,
            overall_score=source.overall_score if source else None,
        ),
        figures=[_figure_response(r, release) for r in results],
    )


def _figure_response(result: FigureResult, release: str | None) -> EvidenceFigureResponse:
    context = result.context if result.kept else None
    return EvidenceFigureResponse(
        value=_figure_value_text(result.figure),
        kept=result.kept,
        period=context.period if context else None,
        scope=context.scope if context else None,
        organisation=context.organisation if context else None,
        attribution=context.attribution if context else None,
        kind=context.kind if context else None,
        release=release if context else None,
        evidence_words=result.evidence_words,
        corrected=result.corrected,
        dropped_reason=result.dropped_reason,
        reason=result.reason,
    )
```

- [ ] **Step 6: Run the evidence tests**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api/test_evidence.py -q
```
Expected: `9 passed` (four tests × two cases + the whitespace test); the two replays take about a second each.

- [ ] **Step 7: Confirm nothing under `agents/` changed**

```bash
git status --porcelain -- src/deep_research/agents src/deep_research/runtime src/deep_research/graph src/deep_research/utils
```
Expected: no output.

- [ ] **Step 8: Commit**

```bash
git add src/deep_research/api/models.py src/deep_research/api/evidence.py tests/test_api/replay_support.py tests/test_api/test_evidence.py && git commit -m "feat(api): E1 evidence models and builder from the composition"
```

---

### Task 6: `GET /research/{id}/evidence` (spec §4.2 A3; T-A3b)

**Files:**
- Modify: `src/deep_research/api/app.py:48-55` (`_SAFE_MESSAGES`), `:238-263` (after the `/report` route; imports)
- Test: `tests/test_api/test_evidence.py` (append)

**Interfaces:**
- Consumes: `build_evidence_response` (Task 5); `judged_state()` from `tests/test_api/test_sessions.py:187` (a composition with two unlabelled kept findings and one not-found target, no `report_evidence`).
- Produces: the route; the code `evidence_unavailable` with message `Research session finished without an evidence log.`

- [ ] **Step 1: Write the failing tests**

Add these imports to the **top** import block of `tests/test_api/test_evidence.py` (ruff `E402`/`I001` reject a second block mid-file):

```python
from fastapi.testclient import TestClient

from deep_research.api.app import create_app
from tests.test_api.fakes import GateRunner, ScriptedRunner
from tests.test_api.test_app import valid_preflight, wait_until_terminal
from tests.test_api.test_sessions import judged_state
```

then append to the file:

```python
E1_KEYS = {"session_id", "iteration", "findings", "not_found", "refused"}
FINDING_KEYS = {"label", "status", "dropped_reason", "context_unchecked", "cited", "target_ids", "content", "snippet", "passage", "source", "figures"}
SOURCE_KEYS = {"url", "title", "organisation", "evaluation_status", "low_confidence", "authority_score", "recency_score", "relevance_score", "overall_score"}


def test_evidence_route_codes() -> None:
    gate = GateRunner()
    app = create_app(runner=gate, preflight=valid_preflight)
    with TestClient(app) as client:
        assert client.get("/research/nope/evidence").status_code == 404
        session_id = client.post("/research", json={"query": "Question"}).json()["session_id"]
        running = client.get(f"/research/{session_id}/evidence")
        assert running.status_code == 409
        assert running.json()["error"]["code"] == "session_not_complete"
        assert client.get(f"/research/{session_id}/evidence?format=pdf").status_code == 422
        client.portal.call(gate.release.set)  # set the asyncio.Event on the app's loop, as the existing tests do
        wait_until_terminal(client, session_id)
    # a finished run without a composition or a log
    app = create_app(runner=ScriptedRunner(), preflight=valid_preflight)
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": "Question"}).json()["session_id"]
        wait_until_terminal(client, session_id)
        for query in ("", "?format=markdown"):
            response = client.get(f"/research/{session_id}/evidence{query}")
            assert response.status_code == 409
            assert response.json()["error"] == {
                "code": "evidence_unavailable",
                "message": "Research session finished without an evidence log.",
                "reason": None,
                "issues": [],
            }


def test_evidence_route_serves_json_and_markdown() -> None:
    state = judged_state().model_copy(update={"report_evidence": "# Evidence log: q\n\n## Findings\n"})
    app = create_app(runner=ScriptedRunner(state=state), preflight=valid_preflight)
    with TestClient(app) as client:
        session_id = client.post("/research", json={"query": "Question"}).json()["session_id"]
        wait_until_terminal(client, session_id)
        body = client.get(f"/research/{session_id}/evidence").json()
        assert set(body) == E1_KEYS
        assert [f["label"] for f in body["findings"]] == ["X01", "X02"]
        assert all(set(f) == FINDING_KEYS and set(f["source"]) == SOURCE_KEYS for f in body["findings"])
        assert body["not_found"][0]["target_id"] == "topic-02-target-01"
        markdown = client.get(f"/research/{session_id}/evidence?format=markdown")
        assert markdown.status_code == 200
        assert markdown.headers["content-type"].startswith("text/markdown")
        assert markdown.text == "# Evidence log: q\n\n## Findings\n"
```

- [ ] **Step 2: Run them to verify they fail**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api/test_evidence.py -q -k evidence_route
```
Expected: `2 failed` (404 where 409 was expected — the route does not exist).

- [ ] **Step 3: Add the code and the route**

In `src/deep_research/api/app.py`: add `"evidence_unavailable": "Research session finished without an evidence log.",` to `_SAFE_MESSAGES`; add `from deep_research.api.evidence import build_evidence_response`; add this route after the `/report` route:

```python
    @router.get("/research/{session_id}/evidence")
    async def research_evidence(
        request: Request,
        format: Literal["json", "markdown"] = Query(default="json"),
    ) -> Response:
        """E1: the run's findings, verification and sources as JSON, or its evidence log.

        Both forms come from the finished run's own state — the composition
        and the ledger Markdown the writer composed — so they cannot disagree
        with each other or with ``/report``. A running session and a run that
        composed nothing are explicit 409s, never an empty list.
        """
        try:
            session = store.require(request.state.session_id)
        except KeyError:
            raise ApiProblem(code="session_not_found", status_code=404) from None
        if session.outcome is None:
            raise ApiProblem(code="session_not_complete", status_code=409)
        if format == "markdown":
            log = session.outcome.state.report_evidence
            if log is None:
                raise ApiProblem(code="evidence_unavailable", status_code=409)
            return Response(log, media_type="text/markdown")
        if session.outcome.composition is None:
            raise ApiProblem(code="evidence_unavailable", status_code=409)
        return JSONResponse(build_evidence_response(session.outcome).model_dump(mode="json"))
```

- [ ] **Step 4: Run the API tests**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api -q
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/api/app.py tests/test_api/test_evidence.py && git commit -m "feat(api): GET /research/{id}/evidence (E1) as JSON or the evidence log"
```

---

### Task 7: The replay runner and the `X-Replay-Case` middleware (spec §4.2 A2; T-A2a–T-A2d; Review Focus 2)

**Files:**
- Create: `src/deep_research/api/replay.py`
- Test: `tests/test_api/test_replay.py`

**Interfaces:**
- Consumes: `run_research`, `ProgressHandler` (`main.py:189`, `:78`), `replay_settings`, `build_replay_runtime`, `production_config_path`, `ReplayScenario` (`replay.py:1817`, `:1956`, `:3510`, `:440`), `REPLAY_CASE_IDS`, `scenario_by_id` (`replay_matrix.py:3571`, `:3588`), `configuration_error` (`runtime/errors.py:71`), `SessionStore` (Task 1), `create_app(mode=)` (Task 4), `guarded()` (Task 5).
- Produces: `REPLAY_CASE_HEADER = "x-replay-case"`; `requested_case: ContextVar[str | None]`; `ReplayRunner(default_case: str, delay: float, root: Path)` — an async callable with the store's runner keywords; `ReplayCaseMiddleware(app, *, default_case: str)` (pure ASGI); `resolve_scenario(case_id) -> ReplayScenario`.

- [ ] **Step 1: Write the failing tests**

`tests/test_api/test_replay.py`:

```python
"""Replay mode: the real graph runs offline inside the API's own loop."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deep_research.api.app import create_app
from deep_research.api.replay import ReplayCaseMiddleware, ReplayRunner
from deep_research.api.sessions import SessionStore
from deep_research.e2e_evaluation.replay import production_config_path
from deep_research.e2e_evaluation.replay_matrix import scenario_by_id
from tests.test_api.fakes import ScriptedRunner
from tests.test_api.replay_support import EXTRA_PASS_CASE, REVIEW_UNAVAILABLE_CASE, guarded
from tests.test_api.test_app import valid_preflight, wait_until_terminal


def replay_app(root: Path, *, delay: float = 0.0):
    runner = ReplayRunner(default_case=EXTRA_PASS_CASE, delay=delay, root=root)
    app = create_app(runner=runner, config_path=str(production_config_path()), mode="replay")
    app.add_middleware(ReplayCaseMiddleware, default_case=EXTRA_PASS_CASE)
    return app


def frames(text: str) -> list[str]:
    """The ``event:`` names of an SSE body, in order."""
    return [line[7:] for frame in text.split("\n\n") for line in frame.splitlines() if line.startswith("event: ")]


def test_replay_runner_completes_the_default_case_with_the_network_denied(tmp_path: Path) -> None:
    with guarded() as attempts, TestClient(replay_app(tmp_path)) as client:
        posted = client.post("/research", json={"query": "anything the operator typed"})
        assert posted.status_code == 202
        session_id = posted.json()["session_id"]
        status = wait_until_terminal(client, session_id)
        assert status["status"] == "completed"
        assert status["iteration"] == 1
        assert status["query"] == scenario_by_id(EXTRA_PASS_CASE).question
        names = frames(client.get(f"/research/{session_id}/stream").text)
        assert names[0] == "graph.session.started" and names[-1] == "graph.session.completed"
        report = client.get(f"/research/{session_id}/report")
        assert report.status_code == 200 and report.headers["content-type"].startswith("text/markdown")
    assert attempts == []


def test_replay_case_header_picks_the_outcome_only_in_replay_mode(tmp_path: Path) -> None:
    with guarded(), TestClient(replay_app(tmp_path)) as client:
        chosen = client.post("/research", json={"query": "q"}, headers={"X-Replay-Case": REVIEW_UNAVAILABLE_CASE})
        assert chosen.json()["query"] == scenario_by_id(REVIEW_UNAVAILABLE_CASE).question
        status = wait_until_terminal(client, chosen.json()["session_id"])
        assert status["status"] == "incomplete"
        assert status["semantic_review_status"] == "provider_failed"
        unknown = client.post("/research", json={"query": "typed text"}, headers={"X-Replay-Case": "no-such-case"})
        assert unknown.status_code == 202
        assert unknown.json()["query"] == "typed text"
        failed = wait_until_terminal(client, unknown.json()["session_id"])
        assert failed["status"] == "failed"
        assert failed["errors"][0]["error_type"] == "api.research.configuration_error"
        assert failed["errors"][0]["details"] == {"reason": "config_invalid"}
    live = create_app(runner=ScriptedRunner(), preflight=valid_preflight)
    with TestClient(live) as client:
        posted = client.post("/research", json={"query": "typed text"}, headers={"X-Replay-Case": REVIEW_UNAVAILABLE_CASE})
        assert posted.json()["query"] == "typed text"


def test_two_replay_sessions_at_once_both_finish(tmp_path: Path) -> None:
    with guarded(), TestClient(replay_app(tmp_path)) as client:
        ids = [client.post("/research", json={"query": "q"}).json()["session_id"] for _ in range(2)]
        for session_id in ids:
            assert wait_until_terminal(client, session_id)["status"] == "completed"
            assert (tmp_path / session_id).is_dir()


def _start(store: SessionStore, session_id: str, question: str) -> None:
    store.start(
        session_id=session_id,
        query=question,
        max_extra_passes=None,
        output_format="markdown",
        config_overrides={},
        config_path=str(production_config_path()),
    )


@pytest.mark.asyncio
async def test_pacer_releases_events_over_time_and_the_tail_before_the_fold(tmp_path: Path) -> None:
    delay = 0.05
    with guarded():
        scenario = scenario_by_id(EXTRA_PASS_CASE)
        store = SessionStore(runner=ReplayRunner(default_case=EXTRA_PASS_CASE, delay=delay, root=tmp_path))
        _start(store, "s1", scenario.question)
        session = store.require("s1")
        stamps: list[float] = []
        statuses: list[str] = []
        names: list[str] = []
        async for event in store.iter_events("s1"):
            stamps.append(time.perf_counter())
            statuses.append(session.status)
            names.append(event.event_type)
        assert session.task is not None
        await session.task
    assert statuses[0] == "running"
    assert names[0] == "graph.session.started" and names[-1] == "graph.session.completed"
    assert statuses[-1] == "running", "the tail is published before the outcome is folded"
    assert session.status == "completed"
    n = len(stamps)
    assert n >= 40
    assert stamps[-1] - stamps[0] >= 0.8 * (n - 1) * delay


@pytest.mark.asyncio
async def test_close_cancels_the_pacer_and_publishes_nothing_more(tmp_path: Path) -> None:
    with guarded():
        scenario = scenario_by_id(EXTRA_PASS_CASE)
        store = SessionStore(runner=ReplayRunner(default_case=EXTRA_PASS_CASE, delay=0.2, root=tmp_path))
        _start(store, "s1", scenario.question)
        session = store.require("s1")
        await asyncio.sleep(1.5)  # the graph is long done; the pacer is mid-way
        published = len(session.events)
        assert 0 < published < 72
        started = time.perf_counter()
        await store.close()
        assert time.perf_counter() - started < 1.0
        await asyncio.sleep(0.5)
    assert len(session.events) == published
    assert session.status == "running" and session.finished_at is not None
```

- [ ] **Step 2: Run them to verify they fail**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api/test_replay.py -q
```
Expected: collection error — `ModuleNotFoundError: No module named 'deep_research.api.replay'`.

- [ ] **Step 3: Write the module**

`src/deep_research/api/replay.py`:

```python
"""Replay mode: run a scripted offline case through the real graph on the API's loop.

``ReplayRunner`` satisfies ``SessionStore``'s runner contract with the e2e
replay harness's own pieces — ``replay_settings`` and ``build_replay_runtime``
— called through ``run_research`` directly (never ``run_replay_scenario``,
which owns its own ``asyncio.run``). Events are released one every ``delay``
seconds through a queue drained by a task on the same loop, so the running
stage is watchable; order and content are untouched. ``ReplayCaseMiddleware``
reads ``X-Replay-Case`` on ``POST /research`` and rewrites the request's
``query`` to the case's own question, so the session records what ran.

The two harness guards (``offline_credentials``, ``network_denied``) are not
entered here: the start command holds them for the whole server process.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import JsonValue
from starlette.types import ASGIApp, Receive, Scope, Send

from deep_research.e2e_evaluation.replay import (
    ReplayScenario,
    build_replay_runtime,
    replay_settings,
)
from deep_research.e2e_evaluation.replay_matrix import REPLAY_CASE_IDS, scenario_by_id
from deep_research.main import ProgressHandler, run_research
from deep_research.runtime.errors import configuration_error
from deep_research.runtime.outcome import ResearchOutcome
from deep_research.utils.config import ConfigSettings
from deep_research.utils.types import ResearchEvent

REPLAY_CASE_HEADER = "x-replay-case"
requested_case: ContextVar[str | None] = ContextVar("deep_research_replay_case", default=None)
_log = logging.getLogger(__name__)


def resolve_scenario(case_id: str) -> ReplayScenario:
    """The case, or the enumerated configuration failure an unknown id earns (R1)."""
    try:
        return scenario_by_id(case_id)
    except KeyError:
        _log.warning("replay: unknown case %r; known cases: %s", case_id, ", ".join(REPLAY_CASE_IDS))
        raise configuration_error(
            reason="config_invalid", message=f"No replay case named {case_id!r}."
        ) from None


@dataclass
class ReplayRunner:
    """Run one replay case as a ``SessionStore`` runner, paced onto the stream."""

    default_case: str
    delay: float
    root: Path
    _build_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def __call__(
        self,
        *,
        question: str,
        session_id: str,
        max_extra_passes: int | None,
        output_format: str,
        config_overrides: Mapping[str, JsonValue],
        config_path: str,
        event_handler: ProgressHandler | None,
    ) -> ResearchOutcome:
        # The case decides the question and the ceiling: its scripted completer
        # requires its own question, and its script is written for its ceiling.
        del question, max_extra_passes
        scenario = resolve_scenario(requested_case.get() or self.default_case)
        session_root = self.root / session_id
        session_root.mkdir(parents=True, exist_ok=True)

        async def builder(current: ConfigSettings, *, session_id: str) -> Any:
            effective = replay_settings(scenario, root=session_root, base=current)
            # build_replay_runtime patches runtime.assembly.compile_research_graph
            # while it builds (replay.py:2031-2050): one build at a time.
            async with self._build_lock:
                replay = await build_replay_runtime(
                    scenario, root=session_root, session_id=session_id, settings=effective
                )
            return replay.runtime

        queue: asyncio.Queue[ResearchEvent | None] = asyncio.Queue()
        drain_task: asyncio.Task[None] | None = None
        paced: ProgressHandler | None = None
        if event_handler is not None:
            paced = queue.put_nowait
            drain_task = asyncio.create_task(self._drain(queue, event_handler))
        try:
            outcome = await run_research(
                question=scenario.question,
                session_id=session_id,
                config_path=config_path,
                max_extra_passes=scenario.max_extra_passes,
                output_format=output_format,
                config_overrides=config_overrides,
                runtime_builder=builder,
                event_handler=paced,
            )
            if drain_task is not None:
                queue.put_nowait(None)
                await drain_task
            return outcome
        except asyncio.CancelledError:
            # Shutdown: stop releasing events now; nothing is published afterwards.
            if drain_task is not None:
                drain_task.cancel()
                await asyncio.gather(drain_task, return_exceptions=True)
            raise
        except BaseException:
            # A failed run still publishes the events it produced before failing.
            if drain_task is not None and not drain_task.done():
                queue.put_nowait(None)
                await drain_task
            raise

    async def _drain(self, queue: asyncio.Queue[ResearchEvent | None], publish: ProgressHandler) -> None:
        while (event := await queue.get()) is not None:
            publish(event)
            if self.delay > 0:
                await asyncio.sleep(self.delay)


class ReplayCaseMiddleware:
    """Pick the case from ``X-Replay-Case`` and record the case's question on the session.

    Pure ASGI so the ContextVar it sets travels in the request's own task
    into ``SessionStore.start``'s ``create_task``. Only ``POST /research`` is
    touched; an unknown case rewrites nothing and lets the runner fail the
    session with the enumerated reason.
    """

    def __init__(self, app: ASGIApp, *, default_case: str) -> None:
        self.app = app
        self.default_case = default_case
        self._questions: dict[str, str] = {}

    def _question(self, case_id: str) -> str:
        if case_id not in self._questions:
            self._questions[case_id] = scenario_by_id(case_id).question
        return self._questions[case_id]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST" or scope.get("path") != "/research":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        case_id = headers.get(REPLAY_CASE_HEADER) or self.default_case
        requested_case.set(case_id)
        if case_id not in REPLAY_CASE_IDS:
            await self.app(scope, receive, send)
            return
        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break
        try:
            payload = json.loads(body)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            payload["query"] = self._question(case_id)
            body = json.dumps(payload).encode("utf-8")
            scope = dict(scope)
            scope["headers"] = [
                (k, v) for k, v in scope["headers"] if k.lower() != b"content-length"
            ] + [(b"content-length", str(len(body)).encode("latin-1"))]
        delivered = False

        async def replay_receive() -> dict[str, Any]:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)
```

- [ ] **Step 4: Run the replay tests**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api/test_replay.py -q
```
Expected: `5 passed` in under 30 s (the paced tests take about 4 s and 2 s).

- [ ] **Step 5: Run the whole API package**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api -q
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/api/replay.py tests/test_api/test_replay.py && git commit -m "feat(api): replay runner and X-Replay-Case middleware for replay mode"
```

---

### Task 8: The start command `python -m deep_research.api` (spec §4.2 A1; T-A1a, T-A1b)

**Files:**
- Create: `src/deep_research/api/__main__.py`
- Modify: `pyproject.toml:10-25` (`dependencies = [` — add `"uvicorn>=0.30",` after `"tldextract>=5,<6",`)
- Test: `tests/test_api/test_main.py`

**Interfaces:**
- Consumes: `create_app` (Task 4), `ReplayRunner`, `ReplayCaseMiddleware` (Task 7), `production_config_path`, `offline_credentials`, `network_denied`, `REPLAY_CASE_IDS`.
- Produces: `build_parser() -> argparse.ArgumentParser`; `parse_args(argv) -> argparse.Namespace` (validates the cross-flag rules with `parser.error`); `build_app(args, *, replay_root: Path | None = None) -> FastAPI`; `main(argv=None, *, serve=uvicorn.run) -> int`; `DEFAULT_REPLAY_CASE`, `DEFAULT_REPLAY_DELAY_MS = 150`.

- [ ] **Step 1: Write the failing tests**

`tests/test_api/test_main.py`:

```python
"""The start command: flags, composition, and the real server under the guards."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from deep_research.api import __main__ as start
from deep_research.api.replay import ReplayCaseMiddleware, ReplayRunner
from deep_research.e2e_evaluation.replay import production_config_path
from deep_research.e2e_evaluation.replay_matrix import scenario_by_id

ROOT = Path(__file__).resolve().parents[2]


def test_parser_defaults() -> None:
    args = start.parse_args([])
    assert (args.mode, args.host, args.port) == ("live", "127.0.0.1", 8000)
    replay = start.parse_args(["--mode", "replay"])
    assert (replay.replay_case, replay.replay_delay_ms) == (start.DEFAULT_REPLAY_CASE, 150)


@pytest.mark.parametrize(
    "argv",
    [
        ["--mode", "live", "--replay-case", start.DEFAULT_REPLAY_CASE],
        ["--mode", "live", "--replay-delay-ms", "0"],
        ["--mode", "replay", "--replay-case", "no-such-case"],
        ["--mode", "replay", "--replay-delay-ms", "-1"],
        ["--port", "70000"],
        ["--mode", "dry-run"],
    ],
)
def test_bad_flags_exit_2(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        start.parse_args(argv)
    assert raised.value.code == 2


def test_build_app_composes_one_app_per_mode(tmp_path: Path) -> None:
    live = start.build_app(start.parse_args([]))
    assert live.state.mode == "live"
    with pytest.raises(ValueError):
        start.build_app(start.parse_args(["--mode", "replay"]))
    replay = start.build_app(start.parse_args(["--mode", "replay", "--replay-delay-ms", "0"]), replay_root=tmp_path)
    assert replay.state.mode == "replay"
    runner = replay.state.session_store._runner
    assert isinstance(runner, ReplayRunner)
    assert (runner.default_case, runner.delay, runner.root) == (start.DEFAULT_REPLAY_CASE, 0.0, tmp_path)
    assert any(m.cls is ReplayCaseMiddleware for m in replay.user_middleware)


def test_main_serves_with_the_numeric_host_and_a_bounded_shutdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_serve(app: Any, **kwargs: Any) -> None:
        calls.append({"app": app, **kwargs})

    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", None)  # tempfile caches the first answer; make it re-read TEMP
    assert start.main(["--mode", "live", "--port", "8123"], serve=fake_serve) == 0
    assert calls[-1]["host"] == "127.0.0.1" and calls[-1]["port"] == 8123
    assert calls[-1]["timeout_graceful_shutdown"] == 5 and calls[-1]["app"].state.mode == "live"
    assert start.main(["--mode", "replay", "--host", "localhost", "--port", "8124"], serve=fake_serve) == 0
    assert calls[-1]["host"] in ("127.0.0.1", "::1") and calls[-1]["port"] == 8124
    assert calls[-1]["timeout_graceful_shutdown"] == 5 and calls[-1]["app"].state.mode == "replay"
    assert list(tmp_path.glob("deep-research-replay-*")) == []  # the root was removed


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_replay_server_serves_a_paced_session_end_to_end(tmp_path: Path) -> None:
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": "src", "PYTHONDONTWRITEBYTECODE": "1", "TEMP": str(tmp_path), "TMP": str(tmp_path)}
    env.pop("DEEPSEEK_API_KEY", None)
    env.pop("TAVILY_API_KEY", None)
    log = (tmp_path / "api.log").open("wb")  # never a PIPE nobody reads: Windows pipe buffers are small
    proc = subprocess.Popen(
        [sys.executable, "-m", "deep_research.api", "--mode", "replay", "--port", str(port), "--replay-delay-ms", "50"],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                ready = httpx.get(f"{base}/research", timeout=2)
                if ready.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            assert time.monotonic() < deadline, "the replay server did not come up"
            time.sleep(0.5)
        assert ready.headers["x-deep-research-mode"] == "replay"
        posted = httpx.post(f"{base}/research", json={"query": "anything"}, timeout=10)
        assert posted.status_code == 202
        assert posted.json()["query"] == scenario_by_id(start.DEFAULT_REPLAY_CASE).question
        session_id = posted.json()["session_id"]
        names: list[str] = []
        status_at_first_frame = None
        with httpx.Client(timeout=60) as client, client.stream("GET", f"{base}/research/{session_id}/stream") as stream:
            for line in stream.iter_lines():
                if line.startswith("event: "):
                    names.append(line[7:])
                    if status_at_first_frame is None:
                        status_at_first_frame = client.get(f"{base}/research/{session_id}/status").json()["status"]
        assert status_at_first_frame == "running"
        assert names[0] == "graph.session.started" and names[-1] == "graph.session.completed"
        deadline = time.monotonic() + 10
        while httpx.get(f"{base}/research/{session_id}/status").json()["status"] == "running":
            assert time.monotonic() < deadline
            time.sleep(0.1)
        assert httpx.get(f"{base}/research/{session_id}/status").json()["status"] == "completed"
        assert httpx.get(f"{base}/research/{session_id}/evidence").status_code == 200
    finally:
        proc.terminate()
        proc.wait(timeout=20)
        log.close()
    roots = list(tmp_path.glob("deep-research-replay-*"))
    assert len(roots) == 1, roots  # a forced kill skips the cleanup; the root landed in tmp_path, not %TEMP%
```

- [ ] **Step 2: Run them to verify they fail**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api/test_main.py -q
```
Expected: collection error — `ImportError: cannot import name '__main__'` / `No module named 'deep_research.api.__main__'`.

- [ ] **Step 3: Declare uvicorn**

In `pyproject.toml`, inside `dependencies = [`, add the line `    "uvicorn>=0.30",` after `    "tldextract>=5,<6",`. That line is the whole change: the venv is shared across checkouts and its installs are **never** modified from this worktree (no `pip install`), and every command here already runs with `PYTHONPATH=src`, which resolves this branch's code. Confirm uvicorn is importable:

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
"$PY" -c "import uvicorn; print(uvicorn.__version__)"
```
Expected: a version ≥ 0.30 (today `0.52.1`).

- [ ] **Step 4: Write the start command**

`src/deep_research/api/__main__.py`:

```python
"""Start command for the local API: ``python -m deep_research.api``.

``--mode live`` serves ``create_app()`` exactly as the module-level ``app``.
``--mode replay`` serves the same app around a ``ReplayRunner`` — the real
graph on scripted offline cases — with the e2e harness's two guards held for
the whole server process: the strict configuration load runs before the
runner (so the placeholder credentials must already be in place), and both
guards patch process globals that overlapping per-run scopes would corrupt.
"""

from __future__ import annotations

import argparse
import shutil
import socket
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from deep_research.api.app import create_app

DEFAULT_REPLAY_CASE = "missing-target-triggers-one-extra-pass"
DEFAULT_REPLAY_DELAY_MS = 150


def _port(value: str) -> int:
    port = int(value)
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 0 and 65535")
    return port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m deep_research.api",
        description="Serve the Deep Research API, live or on offline replay cases.",
    )
    parser.add_argument("--mode", choices=("live", "replay"), default="live",
                        help="live: the real graph (needs the configured secrets); replay: scripted offline cases, no network")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=_port, default=8000)
    parser.add_argument("--replay-case", metavar="ID", default=None,
                        help=f"replay mode only; default {DEFAULT_REPLAY_CASE}")
    parser.add_argument("--replay-delay-ms", metavar="N", type=int, default=None,
                        help=f"replay mode only; milliseconds between released events, default {DEFAULT_REPLAY_DELAY_MS}; 0 releases as they arrive")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.mode == "live":
        if args.replay_case is not None or args.replay_delay_ms is not None:
            parser.error("--replay-case and --replay-delay-ms are valid only with --mode replay")
        return args
    from deep_research.e2e_evaluation.replay_matrix import REPLAY_CASE_IDS

    if args.replay_case is None:
        args.replay_case = DEFAULT_REPLAY_CASE
    if args.replay_case not in REPLAY_CASE_IDS:
        parser.error(f"unknown replay case {args.replay_case!r}; known: {', '.join(REPLAY_CASE_IDS)}")
    if args.replay_delay_ms is None:
        args.replay_delay_ms = DEFAULT_REPLAY_DELAY_MS
    if args.replay_delay_ms < 0:
        parser.error("--replay-delay-ms must be 0 or more")
    return args


def build_app(args: argparse.Namespace, *, replay_root: Path | None = None) -> FastAPI:
    """One app for either mode; replay mode needs the root ``main`` created."""
    if args.mode == "live":
        return create_app()
    if replay_root is None:
        raise ValueError("replay mode needs a replay_root")
    from deep_research.api.replay import ReplayCaseMiddleware, ReplayRunner
    from deep_research.e2e_evaluation.replay import production_config_path

    runner = ReplayRunner(default_case=args.replay_case, delay=args.replay_delay_ms / 1000, root=replay_root)
    app = create_app(runner=runner, config_path=str(production_config_path()), mode="replay")
    app.add_middleware(ReplayCaseMiddleware, default_case=args.replay_case)
    return app


def main(argv: Sequence[str] | None = None, *, serve: Callable[..., None] = uvicorn.run) -> int:
    args = parse_args(argv)
    if args.mode == "live":
        serve(build_app(args), host=args.host, port=args.port, log_level="info", timeout_graceful_shutdown=5)
        return 0
    from deep_research.e2e_evaluation.replay import network_denied, offline_credentials

    root = Path(tempfile.mkdtemp(prefix="deep-research-replay-"))
    try:
        # Resolve before the guard: network_denied() refuses getaddrinfo, and a numeric host needs none.
        numeric_host = socket.getaddrinfo(args.host, args.port, type=socket.SOCK_STREAM)[0][4][0]
        app = build_app(args, replay_root=root)
        print(
            f"deep-research api: mode=replay case={args.replay_case} "
            f"delay_ms={args.replay_delay_ms} root={root}",
            flush=True,
        )
        with offline_credentials(), network_denied():
            serve(app, host=numeric_host, port=args.port, log_level="info", timeout_graceful_shutdown=5)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run the start-command tests**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_api/test_main.py -q
```
Expected: `10 passed` (the subprocess test takes 10–20 s: interpreter start, a paced 72-event replay at 50 ms).

- [ ] **Step 6: Smoke the command by hand once**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m deep_research.api --help | head -n 12
```
Expected: usage listing `--mode {live,replay}`, `--host`, `--port`, `--replay-case ID`, `--replay-delay-ms N`.

- [ ] **Step 7: Commit**

```bash
git add src/deep_research/api/__main__.py pyproject.toml tests/test_api/test_main.py && git commit -m "feat(api): python -m deep_research.api start command with replay mode"
```

---

### Task 9: The `web/` toolchain and the prototype's CSS, verbatim (spec §4.3 Files, §4.1 next.config; AC10, AC19)

**Files:**
- Modify: `.gitignore` (root; append)
- Create: `web/package.json` (+ `package-lock.json`), `web/next.config.ts`, `web/tsconfig.json`, `web/vitest.config.ts`, `web/test/setup.ts`, `web/scripts/check-css-verbatim.mjs`, `web/app/globals.css`, `web/app/layout.tsx`, `web/app/page.tsx`

**Interfaces:**
- Produces: the npm scripts `dev`, `build`, `start`, `typecheck`, `test`, `test:e2e`, `capture:events`, `capture:visual`, `check:css`; `app/globals.css` = `index.html:7-1137`; a `layout.tsx` that renders `<div className="app" data-sidebar="expanded">` around `children` (Task 14 fills it in).

- [ ] **Step 1: Ignore the generated trees before they exist**

Append to the root `.gitignore`:

```
# web/ (Next.js console)
web/node_modules/
web/.next/
web/next-env.d.ts
web/test-results/
web/playwright-report/
web/visual/
web/.e2e-tmp/
.launch-*.pid
```

- [ ] **Step 2: Create the package and install the exact dependency set**

```bash
mkdir -p web/app web/lib web/components web/scripts web/test/fixtures/events web/e2e && cd web && npm init -y >/dev/null && npm pkg set private=true --json && npm pkg set name=deep-research-console && npm pkg delete main && \
npm install next react react-dom react-markdown remark-gfm && \
npm install -D typescript vitest @testing-library/react @testing-library/dom @playwright/test @types/react @types/react-dom @types/node jsdom && \
npm pkg set scripts.dev="next dev" scripts.build="next build" scripts.start="next start" scripts.typecheck="tsc --noEmit" scripts.test="vitest run" "scripts.test:e2e=next build && playwright test --project chromium" "scripts.capture:events=node scripts/capture-replay-events.mjs" "scripts.capture:visual=playwright test --project visual" "scripts.check:css=node scripts/check-css-verbatim.mjs" && \
node -e "const p=require('./package.json');console.log(Object.keys(p.dependencies).sort().join(' '));console.log(Object.keys(p.devDependencies).sort().join(' '))"
```
Expected, the last two lines exactly: `next react react-dom react-markdown remark-gfm` and `@playwright/test @testing-library/dom @testing-library/react @types/node @types/react @types/react-dom jsdom typescript vitest` (AC19). If `npm install` fails with `EPERM`/`EBUSY`, pause OneDrive syncing and rerun (spec §6); if it still fails, junction `web/node_modules` to a directory outside OneDrive (`mklink /J`) and note it in `web/README.md` (Task 20). Then `npx playwright install chromium` once.

- [ ] **Step 3: Write the config files**

`web/next.config.ts`:

```ts
import type { NextConfig } from "next";

// compress: false — the app is local and an SSE body must never wait in a gzip buffer (spec §4.1).
// No rewrites: /api/* is a route handler that streams (app/api/[...path]/route.ts).
const config: NextConfig = { compress: false, reactStrictMode: true };

export default config;
```

`web/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": false,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "paths": { "@/*": ["./*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules", "test-results", "visual", ".e2e-tmp"]
}
```

`web/vitest.config.ts` (the automatic JSX runtime through the installed Vite major's transformer, spec §4.3):

```ts
import { createRequire } from "node:module";
import { defineConfig } from "vitest/config";

const require = createRequire(import.meta.url);
const viteMajor = Number(require("vite/package.json").version.split(".")[0]);
// Next's tsconfig keeps jsx: "preserve"; tests need the automatic runtime. Vite ≤ 7 transforms
// with esbuild, Vite 8 with oxc — the option lives under a different key in each.
const jsxRuntime =
  viteMajor >= 8
    ? { oxc: { jsx: { runtime: "automatic" } } }
    : { esbuild: { jsx: "automatic" } };

export default defineConfig({
  ...(jsxRuntime as object),
  test: {
    environment: "jsdom",
    setupFiles: ["test/setup.ts"],
    include: ["test/**/*.test.ts", "test/**/*.test.tsx"],
  },
});
```

`web/test/setup.ts` — jsdom implements neither `window.matchMedia` nor `ResizeObserver` (jsdom 30 checked by the reviewer); the components call `matchMedia` for the drawer breakpoint, so the test environment gets a stub that answers "not the drawer":

```ts
// Vitest setup: jsdom lacks window.matchMedia and ResizeObserver. The app reads matchMedia for the
// ≤ 1080 px drawer; tests run at "desktop" (matches: false).
if (typeof window !== "undefined") {
  if (typeof window.matchMedia !== "function") {
    window.matchMedia = (query: string): MediaQueryList => ({
      matches: false, media: query, onchange: null,
      addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {},
      dispatchEvent() { return false; },
    });
  }
  if (typeof (window as unknown as { ResizeObserver?: unknown }).ResizeObserver !== "function") {
    (window as unknown as { ResizeObserver: unknown }).ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  }
}
```

If Vitest rejects the `oxc` key on the installed Vite 8, read the option's name from `node_modules/vite/dist/node/index.d.ts` (search `jsx` under the transformer options) and use that name; record the name used in the commit message.

- [ ] **Step 4: Write the CSS check (the failing test)**

`web/scripts/check-css-verbatim.mjs`:

```js
// AC10: app/globals.css begins with docs/design/prototype/index.html lines 7-1137, line for line
// (CRLF-normalised), and anything after that sits under the app-only header with no colour literal.
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

const web = process.cwd();
const html = path.join(web, "..", "docs", "design", "prototype", "index.html");
const css = path.join(web, "app", "globals.css");
const lines = (file) => readFileSync(file, "utf8").replace(/\r\n/g, "\n").split("\n");
if (!existsSync(css)) { console.log("MISMATCH: app/globals.css does not exist"); process.exit(1); }
const expected = lines(html).slice(6, 1137); // 1-based lines 7..1137 → 1131 lines
const actual = lines(css);
for (let i = 0; i < expected.length; i++) {
  if (actual[i] !== expected[i]) {
    console.log(`MISMATCH at globals.css line ${i + 1} (index.html line ${i + 7})`);
    console.log(`  expected: ${JSON.stringify(expected[i])}`);
    console.log(`  actual:   ${JSON.stringify(actual[i])}`);
    process.exit(1);
  }
}
const rest = actual.slice(expected.length).join("\n").trim();
const HEADER = "/* ═══ 2026-09-27: app-only additions ═══ */";
if (rest && !rest.startsWith(HEADER)) { console.log("MISMATCH: text after line 1131 is not under the app-only header"); process.exit(1); }
if (/#[0-9a-fA-F]{3,8}\b|\b(?:rgb|rgba|hsl|oklch)\(/.test(rest)) { console.log("MISMATCH: colour literal in the app-only block"); process.exit(1); }
console.log("OK");
```

Run it:

```bash
cd web && npm run -s check:css
```
Expected: `MISMATCH: app/globals.css does not exist`, exit code 1.

- [ ] **Step 5: Copy the CSS verbatim**

```bash
cd web && node -e "
const fs=require('node:fs'),path=require('node:path');
const src=fs.readFileSync(path.join('..','docs','design','prototype','index.html'),'utf8').replace(/\r\n/g,'\n').split('\n');
fs.writeFileSync(path.join('app','globals.css'), src.slice(6,1137).join('\n')+'\n');
console.log('wrote', 1131, 'lines');" && npm run -s check:css
```
Expected: `wrote 1131 lines` then `OK`.

- [ ] **Step 6: The shell skeleton and the idle page**

`web/app/layout.tsx`:

```tsx
import type { ReactNode } from "react";
import "./globals.css";

export const metadata = { title: "Deep Research — console" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="app" id="app" data-sidebar="expanded">
          <div className="main">
            <div className="viewport" id="viewport">{children}</div>
          </div>
        </div>
      </body>
    </html>
  );
}
```

`web/app/page.tsx`:

```tsx
export default function IdlePage() {
  return (
    <section className="stage is-on" id="stage-idle" aria-labelledby="idle-h">
      <div className="stack" style={{ gap: "var(--space-8)", maxWidth: "var(--reading-max)", marginInline: "auto" }}>
        <div className="stack-2" style={{ paddingTop: "var(--space-8)" }}>
          <h1 className="display" id="idle-h">Ask anything.</h1>
          <p className="lead">
            A run takes a few minutes; harder questions take longer. You can leave — the run keeps going, and its
            report will be here when you return.
          </p>
        </div>
      </div>
    </section>
  );
}
```

- [ ] **Step 7: Build**

```bash
cd web && npm run -s typecheck && npm run -s build 2>&1 | tail -n 15
```
Expected: `tsc` prints nothing; the build ends with the route table listing `/` and no error.

- [ ] **Step 8: Commit**

```bash
git add .gitignore web/package.json web/package-lock.json web/next.config.ts web/tsconfig.json web/vitest.config.ts web/test/setup.ts web/scripts/check-css-verbatim.mjs web/app/globals.css web/app/layout.tsx web/app/page.tsx && git commit -m "feat(web): Next.js toolchain and the prototype CSS, verbatim"
```

---

### Task 10: `lib/format.ts` — the display helpers (spec §4.3 `lib/format.ts`; T-W2; AC12)

**Files:**
- Create: `web/lib/format.ts`, `web/lib/api.ts` (types only in this task — the functions come in Task 12)
- Test: `web/test/format.test.ts`

**Interfaces:**
- Produces (from `lib/api.ts`): the types `SessionStatus`, `ApiMode`, `ResearchRequest`, `ResearchError`, `CoverageProgress`, `EvidenceCounts`, `ResearchSessionResponse`, `SessionListResponse`, `ResearchEvent`, `ValidationIssue`, `ApiErrorBody`, `EvidenceSource`, `EvidenceFigure`, `EvidenceFinding`, `EvidenceNotFound`, `EvidenceRefused`, `EvidenceResponse` exactly as spec §4.3 lists them.
- Produces (from `lib/format.ts`): `SessionView`, `toSessionView(s, passes)`, `STATUS`, `passNumber`, `passTotal`, `passText`, `fmtScore`, `notFoundClause`, `statusNote`, `fmtDur`, `fmtSeconds`, `fmtClock`, `fmtElapsed`, `meterClass`, `HALT_HEADLINES`, `PILL_TEXT`, `VERIFICATION_TEXT` — bodies from `index.html:1704-1784`, `:3267-3270`, `:3708-3717`, `:3483`, `:3485`.

- [ ] **Step 1: Write the API types**

`web/lib/api.ts` (types only for now; Task 12 appends the functions below them):

```ts
// Mirrors src/deep_research/api/models.py (+ query, SessionListResponse, the E1 models).
export type SessionStatus = "running" | "completed" | "max_iterations" | "incomplete" | "failed";
export type ApiMode = "live" | "replay";

export interface ResearchRequest {
  query: string;
  max_iterations: number | null;
  output_format: "markdown";
  config_overrides: Record<string, unknown>;
}
export interface ResearchError {
  error_type: string; source: string; message: string; recoverable: boolean; timestamp: string;
  details: Record<string, unknown>;
}
export interface CoverageProgress {
  required_targets: number; answered_targets: number;
  missing_required_target_ids: string[]; not_found_target_ids: string[];
}
export interface EvidenceCounts {
  read_records: number; network_reads: number; cache_reads: number; unique_works: number; publishers: number;
  source_urls: number; findings: number; assessed_sources: number; cited_assessed_sources: number;
  verified_findings: number; corrected_findings: number; quoted_findings: number; dropped_findings: number;
  context_unchecked_findings: number; cited_findings: number;
}
export interface ResearchSessionResponse {
  session_id: string; query: string; status: SessionStatus; current_agent: string | null; iteration: number;
  started_at: string; finished_at: string | null; report_path: string | null; trace_url: string | null;
  errors: ResearchError[];
  evidence_path: string | null; quality_path: string | null; quality_contract_version: string | null;
  semantic_review_status: string | null; semantic_review_score: number | null; duration_seconds: number | null;
  coverage: CoverageProgress | null; evidence_counts: EvidenceCounts | null;
}
export interface SessionListResponse { sessions: ResearchSessionResponse[] }
export interface ResearchEvent {
  event_type: string; source: string; message: string; timestamp: string; metadata: Record<string, unknown>;
}
export interface ValidationIssue { location: string; type: string }
export interface ApiErrorBody {
  code: string; message: string; reason: string | null; issues: ValidationIssue[];
  target?: string; // the proxy's 502 api_unreachable carries the API origin here
}
export interface EvidenceSource {
  url: string; title: string; organisation: string; evaluation_status: string | null; low_confidence: boolean;
  authority_score: number | null; recency_score: number | null; relevance_score: number | null; overall_score: number | null;
}
export interface EvidenceFigure {
  value: string; kept: boolean; period: string | null; scope: string | null; organisation: string | null;
  attribution: "own" | "relayed" | "unattributed" | null; kind: "actual" | "forecast" | null; release: string | null;
  evidence_words: string | null; corrected: boolean; dropped_reason: string | null; reason: string | null;
}
export interface EvidenceFinding {
  label: string; status: "verified" | "verified_corrected" | "quoted" | "dropped" | null; dropped_reason: string | null;
  context_unchecked: boolean; cited: boolean; target_ids: string[]; content: string; snippet: string | null;
  passage: string | null; source: EvidenceSource; figures: EvidenceFigure[];
}
export interface EvidenceNotFound { target_id: string; question: string; queries: string[]; pages_read: string[]; searched: boolean }
export interface EvidenceRefused { where: string; text: string; reason: string; finding_labels: string[] }
export interface EvidenceResponse {
  session_id: string; iteration: number; findings: EvidenceFinding[]; not_found: EvidenceNotFound[]; refused: EvidenceRefused[];
}
```

- [ ] **Step 2: Write the failing tests**

`web/test/format.test.ts` — the six chip outcomes of the design spec §4.2 table, as API responses:

```ts
import { describe, expect, it } from "vitest";
import type { ResearchSessionResponse } from "../lib/api";
import { fmtClock, fmtScore, fmtSeconds, meterClass, notFoundClause, passText, statusNote, toSessionView } from "../lib/format";

const base: ResearchSessionResponse = {
  session_id: "s", query: "q", status: "running", current_agent: null, iteration: 0,
  started_at: "2026-09-16T14:02:11Z", finished_at: null, report_path: null, trace_url: null, errors: [],
  evidence_path: null, quality_path: null, quality_contract_version: null,
  semantic_review_status: null, semantic_review_score: null, duration_seconds: null, coverage: null, evidence_counts: null,
};
const coverage = (notFound: string[]) => ({ required_targets: 4, answered_targets: 4 - notFound.length, missing_required_target_ids: [], not_found_target_ids: notFound });

describe("statusNote — one rule per API status", () => {
  it("running reads the pass from the stream", () => {
    expect(statusNote(toSessionView(base, 2))).toBe("pass 1 of 2");
    expect(statusNote(toSessionView(base, null))).toBe("pass 1");
  });
  it("completed → review accepted · score + not-found clause", () => {
    const s = { ...base, status: "completed" as const, iteration: 1, semantic_review_status: "scored", semantic_review_score: 0.86, coverage: coverage(["topic-02-target-01"]) };
    expect(statusNote(toSessionView(s, 2))).toBe("review accepted · 0.86 · 1 target not found");
  });
  it("max_iterations → extra passes used + clause", () => {
    const s = { ...base, status: "max_iterations" as const, coverage: coverage(["a"]) };
    expect(statusNote(toSessionView(s, 2))).toBe("extra passes used · 1 target not found");
    expect(statusNote(toSessionView({ ...s, coverage: coverage(["a", "b", "c"]) }, 2))).toBe("extra passes used · 3 targets not found");
    expect(statusNote(toSessionView({ ...s, coverage: coverage([]) }, 2))).toBe("extra passes used");
  });
  it("incomplete with a scored review → not accepted · score", () => {
    const s = { ...base, status: "incomplete" as const, semantic_review_status: "scored", semantic_review_score: 0.71 };
    expect(statusNote(toSessionView(s, 1))).toBe("not accepted · 0.71");
  });
  it("incomplete without a score → review unavailable", () => {
    const s = { ...base, status: "incomplete" as const, semantic_review_status: "provider_failed", semantic_review_score: null };
    expect(statusNote(toSessionView(s, 2))).toBe("review unavailable");
  });
  it("failed → halted", () => {
    expect(statusNote(toSessionView({ ...base, status: "failed" }, 2))).toBe("halted");
  });
});

describe("helpers", () => {
  it("passText adds one to the zero-based iteration and drops the clause without a ceiling", () => {
    expect(passText(toSessionView({ ...base, iteration: 1 }, 2))).toBe("pass 2 of 2");
    expect(passText(toSessionView({ ...base, iteration: 1 }, null))).toBe("pass 2");
  });
  it("fmtScore prints two decimals and null for no score", () => {
    expect(fmtScore(0.8)).toBe("0.80");
    expect(fmtScore(null)).toBeNull();
    expect(fmtScore(undefined)).toBeNull();
  });
  it("notFoundClause pluralises", () => {
    expect(notFoundClause(toSessionView({ ...base, coverage: coverage([]) }, 1))).toBe("");
    expect(notFoundClause(toSessionView({ ...base, coverage: coverage(["a"]) }, 1))).toBe(" · 1 target not found");
    expect(notFoundClause(toSessionView({ ...base, coverage: coverage(["a", "b"]) }, 1))).toBe(" · 2 targets not found");
  });
  it("meterClass paints exactly 0.80 yellow on purpose", () => {
    expect(meterClass(0.8)).toBe("warn");
    expect(meterClass(0.81)).toBe("ok");
    expect(meterClass(0.39)).toBe("danger");
    expect(meterClass(Number.NaN)).toBeNull();
  });
  it("time helpers", () => {
    expect(fmtSeconds(552)).toBe("9m 12s");
    expect(fmtSeconds(0.207)).toBe("0m 00s");
    expect(fmtSeconds(null)).toBeNull();
    expect(fmtClock("2026-09-16T14:02:11Z")).toBe("14:02Z");
    expect(fmtClock(null)).toBeNull();
  });
});
```

- [ ] **Step 3: Run them to verify they fail**

```bash
cd web && npx vitest run test/format.test.ts 2>&1 | tail -n 8
```
Expected: the file fails to import (`Failed to load ../lib/format`).

- [ ] **Step 4: Port the helpers**

`web/lib/format.ts`:

```ts
// The prototype's display helpers, ported from docs/design/prototype/index.html:1704-1784,
// :3267-3270, :3483-3485, :3708-3717. Bodies unchanged; types added; toSessionView adapts the
// API's flat fields to the prototype's session shape so statusNote/passText keep their bodies.
import type { CoverageProgress, ResearchSessionResponse, SessionStatus } from "./api";

export interface SessionView {
  status: SessionStatus;
  iteration: number;
  passes: number | null;
  review: { status: string | null; score: number | null } | null;
  coverage: CoverageProgress | null;
}

export function toSessionView(s: ResearchSessionResponse, passes: number | null): SessionView {
  const review =
    s.semantic_review_status === null && s.semantic_review_score === null
      ? null
      : { status: s.semantic_review_status, score: s.semantic_review_score };
  return { status: s.status, iteration: s.iteration, passes, review, coverage: s.coverage };
}

/* Label and dot per API status (api/models.py:13-19). The second clause is built by statusNote(). */
export const STATUS: Record<SessionStatus, { label: string; dot: "dot-live" | "dot-ok" | "dot-warn" | "dot-danger" }> = {
  running: { label: "Running", dot: "dot-live" },
  completed: { label: "Completed", dot: "dot-ok" },
  max_iterations: { label: "Partially completed", dot: "dot-warn" },
  incomplete: { label: "Partially completed", dot: "dot-warn" },
  failed: { label: "Failed", dot: "dot-danger" },
};

/* `iteration` is the API's zero-based value; the interface counts passes from 1. This is the only
   place the offset lives. */
export function passNumber(iteration: unknown): number {
  const n = Number(iteration);
  return (Number.isFinite(n) ? n : 0) + 1;
}
/* The ceiling: 1 + max_extra_passes. A session that has none drops the "of P" clause. */
export function passTotal(s: { passes: number | null }): number | null {
  const n = Number(s && s.passes);
  return Number.isFinite(n) && n > 0 ? n : null;
}
export function passText(s: SessionView): string {
  const total = passTotal(s);
  return "pass " + passNumber(s.iteration) + (total === null ? "" : " of " + total);
}
/* Scores print with two decimals; null is "no score", never 0. */
export function fmtScore(v: unknown): string | null {
  return typeof v === "number" && Number.isFinite(v) ? v.toFixed(2) : null;
}
/* coverage.not_found_target_ids → " · 1 target not found" / " · n targets not found" / "" */
export function notFoundClause(s: SessionView): string {
  const ids = (s && s.coverage && s.coverage.not_found_target_ids) || [];
  if (!ids.length) return "";
  return " · " + ids.length + (ids.length === 1 ? " target not found" : " targets not found");
}
/* The chip's second clause, one rule per API status. */
export function statusNote(s: SessionView): string {
  const score = fmtScore(s.review && s.review.score);
  switch (s.status) {
    case "completed": return "review accepted" + (score === null ? "" : " · " + score) + notFoundClause(s);
    case "max_iterations": return "extra passes used" + notFoundClause(s);
    case "incomplete": return s.review && s.review.status === "scored" && score !== null ? "not accepted · " + score : "review unavailable";
    case "failed": return "halted";
    default: return passText(s);
  }
}
export function fmtDur(a: string | null, b: string | null): string | null {
  if (!a || !b) return null;
  const ms = new Date(b).getTime() - new Date(a).getTime();
  if (Number.isNaN(ms) || ms < 0) return null;
  const s = Math.round(ms / 1000);
  return Math.floor(s / 60) + "m " + String(s % 60).padStart(2, "0") + "s";
}
export function fmtSeconds(s: number | null): string | null {
  if (typeof s !== "number" || !Number.isFinite(s) || s < 0) return null;
  return Math.floor(s / 60) + "m " + String(Math.round(s % 60)).padStart(2, "0") + "s";
}
export function fmtClock(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return String(d.getUTCHours()).padStart(2, "0") + ":" + String(d.getUTCMinutes()).padStart(2, "0") + "Z";
}
export function fmtElapsed(sec: number): string {
  return String(Math.floor(sec / 60)).padStart(2, "0") + ":" + String(sec % 60).padStart(2, "0");
}
/* Meter colour is a judgement about the number (DESIGN.md §3.6): v > 0.8 is green, so exactly 0.80
   paints yellow on purpose while acceptance is ≥ 0.80. Do not "fix" either side. */
export function meterClass(v: number): "ok" | "warn" | "danger" | null {
  if (Number.isNaN(v)) return null;
  return v > 0.8 ? "ok" : v >= 0.4 ? "warn" : "danger";
}
/* Halting types (graph/state.py:141-150) in plain words; the two api.research.* types are the
   API layer's own failures, which never emit graph.session.completed. */
export const HALT_HEADLINES: Readonly<Record<string, string>> = {
  graph_planning_failed: "Planning failed",
  graph_provider_configuration_error: "Model provider misconfigured",
  graph_agent_configuration_error: "Agent misconfigured",
  graph_invalid_agent_state: "Invalid agent state",
  graph_invalid_route: "Invalid route",
  graph_request_attempt_limit_exceeded: "Request attempt limit reached",
  "api.research.configuration_error": "Service configuration error",
  "api.research.failed": "Research run failed",
};
export const PILL_TEXT: Readonly<Record<string, string>> = {
  verified: "verified", verified_corrected: "corrected", quoted: "quoted", dropped: "dropped",
  not_checked: "not checked", not_found: "not found", refused: "refused",
};
/* The evidence log's verbs (agents/report.py:1929-1933). */
export const VERIFICATION_TEXT: Readonly<Record<string, string>> = {
  verified: "verified",
  verified_corrected: "verified with corrections",
  quoted: "quoted (snippet found on the page; not checked for context)",
  not_checked: "not checked",
};
```

- [ ] **Step 5: Run the tests**

```bash
cd web && npx vitest run test/format.test.ts 2>&1 | tail -n 6
```
Expected: `Tests  11 passed`.

- [ ] **Step 6: Commit**

```bash
git add web/lib/api.ts web/lib/format.ts web/test/format.test.ts && git commit -m "feat(web): API types and the prototype's display helpers"
```

---

### Task 11: `lib/run-state.ts` — the event core, ported unchanged, proven on real captures (spec §4.3 `lib/run-state.ts`; T-W1; AC11)

**Files:**
- Create: `web/lib/run-state.ts`, `web/scripts/capture-replay-events.mjs`, `web/test/fixtures/events/missing-target-triggers-one-extra-pass.json`, `web/test/fixtures/events/scoped-redraft-after-a-named-defect.json`
- Test: `web/test/run-state.test.ts`

**Interfaces:**
- Consumes: the API in replay mode (Task 8) for the capture; `ResearchEvent`, `SessionStatus` (Task 10).
- Produces: everything spec §4.3 lists under `lib/run-state.ts` — `NodeId`, `Mark`, `PaintedMark`, `Stage`, `STAGES`, `AGENT_ORDER`, `ARCS`, `BLURB`, `Counters`, `LoopTag`, `RunState`, `RunEvent`, `Handler`, `EVENT_HANDLERS`, `emptyCounters`, `newRunState`, `plural`, `applyEvent`, `marksFor`, `CounterRow`, `COUNTER_ROWS`, `toRunEvent`, `replayRun`, `failedMarks`. Fixture file shape: `{ case_id, captured_at, api_mode, status: ResearchSessionResponse, events: ResearchEvent[] }`.

- [ ] **Step 1: Write the capture script**

`web/scripts/capture-replay-events.mjs`:

```js
// Record one replay session's frames and its final /status into test/fixtures/events/<case>.json.
// usage (with the API running in replay mode, ideally --replay-delay-ms 0):
//   DEEP_RESEARCH_API_URL=http://127.0.0.1:8010 node scripts/capture-replay-events.mjs <case-id> [<case-id> ...]
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

const api = process.env.DEEP_RESEARCH_API_URL ?? "http://127.0.0.1:8010";
const cases = process.argv.slice(2);
if (!cases.length) { console.error("usage: capture-replay-events.mjs <case-id> ..."); process.exit(2); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const frames = (text) =>
  text.split(/\r?\n\r?\n/).filter((f) => f.includes("data: ")).map((f) =>
    JSON.parse(f.split(/\r?\n/).filter((l) => l.startsWith("data: ")).map((l) => l.slice(6)).join("\n")));

for (const caseId of cases) {
  const posted = await fetch(`${api}/research`, {
    method: "POST",
    headers: { "content-type": "application/json", "x-replay-case": caseId },
    body: JSON.stringify({ query: "capture", max_iterations: null, output_format: "markdown", config_overrides: {} }),
  });
  if (posted.status !== 202) throw new Error(`POST /research → ${posted.status}: ${await posted.text()}`);
  const { session_id } = await posted.json();
  const stream = await fetch(`${api}/research/${session_id}/stream`);
  const events = frames(await stream.text()); // the stream closes at the terminal status
  let status = null;
  for (let i = 0; i < 100; i++) {
    status = await (await fetch(`${api}/research/${session_id}/status`)).json();
    if (status.status !== "running") break;
    await sleep(100);
  }
  if (!status || status.status === "running") throw new Error(`${caseId}: the session did not finish`);
  const out = path.join("test", "fixtures", "events", `${caseId}.json`);
  mkdirSync(path.dirname(out), { recursive: true });
  const record = { case_id: caseId, captured_at: new Date().toISOString(), api_mode: stream.headers.get("x-deep-research-mode"), status, events };
  writeFileSync(out, JSON.stringify(record, null, 1) + "\n");
  console.log(`${out}: ${events.length} events, ${status.status}/${status.iteration}`);
}
```

- [ ] **Step 2: Capture the two cases from the real API in replay mode**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m deep_research.api --mode replay --port 8010 --replay-delay-ms 0 & API_PID=$!
for i in $(seq 1 60); do node -e "fetch('http://127.0.0.1:8010/research').then(r=>process.exit(r.status===200?0:1)).catch(()=>process.exit(1))" && break; sleep 1; done
cd web && DEEP_RESEARCH_API_URL=http://127.0.0.1:8010 node scripts/capture-replay-events.mjs missing-target-triggers-one-extra-pass scoped-redraft-after-a-named-defect; cd ..
kill $API_PID
```
Expected: two lines — `test\fixtures\events\missing-target-triggers-one-extra-pass.json: 72 events, completed/1` and `test\fixtures\events\scoped-redraft-after-a-named-defect.json: 49 events, completed/0` (the counts measured in spec §3.3).

- [ ] **Step 3: Write the failing tests**

`web/test/run-state.test.ts`:

```ts
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { ResearchEvent, ResearchSessionResponse } from "../lib/api";
import { AGENT_ORDER, EVENT_HANDLERS, STAGES, applyEvent, failedMarks, newRunState, replayRun, toRunEvent, type RunState } from "../lib/run-state";

interface Capture { case_id: string; status: ResearchSessionResponse; events: ResearchEvent[] }
const load = (caseId: string): Capture =>
  JSON.parse(readFileSync(fileURLToPath(new URL(`./fixtures/events/${caseId}.json`, import.meta.url)), "utf8"));
const extraPass = load("missing-target-triggers-one-extra-pass");
const redraft = load("scoped-redraft-after-a-named-defect");

/* One snapshot per frame: the state after frames 1..k, as a late subscriber replaying k frames sees it. */
function snapshots(events: ResearchEvent[], passes: number): RunState[] {
  const run = newRunState(passes);
  return events.map((e) => { applyEvent(run, toRunEvent(e)); return structuredClone(run); });
}
const at = (events: ResearchEvent[], pred: (e: ResearchEvent) => boolean, from = 0) => {
  const i = events.findIndex((e, k) => k >= from && pred(e));
  if (i < 0) throw new Error("event not found");
  return i;
};
const P = (c: Capture) => (c.events[0].metadata.max_extra_passes as number) + 1;

describe("the port is the prototype's core", () => {
  it("has the seven rows and the sixteen handlers", () => {
    expect(STAGES.map((s) => s.id)).toEqual(["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]);
    expect(AGENT_ORDER).toEqual(STAGES.map((s) => s.id));
    expect(Object.keys(EVENT_HANDLERS).sort()).toEqual([
      "evidence_verifier.verification.completed", "graph.extra_pass.started", "graph.node.completed", "graph.node.skipped",
      "graph.node.started", "graph.report.redraft_requested", "graph.report.reviewed", "graph.route.decided",
      "graph.session.completed", "graph.session.started", "planner.planning.completed", "report_writer.report.written",
      "researcher.research.completed", "researcher.sub_topic.completed", "researcher.tool_call", "source_evaluator.evaluation.completed",
    ]);
  });
});

describe("(a) terminal agreement with the server after the last frame", () => {
  for (const capture of [extraPass, redraft]) {
    it(capture.case_id, () => {
      const run = replayRun(capture.events, P(capture));
      expect(run.finalStatus).toBe(capture.status.status);
      expect(run.pass).toBe(capture.status.iteration + 1);
      expect(run.maxPasses).toBe(P(capture));
      expect(run.active).toBeNull();
      expect(run.loop).toBe("off");
      expect(run.arc).toBeNull();
      expect(run.tag).toBeNull();
    });
  }
});

describe("(b) the extra pass", () => {
  const snaps = snapshots(extraPass.events, P(extraPass));
  const events = extraPass.events;
  const decided = at(events, (e) => e.event_type === "graph.route.decided" && e.metadata.destination === "extra_pass");
  const reviewerDone = at(events, (e) => e.event_type === "graph.node.completed" && e.metadata.node === "report_reviewer", decided);
  const hop = at(events, (e) => e.event_type === "graph.extra_pass.started", decided);
  it("the route decision re-arms rows 2–6, moves the active row and lights the arc", () => {
    const s = snaps[decided];
    expect(s.marks.planner).toBe("done");
    expect(s.active).toBe("researcher");
    for (const id of ["researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]) expect(s.marks).not.toHaveProperty(id);
    expect(s.loop).toBe("flowing");
    expect(s.arc).toBe("extra_pass");
  });
  it("the reviewer's own completion is inert after the decision", () => {
    const s = snaps[reviewerDone];
    expect(s.marks).not.toHaveProperty("report_reviewer");
    expect(s.active).toBe("researcher");
  });
  it("the hop settles the loop, advances the pass, sets the tag and resets this-pass counters", () => {
    const s = snaps[hop];
    expect(s.loop).toBe("settled");
    expect(s.pass).toBe(2);
    expect(s.tag?.kind).toBe("extra_pass");
    expect(s.tag?.text).toBe("1 required target had no verified finding");
    expect(s.captions.researcher).toBe("1 missing target only");
    expect(s.countersPass).toBe(2);
    for (const key of ["subTopicsDone", "subTopicsResearched", "subTopicsTotal", "findings", "verified", "corrected", "dropped"] as const) expect(s.counters[key]).toBeNull();
  });
  it("(d) counters come from the stream", () => {
    const last = snaps[snaps.length - 1];
    expect(last.counters.toolCalls).toBe(events.filter((e) => e.event_type === "researcher.tool_call").length);
    const evaluations = events.filter((e) => e.event_type === "source_evaluator.evaluation.completed");
    expect(last.counters.sources).toBe(evaluations[evaluations.length - 1].metadata.source_count);
  });
});

describe("(c) the redraft", () => {
  const snaps = snapshots(redraft.events, P(redraft));
  const events = redraft.events;
  const decided = at(events, (e) => e.event_type === "graph.route.decided" && e.metadata.destination === "redraft");
  const requested = at(events, (e) => e.event_type === "graph.report.redraft_requested", decided);
  it("re-arms rows 5–6 only and lights the redraft arc", () => {
    const s = snaps[decided];
    for (const id of ["planner", "researcher", "source_evaluator", "evidence_verifier"]) expect(s.marks[id as keyof typeof s.marks]).toBe("done");
    expect(s.active).toBe("report_writer");
    expect(s.marks).not.toHaveProperty("report_reviewer");
    expect(s.arc).toBe("redraft");
    expect(s.loop).toBe("flowing");
  });
  it("does not advance the pass and names the defect", () => {
    const s = snaps[requested];
    expect(s.pass).toBe(1);
    expect(s.tag).toEqual({ kind: "redraft", label: "redraft", text: "Reviewer named 1 material defect" });
  });
});

describe("(e) the halted run (the prototype's HALTED_EVENTS, index.html:2829-2838)", () => {
  const md = (m: Record<string, unknown>) => m;
  const halted = [
    { type: "graph.session.started", metadata: md({ session_id: "2ad900b1", max_extra_passes: 1, checkpointing: false }) },
    { type: "graph.node.started", metadata: md({ node: "planner", iteration: 0 }) },
    ...["researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer"].map((node) => ({ type: "graph.node.skipped", metadata: md({ node, iteration: 0, reason: "halted" }) })),
    { type: "graph.session.completed", metadata: md({ status: "failed", iteration: 0, error_count: 1, has_report: false }) },
  ];
  it("marks the halting row active, the rest skipped, Publishing skipped, counters unreached", () => {
    const run = newRunState(2);
    for (const ev of halted) applyEvent(run, ev);
    const marks = failedMarks(run, "failed");
    expect(marks.planner).toBe("active");
    for (const id of ["researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]) expect(marks[id as keyof typeof marks]).toBe("skipped");
    expect(Object.values(run.counters).filter((v) => v !== null && v !== false)).toEqual([]);
  });
  it("an API-level failure (no graph.session.completed) still skips Publishing by the session status", () => {
    const run = newRunState(2);
    applyEvent(run, halted[0]);
    applyEvent(run, halted[1]);
    expect(failedMarks(run, "failed").finalize_report).toBe("skipped");
    expect(failedMarks(run, "running").finalize_report).toBeUndefined();
  });
});
```

- [ ] **Step 4: Run them to verify they fail**

```bash
cd web && npx vitest run test/run-state.test.ts 2>&1 | tail -n 6
```
Expected: the file fails to import (`Failed to load ../lib/run-state`).

- [ ] **Step 5: Port the core**

`web/lib/run-state.ts` — the bodies are `index.html:2456-2465`, `:2501-2504`, `:2885-3054`, `:3095-3103` with types added and nothing else changed; the one adaptation is `toRunEvent`:

```ts
// The prototype's event core (docs/design/prototype/index.html:2456-2465, :2501-2504, :2885-3054,
// :3095-3103), ported unchanged: every handler reads only `md.<key>` and the state after event k
// depends only on events 1..k, so bursts, ticks and a replay from event 1 paint the same screen.
import type { ResearchEvent, SessionStatus } from "./api";

export type NodeId = "planner" | "researcher" | "source_evaluator" | "evidence_verifier" | "report_writer" | "report_reviewer" | "finalize_report";
export type Mark = "done" | "loop" | "skipped";
export type PaintedMark = Mark | "active";
export interface Stage { id: NodeId; label: string; meta: string }

export const STAGES: readonly Stage[] = [
  { id: "planner", label: "Planning", meta: "1–10 sub-topics" },
  { id: "researcher", label: "Researching", meta: "search · scrape · read · memory" },
  { id: "source_evaluator", label: "Evaluating sources", meta: "authority · recency · relevance" },
  { id: "evidence_verifier", label: "Verifying evidence", meta: "snippet on page · context check" },
  { id: "report_writer", label: "Writing report", meta: "verified findings only · statement check" },
  { id: "report_reviewer", label: "Reviewing", meta: "7 dimensions · accept at mean 0.80" },
  { id: "finalize_report", label: "Publishing", meta: "report · evidence log · quality record" },
];
export const AGENT_ORDER: readonly NodeId[] = STAGES.map((s) => s.id);
export const ARCS: Record<"extra_pass" | "redraft", { from: NodeId; to: NodeId }> = {
  extra_pass: { from: "report_reviewer", to: "researcher" },
  redraft: { from: "report_reviewer", to: "report_writer" },
};
export const BLURB: Record<NodeId, string> = {
  planner: "Turning the question into sub-topics and evidence targets.",
  researcher: "Searching and reading; every finding keeps a verbatim snippet.",
  source_evaluator: "Scoring every source behind the findings.",
  evidence_verifier: "Checking each snippet is on its page, then each figure's context.",
  report_writer: "Drafting from verified findings; every sentence is checked against what it cites.",
  report_reviewer: "Scoring the report; accepted at a mean of 0.80 with no material defect.",
  finalize_report: "Publishing the report, the evidence log and the quality record.",
};

export interface Counters {
  subTopicsDone: number | null; subTopicsResearched: number | null; subTopicsTotal: number | null; toolCalls: number | null;
  findings: number | null; sources: number | null; verified: number | null; corrected: number | null; dropped: number | null;
  statements: number | null; refused: number | null; reviewSeen: boolean; reviewScore: number | null;
}
export interface LoopTag { kind: "extra_pass" | "redraft"; label: string; text: string }
export interface RunState {
  marks: Partial<Record<NodeId, Mark>>;   /* node id → "done" | "loop" | "skipped"; the active row is derived */
  active: NodeId | null;                  /* the "Now" row: the successor of the last graph.node.completed */
  openNode: NodeId | null;                /* the last graph.node.started with no graph.node.completed — the halting row */
  pass: number; maxPasses: number;
  loop: "off" | "flowing" | "settled"; arc: "extra_pass" | "redraft" | null;
  loopPending: boolean;                   /* a loop was routed; the reviewer's own completion is inert */
  tag: LoopTag | null;
  rearmed: Partial<Record<NodeId, true>>; rearmedFirst: NodeId | null;
  captions: Partial<Record<NodeId, string>>; blurbs: Partial<Record<NodeId, string>>;
  counters: Counters; countersPass: number;
  finalStatus: string | null;
}
export interface RunEvent { type: string; metadata: Record<string, unknown> }
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Md = Record<string, any>; // the handlers read metadata keys exactly as the prototype does
export type Handler = (run: RunState, md: Md) => void;

export function emptyCounters(): Counters {
  return { subTopicsDone: null, subTopicsResearched: null, subTopicsTotal: null, toolCalls: null,
    findings: null, sources: null, verified: null, corrected: null, dropped: null,
    statements: null, refused: null, reviewSeen: false, reviewScore: null };
}
export function newRunState(passes: number | null | undefined): RunState {
  return {
    marks: {}, active: "planner", openNode: null,
    pass: 1, maxPasses: Math.max(1, Number(passes) || 1),
    loop: "off", arc: null, loopPending: false, tag: null,
    rearmed: {}, rearmedFirst: null, captions: {}, blurbs: {},
    counters: emptyCounters(), countersPass: 1, finalStatus: null,
  };
}
function nextRow(node: NodeId): NodeId | null {
  const i = AGENT_ORDER.indexOf(node);
  return i >= 0 && i < AGENT_ORDER.length - 1 ? AGENT_ORDER[i + 1] : null;
}
/* Rows fromIndex..5 go hollow and are re-armed: their next completion reads `loop`, and the first of
   them carries ↺. Publishing goes hollow too; the rows before fromIndex keep `done`. */
function rearm(run: RunState, fromIndex: number): void {
  run.rearmed = {};
  run.rearmedFirst = AGENT_ORDER[fromIndex];
  for (let i = fromIndex; i <= 5; i++) {
    delete run.marks[AGENT_ORDER[i]];
    run.rearmed[AGENT_ORDER[i]] = true;
  }
  delete run.marks.finalize_report;
}
export function plural(n: number, one: string, many: string): string { return n + " " + (n === 1 ? one : many); }

/* Keyed by event type; each handler reads only `md` (the event's metadata). */
export const EVENT_HANDLERS: Readonly<Record<string, Handler>> = {
  "graph.session.started": (run, md) => {
    if (typeof md.max_extra_passes === "number") run.maxPasses = 1 + md.max_extra_passes;
  },
  "graph.node.started": (run, md) => {
    run.openNode = md.node;
    /* the pass number is read here and from graph.extra_pass.started only */
    if (typeof md.iteration === "number") run.pass = md.iteration + 1;
  },
  "graph.node.completed": (run, md) => {
    const node: NodeId = md.node;
    if (run.openNode === node) run.openNode = null;
    if ((node as string) === "extra_pass" || (node as string) === "writer_redraft") return;          /* hops never map to a row */
    if (node === "report_reviewer" && run.loopPending) { run.loopPending = false; return; }         /* inert after a loop decision */
    run.marks[node] = run.rearmed[node] ? "loop" : "done";
    if (node === "report_reviewer") return;                                                          /* the route decision already moved the active row */
    run.active = nextRow(node);
  },
  "graph.node.skipped": (run, md) => {
    run.marks[md.node as NodeId] = "skipped";
    if (run.active === md.node) run.active = null;
  },
  "planner.planning.completed": (run, md) => {
    run.captions.planner = plural(md.sub_topic_count, "sub-topic", "sub-topics");
  },
  "researcher.sub_topic.completed": (run) => {
    run.counters.subTopicsDone = (run.counters.subTopicsDone || 0) + 1;
  },
  "researcher.tool_call": (run) => {
    /* counted only — its `iteration` is the ReAct step index, never the pass */
    run.counters.toolCalls = (run.counters.toolCalls || 0) + 1;
  },
  "researcher.research.completed": (run, md) => {
    const c = run.counters;
    c.subTopicsResearched = md.sub_topics_researched;
    c.subTopicsTotal = md.sub_topics_researched + md.sub_topics_skipped;
    c.findings = md.findings;
  },
  "source_evaluator.evaluation.completed": (run, md) => {
    run.counters.sources = md.source_count;
  },
  "evidence_verifier.verification.completed": (run, md) => {
    const c = run.counters;
    c.verified = md.verified; c.corrected = md.verified_corrected; c.dropped = md.dropped;
  },
  "report_writer.report.written": (run, md) => {
    const c = run.counters;
    c.statements = md.statements; c.refused = md.refused;
  },
  "graph.report.reviewed": (run, md) => {
    const c = run.counters;
    c.reviewSeen = true;
    c.reviewScore = typeof md.mean_score === "number" ? md.mean_score : null;
  },
  "graph.route.decided": (run, md) => {
    run.tag = null;
    run.loopPending = false;
    if (md.destination === "extra_pass") {
      rearm(run, 1); run.active = "researcher";
      run.loopPending = true; run.arc = "extra_pass"; run.loop = "flowing";
    } else if (md.destination === "redraft") {
      rearm(run, 4); run.active = "report_writer";
      run.loopPending = true; run.arc = "redraft"; run.loop = "flowing";
    } else {
      /* finalize or end: an arc lit by an earlier loop clears here */
      run.loop = "off"; run.arc = null;
      run.active = md.destination === "finalize" ? "finalize_report" : null;
    }
  },
  "graph.extra_pass.started": (run, md) => {
    const n = (md.targets || []).length;
    if (typeof md.iteration === "number") run.pass = md.iteration + 1;
    run.loop = "settled";
    run.tag = { kind: "extra_pass", label: "extra pass", text: plural(n, "required target had no verified finding", "required targets had no verified finding") };
    run.captions.researcher = plural(n, "missing target only", "missing targets only");
    run.blurbs.researcher = "Researching the " + plural(n, "target", "targets") + " still missing a verified finding.";
    /* this-pass rows reset; whole-run and current-draft rows keep their values */
    const c = run.counters;
    c.subTopicsDone = null; c.subTopicsResearched = null; c.subTopicsTotal = null; c.findings = null;
    c.verified = null; c.corrected = null; c.dropped = null;
    run.countersPass = run.pass;
  },
  "graph.report.redraft_requested": (run, md) => {
    run.loop = "settled";
    run.tag = { kind: "redraft", label: "redraft", text: "Reviewer named " + plural(md.material_defects, "material defect", "material defects") };
    /* current-draft rows and the review score reset */
    const c = run.counters;
    c.statements = null; c.refused = null; c.reviewSeen = false; c.reviewScore = null;
  },
  "graph.session.completed": (run, md) => {
    run.finalStatus = md.status;
    run.loop = "off"; run.arc = null; run.tag = null;
    run.active = null;
  },
};
export function applyEvent(run: RunState, ev: RunEvent): void {
  const h = EVENT_HANDLERS[ev.type];
  if (h) h(run, ev.metadata || {});
}
/* The marks to paint: the recorded states plus the active row, which is derived. */
export function marksFor(run: RunState, activeId: NodeId | null): Partial<Record<NodeId, PaintedMark>> {
  const m: Partial<Record<NodeId, PaintedMark>> = {};
  (Object.keys(run.marks) as NodeId[]).forEach((k) => { m[k] = run.marks[k]; });
  if (activeId && !m[activeId]) m[activeId] = "active";
  return m;
}
export interface CounterRow { key: string; label: string; scope: string; value(c: Counters): string | { muted: string } | null }
export const COUNTER_ROWS: readonly CounterRow[] = [
  { key: "subTopics", label: "sub-topics researched", scope: "this pass",
    value: (c) => { if (c.subTopicsResearched !== null) return c.subTopicsResearched + " of " + c.subTopicsTotal; return c.subTopicsDone === null ? null : c.subTopicsDone + " this pass"; } },
  { key: "toolCalls", label: "tool calls", scope: "whole run · researcher only", value: (c) => (c.toolCalls === null ? null : String(c.toolCalls)) },
  { key: "findings", label: "findings", scope: "this pass", value: (c) => (c.findings === null ? null : String(c.findings)) },
  { key: "sources", label: "sources scored", scope: "whole run", value: (c) => (c.sources === null ? null : String(c.sources)) },
  { key: "verified", label: "verified / corrected / dropped", scope: "this pass", value: (c) => (c.verified === null ? null : c.verified + " / " + c.corrected + " / " + c.dropped) },
  { key: "statements", label: "sentences / refused", scope: "current draft", value: (c) => (c.statements === null ? null : c.statements + " / " + c.refused) },
  { key: "review", label: "review score", scope: "latest review",
    value: (c) => { if (!c.reviewSeen) return null; return c.reviewScore === null ? { muted: "not scored" } : c.reviewScore.toFixed(2); } },
];

/* The API's frame carries `event_type`; the prototype's scripts carried `type`. */
export function toRunEvent(event: ResearchEvent): RunEvent { return { type: event.event_type, metadata: event.metadata }; }
export function replayRun(events: readonly ResearchEvent[], passes: number | null | undefined): RunState {
  const run = newRunState(passes);
  for (const event of events) applyEvent(run, toRunEvent(event));
  return run;
}
/* The failed stage's marks: the halting row is the open node; Publishing is skipped by the client
   rule status == "failed" (never has_report); an API-level failure has no graph.session.completed,
   so the session's own status is the fallback (index.html:3745-3746). */
export function failedMarks(run: RunState, sessionStatus: SessionStatus): Partial<Record<NodeId, PaintedMark>> {
  const marks = marksFor(run, run.openNode);
  if ((run.finalStatus || sessionStatus) === "failed") marks.finalize_report = "skipped";
  return marks;
}
```

- [ ] **Step 6: Run the tests**

```bash
cd web && npx vitest run test/run-state.test.ts 2>&1 | tail -n 6
```
Expected: `Tests  11 passed`.

- [ ] **Step 7: Commit (fixtures included)**

```bash
git add web/lib/run-state.ts web/scripts/capture-replay-events.mjs web/test/fixtures/events web/test/run-state.test.ts && git commit -m "feat(web): port the prototype's event core; prove it on two real replay captures"
```

---

### Task 12: `lib/stream.ts` and the `lib/api.ts` client (spec §4.3 `lib/stream.ts`, `lib/api.ts`; T-W3; Review Focus 1)

**Files:**
- Create: `web/lib/stream.ts`
- Modify: `web/lib/api.ts` (append the errors and functions below the types of Task 10)
- Test: `web/test/stream.test.ts`, `web/test/api.test.ts`

**Interfaces:**
- Produces (`lib/stream.ts`): `SseFrame`, `parseSse(buffer)`, `StreamCallbacks`, `StreamEnd`, `readStream(url, callbacks, signal)`, `backoffDelaysMs()`.
- Produces (`lib/api.ts`): `ApiError`, `ApiUnreachableError`, `ApiResult<T>`, `startResearch`, `getStatus`, `listSessions`, `getReport`, `getEvidence`, `reportUrl`, `evidenceMarkdownUrl`, `streamUrl`, `modeOf(response)`.

- [ ] **Step 1: Write the failing tests**

`web/test/stream.test.ts`:

```ts
// @vitest-environment node
import { createServer, type Server } from "node:http";
import { afterEach, describe, expect, it } from "vitest";
import type { ResearchEvent } from "../lib/api";
import { backoffDelaysMs, parseSse, readStream } from "../lib/stream";

const frame = (id: number, type: string) =>
  `id: ${id}\nevent: ${type}\ndata: ${JSON.stringify({ event_type: type, source: "graph", message: "m", timestamp: "2026-09-27T00:00:00+00:00", metadata: { id } })}\n\n`;

describe("parseSse", () => {
  it("splits complete frames and keeps the unterminated rest", () => {
    const text = frame(1, "graph.session.started") + "id: 2\nevent: graph.node.started\ndata: {\"a\":";
    const { frames, rest } = parseSse(text);
    expect(frames.map((f) => [f.id, f.event])).toEqual([["1", "graph.session.started"]]);
    expect(rest).toBe("id: 2\nevent: graph.node.started\ndata: {\"a\":");
  });
  it("handles CRLF, multi-line data and comment lines", () => {
    const { frames } = parseSse(": keep-alive\r\n\r\nid: 7\r\nevent: x\r\ndata: {\"a\":\r\ndata: 1}\r\n\r\n");
    expect(frames).toEqual([{ id: "7", event: "x", data: "{\"a\":\n1}" }]);
  });
  it("yields nothing for a comment-only block", () => {
    expect(parseSse(": ping\n\n").frames).toEqual([]);
  });
});

describe("backoffDelaysMs", () => {
  it("doubles from one second and settles at thirty", () => {
    const gen = backoffDelaysMs();
    expect(Array.from({ length: 7 }, () => gen.next().value)).toEqual([1000, 2000, 4000, 8000, 16000, 30000, 30000]);
  });
});

describe("readStream", () => {
  let server: Server | null = null;
  afterEach(() => new Promise<void>((r) => (server ? server.close(() => r()) : r())));
  const listen = (handler: Parameters<typeof createServer>[1]) =>
    new Promise<string>((resolve) => { server = createServer(handler).listen(0, "127.0.0.1", () => resolve(`http://127.0.0.1:${(server!.address() as { port: number }).port}`)); });

  it("delivers frames as they arrive and resolves ended on a clean close", async () => {
    const origin = await listen((_req, res) => {
      res.writeHead(200, { "content-type": "text/event-stream", "x-deep-research-mode": "replay" });
      res.write(frame(1, "graph.session.started"));
      setTimeout(() => { res.write(frame(2, "graph.node.started")); res.end(); }, 100);
    });
    const seen: [string, number, number][] = [];
    let mode: string | null = "unset";
    const t0 = performance.now();
    const end = await readStream(`${origin}/stream`, {
      onOpen: (m) => { mode = m; },
      onEvent: (e: ResearchEvent, id) => seen.push([e.event_type, id, performance.now() - t0]),
    }, new AbortController().signal);
    expect(end).toEqual({ kind: "ended" });
    expect(mode).toBe("replay");
    expect(seen.map(([type, id]) => [type, id])).toEqual([["graph.session.started", 1], ["graph.node.started", 2]]);
    expect(seen[0][2]).toBeLessThan(90); // the first frame did not wait for the second
  });
  it("reports a non-2xx as failed with its status", async () => {
    const origin = await listen((_req, res) => { res.writeHead(404, { "content-type": "application/json" }); res.end("{}"); });
    const end = await readStream(`${origin}/stream`, { onOpen: () => {}, onEvent: () => {} }, new AbortController().signal);
    expect(end.kind).toBe("failed");
    expect(end.kind === "failed" && end.status).toBe(404);
  });
  it("reports a refused connection as failed with no status", async () => {
    const end = await readStream("http://127.0.0.1:59999/stream", { onOpen: () => {}, onEvent: () => {} }, new AbortController().signal);
    expect(end.kind).toBe("failed");
    expect(end.kind === "failed" && end.status).toBeNull();
  });
});
```

`web/test/api.test.ts`:

```ts
// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, ApiUnreachableError, evidenceMarkdownUrl, getStatus, reportUrl, startResearch, streamUrl } from "../lib/api";

const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });
const request = { query: "q", max_iterations: 1, output_format: "markdown" as const, config_overrides: {} };

afterEach(() => vi.unstubAllGlobals());

describe("the client", () => {
  it("posts once and captures the mode header", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json(202, { session_id: "abc", query: "q", status: "running" }, { "x-deep-research-mode": "replay" }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await startResearch(request);
    expect(result.mode).toBe("replay");
    expect(result.data.session_id).toBe("abc");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/research");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
  });
  it("maps the proxy's 502 to ApiUnreachableError and never retries the POST", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json(502, { error: { code: "api_unreachable", message: "Research service not reachable.", reason: null, issues: [], target: "http://127.0.0.1:59999" } }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(startResearch(request)).rejects.toBeInstanceOf(ApiUnreachableError);
    await expect(startResearch(request)).rejects.toMatchObject({ target: "http://127.0.0.1:59999" });
    expect(fetchMock).toHaveBeenCalledTimes(2); // one call per startResearch, none of its own
  });
  it("maps a 422 to ApiError with the issues", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json(422, { error: { code: "validation_error", message: "Request validation failed.", reason: null, issues: [{ location: "body.query", type: "string_too_short" }] } })));
    const error = await startResearch(request).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(422);
    expect(error.body.issues).toEqual([{ location: "body.query", type: "string_too_short" }]);
  });
  it("maps a 404 status read to ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json(404, { error: { code: "session_not_found", message: "Research session not found.", reason: null, issues: [] } })));
    const error = await getStatus("nope").catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.body.code).toBe("session_not_found");
  });
  it("builds same-origin URLs", () => {
    expect(reportUrl("abc")).toBe("/api/research/abc/report");
    expect(evidenceMarkdownUrl("abc")).toBe("/api/research/abc/evidence?format=markdown");
    expect(streamUrl("abc")).toBe("/api/research/abc/stream");
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd web && npx vitest run test/stream.test.ts test/api.test.ts 2>&1 | tail -n 8
```
Expected: both files fail to import (`Failed to load ../lib/stream`; `does not provide an export named 'ApiError'`).

- [ ] **Step 3: Write the reader**

`web/lib/stream.ts`:

```ts
// The SSE reader: fetch + ReadableStream, not EventSource (spec §4.3 — EventSource reconnects on
// its own schedule after every close, including a finished session's clean close, and hides the
// response headers the mode chip needs).
import type { ApiMode, ResearchEvent } from "./api";

export interface SseFrame { id: string | null; event: string | null; data: string }

/* Split complete frames (terminated by a blank line, LF or CRLF) off the front of `buffer`;
   return the unterminated remainder. Comment lines (":…") and data-less blocks are dropped. */
export function parseSse(buffer: string): { frames: SseFrame[]; rest: string } {
  const frames: SseFrame[] = [];
  let rest = buffer;
  for (;;) {
    const m = /\r?\n\r?\n/.exec(rest);
    if (!m) break;
    const raw = rest.slice(0, m.index);
    rest = rest.slice(m.index + m[0].length);
    const frame: SseFrame = { id: null, event: null, data: "" };
    const data: string[] = [];
    for (const line of raw.split(/\r?\n/)) {
      if (!line || line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon < 0 ? line : line.slice(0, colon);
      let value = colon < 0 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "id") frame.id = value;
      else if (field === "event") frame.event = value;
      else if (field === "data") data.push(value);
    }
    if (data.length) { frame.data = data.join("\n"); frames.push(frame); }
  }
  return { frames, rest };
}

export interface StreamCallbacks {
  onOpen(mode: ApiMode | null): void;
  onEvent(event: ResearchEvent, id: number): void;
}
export type StreamEnd = { kind: "ended" } | { kind: "failed"; status: number | null; error: unknown };

/* One connection. Resolves `ended` when the server closes the body (a finished session), `failed`
   on a non-2xx, a network error or an abort. The page owns reconnecting (the backoff ladder). */
export async function readStream(url: string, callbacks: StreamCallbacks, signal: AbortSignal): Promise<StreamEnd> {
  let response: Response;
  try {
    response = await fetch(url, { headers: { accept: "text/event-stream" }, cache: "no-store", signal });
  } catch (error) {
    return { kind: "failed", status: null, error };
  }
  if (!response.ok || !response.body) return { kind: "failed", status: response.status, error: null };
  const mode = response.headers.get("x-deep-research-mode");
  callbacks.onOpen(mode === "live" || mode === "replay" ? mode : null);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parsed = parseSse(buffer);
      buffer = parsed.rest;
      for (const frame of parsed.frames) callbacks.onEvent(JSON.parse(frame.data) as ResearchEvent, Number(frame.id));
    }
  } catch (error) {
    return { kind: "failed", status: response.status, error };
  }
  return { kind: "ended" };
}

/* 1, 2, 4, 8, 16 s, then 30 s forever (spec §4.4). */
export function* backoffDelaysMs(): Generator<number> {
  for (const d of [1000, 2000, 4000, 8000, 16000]) yield d;
  for (;;) yield 30000;
}
```

- [ ] **Step 4: Append the client to `lib/api.ts`**

Append below the types in `web/lib/api.ts`:

```ts
// ── the client ─────────────────────────────────────────────────────────────
// Every call goes to the app's own origin (/api/*, the proxy of app/api/[...path]/route.ts),
// never caches, and maps any non-2xx to ApiError — or ApiUnreachableError for the proxy's
// 502 api_unreachable. Nothing here retries; a POST is exactly one fetch.

export class ApiError extends Error {
  constructor(readonly status: number, readonly body: ApiErrorBody) { super(body.message); this.name = "ApiError"; }
}
export class ApiUnreachableError extends Error {
  constructor(readonly target: string) { super(`Research service not reachable at ${target}`); this.name = "ApiUnreachableError"; }
}
export interface ApiResult<T> { data: T; mode: ApiMode | null }

export function modeOf(response: Response): ApiMode | null {
  const m = response.headers.get("x-deep-research-mode");
  return m === "live" || m === "replay" ? m : null;
}
async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(path, { ...init, cache: "no-store" });
  if (response.ok) return response;
  let body: ApiErrorBody | null = null;
  try { body = ((await response.json()) as { error?: ApiErrorBody }).error ?? null; } catch { body = null; }
  if (body?.code === "api_unreachable") throw new ApiUnreachableError(body.target ?? "");
  throw new ApiError(response.status, body ?? { code: `http_${response.status}`, message: response.statusText || "Request failed.", reason: null, issues: [] });
}
const id = (sessionId: string) => encodeURIComponent(sessionId);
export const reportUrl = (sessionId: string) => `/api/research/${id(sessionId)}/report`;
export const evidenceMarkdownUrl = (sessionId: string) => `/api/research/${id(sessionId)}/evidence?format=markdown`;
export const streamUrl = (sessionId: string) => `/api/research/${id(sessionId)}/stream`;

export async function startResearch(body: ResearchRequest): Promise<ApiResult<ResearchSessionResponse>> {
  const r = await request("/api/research", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  return { data: (await r.json()) as ResearchSessionResponse, mode: modeOf(r) };
}
export async function getStatus(sessionId: string): Promise<ApiResult<ResearchSessionResponse>> {
  const r = await request(`/api/research/${id(sessionId)}/status`);
  return { data: (await r.json()) as ResearchSessionResponse, mode: modeOf(r) };
}
export async function listSessions(limit = 50): Promise<ApiResult<SessionListResponse>> {
  const r = await request(`/api/research?limit=${limit}`);
  return { data: (await r.json()) as SessionListResponse, mode: modeOf(r) };
}
export async function getReport(sessionId: string): Promise<ApiResult<string>> {
  const r = await request(reportUrl(sessionId));
  return { data: await r.text(), mode: modeOf(r) };
}
export async function getEvidence(sessionId: string): Promise<ApiResult<EvidenceResponse>> {
  const r = await request(`/api/research/${id(sessionId)}/evidence`);
  return { data: (await r.json()) as EvidenceResponse, mode: modeOf(r) };
}
```

- [ ] **Step 5: Run the tests**

```bash
cd web && npx vitest run test/stream.test.ts test/api.test.ts 2>&1 | tail -n 6
```
Expected: `Tests  12 passed`.

- [ ] **Step 6: Commit**

```bash
git add web/lib/stream.ts web/lib/api.ts web/test/stream.test.ts web/test/api.test.ts && git commit -m "feat(web): SSE reader and typed API client"
```

---

### Task 13: The streaming proxy `app/api/[...path]/route.ts` (spec §4.1 proxy contract; T-W4; AC13)

**Files:**
- Create: `web/app/api/[...path]/route.ts`
- Test: `web/test/proxy.test.ts`

**Interfaces:**
- Consumes: `NextRequest` (`next/server`); `DEEP_RESEARCH_API_URL`.
- Produces: `GET(request, ctx)`, `POST(request, ctx)` with `ctx.params: Promise<{ path: string[] }>`; `export const dynamic = "force-dynamic"`, `export const runtime = "nodejs"`.

- [ ] **Step 1: Write the failing test**

`web/test/proxy.test.ts`:

```ts
// @vitest-environment node
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { NextRequest } from "next/server";
import { afterEach, describe, expect, it } from "vitest";
import { GET, POST } from "../app/api/[...path]/route";

const ctx = (...path: string[]) => ({ params: Promise.resolve({ path }) });
let server: Server | null = null;
afterEach(async () => { if (server) await new Promise<void>((r) => server!.close(() => r())); server = null; });
function upstream(handler: (req: IncomingMessage, res: ServerResponse, body: string) => void): Promise<string> {
  return new Promise((resolve) => {
    server = createServer((req, res) => { let body = ""; req.on("data", (c) => (body += c)); req.on("end", () => handler(req, res, body)); })
      .listen(0, "127.0.0.1", () => resolve(`http://127.0.0.1:${(server!.address() as { port: number }).port}`));
  });
}

describe("the proxy", () => {
  it("streams an SSE body frame by frame without buffering", async () => {
    let secondWritten = false;
    process.env.DEEP_RESEARCH_API_URL = await upstream((_req, res) => {
      res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache", "x-accel-buffering": "no", "x-deep-research-mode": "replay", "server": "uvicorn", "date": "x" });
      res.write("id: 1\nevent: a\ndata: {}\n\n");
      setTimeout(() => { secondWritten = true; res.write("id: 2\nevent: b\ndata: {}\n\n"); res.end(); }, 300);
    });
    const response = await GET(new NextRequest("http://localhost:3000/api/research/s1/stream"), ctx("research", "s1", "stream"));
    expect(response.status).toBe(200);
    expect([...response.headers.keys()].sort()).toEqual(["cache-control", "content-type", "x-accel-buffering", "x-deep-research-mode"]);
    const reader = response.body!.getReader();
    const first = new TextDecoder().decode((await reader.read()).value);
    expect(first).toContain("event: a");
    expect(secondWritten).toBe(false); // the first frame reached us before the upstream wrote the second
    let rest = "";
    for (;;) { const { value, done } = await reader.read(); if (done) break; rest += new TextDecoder().decode(value); }
    expect(rest).toContain("event: b");
  });
  it("forwards a POST body, the query string and x-replay-case; passes the status through", async () => {
    const seen: { method?: string; url?: string; headers?: IncomingMessage["headers"]; body?: string } = {};
    process.env.DEEP_RESEARCH_API_URL = await upstream((req, res, body) => {
      Object.assign(seen, { method: req.method, url: req.url, headers: req.headers, body });
      res.writeHead(202, { "content-type": "application/json", "x-deep-research-mode": "live" });
      res.end(JSON.stringify({ session_id: "abc" }));
    });
    const request = new NextRequest("http://localhost:3000/api/research?limit=5", {
      method: "POST", body: JSON.stringify({ query: "q" }),
      headers: { "content-type": "application/json", "x-replay-case": "review-unavailable", cookie: "a=b", accept: "application/json" },
    });
    const response = await POST(request, ctx("research"));
    expect(response.status).toBe(202);
    expect(await response.json()).toEqual({ session_id: "abc" });
    expect(seen.method).toBe("POST");
    expect(seen.url).toBe("/research?limit=5");
    expect(seen.body).toBe(JSON.stringify({ query: "q" }));
    expect(seen.headers!["x-replay-case"]).toBe("review-unavailable");
    expect(seen.headers!["accept"]).toBe("application/json");
    expect(seen.headers!["cookie"]).toBeUndefined();
  });
  it("answers 502 api_unreachable with the target when the connection is refused", async () => {
    process.env.DEEP_RESEARCH_API_URL = "http://127.0.0.1:59999";
    const response = await GET(new NextRequest("http://localhost:3000/api/research"), ctx("research"));
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ error: { code: "api_unreachable", message: "Research service not reachable.", reason: null, issues: [], target: "http://127.0.0.1:59999" } });
  });
  it("forwards its abort signal: aborting the handler's request closes the upstream socket", async () => {
    let closed = false;
    process.env.DEEP_RESEARCH_API_URL = await upstream((req, res) => {
      res.writeHead(200, { "content-type": "text/event-stream" });
      res.write("id: 1\nevent: a\ndata: {}\n\n");
      req.on("close", () => { closed = true; });
    });
    const controller = new AbortController();
    const response = await GET(new NextRequest("http://localhost:3000/api/research/s1/stream", { signal: controller.signal }), ctx("research", "s1", "stream"));
    await response.body!.getReader().read();
    controller.abort();
    await new Promise((r) => setTimeout(r, 200));
    expect(closed).toBe(true);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd web && npx vitest run test/proxy.test.ts 2>&1 | tail -n 6
```
Expected: the file fails to import (`Failed to load ../app/api/[...path]/route`).

- [ ] **Step 3: Write the handler**

`web/app/api/[...path]/route.ts`:

```ts
// The same-origin proxy: /api/<path> → ${DEEP_RESEARCH_API_URL}/<path>, request and response
// passed through as streams so an SSE body reaches the browser frame by frame (spec §4.1).
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const REQUEST_HEADERS = ["accept", "content-type", "x-replay-case"] as const;
const RESPONSE_HEADERS = ["content-type", "cache-control", "x-accel-buffering", "x-deep-research-mode"] as const;
type Ctx = { params: Promise<{ path: string[] }> };

const apiOrigin = () => process.env.DEEP_RESEARCH_API_URL ?? "http://127.0.0.1:8000";

async function proxy(request: NextRequest, ctx: Ctx, method: "GET" | "POST"): Promise<Response> {
  const { path } = await ctx.params;
  const target = `${apiOrigin()}/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  const headers = new Headers();
  for (const name of REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method,
      headers,
      body: method === "POST" ? await request.text() : undefined,
      cache: "no-store",
      signal: request.signal,
    });
  } catch {
    return Response.json(
      { error: { code: "api_unreachable", message: "Research service not reachable.", reason: null, issues: [], target: apiOrigin() } },
      { status: 502 },
    );
  }
  const out = new Headers();
  for (const name of RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export function GET(request: NextRequest, ctx: Ctx): Promise<Response> { return proxy(request, ctx, "GET"); }
export function POST(request: NextRequest, ctx: Ctx): Promise<Response> { return proxy(request, ctx, "POST"); }
```

- [ ] **Step 4: Run the test and the build**

```bash
cd web && npx vitest run test/proxy.test.ts 2>&1 | tail -n 6 && npm run -s typecheck && npm run -s build 2>&1 | tail -n 8
```
Expected: `Tests  4 passed`; the build's route table lists `/api/[...path]` as dynamic (`ƒ`).

- [ ] **Step 5: Commit**

```bash
git add "web/app/api/[...path]/route.ts" web/test/proxy.test.ts && git commit -m "feat(web): streaming same-origin proxy to the API"
```

---

### Task 14: The shell and the composer — Sidebar, Topbar, chips, settings popover; checkpoint C1 (spec §4.3 Components, §4.4; T-W5 part 1; AC17 half)

**Files:**
- Create: `web/lib/session-store.ts`, `web/components/ConsoleProvider.tsx`, `web/components/AppShell.tsx`, `web/components/Sidebar.tsx`, `web/components/Topbar.tsx`, `web/components/StatusChip.tsx`, `web/components/ModeChip.tsx`, `web/components/ServiceBanner.tsx`, `web/components/Composer.tsx`, `web/components/SettingsPopover.tsx`, `web/scripts/capture-stage.mjs`, `web/scripts/launch.mjs`
- Modify: `web/app/layout.tsx`, `web/app/page.tsx`
- Test: `web/test/components/status-chip.test.tsx`, `web/test/components/composer.test.tsx`, `web/test/components/sidebar.test.tsx`

**Interfaces:**
- Consumes: `lib/api.ts` (Task 12), `lib/format.ts` (Task 10).
- Produces: `useConsole()` → `{ mode, noteMode(mode), chip, setChip(view), sessions, refreshSessions(), sidebar, setSidebar(mode), unreachable, noteUnreachable(target, retry), clearUnreachable() }`; `recordSubmission(sessionId, settings)`, `readSubmission(sessionId)`, `submittedBeatRemaining(sessionId, now?)`, `SUBMITTED_BEAT_MS = 2200`, `SubmittedSettings { model, thinking, extraPasses, outputDir }`; `buildRequest(question, settings)`, `STARTERS`, `DEFAULT_SETTINGS`; `groupByDay(sessions, now?)`; `<StatusChip view />`, `<ModeChip />`, `<ServiceBanner target onRetry />`; `node scripts/capture-stage.mjs <checkpoint> <name> <url> [--phone] [--view evidence]`; `node scripts/launch.mjs start <name> <port> <ready-path> "<command>"` / `stop <name> <port>`.

- [ ] **Step 1: Write the failing tests**

`web/test/components/status-chip.test.tsx`:

```tsx
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusChip } from "../../components/StatusChip";
import type { SessionView } from "../../lib/format";

const view = (over: Partial<SessionView>): SessionView => ({ status: "running", iteration: 0, passes: 2, review: null, coverage: null, ...over });
const text = (el: HTMLElement) => el.textContent!.replace(/\s+/g, " ").trim();

describe("StatusChip", () => {
  it("reads label · note with the dot class per status", () => {
    const { container, rerender } = render(<StatusChip view={view({})} />);
    expect(text(container)).toBe("Running · pass 1 of 2");
    expect(container.querySelector(".dot")!.className).toContain("dot-live");
    rerender(<StatusChip view={view({ status: "completed", iteration: 1, review: { status: "scored", score: 0.86 }, coverage: { required_targets: 4, answered_targets: 3, missing_required_target_ids: [], not_found_target_ids: ["t"] } })} />);
    expect(text(container)).toBe("Completed · review accepted · 0.86 · 1 target not found");
    rerender(<StatusChip view={view({ status: "incomplete", review: { status: "provider_failed", score: null } })} />);
    expect(text(container)).toBe("Partially completed · review unavailable");
    expect(container.querySelector(".dot")!.className).toContain("dot-warn");
    rerender(<StatusChip view={view({ status: "failed" })} />);
    expect(text(container)).toBe("Failed · halted");
  });
});
```

`web/test/components/composer.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Composer, buildRequest, DEFAULT_SETTINGS } from "../../components/Composer";
import { ConsoleProvider } from "../../components/ConsoleProvider";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }), useParams: () => ({}) }));
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
afterEach(() => vi.unstubAllGlobals());

describe("Composer", () => {
  it("builds the request the design specifies", () => {
    expect(buildRequest("  q  ", DEFAULT_SETTINGS)).toEqual({
      query: "q", max_iterations: 1, output_format: "markdown",
      config_overrides: { llm: { model: "deepseek-flash", thinking_mode: "enabled" }, output: { directory: "output/" } },
    });
    expect(buildRequest("q", { ...DEFAULT_SETTINGS, extraPasses: 0, outputDir: "" }).max_iterations).toBe(0);
    expect(buildRequest("q", { ...DEFAULT_SETTINGS, outputDir: "" }).config_overrides).toEqual({ llm: { model: "deepseek-flash", thinking_mode: "enabled" } });
  });
  it("shows the 422 issues under the box, keeps the question and posts exactly once", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return json(422, { error: { code: "validation_error", message: "Request validation failed.", reason: null, issues: [{ location: "body.config_overrides.llm.model", type: "value_error" }] } });
      return json(200, { sessions: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ConsoleProvider><Composer /></ConsoleProvider>);
    const box = screen.getByLabelText("Research question") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "How mature is quantum error correction?" } });
    fireEvent.submit(box.closest("form")!);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("The service rejected the request: body.config_overrides.llm.model (value_error)"));
    expect(box.value).toBe("How mature is quantum error correction?");
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    expect(push).not.toHaveBeenCalled();
  });
  it("shows the enumerated configuration reason on a 500", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_i: RequestInfo | URL, init?: RequestInit) =>
      init?.method === "POST" ? json(500, { error: { code: "configuration_error", message: "Research service configuration is unavailable.", reason: "missing_secrets", issues: [] } }) : json(200, { sessions: [] })));
    render(<ConsoleProvider><Composer /></ConsoleProvider>);
    fireEvent.change(screen.getByLabelText("Research question"), { target: { value: "q" } });
    fireEvent.submit(screen.getByLabelText("Research question").closest("form")!);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("Service configuration error · missing_secrets"));
  });
});
```

`web/test/components/sidebar.test.tsx`:

```tsx
import { describe, expect, it } from "vitest";
import { groupByDay } from "../../components/Sidebar";
import type { ResearchSessionResponse } from "../../lib/api";

const session = (id: string, started_at: string): ResearchSessionResponse => ({
  session_id: id, query: `q ${id}`, status: "completed", current_agent: null, iteration: 0, started_at, finished_at: null,
  report_path: null, trace_url: null, errors: [], evidence_path: null, quality_path: null, quality_contract_version: null,
  semantic_review_status: null, semantic_review_score: null, duration_seconds: null, coverage: null, evidence_counts: null,
});

describe("groupByDay", () => {
  it("groups Today, Yesterday, Earlier by the local date of started_at, keeping order", () => {
    const now = new Date(2026, 8, 27, 15, 0, 0);
    const groups = groupByDay([session("a", new Date(2026, 8, 27, 9).toISOString()), session("b", new Date(2026, 8, 26, 23).toISOString()), session("c", new Date(2026, 8, 20).toISOString())], now);
    expect(groups.map(([g, items]) => [g, items.map((s) => s.session_id)])).toEqual([["Today", ["a"]], ["Yesterday", ["b"]], ["Earlier", ["c"]]]);
    expect(groupByDay([], now)).toEqual([]);
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd web && npx vitest run test/components 2>&1 | tail -n 8
```
Expected: all three files fail to import their components.

- [ ] **Step 3: The submission store**

`web/lib/session-store.ts`:

```ts
// What only the submitting tab knows: that a session was just submitted (the Submitted beat) and
// the settings it was submitted with (the strip). Memory first, sessionStorage under the
// prototype's own prefix so a reload in the same tab keeps them.
export interface SubmittedSettings { model: string; thinking: "enabled" | "disabled"; extraPasses: number; outputDir: string }
export interface Submission { submittedAt: number; settings: SubmittedSettings }
export const SUBMITTED_BEAT_MS = 2200;

const PREFIX = "dr.console.submission.";
const memory = new Map<string, Submission>();
const storage = () => (typeof window === "undefined" ? null : window.sessionStorage);

export function recordSubmission(sessionId: string, settings: SubmittedSettings): Submission {
  const submission = { submittedAt: Date.now(), settings };
  memory.set(sessionId, submission);
  storage()?.setItem(PREFIX + sessionId, JSON.stringify(submission));
  return submission;
}
export function readSubmission(sessionId: string): Submission | null {
  const held = memory.get(sessionId);
  if (held) return held;
  const raw = storage()?.getItem(PREFIX + sessionId);
  if (!raw) return null;
  try { const parsed = JSON.parse(raw) as Submission; memory.set(sessionId, parsed); return parsed; } catch { return null; }
}
/* Milliseconds of the Submitted beat still to show for this session in this tab; 0 when none. */
export function submittedBeatRemaining(sessionId: string, now = Date.now()): number {
  const submission = readSubmission(sessionId);
  return submission ? Math.max(0, SUBMITTED_BEAT_MS - (now - submission.submittedAt)) : 0;
}
```

- [ ] **Step 4: The console context, the shell, the sidebar, the topbar, the chips, the banner**

`web/components/ConsoleProvider.tsx`:

```tsx
"use client";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiUnreachableError, listSessions, type ApiMode, type ResearchSessionResponse } from "@/lib/api";
import type { SessionView } from "@/lib/format";

type SidebarMode = "expanded" | "collapsed";
interface Unreachable { target: string; retry: () => void }
export interface ConsoleState {
  mode: ApiMode | null; noteMode(mode: ApiMode | null): void;
  chip: SessionView | null; setChip(view: SessionView | null): void;
  sessions: ResearchSessionResponse[]; refreshSessions(): Promise<void>;
  sidebar: SidebarMode; setSidebar(mode: SidebarMode): void;
  unreachable: Unreachable | null; noteUnreachable(target: string, retry: () => void): void; clearUnreachable(): void;
}
const ConsoleContext = createContext<ConsoleState | null>(null);
export function useConsole(): ConsoleState {
  const value = useContext(ConsoleContext);
  if (!value) throw new Error("useConsole() outside <ConsoleProvider>");
  return value;
}
export function ConsoleProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ApiMode | null>(null);
  const [chip, setChip] = useState<SessionView | null>(null);
  const [sessions, setSessions] = useState<ResearchSessionResponse[]>([]);
  const [sidebar, setSidebarState] = useState<SidebarMode>("expanded");
  const [unreachable, setUnreachable] = useState<Unreachable | null>(null);
  const noteMode = useCallback((m: ApiMode | null) => { if (m) setMode(m); }, []);
  const noteUnreachable = useCallback((target: string, retry: () => void) => setUnreachable({ target, retry }), []);
  const clearUnreachable = useCallback(() => setUnreachable(null), []);
  const refreshSessions = useCallback(async () => {
    try {
      const result = await listSessions(50);
      setSessions(result.data.sessions);
      noteMode(result.mode);
      setUnreachable(null);
    } catch (error) {
      if (error instanceof ApiUnreachableError) setUnreachable({ target: error.target, retry: () => void refreshSessions() });
    }
  }, [noteMode]);
  useEffect(() => { void refreshSessions(); }, [refreshSessions]);
  const anyRunning = sessions.some((s) => s.status === "running");
  useEffect(() => {
    if (!anyRunning) return;
    const timer = setInterval(() => void refreshSessions(), 5000);
    return () => clearInterval(timer);
  }, [anyRunning, refreshSessions]);
  useEffect(() => {
    // On the drawer breakpoint (<= 1080 px) "expanded" means "open", so a phone starts closed (index.html:1891-1899).
    if (window.matchMedia("(max-width:1080px)").matches) { setSidebarState("collapsed"); return; }
    const saved = window.localStorage.getItem("dr.console.sidebar");
    if (saved === "collapsed" || saved === "expanded") setSidebarState(saved);
  }, []);
  const setSidebar = useCallback((m: SidebarMode) => { setSidebarState(m); window.localStorage.setItem("dr.console.sidebar", m); }, []);
  const value = useMemo<ConsoleState>(
    () => ({ mode, noteMode, chip, setChip, sessions, refreshSessions, sidebar, setSidebar, unreachable, noteUnreachable, clearUnreachable }),
    [mode, noteMode, chip, sessions, refreshSessions, sidebar, setSidebar, unreachable, noteUnreachable, clearUnreachable],
  );
  return <ConsoleContext.Provider value={value}>{children}</ConsoleContext.Provider>;
}
```

`web/components/AppShell.tsx` (the `.app` grid of `index.html:1141-1186`):

```tsx
"use client";
import { useEffect, type ReactNode } from "react";
import { useConsole } from "./ConsoleProvider";
import { ServiceBanner } from "./ServiceBanner";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";

export function AppShell({ children }: { children: ReactNode }) {
  const { sidebar, setSidebar, unreachable } = useConsole();
  useEffect(() => {
    const drawer = window.matchMedia("(max-width:1080px)").matches;
    document.body.classList.toggle("is-locked", drawer && sidebar === "expanded");
  }, [sidebar]);
  return (
    <div className="app" id="app" data-sidebar={sidebar}>
      <Sidebar />
      <button className="scrim" id="scrim" type="button" tabIndex={-1} aria-hidden="true" onClick={() => setSidebar("collapsed")}>
        <span className="sr">Close session history</span>
      </button>
      <div className="main">
        <Topbar />
        <div className="viewport" id="viewport">
          {unreachable ? <ServiceBanner target={unreachable.target} onRetry={unreachable.retry} /> : null}
          {children}
        </div>
      </div>
    </div>
  );
}
```

`web/components/Sidebar.tsx` (`index.html:1145-1168`, `renderList` `:1855-1889`):

```tsx
"use client";
import { Fragment } from "react";
import { useParams, useRouter } from "next/navigation";
import type { ResearchSessionResponse } from "@/lib/api";
import { useConsole } from "./ConsoleProvider";

/* Today · Yesterday · Earlier by the local date of started_at; the API's order (newest first) is kept. */
export function groupByDay(sessions: ResearchSessionResponse[], now = new Date()): [string, ResearchSessionResponse[]][] {
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const today = day(now);
  const yesterday = today - 86_400_000;
  const groups = new Map<string, ResearchSessionResponse[]>();
  for (const s of sessions) {
    const started = day(new Date(s.started_at));
    const label = started >= today ? "Today" : started >= yesterday ? "Yesterday" : "Earlier";
    groups.set(label, [...(groups.get(label) ?? []), s]);
  }
  return ["Today", "Yesterday", "Earlier"].filter((g) => groups.has(g)).map((g) => [g, groups.get(g)!]);
}

export function Sidebar() {
  const { sessions } = useConsole();
  const router = useRouter();
  const params = useParams();
  const active = typeof params?.id === "string" ? params.id : null;
  return (
    <aside className="sidebar" id="sidebar" aria-label="Session history">
      <div className="sb-head">
        <span className="mark" aria-hidden="true"></span>
        <span className="sb-wordmark">Deep Research</span>
      </div>
      <button className="btn btn-ghost sb-new" id="newResearch" type="button" onClick={() => router.push("/")}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="square" aria-hidden="true" style={{ width: 14, height: 14, flex: "none" }}><path d="M12 5v14M5 12h14" /></svg>
        <span>New Research</span>
      </button>
      <div className="sb-label"><span>Sessions</span><span className="mono" id="sbCount">{sessions.length}</span></div>
      <ul className="sb-list" id="sessionList">
        {groupByDay(sessions).map(([group, items]) => (
          <Fragment key={group}>
            <li className="sb-group">{group}</li>
            {items.map((s) => {
              const running = s.status === "running";
              /* A running session is the only one that carries a mark: no chips, counts or durations here (index.html:1871-1876). */
              return (
                <li key={s.session_id}>
                  <button type="button" className="sb-item" data-session={s.session_id} data-run={running ? "1" : "0"} title={s.session_id}
                    aria-current={active === s.session_id ? "true" : "false"} aria-label={running ? `${s.query} — running` : undefined}
                    onClick={() => router.push(`/research/${s.session_id}`)}>
                    <span className="q">{s.query}</span><span className="sb-live" aria-hidden="true"></span>
                  </button>
                </li>
              );
            })}
          </Fragment>
        ))}
      </ul>
      <div className="sb-foot">
        <p>Sessions are held in the service process's memory; this list empties when the service restarts.</p>
      </div>
    </aside>
  );
}
```

`web/components/StatusChip.tsx`, `ModeChip.tsx`, `Topbar.tsx`, `ServiceBanner.tsx`:

```tsx
// StatusChip.tsx — chipHTML (index.html:1759-1764): one chip, in the topbar, no large variant.
"use client";
import { STATUS, statusNote, type SessionView } from "@/lib/format";
export function StatusChip({ view }: { view: SessionView }) {
  const m = STATUS[view.status] ?? STATUS.running;
  return (
    <span className="chip"><span className={`dot ${m.dot}`} aria-hidden="true"></span>{m.label} <span className="kind">· {statusNote(view)}</span></span>
  );
}
```

```tsx
// ModeChip.tsx — muted "replay mode" when the API answers X-Deep-Research-Mode: replay (spec §4.3).
"use client";
export function ModeChip() { return <span className="avail-mono" id="modeChip">replay mode</span>; }
```

```tsx
// Topbar.tsx — index.html:1175-1185 + renderTopbar (:2119-2130): the chip is the only status copy.
"use client";
import { useConsole } from "./ConsoleProvider";
import { ModeChip } from "./ModeChip";
import { StatusChip } from "./StatusChip";
export function Topbar() {
  const { sidebar, setSidebar, chip, mode } = useConsole();
  const open = sidebar === "expanded";
  return (
    <header className="topbar">
      <div className="topbar-in">
        <button className="icon-btn" id="sidebarToggle" type="button" aria-controls="sidebar" aria-expanded={open} onClick={() => setSidebar(open ? "collapsed" : "expanded")}>
          <span className="sr">Toggle session history</span>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="square" aria-hidden="true" style={{ width: 17, height: 17 }}><path d="M4 6h16M4 12h16M4 18h16" /></svg>
        </button>
        <div className="topbar-right" id="topbarStatus">
          {chip ? <StatusChip view={chip} /> : null}
          {mode === "replay" ? <ModeChip /> : null}
        </div>
      </div>
    </header>
  );
}
```

```tsx
// ServiceBanner.tsx — S4 "API unreachable": the sentence (I) and a Retry; nothing is disabled.
"use client";
export function ServiceBanner({ target, onRetry }: { target: string; onRetry: () => void }) {
  return (
    <div className="note bad" role="alert" data-od-id="service-banner" style={{ marginBottom: "var(--space-5)" }}>
      <div className="note-head"><span className="mk">service</span><span>Research service not reachable at {target}</span></div>
      <p className="sm">The console keeps working; the request is repeated when you retry.</p>
      <button className="btn btn-ghost btn-sm" type="button" onClick={onRetry}>Retry</button>
    </div>
  );
}
```

- [ ] **Step 5: The composer and its popover**

`web/components/SettingsPopover.tsx` (`index.html:1208-1257`; placement rules `placePop` `:2150-2189`):

```tsx
"use client";
import { useLayoutEffect, useRef, type RefObject } from "react";
import type { SubmittedSettings } from "@/lib/session-store";

export const MODELS = ["deepseek-flash", "deepseek-v4-flash", "deepseek-v4-pro"] as const; // product data (the design's three buttons)
export const EXTRA_MIN = 0, EXTRA_MAX = 2;
export const shortModel = (m: string) => m.replace(/^deepseek-/, "");
export const effortLine = (thinking: SubmittedSettings["thinking"]) =>
  thinking === "enabled" ? "effort per agent: planner max · reviewer max · others high" : "effort: not sent (thinking disabled)";

interface Props { open: boolean; settings: SubmittedSettings; onChange(next: SubmittedSettings): void; onClose(): void; anchor: RefObject<HTMLElement | null> }

export function SettingsPopover({ open, settings, onChange, onClose, anchor }: Props) {
  const pop = useRef<HTMLDivElement>(null);
  /* Where the panel can actually be read: above the bar when it fits, else below (index.html:2150-2189). */
  useLayoutEffect(() => {
    const el = pop.current;
    if (!open || !el) return;
    const place = () => {
      const bar = anchor.current?.closest(".composer-bar") ?? anchor.current;
      const r = bar?.getBoundingClientRect();
      const vh = window.innerHeight, vw = window.innerWidth;
      const top = r ? r.top : vh / 2, bottom = r ? r.bottom : top + 44, left = r ? r.left : 16;
      const GAP = 10, EDGE = 12;
      el.style.maxHeight = ""; el.style.top = ""; el.style.left = "";
      el.setAttribute("data-place", "above");
      const h = el.offsetHeight;
      const roomAbove = top - GAP - EDGE, roomBelow = vh - bottom - GAP - EDGE;
      if (h <= roomAbove || roomAbove >= roomBelow) { el.style.maxHeight = `${Math.max(EDGE, Math.round(roomAbove))}px`; return; }
      el.setAttribute("data-place", "below");
      const w = el.offsetWidth || 344;
      el.style.top = `${Math.round(bottom + GAP)}px`;
      el.style.left = `${Math.round(Math.max(EDGE, Math.min(left, vw - w - EDGE)))}px`;
      el.style.maxHeight = `${Math.max(EDGE, Math.round(roomBelow))}px`;
    };
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [open, anchor]);
  const seg = (pressed: boolean) => ({ "aria-pressed": pressed } as const);
  return (
    <div className="popover" id="settingsPop" role="dialog" aria-label="Run settings" data-open={open ? "true" : "false"} ref={pop}>
      <div className="pop-head">
        <h2 className="card-title" style={{ fontSize: "var(--text-base)" }}>Run settings</h2>
        <button className="btn btn-quiet btn-sm" type="button" id="popClose" onClick={onClose}>Close</button>
      </div>
      <div className="pop-row">
        <span className="lbl" id="lblModel">Model</span>
        <div className="seg" role="group" aria-labelledby="lblModel" id="segModel">
          {MODELS.map((m) => <button key={m} type="button" data-model={m} {...seg(settings.model === m)} onClick={() => onChange({ ...settings, model: m })}>{shortModel(m)}</button>)}
        </div>
      </div>
      <div className="pop-row">
        <span className="lbl" id="lblThinking">Thinking</span>
        <div className="seg" role="group" aria-labelledby="lblThinking" id="segThinking">
          {(["enabled", "disabled"] as const).map((t) => <button key={t} type="button" data-thinking={t} {...seg(settings.thinking === t)} onClick={() => onChange({ ...settings, thinking: t })}>{t}</button>)}
        </div>
        <p className="pop-note" id="effortLine" aria-live="polite">{effortLine(settings.thinking)}</p>
      </div>
      <div className="pop-row">
        <label className="lbl" htmlFor="outputDir">Output directory</label>
        <input className="input mono-in" id="outputDir" value={settings.outputDir} onChange={(e) => onChange({ ...settings, outputDir: e.target.value })} />
      </div>
      <div className="pop-row">
        <span className="lbl" id="lblExtra">Extra passes</span>
        <div className="stepper" role="group" aria-labelledby="lblExtra" id="stepExtra">
          <button type="button" id="extraMinus" aria-label="Fewer extra passes" disabled={settings.extraPasses <= EXTRA_MIN} onClick={() => onChange({ ...settings, extraPasses: settings.extraPasses - 1 })}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="square" aria-hidden="true"><path d="M5 12h14" /></svg>
          </button>
          <span className="stepper-v mono" id="extraValue" role="status" aria-live="polite">{settings.extraPasses}</span>
          <button type="button" id="extraPlus" aria-label="More extra passes" disabled={settings.extraPasses >= EXTRA_MAX} onClick={() => onChange({ ...settings, extraPasses: settings.extraPasses + 1 })}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="square" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
          </button>
        </div>
      </div>
    </div>
  );
}
```

(The stepper's disabled ends are the design's own bounds — a control at its limit, not a missing value; the governing rule is about absent data.)

`web/components/Composer.tsx` (`index.html:1194-1263`, starters `:2285-2291`, the request of the design spec §4.3):

```tsx
"use client";
import { useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ApiError, ApiUnreachableError, startResearch, type ResearchRequest } from "@/lib/api";
import { recordSubmission, type SubmittedSettings } from "@/lib/session-store";
import { useConsole } from "./ConsoleProvider";
import { SettingsPopover, shortModel } from "./SettingsPopover";

export const STARTERS = [
  "What are the current constraints on grid-scale battery storage deployment?",
  "How mature is quantum error correction?",
  "What is the current state of sodium-ion battery energy density?",
  "What limits perovskite solar cell commercial lifetime?",
  "Does congestion pricing reduce particulate pollution?",
  "How reliable are consumer-grade air quality sensors?",
];
export const DEFAULT_SETTINGS: SubmittedSettings = { model: "deepseek-flash", thinking: "enabled", extraPasses: 1, outputDir: "output/" };

/* Exactly the body the design specifies: max_iterations always present (0 included). */
export function buildRequest(question: string, s: SubmittedSettings): ResearchRequest {
  const config_overrides: Record<string, unknown> = { llm: { model: s.model, thinking_mode: s.thinking } };
  if (s.outputDir.trim()) config_overrides.output = { directory: s.outputDir.trim() };
  return { query: question.trim(), max_iterations: s.extraPasses, output_format: "markdown", config_overrides };
}

export function Composer() {
  const router = useRouter();
  const { refreshSessions, noteMode, noteUnreachable } = useConsole();
  const [question, setQuestion] = useState("");
  const [settings, setSettings] = useState<SubmittedSettings>(DEFAULT_SETTINGS);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const plus = useRef<HTMLButtonElement>(null);

  async function submit(raw: string) {
    const text = raw.trim();
    if (!text || busy) return;
    setBusy(true); setError(null);
    try {
      const result = await startResearch(buildRequest(text, settings)); // one fetch; never retried
      noteMode(result.mode);
      recordSubmission(result.data.session_id, settings);
      void refreshSessions();
      router.push(`/research/${result.data.session_id}`);
    } catch (e) {
      if (e instanceof ApiUnreachableError) noteUnreachable(e.target, () => void refreshSessions());
      else if (e instanceof ApiError && e.status === 422) setError(`The service rejected the request: ${e.body.issues.map((i) => `${i.location} (${i.type})`).join(", ")}`);
      else if (e instanceof ApiError && e.body.code === "configuration_error") setError(`Service configuration error · ${e.body.reason ?? "unknown"}`);
      else if (e instanceof ApiError) setError(`${e.body.code}: ${e.body.message}`);
      else setError("The request could not be sent.");
    } finally { setBusy(false); }
  }
  const onSubmit = (e: FormEvent) => { e.preventDefault(); void submit(question); };

  return (
    <>
      <div className="composer-wrap" id="composerIdleHost">
        <form className="composer" id="composer" noValidate onSubmit={onSubmit}>
          <label className="sr" htmlFor="prompt">Research question</label>
          <textarea id="prompt" rows={2} placeholder="Ask anything." value={question} onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void submit(question); } }} />
          <div className="composer-bar">
            <span className="pop-anchor">
              <button className="plus" id="plusBtn" type="button" aria-expanded={open} aria-controls="settingsPop" ref={plus} onClick={() => setOpen((o) => !o)}>
                <span className="sr">Run settings</span>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="square" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
              </button>
              <SettingsPopover open={open} settings={settings} onChange={setSettings} onClose={() => setOpen(false)} anchor={plus} />
            </span>
            <span className="setting-pill" id="pillModel"><span className="v" id="pillModelV">{shortModel(settings.model)}</span></span>
            <span className="setting-pill" id="pillThinking"><span className="k">thinking</span><span className="v" id="pillThinkingV">{settings.thinking}</span></span>
            <span className="setting-pill" id="pillExtra"><span className="k">extra passes</span><span className="v" id="pillExtraV">{settings.extraPasses}</span></span>
            <span className="spacer"></span>
            <button className="send" id="sendBtn" type="submit">
              <span className="sr">Start research</span>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="square" aria-hidden="true"><path d="M12 19V5M6 11l6-6 6 6" /></svg>
            </button>
          </div>
          <div className="composer-hint"><span className="hint err" id="composerError" role="alert">{error ?? ""}</span></div>
        </form>
      </div>
      <div className="starters">
        <p className="cap">Try</p>
        <div className="starter-list" id="starterList">
          {STARTERS.map((q) => (
            /* A starter is a complete question: it is submitted in the same click (index.html:2325-2333). */
            <button key={q} type="button" className="starter" data-question={q} onClick={() => { setQuestion(q); void submit(q); }}>
              <span className="arw" aria-hidden="true">→</span><span className="q">{q}</span>
            </button>
          ))}
        </div>
      </div>
    </>
  );
}
```

- [ ] **Step 6: Wire the layout and the idle page**

`web/app/layout.tsx`:

```tsx
import type { ReactNode } from "react";
import { AppShell } from "@/components/AppShell";
import { ConsoleProvider } from "@/components/ConsoleProvider";
import "./globals.css";

export const metadata = { title: "Deep Research — console" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <ConsoleProvider>
          <AppShell>{children}</AppShell>
        </ConsoleProvider>
      </body>
    </html>
  );
}
```

`web/app/page.tsx` (the idle stage, `index.html:1188-1263`):

```tsx
import { Composer } from "@/components/Composer";

export default function IdlePage() {
  return (
    <section className="stage is-on" id="stage-idle" aria-labelledby="idle-h">
      <div className="stack" style={{ gap: "var(--space-8)", maxWidth: "var(--reading-max)", marginInline: "auto" }}>
        <div className="stack-2" style={{ paddingTop: "var(--space-8)" }}>
          <h1 className="display" id="idle-h">Ask anything.</h1>
          <p className="lead">
            A run takes a few minutes; harder questions take longer. You can leave — the run keeps going, and its
            report will be here when you return.
          </p>
        </div>
        <Composer />
      </div>
    </section>
  );
}
```

- [ ] **Step 7: Run the component tests, the type check and the build**

```bash
cd web && npx vitest run test/components 2>&1 | tail -n 6 && npm run -s typecheck && npm run -s build 2>&1 | tail -n 6
```
Expected: `Tests  5 passed`; no type errors; the build lists `/` and `/api/[...path]`.

- [ ] **Step 8: The capture tool and the server launcher for the checkpoints**

`web/scripts/capture-stage.mjs` (used by C1–C3 for single pages; Task 19's `visual.spec.ts` drives the flows):

```js
// Full-page capture of one URL at 1252 or 390 px into visual/<checkpoint>/<name>.png (never viewport-height).
// usage: node scripts/capture-stage.mjs <checkpoint> <name> <url> [--phone] [--view evidence]
import { mkdirSync } from "node:fs";
import path from "node:path";
import { chromium } from "@playwright/test"; // @playwright/test re-exports the browser launchers; `playwright` itself is not a declared dependency

const [checkpoint, name, url, ...flags] = process.argv.slice(2);
if (!checkpoint || !name || !url) { console.error("usage: capture-stage.mjs <checkpoint> <name> <url> [--phone] [--view evidence]"); process.exit(2); }
const phone = flags.includes("--phone");
const view = flags.includes("--view") ? flags[flags.indexOf("--view") + 1] : null;
const dir = path.join("visual", checkpoint);
mkdirSync(dir, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: phone ? { width: 390, height: 844 } : { width: 1252, height: 853 } });
await page.goto(url, { waitUntil: "networkidle" });
if (view === "evidence") {
  // the Report | Evidence toggle is per page load: switch it here, after the report stage has rendered
  await page.locator("#segView button[data-view='evidence']").click();
  await page.locator(".ev-row").first().waitFor();
}
await page.waitForTimeout(700);
const file = path.join(dir, `${name}.png`);
await page.screenshot({ path: file, fullPage: true });
const size = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.scrollHeight]);
console.log(`${file} (${size[0]}x${size[1]} full page)`);
await browser.close();
```

`web/scripts/launch.mjs` — starts and stops a background server that must outlive the shell call (the Conventions bullet explains why a shell `$!` and `kill` cannot). Spawned `detached: true, windowsHide: true`; `start` refuses a port that already answers; `stop` kills the tree and then proves the port refuses:

```js
// usage:
//   node scripts/launch.mjs start <name> <port> <ready-path> "<command>"
//       → exit 1 if the port already answers (a stale server); else spawn the command in the current directory with the
//         current environment (set variables on the same shell line), detached, and wait until http://127.0.0.1:<port><ready-path>
//         answers 200 (120 s); write .launch-<name>.pid
//   node scripts/launch.mjs stop <name> <port>
//       → taskkill /PID <pid> /T /F, then poll until the port refuses connections (10 s); exit 1 loudly if it still answers
import { execSync, spawn } from "node:child_process";
import { existsSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";

const [action, name, portArg, ...rest] = process.argv.slice(2);
const port = Number(portArg);
const pidFile = `.launch-${name}.pid`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// The status the port answers with, or null when the connection is refused.
const answers = async (path) => { try { return (await fetch(`http://127.0.0.1:${port}${path}`)).status; } catch { return null; } };

if (action === "start") {
  const [readyPath, command] = rest;
  if (!name || !port || !readyPath || !command) { console.error('usage: launch.mjs start <name> <port> <ready-path> "<command>"'); process.exit(2); }
  if ((await answers("/")) !== null) { console.error(`port ${port} already answers — a stale server is running; stop it first`); process.exit(1); }
  // detached: a non-detached child sits in a job object that dies with this process and orphans its grandchildren.
  const child = spawn(command, { shell: true, detached: true, windowsHide: true, stdio: "ignore" });
  child.unref();
  writeFileSync(pidFile, String(child.pid));
  for (let i = 0; i < 240; i++) {
    if ((await answers(readyPath)) === 200) { console.log(`up: http://127.0.0.1:${port}${readyPath} (pid ${child.pid} → ${pidFile})`); process.exit(0); }
    await sleep(500);
  }
  console.error(`http://127.0.0.1:${port}${readyPath} did not answer 200 within 120 s (pid ${child.pid} is in ${pidFile}; run stop)`);
  process.exit(1);
} else if (action === "stop") {
  if (!name || !port) { console.error("usage: launch.mjs stop <name> <port>"); process.exit(2); }
  const pid = existsSync(pidFile) ? readFileSync(pidFile, "utf8").trim() : null;
  let killed = false;
  if (pid === null) console.error(`no ${pidFile}: nothing to kill by PID; checking the port`);
  else { try { execSync(`taskkill /PID ${pid} /T /F`, { stdio: "ignore" }); killed = true; } catch { console.error(`taskkill /PID ${pid} /T /F failed — the recorded process is already gone; checking the port`); } }
  for (let i = 0; i < 20; i++) {
    if ((await answers("/")) === null) {
      if (pid !== null) unlinkSync(pidFile);
      console.log(`stopped: port ${port} refuses connections${pid === null ? "" : killed ? ` (pid ${pid} and its tree)` : ` (pid ${pid} was already gone)`}`);
      process.exit(0);
    }
    await sleep(500);
  }
  const why = pid === null ? " and there was no PID file" : killed ? ` although pid ${pid} was killed` : ` and pid ${pid} was already gone`;
  console.error(`FAILED: port ${port} still answers after 10 s${why} — a server is still running; find its owner: powershell "Get-NetTCPConnection -LocalPort ${port} -State Listen | Select OwningProcess" then taskkill /PID <owner> /T /F`);
  process.exit(1);
} else { console.error('usage: launch.mjs start <name> <port> <ready-path> "<command>" | stop <name> <port>'); process.exit(2); }
```

For the API the ready path is `/research` (the root answers 404); for the app it is `/`. The command runs in the launcher's working directory: invoke `node web/scripts/launch.mjs …` from the worktree root for the API and `node scripts/launch.mjs …` from `web/` for the app.

- [ ] **Step 9: Checkpoint C1 — idle at both widths**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 node web/scripts/launch.mjs start api 8010 /research "\"$PY\" -m deep_research.api --mode replay --port 8010"
cd web && npm run -s build >/dev/null && DEEP_RESEARCH_API_URL=http://127.0.0.1:8010 node scripts/launch.mjs start app 3010 / "npm run -s start -- --port 3010"
node scripts/capture-stage.mjs C1 01-idle http://127.0.0.1:3010/ && node scripts/capture-stage.mjs C1 07-idle-phone http://127.0.0.1:3010/ --phone
node scripts/launch.mjs stop app 3010 && cd .. && node web/scripts/launch.mjs stop api 8010
```
Expected first: two `up:` lines; last: two `stopped: port … refuses connections` lines (a `FAILED:` line means a server survived — find and kill its owner as the message says before going on, or Task 15's Playwright servers will refuse ports 3010/8010).
Expected in between: two `visual/C1/*.png (…x… full page)` lines. Review both images full-height against `docs/design/reference/01-idle.png` and `07-idle-phone.png`: the sidebar (296 px, `Deep Research`, `New Research`, `Sessions 0`, the footer sentence), the topbar with the muted `replay mode` chip on the right, `Ask anything.`, the lead, the composer with the three pills (`flash`, `thinking enabled`, `extra passes 1`), the six starters; at 390 px the sidebar is a drawer and nothing scrolls sideways. Attach the two images to the task's summary for the controller (R4: the controller's own viewing is at C3 and C4; the implementer's full-height review happens at every checkpoint). Fix any difference in copy, position or colour before committing.

- [ ] **Step 10: Commit**

```bash
git add web/lib/session-store.ts web/components web/app/layout.tsx web/app/page.tsx web/scripts/capture-stage.mjs web/scripts/launch.mjs web/test/components && git commit -m "feat(web): shell, sidebar, topbar chips, composer with settings popover (C1)"
```

---

### Task 15: The Playwright harness and the session page's S4 states — not in memory, API unreachable (spec §4.3 stage table, §4.4, §4.5 Playwright setup; T-E8, T-E9, T-E13; Review Focus 1)

**Files:**
- Create: `web/playwright.config.ts`, `web/e2e/support.ts`, `web/e2e/replay-chip.spec.ts`, `web/e2e/not-found.spec.ts`, `web/e2e/api-down.spec.ts`, `web/app/research/[id]/page.tsx`, `web/components/SessionScreen.tsx`, `web/components/SessionNotFound.tsx`

**Interfaces:**
- Consumes: `getStatus`, `ApiError`, `ApiUnreachableError` (Task 12); `useConsole` (Task 14).
- Produces: the three `webServer`s (API replay on 8010; the app on 3010; the app on 3011 pointed at `DEEP_RESEARCH_DEAD_PORT`); `e2e/support.ts` helpers `API`, `DEAD_APP`, `installTransitionRecorder(page)`, `transitions(page)`, `submit(page, question) → sessionId`, `waitTerminal(request, sessionId)`; `<SessionScreen sessionId />` (Task 16 gives it the stream and the stages); `<SessionNotFound onNew />`.

- [ ] **Step 1: Write the Playwright config**

`web/playwright.config.ts`:

```ts
import { defineConfig, devices } from "@playwright/test";
import { execSync } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import path from "node:path";

// Paths resolve from process.cwd() (= web/), never __dirname, so this file stays valid as an ES module.
const web = process.cwd();
const repo = path.resolve(web, "..");
// The venv lives at the main checkout root; inside a .worktrees/* tree the default does not exist and
// DEEP_RESEARCH_PYTHON is required. Quoted below: every interpreter path on this machine has spaces.
const python = process.env.DEEP_RESEARCH_PYTHON ?? path.resolve(repo, ".venv", "Scripts", "python.exe");
if (!existsSync(python)) {
  throw new Error(`DEEP_RESEARCH_PYTHON must point at the venv interpreter (…/.venv/Scripts/python.exe); ${python} does not exist`);
}
// The API server's temp root lives here — outside test-results/, which Playwright clears at the start
// of a run — so a forced kill leaks nothing into %TEMP% and nothing is swept away mid-run.
const tmp = path.join(web, ".e2e-tmp");
mkdirSync(tmp, { recursive: true });
// One closed, ordinary port for the "API unreachable" app, reserved once: ??= keeps the value the
// worker processes inherit when they re-evaluate this file. Never port 1 (the Fetch bad-port list).
process.env.DEEP_RESEARCH_DEAD_PORT ??= execSync(
  `node -e "const s=require('net').createServer().listen(0,()=>{process.stdout.write(String(s.address().port));s.close()})"`,
).toString().trim();
const deadPort = process.env.DEEP_RESEARCH_DEAD_PORT;
const API = "http://127.0.0.1:8010";

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  outputDir: "test-results",
  use: { baseURL: "http://127.0.0.1:3010", ...devices["Desktop Chrome"], viewport: { width: 1252, height: 853 } },
  projects: [
    { name: "chromium", testIgnore: /visual\.spec\.ts/ },
    { name: "visual", testMatch: /visual\.spec\.ts/ },
  ],
  webServer: [
    {
      command: `${JSON.stringify(python)} -m deep_research.api --mode replay --port 8010`,
      url: `${API}/research`, cwd: repo, timeout: 120_000, reuseExistingServer: false,
      env: { PYTHONPATH: "src", PYTHONDONTWRITEBYTECODE: "1", TEMP: tmp, TMP: tmp },
    },
    { command: "npm run -s start -- --port 3010", url: "http://127.0.0.1:3010/", cwd: web, timeout: 120_000, reuseExistingServer: false, env: { DEEP_RESEARCH_API_URL: API } },
    { command: "npm run -s start -- --port 3011", url: "http://127.0.0.1:3011/", cwd: web, timeout: 120_000, reuseExistingServer: false, env: { DEEP_RESEARCH_API_URL: `http://127.0.0.1:${deadPort}` } },
  ],
});
```

- [ ] **Step 2: Write the shared helpers and the three specs (the failing tests)**

`web/e2e/support.ts`:

```ts
import { expect, type APIRequestContext, type Page } from "@playwright/test";

export const API = "http://127.0.0.1:8010";
export const DEAD_APP = "http://127.0.0.1:3011";
export const deadPort = () => process.env.DEEP_RESEARCH_DEAD_PORT!;

export interface Transition { loop: string | null; arc: string | null; state: string | null; stage: string | null }
/* Record every data-loop/data-arc/data-state change before the page's scripts run: the flowing arc
   state lasts ≈ 450 ms at the default pacing, shorter than a polling assertion's back-off. */
export async function installTransitionRecorder(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as { __drTransitions: Transition[] };
    w.__drTransitions = [];
    const record = (t: Element) => w.__drTransitions.push({ loop: t.getAttribute("data-loop"), arc: t.getAttribute("data-arc"), state: t.getAttribute("data-state"), stage: t.getAttribute("data-stage") });
    new MutationObserver((mutations) => {
      for (const m of mutations) if (m.target instanceof Element && (m.target.id === "spineWrap" || m.target.matches("li[data-stage]"))) record(m.target);
    }).observe(document, { attributes: true, subtree: true, attributeFilter: ["data-loop", "data-arc", "data-state"] });
  });
}
export const transitions = (page: Page) => page.evaluate(() => (window as unknown as { __drTransitions: Transition[] }).__drTransitions);

export async function submit(page: Page, question: string): Promise<string> {
  await page.goto("/");
  await page.getByLabel("Research question").fill(question);
  await page.getByRole("button", { name: "Start research" }).click();
  await page.waitForURL(/\/research\/[0-9a-f]+$/);
  return page.url().split("/").pop()!;
}
export async function waitTerminal(request: APIRequestContext, sessionId: string): Promise<void> {
  await expect.poll(async () => (await (await request.get(`${API}/research/${sessionId}/status`)).json()).status, { timeout: 60_000 }).not.toBe("running");
}
```

`web/e2e/replay-chip.spec.ts` (T-E13):

```ts
import { expect, test } from "@playwright/test";

test("the topbar shows the muted replay-mode chip on every page served by the replay API", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#modeChip")).toHaveText("replay mode");
  await page.goto("/research/does-not-exist");
  await expect(page.locator("#modeChip")).toHaveText("replay mode");
});
```

`web/e2e/not-found.spec.ts` (T-E9):

```ts
import { expect, test } from "@playwright/test";

test("an unknown session says it is not in the service's memory", async ({ page }) => {
  await page.goto("/research/does-not-exist");
  await expect(page.getByText("This session isn't in the service's memory — sessions are lost when the API restarts.")).toBeVisible();
  await page.getByRole("button", { name: "New research" }).click();
  await expect(page).toHaveURL("http://127.0.0.1:3010/");
  await expect(page.getByLabel("Research question")).toBeVisible();
});
```

`web/e2e/api-down.spec.ts` (T-E8; Review Focus 1):

```ts
import { expect, test } from "@playwright/test";
import { DEAD_APP, deadPort } from "./support";

test("the banner names the unreachable service; the composer stays usable and never retries a POST", async ({ page }) => {
  const posts: string[] = [];
  page.on("request", (r) => { if (r.method() === "POST" && r.url().endsWith("/api/research")) posts.push(r.url()); });
  await page.goto(`${DEAD_APP}/`);
  const banner = page.getByRole("alert").filter({ hasText: "Research service not reachable at" });
  await expect(banner).toContainText(`Research service not reachable at http://127.0.0.1:${deadPort()}`);
  await expect(banner.getByRole("button", { name: "Retry" })).toBeVisible();
  const box = page.getByLabel("Research question");
  await box.fill("How mature is quantum error correction?");
  await page.getByRole("button", { name: "Start research" }).click();
  await expect(banner).toBeVisible();
  await expect(box).toHaveValue("How mature is quantum error correction?");
  await page.getByRole("button", { name: "Start research" }).click();
  await expect(banner).toBeVisible();
  await expect.poll(() => posts.length).toBe(2); // one POST per click, none of the app's own
  await expect(page).toHaveURL(`${DEAD_APP}/`);
});
```

- [ ] **Step 3: Run the e2e suite to verify the new specs fail**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
cd web && DEEP_RESEARCH_PYTHON="$PY" npm run -s test:e2e 2>&1 | tail -n 20
```
Expected: the three servers start; `replay-chip` and `api-down` pass already (the chip and the banner are Task 14's); `not-found` fails — `/research/does-not-exist` is Next's own 404 page, not the sentence.

- [ ] **Step 4: The session page and the two S4 states**

`web/app/research/[id]/page.tsx`:

```tsx
import { SessionScreen } from "@/components/SessionScreen";

export default async function SessionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <SessionScreen sessionId={id} />;
}
```

`web/components/SessionNotFound.tsx`:

```tsx
"use client";
export function SessionNotFound({ onNew }: { onNew: () => void }) {
  return (
    <section className="stage is-on" id="stage-not-found" aria-labelledby="not-found-h">
      <div className="run-wrap">
        <div className="note bad" role="status">
          <div className="note-head"><span className="mk">gone</span><span id="not-found-h">This session isn't in the service's memory — sessions are lost when the API restarts.</span></div>
          <button className="btn btn-primary" type="button" onClick={onNew}>New research</button>
        </div>
      </div>
    </section>
  );
}
```

`web/components/SessionScreen.tsx` (this task: status, 404, unreachable; Task 16 adds the stream, `run` and the stages):

```tsx
"use client";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, ApiUnreachableError, getStatus, type ResearchSessionResponse } from "@/lib/api";
import { useConsole } from "./ConsoleProvider";
import { SessionNotFound } from "./SessionNotFound";

export function SessionScreen({ sessionId }: { sessionId: string }) {
  const router = useRouter();
  const { noteMode, noteUnreachable, clearUnreachable, setChip } = useConsole();
  const [status, setStatus] = useState<ResearchSessionResponse | null>(null);
  const [notFound, setNotFound] = useState(false);

  const load = useCallback(async () => {
    try {
      const result = await getStatus(sessionId);
      noteMode(result.mode);
      setStatus(result.data);
      clearUnreachable();
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) setNotFound(true);
      else if (error instanceof ApiUnreachableError) noteUnreachable(error.target, () => void load());
    }
  }, [sessionId, noteMode, clearUnreachable, noteUnreachable]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => () => setChip(null), [setChip]);

  if (notFound) return <SessionNotFound onNew={() => router.push("/")} />;
  if (!status) {
    return (
      <section className="stage is-on" id="stage-loading"><div className="run-wrap"><p className="avail">loading session</p></div></section>
    );
  }
  return (
    <section className="stage is-on" id="stage-running" aria-labelledby="running-h">
      <div className="run-wrap">
        <div className="ask-head">
          <p className="eyebrow" style={{ margin: 0 }}>Session running</p>
          <h1 className="ask-q ask-locked" id="running-h">{status.query}</h1>
        </div>
      </div>
    </section>
  );
}
```

- [ ] **Step 5: Run the e2e suite**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
cd web && DEEP_RESEARCH_PYTHON="$PY" npm run -s test:e2e 2>&1 | tail -n 12
```
Expected: `3 passed`.

- [ ] **Step 6: Commit**

```bash
git add web/playwright.config.ts web/e2e "web/app/research/[id]/page.tsx" web/components/SessionScreen.tsx web/components/SessionNotFound.tsx && git commit -m "feat(web): Playwright harness (replay API, app, dead-port app); session page S4 states"
```

---

### Task 16: The running stage on the real stream — Submitted, Running, service stopped, reconnect; checkpoint C2 (spec §4.3 data flow, stage table, `lib/stream.ts` reconnect; T-W5 Counters; T-E1 running half, T-E2, T-E3, T-E7, T-E11; Review Focus 3)

**Files:**
- Create: `web/components/SettingsStrip.tsx`, `web/components/SubmittedStage.tsx`, `web/components/RunningPipeline.tsx`, `web/components/Spine.tsx`, `web/components/Counters.tsx`
- Modify: `web/components/SessionScreen.tsx` (replace the Task 15 body with the state machine below)
- Test: `web/test/components/counters.test.tsx`, `web/test/components/spine.test.tsx`, `web/e2e/running.spec.ts`, `web/e2e/arcs.spec.ts`, `web/e2e/sidebar.spec.ts`, `web/e2e/visual.spec.ts` (C2 part)

**Interfaces:**
- Consumes: `lib/run-state.ts` (Task 11), `lib/stream.ts`, `lib/api.ts` (Task 12), `lib/format.ts` (Task 10), `lib/session-store.ts`, `useConsole` (Task 14), `e2e/support.ts` (Task 15).
- Produces: `<SettingsStrip settings ceiling />`, `<SubmittedStage question strip />`, `<RunningPipeline run question strip startedAt />`, `<Spine marks run withArcs />`, `<Counters counters absentText pass />`; in `SessionScreen`: the stage machine (`submitted` → `running` → the terminal branch), the reconnect ladder, the chip via `setChip`, and `SessionScreen`'s terminal rendering hook that Tasks 17–18 replace (`<FinishedHeader>` below).

- [ ] **Step 1: Write the failing component tests**

`web/test/components/counters.test.tsx`:

```tsx
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Counters } from "../../components/Counters";
import { emptyCounters } from "../../lib/run-state";

const dd = (c: HTMLElement, key: string) => c.querySelector(`dd[data-counter="${key}"]`)!;

describe("Counters", () => {
  it("reads muted text for a counter whose event has not arrived, never 0", () => {
    const { container } = render(<Counters counters={emptyCounters()} absentText="not yet" pass={1} />);
    expect(container.querySelectorAll("dd").length).toBe(7);
    for (const dd_ of container.querySelectorAll("dd")) { expect(dd_.textContent).toBe("not yet"); expect(dd_.className).toContain("avail"); }
    expect(container.textContent).not.toMatch(/\b0\b/);
    expect(container.querySelector("#runCountersPass")!.textContent).toBe("pass 1");
  });
  it("prints the rows the prototype prints", () => {
    const counters = { ...emptyCounters(), subTopicsResearched: 3, subTopicsTotal: 4, toolCalls: 12, findings: 18, sources: 14, verified: 9, corrected: 2, dropped: 1, statements: 11, refused: 1, reviewSeen: true, reviewScore: null };
    const { container } = render(<Counters counters={counters} absentText="not reached" pass={2} />);
    expect(dd(container, "subTopics").textContent).toBe("3 of 4");
    expect(dd(container, "verified").textContent).toBe("9 / 2 / 1");
    expect(dd(container, "statements").textContent).toBe("11 / 1");
    expect(dd(container, "review").textContent).toBe("not scored");
    expect(dd(container, "review").className).toContain("avail");
  });
});
```

`web/test/components/spine.test.tsx`:

```tsx
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Spine } from "../../components/Spine";
import { failedMarks, marksFor, newRunState } from "../../lib/run-state";

describe("Spine", () => {
  it("paints data-state per row, data-fed from the previous row, and the caption", () => {
    const run = newRunState(2);
    run.marks = { planner: "done", researcher: "loop" };
    run.active = "source_evaluator";
    run.rearmedFirst = "researcher";
    run.captions.researcher = "1 missing target only";
    const { container } = render(<Spine marks={marksFor(run, run.active)} run={run} withArcs={false} />);
    const rows = [...container.querySelectorAll("li[data-stage]")];
    expect(rows.map((r) => r.getAttribute("data-stage"))).toEqual(["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]);
    expect(rows.map((r) => r.getAttribute("data-state"))).toEqual(["done", "loop", "active", "pending", "pending", "pending", "pending"]);
    expect(rows[1].getAttribute("data-fed")).toBe("1");
    expect(rows[3].getAttribute("data-fed")).toBe("0");
    expect(rows[1].querySelector(".stage-meta")!.textContent).toBe("1 missing target only");
    expect(rows[1].querySelector(".loops")!.textContent).toBe("↺");
    expect(rows[2].querySelector(".stage-name .sr")!.textContent).toBe(" (in progress)");
  });
  it("marks a halted run's Publishing row skipped", () => {
    const run = newRunState(2);
    run.openNode = "planner";
    const { container } = render(<Spine marks={failedMarks(run, "failed")} run={run} withArcs={false} />);
    expect(container.querySelector('li[data-stage="finalize_report"]')!.getAttribute("data-state")).toBe("skipped");
    expect(container.querySelector('li[data-stage="planner"]')!.getAttribute("data-state")).toBe("active");
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd web && npx vitest run test/components/counters.test.tsx test/components/spine.test.tsx 2>&1 | tail -n 6
```
Expected: both files fail to import.

- [ ] **Step 3: The strip, the submitted stage, the counters, the spine**

`web/components/SettingsStrip.tsx` (`optionsHTML`, `index.html:1836-1846`; the "not recorded" rule of spec §4.3):

```tsx
"use client";
import type { SubmittedSettings } from "@/lib/session-store";

export function SettingsStrip({ settings, ceiling, id }: { settings: SubmittedSettings | null; ceiling: number | null; id?: string }) {
  if (!settings) {
    return (
      <div className="opts" id={id}>
        {ceiling ? <span className="opt"><span className="k">extra passes</span>{ceiling - 1}</span> : null}
        <span className="avail">settings as submitted: not recorded</span>
      </div>
    );
  }
  return (
    <div className="opts" id={id}>
      <span className="opt"><span className="k">model</span>{settings.model}</span>
      <span className="opt"><span className="k">thinking</span>{settings.thinking}</span>
      <span className="opt"><span className="k">effort</span>{settings.thinking === "enabled" ? "per agent" : "not sent"}</span>
      <span className="opt"><span className="k">extra passes</span>{settings.extraPasses}</span>
      {settings.outputDir ? <span className="opt"><span className="k">out</span>{settings.outputDir}</span> : null}
    </div>
  );
}
```

`web/components/SubmittedStage.tsx` (`index.html:1277-1285`):

```tsx
"use client";
import type { ReactNode } from "react";
export function SubmittedStage({ question, strip }: { question: string; strip: ReactNode }) {
  return (
    <section className="stage is-on" id="stage-submitted" aria-labelledby="submitted-h">
      <div className="run-wrap">
        <div className="ask-head">
          <p className="eyebrow" style={{ margin: 0 }}>Question locked in</p>
          <h1 className="ask-q ask-locked" id="submitted-h" aria-describedby="submittedOpts">{question}</h1>
          {strip}
        </div>
      </div>
    </section>
  );
}
```

`web/components/Counters.tsx` (`renderCounters`, `index.html:3057-3073`):

```tsx
"use client";
import { COUNTER_ROWS, type Counters as CounterValues } from "@/lib/run-state";

export function Counters({ counters, absentText, pass, id = "runCounters" }: { counters: CounterValues; absentText: "not yet" | "not reached"; pass: number; id?: string }) {
  return (
    <div className="counters">
      <div className="row-between">
        <p className="eyebrow" style={{ margin: 0 }}>counted from the event stream</p>
        <span className="avail-mono" id="runCountersPass">pass {pass}</span>
      </div>
      <dl className="kv kv-2" id={id}>
        {COUNTER_ROWS.map((row) => {
          const v = row.value(counters);
          return [
            <dt key={`${row.key}-k`}>{row.label} <span className="cap">{row.scope}</span></dt>,
            v === null ? <dd key={row.key} data-counter={row.key} className="avail">{absentText}</dd>
              : typeof v === "object" ? <dd key={row.key} data-counter={row.key} className="avail">{v.muted}</dd>
              : <dd key={row.key} data-counter={row.key}>{v}</dd>,
          ];
        })}
      </dl>
    </div>
  );
}
```

`web/components/Spine.tsx` (`buildSpine`/`syncSpine` `index.html:2565-2650`, `drawLoop`/`setLoopState` `:2505-2563`, the row markup `:2577-2581`):

```tsx
"use client";
import { useLayoutEffect, useRef } from "react";
import { ARCS, STAGES, type NodeId, type PaintedMark, type RunState } from "@/lib/run-state";

interface Props { marks: Partial<Record<NodeId, PaintedMark>>; run: RunState; withArcs: boolean; id?: string }

/* Seven rows, one per graph node; data-stage binds the arcs to the node, never to list position.
   With arcs, the list sits in .spine-wrap[data-loop][data-arc] under an SVG whose path is measured
   from the two rows' bullets, exactly as drawLoop() measures it. */
export function Spine({ marks, run, withArcs, id = "spine" }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLOListElement>(null);
  const done = (st: string | undefined) => st === "done" || st === "loop";
  const rows = STAGES.map((s, i) => {
    const st = marks[s.id] || "pending";
    const prev = i > 0 ? marks[STAGES[i - 1].id] || "pending" : null;
    const showLoop = run.rearmedFirst === s.id && st === "loop";
    return (
      <li key={s.id} data-stage={s.id} data-state={st} data-fed={prev === null ? undefined : done(prev) ? "1" : "0"} style={{ ["--delay" as string]: String(i * 60) }}>
        <span className="bullet" aria-hidden="true">{i + 1}</span>
        <span>
          <span className="stage-name">{s.label}<span className="sr">{st === "active" ? " (in progress)" : ""}</span></span>
          <span className="stage-meta">{run.captions[s.id] || s.meta}</span>
          <span className="stage-meta loops" hidden={!showLoop}>{showLoop ? "↺" : ""}</span>
        </span>
      </li>
    );
  });
  useLayoutEffect(() => {
    if (!withArcs) return;
    const draw = () => {
      const host = wrap.current, ol = list.current;
      const svg = host?.querySelector<SVGSVGElement>("svg.loop-layer");
      if (!host || !ol || !svg || !run.arc) return;
      const arc = ARCS[run.arc];
      const from = ol.querySelector<HTMLElement>(`li[data-stage="${arc.from}"] .bullet`);
      const to = ol.querySelector<HTMLElement>(`li[data-stage="${arc.to}"] .bullet`);
      if (!from || !to) return;
      const hostRect = host.getBoundingClientRect();
      const w = Math.round(hostRect.width), h = Math.round(hostRect.height);
      if (!w || !h) return;
      svg.setAttribute("width", String(w)); svg.setAttribute("height", String(h));
      const leave = from.getBoundingClientRect(), enter = to.getBoundingClientRect();
      const x1 = enter.left - hostRect.left + enter.width / 2, y1 = enter.top - hostRect.top + enter.height / 2;
      const x2 = leave.left - hostRect.left + leave.width / 2, y2 = leave.top - hostRect.top + leave.height / 2;
      const r = Math.max(2, x1 - enter.width / 2 - 8);
      const d = `M ${x2} ${y2} H ${r} V ${y1} H ${x1 + 10}`;
      svg.querySelector(".loop-base")?.setAttribute("d", d);
      svg.querySelector(".loop-flow")?.setAttribute("d", d);
      svg.querySelector(".loop-head")?.setAttribute("d", `M ${x1 + 3} ${y1 - 4} L ${x1 + 11} ${y1} L ${x1 + 3} ${y1 + 4} Z`);
    };
    draw();
    window.addEventListener("resize", draw);
    return () => window.removeEventListener("resize", draw);
  }, [withArcs, run.arc, run.loop, marks]);
  if (!withArcs) return <ol className="spine-lg" id={id} ref={list}>{rows}</ol>;
  return (
    <div className="spine-wrap" id="spineWrap" data-loop={run.loop} {...(run.arc ? { "data-arc": run.arc } : {})} ref={wrap}>
      <svg className="loop-layer" aria-hidden="true"><path className="loop-base" /><path className="loop-flow" /><path className="loop-head" /></svg>
      <ol className="spine-lg" id={id} ref={list}>{rows}</ol>
    </div>
  );
}
```

- [ ] **Step 4: The running pipeline**

`web/components/RunningPipeline.tsx` (`index.html:1288-1329`; `updateRunningChrome` `:3104-3120`; `renderLoopTag` `:3074-3082`):

```tsx
"use client";
import { useEffect, useState, type ReactNode } from "react";
import { fmtElapsed } from "@/lib/format";
import { AGENT_ORDER, BLURB, STAGES, marksFor, type RunState } from "@/lib/run-state";
import { Counters } from "./Counters";
import { Spine } from "./Spine";

export function RunningPipeline({ run, question, strip, startedAt }: { run: RunState; question: string; strip: ReactNode; startedAt: string }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const tick = () => setElapsed(Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000)));
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [startedAt]);
  let idx = run.active ? AGENT_ORDER.indexOf(run.active) : STAGES.length - 1;
  if (idx < 0) idx = 0;
  const stage = STAGES[idx];
  return (
    <section className="stage is-on" id="stage-running" aria-labelledby="running-h">
      <div className="run-wrap">
        <div className="ask-head">
          <p className="eyebrow" style={{ margin: 0 }}>Session running</p>
          <h1 className="ask-q ask-locked" id="running-h" aria-describedby="runningOpts">{question}</h1>
          {strip}
          <div className="ask-meta"><span className="avail-mono" id="runElapsed">{fmtElapsed(elapsed)} elapsed</span></div>
        </div>
        <div className="card stack" style={{ gap: "var(--space-5)" }}>
          <div className="pipe-now">
            <div>
              <span className="cap">Now</span>
              <div className="now-stage" id="runNow">{stage.label}</div>
              <p className="sm" id="runningBlurb">{run.blurbs[stage.id] || BLURB[stage.id]}</p>
              <p className="loop-tag" id="runLoopTag" hidden={!run.tag} {...(run.tag ? { "data-kind": run.tag.kind } : {})}>
                <span className="tag">{run.tag?.label ?? ""}</span><span className="why">{run.tag?.text ?? ""}</span>
              </p>
            </div>
            <div className="row-between">
              <span className="avail-mono" id="runProgressLabel">stage {idx + 1} of {STAGES.length}</span>
              <span className="avail-mono" id="runPasses">pass {run.pass} of {run.maxPasses}</span>
            </div>
            <span className="track" id="runTrack" role="progressbar" aria-label="Pipeline progress" aria-valuemin={1} aria-valuemax={7} aria-valuenow={idx + 1}>
              <span id="runTrackFill" style={{ width: `${Math.round(((idx + 1) / STAGES.length) * 100)}%` }}></span>
            </span>
          </div>
          <Spine marks={marksFor(run, run.active)} run={run} withArcs />
          <Counters counters={run.counters} absentText="not yet" pass={run.countersPass} />
        </div>
      </div>
    </section>
  );
}
```

- [ ] **Step 5: The session state machine**

Replace `web/components/SessionScreen.tsx` with:

```tsx
"use client";
import { useCallback, useEffect, useReducer, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { ApiError, ApiUnreachableError, getStatus, streamUrl, type ResearchSessionResponse } from "@/lib/api";
import { toSessionView, type SessionView } from "@/lib/format";
import { applyEvent, newRunState, toRunEvent, type RunState } from "@/lib/run-state";
import { readSubmission, submittedBeatRemaining, type Submission } from "@/lib/session-store";
import { backoffDelaysMs, readStream } from "@/lib/stream";
import { useConsole } from "./ConsoleProvider";
import { Counters } from "./Counters";
import { RunningPipeline } from "./RunningPipeline";
import { SessionNotFound } from "./SessionNotFound";
import { SettingsStrip } from "./SettingsStrip";
import { SubmittedStage } from "./SubmittedStage";

/* The stage is derived from /status and the stream (spec §4.3 stage table):
   404 → not in memory · running+finished_at → service stopped · running → Submitted (this tab, < 2.2 s) then Running ·
   failed → Failed (Task 18) · other terminal → Report (Task 17). */
export function SessionScreen({ sessionId }: { sessionId: string }) {
  const router = useRouter();
  const { noteMode, noteUnreachable, clearUnreachable, setChip } = useConsole();
  const [status, setStatus] = useState<ResearchSessionResponse | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [submission, setSubmission] = useState<Submission | null>(null);
  const [beat, setBeat] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const run = useRef<RunState>(newRunState(null));
  const ceilingFromStream = useRef<number | null>(null);
  const wake = useRef<(() => void) | null>(null);
  const [version, bump] = useReducer((n: number) => n + 1, 0);

  // Facts only this tab has (sessionStorage): read after mount so the server render never disagrees.
  useEffect(() => {
    const held = readSubmission(sessionId);
    setSubmission(held);
    const remaining = submittedBeatRemaining(sessionId);
    if (remaining > 0) { setBeat(true); const t = setTimeout(() => setBeat(false), remaining); return () => clearTimeout(t); }
  }, [sessionId]);

  const load = useCallback(async (): Promise<ResearchSessionResponse | null> => {
    try {
      const result = await getStatus(sessionId);
      noteMode(result.mode);
      setStatus(result.data);
      clearUnreachable();
      return result.data;
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) setNotFound(true);
      else if (error instanceof ApiUnreachableError) noteUnreachable(error.target, () => { wake.current?.(); void load(); });
      return null;
    }
  }, [sessionId, noteMode, clearUnreachable, noteUnreachable]);
  useEffect(() => { void load(); }, [load]);

  const ceiling = useCallback(() => ceilingFromStream.current ?? (submission ? submission.settings.extraPasses + 1 : null), [submission]);

  // The stream: opened for every known session (a finished one replays and closes); reconnect on the
  // ladder while the session is still running; every attempt rebuilds `run` from event 1.
  const ready = status !== null && !notFound && !(status.status === "running" && status.finished_at !== null);
  useEffect(() => {
    if (!ready) return;
    const controller = new AbortController();
    let cancelled = false;
    const delays = backoffDelaysMs();
    const sleep = (ms: number) => new Promise<void>((resolve) => { const t = setTimeout(() => { wake.current = null; resolve(); }, ms); wake.current = () => { clearTimeout(t); wake.current = null; resolve(); }; });
    (async () => {
      while (!cancelled) {
        run.current = newRunState(ceiling());
        bump();
        const end = await readStream(streamUrl(sessionId), {
          onOpen: (mode) => { noteMode(mode); clearUnreachable(); setStreaming(true); },
          onEvent: (event) => {
            if (event.event_type === "graph.session.started" && typeof event.metadata.max_extra_passes === "number") ceilingFromStream.current = (event.metadata.max_extra_passes as number) + 1;
            applyEvent(run.current, toRunEvent(event));
            bump();
          },
        }, controller.signal);
        setStreaming(false);
        if (cancelled) return;
        const latest = await load();
        if (latest === null && end.kind === "failed") { /* unreachable or 404: the banner or the not-found state is up */ }
        if (latest && (latest.status !== "running" || latest.finished_at !== null)) return;
        if (notFound) return;
        await sleep(delays.next().value);
      }
    })();
    return () => { cancelled = true; controller.abort(); wake.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, ready]);

  // The topbar chip follows the run while streaming and /status afterwards.
  const passes = ceiling();
  const view: SessionView | null = status ? toSessionView(status, passes) : null;
  if (view && status?.status === "running" && streaming) { view.iteration = run.current.pass - 1; view.passes = run.current.maxPasses; }
  useEffect(() => { setChip(view); return () => setChip(null); }, [setChip, status, version, streaming, passes]); // eslint-disable-line react-hooks/exhaustive-deps

  if (notFound) return <SessionNotFound onNew={() => router.push("/")} />;
  if (!status) return <section className="stage is-on" id="stage-loading"><div className="run-wrap"><p className="avail">loading session</p></div></section>;
  const strip = <SettingsStrip settings={submission?.settings ?? null} ceiling={passes} id="runningOpts" />;
  if (status.status === "running" && status.finished_at !== null) return <StoppedStage status={status} run={run.current} strip={strip} onNew={() => router.push("/")} />;
  if (status.status === "running") {
    if (beat) return <SubmittedStage question={status.query} strip={strip} />;
    return <RunningPipeline run={run.current} question={status.query} strip={strip} startedAt={status.started_at} />;
  }
  return <FinishedHeader status={status} run={run.current} strip={strip} />;
}

/* S4: the service stopped while the run was in progress (running + finished_at). */
function StoppedStage({ status, run, strip, onNew }: { status: ResearchSessionResponse; run: RunState; strip: ReactNode; onNew: () => void }) {
  return (
    <section className="stage is-on" id="stage-stopped" aria-labelledby="stopped-h">
      <div className="run-wrap">
        <div className="ask-head">
          <p className="eyebrow" style={{ margin: 0 }}>Session interrupted</p>
          <h1 className="ask-q ask-locked" id="stopped-h">{status.query}</h1>
          {strip}
        </div>
        <div className="note bad" role="status">
          <div className="note-head"><span className="mk">stopped</span><span>The service stopped while this run was in progress. Nothing was published.</span></div>
          <button className="btn btn-primary" type="button" onClick={onNew}>New research</button>
        </div>
        <div className="card stack" style={{ gap: "var(--space-5)" }}>
          <Counters counters={run.counters} absentText="not reached" pass={run.countersPass} />
        </div>
      </div>
    </section>
  );
}

/* A finished session's header and frozen counters; Task 17 renders the Report stage and Task 18 the Failed stage here. */
function FinishedHeader({ status, run, strip }: { status: ResearchSessionResponse; run: RunState; strip: ReactNode }) {
  return (
    <section className="stage is-on" id="stage-finished" aria-labelledby="finished-h">
      <div className="run-wrap">
        <div className="ask-head">
          <p className="eyebrow" style={{ margin: 0 }}>Session finished</p>
          <h1 className="ask-q ask-locked" id="finished-h">{status.query}</h1>
          {strip}
        </div>
        <div className="card stack" style={{ gap: "var(--space-5)" }}>
          <Counters counters={run.counters} absentText="not reached" pass={run.countersPass} />
        </div>
      </div>
    </section>
  );
}
```

- [ ] **Step 6: Run the component tests, the type check and the build**

```bash
cd web && npx vitest run test/components 2>&1 | tail -n 6 && npm run -s typecheck && npm run -s build 2>&1 | tail -n 6
```
Expected: `Tests  9 passed`; no type errors; the build lists `/`, `/api/[...path]`, `/research/[id]`.

- [ ] **Step 7: Verification spec — the running stage end to end** (written after the code, per the TDD boundary in Conventions)

`web/e2e/running.spec.ts` (T-E1 running half, T-E7):

```ts
import { expect, test } from "@playwright/test";
import { submit, waitTerminal } from "./support";

const ACME = "What was the Acme widget adoption rate in the United States in 2024?";

test("submit → Submitted beat → Running; the sidebar shows the case's question", async ({ page, request }) => {
  const id = await submit(page, "How mature is quantum error correction?");
  await expect(page.locator("#stage-submitted")).toBeVisible();
  await expect(page.locator("#submitted-h")).toHaveText(ACME);
  await expect(page.locator("#stage-running")).toBeVisible({ timeout: 5_000 });
  // The planner finishes inside the 2.2 s beat: the first Running render already has Planning done.
  await expect(page.locator('li[data-stage="planner"]')).toHaveAttribute("data-state", "done");
  const active = await page.locator("li[data-stage][data-state='active']").getAttribute("data-stage");
  expect(["researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]).toContain(active);
  await expect(page.locator("#topbarStatus .chip")).toContainText("Running · pass 1 of 2");
  await expect(page.locator(`.sb-item[data-session="${id}"] .q`)).toHaveText(ACME);
  await expect(page.locator("#runElapsed")).toContainText("elapsed");
  await waitTerminal(request, id);
  await expect(page.locator("#topbarStatus .chip")).not.toContainText("Running", { timeout: 20_000 });
});

test("a reload mid-run rebuilds the running stage from the replay", async ({ page, request }) => {
  const id = await submit(page, "q");
  await expect(page.locator("#runNow")).toHaveText("Researching", { timeout: 10_000 });
  const before = await page.locator("#runElapsed").textContent();
  await page.reload();
  await expect(page.locator("#stage-running")).toBeVisible();
  await expect(page.locator("#stage-submitted")).toHaveCount(0);
  await expect(page.locator('li[data-stage="planner"]')).toHaveAttribute("data-state", "done");
  const after = await page.locator("#runElapsed").textContent();
  expect(after! >= before!).toBe(true); // "MM:SS elapsed" compares lexically
  await waitTerminal(request, id);
});
```

`web/e2e/arcs.spec.ts` (T-E2, T-E3):

```ts
import { expect, test } from "@playwright/test";
import { installTransitionRecorder, submit, transitions, waitTerminal } from "./support";

test("the extra pass lights the amber arc: flowing then settled, tag, pass 2 of 2", async ({ page, request, context }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "missing-target-triggers-one-extra-pass" });
  await installTransitionRecorder(page);
  const id = await submit(page, "q");
  await expect(page.locator("#runLoopTag:not([hidden]) .tag")).toHaveText("extra pass", { timeout: 30_000 });
  await expect(page.locator("#runLoopTag .why")).toHaveText("1 required target had no verified finding");
  await expect(page.locator("#runPasses")).toHaveText("pass 2 of 2");
  await expect(page.locator('li[data-stage="researcher"] .stage-meta').first()).toHaveText("1 missing target only");
  await waitTerminal(request, id);
  const seen = (await transitions(page)).filter((t) => t.loop !== null).map((t) => `${t.loop}/${t.arc}`);
  expect(seen).toContain("flowing/extra_pass");
  expect(seen.indexOf("flowing/extra_pass")).toBeLessThan(seen.indexOf("settled/extra_pass"));
});

test("the redraft lights the grey arc and keeps pass 1 of 2", async ({ page, request, context }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "scoped-redraft-after-a-named-defect" });
  await installTransitionRecorder(page);
  const id = await submit(page, "q");
  await expect(page.locator("#runLoopTag:not([hidden]) .tag")).toHaveText("redraft", { timeout: 30_000 });
  await expect(page.locator("#runLoopTag .why")).toHaveText("Reviewer named 1 material defect");
  await expect(page.locator("#runPasses")).toHaveText("pass 1 of 2");
  await waitTerminal(request, id);
  const seen = (await transitions(page)).filter((t) => t.loop !== null).map((t) => `${t.loop}/${t.arc}`);
  expect(seen).toContain("flowing/redraft");
  expect(seen.indexOf("flowing/redraft")).toBeLessThan(seen.indexOf("settled/redraft"));
});
```

`web/e2e/sidebar.spec.ts` (T-E11):

```ts
import { expect, test } from "@playwright/test";
import { API, submit, waitTerminal } from "./support";

test("the sidebar lists sessions newest first, refreshes while one runs, and states its memory", async ({ page, request }) => {
  const first = await submit(page, "first");
  const second = await submit(page, "second");
  const ids = await page.locator(".sb-item").evaluateAll((els) => els.map((e) => e.getAttribute("data-session")));
  expect(ids.indexOf(second)).toBeLessThan(ids.indexOf(first));
  // a session started elsewhere appears without a reload while one runs
  const posted = await request.post(`${API}/research`, { data: { query: "elsewhere" } });
  const { session_id: third } = await posted.json();
  await expect(page.locator(`.sb-item[data-session="${third}"]`)).toBeVisible({ timeout: 8_000 });
  await expect(page.locator(".sb-foot")).toHaveText("Sessions are held in the service process's memory; this list empties when the service restarts.");
  for (const id of [first, second, third]) await waitTerminal(request, id);
});
```

- [ ] **Step 8: Verification spec — the visual captures, C2 part**

`web/e2e/visual.spec.ts` (Tasks 17–19 append to this file; `VISUAL_CHECKPOINT` names the output folder, default `C4`):

```ts
import { mkdirSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { submit, waitTerminal } from "./support";

const CHECKPOINT = process.env.VISUAL_CHECKPOINT ?? "C4";
const dir = path.join("visual", CHECKPOINT);
mkdirSync(dir, { recursive: true });
const shoot = async (page: Page, name: string) => {
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(dir, `${name}.png`), fullPage: true });
  const width = await page.evaluate(() => [document.scrollingElement!.scrollWidth, window.innerWidth]);
  expect(width[0], `${name}: no page-wide horizontal scroll`).toBeLessThanOrEqual(width[1]);
};
const PHONE = { width: 390, height: 844 };

for (const [suffix, viewport] of [["", null], ["-phone", PHONE]] as const) {
  test.describe(`captures${suffix}`, () => {
    if (viewport) test.use({ viewport });

    test(`01-idle${suffix} / 07-idle-phone`, async ({ page }) => {
      await page.goto("/");
      await shoot(page, suffix ? "07-idle-phone" : "01-idle");
    });

    test(`02-submitted${suffix}, 03-running${suffix}, 09-running-extra-pass${suffix}`, async ({ page, request, context }) => {
      await context.setExtraHTTPHeaders({ "X-Replay-Case": "missing-target-triggers-one-extra-pass" });
      const id = await submit(page, "What is the current state of grid-scale battery storage?");
      await expect(page.locator("#stage-submitted")).toBeVisible();
      await shoot(page, `02-submitted${suffix}`);
      await expect(page.locator("#runNow")).toHaveText("Researching", { timeout: 10_000 });
      await shoot(page, `03-running${suffix}`);
      await expect(page.locator('#spineWrap[data-loop="settled"][data-arc="extra_pass"]')).toBeVisible({ timeout: 30_000 });
      await shoot(page, `09-running-extra-pass${suffix}`);
      await waitTerminal(request, id);
    });
  });
}
```

- [ ] **Step 9: Run the e2e suite**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
cd web && DEEP_RESEARCH_PYTHON="$PY" npm run -s test:e2e 2>&1 | tail -n 16
```
Expected: `8 passed` (the three of Task 15, `running` ×2, `arcs` ×2, `sidebar`); `test:e2e` runs the `chromium` project only, so the `visual` project does not run here.

- [ ] **Step 10: Checkpoint C2 — the running stage at both widths**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
cd web && DEEP_RESEARCH_PYTHON="$PY" VISUAL_CHECKPOINT=C2 npx playwright test --project visual 2>&1 | tail -n 8 && node -e "console.log(require('fs').readdirSync('visual/C2').join(' '))"
```
Expected: `4 passed`; the listing includes `02-submitted.png 02-submitted-phone.png 03-running.png 03-running-phone.png 09-running-extra-pass.png 09-running-extra-pass-phone.png` (and the two idle captures). Review full-height against `docs/design/reference/02-submitted.png`, `03-running.png`, `09-running-extra-pass.png`: the question and strip in the same position on stages 2 and 3; the pipeline card centred at reading width with `Now / Researching`, the blurb, `stage 2 of 7`, `pass 1 of 2`, seven rows, the counters block reading `not yet` (never `0`); on `09-…` the amber arc from Reviewing to Researching, the `extra pass` tag with `1 required target had no verified finding`, `pass 2 of 2`, rows 3–6 hollow. The three phone captures are judged against the design rules (drawer sidebar, 16 px gutters, no horizontal scroll, the spine centred). Attach the images to the task's summary; fix any difference before committing.

- [ ] **Step 11: Commit**

```bash
git add web/components web/test/components web/e2e && git commit -m "feat(web): running stage on the real stream with arcs, counters and reconnect (C2)"
```

---

### Task 17: The report stage and the Evidence view (spec §4.3 Report rendering, The Evidence view, Components; T-W5 part 2; T-E1 report half, T-E4, T-E5, T-E6; Review Focus 4)

**Files:**
- Create: `web/components/ReportStage.tsx`, `web/components/ReportBody.tsx`, `web/components/ReportRail.tsx`, `web/components/EvidenceView.tsx`, `web/test/fixtures/evidence-default.json`
- Modify: `web/components/SessionScreen.tsx` (the terminal branch), `web/app/globals.css` (append the app-only block)
- Test: `web/test/components/report-body.test.tsx`, `web/test/components/report-rail.test.tsx`, `web/test/components/evidence-view.test.tsx`, `web/e2e/report.spec.ts`, `web/e2e/visual.spec.ts` (C3 part: `04-report`, `08-evidence`)

**Interfaces:**
- Consumes: `getReport`, `getEvidence`, `reportUrl`, `evidenceMarkdownUrl` (Task 12); `format.ts`; `run-state.ts`; `react-markdown`, `remark-gfm`.
- Produces: `<ReportStage sessionId status run strip passes />`; `<ReportBody markdown evidenceLoaded onOpenEvidence />` and its exported `remarkReportShape` plugin; `<ReportRail status evidence view />`; `<EvidenceView evidence />` and its exported `evidenceRows(evidence)`, `EV_FILTERS`.

- [ ] **Step 1: The Evidence fixture (the prototype's `EVIDENCE.default`, `index.html:3410-3471`, in E1's shape)**

`web/test/fixtures/evidence-default.json`:

```json
{
  "session_id": "b41e77aa", "iteration": 0,
  "findings": [
    { "label": "F01", "status": "verified", "dropped_reason": null, "context_unchecked": false, "cited": true, "target_ids": ["T01"],
      "content": "2,600 GW of proposed U.S. generation and storage projects were waiting for grid access at the end of 2023.",
      "snippet": "As of the end of 2023, 2,600 GW of proposed generation and storage projects were waiting for grid access, with typical wait times stretching to five years or more.",
      "passage": "The report notes that as of the end of 2023, 2,600 GW of proposed generation and storage projects were waiting for grid access, with typical wait times stretching to five years or more, and that fewer than a quarter of queued projects reach completion.",
      "source": { "url": "https://www.novoco.com/", "title": "Resolving the Interconnection Queue Bottleneck", "organisation": "Novogradac", "evaluation_status": "scored", "low_confidence": false, "authority_score": 0.62, "recency_score": 0.71, "relevance_score": 0.93, "overall_score": 0.74 },
      "figures": [{ "value": "2,600 GW", "kept": true, "period": "end of 2023", "scope": "proposed U.S. generation and storage awaiting grid access", "organisation": "Lawrence Berkeley National Laboratory", "attribution": "relayed", "kind": "actual", "release": "2024 queue study", "evidence_words": "2,600 GW of proposed generation and storage projects", "corrected": false, "dropped_reason": null, "reason": null }] },
    { "label": "F02", "status": "verified_corrected", "dropped_reason": null, "context_unchecked": false, "cited": true, "target_ids": ["T03"],
      "content": "China's average share of mineral supply, excluding rare earths, rose to 72 percent in 2025 from 70 percent in 2023.",
      "snippet": "Excluding rare earths, the average share of mineral supply held by China rose to 72% in 2025 from 70% in 2023.", "passage": null,
      "source": { "url": "https://crsreports.congress.gov/", "title": "Critical Minerals and Materials for Selected Energy Technologies", "organisation": "Congressional Research Service", "evaluation_status": "scored", "low_confidence": false, "authority_score": 0.91, "recency_score": 0.84, "relevance_score": 0.88, "overall_score": 0.89 },
      "figures": [{ "value": "72%", "kept": true, "period": "2025", "scope": "average share of mineral supply held by China, excluding rare earths", "organisation": "Congressional Research Service", "attribution": "own", "kind": "actual", "release": "R48149, 2025", "evidence_words": "rose to 72% in 2025", "corrected": true, "dropped_reason": null, "reason": "the draft read 2024; the page says 2025" }] },
    { "label": "F03", "status": "quoted", "dropped_reason": null, "context_unchecked": false, "cited": true, "target_ids": ["T03"],
      "content": "The IEA estimates that full implementation of China's export controls could put $6.5 trillion in annual production at risk.",
      "snippet": "The IEA estimates that full implementation of China's export controls could put $6.5 trillion in annual production outside China at risk.", "passage": null,
      "source": { "url": "https://www.aa.com.tr/", "title": "Why the IEA says critical minerals have become a $6.5 trillion economic security risk", "organisation": "Anadolu Agency", "evaluation_status": "scored", "low_confidence": false, "authority_score": 0.74, "recency_score": 0.90, "relevance_score": 0.80, "overall_score": 0.80 },
      "figures": [] },
    { "label": "F04", "status": "dropped", "dropped_reason": "snippet_not_on_page", "context_unchecked": false, "cited": false, "target_ids": ["T02"],
      "content": "The U.S. interconnection queue swelled to a 2,600 GW backlog as of 2026 with median waits approaching five years.",
      "snippet": "a 2,600 GW backlog as of 2026 with median waits approaching five years", "passage": null,
      "source": { "url": "https://www.enkiai.com/", "title": "Grid Interconnection Delays 2026: A Threat to US Energy", "organisation": "Enki AI", "evaluation_status": "scored", "low_confidence": true, "authority_score": 0.38, "recency_score": 0.95, "relevance_score": 0.82, "overall_score": 0.67 },
      "figures": [] },
    { "label": "F05", "status": null, "dropped_reason": null, "context_unchecked": false, "cited": false, "target_ids": ["T01"],
      "content": "For every $1 billion in delayed transmission investment, consumers lose between $150 million and $370 million in net benefits per year of delay.",
      "snippet": "For every $1 billion in transmission investments that is delayed, consumers lose between $150 million and $370 million in net benefits per year of delay.", "passage": null,
      "source": { "url": "https://www.enkiai.com/", "title": "Grid Interconnection Delays 2026: A Threat to US Energy", "organisation": "Enki AI", "evaluation_status": "scored", "low_confidence": true, "authority_score": 0.38, "recency_score": 0.95, "relevance_score": 0.82, "overall_score": 0.67 },
      "figures": [] },
    { "label": "F06", "status": "verified", "dropped_reason": null, "context_unchecked": true, "cited": true, "target_ids": ["T03"],
      "content": "China processed 70 to 95 percent of global lithium, cobalt, phosphate and graphite in 2024.",
      "snippet": "China retained its dominance over the midstream and downstream EV and storage battery supply chain in 2024, processing 70-95% of global lithium, cobalt, phosphate and graphite.", "passage": null,
      "source": { "url": "https://www.iea.org/", "title": "Global Critical Minerals Outlook 2025", "organisation": "IEA", "evaluation_status": "scored", "low_confidence": false, "authority_score": 0.95, "recency_score": 0.88, "relevance_score": 0.92, "overall_score": 0.92 },
      "figures": [{ "value": "70–95%", "kept": true, "period": "2024", "scope": "share of global lithium, cobalt, phosphate and graphite processed in China", "organisation": "IEA", "attribution": "own", "kind": "actual", "release": "Global Critical Minerals Outlook 2025", "evidence_words": "processing 70-95% of global lithium, cobalt, phosphate and graphite", "corrected": false, "dropped_reason": null, "reason": null }] }
  ],
  "not_found": [
    { "target_id": "T04", "question": "How do non-U.S. grid connection regimes constrain battery deployment?", "queries": ["EU grid connection queue battery storage 2025", "UK connections reform battery storage wait times"], "pages_read": ["https://www.entsoe.eu/", "https://www.neso.energy/"], "searched": true }
  ],
  "refused": [
    { "where": "Critical minerals and supply-chain concentration", "text": "Export controls have already removed $6.5 trillion of production from world markets.", "reason": "The cited finding gives an estimate of production at risk under full implementation, not a realised loss.", "finding_labels": ["F03"] }
  ]
}
```

(The fixture keeps the prototype's `T0n` ids on purpose — it tests rendering; the engine's ids are `topic-NN-target-NN`, and T-E5 asserts those.)

- [ ] **Step 2: Write the failing component tests**

`web/test/components/report-body.test.tsx`:

```tsx
import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ReportBody } from "../../components/ReportBody";

const MARKDOWN = `# What is the current state of grid-scale battery storage?

Evidence as of 2026-09-16 · 3 sources

## Bottom line

Storage grew fast in 2024 [1][2]. Costs fell [3].

| Option | Energy density | Cycle life | Recommended by |
|---|---|---|---|
| LFP | lower | long | [1] |
| NMC | higher | shorter | [2] |

*Options compared on the parts the plan named; an empty cell reads not stated.*

## Grid connection

- Queues stretch to five years [1].
- Reform is under way [2][3].

## What we couldn't confirm

Nothing on non-U.S. regimes.

## Sources

1. Novogradac — [Resolving the Interconnection Queue Bottleneck](https://www.novoco.com/) (2024)
2. IEA — [Global Critical Minerals Outlook 2025](https://www.iea.org/) (2025)
3. CRS — [Critical Minerals and Materials](https://crsreports.congress.gov/) (2025)

How this was researched: [evidence log](report-abc-0-evidence.md)
`;

describe("ReportBody renders the server's Markdown as-is with the design's adaptations", () => {
  it("does not repeat the H1, mutes the evidence line, anchors [n], frames the options table, ids the sources", () => {
    const onOpen = vi.fn();
    const { container } = render(<ReportBody markdown={MARKDOWN} evidenceLoaded onOpenEvidence={onOpen} />);
    expect(container.querySelector("h1")).toBeNull();
    expect(container.querySelector("p.avail")!.textContent).toBe("Evidence as of 2026-09-16 · 3 sources");
    expect([...container.querySelectorAll("h2")].map((h) => h.textContent)).toEqual(["Bottom line", "Grid connection", "What we couldn't confirm", "Sources"]);
    const anchors = [...container.querySelectorAll('a[href^="#src-"]')].map((a) => [a.getAttribute("href"), a.textContent]);
    expect(anchors.slice(0, 3)).toEqual([["#src-1", "[1]"], ["#src-2", "[2]"], ["#src-3", "[3]"]]);
    expect(container.querySelector("p")!.textContent).not.toContain("[1] [2]"); // a run stays a run: "[1][2]"
    const frame = container.querySelector(".tbl-frame")!;
    expect(frame.hasAttribute("data-pinned")).toBe(true);
    expect(frame.querySelector("table")!.className).toContain("tbl");
    expect(frame.nextElementSibling!.className).toContain("tbl-foot");
    expect([...container.querySelectorAll("ol li")].map((li) => li.id)).toEqual(["src-1", "src-2", "src-3"]);
    for (const a of container.querySelectorAll("ol li a")) { expect(a.getAttribute("target")).toBe("_blank"); expect(a.getAttribute("rel")).toBe("noopener"); }
    const link = container.querySelector('p.avail button.link')!;
    expect(link.textContent).toBe("evidence log");
    fireEvent.click(link);
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(container.textContent).not.toContain("Executive Summary");
  });
  it("shows the evidence-log words as muted text while E1 has not loaded", () => {
    const { container } = render(<ReportBody markdown={MARKDOWN} evidenceLoaded={false} onOpenEvidence={() => {}} />);
    expect(container.querySelector("button.link")).toBeNull();
    expect([...container.querySelectorAll("p.avail")].at(-1)!.textContent).toBe("How this was researched: evidence log");
  });
});
```

`web/test/components/report-rail.test.tsx`:

```tsx
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ReportRail } from "../../components/ReportRail";
import type { ResearchSessionResponse } from "../../lib/api";

const base: ResearchSessionResponse = {
  session_id: "s", query: "q", status: "incomplete", current_agent: null, iteration: 0, started_at: "2026-09-16T14:02:11Z",
  finished_at: "2026-09-16T14:12:00Z", report_path: null, trace_url: null, errors: [], evidence_path: null, quality_path: null,
  quality_contract_version: null, semantic_review_status: "provider_failed", semantic_review_score: null, duration_seconds: null,
  coverage: null, evidence_counts: null,
};

describe("ReportRail", () => {
  it("reads muted words for every absent value and never a zero", () => {
    const { container } = render(<ReportRail status={base} evidence={null} passes={2} />);
    const text = container.textContent!;
    expect(container.querySelector("#repReviewN")!.textContent).toBe("not scored");
    expect(container.querySelector("#repReviewStatus")!.textContent).toBe("review unavailable");
    expect(container.querySelector("#repCitedN")!.textContent).toBe("not measured");
    expect(container.querySelector("#repEvidenceNone")!.hasAttribute("hidden")).toBe(false);
    expect(container.querySelector("#repCovAnswered")!.textContent).toBe("not measured");
    expect(container.querySelector("#repFactPath")!.textContent).toBe("Not published");
    expect(container.querySelector("#repFactDuration")!.textContent).toBe("not recorded");
    expect(text).toContain("Not recorded");
    expect(text).not.toMatch(/NaN/);
    expect(container.querySelector("#repFactPass")!.textContent).toBe("pass 1 of 2");
  });
  it("paints a 0.80 meter yellow and lists not-found targets with their question once E1 loaded", () => {
    const status = { ...base, status: "max_iterations" as const, semantic_review_status: "scored", semantic_review_score: 0.8,
      coverage: { required_targets: 3, answered_targets: 2, missing_required_target_ids: [], not_found_target_ids: ["topic-01-target-01"] },
      evidence_counts: { read_records: 5, network_reads: 4, cache_reads: 1, unique_works: 4, publishers: 3, source_urls: 4, findings: 6, assessed_sources: 5, cited_assessed_sources: 4, verified_findings: 3, corrected_findings: 1, quoted_findings: 1, dropped_findings: 1, context_unchecked_findings: 1, cited_findings: 4 } };
    const evidence = { session_id: "s", iteration: 1, findings: [], refused: [], not_found: [{ target_id: "topic-01-target-01", question: "How much storage is planned for 2025?", queries: [], pages_read: [], searched: true }] };
    const { container } = render(<ReportRail status={status} evidence={evidence} passes={2} />);
    expect(container.querySelector("#repReviewFill")!.className).toContain("warn");
    expect(container.querySelector("#repCitedN")!.textContent).toBe("0.80");
    expect(container.querySelector("#repCovNotFound")!.textContent).toBe("topic-01-target-01 — How much storage is planned for 2025?");
    expect(container.querySelector("#repEvidenceCounts")!.textContent).toContain("4 network · 1 cache");
  });
});
```

`web/test/components/evidence-view.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EvidenceView, evidenceRows } from "../../components/EvidenceView";
import fixture from "../fixtures/evidence-default.json";
import type { EvidenceResponse } from "../../lib/api";

const evidence = fixture as EvidenceResponse;

describe("EvidenceView", () => {
  it("lists every row under All with counted filter chips", () => {
    expect(evidenceRows(evidence).map((r) => r.status)).toEqual(["verified", "verified_corrected", "quoted", "dropped", "not_checked", "verified", "not_found", "refused"]);
    const { container } = render(<EvidenceView evidence={evidence} />);
    expect(container.querySelectorAll(".ev-row").length).toBe(8);
    const chip = (id: string) => container.querySelector(`.chip-filter[data-filter="${id}"] .n`)!.textContent;
    expect([chip("all"), chip("verified"), chip("verified_corrected"), chip("quoted"), chip("dropped"), chip("not_found"), chip("refused")]).toEqual(["8", "2", "1", "1", "1", "1", "1"]);
    expect(container.querySelector('.ev-row[aria-selected="true"] .lbl')!.textContent).toBe("F01");
    expect(container.querySelector(".ev-title")!.textContent).toBe("F01 — Resolving the Interconnection Queue Bottleneck");
  });
  it("shows a not-checked finding only under All", () => {
    const { container } = render(<EvidenceView evidence={evidence} />);
    expect(container.querySelector('.ev-row[data-status="not_checked"]')).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^Verified/ }));
    expect(container.querySelectorAll(".ev-row").length).toBe(2);
    expect(container.querySelector('.ev-row[data-status="not_checked"]')).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^Dropped/ }));
    expect(container.querySelector(".ev-status")!.textContent).toBe("dropped (snippet_not_on_page)");
  });
  it("renders a not-found row and a refused row with their details", () => {
    const { container } = render(<EvidenceView evidence={evidence} />);
    fireEvent.click(screen.getByRole("button", { name: /^Not found/ }));
    expect(container.querySelector(".ev-row .txt")!.textContent).toBe("How do non-U.S. grid connection regimes constrain battery deployment?");
    expect(container.querySelector(".ev-row .tag.soft")!.textContent).toBe("2 queries · 2 pages read");
    fireEvent.click(screen.getByRole("button", { name: /^Refused/ }));
    expect(container.querySelector(".ev-row .tag.soft")!.textContent).toBe("cited F03");
    expect(container.querySelector(".ev-title")!.textContent).toBe("R01 — refused sentence");
  });
});
```

- [ ] **Step 3: Run them to verify they fail**

```bash
cd web && npx vitest run test/components/report-body.test.tsx test/components/report-rail.test.tsx test/components/evidence-view.test.tsx 2>&1 | tail -n 8
```
Expected: all three fail to import.

- [ ] **Step 4: The app-only CSS (the one allowed block)**

Append to `web/app/globals.css` (after line 1131):

```css
/* ═══ 2026-09-27: app-only additions ═══ */
/* The options table pins its Option column over the sideways scroll (states.html:157-159). */
.tbl-frame[data-pinned] .tbl{min-width:760px}
.tbl-frame[data-pinned] th:first-child,.tbl-frame[data-pinned] td:first-child{position:sticky;left:0;background:var(--surface);z-index:1}
/* The evidence-log link in the report is a button styled as text: it switches the view instead of navigating. */
.prose .link{background:none;border:0;padding:0;font:inherit;color:inherit;text-decoration:underline;cursor:pointer}
```

Then `cd web && npm run -s check:css` → `OK`.

- [ ] **Step 5: The report body**

`web/components/ReportBody.tsx`:

```tsx
"use client";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Root, RootContent, Parent, Text, PhrasingContent } from "mdast";

/* remarkReportShape: mark what the design adapts (spec §4.3 Report rendering) with hProperties the
   components below read — the evidence line, the caption after a table, the Sources list ids, the
   evidence-log link line — and turn every "[n]" into a link to #src-n. Hand-written recursion:
   unist-util-visit is not a dependency. */
const textOf = (node: RootContent | PhrasingContent): string =>
  node.type === "text" ? node.value : "children" in node ? (node.children as PhrasingContent[]).map(textOf).join("") : "";
const setProps = (node: { data?: { hProperties?: Record<string, unknown> } }, props: Record<string, unknown>) => {
  node.data = node.data ?? {};
  node.data.hProperties = { ...(node.data.hProperties ?? {}), ...props };
};
function citationAnchors(node: Parent): void {
  const out: PhrasingContent[] = [];
  for (const child of node.children as PhrasingContent[]) {
    if (child.type === "text") {
      const re = /\[(\d+)\]/g;
      let last = 0;
      let m: RegExpExecArray | null;
      while ((m = re.exec(child.value)) !== null) {
        if (m.index > last) out.push({ type: "text", value: child.value.slice(last, m.index) });
        out.push({ type: "link", url: `#src-${m[1]}`, data: { hProperties: { "data-cite": m[1] } }, children: [{ type: "text", value: m[0] }] });
        last = m.index + m[0].length;
      }
      if (last < child.value.length) out.push({ type: "text", value: child.value.slice(last) } as Text);
    } else {
      if (child.type !== "link" && child.type !== "inlineCode" && "children" in child) citationAnchors(child as Parent);
      out.push(child);
    }
  }
  node.children = out;
}
export function remarkReportShape() {
  return (tree: Root) => {
    let inSources = false;
    tree.children.forEach((node, i) => {
      if (node.type === "heading" && node.depth === 2) { inSources = textOf(node) === "Sources"; return; }
      if (node.type === "list" && inSources) {
        inSources = false;
        const start = node.start ?? 1;
        setProps(node, { className: "sources" });
        node.children.forEach((li, k) => setProps(li, { id: `src-${start + k}` }));
        return;
      }
      if (node.type === "paragraph") {
        const t = textOf(node);
        if (/^Evidence as of |^No source could be checked\.$/.test(t)) setProps(node, { className: "avail", "data-role": "evidence-line" });
        else if (t.startsWith("How this was researched:")) setProps(node, { className: "avail", "data-role": "evidence-log-link" });
        else if (node.children.length === 1 && node.children[0].type === "emphasis" && tree.children[i - 1]?.type === "table") setProps(node, { className: "tbl-foot" });
      }
    });
    for (const node of tree.children) if (node.type === "paragraph" || node.type === "list" || node.type === "table") citationAnchors(node as Parent);
  };
}

interface Props { markdown: string; evidenceLoaded: boolean; onOpenEvidence(): void }

export function ReportBody({ markdown, evidenceLoaded, onOpenEvidence }: Props) {
  const components: Components = {
    h1: () => null, // the question is the stage's own <h1>
    p: ({ node, children, ...rest }) => {
      const role = (node?.properties as Record<string, unknown> | undefined)?.["dataRole"] ?? (node?.properties as Record<string, unknown> | undefined)?.["data-role"];
      if (role === "evidence-log-link") {
        return (
          <p className="avail" id="repEvidenceLink">How this was researched: {evidenceLoaded
            ? <button type="button" className="link" onClick={onOpenEvidence}>evidence log</button>
            : <span className="mono">evidence log</span>}</p>
        );
      }
      return <p {...rest}>{children}</p>;
    },
    table: ({ node, children }) => {
      const firstHeader = (() => {
        const thead = node?.children.find((c) => c.type === "element" && c.tagName === "thead");
        const tr = thead && "children" in thead ? thead.children.find((c) => c.type === "element") : undefined;
        const th = tr && "children" in tr ? tr.children.find((c) => c.type === "element") : undefined;
        const text = th && "children" in th ? th.children.map((c) => (c.type === "text" ? c.value : "")).join("") : "";
        return text.trim();
      })();
      return <div className="tbl-frame" {...(firstHeader === "Option" ? { "data-pinned": "" } : {})}><table className="tbl">{children}</table></div>;
    },
    a: ({ href, children }) => (href?.startsWith("#src-") ? <a href={href}>{children}</a> : <a href={href} className="tlink" target="_blank" rel="noopener">{children}</a>),
  };
  return (
    <article className="card stack" style={{ gap: "var(--space-5)" }}>
      <div className="prose">
        <Markdown remarkPlugins={[remarkGfm, remarkReportShape]} components={components}>{markdown}</Markdown>
      </div>
    </article>
  );
}
```

If the installed `react-markdown` names the hast node prop differently or keeps `data-*` properties as `dataRole`, the `p` component above already reads both spellings; if `Components` types reject the `node` destructure, import `ExtraProps` from `react-markdown` and type the handlers as `(props: ComponentProps<"p"> & ExtraProps) => …` (the package's own documented pattern).

- [ ] **Step 6: The rail**

`web/components/ReportRail.tsx` (`index.html:1438-1497`, `populateReport` `:3316-3400`, `renderEvidenceCounts` `:3299-3315`):

```tsx
"use client";
import type { EvidenceResponse, ResearchSessionResponse } from "@/lib/api";
import { fmtScore, meterClass, passText, statusNote, toSessionView } from "@/lib/format";

const ratio = (a: number, b: number) => (Number.isFinite(a) && b > 0 ? a / b : null);
function Meter({ id, label, value, absent, gate }: { id: string; label: string; value: number | null; absent: string; gate?: boolean }) {
  const cls = value === null ? null : meterClass(value);
  return (
    <div className="bar-row"><span style={{ color: "var(--fg)" }}>{label}</span>
      <span className={`bar${gate ? " bar-gate" : ""}`}><span id={`${id}Fill`} className={cls ?? undefined} style={{ width: value === null ? "0%" : `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%` }}></span></span>
      <span className={`n${value === null ? " avail" : ""}`} id={`${id}N`}>{value === null ? absent : fmtScore(value)}</span>
    </div>
  );
}

export function ReportRail({ status, evidence, passes }: { status: ResearchSessionResponse; evidence: EvidenceResponse | null; passes: number | null }) {
  const view = toSessionView(status, passes);
  const ec = status.evidence_counts;
  const cited = ec ? ratio(ec.cited_assessed_sources, ec.assessed_sources) : null;
  const cov = status.coverage;
  const questions = new Map((evidence?.not_found ?? []).map((t) => [t.target_id, t.question]));
  const notFound = cov ? cov.not_found_target_ids.map((id) => (questions.has(id) ? `${id} — ${questions.get(id)}` : id)) : [];
  const recoverable = status.errors.filter((e) => e.recoverable).length;
  return (
    <aside className="rail" aria-label="Report details">
      <div className="card stack-2">
        <h2 className="card-title">Review</h2>
        <div className="bars">
          <Meter id="repReview" label="Review score" value={status.semantic_review_score} absent="not scored" gate />
          <Meter id="repCited" label="Scored sources cited" value={cited} absent="not measured" />
        </div>
        <p className="avail" id="repReviewStatus">{statusNote(view)}</p>
      </div>
      <div className="card stack-2">
        <h2 className="card-title">Coverage</h2>
        <dl className="kv">
          <dt>required targets answered</dt><dd id="repCovAnswered" className={cov ? undefined : "avail"}>{cov ? `${cov.answered_targets} of ${cov.required_targets}` : "not measured"}</dd>
          <dt>not found</dt><dd id="repCovNotFound" className={!cov || !notFound.length ? "avail" : undefined}>{!cov ? "not measured" : notFound.length ? notFound.join(" · ") : "none"}</dd>
        </dl>
      </div>
      <div className="card stack-2">
        <h2 className="card-title">Evidence</h2>
        <dl className="kv" id="repEvidenceCounts" hidden={!ec}>
          {ec ? [["findings", ec.findings], ["verified", ec.verified_findings], ["corrected", ec.corrected_findings], ["quoted", ec.quoted_findings], ["dropped", ec.dropped_findings],
            ["context unchecked", ec.context_unchecked_findings], ["cited", ec.cited_findings], ["reads", `${ec.network_reads} network · ${ec.cache_reads} cache`], ["unique works", ec.unique_works], ["publishers", ec.publishers]]
            .map(([k, v]) => [<dt key={`${k}-k`}>{k}</dt>, <dd key={`${k}-v`}>{String(v)}</dd>]) : null}
        </dl>
        <p className="avail" id="repEvidenceNone" hidden={!!ec}>not measured</p>
      </div>
      <div className="card stack-2">
        <h2 className="card-title">Session facts</h2>
        <dl className="kv">
          <dt>status</dt><dd id="repFactStatus">{status.status}</dd>
          <dt>pass</dt><dd id="repFactPass">{passText(view)}</dd>
          <dt>started_at</dt><dd id="repFactStarted">{status.started_at}</dd>
          <dt>finished_at</dt><dd id="repFactFinished" className={status.finished_at ? undefined : "avail"}>{status.finished_at ?? "not recorded"}</dd>
          <dt>duration_seconds</dt><dd id="repFactDuration" className={status.duration_seconds === null ? "avail" : undefined}>{status.duration_seconds === null ? "not recorded" : String(status.duration_seconds)}</dd>
          <dt>report_path</dt><dd id="repFactPath" className={status.report_path ? undefined : "avail"}>{status.report_path ?? "Not published"}</dd>
          <dt>evidence_path</dt><dd id="repFactEvidence" className={status.evidence_path ? undefined : "avail"}>{status.evidence_path ?? "Not published"}</dd>
          <dt>quality_path</dt><dd id="repFactQuality" className={status.quality_path ? undefined : "avail"}>{status.quality_path ?? "Not published"}</dd>
          <dt>errors</dt><dd id="repFactErrors">{status.errors.length}</dd>
        </dl>
      </div>
      <div className="card stack-2">
        <h2 className="card-title">Cost and usage</h2>
        <dl className="kv">
          <dt>tool calls</dt><dd className="avail">Not recorded</dd>
          <dt>input tokens</dt><dd className="avail">Not recorded</dd>
          <dt>output tokens</dt><dd className="avail">Not recorded</dd>
        </dl>
        <p className="avail">The response carries no token or tool-call totals. The running stage's tool-call counter is a stream-derived, researcher-only figure and is not copied here. Nothing is rendered as <span className="mono">0</span>.</p>
      </div>
      <div className="card stack-2">
        <h2 className="card-title">Errors</h2>
        <details className="disc">
          <summary><span id="repErrSummary">{recoverable} recoverable · {status.errors.length - recoverable} non-recoverable</span></summary>
          <div className="disc-body stack-2" id="repErrList">
            {status.errors.length === 0 ? <span className="avail">No errors recorded.</span> : status.errors.map((e, i) => (
              <div key={i}><span className="avail-mono">{e.error_type}</span>{e.message ? <span className="sm" style={{ display: "block", marginTop: "var(--space-1)" }}>{e.message}</span> : null}</div>
            ))}
          </div>
        </details>
      </div>
    </aside>
  );
}
```

- [ ] **Step 7: The Evidence view**

`web/components/EvidenceView.tsx` (`index.html:3474-3671`):

```tsx
"use client";
import { useEffect, useState, type KeyboardEvent } from "react";
import type { EvidenceFinding, EvidenceNotFound, EvidenceRefused, EvidenceResponse } from "@/lib/api";
import { PILL_TEXT, VERIFICATION_TEXT, fmtScore, meterClass } from "@/lib/format";

export const EV_FILTERS = [
  { id: "all", label: "All" }, { id: "verified", label: "Verified" }, { id: "verified_corrected", label: "Corrected" }, { id: "quoted", label: "Quoted" },
  { id: "dropped", label: "Dropped" }, { id: "not_found", label: "Not found" }, { id: "refused", label: "Refused" },
] as const;
export type EvidenceRow =
  | { kind: "finding"; status: string; id: string; item: EvidenceFinding }
  | { kind: "not_found"; status: "not_found"; id: string; item: EvidenceNotFound }
  | { kind: "refused"; status: "refused"; id: string; item: EvidenceRefused };

export function evidenceRows(data: EvidenceResponse): EvidenceRow[] {
  return [
    ...data.findings.map((f): EvidenceRow => ({ kind: "finding", status: f.status ?? "not_checked", id: f.label, item: f })),
    ...data.not_found.map((t): EvidenceRow => ({ kind: "not_found", status: "not_found", id: t.target_id, item: t })),
    ...data.refused.map((r, i): EvidenceRow => ({ kind: "refused", status: "refused", id: "R" + String(i + 1).padStart(2, "0"), item: r })),
  ];
}
const firstSentence = (s: string | null) => { const m = /^(.*?[.!?])(\s|$)/.exec(s ?? ""); return m ? m[1] : s ?? ""; };
const Pill = ({ status }: { status: string }) => <span className="pill" data-status={status}>{PILL_TEXT[status] ?? status}</span>;
function MeterRow({ label, value }: { label: string; value: number | null }) {
  const cls = value === null ? null : meterClass(value);
  return (
    <div className="bar-row"><span>{label}</span><span className="bar"><span className={cls ?? undefined} style={{ width: typeof value === "number" ? `${Math.round(value * 100)}%` : "0%" }}></span></span>
      <span className={`n${fmtScore(value) === null ? " avail" : ""}`}>{fmtScore(value) ?? "not scored"}</span></div>
  );
}

export function EvidenceView({ evidence }: { evidence: EvidenceResponse }) {
  const rows = evidenceRows(evidence);
  const [filter, setFilter] = useState<string>("all");
  const [selected, setSelected] = useState(0);
  useEffect(() => { setFilter("all"); setSelected(0); }, [evidence]);
  const visible = filter === "all" ? rows : rows.filter((r) => r.status === filter);
  const current = visible[Math.min(selected, Math.max(0, visible.length - 1))] ?? null;
  const selectLabel = (label: string) => { setFilter("all"); const idx = rows.findIndex((r) => r.id === label); setSelected(idx < 0 ? 0 : idx); };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSelected((s) => Math.min(visible.length - 1, s + 1)); }
    if (e.key === "ArrowUp") { e.preventDefault(); setSelected((s) => Math.max(0, s - 1)); }
  };
  return (
    <div className="with-rail evidence-view">
      <div className="stack" style={{ gap: "var(--space-4)" }}>
        <div className="chip-filters" id="evChips" role="group" aria-label="Filter evidence by status">
          {EV_FILTERS.map((f) => {
            const count = f.id === "all" ? rows.length : rows.filter((r) => r.status === f.id).length;
            return <button key={f.id} type="button" className="chip-filter" data-filter={f.id} aria-pressed={filter === f.id} onClick={() => { setFilter(f.id); setSelected(0); }}>{f.label} <span className="n">{count}</span></button>;
          })}
        </div>
        <ol className="ev-list" id="evList" role="listbox" aria-label="Evidence" tabIndex={0} onKeyDown={onKey}>
          {visible.map((r, i) => (
            <li key={`${r.kind}-${r.id}`} className="ev-row" role="option" data-kind={r.kind} data-status={r.status} data-id={r.id} aria-selected={i === selected} onClick={() => setSelected(i)}>
              <Pill status={r.status} /><span className="lbl">{r.id}</span>
              {r.kind === "finding" ? <span className="txt">{firstSentence(r.item.snippet)}</span> : r.kind === "not_found" ? <span className="txt">{r.item.question}</span> : <span className="txt">{r.item.text}</span>}
              <span className="tags">
                {r.kind === "finding" ? <>{r.item.target_ids.map((t) => <span key={t} className="tag">{t}</span>)}{r.item.context_unchecked ? <span className="tag soft">context unchecked</span> : null}</>
                  : r.kind === "not_found" ? <span className="tag soft">{r.item.searched ? `${r.item.queries.length} queries · ${r.item.pages_read.length} pages read` : "not searched in this run"}</span>
                  : <span className="tag soft">{r.item.finding_labels.length ? `cited ${r.item.finding_labels.join(", ")}` : "cited nothing"}</span>}
              </span>
            </li>
          ))}
        </ol>
        <p className="avail" id="evEmpty" hidden={visible.length > 0}>Nothing to show under this filter.</p>
      </div>
      <aside className="rail ev-detail card stack-2" id="evDetail" aria-label="Evidence detail" aria-live="polite">
        {current === null ? <p className="avail">Nothing to show under this filter.</p> : current.kind === "finding" ? <FindingDetail f={current.item} /> : current.kind === "not_found" ? <NotFoundDetail t={current.item} /> : <RefusedDetail id={current.id} r={current.item} onSelectLabel={selectLabel} />}
      </aside>
    </div>
  );
}

function FindingDetail({ f }: { f: EvidenceFinding }) {
  const verb = f.status === "dropped" ? `dropped (${f.dropped_reason})` : VERIFICATION_TEXT[f.status ?? "not_checked"];
  const kept = f.figures.find((x) => x.kept && x.organisation);
  const context = !f.figures.length ? "not run (no figures)" : f.context_unchecked || !kept ? "context unchecked" : [kept.organisation, kept.kind, kept.release].filter(Boolean).join(" · ");
  return (
    <>
      <h2 className="ev-title">{f.label} — {f.source.title || "untitled source"}</h2>
      <p className="ev-status">{verb}{f.context_unchecked ? "; context unchecked" : ""}</p>
      <div className="stack-2"><span className="cap">Snippet</span><blockquote>{f.snippet}</blockquote>{f.passage ? <><span className="cap">Passage</span><blockquote>{f.passage}</blockquote></> : null}</div>
      <p className="sm">Source: <a className="tlink" href={f.source.url} target="_blank" rel="noopener">{f.source.title || f.source.url}</a></p>
      <p className="sm">Context check: {context}</p>
      <div className="bars">
        <MeterRow label="authority" value={f.source.authority_score} /><MeterRow label="recency" value={f.source.recency_score} />
        <MeterRow label="relevance" value={f.source.relevance_score} /><MeterRow label="overall" value={f.source.overall_score} />
      </div>
      {f.figures.length ? <><span className="cap">Figures</span><ul className="ev-figures">{f.figures.map((x, i) => <li key={i}>{x.kept ? `${x.value}: kept · period ${x.period || "not stated"} · scope ${x.scope || "not stated"}${x.corrected ? " · corrected" : ""}` : `${x.value}: dropped (${x.dropped_reason}) ${x.reason || ""}`}</li>)}</ul></> : null}
    </>
  );
}
function NotFoundDetail({ t }: { t: EvidenceNotFound }) {
  return (
    <>
      <h2 className="ev-title">{t.target_id} — not found</h2>
      <p className="ev-status">{t.question}</p>
      {!t.searched ? <p className="avail">not searched in this run</p> : <>
        <span className="cap">Searched</span><ul className="ev-queries">{t.queries.map((q) => <li key={q}>{q}</li>)}</ul>
        <span className="cap">Pages read</span><ul className="ev-pages">{t.pages_read.map((u) => <li key={u}><a className="tlink" href={u} target="_blank" rel="noopener">{u}</a></li>)}</ul>
      </>}
    </>
  );
}
function RefusedDetail({ id, r, onSelectLabel }: { id: string; r: EvidenceRefused; onSelectLabel(label: string): void }) {
  return (
    <>
      <h2 className="ev-title">{id} — refused sentence</h2>
      <blockquote>{r.text}</blockquote>
      <p className="ev-cited sm">{r.finding_labels.length ? "cited " : "cited nothing"}{r.finding_labels.map((l) => <button key={l} type="button" onClick={() => onSelectLabel(l)}>{l}</button>)}</p>
      <dl className="kv"><dt>reason</dt><dd className="plain">{r.reason}</dd><dt>where</dt><dd className="plain">{r.where}</dd></dl>
    </>
  );
}
```

- [ ] **Step 8: The report stage, and wiring it into `SessionScreen`**

`web/components/ReportStage.tsx` (`index.html:1347-1373`, `#segView` `:1353-1356`, `setView` `:3683-3691`):

```tsx
"use client";
import { useEffect, useState, type ReactNode } from "react";
import { ApiError, evidenceMarkdownUrl, getEvidence, getReport, reportUrl, type EvidenceResponse, type ResearchSessionResponse } from "@/lib/api";
import { fmtClock, fmtSeconds, passText, toSessionView } from "@/lib/format";
import type { RunState } from "@/lib/run-state";
import { EvidenceView } from "./EvidenceView";
import { ReportBody } from "./ReportBody";
import { ReportRail } from "./ReportRail";

type Loaded<T> = { kind: "loading" } | { kind: "ready"; value: T } | { kind: "unavailable" };

export function ReportStage({ sessionId, status, strip, passes }: { sessionId: string; status: ResearchSessionResponse; run: RunState; strip: ReactNode; passes: number | null }) {
  const [view, setView] = useState<"report" | "evidence">("report");
  const [report, setReport] = useState<Loaded<string>>({ kind: "loading" });
  const [evidence, setEvidence] = useState<Loaded<EvidenceResponse>>({ kind: "loading" });
  useEffect(() => {
    let live = true;
    const unavailable = (e: unknown) => e instanceof ApiError && e.status === 409;
    getReport(sessionId).then((r) => live && setReport({ kind: "ready", value: r.data })).catch((e) => live && setReport(unavailable(e) ? { kind: "unavailable" } : { kind: "loading" }));
    getEvidence(sessionId).then((r) => live && setEvidence({ kind: "ready", value: r.data })).catch((e) => live && setEvidence(unavailable(e) ? { kind: "unavailable" } : { kind: "loading" }));
    return () => { live = false; };
  }, [sessionId]);
  const sv = toSessionView(status, passes);
  const dur = fmtSeconds(status.duration_seconds);
  const meta = `session ${status.session_id} · finished ${fmtClock(status.finished_at) ?? "not recorded"}${dur ? ` · ${dur}` : ""} · ${passText(sv)}`;
  const evidenceLoaded = evidence.kind === "ready";
  return (
    <section className="stage is-on" id="stage-report" data-view={view} aria-labelledby="report-h">
      <div className="report-head">
        <div className="report-head-bar">
          <p className="cap" id="reportMeta">{meta}</p>
          <div className="seg seg-view" role="group" aria-label="Report view" id="segView">
            <button type="button" data-view="report" aria-pressed={view === "report"} onClick={() => setView("report")}>Report</button>
            <button type="button" data-view="evidence" aria-pressed={view === "evidence"} onClick={() => setView("evidence")}>Evidence</button>
          </div>
          <div className="row wrap">
            {report.kind === "ready" ? <a className="btn btn-primary" href={reportUrl(sessionId)} download={`report-${sessionId}.md`} id="downloadBtn">Download Report</a> : null}
            {evidenceLoaded ? <a className="btn btn-ghost" href={evidenceMarkdownUrl(sessionId)} download={`report-${sessionId}-evidence.md`} id="downloadEvidenceBtn">Download evidence log</a> : null}
            {status.trace_url ? <a className="btn btn-ghost" href={status.trace_url} target="_blank" rel="noopener" id="traceBtn">Open LangSmith trace <span aria-hidden="true">↗</span></a> : null}
          </div>
        </div>
        <h1 className="report-q" id="report-h">{status.query}</h1>
        {strip}
      </div>
      {view === "report" ? (
        <div className="with-rail report-main">
          <div className="stack" style={{ gap: "var(--space-6)" }}>
            {report.kind === "ready" ? <ReportBody markdown={report.value} evidenceLoaded={evidenceLoaded} onOpenEvidence={() => setView("evidence")} />
              : <article className="card"><p className="avail">{report.kind === "unavailable" ? "Not published" : "loading report"}</p></article>}
          </div>
          <ReportRail status={status} evidence={evidenceLoaded ? evidence.value : null} passes={passes} />
        </div>
      ) : evidenceLoaded ? <EvidenceView evidence={evidence.value} />
        : <div className="with-rail"><p className="avail" id="evEmpty">{evidence.kind === "unavailable" ? "Not published" : "loading evidence log"}</p></div>}
    </section>
  );
}
```

In `web/components/SessionScreen.tsx`, import `ReportStage` and replace the last line of the component body

```tsx
  return <FinishedHeader status={status} run={run.current} strip={strip} />;
```

with

```tsx
  if (status.status === "failed") return <FinishedHeader status={status} run={run.current} strip={strip} />; // Task 18: FailedStage
  return <ReportStage sessionId={sessionId} status={status} run={run.current} strip={<SettingsStrip settings={submission?.settings ?? null} ceiling={passes} id="reportOpts" />} passes={passes} />;
```

- [ ] **Step 9: Run the component tests, the type check, the build**

```bash
cd web && npx vitest run test/components 2>&1 | tail -n 6 && npm run -s check:css && npm run -s typecheck && npm run -s build 2>&1 | tail -n 6
```
Expected: `Tests  16 passed`; `OK`; no type errors; a clean build.

- [ ] **Step 10: Verification spec — the report stage end to end**

`web/e2e/report.spec.ts` (T-E1 report half, T-E4, T-E5, T-E6):

```ts
import { expect, test } from "@playwright/test";
import { API, submit, waitTerminal } from "./support";

test("the default case ends on the Report stage with the body, the rail and both downloads", async ({ page, request }) => {
  const id = await submit(page, "q");
  await waitTerminal(request, id);
  await expect(page.locator("#stage-report")).toBeVisible({ timeout: 20_000 });
  await expect(page.locator("#topbarStatus .chip")).toContainText(/^Completed · review accepted · \d\.\d\d/);
  await expect(page.locator("#stage-report .prose p.avail").first()).toContainText(/^Evidence as of|^No source could be checked/);
  await expect(page.locator("#stage-report .prose h2", { hasText: "Bottom line" })).toBeVisible();
  await expect(page.locator("#stage-report .prose h2", { hasText: "Sources" })).toBeVisible();
  await expect(page.locator("#stage-report")).not.toContainText("Executive Summary");
  for (const sel of ["#downloadBtn", "#downloadEvidenceBtn"]) {
    const href = await page.locator(sel).getAttribute("href");
    expect((await request.get(`http://127.0.0.1:3010${href}`)).status()).toBe(200);
  }
  await expect(page.locator("#repFactPass")).toHaveText("pass 2 of 2");
});

test("review unavailable: Partially completed · review unavailable, not scored", async ({ page, request, context }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "review-unavailable" });
  const id = await submit(page, "q");
  await waitTerminal(request, id);
  await expect(page.locator("#topbarStatus .chip")).toHaveText(/Partially completed · review unavailable$/, { timeout: 20_000 });
  await expect(page.locator("#repReviewStatus")).toHaveText("review unavailable");
  await expect(page.locator("#repReviewN")).toHaveText("not scored");
});

test("empty but clean: extra passes used · 3 targets not found, with the engine's ids and questions", async ({ page, request, context }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "empty-but-clean" });
  const id = await submit(page, "q");
  await waitTerminal(request, id);
  await expect(page.locator("#topbarStatus .chip")).toHaveText("Partially completed · extra passes used · 3 targets not found", { timeout: 20_000 });
  await expect(page.locator("#stage-report")).toBeVisible();
  await expect(page.locator("#repCovNotFound")).toContainText("topic-01-target-01 — ");
  await expect(page.locator("#repCovNotFound")).toContainText("topic-03-target-01 — ");
  await expect(page.locator("#stage-report")).not.toContainText("Executive Summary");
});

test("the Evidence view lists the run's findings, filters, and opens from the report's link", async ({ page, request, context }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "extra-pass-finds-nothing" });
  const id = await submit(page, "q");
  await waitTerminal(request, id);
  await expect(page.locator("#stage-report")).toBeVisible({ timeout: 20_000 });
  await page.locator("#segView button[data-view='evidence']").click();
  await expect(page.locator("#stage-report")).toHaveAttribute("data-view", "evidence");
  await expect(page.locator(".chip-filter[data-filter='all'] .n")).not.toHaveText("0");
  await expect(page.locator(".ev-row[aria-selected='true']")).toHaveCount(1);
  await expect(page.locator("#evDetail a.tlink").first()).toBeVisible();
  await page.locator(".chip-filter[data-filter='dropped']").click();
  await expect(page.locator(".ev-row")).toHaveCount(1);
  await expect(page.locator(".ev-row .lbl")).toHaveText("X01");
  await page.locator("#segView button[data-view='report']").click();
  await page.locator("#repEvidenceLink button.link").click();
  await expect(page.locator("#stage-report")).toHaveAttribute("data-view", "evidence");
  const e1 = await (await request.get(`${API}/research/${id}/evidence`)).json();
  expect(e1.findings.filter((f: { status: string | null }) => f.status === "dropped")).toHaveLength(1);
});
```

- [ ] **Step 11: Verification spec — the visual captures, C3 part (report and evidence)**

Append inside the `test.describe` loop of `web/e2e/visual.spec.ts`, after the running test:

```ts
    test(`04-report${suffix}, 08-evidence${suffix}`, async ({ page, request }) => {
      const id = await submit(page, "What is the current state of grid-scale battery storage?");
      await waitTerminal(request, id);
      await expect(page.locator("#stage-report .prose h2").first()).toBeVisible({ timeout: 20_000 });
      await shoot(page, `04-report${suffix}`);
      await page.locator("#segView button[data-view='evidence']").click();
      await expect(page.locator(".ev-row").first()).toBeVisible();
      await shoot(page, `08-evidence${suffix}`);
    });
```

- [ ] **Step 12: Run the e2e suite**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
cd web && DEEP_RESEARCH_PYTHON="$PY" npm run -s test:e2e 2>&1 | tail -n 16
```
Expected: `12 passed`.

- [ ] **Step 13: Commit**

```bash
git add web/app/globals.css web/components web/test web/e2e && git commit -m "feat(web): report stage with the design's Markdown rules, the rail and the Evidence view"
```

---

### Task 18: The failed stage; checkpoint C3 (spec §4.3 `FailedStage`, §4.4 API-level failure; T-W5 part 3; T-E10)

**Files:**
- Create: `web/components/FailedStage.tsx`
- Modify: `web/components/SessionScreen.tsx` (the `failed` branch; delete `FinishedHeader`)
- Test: `web/test/components/failed-stage.test.tsx`, `web/e2e/failed.spec.ts`, `web/e2e/visual.spec.ts` (C3 part: `05-failed`)

**Interfaces:**
- Consumes: `failedMarks`, `AGENT_ORDER` (Task 11), `HALT_HEADLINES`, `fmtClock` (Task 10), `Spine`, `Counters` (Task 16).
- Produces: `<FailedStage status run strip />`.

- [ ] **Step 1: Write the failing component test**

`web/test/components/failed-stage.test.tsx`:

```tsx
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FailedStage } from "../../components/FailedStage";
import type { ResearchError, ResearchSessionResponse } from "../../lib/api";
import { applyEvent, newRunState } from "../../lib/run-state";

const failed = (error: ResearchError): ResearchSessionResponse => ({
  session_id: "s", query: "q", status: "failed", current_agent: null, iteration: 0, started_at: "2026-09-27T10:00:00Z",
  finished_at: "2026-09-27T10:00:04Z", report_path: null, trace_url: null, errors: [error], evidence_path: null, quality_path: null,
  quality_contract_version: null, semantic_review_status: null, semantic_review_score: null, duration_seconds: null, coverage: null, evidence_counts: null,
});

describe("FailedStage", () => {
  it("headlines an API-level configuration failure with its enumerated reason and no download", () => {
    const status = failed({ error_type: "api.research.configuration_error", source: "api", message: "Research service configuration is unavailable.", recoverable: false, timestamp: "", details: { reason: "config_invalid" } });
    const run = newRunState(2);
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
    expect(container.querySelector("#failedType")!.textContent).toBe("Service configuration error");
    expect(container.querySelector("#failedMessage")!.textContent).toBe("Research service configuration is unavailable.");
    expect(container.querySelector("#failFactType")!.textContent).toBe("api.research.configuration_error");
    expect(container.querySelector("#failFactReason")!.textContent).toBe("config_invalid");
    expect(container.querySelector("#failedHaltedAt")!.textContent).toBe("halted before the first stage");
    expect(container.querySelector('#spineFailed li[data-stage="finalize_report"]')!.getAttribute("data-state")).toBe("skipped");
    expect(container.querySelectorAll('#spineFailed li[data-state="done"]').length).toBe(0);
    for (const dd of container.querySelectorAll("#failedCounters dd")) expect(dd.textContent).toBe("not reached");
    expect(container.querySelector("#downloadBtn")).toBeNull();
    expect(container.querySelector("button[disabled], a[aria-disabled]")).toBeNull();
  });
  it("headlines a graph halt in plain words and marks the halting row", () => {
    const status = failed({ error_type: "graph_provider_configuration_error", source: "graph", message: "The model provider is not configured, so the research run stopped.", recoverable: false, timestamp: "", details: { exception_type: "ValidationError" } });
    const run = newRunState(2);
    applyEvent(run, { type: "graph.session.started", metadata: { max_extra_passes: 1 } });
    applyEvent(run, { type: "graph.node.started", metadata: { node: "planner", iteration: 0 } });
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
    expect(container.querySelector("#failedType")!.textContent).toBe("Model provider misconfigured");
    expect(container.querySelector("#failedHaltedAt")!.textContent).toBe("halted at stage 1");
    expect(container.querySelector('#spineFailed li[data-stage="planner"]')!.getAttribute("data-state")).toBe("active");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd web && npx vitest run test/components/failed-stage.test.tsx 2>&1 | tail -n 6
```
Expected: fails to import `../../components/FailedStage`.

- [ ] **Step 3: The failed stage**

`web/components/FailedStage.tsx` (`index.html:1513-1565`, `populateFailed` `:3718-3751`):

```tsx
"use client";
import type { ReactNode } from "react";
import type { ResearchError, ResearchSessionResponse } from "@/lib/api";
import { HALT_HEADLINES, fmtClock } from "@/lib/format";
import { AGENT_ORDER, failedMarks, type RunState } from "@/lib/run-state";
import { Counters } from "./Counters";
import { Spine } from "./Spine";

const API_FAILURE: ResearchError = { error_type: "api.research.failed", source: "api", message: "", recoverable: false, timestamp: "", details: {} };

export function FailedStage({ status, run, strip }: { status: ResearchSessionResponse; run: RunState; strip: ReactNode }) {
  const hard = status.errors.find((e) => !e.recoverable) ?? API_FAILURE;
  const reason = typeof hard.details.reason === "string" ? hard.details.reason : null;
  const exceptionType = typeof hard.details.exception_type === "string" ? hard.details.exception_type : null;
  const haltIndex = run.openNode ? AGENT_ORDER.indexOf(run.openNode) : -1;
  return (
    <section className="stage is-on" id="stage-failed" aria-labelledby="failed-h">
      <div className="report-head">
        <div style={{ minWidth: 0 }}>
          <p className="cap" id="failedMeta">session {status.session_id} · finished {fmtClock(status.finished_at) ?? "not recorded"}</p>
          <h1 className="report-q" id="failed-h">{status.query}</h1>
          {strip}
        </div>
        <div className="row wrap">
          {status.trace_url ? <a className="btn btn-ghost" href={status.trace_url} target="_blank" rel="noopener" id="failedTrace">Open LangSmith trace <span aria-hidden="true">↗</span></a> : null}
        </div>
      </div>
      <div className="with-rail">
        <div className="stack" style={{ gap: "var(--space-5)" }}>
          <div className="note bad">
            {/* The headline is the halting type in plain words; the enumerated type stays in the facts; the API's message is the sentence. */}
            <div className="note-head"><span className="mk">halt</span><span id="failedType">{HALT_HEADLINES[hard.error_type] ?? hard.error_type}</span></div>
            <p id="failedMessage">{hard.message || "The run stopped on a non-recoverable error."}</p>
          </div>
          <div className="card stack" style={{ gap: "var(--space-4)" }}>
            <h2 className="card-title">Why nothing was published</h2>
            <p className="sm">A halted run skips publication: no report, evidence log or quality record was written.</p>
            <dl className="kv">
              <dt>error_type</dt><dd id="failFactType">{hard.error_type}</dd>
              <dt>source</dt><dd id="failFactSource">{hard.source || "graph"}</dd>
              <dt>recoverable</dt><dd id="failFactRecoverable">{String(Boolean(hard.recoverable))}</dd>
              {reason ? <><dt>reason</dt><dd id="failFactReason">{reason}</dd></> : null}
              {exceptionType ? <><dt>exception_type</dt><dd id="failFactException">{exceptionType}</dd></> : null}
              <dt>report_path</dt><dd className="avail">Not published</dd>
              <dt>GET /report</dt><dd>409 report_unavailable</dd>
            </dl>
          </div>
        </div>
        <aside className="rail" aria-label="Run details">
          <div className="card stack" style={{ gap: "var(--space-4)" }}>
            <div className="row-between">
              <h2 className="card-title">Where it stopped</h2>
              <span className="avail-mono" id="failedHaltedAt">{haltIndex >= 0 ? `halted at stage ${haltIndex + 1}` : "halted before the first stage"}</span>
            </div>
            <Spine marks={failedMarks(run, status.status)} run={run} withArcs={false} id="spineFailed" />
          </div>
          <div className="card stack-2">
            <h2 className="card-title">What survived the halt</h2>
            <Counters counters={run.counters} absentText="not reached" pass={run.countersPass} id="failedCounters" />
            <p className="avail">The same counters the running stage keeps, frozen at the halt. A counter whose node never ran reads <span className="mono">not reached</span>: a value never measured, not a zero.</p>
          </div>
        </aside>
      </div>
    </section>
  );
}
```

In `web/components/SessionScreen.tsx`: import `FailedStage`, replace the `failed` branch with

```tsx
  if (status.status === "failed") return <FailedStage status={status} run={run.current} strip={strip} />;
```

and delete the `FinishedHeader` function (nothing renders it any more).

- [ ] **Step 4: Run the component tests, the type check, the build**

```bash
cd web && npx vitest run test/components 2>&1 | tail -n 6 && npm run -s typecheck && npm run -s build 2>&1 | tail -n 6
```
Expected: `Tests  18 passed`; no type errors; a clean build.

- [ ] **Step 5: Verification spec — the failed stage end to end**

`web/e2e/failed.spec.ts` (T-E10 — the unknown-case path is the one API-level failure replay mode can produce):

```ts
import { expect, test } from "@playwright/test";
import { submit, waitTerminal } from "./support";

test("an API-level failure lands on the Failed stage with the plain-words headline and its reason", async ({ page, request, context }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "no-such-case" });
  const id = await submit(page, "typed question");
  await waitTerminal(request, id);
  await expect(page.locator("#stage-failed")).toBeVisible({ timeout: 20_000 });
  await expect(page.locator("#topbarStatus .chip")).toHaveText("Failed · halted");
  await expect(page.locator("#failedType")).toHaveText("Service configuration error");
  await expect(page.locator("#failFactType")).toHaveText("api.research.configuration_error");
  await expect(page.locator("#failFactReason")).toHaveText("config_invalid");
  await expect(page.locator("#failed-h")).toHaveText("typed question");
  await expect(page.locator('#spineFailed li[data-state="done"]')).toHaveCount(0);
  await expect(page.locator('#spineFailed li[data-stage="finalize_report"]')).toHaveAttribute("data-state", "skipped");
  await expect(page.locator("#failedCounters dd").first()).toHaveText("not reached");
  await expect(page.locator("#downloadBtn")).toHaveCount(0);
  await expect(page.locator("#stage-failed button[disabled]")).toHaveCount(0);
});
```

- [ ] **Step 6: Verification spec — the visual captures, C3 part (failed)**

Append inside the `test.describe` loop of `web/e2e/visual.spec.ts`:

```ts
    test(`05-failed${suffix}`, async ({ page, request, context }) => {
      await context.setExtraHTTPHeaders({ "X-Replay-Case": "no-such-case" });
      const id = await submit(page, "What is the current state of grid-scale battery storage?");
      await waitTerminal(request, id);
      await expect(page.locator("#stage-failed")).toBeVisible({ timeout: 20_000 });
      await shoot(page, `05-failed${suffix}`);
    });
```

- [ ] **Step 7: Run the e2e suite**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
cd web && DEEP_RESEARCH_PYTHON="$PY" npm run -s test:e2e 2>&1 | tail -n 16
```
Expected: `13 passed`.

- [ ] **Step 8: Checkpoint C3 — report, evidence, failed at both widths**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
cd web && DEEP_RESEARCH_PYTHON="$PY" VISUAL_CHECKPOINT=C3 npx playwright test --project visual 2>&1 | tail -n 8 && node -e "console.log(require('fs').readdirSync('visual/C3').join(' '))"
```
Expected: `8 passed`; the listing includes `04-report.png 04-report-phone.png 08-evidence.png 08-evidence-phone.png 05-failed.png 05-failed-phone.png` (plus the C1/C2 names). Review full-height against `docs/design/reference/04-report.png`, `08-evidence.png`, `05-failed.png`, and the chips and failed panel against `06-states.png`: the head bar (meta, `Report | Evidence`, `Download Report`, `Download evidence log`), the question, the strip, the body card (muted evidence line, `Bottom line`, the table in its frame with the italic caption, the parts, `Sources` with numbered items, the evidence-log line), the rail (Review with the 0.80 line, Coverage, Evidence, Session facts, Cost and usage `Not recorded`, Errors); the Evidence view's chips with counts, list and detail pane; the failed stage's headline and facts. Three things are **engine truth, not defects** (spec §4.5): a dropped finding is labelled `X01`, not-found targets read `topic-01-target-01`, and the head bar reads `0m 00s` in replay mode. The phone captures are judged against the design rules (tables in scrolling frames, the rail stacked below the column, no horizontal scroll). **This is a controller-viewed checkpoint (R4): attach all six images and stop for the controller's own review before committing.** Fix any difference first.

- [ ] **Step 9: Commit**

```bash
git add web/components web/test/components/failed-stage.test.tsx web/e2e && git commit -m "feat(web): failed stage with plain-words headline, halting row and frozen counters (C3)"
```

---

### Task 19: Layout assertions, the full capture set, and full verification — checkpoint C4 (spec §4.5 T-E12, Visual checkpoints, T-A8; AC9, AC14, AC15, AC18)

**Files:**
- Create: `web/e2e/layout.spec.ts`
- Test: everything

**Interfaces:**
- Consumes: every earlier task.
- Produces: the fourteen full-page captures under `web/visual/C4/`; the verification record for the plan's summary.

- [ ] **Step 1: Verification spec — layout at both widths (T-E12)**

`web/e2e/layout.spec.ts`:

```ts
import { expect, test, type Page } from "@playwright/test";
import { submit, waitTerminal } from "./support";

const px = (page: Page, sel: string, prop: "width" | "height") => page.locator(sel).first().evaluate((el, p) => el.getBoundingClientRect()[p as "width" | "height"], prop);
const noSideScroll = async (page: Page) => expect(await page.evaluate(() => document.scrollingElement!.scrollWidth <= window.innerWidth)).toBe(true);
const cardsUnclipped = async (page: Page) => {
  const clipped = await page.locator(".card").evaluateAll((els) => els.filter((e) => e.scrollHeight > e.clientHeight + 1 && getComputedStyle(e).overflowY !== "auto").length);
  expect(clipped).toBe(0);
};

for (const [label, viewport] of [["1252×853", { width: 1252, height: 853 }], ["390×844", { width: 390, height: 844 }]] as const) {
  test.describe(label, () => {
    // reducedMotion: the .app grid animates its columns over --motion-base (index.html:78-81); the CSS
    // zeroes transitions under prefers-reduced-motion (index.html:997-1000), so widths are final at once.
    test.use({ viewport, reducedMotion: "reduce" });
    const phone = viewport.width === 390;

    test("idle", async ({ page }) => {
      await page.goto("/");
      await noSideScroll(page);
      if (phone) {
        await expect(page.locator("#app")).toHaveAttribute("data-sidebar", "collapsed");
      } else {
        await expect.poll(() => px(page, "#sidebar", "width")).toBe(296);
        expect(await px(page, ".topbar-in", "height")).toBeGreaterThanOrEqual(56);
        await page.locator("#sidebarToggle").click();
        await expect.poll(() => px(page, "#sidebar", "width")).toBe(64);
        await page.locator("#sidebarToggle").click();
        await expect.poll(() => px(page, "#sidebar", "width")).toBe(296);
      }
    });

    test("running, report, evidence, failed, not found", async ({ page, request, context }) => {
      await context.setExtraHTTPHeaders({ "X-Replay-Case": "extra-pass-finds-nothing" });
      const id = await submit(page, "q");
      await expect(page.locator("#stage-running")).toBeVisible({ timeout: 5_000 });
      await noSideScroll(page);
      await expect(page.locator("#spine li[data-stage]")).toHaveCount(7);
      expect(await page.locator("#spine li[data-stage]").evaluateAll((els) => els.map((e) => e.getAttribute("data-stage")))).toEqual(["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]);
      await cardsUnclipped(page);
      await waitTerminal(request, id);
      await expect(page.locator("#stage-report .prose h2").first()).toBeVisible({ timeout: 20_000 });
      await noSideScroll(page);
      await cardsUnclipped(page);
      if (!phone) {
        expect(await px(page, ".prose", "width")).toBeLessThanOrEqual(720);
        expect(await px(page, ".rail", "width")).toBe(300);
      }
      await page.locator("#segView button[data-view='evidence']").click();
      await expect(page.locator(".ev-row").first()).toBeVisible();
      await noSideScroll(page);
      await page.goto("/research/does-not-exist");
      await expect(page.getByRole("button", { name: "New research" })).toBeVisible();
      await noSideScroll(page);
      await context.setExtraHTTPHeaders({ "X-Replay-Case": "no-such-case" });
      const failedId = await submit(page, "q");
      await waitTerminal(request, failedId);
      await expect(page.locator("#stage-failed")).toBeVisible({ timeout: 20_000 });
      await noSideScroll(page);
      await cardsUnclipped(page);
    });
  });
}
```

- [ ] **Step 2: Run the whole e2e suite**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
cd web && DEEP_RESEARCH_PYTHON="$PY" npm run -s test:e2e 2>&1 | tail -n 20
```
Expected: `17 passed`. A failing layout assertion is a real defect in the port: fix the component (never the assertion) and rerun.

- [ ] **Step 3: The full capture set — checkpoint C4**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
cd web && DEEP_RESEARCH_PYTHON="$PY" VISUAL_CHECKPOINT=C4 npx playwright test --project visual 2>&1 | tail -n 8 && node -e "const f=require('fs').readdirSync('visual/C4').sort();console.log(f.length, f.join(' '))"
```
Expected: `8 passed` and `14 01-idle.png 02-submitted-phone.png 02-submitted.png 03-running-phone.png 03-running.png 04-report-phone.png 04-report.png 05-failed-phone.png 05-failed.png 07-idle-phone.png 08-evidence-phone.png 08-evidence.png 09-running-extra-pass-phone.png 09-running-extra-pass.png`. Review every capture full-height against its reference (`01`, `02`, `03`, `04`, `05`, `07`, `08`, `09`; chips and the failed panel against `06-states.png`) and the five phone-only captures against the design rules. **Controller-viewed checkpoint (R4): attach all fourteen images and stop for the controller's own review.** Fix any difference and recapture before going on.

- [ ] **Step 4: The whole pytest suite (T-A8, AC9)**

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q 2>&1 | tail -n 5
```
Expected: `… passed` with `0 failed` (the brief counted 4,689 before this branch; the count now includes the new API tests). Record the exact count.

- [ ] **Step 5: The whole Vitest suite, the CSS check, the type check**

```bash
cd web && npx vitest run 2>&1 | tail -n 6 && npm run -s check:css && npm run -s typecheck && echo TYPES-OK
```
Expected: `Tests  56 passed` (11 format + 11 run-state + 7 stream + 5 api + 4 proxy + 18 components), `OK`, `TYPES-OK`.

- [ ] **Step 6: The scope checks (AC5, AC10, AC19, AC20)**

```bash
git diff --stat 4e10823 -- src/deep_research/agents src/deep_research/runtime src/deep_research/graph src/deep_research/utils && echo "(engine untouched when the line above is empty)"
git diff --stat 4e10823 -- docs/design/
cd web && node -e "const p=require('./package.json');console.log(Object.keys(p.dependencies).sort().join(' '));console.log(Object.keys(p.devDependencies).sort().join(' '))"
```
Expected: the first command prints only the echo line; the second prints nothing yet (Task 20 edits `api-gaps.md` and `DESIGN.md`); the third prints the two dependency lines of Task 9 step 2.

- [ ] **Step 7: Commit**

```bash
git add web/e2e/layout.spec.ts && git commit -m "test(web): layout assertions at both widths; full capture set (C4)"
```

---

### Task 20: Docs — README "Run the app", api-gaps closures, the DESIGN.md note, `web/README.md` (spec §4.5 Docs; AC20)

**Files:**
- Modify: `README.md:755-846` (the *FastAPI Interface* and *UI* sections), `docs/design/api-gaps.md:19-38` (Existing surface), `:55-66` (Closed since 2026-09-16), `:69-104` (E1), `:111-112` (1.1, 1.2), `docs/design/DESIGN.md:1-27` (header)
- Create: `web/README.md`

**Interfaces:** none (documentation).

- [ ] **Step 1: The failing check**

```bash
node -e "
const fs=require('fs');
const r=fs.readFileSync('README.md','utf8'), g=fs.readFileSync('docs/design/api-gaps.md','utf8'), d=fs.readFileSync('docs/design/DESIGN.md','utf8');
const checks={runTheApp:/^## Run the app$/m.test(r), routes:r.includes('/research/{session_id}/evidence')&&r.includes('| `GET` | `/research` |'), noE1Section:!/^## E1 /m.test(g), closedRows:['| E1 |','| 1.1 |','| 1.2 |','iteration'].every(k=>g.split('## Closed since 2026-09-16')[1]?.split('---')[0]?.includes(k)), designNote:d.includes('Implemented by the Next.js app in `web/` (2026-09-27)'), webReadme:fs.existsSync('web/README.md')};
console.log(JSON.stringify(checks)); process.exit(Object.values(checks).every(Boolean)?0:1)"
```
Expected now: every value `false`, exit 1.

- [ ] **Step 2: `README.md` — the API table, `query`, the header, and a new section**

In the *FastAPI Interface* section (`README.md:757-835`): replace the routes table with

```markdown
| Method | Path | Response |
| --- | --- | --- |
| `POST` | `/research` | `202` `ResearchSessionResponse` |
| `GET` | `/research` | `200` `{"sessions": [ResearchSessionResponse, …]}`, newest first (`?limit=`, default 20, 1–200) |
| `GET` | `/research/{session_id}/status` | `200` `ResearchSessionResponse` |
| `GET` | `/research/{session_id}/stream` | `200` `text/event-stream` |
| `GET` | `/research/{session_id}/report` | `200` `text/markdown` |
| `GET` | `/research/{session_id}/evidence` | `200` JSON (the findings, their verification and sources, the not-found targets, the refused sentences); `?format=markdown` → the evidence log as `text/markdown` |
| `GET` | `/research/{session_id}/trace` | `200` `TraceResponse` |
```

In the paragraph beginning `The \`202\` response carries the session snapshot:` insert `query` (the stripped question) as the second field after `session_id`. In the errors table, extend the `409` row: `…or from a session that finished without a report (\`report_unavailable\`) or without an evidence log (\`evidence_unavailable\`, on \`/evidence\`)`. After the errors table add:

```markdown
Every response carries `X-Deep-Research-Mode: live` or `replay` (see *Run the app*).
```

Replace the *UI* section (`README.md:837-846`) with (a four-backtick fence here, because the snippet contains its own code block):

````markdown
## Run the app

The console is a Next.js app in `web/` that talks to the API through a same-origin
proxy (`/api/*` → `DEEP_RESEARCH_API_URL`, default `http://127.0.0.1:8000`). Two
processes:

```bash
# 1. the API — live (needs the secrets of the matrix above) …
python -m deep_research.api --mode live --host 127.0.0.1 --port 8000
#    … or replay: the real graph on scripted offline cases, no network, no keys
python -m deep_research.api --mode replay --replay-case missing-target-triggers-one-extra-pass --replay-delay-ms 150

# 2. the app, in web/ (once: npm install)
npm run dev            # http://localhost:3000; DEEP_RESEARCH_API_URL overrides the API origin
```

Replay mode wraps the whole server in the e2e harness's `offline_credentials()` and
`network_denied()`: every provider call is scripted and every socket connect is refused, so
a session costs nothing and finishes in seconds (`--replay-delay-ms` paces the stream so the
running stage can be watched). A `POST /research` may name the case with the header
`X-Replay-Case: <case id>` (the ids of `e2e_evaluation/replay_matrix.py`); the session
records the case's own question. In replay mode the topbar shows a muted `replay mode` chip.
Sessions are held in the API process's memory: the sidebar's list empties when the API
restarts.

Tests: `pytest` for the API; in `web/`, `npm test` (Vitest), `npm run test:e2e` (Playwright
against the API in replay mode; set `DEEP_RESEARCH_PYTHON` to the venv interpreter inside a
worktree), `npm run capture:visual` (full-page captures at 1252 and 390 px into `web/visual/`).

Live-provider smoke tests are opt-in and require separate authorization at
execution time. They are not part of the default repository test runs.
````

- [ ] **Step 3: `docs/design/api-gaps.md`**

In *Existing surface, for reference* (`:19-38`): add rows for `GET /research` (`200 {"sessions": […]}`, newest first, `api/app.py`) and `GET /research/{id}/evidence` (`200` JSON or `text/markdown`; `409 evidence_unavailable`); change "17 fields" to "18 fields" and add `query` after `session_id` in the field list; remove `query` from the "Not on the response" sentence; add the sentence `Every response carries \`X-Deep-Research-Mode: live|replay\`.`

In *Closed since 2026-09-16* (`:55-66`) append four rows:

```markdown
| E1 | `GET /research/{id}/evidence` | served since 2026-09-27: JSON in the shape recorded here (built from `ReportComposition` by `api/evidence.py`, reusing the evidence log's own label pairing and figure text); `?format=markdown` returns `state.report_evidence` as `text/markdown`; `409 session_not_complete` while running, `409 evidence_unavailable` when nothing was composed |
| 1.1 | `query` echo | `query` is on every `ResearchSessionResponse` (`api/models.py`) |
| 1.2 | `GET /research` | the session list, newest first, `?limit=` 1–200 (default 20); process-local memory, as SB.2 records |
| — | `/status.iteration` store fix | `ResearchSession.publish` copies `iteration` from `graph.*` events only (`api/sessions.py`), so `researcher.tool_call`'s ReAct step index never moves the pass |
```

Delete the *E1* section (`:69-104`, the heading through its field-group table and the `---` after it) — the JSON shape now lives in the closed row's reference to `api/evidence.py` and in `docs/superpowers/specs/2026-09-27-frontend-backend-integration-design.md` §4.2 A3. Delete rows 1.1 and 1.2 from the *Stage 1 — Idle* table (`:111-112`); 1.3–1.5 stay. Update the sentence at `:12-15` so it no longer says E1 is "listed first" ("…keyed `{stage}.{n}` plus `SB.{n}` for the sidebar; E1, 1.1 and 1.2 are closed and recorded above.").

- [ ] **Step 4: `docs/design/DESIGN.md`**

After the header's revision note (the italic *Updated 2026-09-26 …* paragraph in `:1-27`), add one line:

```markdown
*Implemented by the Next.js app in `web/` (2026-09-27); this package remains the reference.*
```

- [ ] **Step 5: `web/README.md`**

```markdown
# Deep Research console (`web/`)

The Next.js 16 console for the FastAPI service. It renders `docs/design/` from the live
API through a same-origin streaming proxy (`app/api/[...path]/route.ts`).

## Run

1. API: from the repository root, `python -m deep_research.api --mode replay` (free, offline)
   or `--mode live` (needs the configured secrets). Default `127.0.0.1:8000`.
2. App: `npm install` once, then `npm run dev` → http://localhost:3000.
   `DEEP_RESEARCH_API_URL` overrides the API origin (default `http://127.0.0.1:8000`).

## Test

- `npm test` — Vitest: the event core on real replay captures, the display helpers, the
  SSE reader, the client, the proxy, the components.
- `npm run test:e2e` — `next build` then Playwright against the API in replay mode
  (three servers: the API on 8010, the app on 3010, an app on 3011 pointed at a closed
  port). Inside a `.worktrees/*` tree set `DEEP_RESEARCH_PYTHON` to the venv interpreter
  (`…/deep-research/.venv/Scripts/python.exe`); `npx playwright install chromium` once.
- `npm run capture:visual` — the fourteen full-page captures (7 stages/views × 1252 and
  390 px) into `visual/<VISUAL_CHECKPOINT>/` (default `C4`).
- `npm run capture:events -- <case-id>` — records a replay session's frames into
  `test/fixtures/events/` (needs the API in replay mode with `--replay-delay-ms 0` at
  `DEEP_RESEARCH_API_URL`, default `http://127.0.0.1:8010`).
- `npm run check:css` — `app/globals.css` begins with the prototype's CSS, verbatim.

## Notes

- Replay mode's `duration_seconds` is the unpaced span (about 0.2 s), so the report head
  bar reads `0m 00s` there; a dropped finding is labelled `X01`; not-found targets read
  `topic-01-target-01`. All three are the engine's own values.
- The tree lives under OneDrive. If `npm install` or `next build` fails with `EPERM`/`EBUSY`,
  pause syncing for the command; if it persists, junction `node_modules` and `.next` to a
  directory outside OneDrive (`mklink /J`).
```

- [ ] **Step 6: Re-run the check, then the scope diff**

```bash
node -e "
const fs=require('fs');
const r=fs.readFileSync('README.md','utf8'), g=fs.readFileSync('docs/design/api-gaps.md','utf8'), d=fs.readFileSync('docs/design/DESIGN.md','utf8');
const checks={runTheApp:/^## Run the app$/m.test(r), routes:r.includes('/research/{session_id}/evidence')&&r.includes('| `GET` | `/research` |'), noE1Section:!/^## E1 /m.test(g), closedRows:['| E1 |','| 1.1 |','| 1.2 |','iteration'].every(k=>g.split('## Closed since 2026-09-16')[1]?.split('---')[0]?.includes(k)), designNote:d.includes('Implemented by the Next.js app in `web/` (2026-09-27)'), webReadme:fs.existsSync('web/README.md')};
console.log(JSON.stringify(checks)); process.exit(Object.values(checks).every(Boolean)?0:1)" && git diff --stat 4e10823 -- docs/design/
```
Expected: every value `true`, exit 0; the diff lists exactly `docs/design/DESIGN.md` and `docs/design/api-gaps.md`.

- [ ] **Step 7: Commit**

```bash
git add README.md docs/design/api-gaps.md docs/design/DESIGN.md web/README.md && git commit -m "docs: run the app; close E1, query, session list and the iteration fix in api-gaps"
```

---

### Task 21: The live run (spec §4.5 The live run; AC21) — only after Tasks 1–20 are green and C4 passed

**Files:** none in the tree; the record goes into the plan's implementation summary (and the PR description).

**Interfaces:** none.

- [ ] **Step 1: Check the clock against the DeepSeek peak windows**

```bash
date -u
```
Peak is **01:00–04:00 and 06:00–10:00 UTC, Monday–Friday** (excluding Chinese public holidays). A run takes 70–110 minutes and must start **at least 90 minutes before the next window opens**. Decide from this table (UTC, weekdays):

| Now (UTC) | Start allowed? |
|---|---|
| 04:00–04:30 | yes (short gap; a run that reaches 06:00 is allowed to finish — never stop it) |
| 10:00–23:30 | yes |
| 23:30–04:00 | no (the 01:00 window, or inside it) |
| 04:30–10:00 | no (inside or within 90 min of the 06:00 window) |
| Saturday, Sunday | yes, except a Sunday start after 23:10 UTC (the run must end before Monday 01:00) |

If the answer is **no**, report the next allowed start and stop here — do not start.

- [ ] **Step 2: Ask the human, through the controller, to confirm the time**

Report: the current UTC time, the window it falls in, the expected end time (start + 110 min), and the exact commands below. The key mechanism is decided (the human's ruling of 2026-09-28: a file copy of `.env` into the worktree, below) — this confirmation covers the **start time only**. **Start only after the human has confirmed.** Never start a second run; never stop a run that has started (a killed run publishes nothing and loses its spend).

- [ ] **Step 3: Start the two processes in live mode**

The worktree has no `.env` (spec §4.5 "Secrets", the human's ruling): the main checkout's `.env` is **copied as a file** into the worktree root immediately before the API starts — a `copyFileSync`, so nobody reads, prints or greps its contents; `.env` is gitignored (`.gitignore:151`). The live API then starts **from the worktree root** with this branch's `config.yaml` (`load_dotenv` reads `.env` from the config file's own directory, `utils/config.py:728`; the main checkout's `config.yaml` is never used — it belongs to an older `main` and this branch's loader rejects it). The agent harness's own environment already carries key variables, and `load_dotenv(override=False)` never replaces a variable that is set — so the `env -u …` prefix (Git's `env.exe`) removes `DEEPSEEK_API_KEY`, `TAVILY_API_KEY`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` and `OPENAI_API_KEY` from both the pre-flight and the API's environment, and the copied `.env` is the only key source (the reviewer verified: with the prefix the pre-flight reports `config ok` with the copy and `missing_secrets` without it). A pre-flight strict load under that prefix proves the copied keys are in place before anything is spent. Both processes go through the launcher of Task 14 so they outlive this call and are stopped verifiably in step 5:

```bash
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
MAIN="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research"
node -e "require('fs').copyFileSync(process.argv[1], '.env'); console.log('.env copied (not read)')" "$MAIN/.env"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 env -u DEEPSEEK_API_KEY -u TAVILY_API_KEY -u LANGSMITH_API_KEY -u LANGSMITH_PROJECT -u OPENAI_API_KEY "$PY" -c "from deep_research.main import load_settings; s = load_settings('config.yaml'); print('config ok:', s.llm.provider, s.llm.model, 'max_extra_passes', s.graph.max_extra_passes)"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 env -u DEEPSEEK_API_KEY -u TAVILY_API_KEY -u LANGSMITH_API_KEY -u LANGSMITH_PROJECT -u OPENAI_API_KEY node web/scripts/launch.mjs start api 8000 /research "\"$PY\" -m deep_research.api --mode live --port 8000"
cd web && npm run -s build >/dev/null && DEEP_RESEARCH_API_URL=http://127.0.0.1:8000 node scripts/launch.mjs start app 3000 / "npm run -s start -- --port 3000"
```
Expected: `.env copied (not read)`; `config ok: deepseek deepseek-flash max_extra_passes 1` — with the harness's keys removed by `env -u`, this line can only come from the copied `.env`, so it proves the copy holds the keys strict mode needs; a `ResearchConfigurationError` (`missing_secrets` or `config_invalid`) here means the copy or the config is wrong — fix it before starting anything; two `up:` lines. Then confirm `node -e "fetch('http://127.0.0.1:3000/').then(r=>console.log(r.status))"` → `200`, and that the topbar shows **no** `replay mode` chip (open http://127.0.0.1:3000 in Chrome). The run's `output/` and `memory/` land under the worktree, where both are gitignored (`.gitignore:221-222`).

- [ ] **Step 4: Submit one question through the composer and watch it**

In the browser: type the question the human chose (a real research question; the six starters are suitable), keep the default settings (`deepseek-flash`, thinking enabled, extra passes 1, `output/`), press send. Record the session id from the URL and the UTC start time. Every few minutes note: the active row, the counters (they move once per node step), any reconnect (the browser's network tab shows a new `/api/research/<id>/stream` request; the running stage must look identical after it), and the chip's pass. Do not reload unless testing the reload path is wanted — a reload is safe (it rebuilds from the replay) but changes what is being observed.

- [ ] **Step 5: When the run ends, verify and capture**

- The Report stage: the chip note (`Completed · review accepted · {score}` or one of the partial notes), the body, the rail (real `duration_seconds`, `report_path`, `evidence_path`, `quality_path`), `Download Report` and `Download evidence log` answering 200, the Evidence view with the run's findings.
- Capture, full-page, at both widths:

```bash
cd web && SESSION="<session id>" && URL="http://127.0.0.1:3000/research/$SESSION" && \
node scripts/capture-stage.mjs LIVE 04-report "$URL" && node scripts/capture-stage.mjs LIVE 04-report-phone "$URL" --phone && \
node scripts/capture-stage.mjs LIVE 08-evidence "$URL" --view evidence && node scripts/capture-stage.mjs LIVE 08-evidence-phone "$URL" --phone --view evidence
```
(`--view evidence` clicks the `Report | Evidence` toggle after the report stage has rendered, then captures.)

- Stop both processes, verifiably, and remove the `.env` copy:

```bash
cd web && node scripts/launch.mjs stop app 3000 && cd .. && node web/scripts/launch.mjs stop api 8000
node -e "const fs=require('fs'); if (fs.existsSync('.env')) fs.unlinkSync('.env'); console.log(fs.existsSync('.env') ? 'FAILED: .env still present' : '.env copy removed')"
```
Expected: two `stopped: port … refuses connections` lines and `.env copy removed`. A `FAILED:` line from either command is a blocker: find the port's owner as the launcher's message says, or delete the file, and rerun the check before recording anything.

- [ ] **Step 6: Record**

In the plan's implementation summary (and the PR description): the session id, UTC start and end, the chip note, `duration_seconds`, the number of reconnects observed, whether the screen after any reconnect was identical, and the four capture paths. `web/visual/` is gitignored: attach the four images to the summary rather than committing them.

---

## Acceptance-criteria coverage

| AC | Satisfied by | Proven by |
|---|---|---|
| AC1 flags, exit codes, `timeout_graceful_shutdown`, uvicorn declared | Task 8 | `test_main.py` T-A1a; step 6 `--help` |
| AC2 replay end to end with no keys, streaming first frame, zero connects | Tasks 7, 8 | `test_main.py` T-A1b; `test_replay.py` T-A2a |
| AC3 `X-Replay-Case` picks the outcome; unknown → `config_invalid`; ignored in live mode; `runtime/` untouched | Task 7 | `test_replay.py` T-A2c; Task 5 step 7 / Task 19 step 6 diff |
| AC4 pacing in aggregate, tail before the fold, cancellation, two at once | Task 7 | `test_replay.py` T-A2b, T-A2d |
| AC5 E1 key sets, labels = log headings, cited sum on two cases, `X01`, verbatim whitespace, markdown, codes; private-helper imports; empty engine diff | Tasks 5, 6 | `test_evidence.py` T-A3a, T-A3b; Task 19 step 6 |
| AC6 `query` everywhere; list shape, order, bounds | Tasks 2, 3 | `test_app.py` T-A4, T-A5; `test_sessions.py` |
| AC7 iteration only from graph events | Task 1 | `test_sessions.py` T-A6 |
| AC8 mode header on every response | Task 4 | `test_app.py` T-A7 |
| AC9 whole suite green; only the five `query=` edits to old tests | Tasks 1–8 | Task 19 step 4 |
| AC10 CSS verbatim + app-only block, Node check | Tasks 9, 17 | `npm run check:css` (Task 19 step 5) |
| AC11 `STAGES`, sixteen handlers, seven counter rows; T-W1 on real captures | Task 11 | `run-state.test.ts` |
| AC12 six chip notes, `fmtScore`, `meterClass(0.8)` | Task 10 | `format.test.ts` |
| AC13 proxy streams, header sets, 502 body, signal forwarded; `compress:false`, no rewrites | Tasks 9, 13 | `proxy.test.ts` T-W4 |
| AC14 T-E1…T-E13 pass | Tasks 15–19 | Task 19 step 2 (`17 passed`) |
| AC15 layout at both widths | Task 19 | `layout.spec.ts` T-E12 |
| AC16 the S4 texts verbatim; no `0`/`—`/`null`/disabled stand-in | Tasks 14–18 | `api-down`, `not-found`, `failed` specs; `counters`, `report-rail`, `failed-stage` tests |
| AC17 replay chip only in replay mode | Task 14 | `replay-chip.spec.ts` T-E13; the live run shows none (Task 21 step 3) |
| AC18 fourteen captures at both widths for C4; controller viewing at C3 and C4 | Tasks 16–19 | Task 19 step 3; Task 18 step 8 |
| AC19 exact dependency sets | Task 9 | Task 9 step 2; Task 19 step 6 |
| AC20 README, api-gaps, DESIGN.md; nothing else under `docs/design/` | Task 20 | Task 20 step 6 |
| AC21 one live run recorded, off-peak, confirmed | Task 21 | the summary record |
| AC22 whole-branch review, merge commit | the controller after Task 21 | PR |

## Self-review

**1. Spec coverage.** §4.1 (processes, proxy contract, pages, env) → Tasks 8, 13, 15, 9. §4.2 A1 → Task 8; A2 → Task 7; A3 → Tasks 5–6; A4 → Task 2; A5 → Task 3; A6 → Task 1; the mode header → Task 4; the Python files table → Tasks 1–8 (no `agents/` file, R2). §4.3 files → Tasks 9–18 (`SessionScreen.tsx` is the one file the spec's list calls `research/[id]/page.tsx`'s client half; both exist); data flow steps 1–8 → Tasks 14 (submit, sidebar, mode chip), 16 (stream, reconnect, chip), 17 (finished); the stage table → Tasks 15–18; the strip's "not recorded" rule → Task 16; the replay-duration note → Task 17's `fmtSeconds` and Task 18's C3 note; `lib/*` → Tasks 10–12; components ↔ regions → Tasks 14–18; report rendering rules → Task 17; the Evidence view → Task 17. §4.4 → Tasks 14 (422, 500, unreachable), 15 (404), 16 (drop/reconnect, service stopped), 17 (409 → Not published, absent values), 18 (API-level failure). §4.5 test matrix → every T-id appears in a task; Playwright setup → Task 15; visual checkpoints → Tasks 14, 16, 18, 19; the live run → Task 21; docs → Task 20. §5 → the table above.

**2. Placeholder scan.** No "TBD", "TODO", "similar to Task N" or "add validation"; every code step shows the code, every check its command and expected output. The two places the plan defers to the installed package are explicit and bounded: the Vite 8 transformer option name (Task 9 step 3, with the file to read) and `react-markdown`'s `node` prop typing (Task 17 step 5, with the documented fallback).

**3. Type consistency.** `ReplayRunner(default_case, delay, root)` (Task 7) is constructed the same way in Task 8; `build_app(args, *, replay_root)` and `main(argv, *, serve)` match their tests; `EvidenceResponse` and the six models (Task 5) are what Task 6's route dumps and Task 10's TS types mirror; `useConsole()`'s fields (Task 14) are the ones Tasks 15–17 read (`noteMode`, `noteUnreachable`, `clearUnreachable`, `setChip`, `refreshSessions`, `sidebar`, `setSidebar`, `unreachable`); `Spine({ marks, run, withArcs, id })` and `Counters({ counters, absentText, pass, id })` (Task 16) are called with those props in Tasks 16 and 18; `ReportStage`'s props (Task 17) match the call in `SessionScreen`; `evidenceRows`/`EV_FILTERS` (Task 17) match their tests; `installTransitionRecorder`/`transitions`/`submit`/`waitTerminal`/`deadPort` (Task 15) are the helpers Tasks 16–19 import.

**4. Review Focus.** All five are pinned: (1) Task 12 step 1 (`api.test.ts`: one fetch per `startResearch`) and Task 15 step 2 (`api-down.spec.ts`: one POST per click); (2) Task 7 step 1 (`test_two_replay_sessions_at_once_both_finish`); (3) Task 16 step 7 (`running.spec.ts` reload) and Task 11 step 3 (T-W1(a)); (4) Task 17 step 2 (`report-rail.test.tsx` nulls) and step 10 (`review-unavailable`); (5) Task 5 step 2 (`test_a_finding_without_a_scored_source_serialises_with_null_scores`, dropped-figure fields).

**5. Environment.** Every Python command uses the venv interpreter by absolute path with `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1`; every check is pytest, Vitest, Playwright or a `node -e`/`.mjs` script; no `bash file.sh`; the Playwright config quotes the interpreter and requires `DEEP_RESEARCH_PYTHON` inside a worktree, and every Playwright block sets it; no `pip install` touches the shared venv; outside Playwright a server that outlives a call is started and stopped only through `scripts/launch.mjs` (detached spawn; `taskkill /T /F`; stop proven by port refusal); the live run's keys arrive by a file copy of `.env` that is deleted afterwards; the API's temp roots live under pytest's `tmp_path` or `web/.e2e-tmp/`; no live provider call happens before Task 21, and Task 21 starts only off-peak and after the human's confirmation.

## Execution notes for the controller

- Recommended execution: **subagent-driven**, one fresh implementer per task and a reviewer gate after each — twenty-one tasks whose later tasks depend on the exact names of earlier ones (the runner contract, the context fields, the component props), and a shipped mistake here is what the first human-visible product runs on.
- Implementation agents: the user's standing routing ruling applies (no DeepSeek-backed role); the plan itself names no model as a role.
- **Tasks run strictly one after another, 1 → 21**, in this single worktree: Tasks 1–8 share `app.py`/`models.py`/`sessions.py`, Tasks 14–18 share `SessionScreen.tsx` and `visual.spec.ts`, and every commit takes the same `.git/index.lock`.
- The controller's own viewing (R4) happens at **C3 (Task 18 step 8)** and **C4 (Task 19 step 3)**; the implementer reviews every checkpoint (C1 Task 14 step 9, C2 Task 16 step 10) full-height at both widths and attaches the images.
- Task 21 is gated twice: by `date -u` against the peak windows and by the human's confirmation through the controller. A "no" at either gate ends the task with the next allowed start reported.
