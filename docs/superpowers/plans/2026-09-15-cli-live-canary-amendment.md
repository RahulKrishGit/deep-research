# CLI Live Canary Amendment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:subagent-driven-development` task-by-task. Each implementation
> task is assigned to a fresh `gpt-5.6-luna` worker at `max` reasoning on the
> collaboration service's fast/priority tier. Each
> scoped review is performed in the dedicated regular ChatGPT
> `GPT-5.6 Sol / High` review chat after the reviewed commit is pushed.

**Goal:** Make the CLI pipeline produce a report that clears every release
criterion in the spec's §4 **in a single live run** — terminal `accepted`
quality, coverage ≥ 0.80 with every planned topic accounted for, zero duplicate
claims/source rows/unresolved citations, every cited source scored and every
settled point claim-linked, all verification passages provenance-bearing and
independently published, reader report ≤ 8,000 words, backmatter ≤ 35%, no
unexplained agent error, whole-report judge ≥ 0.80, and the CLI summary exactly
matching artifacts and state.

**Architecture:** Three sequential phases on one branch.

- **Phase 1 — Legibility and bounded spend (Tasks 1–11, unchanged).** Failures
  carry safe, bounded, countable diagnostics; one thread-safe `RequestBudget` per
  production runtime reserves every transport attempt at the real provider and
  search boundaries, so spend cannot silently overrun; the CLI gains
  request-scoped controls and safe observer output.
- **Phase 2 — Evidence and reliability (Tasks 13–19, new).** Close the remaining
  telemetry leaks, observe the real scraper failure classes, then fix
  reliability, per-agent reasoning effort, agent tool budgets and evidence
  volume — each guided by evidence rather than assumption.
- **Phase 3 — Convergence (Tasks 20–21, new).** Iterate predeclared canaries,
  each evaluated against all ten criteria, until one run clears them; any
  shortfall yields a diagnosis and an amendment, never an ad-hoc tuning edit.

Phase 1 is the foundation, not the goal: a run that is honest but partial is
still partial. Phases 2 and 3 are where the report itself becomes good.

**Tech stack:** Python 3.11+, asyncio, Pydantic v2, OpenAI-compatible provider
SDKs, Tavily, httpx, LangGraph, argparse, pytest/pytest-asyncio, Ruff.

**Authority:**

- `docs/superpowers/specs/2026-09-15-production-ready-reports-design.md` — the
  design this plan implements, and the binding authority for the goal, the
  release criteria, the phase structure, the constraint changes and the
  authorization below.
- `docs/superpowers/specs/2026-09-15-cli-live-canary-amendment-design.md`
- Amends `docs/superpowers/plans/2026-09-14-cli-report-quality-and-agent-output-integrity.md`
- Starting branch: `codex/cross-agent-planner-fix-parity`
- Starting local and remote HEAD: `ab79f7a8b309f5ffd4905f64d71583d28c412912`
- Spent Q1 candidate: `7ef89ef057810464857413ea9078ff06b73814d9`
- Current head at this amendment: `325c17f56087390c3728c3f3ff36b0f0921bb47e`

The starting HEAD is the plan-authoring base. Committing and pushing this plan
advances the branch before Task 1; Task 1 therefore requires local/remote
equality at the reviewed plan commit, not equality to the authoring base.

## Pre-execution audit corrections

The Sol/High draft was reviewed against the checked-out code before execution.
This tracked plan corrects these inconsistencies from that draft:

1. The repository targets Python 3.11 (`requires-python >=3.11`, Ruff
   `py311`), not Python 3.12.
2. The approved spec requires both DeepSeek and OpenAI chat transports to be
   counted; `ProviderCategory` therefore includes `openai`, and OpenAI receives
   its own reviewer-sized task.
3. `RequestBudgetConfig` follows the existing configuration layer and inherits
   `BaseModel` with `ConfigDict(extra="forbid")`; it does not introduce the
   research-domain `ContractModel` into `utils/config.py`.
4. The graph has `_from_exception()` and `planning_failed_error()`; it has no
   `graph_error_from_exception()` helper. Tasks below name the real seams.
5. The CLI must pass both request-scoped config overrides and a budget observer
   to `run_research_sync`; the observer is attached before graph execution.
6. Branch synchronization occurs before every Sol/High task review and again
   after every completed review/fix round, matching the user's push rule.
7. The final broad whole-branch review remains user-owned. This plan prepares
   and pushes the exact-head review packet, then stops. The predeclaration task
   resumes only after the user returns or authorizes a clean broad review.

## Global constraints

- Work only in the linked worktree for
  `codex/cross-agent-planner-fix-parity`. Before Task 1 run:

  ```powershell
  git branch --show-current
  git rev-parse HEAD
  git rev-parse origin/codex/cross-agent-planner-fix-parity
  git status --short
  git rev-parse --git-dir
  git rev-parse --git-common-dir
  ```

- The known untracked `.deepseek-runs/` and `tools/` paths are unrelated.
  Never inspect them for implementation material, stage, edit, move, delete,
  or clean them.
- Never run `git clean`, `git add -A`, `git add .`, `git add -f`, force-push,
  rebase, or reset. Stage only the exact paths listed for the current task.
- `output/` and `.superpowers/` remain ignored audit/scratch locations. Never
  force-add them.
- **Live calls are permitted ONLY inside a predeclared canary task** — Tasks 15
  and 20 — under that task's own declared request ceilings and within the
  authorization recorded below. Every other task in this plan stays fully
  offline and uses offline fakes only. No DeepSeek, OpenAI, Tavily,
  scraper/network, judge, or LangSmith call is authorized anywhere else.
- **Configuration changes are permitted where the task carries the evidence for
  the specific change.** This replaces the earlier blanket freeze, which was
  correct for a measurement-only plan and would now forbid this plan's own
  success. Now in scope, for the named task only: production reasoning effort
  (Task 17); `agents.tool_budget` (Task 18); `llm.timeout` and scraper
  `timeout_s` (Task 16, and only if the Task 15 diagnosis implicates them); the
  scraper retry policy (Task 16, and only if the diagnosis specifically
  implicates it). Every such change must preserve fail-closed behaviour, the
  provider retry and repair contracts, error translation, response clearing, and
  traceback safety, and must be justified in the task report by the evidence
  that motivated it.
- Tool invocations and transport attempts are different units.
  `web_search=365` is a tool-invocation count, not a proven Tavily request
  count.
- Token totals are post-response provider-reported observability, not a
  pre-request cost ceiling or dollar-cost guarantee.
- Do not count LangSmith, local embeddings, memory operations, scraper HTTP
  requests, or agent tool invocations in the DeepSeek/OpenAI/Tavily budget.
- Public state, errors, progress, and CLI output may contain only static
  project-authored messages and bounded categorical values. Never propagate
  exception messages, queries, URLs, page content, prompts, responses, tool
  arguments, trace payloads, credentials, or environment values.
- With ceiling `C` and stop fraction `F`, permit attempts
  `1..floor(C * F)` and reject `floor(C * F) + 1` before network I/O. A zero
  effective limit is valid and blocks the first attempt.
- A request limit is a hard run boundary. It must escape `BaseTool` and ReAct,
  halt the graph, and preserve CLI exit 3 precedence over quality exit 4.
- Preserve provider retry, repair, error translation, response clearing, and
  traceback-safety behavior. Add reservations at the call sites without broad
  provider refactors.
- Every task uses strict RED -> minimal implementation -> GREEN. Run the listed
  neighboring tests, Ruff on touched Python paths, and `git diff --check`.
- After each task commit:
  1. independently rerun the task gate;
  2. push the exact commit to the branch;
  3. obtain action-time browser confirmation;
  4. submit the scoped diff to the dedicated Sol/High review chat;
  5. if Critical/Important findings exist, dispatch a fresh Luna/max fix
     worker, verify and push the fix, and obtain confirmation for scoped
     re-review;
  6. when review is clean, push once more and record local/remote SHA equality.
- Do not begin the next task with an unresolved Critical or Important finding.

## Live authorization

Granted 2026-09-15 by the human, and the only spend this plan may make:

- **~10 live iteration runs inside a US$100 currency ceiling.**
- **Each run is predeclared before it happens**, recording: candidate SHA, the
  exact question, repetitions, the DeepSeek / OpenAI / Tavily request ceilings
  and stop fraction, stop conditions, artifact locations, and credential source.
  A run that was not predeclared does not happen.
- **The provider-side spend cap is the enforced bound**; the per-run request
  ceilings are the operational bound, enforced by the Task 5 `RequestBudget` at
  the transport boundaries.
- **LangSmith tracing stays ON** (`langsmith.tracing_enabled: true`, project
  `deep-research-dev`), explicitly authorized. Traces carry report and prompt
  content to the configured endpoint; credentials are never emitted.
- **No criterion in the spec's §4 may be waived, no threshold lowered, and no
  fixture adjusted to reduce spend or make a run appear to pass.** If the bar
  cannot be cleared within this budget, the deliverable is an explicit partial
  result naming the shortfall and the evidence for it.
- The **provider configuration is fixed for the whole campaign** except where
  Tasks 16–19 change it on evidence: model `deepseek-v4-flash`, thinking mode
  enabled, and the per-agent reasoning effort Task 17 establishes.

## Fixed interfaces

Create `src/deep_research/request_budget.py` with these public contracts:

```python
ProviderCategory = Literal["deepseek", "openai", "tavily"]
RequestBudgetEventKind = Literal[
    "attempt_reserved",
    "tokens_reported",
    "attempt_blocked",
]

@dataclass(frozen=True, slots=True)
class RequestBudgetSnapshot:
    provider: ProviderCategory
    attempts: int
    ceiling: int | None
    effective_limit: int | None
    input_tokens: int
    output_tokens: int

@dataclass(frozen=True, slots=True)
class RequestBudgetUpdate:
    kind: RequestBudgetEventKind
    snapshot: RequestBudgetSnapshot

RequestBudgetObserver = Callable[[RequestBudgetUpdate], None]

class RequestAttemptLimitError(RuntimeError):
    snapshot: RequestBudgetSnapshot

class RequestBudget:
    def reserve(self, provider: ProviderCategory) -> RequestBudgetSnapshot: ...
    def record_tokens(
        self,
        provider: ProviderCategory,
        *,
        input_tokens: int,
        output_tokens: int,
    ) -> RequestBudgetSnapshot: ...
    def snapshot(self, provider: ProviderCategory) -> RequestBudgetSnapshot: ...
    def snapshots(self) -> tuple[RequestBudgetSnapshot, ...]: ...
    def set_observer(self, observer: RequestBudgetObserver | None) -> None: ...
```

Add to `src/deep_research/utils/config.py`:

```python
class RequestBudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)

    deepseek_attempt_ceiling: int | None = Field(default=None, ge=1)
    openai_attempt_ceiling: int | None = Field(default=None, ge=1)
    tavily_attempt_ceiling: int | None = Field(default=None, ge=1)
    stop_fraction: float = Field(default=1.0, gt=0.0, le=1.0)
```

`ConfigSettings` gains
`request_budget: RequestBudgetConfig = RequestBudgetConfig()` to match the
existing immutable-default style in that module. Absent ceilings still count
attempts but never block.

---

### Task 1: Track the spent Q1 as a failed validation

**Status:** complete — reviewed clean, pushed at `21df64c349093f65f8a8219222f7b198386c1689`.

**Files:**

- Create: `docs/superpowers/validation/2026-09-15-cli-q1-failed-validation.md`

- [ ] **RED:**

  ```powershell
  python -c "from pathlib import Path; p=Path('docs/superpowers/validation/2026-09-15-cli-q1-failed-validation.md'); assert p.is_file(), p"
  ```

  Expected: assertion failure.

- [ ] Write a self-contained failed-validation record. It must include:

  - candidate `7ef89ef057810464857413ea9078ff06b73814d9`;
  - the absolute shared-venv/launcher command actually used, the exact Q1
    question, `--config config.yaml --verbose --require-quality`, and the
    pre-spend worktree probe; never include credential values;
  - the no-spend wrong-worktree argparse abort, the paid planner failure at
    `1839907`, and the completed pipeline run at `7ef89ef` as distinct events;
  - terminal `partial`, critic `4/10`, coverage `4/6`, exit `4`;
  - reader path, 10,724 bytes, SHA-256
    `0867c065c29d369115deabe306a329eb924ecfa14cbf4a894d54ef2ff545be01`;
  - evidence-ledger path, 24,119 bytes, SHA-256
    `0812c278f0f4a0045a023d4e89abbf3402cc297cdee1303efb9a53dda062b6ef`;
  - completed log path, 28,558 bytes, SHA-256
    `da07679972e1e3e54174e12542002483e66d486a8e49539c67a3452210a27be7`;
  - planner-failure log path, 2,346 bytes, SHA-256
    `3ea7a9e361fb1344e4493190dc655ccb57ce84abeaddc625a527d97b3380e1d1`;
  - tool invocations: `web_search=365`, `web_scraper=24`, failures `14`;
  - persisted-ledger `agent_tool_budget_exhausted` **rows**: researcher 46,
    fact-checker 18, critic 6, total 70;
  - completed CLI log string **occurrences**: 72;
  - the earlier report's `51/34/8` claim is unsupported and must not be
    repeated as an event count;
  - tokens as usage, not cost proof; missing transport-request counts;
  - credential-rotation disposition without the credential;
  - Q1 spent/failed, Q2/Q3 blocked, whole-report judge not run.

- [ ] **GREEN and hygiene:**

  ```powershell
  python -c "from pathlib import Path; p=Path('docs/superpowers/validation/2026-09-15-cli-q1-failed-validation.md'); t=p.read_text(encoding='utf-8'); required=['7ef89ef057810464857413ea9078ff06b73814d9','partial','4/10','4/6','365','24','14','46','18','6','70','72','Q1: SPENT','Q2: BLOCKED','Q3: BLOCKED']; assert all(x in t for x in required)"
  git check-ignore docs/superpowers/validation/2026-09-15-cli-q1-failed-validation.md
  git diff --check
  git status --short
  ```

  `git check-ignore` must return non-zero. Status may contain only the new
  document plus the two known unrelated untracked paths.

- [ ] Commit: `docs(validation): preserve failed CLI Q1 gate`

---

### Task 2: Preserve safe `PlanningError.problems`

**Status:** complete — reviewed clean, pushed at `e772966c70e2e88f37b71ad7c24455694bb54787`.

**Files:**

- Modify: `src/deep_research/graph/errors.py`
- Modify: `tests/test_graph/test_records.py`
- Modify: `tests/test_graph/test_nodes.py`

- [ ] Add RED tests proving `planning_failed_error()` keeps the enumerated
  message, adds `problems` as a list beside `exception_type`, and excludes a
  hostile `PlanningError` message/provider sentinel. Neighboring exception
  builders must retain their current detail shapes.
- [ ] RED:

  ```powershell
  python -m pytest tests/test_graph/test_records.py tests/test_graph/test_nodes.py -q -k "planning and problems"
  ```

- [ ] Change only `planning_failed_error()`: build details from
  `{"exception_type": type(error).__name__, "problems": list(error.problems)}`
  and call `graph_error(...)`. Import/type the parameter as `PlanningError`.
  Leave `_from_exception()` unchanged for every other builder. Do not copy
  `str(error)`, `repr(error)`, `args`, or traceback.
- [ ] GREEN:

  ```powershell
  python -m pytest tests/test_graph/test_records.py tests/test_graph/test_nodes.py tests/test_graph/test_state.py -q
  python -m ruff check src/deep_research/graph/errors.py tests/test_graph/test_records.py tests/test_graph/test_nodes.py
  git diff --check
  ```

- [ ] Commit: `fix(graph): preserve safe planning diagnostics`

---

### Task 3: Classify scraper failures without changing transport behavior

**Status:** complete — reviewed clean after one fix round, pushed at `9252263906eeba470e074c52d30c342f1e327328`. The fix round made the producer **omit** `status_code` when it falls outside `100..599`; Task 4 depends on that.

**Files:**

- Modify: `src/deep_research/tools/web_scraper.py`
- Modify: `tests/test_tools/test_web_scraper.py`

- [ ] Characterize the existing retry/timeout behavior before editing:

  ```powershell
  python -m pytest tests/test_tools/test_web_scraper.py -q -k "retries_rate_limited or exhausts_two_retries or constructor_rejects_invalid_limits"
  ```

- [ ] Add RED coverage for timeout, exhausted retry, HTTP status, robots
  denial, and unsupported content type. Hostile exception text, URL, response
  text, and content-type parameters must not enter public error details.
- [ ] RED:

  ```powershell
  python -m pytest tests/test_tools/test_web_scraper.py -q -k "bounded or classification or robots_disallows or unsupported_content_type"
  ```

- [ ] Preserve all retry decisions/call counts. Replace transport failure
  messages with static project text. Failure details may contain only:
  `attempts` in `1..3`, computed `retries` in `0..2`, integer `status_code` in
  `100..599`, and a lower-case media-type `content_type` of at most 64 ASCII
  characters. Invalid/malformed content types become `unknown`, never a
  truncated copy of attacker-controlled text. Remove the URL from robots-denial
  details.
- [ ] GREEN and re-characterization:

  ```powershell
  python -m pytest tests/test_tools/test_web_scraper.py tests/test_tools/test_base.py -q
  python -m pytest tests/test_tools/test_web_scraper.py -q -k "retries_rate_limited or exhausts_two_retries"
  python -m ruff check src/deep_research/tools/web_scraper.py tests/test_tools/test_web_scraper.py
  git diff --check
  ```

- [ ] Commit: `fix(tools): classify scraper failures safely`

---

### Task 4: Project bounded scraper diagnostics into agent records

**Files:**

- Modify: `src/deep_research/agents/react.py`
- Modify: `tests/test_agents/test_react.py`

- [ ] RED tests feed a hostile failed `web_scraper` `ToolResult` containing
  safe and forbidden keys. Expected `agent_tool_failed.details` is exactly
  `tool`, `iteration`, `tool_error_type`, `attempts`, `retries`,
  `status_code`, and `content_type`. An arbitrary tool must not gain this
  projection.
- [ ] RED:

  ```powershell
  python -m pytest tests/test_agents/test_react.py -q -k "safe_scraper or drops_scraper"
  ```

- [ ] Add a private allowlist projection that revalidates types/bounds and
  never copies the error message. Apply the same `attempts`, `retries`,
  `status_code`, and `content_type` bounds from Task 3. Preserve the existing
  observation behavior.
- [ ] GREEN:

  ```powershell
  python -m pytest tests/test_agents/test_react.py tests/test_agents/test_toolset.py -q
  python -m ruff check src/deep_research/agents/react.py tests/test_agents/test_react.py
  git diff --check
  ```

- [ ] Commit: `fix(agents): preserve bounded scraper diagnostics`

---

### Task 5: Add the request-budget primitive and configuration

**Files:**

- Create: `src/deep_research/request_budget.py`
- Create: `tests/test_request_budget.py`
- Modify: `src/deep_research/utils/config.py`
- Modify: `tests/test_config.py`

- [ ] RED tests cover floor arithmetic; zero effective limit; blocked attempt
  non-increment; absent ceiling; immutable safe snapshots; token accumulation;
  deterministic DeepSeek/OpenAI/Tavily order; observer update kinds; observer
  failure not masking/blocking enforcement; and parallel thread reservations.
- [ ] RED:

  ```powershell
  python -m pytest tests/test_request_budget.py -q
  python -m pytest tests/test_config.py -q -k "request_budget"
  ```

- [ ] Implement with `threading.Lock`. Call observers only after releasing the
  state lock. Observer failures are best-effort and must neither allow an SDK
  call after a blocked reservation nor mask `RequestAttemptLimitError`.
  `RequestAttemptLimitError.__str__` is static project text.
- [ ] Add the three optional positive ceilings and `(0, 1]` stop fraction.
  Do not edit `config.yaml`; canary limits are request-scoped CLI overrides.
- [ ] GREEN:

  ```powershell
  python -m pytest tests/test_request_budget.py tests/test_config.py tests/test_imports.py -q
  python -m ruff check src/deep_research/request_budget.py src/deep_research/utils/config.py tests/test_request_budget.py tests/test_config.py
  git diff --check
  ```

- [ ] Commit: `feat(runtime): add request attempt budget`

---

### Task 6: Count every DeepSeek transport attempt and reported token response

**Files:**

- Modify: `src/deep_research/providers/deepseek_provider.py`
- Modify: `src/deep_research/providers/factory.py`
- Modify: `tests/test_deepseek_provider.py`
- Modify: `tests/test_provider_factory.py`

- [ ] RED tests cover repo-owned retries, pre-I/O blocking, structured repair,
  native ReAct, Responses schema calls, token recording after response, and no
  invented tokens on transport failure.
- [ ] RED:

  ```powershell
  python -m pytest tests/test_deepseek_provider.py tests/test_provider_factory.py -q -k "request_budget"
  ```

- [ ] Add optional `request_budget` to DeepSeek construction and factory.
  Immediately before each of the four checked SDK call sites
  (`chat.completions.create` at current lines 765, 839, 1025 and
  `responses.create` at current line 1165), reserve `deepseek` outside SDK
  exception translation. Increment existing local attempt telemetry only after
  reservation succeeds. Record response usage after safe telemetry parsing and
  before local acceptance/validation logic. Preserve local clearing behavior.
- [ ] GREEN:

  ```powershell
  python -m pytest tests/test_deepseek_provider.py tests/test_retry_policy.py tests/test_provider_factory.py -q
  python -m ruff check src/deep_research/providers/deepseek_provider.py src/deep_research/providers/factory.py tests/test_deepseek_provider.py tests/test_provider_factory.py
  git diff --check
  ```

- [ ] Commit: `feat(providers): budget DeepSeek transport attempts`

---

### Task 7: Count every OpenAI transport attempt and reported token response

**Files:**

- Modify: `src/deep_research/providers/openai_provider.py`
- Modify: `src/deep_research/providers/factory.py`
- Modify: `tests/test_openai_provider.py`
- Modify: `tests/test_provider_factory.py`

- [ ] RED tests cover retries, structured repairs, native ReAct, pre-I/O
  blocking, and response token accumulation. An SDK `responses.parse`
  validation exception with no returned response consumes an attempt but cannot
  invent token usage.
- [ ] RED:

  ```powershell
  python -m pytest tests/test_openai_provider.py tests/test_provider_factory.py -q -k "request_budget"
  ```

- [ ] Pass the same optional factory budget to OpenAI. Reserve `openai`
  immediately before all three checked SDK sites (`responses.create` at current
  lines 424 and 577, `responses.parse` at current line 491), outside SDK error
  translation. Record tokens only from returned responses, before local output
  acceptance/validation.
- [ ] GREEN:

  ```powershell
  python -m pytest tests/test_openai_provider.py tests/test_retry_policy.py tests/test_provider_factory.py -q
  python -m ruff check src/deep_research/providers/openai_provider.py src/deep_research/providers/factory.py tests/test_openai_provider.py tests/test_provider_factory.py
  git diff --check
  ```

- [ ] Commit: `feat(providers): budget OpenAI transport attempts`

---

### Task 8: Count Tavily attempts and preserve hard-limit pass-through

**Files:**

- Modify: `src/deep_research/tools/web_search.py`
- Modify: `src/deep_research/tools/base.py`
- Modify: `tests/test_tools/test_web_search.py`
- Modify: `tests/test_tools/test_base.py`
- Modify: `tests/test_agents/test_react.py`

- [ ] RED tests prove one reservation per real/retried fake `search()` call,
  blocking before the next client call, BaseTool re-raise, and ReAct escape
  without `agent_tool_failed`.
- [ ] RED:

  ```powershell
  python -m pytest tests/test_tools/test_web_search.py tests/test_tools/test_base.py tests/test_agents/test_react.py -q -k "request_budget or request_attempt_limit"
  ```

- [ ] Add optional budget to `WebSearchTool`. The function passed to
  `asyncio.to_thread` must reserve `tavily` and then invoke
  `SearchClient.search`; retain the existing retry loop. Add
  `except RequestAttemptLimitError: raise` before BaseTool's generic handler.
  Do not catch it in ReAct.
- [ ] GREEN:

  ```powershell
  python -m pytest tests/test_tools/test_web_search.py tests/test_tools/test_base.py tests/test_agents/test_react.py -q
  python -m ruff check src/deep_research/tools/web_search.py src/deep_research/tools/base.py tests/test_tools/test_web_search.py tests/test_tools/test_base.py tests/test_agents/test_react.py
  git diff --check
  ```

- [ ] Commit: `feat(tools): budget Tavily transport attempts`

---

### Task 9: Share one budget through runtime and halt the graph

**Files:**

- Modify: `src/deep_research/runtime/assembly.py`
- Modify: `src/deep_research/graph/errors.py`
- Modify: `src/deep_research/graph/nodes.py`
- Modify: `src/deep_research/graph/state.py`
- Modify: `tests/test_runtime/test_assembly.py`
- Modify: `tests/test_graph/test_records.py`
- Modify: `tests/test_graph/test_nodes.py`
- Modify: `tests/test_graph/test_state.py`

- [ ] RED tests prove runtime object identity through constructor captures,
  configured limits, no embedding/scraper budget, an enumerated
  `graph_request_attempt_limit_exceeded`, and halting safe details exactly:
  `exception_type`, `provider`, `attempts`, `ceiling`, `effective_limit`.
- [ ] RED:

  ```powershell
  python -m pytest tests/test_runtime/test_assembly.py tests/test_graph/test_records.py tests/test_graph/test_nodes.py tests/test_graph/test_state.py -q -k "request_budget or request_attempt_limit"
  ```

- [ ] `ResearchRuntime` gains `request_budget`. `build_runtime()` constructs
  exactly one from `settings.request_budget`, passes it to
  `build_chat_provider()` and `build_tools()`, and `build_tools()` passes it
  only to `WebSearchTool`.
- [ ] Add the static graph message, add the error type to
  `HALTING_ERROR_TYPES`, add a dedicated safe builder from the exception
  snapshot, and catch `RequestAttemptLimitError` explicitly in `agent_node()`.
  Never route it through `_from_exception()`, provider retry, or recoverable
  tool taxonomies.
- [ ] GREEN:

  ```powershell
  python -m pytest tests/test_runtime/test_assembly.py tests/test_graph/test_records.py tests/test_graph/test_nodes.py tests/test_graph/test_state.py tests/test_graph/test_orchestrator.py -q
  python -m ruff check src/deep_research/runtime/assembly.py src/deep_research/graph/errors.py src/deep_research/graph/nodes.py src/deep_research/graph/state.py tests/test_runtime/test_assembly.py tests/test_graph/test_records.py tests/test_graph/test_nodes.py tests/test_graph/test_state.py
  git diff --check
  ```

- [ ] Commit: `feat(graph): halt on request attempt limits`

---

### Task 10: Add request-scoped CLI controls and safe live/terminal output

**Files:**

- Modify: `src/deep_research/runtime/outcome.py`
- Modify: `src/deep_research/main.py`
- Modify: `src/deep_research/cli.py`
- Modify: `tests/test_runtime/test_outcome.py`
- Modify: `tests/test_runtime/test_run_research.py`
- Modify: `tests/test_cli/test_arguments.py`
- Modify: `tests/test_cli/test_render.py`
- Modify: `tests/test_cli/test_entrypoint.py`

- [ ] RED argument tests add positive optional ceilings for DeepSeek, OpenAI,
  and Tavily plus `--request-stop-fraction` with `0 < F <= 1`. Omitted values
  produce no active limit. The CLI must build a partial nested
  `config_overrides["request_budget"]` rather than edit YAML.
- [ ] RED runtime tests add immutable terminal budget snapshots to
  `ResearchOutcome`, attach `request_budget_handler` after runtime construction
  but before graph execution, and pass `runtime.request_budget.snapshots()` to
  `build_outcome()`. The new `build_outcome()` parameter defaults to an empty
  tuple so existing injected/unit callers remain source-compatible.
- [ ] RED render/entry tests prove:
  - verbose live reserved/token/blocked lines are safe and flushed;
  - a local lock serializes worker-thread Tavily writes;
  - CLI `main()` passes **both** `config_overrides` and
    `request_budget_handler` to its runner;
  - terminal output distinguishes three provider categories and absent limits;
  - tokens are labelled post-response/not cost;
  - bounded scraper fields render without error message/URL/query content;
  - non-verbose output omits diagnostic detail;
  - request-limit graph failure exits 3 before `--require-quality` exit 4.
- [ ] RED:

  ```powershell
  python -m pytest tests/test_cli/test_arguments.py tests/test_cli/test_entrypoint.py tests/test_cli/test_render.py tests/test_runtime/test_outcome.py tests/test_runtime/test_run_research.py -q -k "request_budget or request_attempt or stop_fraction or scraper_failure or post_response"
  ```

- [ ] Implement `_stop_fraction()` parser, extend `CliOptions`, create a
  thread-safe `RequestBudgetStream`, and wire its handler into the runner.
  Keep ordinary `ResearchEvent` progress unchanged. Preserve
  `ResearchOutcome.token_usage` for API compatibility, but when budget
  snapshots exist replace the existing verbose CLI token line with the
  per-provider snapshot token section; do not print the same tokens twice.
  Legacy/injected outcomes with no snapshots may retain the old token line.
  Update fake runner/runtime builders in these tests to accept the new keyword
  arguments explicitly.
- [ ] GREEN:

  ```powershell
  python -m pytest tests/test_cli tests/test_runtime -q
  python -m ruff check src/deep_research/runtime/outcome.py src/deep_research/main.py src/deep_research/cli.py tests/test_runtime/test_outcome.py tests/test_runtime/test_run_research.py tests/test_cli/test_arguments.py tests/test_cli/test_render.py tests/test_cli/test_entrypoint.py
  git diff --check
  ```

- [ ] Commit: `feat(cli): report request budget observability`

---

### Task 11: Complete the offline gate and prepare the user-owned broad review

**Files:** none unless a focused failure requires a separately reviewed fix.

- [ ] Confirm every scoped task review is clean and every reviewed/fixed commit
  is pushed.
- [ ] Verify branch and scope:

  ```powershell
  git branch --show-current
  git status --short
  git log --oneline ab79f7a8b309f5ffd4905f64d71583d28c412912..HEAD
  ```

- [ ] Complete offline suite. Keep files from the same test subdirectory
  contiguous if a narrowed rerun is needed; the repository ledger records a
  pytest 9.1.1 fixture-resolution quirk for interleaved paths.

  ```powershell
  python -m pytest -q --ignore=tests/live
  python -m ruff check src tests
  python -m compileall -q src tests
  git diff --check ab79f7a8b309f5ffd4905f64d71583d28c412912..HEAD
  python -c "from deep_research.utils.config import load_config; s=load_config('config.yaml'); assert s.llm.reasoning_effort == 'high'; assert s.agents.tool_budget == 10; assert s.llm.timeout == 60.0"
  git diff ab79f7a8b309f5ffd4905f64d71583d28c412912..HEAD -- config.yaml
  ```

  The config diff must show no reasoning, tool-budget, timeout, or active
  canary-ceiling change.

- [ ] Run only the existing network-zero whole-report campaign:

  ```powershell
  python -m deep_research.e2e_evaluation suite --tier controlled --repetitions 3
  ```

  Never stage its ignored output.

- [ ] Push the verified exact head and assert local/remote equality.
- [ ] Prepare a broad-review packet: base/head SHAs, commit list, diff stat,
  scoped review dispositions, offline suite/Ruff/compileall/diff results, and
  controlled campaign result.
- [ ] **STOP.** Return this packet to the user for the user-owned whole-branch
  review. Do not dispatch that broad review and do not start Task 20 yet.

---

### Task 12 — RETIRED (superseded by Task 20)

The former Task 12 drafted a single next canary after one clean review. Under
this plan's goal the canary is no longer a one-off to be drafted once: Task 20
iterates predeclared canaries and re-drafts on every shortfall. Task 12 is
retired rather than renumbered so that references inside Tasks 1–11, which are
already reviewed, stay stable. Its content is preserved here for reference —
the predeclaration shape it describes is reused by Tasks 15 and 20:

- A draft predeclaration beginning exactly
  `Status: DRAFT — BLOCKED — NO PAID RUN AUTHORIZED` recorded the reviewed parent
  SHA, the then-frozen configuration, the separate tool-invocation/transport
  units, the post-response token caveat, and a proposed request-scoped budget
  (DeepSeek ceiling 240 / stop fraction 0.90 / effective 216; Tavily ceiling 330
  / 0.90 / effective 297). That budget shape is superseded by the per-run
  predeclarations Tasks 15 and 20 require, which must be derived from measured
  actuals rather than from the arithmetic that produced the spent Q1 ceilings.

---

### Task 13: Close the `web_search` telemetry leak

**Files:**

- Modify: `src/deep_research/tools/web_search.py`
- Modify: `tests/test_tools/test_web_search.py`

**Interfaces:** consumes the bounded-detail contract Task 3 established
(`attempts` 1..3, `retries` 0..2, `status_code` 100..599 when in range and
otherwise omitted, `content_type` a lower-case ASCII media type ≤ 64 characters
or the literal `unknown`).

**Why:** `web_search.py:174-182` holds a helper **AST-verified byte-identical**
to the pre-fix `web_scraper` helper. It publishes `str(error)` as the public
failure message, and its details lack `retries`. Same defect class as Task 3,
still live in a sibling module.

- [ ] RED: a failed `web_search` whose underlying exception carries a hostile
  sentinel must publish static project text, not the exception message, and its
  details must contain only the bounded keys. Include a timeout, an HTTP status
  (in range and out of range), and a malformed content type.
- [ ] RED:

  ```powershell
  python -m pytest tests/test_tools/test_web_search.py -q -k "bounded or classification"
  ```

- [ ] Replace the pre-fix helper's message and detail construction with Task 3's
  contract. Revalidate types and bounds at the producer rather than trusting the
  caller. **Preserve every retry decision and call count.**
- [ ] GREEN:

  ```powershell
  python -m pytest tests/test_tools/test_web_search.py tests/test_tools/test_base.py -q
  python -m ruff check src/deep_research/tools/web_search.py tests/test_tools/test_web_search.py
  git diff --check
  ```

- [ ] Commit: `fix(tools): bound web_search failure diagnostics`

---

### Task 14: Keep raw exception text out of public tool errors

**Files:**

- Modify: `src/deep_research/tools/base.py`
- Modify: `tests/test_tools/test_base.py`

**Why:** `base.py:119-127,134` builds
`ToolExecutionError(str(error) or type(error).__name__, ...)` for **any**
non-`ToolExecutionError` and publishes it. `agents/react.py:53` folds that
message into the model-visible observation summary. So every tool that lets a
raw exception escape leaks exception text — a per-tool fix in Tasks 3 and 13
closes instances, this closes the **class**.

- [ ] RED: a tool whose `execute` raises a hostile non-`ToolExecutionError`
  must not publish its message; assert the public `ToolError.message` is static
  project text, the failure stays classifiable via its enumerated type, and a
  serialized `ToolResult` contains no sentinel. Cover an arbitrary custom tool,
  not only the two real ones.
- [ ] RED:

  ```powershell
  python -m pytest tests/test_tools/test_base.py -q -k "hostile or static"
  ```

- [ ] Publish static project text plus the existing enumerated error type. Keep
  the failure typed and recoverable exactly as today; change only what carries
  text into the public field.
- [ ] GREEN:

  ```powershell
  python -m pytest tests/test_tools/test_base.py tests/test_tools/test_web_scraper.py tests/test_tools/test_web_search.py -q
  python -m ruff check src/deep_research/tools/base.py tests/test_tools/test_base.py
  git diff --check
  ```

- [ ] Commit: `fix(tools): keep raw exception text out of public errors`

---

### Task 15: Scraper-diagnosis canary — EVIDENCE ONLY, NO PRODUCTION CHANGE

**Files:**

- Create: `docs/superpowers/validation/2026-09-15-scraper-diagnosis-predeclaration.md`
- Create: `docs/superpowers/validation/YYYY-MM-DD-scraper-diagnosis.md`

**Why:** a live canary failed **14 of 24 `web_scraper` calls (58%)** and the
cause is unknown, because the failure reason was never populated before Task 3
shipped its classification and Task 4 projected it into agent records. This task
buys that knowledge. **It changes no production code.** Its outcome decides
Task 16's content.

- [ ] Predeclare the run in the predeclaration file: candidate SHA, the exact
  question (reuse the spent Q1 question for comparability), repetitions,
  DeepSeek / OpenAI / Tavily attempt ceilings and stop fraction, stop
  conditions, artifact locations, credential source. Verify the offline gate is
  green at the candidate head first.
- [ ] Run it **once**, under the predeclared ceilings enforced by the Task 5/9
  `RequestBudget`. Do not rerun after a failed gate.
- [ ] Extract the observed `web_scraper` failure classes with counts from the
  run's agent records, and attribute them across the whole run rather than one
  pass.
- [ ] Write the diagnosis: each observed failure class, its count, and what it
  implies. **State explicitly whether the failures are a defect at all.** Dead
  links returning 404, or robots denials on sources the Researcher chose, are
  legitimate outcomes rather than bugs, and reporting that is a valid result
  that reduces Task 16 to its finding.
- [ ] Record in the diagnosis the run's token and request actuals against its
  declared ceilings, and whether any ceiling was approached.
- [ ] Commit: `docs(validation): record scraper failure diagnosis`

---

### Task 16: Fix scraper reliability, guided by Task 15

**Files:** determined by Task 15. Candidate shape:

- Modify: `src/deep_research/tools/web_scraper.py` (transport timeouts, retry
  policy — only if implicated)
- Modify: `src/deep_research/agents/researcher.py` (only if the diagnosis shows
  a sourcing problem rather than a scraper problem)
- Test: `tests/test_tools/test_web_scraper.py`, plus the affected agent tests

**Decision procedure — the diagnosis selects the branch, not preference:**

| If Task 15 shows | Then fix | Where |
| --- | --- | --- |
| Per-request timeouts against slow hosts | `httpx` timeout configuration; **not** `scraper timeout_s` alone | `web_scraper.py` |
| Robots denying the chosen sources | a **sourcing** problem: choose sources that permit reading | `researcher.py` |
| Content-type rejections | source-classification problem | `web_scraper.py` / Researchers's source choice |
| Failures are not a defect | no code change; the task's deliverable is that finding | — |

- [ ] RED for whichever branch the diagnosis selects, with the failing assertion
  naming the observed class.
- [ ] Implement the minimal fix for that branch. **Preserve every retry decision
  and call count unless Task 15 specifically implicates the retry policy**, and
  state in the report which branch was taken and why.
- [ ] GREEN plus re-characterization of the pre-existing retry/timeout tests, so
  a transport change is provably not a behavioural accident.
- [ ] Commit: `fix(tools): improve scraper reliability` (or
  `fix(researcher): prefer readable sources` for the sourcing branch)

---

### Task 17: Per-agent reasoning effort in production

**Files:**

- Modify: `src/deep_research/utils/config.py`
- Modify: `src/deep_research/runtime/assembly.py` (or the provider-construction
  seam it uses)
- Modify: `config.yaml`
- Test: `tests/test_config.py`, plus the assembly/provider tests

**Why:** production exposes one global `llm.reasoning_effort`. The approved
cutover baseline is per-agent — `planner`, `fact_checker`, `synthesizer`,
`critic` at `max`; `researcher`, `source_evaluator` at `high` — and today it
exists only in `evaluation.target_reasoning_effort_overrides`, which applies to
**evaluation targets**, not production.

**⚠ Known risk, must be tested rather than assumed:** the controller's earlier
*global* `max` override broke the planner outright, producing
`graph_planning_failed` on every run. `high`-for-all works. Raising only some
agents to `max` is therefore **untested** at that seam and must be validated
per-agent, not assumed to be a strict improvement.

- [ ] RED: each of the six agents resolves its own configured effort, and an
  unset override falls back to the global value.
- [ ] Implement per-agent overrides mirroring the evaluation config's shape,
  applied where each agent's provider call is constructed.
- [ ] Validate on evidence, not on default: if any agent regresses under `max`,
  record it and keep that agent at `high` rather than shipping the split.
- [ ] Commit: `feat(config): allow per-agent reasoning effort`

---

### Task 18: Right-size agent tool budgets

**Files:**

- Modify: `src/deep_research/utils/config.py`
- Modify: `config.yaml`
- Modify: the agent construction seam that reads the budget
- Test: `tests/test_config.py` plus the affected agent tests

**Why:** `agent_tool_budget_exhausted` fired **51 times in the researcher, 34 in
the fact_checker and 8 in the critic**, repeatedly with up to five
provider-requested calls dropped at once. The provider consistently wants more
work than `agents.tool_budget: 10` allows per ReAct loop, and this is the most
likely direct cause of the canary's 67% coverage.

- [ ] Decide, on evidence and not preference, between a **global** increase and
  **per-agent** budgets. The distribution is dominated by the researcher (51 of
  93), which argues for per-agent; state the reasoning in the report.
- [ ] RED: the configured budget is what each agent's loop actually enforces,
  for both the global and the per-agent shape.
- [ ] Implement, with the value(s) justified in the report by the Task 15
  diagnosis plus the exhaustion distribution.
- [ ] Commit: `feat(agents): right-size agent tool budgets`

---

### Task 19: Evidence-volume experiments

**Files:** `config.yaml`, `src/deep_research/utils/config.py`, and the tests for
whichever knob each experiment changes.

**Why:** the canary produced only **4 cited sources and 2 verified claims**.
Whichever of Tasks 16–18 do not close the coverage gap, these do.

**One at a time.** Each experiment is its own predeclared canary, evaluated
against its own acceptance criterion, then kept or reverted — never bundled, so
a change can be attributed. The criteria are carried verbatim from the prior
plan:

| Experiment | Current | Candidate | Accept only if |
| --- | --- | --- | --- |
| Tavily search depth | `basic` | `advanced` | primary-source retrieval and coverage improve enough to justify latency/cost |
| Tavily results | 5 | 8 | source quality/coverage improves without increasing unused-source noise |
| Research observation summary | 200 chars | 600 chars | tool-loop completion and source-read selection improve without prompt overflow |
| Graph refinement budget | 3 | unchanged first | increase only if targeted refinements close gaps and cost per accepted report stays acceptable |

- [ ] For each experiment, in this order: predeclare the canary with its
  ceilings; run it once; compare the measured result against that row's
  acceptance criterion; **keep the change only if the criterion is met**,
  otherwise revert it and record why.
- [ ] After each accepted change, confirm the CLI summary still matches state
  and that `--require-quality` still returns the correct exit code for that
  run's terminal quality, before moving to the next experiment.
- [ ] Commit per accepted change: `feat(config): <the accepted experiment>`

---

### Task 20: Iterate predeclared canaries to the bar

**Files:**

- Create: `docs/superpowers/validation/YYYY-MM-DD-cli-canary-<n>-predeclaration.md`
- Create: `docs/superpowers/validation/YYYY-MM-DD-cli-canary-<n>-results.md`

**Why:** this is the convergence loop. Everything before it makes a run honest,
bounded, and well-informed; this is where one run finally clears all ten
criteria.

- [ ] Predeclare each iteration with its own ceilings, derived from the previous
  run's **measured** actuals rather than from arithmetic.
- [ ] Evaluate each run against **all ten** criteria in the spec's §4, recording
  each criterion's measured value, not a pass/fail only.
- [ ] Every shortfall produces a **diagnosis and an amendment** — never an
  ad-hoc prompt edit, threshold change, or fixture adjustment to suit the gate.
- [ ] **Stopping rules — the loop ends when any holds:**
  1. one run clears all ten criteria;
  2. the ~10-run / US$100 authorization is exhausted;
  3. two consecutive runs fail the same criterion — then produce a documented
     diagnosis and a proposed amendment instead of another run.
- [ ] **Honest-failure path:** if coverage cannot reach 0.80 within budget, the
  deliverable is an explicit partial result naming the shortfall and the
  evidence for it. **No criterion may be relaxed, no threshold lowered, and no
  fixture adjusted to make a run appear to pass.**
- [ ] Commit each iteration's predeclaration and results:
  `docs(validation): record canary iteration <n>`

---

### Task 21: Final validation record and review packet

**Files:**

- Create: `docs/superpowers/validation/YYYY-MM-DD-cli-report-quality-live-validation.md`
- Update: `docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md`

**Distinct from Task 11.** Task 11 gates the *amendment* phase: offline gate plus
an exact-head packet for the user-owned broad review. This task is the **final**
record for the **converged** SHA. Different SHAs, different scopes.

- [ ] Write the validation record for the converged SHA: candidate SHA, the
  winning run's session id, both artifact paths and their hashes, token and
  request actuals against the declared ceilings, and each of the ten criteria
  with its measured value.
- [ ] **Attest only to criteria actually met.** If the loop ended on rule 2 or 3,
  the record states which criteria were met, which were not, and the evidence
  for each shortfall. A validation record that overstates is worse than none.
- [ ] Run the complete offline gate at the recorded SHA and record exact counts.
- [ ] Prepare the broad-review packet: base/head SHAs, commit list, diff stat,
  every task-review disposition, the offline gate results, the controlled
  campaign result, and the canary record.
- [ ] Commit: `docs(validation): record production-ready report validation`
- [ ] **STOP.** Return the packet to the user for the user-owned whole-branch
  review. Do not dispatch that review.

## Final stop condition

The live authorization in this plan is limited to the predeclared canaries of
Tasks 15, 19 and 20, within ~10 runs and a US$100 currency ceiling. Nothing else
in this plan may make an external call. When Task 21 stops, the plan is complete
and any further paid run requires a new, explicit user authorization.
