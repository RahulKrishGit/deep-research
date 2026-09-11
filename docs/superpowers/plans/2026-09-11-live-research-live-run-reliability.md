# Live Research Reliability Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers (recommended) or superpowers to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Make the Streamlit UI and live-provider research path reliably launch from an isolated worktree, preserve deterministic partial reports, and report evidence-empty or provider-degraded research as `incomplete` rather than `completed`.

**Architecture:** Keep graph routing, provider retry policy, deterministic fallback synthesis, and front-end projections as separate responsibilities. Add one canonical research-completeness predicate to the graph layer, eliminate the Streamlit string-duration import trigger, enforce one-worktree/one-interpreter launch isolation, then harden the provider path with safe diagnostics, bounded degradation, cooperative cancellation, tested dependency constraints, and explicitly opt-in live smoke checks.

**Tech Stack:** Python 3.11+, LangGraph, Streamlit, Pydantic v2, DeepSeek/OpenAI provider adapters, ChromaDB/local embeddings, Tavily, LangSmith, FastAPI, pytest, pytest-asyncio.

**Spec:** `docs/analysis/2026-09-11-live-research-failure-analysis.md`

**Source commit:** `0f98c272ba209992608767c92c41d96e070b9c57`

**Plan path:** `docs/superpowers/plans/2026-09-11-live-research-live-run-reliability.md`

## Global Constraints

- Preserve partial reports and the Synthesizer's existing deterministic fallback-report generation.
- Never label an evidence-empty or essential-provider-degraded result `completed`.
- Keep provider retries owned exclusively by `src/deep_research/providers/retry.py` and the provider adapters. Do not add generic agent-, graph-, UI-, CLI-, or API-level provider retry loops.
- Do not add an incident-specific NumPy pin without a deterministic reproducer or a separately justified compatibility policy.
- Keep all external-provider and paid tests explicitly opt-in.
- The default offline `python -m pytest` path must remain deterministic and network-free.
- Never expose API keys, `.env` contents, authorization headers, prompts, completions, rejected model output, raw provider payloads, raw provider response bodies, or raw provider exception strings.
- Use one Git worktree, one virtual environment, and one editable install.
- Use `python -m pip`, `python -m pytest`, `python -m streamlit`, and `python -m deep_research` entry points.
- Treat `PYTHONPATH` overrides as diagnostic-only. Do not document them as the supported launch method.
- Leave the main checkout untouched.
- Preserve the distinction between graph/runtime failure and controlled degradation:
  - `failed`: execution could not terminate normally because of a graph-owned fatal condition.
  - `incomplete`: execution terminated safely but the research result is not complete enough to call successful.
  - `completed`: evidence-backed result with no quality-blocking condition.
  - `max_iterations`: evidence-backed result stopped because the configured refinement budget was exhausted.

- Do not use `bool(state.errors)`, every `recoverable=False` error, or report existence as the completeness predicate.
- A recoverable isolated tool failure must not by itself invalidate an otherwise evidence-backed completed result.
- Do not make agent-level provider failures graph-fatal merely to fix status labeling.
- Preserve `ResearchOutcome.status` as the single terminal-status value consumed by CLI, API, UI, and history. Do not reclassify status independently in each surface.

---

## File Map

### Terminal outcome and completeness semantics

- `src/deep_research/graph/state.py`
  - Current `graph_route(state: ResearchState) -> tuple[str, str]`.
  - Current `graph_status(state: ResearchState) -> str`.
  - Current route/status mapping:
    - `critique_satisfied -> completed`
    - `max_iterations_reached -> max_iterations`
    - `missing_critique -> incomplete`
    - `refinement_requested -> incomplete`
    - `halted -> failed`

  - Add the canonical quality-blocking error taxonomy and research-completeness predicates.

- `src/deep_research/graph/orchestrator.py`
  - Continue obtaining the final status from `graph_status(final_state)`.
  - Continue emitting the same canonical value in `graph.session.completed` and trace outputs.

- `src/deep_research/runtime/outcome.py`
  - `ResearchOutcome.status`.
  - `build_outcome(run, metrics)`.
  - Remain a projection layer; do not duplicate completeness policy here.

### Error producers used by completeness semantics

- `src/deep_research/agents/researcher.py`
  - `researcher_extraction_provider_error`.

- `src/deep_research/agents/source_evaluator.py`
  - `source_evaluator_scoring_provider_error`.

- `src/deep_research/agents/fact_checker.py`
  - `claim_extraction_provider_error`.
  - Exact error type: `fact_checker_extraction_provider_error`.
  - `claim_verification_provider_error`.
  - Exact error type: `fact_checker_verification_provider_error`.
  - `verify_claim` catches `ProviderError`, records the verification-provider error, preserves an `insufficient_evidence` claim, and returns `provider_failed=True`.

- `src/deep_research/agents/synthesizer.py`
  - `synthesizer_report_provider_error`.
  - `synthesizer_no_evidence`.
  - Preserve deterministic fallback Markdown.

- `src/deep_research/agents/critic.py`
  - `critic_review_provider_error`.

### Status consumers

- `src/deep_research/cli.py`
  - `STATUS_NOTES`.
  - `render_summary`.
  - `main`.

- `src/deep_research/api/models.py`
  - `SessionStatus`.

- `src/deep_research/api/sessions.py`
  - `TERMINAL_STATUSES`.
  - `ResearchSession.status`.
  - `SessionStore._run`.

- `src/deep_research/ui/runner.py`
  - `LocalResearchController._run`.
  - `LocalResearchController._snapshot_from_values`.

- `src/deep_research/ui/components.py`
  - `_STATUS_PRESENTATION`.
  - `status_presentation`.
  - completed/incomplete report rendering.
  - `_EXECUTION_ERROR_SPECS`.

- `src/deep_research/ui/history.py`
  - terminal snapshot persistence and report lookup.

- `src/deep_research/ui/models.py`
  - `UiSessionSnapshot`.
  - `SessionHistoryEntry`.
  - `history_entry_from_snapshot`.

### Streamlit startup mitigation

- `src/deep_research/ui/app.py`
  - Current `@st.fragment(run_every="2s")`.
  - Change to numeric `@st.fragment(run_every=2.0)`.

- `tests/test_ui/test_app.py`
  - Add numeric-fragment regression.

- `tests/test_ui/test_runner.py`
  - Add network-free repeated startup/controller stress coverage.

### Worktree and interpreter isolation

- Create `src/deep_research/runtime/environment.py`
  - Safe package/interpreter origin checks.
  - Boolean-only CLI output.

- Create `tests/test_runtime/test_environment.py`
  - Deterministic origin-preflight tests.

- `README.md`
  - Replace bare executable instructions with `python -m` commands.
  - Document one-worktree/one-venv/one-editable-install contract.

- Create `docs/runbooks/2026-09-11-live-research-verification.md`
  - Offline and opt-in live verification sequence.

### Safe provider diagnostics

- `src/deep_research/providers/contracts.py`
  - Existing `ProviderError` hierarchy.
  - Existing `ProviderResponseTelemetry`.
  - Existing `ProviderFailureCategory`.
  - Add a bounded safe provider-failure diagnostic model.

- `src/deep_research/providers/retry.py`
  - Existing `with_retries`.
  - Continue owning retry decisions and attempt counting.

- `src/deep_research/providers/deepseek_provider.py`
  - Attach provider/model context to safe failure metadata.

- `src/deep_research/providers/openai_provider.py`
  - Attach provider/model context to safe failure metadata.

- `src/deep_research/agents/errors.py`
  - Add one shared provider-error-to-safe-details function.

- Exact tests:
  - `tests/test_provider_contracts.py`
  - `tests/test_retry_policy.py`
  - `tests/test_deepseek_provider.py`
  - `tests/test_openai_provider.py`
  - `tests/test_agents/test_errors.py`
  - `tests/test_agents/test_researcher.py`
  - `tests/test_agents/test_source_evaluator.py`
  - `tests/test_agents/test_fact_checker.py`
  - `tests/test_agents/test_synthesizer.py`
  - `tests/test_agents/test_critic.py`
  - `tests/test_agents/test_react.py`
  - `tests/test_evaluation/test_isolation_and_secrets.py`

### Provider-degraded routing

- `src/deep_research/graph/state.py`
  - Add `is_provider_degraded(state)` and a `provider_degraded` route reason.
  - Prevent another macro refinement after essential provider degradation.
  - Preserve the current pass and partial report.

- `tests/test_graph/test_state.py`
- `tests/test_graph/test_nodes.py`
- `tests/test_graph/test_session.py`

### Cooperative cancellation

- Create `src/deep_research/runtime/cancellation.py`
- Modify `src/deep_research/main.py`
- Modify `src/deep_research/graph/nodes.py`
- Modify `src/deep_research/agents/react.py`
- Modify `src/deep_research/ui/runner.py`
- Modify `src/deep_research/ui/components.py`

### Dependency reproducibility and live smoke

- `pyproject.toml`
  - Do not add a NumPy incident pin.

- Create only after compatibility verification:
  - `constraints/dev-tested.txt`

- Existing:
  - `tests/live/test_deepseek_live.py`

- Create:
  - `tests/live/test_research_smoke_live.py`

---

# Wave 1 — Minimum Required for a Trustworthy Live Run

## Task 1: Add Canonical Research-Completeness Semantics

**Files:**

- Modify: `src/deep_research/graph/state.py`
- Modify: `tests/test_graph/test_state.py`
- Use existing fixtures from: `tests/graph_fakes.py`

**Interfaces:**

Add these exact public graph-layer interfaces:

```
QUALITY_BLOCKING_PROVIDER_ERROR_TYPES: frozenset[str]
QUALITY_BLOCKING_RESULT_ERROR_TYPES: frozenset[str]

def has_substantive_research_evidence(state: ResearchState) -> bool:
    return bool(
        state.raw_findings
        or state.evaluated_sources
        or state.verified_claims
    )

def has_quality_blocking_error(state: ResearchState) -> bool:
    return any(
        error.error_type in QUALITY_BLOCKING_RESULT_ERROR_TYPES
        for error in state.errors
    )

def research_result_is_complete(state: ResearchState) -> bool:
    return (
        has_substantive_research_evidence(state)
        and not has_quality_blocking_error(state)
    )
```

Define the exact initial provider blocker set as:

```
QUALITY_BLOCKING_PROVIDER_ERROR_TYPES = frozenset(
    {
        "researcher_extraction_provider_error",
        "source_evaluator_scoring_provider_error",
        "fact_checker_extraction_provider_error",
        "fact_checker_verification_provider_error",
        "synthesizer_report_provider_error",
        "critic_review_provider_error",
    }
)

QUALITY_BLOCKING_RESULT_ERROR_TYPES = frozenset(
    {
        *QUALITY_BLOCKING_PROVIDER_ERROR_TYPES,
        "synthesizer_no_evidence",
    }
)
```

Do not add `HALTING_ERROR_TYPES` entries for these errors.

Update `graph_status` so route status is still authoritative for fatal execution outcomes, but completeness can downgrade successful-looking terminal routes:

```
def graph_status(state: ResearchState) -> str:
    _, reason = graph_route(state)

    if reason == "halted":
        return "failed"

    if reason in {"missing_critique", "refinement_requested"}:
        return "incomplete"

    if not research_result_is_complete(state):
        return "incomplete"

    return _STATUS_BY_ROUTE_REASON[reason]
```

This produces:

- valid `critique_satisfied` -> `completed`;
- degraded `critique_satisfied` -> `incomplete`;
- valid `max_iterations_reached` -> `max_iterations`;
- degraded `max_iterations_reached` -> `incomplete`;
- halted -> `failed`.

### TDD steps

- [ ] **Step 1: Add a failing test for the observed multi-stage provider-degraded result**

Add to `tests/test_graph/test_state.py`:

```
def test_provider_degraded_satisfied_critique_is_incomplete() -> None:
    state = fake_research_state(
        report="# Partial fallback report",
        critique=fake_critique(should_continue=False, score=8),
        errors=[
            ResearchError(
                error_type="researcher_extraction_provider_error",
                source="agent.researcher",
                message="Finding extraction failed.",
                recoverable=False,
            ),
            ResearchError(
                error_type="fact_checker_extraction_provider_error",
                source="agent.fact_checker",
                message="Claim extraction failed.",
                recoverable=False,
            ),
            ResearchError(
                error_type="synthesizer_report_provider_error",
                source="agent.synthesizer",
                message="Report generation failed.",
                recoverable=False,
            ),
        ],
    )

    assert graph_route(state) == (ROUTE_END, "critique_satisfied")
    assert graph_status(state) == "incomplete"
```

- [ ] **Step 2: Add a failing test for the exact verified fact-check verification error type**

```
def test_fact_check_verification_provider_failure_blocks_completion() -> None:
    state = fake_research_state(
        raw_findings=[fake_finding()],
        verified_claims=[
            fake_claim().model_copy(
                update={
                    "verdict": "insufficient_evidence",
                    "confidence": 0.0,
                    "evidence": [],
                }
            )
        ],
        report="# Partial report",
        critique=fake_critique(should_continue=False, score=8),
        errors=[
            ResearchError(
                error_type="fact_checker_verification_provider_error",
                source="agent.fact_checker",
                message="Claim verification failed.",
                recoverable=False,
            )
        ],
    )

    assert graph_status(state) == "incomplete"
```

- [ ] **Step 3: Add a failing test for synthesizer_no_evidence**

```
def test_evidence_empty_fallback_report_is_incomplete() -> None:
    state = fake_research_state(
        report="# Limitation-only fallback report",
        raw_findings=[],
        evaluated_sources=[],
        verified_claims=[],
        critique=fake_critique(should_continue=False, score=8),
        errors=[
            ResearchError(
                error_type="synthesizer_no_evidence",
                source="agent.synthesizer",
                message="No evidence was available.",
                recoverable=True,
            )
        ],
    )

    assert graph_status(state) == "incomplete"
```

- [ ] **Step 4: Add a failing test proving report existence is not evidence**

```
def test_report_text_alone_does_not_make_a_result_complete() -> None:
    state = fake_research_state(
        report="# Deterministic report skeleton",
        raw_findings=[],
        evaluated_sources=[],
        verified_claims=[],
        critique=fake_critique(should_continue=False, score=9),
    )

    assert has_substantive_research_evidence(state) is False
    assert research_result_is_complete(state) is False
    assert graph_status(state) == "incomplete"
```

- [ ] **Step 5: Add a compatibility test for a recoverable tool failure**

```
def test_recoverable_tool_failure_does_not_block_valid_completion() -> None:
    state = fake_research_state(
        raw_findings=[fake_finding()],
        report="# Evidence-backed report",
        critique=fake_critique(should_continue=False, score=9),
        errors=[
            ResearchError(
                error_type="agent_tool_failed",
                source="agent.researcher",
                message="One tool failed and research continued.",
                recoverable=True,
            )
        ],
    )

    assert research_result_is_complete(state) is True
    assert graph_status(state) == "completed"
```

- [ ] **Step 6: Update the existing satisfied-Critic test to contain substantive evidence**

Change its fixture from a bare critique to:

```
state = fake_research_state(
    raw_findings=[fake_finding()],
    report="# Evidence-backed report",
    critique=fake_critique(should_continue=False, score=9),
)
```

Keep:

```
assert graph_route(state) == (ROUTE_END, "critique_satisfied")
assert graph_status(state) == "completed"
```

- [ ] **Step 7: Add a valid max-iterations compatibility test**

```
def test_evidence_backed_max_iteration_result_keeps_max_iterations_status() -> None:
    state = fake_research_state(
        raw_findings=[fake_finding()],
        report="# Evidence-backed report",
        iteration=2,
        max_iterations=2,
        critique=fake_critique(should_continue=True),
    )

    assert graph_route(state) == (ROUTE_END, "max_iterations_reached")
    assert graph_status(state) == "max_iterations"
```

- [ ] **Step 8: Add a degraded max-iterations test**

```
def test_quality_blocker_downgrades_max_iterations_to_incomplete() -> None:
    state = fake_research_state(
        raw_findings=[fake_finding()],
        report="# Partial report",
        iteration=2,
        max_iterations=2,
        critique=fake_critique(should_continue=True),
        errors=[
            ResearchError(
                error_type="synthesizer_report_provider_error",
                source="agent.synthesizer",
                message="Report generation failed.",
                recoverable=False,
            )
        ],
    )

    assert graph_route(state) == (ROUTE_END, "max_iterations_reached")
    assert graph_status(state) == "incomplete"
```

- [ ] **Step 9: Run the targeted file before implementation**

```
python -m pytest tests/test_graph/test_state.py -v
```

**Expected failure:** new degraded/evidence-empty tests currently receive `completed` or `max_iterations` because `graph_status()` only maps `graph_route()`'s reason.

- [ ] **Step 10: Implement the minimal predicates and graph_status() downgrade**

Modify only `src/deep_research/graph/state.py`.

Do not change `graph_route()` in this task.
Do not change `HALTING_ERROR_TYPES`.
Do not change agent error recoverability.

- [ ] **Step 11: Run the targeted graph-state tests**

```
python -m pytest tests/test_graph/test_state.py -v
```

**Expected:** PASS.

- [ ] **Step 12: Run all graph tests**

```
python -m pytest tests/test_graph -v
```

**Expected:** PASS.

- [ ] **Step 13: Commit**

```
git add src/deep_research/graph/state.py tests/test_graph/test_state.py
git commit -m "fix: classify degraded research as incomplete"
```

**Acceptance criteria:**

- `fact_checker_verification_provider_error` is explicitly quality-blocking.
- Evidence-empty fallback reports cannot be `completed`.
- Recoverable tool errors alone do not block valid completion.
- Fatal graph semantics remain unchanged.
- Partial report state is untouched.

---

## Task 2: Lock Status Consistency Across Runtime, CLI, API, UI, and History

**Files:**

- Modify: `tests/test_runtime/test_outcome.py`
- Modify: `tests/test_cli/test_render.py`
- Modify: `tests/test_cli/test_entrypoint.py`
- Modify: `src/deep_research/cli.py`
- Modify: `tests/test_api/test_sessions.py`
- Modify: `tests/test_api/test_stream_and_artifacts.py`
- Modify: `tests/test_ui/test_runner.py`
- Modify: `tests/test_ui/test_history.py`
- Modify: `tests/test_ui/test_app.py`
- Modify: `src/deep_research/ui/components.py`

**Interfaces:**

No new status classifier is allowed outside `graph/state.py`.

The propagation contract remains:

```
ResearchOutcome.status == GraphRun.status
ResearchSession.status == ResearchOutcome.status
_ActiveSession.status == ResearchOutcome.status
SessionHistoryEntry.status == UiSessionSnapshot.status
```

### TDD steps

- [ ] **Step 1: Add an incomplete-outcome projection test to tests/test_runtime/test_outcome.py**

Use existing `base_state`, `synthesis_event`, `GraphRun`, and `build_outcome`:

```
def test_build_outcome_preserves_incomplete_status_and_partial_report() -> None:
    state = base_state(
        report="# Partial report",
        events=[synthesis_event("report-session-1-partial.md")],
    )
    run = GraphRun(
        session_id="session-1",
        state=state,
        status="incomplete",
        trace_url=None,
    )

    outcome = build_outcome(run, metrics=[])

    assert outcome.status == "incomplete"
    assert outcome.report == "# Partial report"
    assert outcome.report_path == "report-session-1-partial.md"
    assert outcome.failed is False
```

Expected behavior should already pass. This test protects against later projection drift.

- [ ] **Step 2: Add CLI rendering coverage to tests/test_cli/test_render.py**

Construct an existing `ResearchOutcome` test fixture with:

- `status="incomplete"`;
- `report="# Partial report"`;
- a report path.

Assert rendered summary contains:

```
Status: incomplete
Research ended with a partial result
```

and does not contain:

```
Research completed with limitations: the run ended without an accepted critique.
```

- [ ] **Step 3: Change the CLI incomplete status note**

In `src/deep_research/cli.py`, set:

```
"incomplete": (
    "Research ended with a partial result; review the recorded limitations "
    "and warnings before using the report."
),
```

Do not change the exit-code contract in this task.

- [ ] **Step 4: Add CLI entrypoint coverage to tests/test_cli/test_entrypoint.py**

Use the existing injected runner pattern.

Return a `ResearchOutcome` with `status="incomplete"`.

Assert:

- command returns `EXIT_OK`;
- output says `Status: incomplete`;
- retained report path is printed.

This intentionally preserves the current rule that only `failed` returns exit code 3.

- [ ] **Step 5: Add API session propagation coverage to tests/test_api/test_sessions.py**

Use the existing fake async runner.

Return an outcome with:

- `status="incomplete"`;
- `state.report="# Partial report"`.

Assert after the task finishes:

```
assert session.status == "incomplete"
assert session.outcome is not None
assert session.outcome.report == "# Partial report"
```

- [ ] **Step 6: Add API stream/artifact coverage to tests/test_api/test_stream_and_artifacts.py**

Test an incomplete terminal session with a retained report.

Assert:

- SSE terminates after the incomplete result is reached;
- status response says `incomplete`;
- report endpoint returns the retained Markdown;
- report is not rejected merely because the terminal state is incomplete.
- [ ] **Step 7: Add UI controller coverage to tests/test_ui/test_runner.py**

Add:

```
def test_incomplete_outcome_retains_partial_report_and_status(
    tmp_path: Path,
) -> None:
```

Use `GatedSyncRunner` configured with:

- `status="incomplete"`;
- `report="# Partial report"`;
- `report_path="reports/partial.md"`.

Create the actual report file under the test output directory as existing report-history tests do.

Assert:

- terminal snapshot status is `incomplete`;
- `snapshot.report == "# Partial report"`;
- `snapshot.report_path == "reports/partial.md"`;
- `snapshot.current_agent is None`.
- [ ] **Step 8: Add history restart coverage to tests/test_ui/test_history.py**

Persist an incomplete terminal snapshot with a partial report path.

Instantiate a fresh history store/controller view over the same test directory.

Assert:

- restored history entry status remains `incomplete`;
- report path remains present;
- report body can be loaded;
- status is not transformed to `completed`.
- [ ] **Step 9: Add UI presentation coverage to tests/test_ui/test_app.py**

Render an incomplete snapshot with a retained partial report.

Assert the page contains:

- `Incomplete`;
- report body text.

Assert it does not display `Completed` as the terminal report status.

- [ ] **Step 10: Make incomplete report labeling explicit in src/deep_research/ui/components.py**

Retain `_STATUS_PRESENTATION["incomplete"]`.

For the completed-report body/header path, ensure the incomplete report label renders:

```
Incomplete · Partial report
```

Do not suppress the report body.

Do not add a second completeness predicate.

- [ ] **Step 11: Run all targeted files**

```
python -m pytest tests/test_runtime/test_outcome.py -v
python -m pytest tests/test_cli/test_render.py tests/test_cli/test_entrypoint.py -v
python -m pytest tests/test_api/test_sessions.py tests/test_api/test_stream_and_artifacts.py -v
python -m pytest tests/test_ui/test_runner.py tests/test_ui/test_history.py tests/test_ui/test_app.py -v
```

**Expected:** PASS.

- [ ] **Step 12: Commit**

```
git add src/deep_research/cli.py
git add src/deep_research/ui/components.py
git add tests/test_runtime/test_outcome.py
git add tests/test_cli/test_render.py tests/test_cli/test_entrypoint.py
git add tests/test_api/test_sessions.py tests/test_api/test_stream_and_artifacts.py
git add tests/test_ui/test_runner.py tests/test_ui/test_history.py tests/test_ui/test_app.py
git commit -m "test: keep incomplete status consistent across surfaces"
```

**Acceptance criteria:**

- One canonical status reaches every surface.
- Incomplete reports remain readable/downloadable.
- CLI/API/UI/history never infer `completed` from report existence.
- Existing `failed` behavior remains unchanged.

---

## Task 3: Replace the Streamlit String Fragment Interval with Numeric Seconds

**Files:**

- Modify: `src/deep_research/ui/app.py`
- Modify: `tests/test_ui/test_app.py`

**Interfaces:**

Current:

```
@st.fragment(run_every="2s")
def render_live_progress(controller: LocalResearchController) -> None:
```

Target:

```
@st.fragment(run_every=2.0)
def render_live_progress(controller: LocalResearchController) -> None:
```

### TDD steps

- [ ] **Step 1: Add a regression test that inspects the source AST**

In `tests/test_ui/test_app.py`, add a deterministic AST-based test that:

- reads `src/deep_research/ui/app.py`;
- finds `render_live_progress`;
- finds its `st.fragment` decorator;
- asserts the `run_every` keyword is a numeric constant equal to `2.0`.

Name:

```
def test_live_progress_fragment_uses_numeric_two_second_interval() -> None:
```

The AST route avoids reloading Streamlit modules and remains network-free.

- [ ] **Step 2: Run the exact failing test**

```
python -m pytest tests/test_ui/test_app.py::test_live_progress_fragment_uses_numeric_two_second_interval -v
```

**Expected failure:** decorator value is the string `"2s"`.

- [ ] **Step 3: Apply the one-line implementation**

Change only:

```
@st.fragment(run_every=2.0)
```

Do not:

- import NumPy;
- preload NumPy;
- pin NumPy;
- change refresh cadence.
- [ ] **Step 4: Run the exact regression**

```
python -m pytest tests/test_ui/test_app.py::test_live_progress_fragment_uses_numeric_two_second_interval -v
```

**Expected:** PASS.

- [ ] **Step 5: Run the full UI test set**

```
python -m pytest tests/test_ui -v
```

**Expected:** PASS.

- [ ] **Step 6: Commit**

```
git add src/deep_research/ui/app.py tests/test_ui/test_app.py
git commit -m "fix: use numeric Streamlit fragment interval"
```

**Acceptance criteria:**

- Refresh cadence remains two seconds.
- Project code no longer requests Streamlit's string duration parser for the fragment.
- No dependency workaround is introduced.

---

## Task 4: Add Network-Free Worker/Refresh Stress Coverage

**Files:**

- Modify: `tests/test_ui/test_runner.py`
- Modify: `tests/test_ui/test_app.py`
- Use: `tests/test_ui/fakes.py`

**Interfaces:**

- `LocalResearchController.start`
- `LocalResearchController.snapshot`
- `render_live_progress`
- `GatedSyncRunner`

### TDD steps

- [ ] **Step 1: Add a repeated worker-start test to tests/test_ui/test_runner.py**

Name:

```
def test_repeated_worker_start_and_snapshot_polling_remain_stable(
    tmp_path: Path,
) -> None:
```

Run 25 sequential sessions.

For each session:

- call `controller.start(question="Question", max_iterations=1)`;
- wait for `runner.started`;
- poll `controller.snapshot(session_id)` while status is running;
- release the runner;
- wait until status is terminal;
- assert exactly one runner invocation was made for that session.

Use fake runners only.

- [ ] **Step 2: Add live-fragment transition coverage to tests/test_ui/test_app.py**

Name:

```
def test_live_fragment_clears_poll_target_when_session_becomes_terminal() -> None:
```

Use the existing fake controller/AppTest mechanism.

Assert:

- running snapshot keeps `_LIVE_SESSION_KEY`;
- incomplete terminal snapshot clears `_LIVE_SESSION_KEY`;
- report remains available after the app-level rerun.
- [ ] **Step 3: Run both tests**

```
python -m pytest tests/test_ui/test_runner.py::test_repeated_worker_start_and_snapshot_polling_remain_stable -v
python -m pytest tests/test_ui/test_app.py::test_live_fragment_clears_poll_target_when_session_becomes_terminal -v
```

If both pass immediately, retain them as regression guards. Do not claim they reproduce the historical NumPy failure.

- [ ] **Step 4: Run the entire UI set three consecutive times**

```
python -m pytest tests/test_ui -v
python -m pytest tests/test_ui -v
python -m pytest tests/test_ui -v
```

**Expected:** PASS three times.

- [ ] **Step 5: Do not modify application synchronization unless the new deterministic tests fail**

The accepted implementation for this task is test-only when the stress path is stable after Task 3.

No import lock, NumPy preload, artificial sleep, or global worker serialization is permitted without a deterministic failing test.

- [ ] **Step 6: Commit**

```
git add tests/test_ui/test_runner.py tests/test_ui/test_app.py
git commit -m "test: stress live UI worker refresh lifecycle"
```

**Acceptance criteria:**

- Stress coverage requires no provider/network.
- Worker lifecycle remains observable while Streamlit progress polling occurs.
- No unsupported race root-cause claim is introduced.

---

## Task 5: Add a Worktree and Interpreter Origin Preflight

**Files:**

- Create: `src/deep_research/runtime/environment.py`
- Create: `tests/test_runtime/test_environment.py`
- Modify: `README.md`

**Interfaces:**

Create:

```
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True, slots=True)
class LaunchEnvironmentCheck:
    package_origin_matches_checkout: bool
    streamlit_origin_matches_environment: bool

    @property
    def valid(self) -> bool:
        return (
            self.package_origin_matches_checkout
            and self.streamlit_origin_matches_environment
        )

def package_origin_matches_checkout(
    *,
    checkout_root: Path,
    package_file: str | Path,
) -> bool:
    package_root = (checkout_root / "src" / "deep_research").resolve()
    candidate = Path(package_file).resolve()
    return candidate.is_relative_to(package_root)

def streamlit_origin_matches_environment(
    *,
    streamlit_file: str | Path,
    environment_prefix: Path,
) -> bool:
    return Path(streamlit_file).resolve().is_relative_to(
        environment_prefix.resolve()
    )

def inspect_launch_environment(
    *,
    checkout_root: Path,
) -> LaunchEnvironmentCheck:
    package_spec = importlib.util.find_spec("deep_research")
    streamlit_spec = importlib.util.find_spec("streamlit")
    package_file = package_spec.origin if package_spec is not None else None
    streamlit_file = streamlit_spec.origin if streamlit_spec is not None else None
    return LaunchEnvironmentCheck(
        package_origin_matches_checkout=(
            package_file is not None
            and package_origin_matches_checkout(
                checkout_root=checkout_root, package_file=package_file
            )
        ),
        streamlit_origin_matches_environment=(
            streamlit_file is not None
            and streamlit_origin_matches_environment(
                streamlit_file=streamlit_file,
                environment_prefix=Path(sys.prefix),
            )
        ),
    )

def main() -> int:
    result = inspect_launch_environment(checkout_root=Path.cwd())
    print(f"package_origin_matches_checkout={str(result.package_origin_matches_checkout).lower()}")
    print(f"streamlit_origin_matches_environment={str(result.streamlit_origin_matches_environment).lower()}")
    return 0 if result.valid else 1
```

`main()` must print only:

```
package_origin_matches_checkout=true
streamlit_origin_matches_environment=true
```

or the corresponding `false` values.

Return 0 only when both booleans are true; otherwise return 1.

The implementation must obtain module origins with `importlib.util.find_spec(module_name)`, not shell executable discovery.

### TDD steps

- [ ] **Step 1: Add package-origin unit tests**

In `tests/test_runtime/test_environment.py`:

```
def test_package_origin_inside_checkout_is_accepted(tmp_path: Path) -> None:
```

Create:

```
<tmp>/src/deep_research/__init__.py
```

Assert `True`.

- [ ] **Step 2: Add cross-worktree rejection coverage**

```
def test_package_origin_from_another_worktree_is_rejected(
    tmp_path: Path,
) -> None:
```

Use:

- checkout root `<tmp>/current`;
- package file `<tmp>/other/src/deep_research/__init__.py`.

Assert `False`.

- [ ] **Step 3: Add interpreter-origin coverage**

```
def test_streamlit_origin_inside_active_environment_is_accepted(
    tmp_path: Path,
) -> None:
```

and:

```
def test_streamlit_origin_outside_active_environment_is_rejected(
    tmp_path: Path,
) -> None:
```

- [ ] **Step 4: Add CLI-output safety coverage**

Patch `inspect_launch_environment(checkout_root=checkout_root)` to return known booleans.

Assert `main()` emits only the two boolean keys and contains neither:

- a filesystem path;
- an environment-variable value;
- a package version.
- [ ] **Step 5: Run tests before implementation**

```
python -m pytest tests/test_runtime/test_environment.py -v
```

**Expected failure:** module does not exist.

- [ ] **Step 6: Implement environment.py**

Use:

- `Path.cwd()` as checkout root in `main()`;
- `importlib.util.find_spec("deep_research")`;
- `importlib.util.find_spec("streamlit")`;
- `sys.prefix`.

If either module origin cannot be resolved, report the corresponding boolean as false.

Do not read `.env`.

- [ ] **Step 7: Run environment tests**

```
python -m pytest tests/test_runtime/test_environment.py -v
```

**Expected:** PASS.

- [ ] **Step 8: Update README setup commands**

Use exactly:

```
python -m venv .venv
python -m pip install -e ".[dev]"
python -m deep_research.runtime.environment
python -m pytest
python -m streamlit run src/deep_research/ui/app.py
```

Add:

```
Use one virtual environment and one editable install per Git worktree.
The environment preflight must pass before live-provider verification.
PYTHONPATH overrides are diagnostic-only and are not the supported launch path.
```

- [ ] **Step 9: Run related tests**

```
python -m pytest tests/test_runtime/test_environment.py tests/test_ui/test_runner.py tests/test_ui/test_app.py -v
```

**Expected:** PASS.

- [ ] **Step 10: Commit**

```
git add src/deep_research/runtime/environment.py
git add tests/test_runtime/test_environment.py
git add README.md
git commit -m "feat: validate local launch environment"
```

**Acceptance criteria:**

- Another worktree's editable package is detected.
- Bare `streamlit` is no longer the documented launch path.
- Preflight output contains only safe booleans.

---

## Task 6: Add the Clean Offline/Live Verification Runbook

**Files:**

- Create: `docs/runbooks/2026-09-11-live-research-verification.md`
- Modify: `README.md`

**Interfaces:**

- `python -m deep_research.runtime.environment`
- existing default pytest marker configuration
- `tests/test_ui/manual_mock_app.py`
- `tests/live/test_deepseek_live.py`
- future `tests/live/test_research_smoke_live.py`

### TDD/documentation steps

- [ ] **Step 1: Write Phase A — isolated environment**

Document:

```
python -m venv .venv
python -m pip install -e ".[dev]"
python -m deep_research.runtime.environment
```

Gate:

- both preflight booleans must be true.
- [ ] **Step 2: Write Phase B — full deterministic offline suite**

Document:

```
python -m pytest
```

State explicitly:

- default tests exclude `live`;
- fresh counts from this run must be recorded;
- the historical `2119 passed, 2 skipped, 1 deselected` result is not post-fix verification.
- [ ] **Step 3: Add deterministic mock UI verification**

Document:

```
python -m streamlit run tests/test_ui/manual_mock_app.py
```

Verify:

- New;
- Running;
- Completed;
- Incomplete with partial report;
- Max iterations;
- Failed/partial.
- [ ] **Step 4: Add real UI startup without research**

Document:

```
python -m streamlit run src/deep_research/ui/app.py
```

Gate:

- UI loads;
- no research session is started;
- no external provider request occurs;
- no NumPy partial-initialization error occurs.

Repeat startup at least three times.

- [ ] **Step 5: Add opt-in provider adapter gate**

Document:

```
RUN_DEEPSEEK_LIVE_TESTS=1 python -m pytest tests/live/test_deepseek_live.py -m live -v
```

State:

- explicit authorization required;
- real provider call;
- may incur cost;
- failure blocks end-to-end live verification.
- [ ] **Step 6: Reserve the one-iteration end-to-end gate for Task 11**

Document the future command exactly:

```
RUN_RESEARCH_LIVE_SMOKE=1 python -m pytest tests/live/test_research_smoke_live.py -m live -v
```

State that it must run only after the adapter smoke passes.

- [ ] **Step 7: Add full-demo final gate**

Only after the bounded smoke passes, run the Streamlit demonstration question:

```
What are the biggest developments in renewable energy storage in 2025–2026, and how might they affect grid reliability?
```

- [ ] **Step 8: Define safe metadata capture**

Allow only:

- session ID;
- status;
- macro iteration count;
- finding count;
- evaluated-source count;
- verified-claim count;
- safe provider failure category;
- retry-attempt count;
- safe HTTP status code;
- provider identifier;
- model alias;
- trace URL when tracing is intentionally enabled;
- dependency versions.

Explicitly prohibit:

- secret values;
- `.env` contents;
- request/response bodies;
- prompts;
- completions;
- authorization headers;
- raw SDK exception text.
- [ ] **Step 9: Link the runbook from README**

Place the link in the Streamlit/live-verification section.

- [ ] **Step 10: Commit**

```
git add docs/runbooks/2026-09-11-live-research-verification.md README.md
git commit -m "docs: add live research verification runbook"
```

**Acceptance criteria:**

- Offline and paid/external verification are separate phases.
- Full demonstration is the final gate.
- No runbook command requires `PYTHONPATH`.

---

# Wave 2 — Reliability Hardening

## Task 7: Add Safe Structured Provider-Failure Diagnostics

**Files:**

- Modify: `src/deep_research/providers/contracts.py`
- Modify: `src/deep_research/providers/retry.py`
- Modify: `src/deep_research/providers/deepseek_provider.py`
- Modify: `src/deep_research/providers/openai_provider.py`
- Modify: `src/deep_research/agents/errors.py`
- Modify: `src/deep_research/agents/researcher.py`
- Modify: `src/deep_research/agents/source_evaluator.py`
- Modify: `src/deep_research/agents/fact_checker.py`
- Modify: `src/deep_research/agents/synthesizer.py`
- Modify: `src/deep_research/agents/critic.py`
- Modify: `src/deep_research/agents/react.py`
- Modify: `tests/test_provider_contracts.py`
- Modify: `tests/test_retry_policy.py`
- Modify: `tests/test_deepseek_provider.py`
- Modify: `tests/test_openai_provider.py`
- Modify: `tests/test_agents/test_errors.py`
- Modify: `tests/test_agents/test_researcher.py`
- Modify: `tests/test_agents/test_source_evaluator.py`
- Modify: `tests/test_agents/test_fact_checker.py`
- Modify: `tests/test_agents/test_synthesizer.py`
- Modify: `tests/test_agents/test_critic.py`
- Modify: `tests/test_agents/test_react.py`
- Modify: `tests/test_evaluation/test_isolation_and_secrets.py`

**Interfaces:**

Add to `providers/contracts.py`:

```
ProviderFailureKind: TypeAlias = Literal[
    "timeout",
    "rate_limit",
    "transport",
    "http",
    "response",
    "output_limit",
    "structured_output",
]

ProviderOperation: TypeAlias = Literal[
    "react_decision",
    "researcher_extraction",
    "source_evaluator_scoring",
    "fact_checker_claim_extraction",
    "fact_checker_claim_verification",
    "synthesizer_report_generation",
    "critic_review",
]

class ProviderFailureDiagnostic(ProviderContract):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        frozen=True,
    )

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    category: ProviderFailureKind
    retryable: bool
    configured_retry_count: int = Field(ge=0)
    attempts_consumed: int = Field(ge=1)
    structured_repair_attempted: bool = False
    http_status_code: int | None = Field(default=None, ge=100, le=599)
```

Add safe retry metadata fields/properties to project-owned `ProviderError` instances:

```
configured_retry_count: int
attempts_consumed: int
failure_diagnostic: ProviderFailureDiagnostic | None
```

Add to `agents/errors.py`:

```
def safe_provider_failure_details(
    error: ProviderError,
    *,
    operation: ProviderOperation,
) -> dict[str, JsonValue]:
    diagnostic = error.failure_diagnostic
    details = (
        diagnostic.model_dump(exclude_none=True)
        if diagnostic is not None
        else {}
    )
    details["operation"] = operation
    details["exception_type"] = type(error).__name__
    return details
```

The resulting `ResearchError.details` keys must be:

```
operation
provider
model
category
retryable
configured_retry_count
attempts_consumed
structured_repair_attempted
http_status_code
exception_type
```

`http_status_code` is omitted when absent.

`exception_type` remains class name only.

### TDD steps

- [ ] **Step 1: Add provider-contract tests to tests/test_provider_contracts.py**

Create tests for:

- accepted diagnostic;
- HTTP status below 100 rejected;
- HTTP status above 599 rejected;
- extra `raw_payload` field rejected;
- blank provider/model rejected.
- [ ] **Step 2: Add retry-attempt tests to tests/test_retry_policy.py**

Add:

- transient failure succeeds on third attempt -> caught intermediate errors show attempts 1 and 2, final success count is 3 through the fake operation counter;
- exhausted transient error -> final error contains:
  - `configured_retry_count == 2`;
  - `attempts_consumed == 3`;

- non-transient error ->:
  - `configured_retry_count == 2`;
  - `attempts_consumed == 1`;
  - no retry sleep.

- [ ] **Step 3: Run retry tests before implementation**

```
python -m pytest tests/test_retry_policy.py -v
```

**Expected failure:** errors do not yet carry retry metadata.

- [ ] **Step 4: Implement retry metadata in with_retries**

On every caught project-owned `ProviderError`, set:

- configured retry count;
- attempts consumed.

Do not change `_is_transient(error)`.
Do not change retry delays.
Do not change retry count semantics.

- [ ] **Step 5: Add DeepSeek mapping tests to tests/test_deepseek_provider.py**

Using existing fake SDK clients, assert final diagnostic categories for:

- timeout -> `timeout`;
- rate limit -> `rate_limit`;
- connection/transport -> `transport`;
- retryable HTTP -> `http`;
- deterministic HTTP -> `http`;
- output limit -> `output_limit`;
- structured output exhausted after repair -> `structured_output`.

Assert provider is `deepseek` and model equals the effective configured model.

- [ ] **Step 6: Add OpenAI mapping tests to tests/test_openai_provider.py**

Cover the same safe categories supported by that adapter.

Assert provider is `openai`.

- [ ] **Step 7: Implement adapter diagnostic attachment**

At the boundary where a project-owned `ProviderError` leaves each adapter, attach `ProviderFailureDiagnostic`.

For structured output failure after the repository's repair attempt, set:

```
structured_repair_attempted=True
category="structured_output"
```

Do not include provider response text.

- [ ] **Step 8: Add safe_provider_failure_details tests to tests/test_agents/test_errors.py**

Construct a `ProviderError` with a diagnostic and assert the exact safe keys.

Also create an exception containing sentinel text:

```
SECRET_TOKEN_SHOULD_NOT_APPEAR
```

Assert the sentinel is absent from the result.

- [ ] **Step 9: Replace ad hoc provider details in Researcher**

In `src/deep_research/agents/researcher.py`, use:

```
safe_provider_failure_details(
    error,
    operation="researcher_extraction",
)
```

Update `tests/test_agents/test_researcher.py` to assert:

- exact operation;
- safe category;
- retry counts;
- sentinel provider text absent.
- [ ] **Step 10: Replace ad hoc provider details in Source Evaluator**

Use operation:

```
source_evaluator_scoring
```

Update `tests/test_agents/test_source_evaluator.py`.

- [ ] **Step 11: Replace both Fact Checker provider paths**

For claim extraction:

```
fact_checker_claim_extraction
```

For `claim_verification_provider_error(error)`:

```
fact_checker_claim_verification
```

Update `tests/test_agents/test_fact_checker.py`.

Explicitly assert that the exact error type remains:

```
fact_checker_verification_provider_error
```

- [ ] **Step 12: Replace Synthesizer provider details**

Use:

```
synthesizer_report_generation
```

Update `tests/test_agents/test_synthesizer.py`.

- [ ] **Step 13: Replace Critic provider details**

Use:

```
critic_review
```

Update `tests/test_agents/test_critic.py`.

- [ ] **Step 14: Enrich generic ReAct provider errors**

In `src/deep_research/agents/react.py`, use operation:

```
react_decision
```

Update `tests/test_agents/test_react.py`.

Do not change whether `propagate_provider_errors` is true or false.

- [ ] **Step 15: Add secret/isolation regression to tests/test_evaluation/test_isolation_and_secrets.py**

Serialize representative structured provider diagnostics containing safe fields.

Assert:

- secret sentinel absent;
- raw response marker absent;
- raw URL query secret absent;
- diagnostic category and attempt count remain present.
- [ ] **Step 16: Run the exact provider/agent/security tests**

```
python -m pytest tests/test_provider_contracts.py -v
python -m pytest tests/test_retry_policy.py -v
python -m pytest tests/test_deepseek_provider.py tests/test_openai_provider.py -v
python -m pytest tests/test_agents/test_errors.py -v
python -m pytest tests/test_agents/test_researcher.py -v
python -m pytest tests/test_agents/test_source_evaluator.py -v
python -m pytest tests/test_agents/test_fact_checker.py -v
python -m pytest tests/test_agents/test_synthesizer.py -v
python -m pytest tests/test_agents/test_critic.py -v
python -m pytest tests/test_agents/test_react.py -v
python -m pytest tests/test_evaluation/test_isolation_and_secrets.py -v
```

**Expected:** PASS.

- [ ] **Step 17: Commit**

```
git add src/deep_research/providers/contracts.py
git add src/deep_research/providers/retry.py
git add src/deep_research/providers/deepseek_provider.py
git add src/deep_research/providers/openai_provider.py
git add src/deep_research/agents/errors.py
git add src/deep_research/agents/researcher.py
git add src/deep_research/agents/source_evaluator.py
git add src/deep_research/agents/fact_checker.py
git add src/deep_research/agents/synthesizer.py
git add src/deep_research/agents/critic.py
git add src/deep_research/agents/react.py
git add tests/test_provider_contracts.py tests/test_retry_policy.py
git add tests/test_deepseek_provider.py tests/test_openai_provider.py
git add tests/test_agents/test_errors.py
git add tests/test_agents/test_researcher.py
git add tests/test_agents/test_source_evaluator.py
git add tests/test_agents/test_fact_checker.py
git add tests/test_agents/test_synthesizer.py
git add tests/test_agents/test_critic.py tests/test_agents/test_react.py
git add tests/test_evaluation/test_isolation_and_secrets.py
git commit -m "feat: add safe provider failure diagnostics"
```

**Acceptance criteria:**

- Failure stage/category/retry metadata is actionable.
- Existing retry ownership remains unchanged.
- Raw provider text never enters `ResearchError.details`.

---

## Task 8: Stop Unnecessary Refinement After Essential Provider Degradation

**Files:**

- Modify: `src/deep_research/graph/state.py`
- Modify: `tests/test_graph/test_state.py`
- Modify: `tests/test_graph/test_nodes.py`
- Modify: `tests/test_graph/test_session.py`

**Interfaces:**

Add:

```
def is_provider_degraded(state: ResearchState) -> bool:
    return any(
        error.error_type in QUALITY_BLOCKING_PROVIDER_ERROR_TYPES
        for error in state.errors
    )
```

Add route reason:

```
"provider_degraded": (
    "An essential provider operation failed, so another refinement pass "
    "would not be started."
)
```

Map:

```
"provider_degraded": "incomplete"
```

Update `graph_route(state)` ordering:

```
def graph_route(state: ResearchState) -> tuple[str, str]:
    if is_halted(state):
        return ROUTE_END, "halted"

    critique = state.critique
    if critique is None:
        return ROUTE_END, "missing_critique"

    if not critique.should_continue:
        return ROUTE_END, "critique_satisfied"

    if state.iteration >= state.max_iterations:
        return ROUTE_END, "max_iterations_reached"

    if is_provider_degraded(state):
        return ROUTE_END, "provider_degraded"

    return ROUTE_REFINE, "refinement_requested"
```

This intentionally allows the current pass to finish and does not skip downstream partial-artifact work.

### TDD steps

- [ ] **Step 1: Add route-unit test**

In `tests/test_graph/test_state.py`:

```
def test_provider_degradation_prevents_another_refinement_pass() -> None:
    state = fake_research_state(
        raw_findings=[fake_finding()],
        report="# Partial report",
        critique=fake_critique(should_continue=True),
        iteration=1,
        max_iterations=3,
        errors=[
            ResearchError(
                error_type="fact_checker_verification_provider_error",
                source="agent.fact_checker",
                message="Claim verification failed.",
                recoverable=False,
            )
        ],
    )

    assert graph_route(state) == (ROUTE_END, "provider_degraded")
    assert graph_status(state) == "incomplete"
```

- [ ] **Step 2: Add healthy refinement compatibility test**

Assert the same critique with no provider blocker still returns:

```
(ROUTE_REFINE, "refinement_requested")
```

- [ ] **Step 3: Update route-vocabulary test**

Include `provider_degraded` in `GRAPH_ROUTES`.

- [ ] **Step 4: Add Critic-node event test to tests/test_graph/test_nodes.py**

Create a state containing:

- provider blocker;
- Critic result requesting refinement;
- remaining budget.

Run `critic_node(state)`.

Assert the emitted route event contains:

```
destination=end
reason=provider_degraded
```

- [ ] **Step 5: Add full graph-session call-count test to tests/test_graph/test_session.py**

Use `FakeAgent` from `tests/graph_fakes.py`.

Script:

- initial Researcher produces findings plus a quality-blocking provider error;
- Synthesizer produces a partial report;
- Critic requests another pass.

Assert:

- Researcher `len(calls) == 1`;
- Synthesizer ran and report survives;
- no refine pass occurred;
- final status is `incomplete`.
- [ ] **Step 6: Run before implementation**

```
python -m pytest tests/test_graph/test_state.py tests/test_graph/test_nodes.py tests/test_graph/test_session.py -v
```

**Expected failure:** current graph accepts Critic refinement request while budget remains.

- [ ] **Step 7: Implement the route reason and predicate**

Modify only `graph/state.py`.

No new provider retries.
No node-level provider skipping.

- [ ] **Step 8: Run all graph tests**

```
python -m pytest tests/test_graph -v
```

**Expected:** PASS.

- [ ] **Step 9: Commit**

```
git add src/deep_research/graph/state.py
git add tests/test_graph/test_state.py tests/test_graph/test_nodes.py tests/test_graph/test_session.py
git commit -m "fix: stop refinement after provider degradation"
```

**Acceptance criteria:**

- Current-pass partial work is preserved.
- No additional macro research pass begins after an essential provider blocker.
- Healthy low-score sessions can still refine.

---

## Task 9: Add Cooperative Cancellation for Paid/Live Work

**Files:**

- Create: `src/deep_research/runtime/cancellation.py`
- Modify: `src/deep_research/main.py`
- Modify: `src/deep_research/graph/nodes.py`
- Modify: `src/deep_research/agents/react.py`
- Modify: `src/deep_research/ui/runner.py`
- Modify: `src/deep_research/ui/components.py`
- Create: `tests/test_runtime/test_cancellation.py`
- Modify: `tests/test_agents/test_react.py`
- Modify: `tests/test_graph/test_nodes.py`
- Modify: `tests/test_ui/test_runner.py`
- Modify: `tests/test_ui/test_app.py`

**Interfaces:**

Create:

```
from threading import Event

class ResearchCancelledError(Exception):
    pass

class ResearchCancellation:
    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise ResearchCancelledError()
```

Add to `run_research(question, max_iterations=1)`:

```
cancellation: ResearchCancellation | None = None
```

Add to controller:

```
def cancel(self, session_id: str) -> UiSessionSnapshot:
    session = self._active_sessions[session_id]
    session.cancellation.cancel()
    return self.snapshot(session_id)
```

Extend `_ActiveSession` with:

```
cancellation: ResearchCancellation
```

For this implementation wave, public cancellation ends as:

```
status=incomplete
error_type=research_cancelled
```

Do not add a new public `cancelled` status yet.

### TDD steps

- [ ] **Step 1: Add primitive tests to tests/test_runtime/test_cancellation.py**

Test:

- starts uncancelled;
- `cancel()` is idempotent;
- `raise_if_cancelled()` raises only after cancellation.
- [ ] **Step 2: Add ReAct cancellation test**

In `tests/test_agents/test_react.py`, add a cancellation-aware test where cancellation is set after the first completed iteration.

Assert:

- second `decide()` callback is not called;
- no second tool is executed.

Add an optional parameter to `run_react_loop`:

```
cancellation: ResearchCancellation | None = None
```

Check at:

- top of each iteration;
- immediately before `tool.execute(tool_call)`.
- [ ] **Step 3: Add graph-node cancellation test**

In `tests/test_graph/test_nodes.py`, inject cancellation into the node factory interface:

```
def agent_node(
    agent: ResearchAgent,
    *,
    node_name: str | None = None,
    cancellation: ResearchCancellation | None = None,
) -> GraphNode:
```

Before `agent.run(started)`, call:

```
cancellation.raise_if_cancelled()
```

Translate `ResearchCancelledError` into a controlled state update with:

```
error_type=research_cancelled
recoverable=False
```

Do not add it to `HALTING_ERROR_TYPES`; final status will be `incomplete`.

- [ ] **Step 4: Add controller cancellation coverage**

In `tests/test_ui/test_runner.py`:

```
def test_cancel_marks_running_session_for_cooperative_stop(
    tmp_path: Path,
) -> None:
```

Assert:

- `controller.cancel(session_id)` sets the session token;
- repeated `cancel(session_id)` is idempotent;
- partial events already published remain present;
- final terminal status becomes `incomplete`;
- `research_cancelled` is present in errors.
- [ ] **Step 5: Propagate token from controller into run_research_sync**

`LocalResearchController._run` must call runner with:

```
cancellation=session.cancellation
```

Update test fakes that define explicit runner call signatures to accept and record this argument.

- [ ] **Step 6: Propagate cancellation through run_research(question, max_iterations=1)**

Pass the token into graph construction/invocation so node wrappers and ReAct loops share the same object.

Use one token per research session.

- [ ] **Step 7: Add UI cancel action**

In `src/deep_research/ui/components.py`, render a `Cancel research` button only for:

- `snapshot.status == "running"`;
- controller owns the active session.

Click calls:

```
controller.cancel(snapshot.session_id)
```

Disable or remove the control after the request is registered.

- [ ] **Step 8: Add AppTest coverage in tests/test_ui/test_app.py**

Assert:

- running owned session shows `Cancel research`;
- terminal session does not;
- clicking invokes the fake controller's cancellation method exactly once.
- [ ] **Step 9: Run exact cancellation coverage**

```
python -m pytest tests/test_runtime/test_cancellation.py -v
python -m pytest tests/test_agents/test_react.py -v
python -m pytest tests/test_graph/test_nodes.py -v
python -m pytest tests/test_ui/test_runner.py tests/test_ui/test_app.py -v
```

**Expected:** PASS.

- [ ] **Step 10: Commit**

```
git add src/deep_research/runtime/cancellation.py
git add src/deep_research/main.py
git add src/deep_research/graph/nodes.py
git add src/deep_research/agents/react.py
git add src/deep_research/ui/runner.py src/deep_research/ui/components.py
git add tests/test_runtime/test_cancellation.py
git add tests/test_agents/test_react.py
git add tests/test_graph/test_nodes.py
git add tests/test_ui/test_runner.py tests/test_ui/test_app.py
git commit -m "feat: add cooperative research cancellation"
```

**Acceptance criteria:**

- No thread is force-killed.
- New provider/tool work stops at checked safe boundaries.
- Partial progress/report state survives.
- Public status remains backward-compatible as `incomplete`.

---

# Wave 3 — Reproducibility and Explicit Live Gates

## Task 10: Establish a Tested Dependency Constraint Set

**Files:**

- Create after verification: `constraints/dev-tested.txt`
- Modify: `README.md`
- Modify: `docs/runbooks/2026-09-11-live-research-verification.md`
- Do not change: NumPy dependency bounds in `pyproject.toml` as an incident response.

**Interfaces:**

The constraints file is a reproducibility aid used with:

```
python -m pip install -c constraints/dev-tested.txt -e ".[dev]"
```

It does not replace `pyproject.toml`.

### Verification-first steps

- [ ] **Step 1: Complete Tasks 1–9 and verify all their offline tests first**

Do not create `constraints/dev-tested.txt` before this condition is satisfied.

- [ ] **Step 2: Create a fresh isolated worktree virtual environment**

Use:

```
python -m venv .venv
python -m pip install -e ".[dev]"
python -m deep_research.runtime.environment
```

Both environment booleans must be true.

- [ ] **Step 3: Record dependency versions from the passing environment**

Record exact installed versions for:

- Python;
- Streamlit;
- NumPy;
- Pandas;
- ChromaDB;
- OpenAI SDK;
- Pydantic;
- LangGraph;
- LangGraph checkpoint;
- Tavily client;
- pytest;
- pytest-asyncio.

No version is selected in advance by this plan.

- [ ] **Step 4: Run full deterministic verification**

```
python -m pytest
```

Must pass.

- [ ] **Step 5: Run real Streamlit startup without research three times**

```
python -m streamlit run src/deep_research/ui/app.py
```

Each startup must be clean.

- [ ] **Step 6: Write constraints/dev-tested.txt from the exact verified environment**

Header:

```
# Tested development reproduction set.
# Versions below were captured only after the full offline suite and
# repeated Streamlit startup verification passed in an isolated worktree.
```

List the verified packages and versions.

Do not label any one version as the NumPy fix.

- [ ] **Step 7: Verify the constraints in a second fresh virtual environment**

```
python -m venv .venv-constraints-check
python -m pip install -c constraints/dev-tested.txt -e ".[dev]"
python -m pytest
```

Must pass.

- [ ] **Step 8: Update README and runbook**

Document:

- ordinary development install;
- optional tested-reproduction install using constraints;
- constraints do not change supported dependency floors.
- [ ] **Step 9: Commit**

```
git add constraints/dev-tested.txt README.md
git add docs/runbooks/2026-09-11-live-research-verification.md
git commit -m "build: record tested development dependency set"
```

**Acceptance criteria:**

- Constraint versions come from actual verification.
- No incident-specific NumPy pin is invented.
- Fresh constrained install passes the offline suite.

---

## Task 11: Add a Small Opt-In End-to-End Live Research Smoke

**Files:**

- Create: `tests/live/test_research_smoke_live.py`
- Modify: `docs/runbooks/2026-09-11-live-research-verification.md`
- Use existing: `tests/live/test_deepseek_live.py`

**Interfaces:**

Opt-in variable:

```
RUN_RESEARCH_LIVE_SMOKE=1
```

Test marker:

```
@pytest.mark.live
@pytest.mark.asyncio
```

Research entry point:

```
await run_research(
    question=question,
    max_iterations=1,
)
```

### TDD steps

- [ ] **Step 1: Create the gated test**

Use:

```
@pytest.mark.live
@pytest.mark.asyncio
async def test_one_iteration_research_smoke_live() -> None:
    if os.getenv("RUN_RESEARCH_LIVE_SMOKE") != "1":
        pytest.skip("set RUN_RESEARCH_LIVE_SMOKE=1 to opt in")

    if not os.getenv("DEEPSEEK_API_KEY", "").strip():
        pytest.skip("DEEPSEEK_API_KEY is required")
    if not os.getenv("TAVILY_API_KEY", "").strip():
        pytest.skip("TAVILY_API_KEY is required")
```

Never print values.

- [ ] **Step 2: Use a narrow bounded question**

Use:

```
What is one major publicly documented grid-scale battery storage development announced in 2026?
```

Set:

```
max_iterations=1
```

- [ ] **Step 3: Assert system invariants, not a specific external answer**

Assert:

```
assert outcome.status in {
    "completed",
    "max_iterations",
    "incomplete",
    "failed",
}
```

Then:

```
if outcome.status == "completed":
    assert research_result_is_complete(outcome.state)
```

And:

```
if has_quality_blocking_error(outcome.state):
    assert outcome.status == "incomplete"
```

Also assert:

- session ID non-blank;
- if `outcome.report` is present, it is non-blank Markdown;
- no `ResearchError.details` value contains known secret environment values;
- no raw provider response field exists in error details.

Do not assert a particular company, technology, number, citation URL, or factual answer.

- [ ] **Step 4: Verify default test behavior remains offline**

Run without opt-in:

```
python -m pytest tests/live/test_research_smoke_live.py -v
```

**Expected:** SKIPPED without external request.

- [ ] **Step 5: Run the full default suite**

```
python -m pytest
```

The new live smoke must be excluded/skipped.

- [ ] **Step 6: Add runbook command ordering**

The runbook must state:

First:

```
RUN_DEEPSEEK_LIVE_TESTS=1 python -m pytest tests/live/test_deepseek_live.py -m live -v
```

Only if that succeeds:

```
RUN_RESEARCH_LIVE_SMOKE=1 python -m pytest tests/live/test_research_smoke_live.py -m live -v
```

Only after both bounded gates pass may the full Streamlit demonstration run.

- [ ] **Step 7: Commit**

```
git add tests/live/test_research_smoke_live.py
git add docs/runbooks/2026-09-11-live-research-verification.md
git commit -m "test: add opt-in end-to-end live research smoke"
```

**Acceptance criteria:**

- No external call occurs without explicit opt-in.
- Smoke exercises provider + tools/search + graph + synthesis.
- Assertions test reliability semantics rather than volatile web content.

---

# Task 12: Final Verification and Release Gate

**Files:**

- No production file is expected to change during this task.
- Any change discovered here must be made in the task that owns the affected behavior, then that task's tests and this final gate must be rerun.

### Offline verification

- [ ] **Step 1: Verify execution isolation**

Run:

```
python -m deep_research.runtime.environment
```

Required:

```
package_origin_matches_checkout=true
streamlit_origin_matches_environment=true
```

- [ ] **Step 2: Run graph semantics**

```
python -m pytest tests/test_graph/test_state.py -v
python -m pytest tests/test_graph/test_nodes.py -v
python -m pytest tests/test_graph/test_session.py -v
```

- [ ] **Step 3: Run runtime/status consumers**

```
python -m pytest tests/test_runtime/test_outcome.py -v
python -m pytest tests/test_runtime/test_environment.py -v
python -m pytest tests/test_runtime/test_cancellation.py -v
python -m pytest tests/test_cli/test_render.py tests/test_cli/test_entrypoint.py -v
python -m pytest tests/test_api/test_sessions.py tests/test_api/test_stream_and_artifacts.py -v
python -m pytest tests/test_ui/test_runner.py tests/test_ui/test_history.py tests/test_ui/test_app.py -v
```

- [ ] **Step 4: Run provider and redaction coverage**

```
python -m pytest tests/test_provider_contracts.py tests/test_retry_policy.py -v
python -m pytest tests/test_deepseek_provider.py tests/test_openai_provider.py -v
python -m pytest tests/test_agents/test_errors.py -v
python -m pytest tests/test_agents/test_researcher.py -v
python -m pytest tests/test_agents/test_source_evaluator.py -v
python -m pytest tests/test_agents/test_fact_checker.py -v
python -m pytest tests/test_agents/test_synthesizer.py -v
python -m pytest tests/test_agents/test_critic.py tests/test_agents/test_react.py -v
python -m pytest tests/test_evaluation/test_isolation_and_secrets.py -v
```

- [ ] **Step 5: Run full offline suite**

```
python -m pytest
```

Record fresh:

- passed;
- skipped;
- deselected;
- warnings;
- exit code.

Do not reuse historical counts.

### UI verification

- [ ] **Step 6: Run deterministic mock UI**

```
python -m streamlit run tests/test_ui/manual_mock_app.py
```

Manually verify:

- valid completed fixture -> Completed;
- provider-degraded/evidence-empty fixture -> Incomplete;
- incomplete fixture retains report;
- max-iteration fixture remains distinct;
- failed fixture remains distinct.
- [ ] **Step 7: Run real UI without research**

```
python -m streamlit run src/deep_research/ui/app.py
```

Repeat three clean startups.

Required:

- no NumPy partial-initialization failure;
- no research session begins automatically;
- source/interpreter preflight remains valid.

### External-provider verification

- [ ] **Step 8: Stop here unless paid/external checks are explicitly authorized**

The offline implementation is not permission to spend money.

- [ ] **Step 9: When explicitly authorized, run provider adapter smoke**

```
RUN_DEEPSEEK_LIVE_TESTS=1 python -m pytest tests/live/test_deepseek_live.py -m live -v
```

Do not continue if it fails.

- [ ] **Step 10: When separately authorized, run one-iteration end-to-end smoke**

```
RUN_RESEARCH_LIVE_SMOKE=1 python -m pytest tests/live/test_research_smoke_live.py -m live -v
```

Acceptance:

- terminal semantics truthful;
- provider degradation produces `incomplete`;
- safe partial report retained where available;
- diagnostics remain redacted.
- [ ] **Step 11: Only after bounded gates pass, run the original full demonstration question through Streamlit**

Question:

```
What are the biggest developments in renewable energy storage in 2025–2026, and how might they affect grid reliability?
```

Capture only runbook-approved safe metadata.

- [ ] **Step 12: Verify the observed historical failure combination now classifies correctly**

If the full run reproduces:

- Researcher provider failure;
- Fact Checker extraction or verification provider failure;
- Synthesizer provider failure;
- no substantive evidence;

then required behavior is:

- `status == "incomplete"`;
- fallback report remains available;
- UI does not display Completed;
- no additional macro refinement begins after provider degradation;
- no raw provider content is shown.
- [ ] **Step 13: Verify commit boundaries**

Expected implementation commits:

```
fix: classify degraded research as incomplete
test: keep incomplete status consistent across surfaces
fix: use numeric Streamlit fragment interval
test: stress live UI worker refresh lifecycle
feat: validate local launch environment
docs: add live research verification runbook
feat: add safe provider failure diagnostics
fix: stop refinement after provider degradation
feat: add cooperative research cancellation
build: record tested development dependency set
test: add opt-in end-to-end live research smoke
```

Do not create an empty final-verification commit.

---

## Dependency and Ordering Notes

- Task 1 is the first implementation task because truthful status is the primary correctness defect exposed by the live run.
- Task 2 immediately follows Task 1 so no front end can silently diverge from canonical graph semantics.
- Task 3 removes the concrete Streamlit string-duration import trigger before any dependency changes are considered.
- Task 4 adds deterministic regression pressure around the worker/fragment lifecycle without claiming the original NumPy race has been reproduced.
- Tasks 5 and 6 establish the supported launch and verification contract before another live demonstration is attempted.
- Tasks 1–6 are the minimum trustworthy-live-run wave. Do not run the full paid demonstration before they pass offline.
- Task 7 must precede new live debugging because provider failures currently lack enough safe structured context to distinguish several failure classes.
- Task 8 must reuse `QUALITY_BLOCKING_PROVIDER_ERROR_TYPES`; it must not create a second degradation taxonomy.
- Task 8 prevents additional macro refinement only. It deliberately does not assume one failed provider operation means every later within-pass call must be skipped.
- Task 9 adds cancellation after terminal/status semantics are already trustworthy.
- Task 10 is deliberately late. Dependency constraints are evidence from a verified environment, not a speculative first fix.
- Task 11 stays outside default verification and requires separate authorization.
- Task 12 is an evidence gate, not a replacement for each task's red-green TDD cycle.

---

## Rollback Considerations

### Terminal completeness

Task 1 changes classification only, not graph execution order. If incompatibility appears, revert the completeness commit without removing fallback report generation or changing agent provider error handling.

### Cross-surface presentation

Task 2 should contain presentation/test changes only. Revert it independently without changing canonical graph classification.

### Fragment mitigation

Task 3 is intentionally one production-line change. Revert independently if a verified Streamlit compatibility problem appears. Do not replace it with NumPy preload without a reproducer.

### Environment preflight

The environment checker is additive. If platform-specific path behavior is discovered, correct the checker rather than restoring bare executable documentation.

### Provider diagnostics

Diagnostics must not alter retry policy. Any change in retry count, delay, or transient classification is a regression and reason to revert Task 7.

### Provider-degraded routing

Task 8 must only block another macro refinement. If useful current-pass work is lost, revert or narrow that routing change while retaining Task 1's truthful terminal classification.

### Cancellation

Cancellation is additive and cooperative. Normal uncancelled sessions must remain behaviorally identical.

### Dependency constraints

`constraints/dev-tested.txt` is not a supported-version declaration. Remove or split it if verification proves environment-specific; do not opportunistically narrow `pyproject.toml`.

### Live smoke

A flaky external smoke must remain opt-in. Do not make it a default merge gate to compensate for external-provider instability.

---

## Final Acceptance Checklist

- [ ] **Main checkout remained untouched.**
- [ ] **Implementation used one worktree, one venv, one editable install.**
- [ ] **Supported commands use python -m.**
- [ ] **Package origin preflight rejects another worktree.**
- [ ] **fact_checker_verification_provider_error is in the canonical quality-blocking provider set.**
- [ ] **Researcher extraction provider failure blocks completed.**
- [ ] **Source-evaluator scoring provider failure blocks completed.**
- [ ] **Fact-check extraction provider failure blocks completed.**
- [ ] **Fact-check verification provider failure blocks completed.**
- [ ] **Synthesizer provider failure blocks completed.**
- [ ] **Critic provider failure blocks completed.**
- [ ] **synthesizer_no_evidence blocks completed.**
- [ ] **Report existence alone does not count as evidence.**
- [ ] **Valid evidence-backed result with one recoverable tool failure can still be completed.**
- [ ] **Valid evidence-backed budget exhaustion remains max_iterations.**
- [ ] **Degraded budget exhaustion becomes incomplete.**
- [ ] **Fatal graph failure remains failed.**
- [ ] **Partial report is retained when status is incomplete.**
- [ ] **Runtime, CLI, API, UI, and history expose the same status.**
- [ ] **UI no longer labels an incomplete partial report Completed.**
- [ ] **st.fragment uses numeric run_every=2.0.**
- [ ] **No NumPy preload is added.**
- [ ] **No incident-specific NumPy pin is added without verification evidence.**
- [ ] **Worker/fragment stress tests require no external provider.**
- [ ] **Provider retry ownership remains in the provider layer.**
- [ ] **Provider diagnostics identify stage/category/retry metadata.**
- [ ] **Provider diagnostics never expose raw provider text or secrets.**
- [ ] **Provider-degraded sessions do not start another macro refinement pass.**
- [ ] **Deterministic partial work survives provider degradation.**
- [ ] **Cancellation is cooperative.**
- [ ] **Cancellation does not terminate Python threads forcibly.**
- [ ] **Cancellation preserves partial state.**
- [ ] **Default python -m pytest makes no paid/external calls.**
- [ ] **Existing provider adapter smoke remains opt-in.**
- [ ] **New end-to-end research smoke remains separately opt-in.**
- [ ] **Adapter smoke runs before end-to-end live smoke.**
- [ ] **End-to-end bounded smoke runs before the full Streamlit demonstration.**
- [ ] **Full demonstration captures only safe metadata.**
- [ ] **Full offline suite passes freshly after implementation.**
- [ ] **Every implementation task ends in a focused reviewable commit.**

## Validation gaps

The only intentionally unresolved implementation input is the exact dependency-version set for `constraints/dev-tested.txt`. Those versions must be captured later from a fresh isolated environment that passes the complete offline suite and repeated real Streamlit startup verification. This plan intentionally does not invent or preselect dependency pins.
