# CLI Live Canary Amendment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:subagent-driven-development` task-by-task. Each implementation
> task is assigned to a fresh `gpt-5.6-luna` worker at `max` reasoning on the
> collaboration service's fast/priority tier. Each
> scoped review is performed in the dedicated regular ChatGPT
> `GPT-5.6 Sol / High` review chat after the reviewed commit is pushed.

**Goal:** Preserve the failed Q1 canary as durable evidence, expose safe
diagnostics for the failures it revealed, and enforce run-scoped network
attempt ceilings at the real DeepSeek, OpenAI, and Tavily transport boundaries
before any separately authorized paid rerun.

**Architecture:** Add one thread-safe `RequestBudget` per production research
runtime in a neutral top-level module. Inject that exact instance into the
configured chat provider and Tavily search tool. Reserve an attempt immediately
before each SDK/client call, including repo-owned retries and structured repair
requests. A typed limit error bypasses recoverable tool handling and becomes an
enumerated halting graph error. Provider-reported token usage is accumulated
only after a response exists. The CLI receives a separate safe observer stream
for live budget updates and carries immutable terminal snapshots in
`ResearchOutcome`.

**Tech stack:** Python 3.11+, asyncio, Pydantic v2, OpenAI-compatible provider
SDKs, Tavily, httpx, LangGraph, argparse, pytest/pytest-asyncio, Ruff.

**Authority:**

- `docs/superpowers/specs/2026-09-15-cli-live-canary-amendment-design.md`
- Amends `docs/superpowers/plans/2026-09-14-cli-report-quality-and-agent-output-integrity.md`
- Starting branch: `codex/cross-agent-planner-fix-parity`
- Starting local and remote HEAD: `ab79f7a8b309f5ffd4905f64d71583d28c412912`
- Spent Q1 candidate: `7ef89ef057810464857413ea9078ff06b73814d9`

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
- No DeepSeek, OpenAI, Tavily, scraper/network, judge, LangSmith, or other live
  external call is authorized by this plan. Tests use offline fakes only.
- Do not change production reasoning effort (`high`), `agents.tool_budget`
  (`10`), `llm.timeout` (`60.0`), scraper `timeout_s` (`10.0`), or scraper
  retry policy.
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
  review. Do not dispatch that broad review and do not start Task 12 yet.

---

### Task 12: After a clean user-owned review, draft the next blocked canary

**Precondition:** the user has returned or explicitly authorized a clean broad
review of the exact pushed Task 11 head. Any code change after that review
invalidates the precondition and requires affected gates/review again.

**Files:**

- Create: `docs/superpowers/validation/2026-09-15-cli-live-canary-predeclaration-draft.md`

- [ ] RED path assertion.
- [ ] Write a draft beginning exactly:

  `Status: DRAFT — BLOCKED — NO PAID RUN AUTHORIZED`

- [ ] Record the reviewed parent SHA and results; unchanged production high
  reasoning, tool budget 10, LLM timeout 60.0, scraper timeout 10.0/retry
  policy; separate tool-invocation/transport units; post-response token caveat;
  exact Q1 question/options from the spent audit; and this proposed future
  request-scoped budget:

  - DeepSeek ceiling 240, stop fraction 0.90, effective 216;
  - OpenAI ceiling omitted because the Q1 provider is DeepSeek;
  - Tavily ceiling 330, stop fraction 0.90, effective 297.

- [ ] Record—but do not execute—the future command with:
  `--verbose --require-quality --deepseek-attempt-ceiling 240
  --tavily-attempt-ceiling 330 --request-stop-fraction 0.90`.
- [ ] State explicitly: Q1 blocked pending separate paid-run authorization;
  Q2/Q3 blocked pending a separately authorized Q1 passing every declared
  quality/integrity/judge gate. Implementation completion, review, or this
  document is not authorization for any external call.
- [ ] Commit `docs(validation): draft next CLI live canary`, push, obtain
  action-time confirmation for its scoped Sol/High documentation review, fix
  and re-review if needed, then push the clean reviewed commit again.
- [ ] Assert local/remote SHA equality, both validation documents tracked, and
  only the two known unrelated untracked paths remain.
- [ ] Stop and return the reviewed implementation HEAD, all task-review
  dispositions, offline gate/campaign results, and the predeclaration path.

## Final stop condition

Do not run Q1, Q2, or Q3. A new paid Q1 requires a separate, explicit user
authorization after Task 12's handoff.
