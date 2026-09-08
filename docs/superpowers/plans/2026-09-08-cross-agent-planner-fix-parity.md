# Cross-Agent Planner-Fix Parity and Controlled Evaluation Campaign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden every non-Planner agent against the transferable failure modes discovered during the Planner campaign, without regressing the now-working Planner, and use controlled evaluation evidence to decide whether any agent needs an operation-specific output-token budget or an agent-specific prompt/behavior repair.

**Architecture:** Move the confirmed empty-optional-field `ReActDecision -> ReActStep` normalization from the Planner-local provider wrapper to the shared ReAct boundary, then prove Researcher, Fact Checker, and Critic inherit the fix while Source Evaluator and Synthesizer remain unaffected because they do not run ReAct. Preserve each non-Planner agent's intentional graceful/partial-result semantics, but enrich caught provider failures with a shared, bounded, provider-content-free diagnostic snapshot so output-limit/schema/transport/HTTP failures remain distinguishable even when an agent returns a fallback result instead of raising. After offline TDD is green, run one immutable controlled baseline per non-Planner agent, diagnose from typed artifacts, and add an operation-specific max-token override only when an actual target-side `output_limit` is observed for that operation; never raise the global 4096 cap as a speculative fix.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest + pytest-asyncio, Ruff, Git worktrees, PowerShell, DeepSeek V4 Flash, LangSmith controlled evaluation, the existing fake-driven dependency bundles, and the existing `StructuredCompleter.complete_structured(..., max_tokens=...)` per-call override.

**Spec / design basis:**
- `docs/superpowers/2026-08-25-planner-evaluation-fix-log.md`
- `docs/superpowers/specs/2026-08-25-planner-provider-failure-remediation-design.md`
- `docs/superpowers/specs/2026-08-24-agent-evaluation-improvement-workflow-design.md`
- `docs/superpowers/plans/2026-08-24-planner-controlled-evaluation-improvement.md`
- Current `obra/superpowers` `brainstorming`, `writing-plans`, and `subagent-driven-development` skills as of 2026-09-08.

## Brainstorming Outcome

This is an **architectural** change, not a bounded patch: the confirmed bug sits on a shared runtime boundary used by multiple agents, while provider-diagnostic and token-budget behavior crosses provider, runtime, evaluation, and agent-specific fallback interfaces.

Three approaches were considered:

1. **Copy the Planner fixes into every agent.** Rejected. A Planner-local `_DecisionNormalizingCompleter` copied into Researcher, Fact Checker, and Critic would duplicate the same workaround three times, leave the real shared contract mismatch in place, and create provider-identity/wrapper-lifecycle complexity in every agent. Copying Planner prompt rules into the other agents is also incorrect because those agents have different jobs.
2. **Fix shared boundaries once, preserve agent-specific semantics, and make expensive/behavioral changes evidence-gated. Recommended.** Normalize the unused ReAct decision fields at the shared `ReActStep` construction boundary; keep each agent's current fallback/partial-result policy; preserve typed provider diagnosis in a safe shared snapshot; then use controlled experiments to justify any output-budget or prompt changes one operation/root cause at a time.
3. **Generalize all model-call policy into one new global operation-budget/failure framework immediately.** Rejected for YAGNI. The Planner campaign proved that one operation-specific budget is useful, not that every operation needs its own override. Pre-adding six token knobs would expand configuration and test surface without evidence.

### Transferability matrix from the Planner fix log and current `main`

| Planner lesson | Researcher | Source Evaluator | Fact Checker | Synthesizer | Critic | Plan decision |
| --- | --- | --- | --- | --- | --- | --- |
| RC-A: unused `ReActDecision` field may be `""`, but `ReActStep` requires non-blank optional strings | **Applies**: custom per-subtopic ReAct loop | **Does not apply on current main**: no ReAct loop | **Applies**: custom per-claim ReAct loop | **Does not apply on current main**: no ReAct loop | **Applies**: spot-check ReAct loop | Fix once in shared `react.py`; remove Planner-local workaround after parity tests |
| RC-B: Planner searched despite not needing search | Different role; do not copy wording | N/A | Different role; often must search | N/A | Different role; spot-check may search | Diagnose only if an agent's controlled trajectory shows an analogous unnecessary/prohibited call |
| RC-C: Planner produced priorities out of order | Planner-output contract only | N/A | N/A | N/A | N/A | No transplant |
| RC-D: Planner ambiguous-scope wording missed Planner-specific rubric terms | Planner rubric only | N/A | N/A | N/A | N/A | No transplant |
| Repo-owned transient retry policy | Already provider-wide | Already provider-wide | Already provider-wide | Already provider-wide | Already provider-wide | Verify effective config; no new retry implementation |
| Typed provider/output-limit/schema telemetry | Provider layer already supports it, but local catches collapse it to `exception_type` | Same | Same | Same | Same | Add safe diagnostic projection while preserving current fallback semantics |
| Planner-final per-call max-token override | Candidate only if extraction actually hits output limit | Candidate only if scoring hits output limit | Candidate only if extraction/verdict hits output limit | **Highest-risk candidate** because report draft is long, but still evidence-gated | Candidate only if review hits output limit | Never raise global 4096; add one operation-specific field only after typed evidence |
| Judge-side typed diagnostics / URL preservation | Already shared evaluation behavior | Already shared | Already shared | Already shared | Already shared | No agent-specific change |

**Important correction to the historical fix log:** RC-A's original follow-up text named all five sibling agents. On current `main`, Source Evaluator and Synthesizer explicitly override `run()` and perform no ReAct loop, so they are not exposed to the `ReActDecision -> ReActStep` blank-field crash. The transferable RC-A production fix therefore targets Researcher, Fact Checker, and Critic plus Planner's local-workaround cleanup.

## Success Criteria

The campaign is complete only when all of the following are true:

- The shared ReAct loop safely converts an unused `tool_name=""` or `final_answer=""` to `None` before `ReActStep` validation.
- The Planner's two RC-A regression tests still pass after its `_DecisionNormalizingCompleter` is removed; provider identity remains unchanged before, during, and after a Planner run.
- Researcher, Fact Checker, and Critic each have an agent-level regression proving the shared fix reaches their custom ReAct loop.
- Source Evaluator and Synthesizer have explicit tests/documentation proving they do not run ReAct and therefore do not need the RC-A workaround.
- Every caught non-Planner `ProviderError` records a bounded typed diagnostic kind without serializing `str(error)`, raw finish values, provider output, prompts, request payloads, URLs derived from secrets, or validation input values.
- Existing fallback semantics remain intact: Researcher keeps prior findings and stops later subtopics on a provider failure; Fact Checker keeps prior claims and uses `insufficient_evidence` where designed; Source Evaluator emits low-confidence fallback rows; Synthesizer emits the evidence-only report skeleton; Critic emits its existing fallback critique and routing decision.
- `llm.max_tokens` remains 4096 throughout the campaign.
- No non-Planner operation-specific token-budget field is added unless a controlled target repetition reports a typed target-side `output_limit` for that exact operation and the repair amendment documents the evidence.
- Every agent-specific source/prompt change is tied to one root-cause ID, has RED/GREEN offline evidence, a focused three-repetition controlled retest, and a reviewer gate before a full nine-repetition validation.
- No controlled case, gate, rubric, evaluator, threshold, judge prompt, dependency script, or weight is changed to make an agent pass. A demonstrated harness defect stops agent tuning and moves to a separate approved harness plan.
- The final full tracked offline suite and Ruff are green, and the final whole-branch review has no unresolved load-bearing findings.

## Global Constraints

- Treat Planner behavior on `main` after merged PR #19 (`bc67620666bbc41c516556d45602de6dd00d7102`) as the known-good reference. The execution base must contain that merge commit as an ancestor.
- Do not re-tune Planner quality, prompts, scoring, cases, or budgets in this campaign. Planner changes are limited to deleting its now-redundant RC-A local normalization wrapper after the shared fix is proven equivalent.
- Do not copy Planner RC-B/RC-C/RC-D prompt text into sibling agents without a separate agent-specific controlled failure proving the analogous instruction ambiguity.
- Preserve `AgentRuntimeConfig.planner_final_max_tokens` and `AGENTS_PLANNER_FINAL_MAX_TOKENS` exactly as they work now.
- Preserve the global `llm.max_tokens: 4096`; ReAct decisions and judges stay at the global cap in this plan.
- Preserve the repo-owned retry policy. Verify the *effective* retry count/backoff through configuration before provider runs because the Planner campaign discovered that a repository `.env` can override `config.yaml`. Never print the value of any secret while doing this.
- Use an isolated branch/worktree: branch `codex/cross-agent-planner-fix-parity`, worktree `.worktrees/cross-agent-planner-fix-parity`.
- Use the current Superpowers SDD workspace convention: initialize this plan's workspace with `scripts/sdd-workspace` from the installed/current Superpowers skill when available; the ledger identity must name this exact plan file. If that helper is unavailable in the execution environment, create the equivalent ignored workspace at `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/` and record the ruling.
- A fresh subagent gets only its task brief, the exact interfaces it consumes, prior task decisions required by that brief, and the report path. Do not make implementers read the whole conversation history.
- One implementer task gets a spec-compliance + code-quality review before the next task. Use a fresh reviewer. After all tasks, perform one broad whole-branch review with the most capable available model.
- Batch only truly same-shape test additions. Do not batch tasks whose failures require different architectural judgment.
- `pytest` and Ruff are offline. Controlled evaluation intentionally makes paid target-model, judge-model, and LangSmith calls.
- Do not run any controlled provider command without immediate human authorization at the paid-call gate in the execution session. Do not run `--tier live` anywhere in this plan.
- Never run `python -m deep_research.evaluation suite` until each non-Planner agent has independently passed its own full controlled campaign or has a documented infrastructure-blocked terminal state.
- Never print, commit, upload, or quote credentials, hidden chain-of-thought, raw provider payloads, unredacted exception strings, prompts, evaluator inputs, or provider reasoning content.
- Use only typed artifacts, bounded trajectory summaries, safe events, safe provider diagnostics, gate IDs/details that are already allowed, concise judge rationale, and trace URLs directly supplied by LangSmith.
- Do not commit `.env`, `.superpowers/`, `output/`, evaluation artifacts, review packets, or provider-run ledgers.
- No merge, push to a shared branch, release, deployment, or live-tier run is part of this plan.

---

## File Structure

### Shared production files

| File | Responsibility in this campaign |
| --- | --- |
| `src/deep_research/agents/react.py` | Own the single shared normalization boundary from a validated `ReActDecision` to `ReActStep`; preserve current provider-error propagation compatibility switch. |
| `src/deep_research/agents/planner.py` | Remove `_DecisionNormalizingCompleter` and the temporary provider swap after the shared boundary is proven equivalent; retain Planner's operation-specific provider wrapping and final-plan budget. |
| `src/deep_research/providers/contracts.py` | Define one finite, immutable, provider-content-free runtime snapshot for caught provider failures, built only from already-safe typed provider fields. |
| `src/deep_research/agents/errors.py` | Convert a caught `ProviderError` plus a static operation name into JSON-safe `ResearchError.details` without `str(error)`. |

### Agent production files consuming the shared provider diagnostic

| File | Provider operations whose fallback error must preserve safe typed diagnosis |
| --- | --- |
| `src/deep_research/agents/researcher.py` | ReAct decision (through shared loop) and `SubTopicFindingsDraft` extraction |
| `src/deep_research/agents/source_evaluator.py` | `SourceScoresDraft` scoring |
| `src/deep_research/agents/fact_checker.py` | claim extraction, ReAct decision, claim verdict |
| `src/deep_research/agents/synthesizer.py` | `ReportDraft` generation |
| `src/deep_research/agents/critic.py` | ReAct decision and `CritiqueDraft` review |

### Primary tests

| File | Responsibility |
| --- | --- |
| `tests/test_agents/test_react.py` | Direct RC-A shared-boundary RED/GREEN tests and provider-diagnostic ReAct tests. |
| `tests/test_agents/test_planner.py` | Existing Planner RC-A regression remains green after local wrapper deletion; provider identity/no-wrapper-stack checks. |
| `tests/test_agents/test_researcher.py` | Agent-level blank-field regression; extraction provider snapshot and partial-result semantics. |
| `tests/test_agents/test_source_evaluator.py` | Scoring provider snapshot and low-confidence fallback semantics; no-ReAct characterization. |
| `tests/test_agents/test_fact_checker.py` | Agent-level blank-field regression; extraction/verdict provider snapshots; prior-claim retention. |
| `tests/test_agents/test_synthesizer.py` | Report provider snapshot; evidence-only fallback semantics; no-ReAct characterization. |
| `tests/test_agents/test_critic.py` | Agent-level blank-field regression; review provider snapshot; fallback routing. |
| `tests/test_agents/test_errors.py` | Snapshot-to-`ResearchError.details` mapping, bounds, and secret/provider-content exclusion. |
| `tests/test_deepseek_provider.py` | Provider snapshot compatibility with output-limit/schema telemetry if the snapshot is defined in provider contracts. |
| `tests/test_openai_provider.py` | Generic provider-response/timeout/HTTP compatibility; no unsupported output-limit inference. |
| `tests/test_evaluation/test_targets.py` | Prove fallback-producing agents remain `completed=True` when designed to return a result, while their `errors` retain safe typed diagnosis; Planner raised failures remain target `failure` records. |
| `tests/test_evaluation/test_factory.py` and `tests/test_runtime/test_assembly.py` | Preserve exact provider identity/parity after Planner-local wrapper removal. |
| `tests/test_config.py` and `tests/test_evaluation/test_config.py` | Conditional only: operation-specific budget field + environment override + fingerprint when a controlled output-limit amendment authorizes one. |

### Campaign artifacts

| Path | Responsibility |
| --- | --- |
| `.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/progress.md` | Ignored SDD recovery ledger, task completion, rulings, review findings, exact commits, provider-run gates, artifact paths, and next action. |
| `docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md` | Tracked final permanent record created only after implementation begins: confirmed transferable causes, changes, RED/GREEN commands, controlled evidence, and terminal state for each agent. |
| `output/evaluations/<agent>/*/results.json` | Immutable per-agent controlled evidence; ignored, never committed. |

### Frozen evaluation inputs during agent repair loops

Do not modify these while tuning an agent:

```text
src/deep_research/evaluation/cases/researcher.py
src/deep_research/evaluation/cases/source_evaluator.py
src/deep_research/evaluation/cases/fact_checker.py
src/deep_research/evaluation/cases/synthesizer.py
src/deep_research/evaluation/cases/critic.py
src/deep_research/evaluation/evaluators.py
src/deep_research/evaluation/judging.py
src/deep_research/evaluation/runner.py
src/deep_research/evaluation/reporting.py
src/deep_research/evaluation/config.py
src/deep_research/evaluation/dependencies.py
src/deep_research/evaluation/models.py
```

`src/deep_research/evaluation/targets.py` may change only in the explicit offline artifact-visibility task below, not during any later agent quality repair.

---

### Task 1: Create the Isolated SDD Campaign and Freeze the Known-Good Base

**Files:**
- Create, ignored: this plan's `.superpowers/sdd/.../progress.md` workspace ledger
- Create, ignored if required by the local environment: a secret-safe repo-env launcher equivalent to the Planner campaign's launcher
- Verify: `docs/superpowers/2026-08-25-planner-evaluation-fix-log.md`
- Verify: this plan file

**Interfaces:**
- Consumes: `main` containing merge commit `bc67620666bbc41c516556d45602de6dd00d7102` and this plan.
- Produces: isolated branch/worktree, exact approved-base SHA, clean offline baseline, and a recovery ledger that survives context compaction.

- [ ] **Step 1: Resolve the repository and verify the Planner remediation is in the base**

```powershell
$Repository = (git rev-parse --show-toplevel).Trim()
$PlannerMerge = 'bc67620666bbc41c516556d45602de6dd00d7102'
$Plan = 'docs/superpowers/plans/2026-09-08-cross-agent-planner-fix-parity.md'
$Branch = 'codex/cross-agent-planner-fix-parity'
$Worktree = Join-Path $Repository '.worktrees\cross-agent-planner-fix-parity'

$ApprovedBase = (git -C $Repository rev-parse main).Trim()
git -C $Repository merge-base --is-ancestor $PlannerMerge $ApprovedBase
if ($LASTEXITCODE -ne 0) { throw 'main does not contain the known-good Planner remediation merge' }
git -C $Repository cat-file -e "$ApprovedBase`:$Plan"
if ($LASTEXITCODE -ne 0) { throw 'approved base does not contain this plan' }
```

Expected: both checks exit 0 and `$ApprovedBase` is a literal 40-character SHA.

- [ ] **Step 2: Create or verify the worktree without deleting anything unexpected**

```powershell
if (Test-Path -LiteralPath $Worktree) {
    if ((git -C $Worktree branch --show-current).Trim() -ne $Branch) {
        throw 'campaign worktree path belongs to another branch'
    }
} else {
    if (git -C $Repository branch --list $Branch) {
        throw 'campaign branch exists without the expected worktree; inspect manually'
    }
    git -C $Repository worktree add -b $Branch $Worktree $ApprovedBase
}
Set-Location -LiteralPath $Worktree
if (git status --porcelain) { throw 'campaign worktree is dirty before execution' }
```

Expected: clean `codex/cross-agent-planner-fix-parity` at `$ApprovedBase`.

- [ ] **Step 3: Initialize the SDD workspace and ledger**

Use the current Superpowers `subagent-driven-development` workspace helper if installed. The ledger's first line must identify this plan exactly:

```markdown
# SDD ledger — plan: docs/superpowers/plans/2026-09-08-cross-agent-planner-fix-parity.md
```

Then record these literal values beneath it:

```markdown
- Approved base: <the 40-character SHA printed in Step 1>
- Planner reference merge: bc67620666bbc41c516556d45602de6dd00d7102
- Branch: codex/cross-agent-planner-fix-parity
- Worktree: .worktrees/cross-agent-planner-fix-parity
- Global max tokens: 4096
- Live tier: prohibited
- Workflow state: OFFLINE_BASELINE_REQUIRED
```

Do not save the angle-bracket text; replace it with the actual SHA.

- [ ] **Step 4: Verify interpreter/editable-install provenance**

```powershell
python -m pip install -e ".[dev]"
python -c "import pathlib, deep_research; print(pathlib.Path(deep_research.__file__).resolve())"
```

Expected: the printed module path resolves under this campaign worktree's `src`.

- [ ] **Step 5: Run the complete tracked offline baseline**

```powershell
python -m pytest -q -p no:cacheprovider
python -m ruff check src tests
git diff --check
```

Expected: pytest matches or improves the merged Planner-remediation baseline (PR #19 recorded 1881 passed, 1 deselected, 2 known dependency warnings), Ruff is clean, and `git diff --check` exits 0. If the local Windows temp-root reproduces the documented path-length issue, use a short `--basetemp` and record that exact ruling in the ledger; do not change tracked code to fix the environment.

- [ ] **Step 6: Commit no code in this task**

The task ends with a clean baseline and ledger only. Record `Task 1: complete` in the ignored ledger. No tracked commit is required.

---

### Task 2: Move RC-A Normalization to the Shared ReAct Boundary

**Files:**
- Modify: `src/deep_research/agents/react.py`
- Test: `tests/test_agents/test_react.py`

**Interfaces:**
- Consumes: a `ReActDecision` whose unused optional field may be `""` after provider validation.
- Produces: every `ReActStep` stores `tool_name=None` on finish decisions and `final_answer=None` on tool decisions; meaningful non-empty values are unchanged.
- Does not change: `ReActDecision` schema, `ReActStep` schema, provider contracts, stop reasons, tool-budget behavior, or provider-error propagation mode.

- [ ] **Step 1: Add the two direct failing shared-loop regression tests**

Append tests beside `test_one_step_loop_finishes_immediately` / `test_multi_step_loop_calls_a_tool_then_finishes` in `tests/test_agents/test_react.py`:

```python
@pytest.mark.asyncio
async def test_finish_decision_normalizes_empty_unused_tool_name(
    tracker: Tracker,
) -> None:
    decision = ReActDecision(
        thought="Enough evidence.",
        action="finish",
        tool_name="",
        tool_input_json="{}",
        final_answer="Done.",
    )
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker),
            decide=_decider([decision]),
            max_iterations=2,
            tool_budget=0,
        )

    assert run.stop_reason == "finished"
    assert run.steps[0].tool_name is None
    assert run.steps[0].final_answer == "Done."


@pytest.mark.asyncio
async def test_tool_decision_normalizes_empty_unused_final_answer(
    tracker: Tracker,
) -> None:
    decision = ReActDecision(
        thought="Check one source.",
        action="use_tool",
        tool_name="echo",
        tool_input_json='{"value": "x"}',
        final_answer="",
    )
    async with agent_scope(tracker):
        run = await run_react_loop(
            agent_name="researcher",
            tracker=tracker,
            tools=_toolset(tracker, "echo"),
            decide=_decider(
                [decision, finish("Enough.", "Done.")]
            ),
            max_iterations=2,
            tool_budget=1,
        )

    assert run.steps[0].tool_name == "echo"
    assert run.steps[0].final_answer is None
```

- [ ] **Step 2: Run them and verify RED**

```powershell
python -m pytest -q tests/test_agents/test_react.py -k "normalizes_empty_unused"
```

Expected before the fix: both tests fail at `ReActStep(...)` validation because the unused value is an empty string.

- [ ] **Step 3: Apply the minimal shared-boundary fix**

In the existing `ReActStep(...)` construction in `run_react_loop`, change only the two optional values:

```python
step = ReActStep(
    iteration=iteration,
    thought=decision.thought,
    action=decision.action,
    tool_name=decision.tool_name or None,
    tool_input=tool_input,
    observation=observation,
    tool_result=tool_result,
    final_answer=decision.final_answer or None,
)
```

Do not weaken `ReActStep`'s `min_length=1` contract. Do not add a second provider wrapper. `ContractModel` already strips whitespace, so `field or None` also handles whitespace-only unused strings after model validation.

- [ ] **Step 4: Verify focused and neighboring GREEN**

```powershell
python -m pytest -q tests/test_agents/test_react.py -k "normalizes_empty_unused"
python -m pytest -q tests/test_agents/test_react.py tests/test_agents/test_base.py
python -m ruff check src/deep_research/agents/react.py tests/test_agents/test_react.py
git diff --check
```

Expected: all commands pass.

- [ ] **Step 5: Commit**

```powershell
git add src/deep_research/agents/react.py tests/test_agents/test_react.py
git commit -m "fix(agents): normalize optional ReAct step fields"
```

Record commit SHA and `Task 2: complete` in the ledger.

---

### Task 3: Remove the Planner-Local RC-A Workaround and Prove Cross-Agent ReAct Parity

**Files:**
- Modify: `src/deep_research/agents/planner.py`
- Modify: `tests/test_agents/test_planner.py`
- Modify: `tests/test_agents/test_researcher.py`
- Modify: `tests/test_agents/test_fact_checker.py`
- Modify: `tests/test_agents/test_critic.py`
- Verify: `tests/test_evaluation/test_factory.py`
- Verify: `tests/test_runtime/test_assembly.py`

**Interfaces:**
- Consumes: Task 2's shared ReAct normalization.
- Produces: Planner no longer swaps/wraps its provider for RC-A; Researcher, Fact Checker, and Critic all complete a scripted ReAct path containing an empty unused optional field.
- Preserves: Planner's `preserve_provider_errors=True`, `planning_provider_error("react_decision")` cause wrapping, and `planner_final_max_tokens` only on `ResearchPlanDraft`.

- [ ] **Step 1: Add/retain parity tests before deleting Planner code**

Keep the existing Planner RC-A regression tests unchanged. Add one agent-level regression to each custom ReAct agent using its existing test constructors/fakes. Each test must queue a valid decision whose *unused* optional field is `""` and assert the agent does not raise `ValidationError`.

Required assertions:

```text
Researcher: the sub-topic ReAct run completes and the stored step has final_answer is None on a tool decision.
Fact Checker: the per-claim verification loop completes and the stored step has tool_name is None on a finish decision.
Critic: the spot-check loop completes and the stored step has final_answer is None on a tool decision.
```

Use the existing `ScriptedCompleter` and each test module's current state/tool fixtures rather than introducing a second fake framework.

- [ ] **Step 2: Verify the new sibling tests are already GREEN on Task 2**

```powershell
python -m pytest -q tests/test_agents/test_researcher.py -k "empty_unused"
python -m pytest -q tests/test_agents/test_fact_checker.py -k "empty_unused"
python -m pytest -q tests/test_agents/test_critic.py -k "empty_unused"
```

Expected: all pass because Task 2 fixed the shared boundary. If any fails for a different reason, record the exact failure as a separate root-cause candidate; do not weaken the test or copy the Planner wrapper into that agent.

- [ ] **Step 3: Delete only the Planner-local normalization wrapper**

In `planner.py`:
- remove `_DecisionNormalizingCompleter` completely;
- remove imports used only by that wrapper (`Any`, `StructuredCompleter`, and `ReActDecision` if no longer used elsewhere);
- simplify `PlannerAgent.run()` so it does not replace `self._provider`;
- retain the provider-error translation:

```python
try:
    outcome = await super().run(state)
except ProviderError as error:
    raise planning_provider_error("react_decision") from error
```

Do not change `_request_plan()` or its `max_tokens=self.config.planner_final_max_tokens` argument.

- [ ] **Step 4: Run the Planner and provider-identity regression set**

```powershell
python -m pytest -q tests/test_agents/test_planner.py -k "planner_regression or provider"
python -m pytest -q tests/test_evaluation/test_factory.py tests/test_runtime/test_assembly.py
python -m pytest -q tests/test_agents/test_react.py tests/test_agents/test_planner.py tests/test_agents/test_researcher.py tests/test_agents/test_fact_checker.py tests/test_agents/test_critic.py
python -m ruff check src/deep_research/agents tests/test_agents
```

Expected: the Planner's known-good RC-A tests remain green and exact provider identity/parity tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/deep_research/agents/planner.py `
  tests/test_agents/test_planner.py `
  tests/test_agents/test_researcher.py `
  tests/test_agents/test_fact_checker.py `
  tests/test_agents/test_critic.py
git commit -m "refactor(agents): share ReAct decision normalization"
```

Record commit SHA and `Task 3: complete`.

---

### Task 4: Add a Shared Safe Snapshot for Provider Failures Caught by Fallbacking Agents

**Files:**
- Modify: `src/deep_research/providers/contracts.py`
- Modify: `src/deep_research/providers/__init__.py`
- Modify: `src/deep_research/agents/errors.py`
- Test: `tests/test_agents/test_errors.py`
- Test: `tests/test_deepseek_provider.py`
- Test: `tests/test_openai_provider.py`

**Interfaces:**
- Consumes: direct `ProviderError` subclasses already emitted by the providers.
- Produces: one immutable, finite `ProviderFailureSnapshot` and `provider_failure_snapshot(error)` helper that never renders exception messages or provider content; one `agent_provider_failure_details(operation, error, **extra)` helper returning JSON-safe details.
- The snapshot is runtime/provider infrastructure, not an evaluation model; agents must not import from `deep_research.evaluation`.

- [ ] **Step 1: Write RED tests for the safe finite projection**

Add tests that construct:
- `ProviderOutputLimitError` with `finish_reason_category="length"`, `configured_max_tokens=4096`, typed usage, request attempt 2, structured attempt 1;
- `StructuredOutputError` with two `StructuredValidationDiagnostic` records;
- `ProviderTimeoutError`;
- `ProviderRateLimitError`;
- `ProviderResponseError` for transport, HTTP 503 retryable, HTTP 401 nonretryable, and generic response.

For each, assert the snapshot has exactly one finite kind from:

```text
output_limit
schema_output
provider_timeout
provider_rate_limit
provider_transport
provider_http
provider_response
provider_failure
```

For output-limit, assert configured cap, usage, request attempt, and structured attempt are preserved. For schema output, assert only normalized bounded field paths and attempts are preserved. For HTTP, assert only validated status and retryability are preserved.

Add an adversarial error whose message contains `PROVIDER_SECRET_SENTINEL` and assert that sentinel is absent from both `snapshot.model_dump(mode="json")` and `repr(snapshot.model_dump(mode="json"))`.

- [ ] **Step 2: Run RED**

```powershell
python -m pytest -q tests/test_agents/test_errors.py tests/test_deepseek_provider.py tests/test_openai_provider.py -k "provider_failure_snapshot or agent_provider_failure_details"
```

Expected: collection/assertions fail because the shared snapshot/helper do not exist.

- [ ] **Step 3: Implement `ProviderFailureSnapshot` in provider contracts**

Define an immutable model with only these fields:

```python
ProviderFailureKind: TypeAlias = Literal[
    "output_limit",
    "schema_output",
    "provider_timeout",
    "provider_rate_limit",
    "provider_transport",
    "provider_http",
    "provider_response",
    "provider_failure",
]

class ProviderFailureSnapshot(ProviderContract):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, frozen=True
    )

    kind: ProviderFailureKind
    exception_type: str = Field(min_length=1, max_length=128)
    retryable: bool | None = None
    http_status_code: int | None = Field(default=None, ge=100, le=599)
    configured_max_tokens: PositiveInt | None = None
    usage: TokenUsage | None = None
    request_attempt: PositiveInt | None = None
    structured_attempt: PositiveInt | None = None
    diagnostics: tuple[StructuredValidationDiagnostic, ...] = Field(
        default=(), max_length=2
    )
```

Implement `provider_failure_snapshot(error: ProviderError) -> ProviderFailureSnapshot` by type, most specific first. Do not inspect `str(error)`. Map `ProviderResponseError.failure_category="output_limit"` to `provider_response` unless the concrete type is `ProviderOutputLimitError`, matching the evaluation taxonomy's existing conservative rule.

- [ ] **Step 4: Implement the agent JSON helper**

In `agents/errors.py`, add:

```python
def agent_provider_failure_details(
    operation: str,
    error: ProviderError,
    **extra: JsonValue,
) -> dict[str, JsonValue]:
    if not operation.strip():
        raise ValueError("operation must not be blank")
    snapshot = provider_failure_snapshot(error)
    return {
        "operation": operation.strip(),
        "provider_failure": snapshot.model_dump(mode="json"),
        **extra,
    }
```

Do not retain a second `exception_type` outside the snapshot. Export the provider snapshot/helper from `providers.__init__` using the package's existing explicit-export style.

- [ ] **Step 5: Run GREEN and neighboring provider tests**

```powershell
python -m pytest -q tests/test_agents/test_errors.py tests/test_deepseek_provider.py tests/test_openai_provider.py -k "provider_failure_snapshot or agent_provider_failure_details"
python -m pytest -q tests/test_deepseek_provider.py tests/test_openai_provider.py tests/test_retry_policy.py tests/test_evaluation/test_failure_taxonomy.py
python -m ruff check src/deep_research/providers src/deep_research/agents/errors.py tests/test_agents/test_errors.py tests/test_deepseek_provider.py tests/test_openai_provider.py
```

Expected: all pass; existing evaluation taxonomy behavior remains unchanged.

- [ ] **Step 6: Commit**

```powershell
git add src/deep_research/providers/contracts.py `
  src/deep_research/providers/__init__.py `
  src/deep_research/agents/errors.py `
  tests/test_agents/test_errors.py `
  tests/test_deepseek_provider.py `
  tests/test_openai_provider.py
git commit -m "feat(agents): retain safe provider failure diagnostics"
```

Record commit SHA and `Task 4: complete`.

---

### Task 5: Wire Safe Provider Diagnostics into Every Non-Planner Fallback Path

**Files:**
- Modify: `src/deep_research/agents/react.py`
- Modify: `src/deep_research/agents/researcher.py`
- Modify: `src/deep_research/agents/source_evaluator.py`
- Modify: `src/deep_research/agents/fact_checker.py`
- Modify: `src/deep_research/agents/synthesizer.py`
- Modify: `src/deep_research/agents/critic.py`
- Test: corresponding six `tests/test_agents/test_*.py` modules

**Interfaces:**
- Consumes: Task 4 `agent_provider_failure_details`.
- Produces: every caught provider failure has a static operation name plus the safe snapshot while retaining the agent's exact existing stop/fallback behavior.

Use these operation names verbatim:

```text
react_decision
researcher_finding_extraction
source_evaluator_scoring
fact_checker_claim_extraction
fact_checker_claim_verification
synthesizer_report_draft
critic_report_review
```

- [ ] **Step 1: Add RED assertions to each existing provider-failure test**

For each path, replace assertions that only expect `{"exception_type": ...}` with assertions on:

```python
assert error.details["operation"] == "<exact operation above>"
provider = error.details["provider_failure"]
assert provider["kind"] == "output_limit"
assert provider["configured_max_tokens"] == 4096
assert provider["request_attempt"] == 1
```

Use a `ProviderOutputLimitError` in at least one test for every operation. Add one schema-output test to Researcher extraction and one transport/HTTP test to Critic or Source Evaluator so non-output categories are exercised through an agent boundary too.

Also keep/extend each test's semantic assertions:
- Researcher: earlier findings survive and later subtopics stop.
- Source Evaluator: every source still gets a low-confidence fallback row.
- Fact Checker: already-completed claims survive; failed verdict becomes `insufficient_evidence` exactly as before.
- Synthesizer: report skeleton is still composed/written from recorded evidence.
- Critic: fallback critique/routing remains unchanged.
- ReAct compatibility mode: `stop_reason == "provider_error"`, nonrecoverable ResearchError, no raised provider exception when `propagate_provider_errors=False`.

- [ ] **Step 2: Run RED**

```powershell
python -m pytest -q `
  tests/test_agents/test_react.py `
  tests/test_agents/test_researcher.py `
  tests/test_agents/test_source_evaluator.py `
  tests/test_agents/test_fact_checker.py `
  tests/test_agents/test_synthesizer.py `
  tests/test_agents/test_critic.py `
  -k "provider or output_limit or schema"
```

Expected: new detail assertions fail because the current code records only generic exception type/counts.

- [ ] **Step 3: Replace only provider-error `details` construction**

Use `agent_provider_failure_details(...)` in each provider catch. Preserve every existing `error_type`, static user-facing message, `recoverable` value, stop reason, fallback object, and loop-break rule.

Examples of the intended shape:

```python
return agent_error(
    agent_name=RESEARCHER_NAME,
    error_type="researcher_extraction_provider_error",
    message=(...),
    recoverable=False,
    details=agent_provider_failure_details(
        "researcher_finding_extraction",
        error,
        iterations=run.iterations,
        tool_calls=run.tool_calls,
    ),
)
```

```python
errors.append(
    agent_error(
        agent_name=agent_name,
        error_type="agent_provider_error",
        message="The model provider failed and the ReAct loop stopped.",
        recoverable=False,
        details=agent_provider_failure_details(
            "react_decision", error, iteration=iteration
        ),
    )
)
```

Do not change Planner's raised cause-chain behavior; when `propagate_provider_errors=True`, the shared loop still re-raises the original provider exception after recording its safe event.

- [ ] **Step 4: Run focused and full agent tests**

```powershell
python -m pytest -q tests/test_agents
python -m pytest -q tests/test_evaluation/test_failure_taxonomy.py tests/test_evaluation/test_targets.py
python -m ruff check src/deep_research/agents tests/test_agents
```

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add src/deep_research/agents/react.py `
  src/deep_research/agents/researcher.py `
  src/deep_research/agents/source_evaluator.py `
  src/deep_research/agents/fact_checker.py `
  src/deep_research/agents/synthesizer.py `
  src/deep_research/agents/critic.py `
  tests/test_agents/test_react.py `
  tests/test_agents/test_researcher.py `
  tests/test_agents/test_source_evaluator.py `
  tests/test_agents/test_fact_checker.py `
  tests/test_agents/test_synthesizer.py `
  tests/test_agents/test_critic.py
git commit -m "fix(agents): preserve typed provider diagnostics in fallbacks"
```

Record commit SHA and `Task 5: complete`.

---

### Task 6: Prove Typed Fallback Diagnostics Survive the Evaluation Artifact Boundary

**Files:**
- Modify: `tests/test_evaluation/test_targets.py`
- Modify `src/deep_research/evaluation/targets.py` only if a failing test demonstrates the existing `_success_output` path drops or corrupts the safe details

**Interfaces:**
- Consumes: a non-Planner agent that intentionally returns a fallback `AgentRun.result` plus `run.errors` containing Task 5 provider snapshots.
- Produces: `TargetOutput.completed=True`, `TargetOutput.failure=None`, and the safe typed provider snapshot preserved in `TargetOutput.errors` for fallback-producing agents; Planner exceptions continue to produce `completed=False` with top-level typed `failure` via the cause-chain taxonomy.

- [ ] **Step 1: Add a target-level fallback test**

Extend the target harness/fakes using the existing fixture style so one non-Planner agent returns a valid fallback result and one `ResearchError` whose `details.provider_failure.kind == "output_limit"`. Assert:

```python
output = TargetOutput.model_validate(payload)
assert output.completed is True
assert output.failure is None
assert output.result is not None
assert output.errors[0]["details"]["provider_failure"]["kind"] == "output_limit"
assert output.errors[0]["details"]["provider_failure"]["configured_max_tokens"] == 4096
```

Also retain the existing Planner test that a raised output-limit cause becomes top-level `failure.reason == "output_limit"`.

- [ ] **Step 2: Run the target tests**

```powershell
python -m pytest -q tests/test_evaluation/test_targets.py -k "provider or fallback or output_limit"
```

Expected: this should already be GREEN because `_success_output` serializes `run.errors`. If it is green, make no production target change; the test is the regression guard. If it fails because typed safe fields are dropped, fix only the serialization boundary required by the failing assertion, then rerun the complete target suite.

- [ ] **Step 3: Run neighboring evaluation tests**

```powershell
python -m pytest -q tests/test_evaluation/test_targets.py tests/test_evaluation/test_models.py tests/test_evaluation/test_failure_taxonomy.py tests/test_evaluation/test_runner.py
python -m ruff check src/deep_research/evaluation tests/test_evaluation/test_targets.py
```

- [ ] **Step 4: Commit the test (and only a demonstrated minimal production fix if needed)**

```powershell
git add tests/test_evaluation/test_targets.py
if (git diff --name-only | Select-String 'src/deep_research/evaluation/targets.py') {
    git add src/deep_research/evaluation/targets.py
}
git commit -m "test(evaluation): preserve fallback provider diagnostics"
```

Record commit SHA and `Task 6: complete`.

---

### Task 7: Characterize Source Evaluator and Synthesizer as Non-ReAct Agents

**Files:**
- Modify: `tests/test_agents/test_source_evaluator.py`
- Modify: `tests/test_agents/test_synthesizer.py`
- No production change expected

**Interfaces:**
- Produces a regression guard for the audit conclusion that RC-A does not apply to these two agents on current `main`.

- [ ] **Step 1: Add test assertions that their normal provider calls never request `ReActDecision`**

Use `ScriptedCompleter.calls` / each module's existing recording provider. After a representative successful run, assert the requested schema sequence contains:

```text
Source Evaluator: SourceScoresDraft only
Synthesizer: ReportDraft only
```

and does not contain `ReActDecision`.

- [ ] **Step 2: Run tests**

```powershell
python -m pytest -q tests/test_agents/test_source_evaluator.py tests/test_agents/test_synthesizer.py -k "react or schema or provider"
```

Expected: GREEN with no production edit. If a current code path unexpectedly requests `ReActDecision`, stop and record the architecture drift before proceeding; RC-A applicability must be reclassified.

- [ ] **Step 3: Commit characterization tests**

```powershell
git add tests/test_agents/test_source_evaluator.py tests/test_agents/test_synthesizer.py
git commit -m "test(agents): pin non-ReAct agent architecture"
```

Record commit SHA and `Task 7: complete`.

---

### Task 8: Run the Offline Integration Gate Before Any Paid Evaluation

**Files:**
- No production files
- Update ignored ledger

**Interfaces:**
- Consumes: Tasks 2-7.
- Produces: one reviewed offline candidate SHA eligible for controlled evaluation.

- [ ] **Step 1: Run focused integration suites**

```powershell
python -m pytest -q `
  tests/test_agents `
  tests/test_deepseek_provider.py `
  tests/test_openai_provider.py `
  tests/test_retry_policy.py `
  tests/test_config.py `
  tests/test_evaluation/test_failure_taxonomy.py `
  tests/test_evaluation/test_targets.py `
  tests/test_evaluation/test_factory.py `
  tests/test_runtime/test_assembly.py
```

- [ ] **Step 2: Run full tracked offline verification**

```powershell
python -m pytest -q -p no:cacheprovider
python -m ruff check src tests
git diff --check
git status --short
```

Expected: full suite green, Ruff clean, whitespace clean, tracked worktree clean.

- [ ] **Step 3: Run the task review gate**

Dispatch a fresh reviewer on the complete diff from `$ApprovedBase` through current HEAD. Required review questions:

```text
1. Does shared RC-A normalization occur exactly once at the ReActDecision -> ReActStep boundary?
2. Is Planner behavior identical except for deleting its redundant local wrapper?
3. Do any provider diagnostic fields retain exception strings, raw finish values, provider content, validation input values, prompts, or secrets?
4. Did any fallback/partial-result behavior change unintentionally?
5. Did any global model budget, judge budget, retry classification, evaluator, gate, case, threshold, or rubric change?
6. Are Source Evaluator and Synthesizer correctly excluded from RC-A because they do not run ReAct?
```

Fix Critical/Important findings with TDD and scoped re-review before proceeding. Record review report path and rulings in the SDD ledger.

- [ ] **Step 4: Freeze candidate SHA**

```powershell
$CandidateSha = (git rev-parse HEAD).Trim()
git status --porcelain
```

Expected: clean worktree. Record literal `$CandidateSha` in the ledger as `Offline candidate` and `Workflow state: CONTROLLED_BASELINES_REQUIRED`.

---

### Task 9: Run Immutable Controlled Baselines for Each Non-Planner Agent

**Files:**
- No tracked production/test changes during baseline acquisition
- Update ignored ledger
- Create ignored `output/evaluations/<agent>/...` artifacts through existing harness

**Interfaces:**
- Consumes: Task 8 candidate SHA and the existing frozen controlled datasets.
- Produces: one immutable full controlled baseline per non-Planner agent at the same candidate SHA.

**Run order:** `researcher`, `fact_checker`, `critic`, `synthesizer`, `source_evaluator`.

This order checks the three RC-A-exposed agents first, then the long-output non-ReAct agent, then the bounded scoring agent.

- [ ] **Step 1: Preflight effective model/retry/budget configuration without printing secrets**

Through the repo's secret-safe launcher or inherited environment, print only non-secret values:

```python
settings.llm.model
settings.llm.max_tokens
settings.llm.retry_count
settings.llm.retry_initial_delay
settings.llm.retry_max_delay
settings.agents.planner_final_max_tokens
```

Required baseline values:

```text
llm.max_tokens = 4096
planner_final_max_tokens = 4096 unless a Planner-specific shell override is intentionally present (clear it for this campaign)
retry policy = the repository's intended controlled-evaluation policy; if `.env` changes it, set the approved process override before the launcher and record the effective non-secret value
```

Never print API keys or the full environment.

- [ ] **Step 2: Immediately before each paid command, obtain human confirmation**

The controller states the agent, candidate SHA, exact command, controlled-only scope, expected 3 cases × 3 repetitions, target/judge model, global 4096 cap, and that the run makes paid provider/LangSmith calls. Do not reuse an old confirmation for a later agent.

- [ ] **Step 3: Run each full controlled baseline separately**

For each `$Agent` in the fixed run order:

```powershell
python -m deep_research.evaluation agent $Agent `
  --config config.yaml `
  --experiment-prefix cross-agent-planner-fix-parity-baseline `
  --verbose
```

Do not use `--tier live` and do not run the suite command.

- [ ] **Step 4: Strictly validate and inventory each artifact**

For each `results.json`, record in the ledger:

```text
agent
experiment name / artifact path / experiment URL
candidate SHA and configuration fingerprint
cases completed / repetitions completed / hard gates
status and mean score
all target failure stages/reasons
all safe provider_failure.kind values found under successful fallback output.errors
judge status/not-run reasons and safe evaluator diagnostics
per-case deterministic score / judge score / aggregate
all prohibited-call gate failures
all trajectory stop reasons
```

Never record provider exception messages or prompt/provider content.

- [ ] **Step 5: Assign root-cause IDs**

Use these prefixes:

```text
researcher-...
source-evaluator-...
fact-checker-...
synthesizer-...
critic-...
```

A root cause must be falsifiable and evidence-backed. Do not call every low judge score a prompt problem. Separate infrastructure/provider failures from deterministic quality failures.

- [ ] **Step 6: Determine terminal routing per agent**

For each agent:

```text
A. PASS: full controlled status REVIEW REQUIRED, all expected reps complete, no unexplained target/fallback provider failure, no judge infrastructure failure that invalidates quality.
B. PROVIDER OUTPUT-LIMIT CANDIDATE: typed output_limit observed in a specific target operation; route to Task 10.
C. OTHER PROVIDER/SCHEMA CANDIDATE: typed schema/transport/http/etc.; diagnose typed cause before any prompt/budget edit; use Task 11 amendment process.
D. QUALITY/TRAJECTORY CANDIDATE: target completed but gate/deterministic/judge evidence identifies one behavior defect; use Task 11 amendment process.
E. HARNESS DEFECT: frozen harness/evaluator is demonstrably wrong; stop tuning that agent and open a separate harness plan.
```

No source edit occurs in Task 9.

---

### Task 10: Evidence-Gated Operation-Specific Output-Budget Repair

**Files:** conditional; modify only for an agent/operation that produced a typed target-side `output_limit`
- `src/deep_research/utils/config.py`
- `config.yaml`
- the exact agent file containing the failing structured request
- `tests/test_config.py`
- `tests/test_evaluation/test_config.py`
- the exact agent test module
- provider tests only if the existing `max_tokens` override contract itself is broken (not expected)

**Interfaces:**
- Consumes: one Task 9 artifact proving target-side `output_limit`, including exact operation name and configured cap 4096.
- Produces: a single agent-operation-specific budget field; only that request passes it to `complete_structured(max_tokens=...)`; ReAct and judge calls remain `None`/global 4096.

Candidate field names are fixed by operation:

```text
researcher_finding_extraction -> agents.researcher_extraction_max_tokens / AGENTS_RESEARCHER_EXTRACTION_MAX_TOKENS
source_evaluator_scoring -> agents.source_evaluator_scoring_max_tokens / AGENTS_SOURCE_EVALUATOR_SCORING_MAX_TOKENS
fact_checker_claim_extraction -> agents.fact_checker_extraction_max_tokens / AGENTS_FACT_CHECKER_EXTRACTION_MAX_TOKENS
fact_checker_claim_verification -> agents.fact_checker_verdict_max_tokens / AGENTS_FACT_CHECKER_VERDICT_MAX_TOKENS
synthesizer_report_draft -> agents.synthesizer_report_max_tokens / AGENTS_SYNTHESIZER_REPORT_MAX_TOKENS
critic_report_review -> agents.critic_review_max_tokens / AGENTS_CRITIC_REVIEW_MAX_TOKENS
```

**There is intentionally no ReAct-decision budget field in this campaign.** If a ReAct decision itself reaches 4096, treat it as a separate trajectory/model-behavior investigation; the Planner campaign deliberately kept ReAct at the global cap.

- [ ] **Step 1: Write the literal repair amendment into the SDD ledger before editing code**

The amendment must include:

```text
Root-cause ID
agent + exact operation
baseline artifact path and repetition(s)
typed provider_failure.kind / configured cap / attempt(s)
why the failure is output truncation rather than transport/schema/quality
exact config field/env name from the mapping above
candidate focused override: 8192
files allowed to change
literal RED test name/code
focused pytest command
paid focused controlled command
rollback condition
```

No source edit before this record exists.

- [ ] **Step 2: Add RED config and call-budget tests**

Follow the Planner Task 3 pattern: the fake provider's `budgets` list must prove all neighboring operations stay `None` and exactly the affected operation receives the configured value. Assert the effective configuration fingerprint changes when and only when the new field changes.

- [ ] **Step 3: Implement the one field and one call-site override**

Default the new field to `4096`, `ge=1`, include the exact environment mapping above, add `config.yaml` value 4096, and pass `self.config.<field>` only to the affected structured request.

Do not modify `llm.max_tokens` or any judge/ReAct call.

- [ ] **Step 4: Offline GREEN**

Run the affected agent tests, config tests, provider max-token tests, evaluation fingerprint tests, then the full offline suite and Ruff.

- [ ] **Step 5: Review before any paid retest**

Fresh reviewer must explicitly verify isolation: affected operation gets 8192 only when process override is set; all other target operations, ReAct decisions, and judge calls retain global 4096.

- [ ] **Step 6: Human-confirmed focused 8192 experiment**

Set only the new operation-specific env override to 8192 in the launching shell, verify effective config/fingerprint without secrets, then run exactly the failing controlled case with three repetitions via:

```powershell
python -m deep_research.evaluation agent <agent> `
  --config config.yaml `
  --case <failing-case-id> `
  --experiment-prefix cross-agent-<agent>-8192 `
  --verbose
```

Clear the process override after evidence is recorded.

Focused gate passes only if all three target repetitions complete, the target-side output-limit disappears, hard gates pass, and all expected judges score without evaluator failure. If output-limit persists at 8192, stop and write a new diagnosis; do not automatically jump to 16384.

- [ ] **Step 7: Commit only after focused evidence justifies keeping the field**

Use commit subject:

```text
fix(<agent>): isolate <operation> output budget
```

If focused evidence disproves the hypothesis, revert the unneeded config/call-site change and retain the artifact/ledger evidence; do not keep speculative knobs.

---

### Task 11: Evidence-Gated Agent-Specific Quality / Trajectory Repair Loop

**Files:** conditional per diagnosed root cause; never edit frozen evaluation inputs

**Interfaces:**
- Consumes: one Task 9 or Task 10 focused failure with a falsifiable non-harness root cause.
- Produces: one cohesive repair, one focused three-repetition validation, and at most one full controlled validation before moving to the next root cause.

- [ ] **Step 1: Route the failure before editing**

Classify the root cause into exactly one category:

```text
shared runtime contract (should already be solved by Tasks 2-3)
agent prompt/instruction ambiguity
agent local validation/normalization
agent tool policy/trajectory control
agent fallback/state-update logic
structured provider output limit (Task 10 instead)
provider transport/rate/HTTP reliability (do not tune prompt)
schema-output failure
harness/evaluator defect (separate plan)
```

- [ ] **Step 2: Write a literal repair amendment**

Mirror the Planner campaign discipline. Before source edits, record:

```text
root-cause ID and mechanism
artifact/trace evidence
why neighboring hypotheses are ruled out
exact file(s) allowed to change
literal failing offline test
minimal implementation text
focused command and expected delta
non-target regression expectations
rollback condition
```

- [ ] **Step 3: TDD the repair**

Write the test, run RED, make the minimal change, run GREEN, run neighboring tests, Ruff, and `git diff --check`.

Prompt-repair rule: copy a *principle* from Planner only if the sibling's evidence proves the same ambiguity. Examples:
- unnecessary/prohibited search: tell that agent when existing evidence is sufficient or how to prioritize provided queries; do not tell Researcher/Fact Checker never to search, because search is part of their role;
- ordering/coverage: state the exact agent contract that failed, not Planner's priority/benefit-risk rubric;
- invented constraints/citations: enforce only the sibling's own output/evidence contract.

- [ ] **Step 4: Review the diagnosis and diff before provider calls**

Use a fresh reviewer. Fix Critical/Important findings before proceeding.

- [ ] **Step 5: Human-confirmed focused controlled retest**

Run the exact failing case for three repetitions. Record gates, deterministic/judge score, trajectory, typed provider/fallback diagnostics, and prohibited calls. The repair advances only if the targeted metric/failure improves without a new non-target regression.

- [ ] **Step 6: Attempt limit**

After three unsuccessful focused repairs for the same root-cause ID, stop that root-cause loop and write an escalation entry. Do not keep prompt-tuning indefinitely.

- [ ] **Step 7: Commit a successful cohesive repair**

Use an agent-specific commit subject and record SHA/evidence in the ledger.

Repeat Task 11 only for another independently diagnosed root cause; never bundle unrelated causes in one repair.

---

### Task 12: Full Controlled Validation for Every Repaired Agent

**Files:**
- No tracked changes during validation
- Update ignored ledger and ignored artifacts

**Interfaces:**
- Consumes: the final candidate commit for one agent after Tasks 10/11.
- Produces: one authoritative nine-repetition full controlled artifact at that commit.

- [ ] **Step 1: Re-run the full offline suite and verify clean Git state**

```powershell
python -m pytest -q -p no:cacheprovider
python -m ruff check src tests
git diff --check
if (git status --porcelain) { throw 'tracked worktree must be clean before controlled validation' }
```

- [ ] **Step 2: Obtain immediate human confirmation for this agent's full controlled run**

State exact candidate SHA, agent, command, model/reasoning configuration, effective non-secret budgets, and cost/network scope.

- [ ] **Step 3: Run the full controlled dataset**

```powershell
python -m deep_research.evaluation agent <agent> `
  --config config.yaml `
  --experiment-prefix cross-agent-planner-fix-parity-final `
  --verbose
```

- [ ] **Step 4: Validate terminal state**

A repaired agent is green only when:

```text
all expected repetitions completed
all hard gates passed
case averages/floors meet the frozen contract
all expected judges scored, or any judge failure is separately typed and makes the quality verdict explicitly non-promotable
no target-side output_limit/schema/provider failure remains unexplained
no fallback provider_failure snapshot is silently present in a supposedly clean case
no prohibited-call regression appeared
```

If a typed provider infrastructure failure prevents a valid quality verdict after the approved retry policy, record `INFRASTRUCTURE_BLOCKED` for that run; do not disguise it as an agent-quality failure.

- [ ] **Step 5: Record unchanged agents too**

If an agent passed its Task 9 baseline and required no repair, its immutable baseline artifact is its authoritative validation; do not spend money rerunning it merely for symmetry unless later shared code changes touched its execution path. If later shared code did touch it, rerun only after immediate human confirmation.

---

### Task 13: Create the Permanent Cross-Agent Fix Log

**Files:**
- Create: `docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md`

**Interfaces:**
- Consumes: SDD ledger, git history, reviewed diffs, and validated controlled artifacts.
- Produces: a tracked, secret-safe permanent record analogous to the Planner fix log.

- [ ] **Step 1: Write the log with these exact sections**

```markdown
# Cross-Agent Planner-Fix Parity Fix Log (2026-09-08)

## 1. Campaign Context and Frozen Base
## 2. Planner Lessons Reviewed
## 3. Transferability Matrix
## 4. Shared RC-A ReAct Boundary Fix
## 5. Planner Local-Wrapper Removal / Regression Evidence
## 6. Researcher Findings and Repairs
## 7. Source Evaluator Findings and Repairs
## 8. Fact Checker Findings and Repairs
## 9. Synthesizer Findings and Repairs
## 10. Critic Findings and Repairs
## 11. Safe Provider Diagnostic Projection
## 12. Operation-Specific Budget Decisions
## 13. Controlled Evaluation Evidence
## 14. Environment / Retry / Worktree Rulings
## 15. Review Findings and Resolutions
## 16. Deferred Non-Load-Bearing Findings
## 17. Final Verification and Terminal State
```

For an agent that needed no change, say so and cite the exact controlled artifact/gates that justified no change. Do not manufacture a repair section.

- [ ] **Step 2: Secret/data-leakage scan**

The fix log may contain commit SHAs, case IDs, gate IDs, finite typed reasons, non-secret config values/fingerprints, counts, scores, experiment URLs directly supplied by LangSmith, and static error messages. It must not contain prompts, provider responses, evaluator inputs, secrets, hidden reasoning, raw exception strings, or raw environment dumps.

- [ ] **Step 3: Commit**

```powershell
git add docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md
git commit -m "docs: record cross-agent planner-fix parity evidence"
```

Record `Task 13: complete`.

---

### Task 14: Final Offline Verification and Whole-Branch Review

**Files:**
- No new production scope
- May modify only files already changed by this plan to resolve final review findings

**Interfaces:**
- Produces: review-clean branch and final execution handoff; no merge/push.

- [ ] **Step 1: Run authoritative full verification**

```powershell
python -m pytest -q -p no:cacheprovider
python -m ruff check src tests
git diff --check
git status --short
git log --oneline --decorate --max-count=30
```

Expected: full suite green; Ruff/whitespace green; clean tracked worktree.

- [ ] **Step 2: Run reserved-data leakage checks over changed tracked files**

Inspect the diff from `$ApprovedBase` and verify no added production/logging path contains:

```text
str(error)
repr(error)
raw provider response/reasoning content
prompt/request payload serialization
raw finish_reason values
secret/environment dumps
unbounded validation input values
```

Legitimate test assertions may mention these strings only to prove they are absent.

- [ ] **Step 3: Dispatch the final whole-branch reviewer**

Use the most capable available model. Supply:
- this plan path;
- the Planner fix log and provider-remediation design paths;
- base SHA and HEAD SHA;
- the complete diff/review package;
- permanent cross-agent fix log;
- no conversation history.

Required review focus:

```text
correctness and regression risk
shared-vs-local responsibility boundaries
Planner behavior preservation
agent fallback semantics
provider diagnostic safety/bounds
operation budget isolation
controlled-evaluation integrity
secret/provider-content leakage
missing tests for interfaces changed across tasks
```

- [ ] **Step 4: Resolve final findings once**

For any Critical/Important finding, dispatch one fix task with TDD and then one scoped re-review. Adjudicate remaining Minor findings into the permanent fix log rather than expanding scope indefinitely.

- [ ] **Step 5: Re-run full verification after any final fix**

Run the same full pytest/Ruff/whitespace commands from Step 1 and record final HEAD SHA and counts in the ledger/fix log.

- [ ] **Step 6: Stop before integration side effects**

Do not merge, push, open a PR, deploy, or begin live evaluation as part of this plan. Hand the clean reviewed branch to `superpowers:finishing-a-development-branch` (or the user's chosen integration workflow) as a separate action.

---

## Subagent-Driven Development Execution Contract

At execution time, use the current `superpowers:subagent-driven-development` process rather than giving one agent this entire plan as a monolithic prompt:

1. Initialize/verify the plan-specific SDD workspace and recovery ledger.
2. Before Task 1, run the plan preflight consistency scan: for every pair of tasks sharing a file/interface, record producer/consumer compatibility in the ledger and rule on conflicts before dispatch.
3. Extract each task into a task brief. Give the implementer the brief path as its exact requirements, plus only prior interfaces/rulings it needs.
4. Use a fresh implementer for each judgment-bearing task; batch only same-shape mechanical tests.
5. Implementer runs tests, commits, self-reviews, writes its report artifact, and returns only status/commit/test summary/concerns.
6. Generate a review package and dispatch a fresh task reviewer for spec compliance and code quality.
7. Fix/re-review findings before marking the task complete. Escalate stuck fix loops per the current SDD skill rather than repeatedly using the same failing context.
8. Continue through the plan without pausing between offline tasks. Stop only at the explicit paid-provider confirmation gates, security/destructive/external-side-effect gates, or if the plan is so inconsistent that every path is guesswork.
9. After the final task, run one broad whole-branch review and then the branch-finishing workflow.

## Recommended Subagent Model Routing

Use the least expensive model that can reliably do the role, explicitly selected on every dispatch:

| Work | Suggested capability |
| --- | --- |
| Task 2 shared ReAct two-line implementation after tests are written | cheap/fast implementation model |
| Task 3 multi-agent parity + Planner wrapper removal | standard coding model |
| Task 4 provider diagnostic contract design/implementation | strong standard or high reasoning model |
| Task 5 repetitive agent wiring after Task 4 contract is fixed | standard model; batch same-shape edits if reviewer surface remains coherent |
| Task 6-7 characterization tests | cheap-to-standard model |
| Controlled artifact diagnosis / root-cause amendment | most capable reasoning model |
| Agent-specific prompt/trajectory repair | standard/high depending on evidence complexity |
| Task reviews | at least standard; high for provider contracts/evaluation semantics |
| Final whole-branch review | most capable available model |

## Explicit Non-Goals

- No live-tier evaluation.
- No end-to-end graph quality campaign.
- No change to Planner quality prompts or Planner scoring.
- No global increase above `llm.max_tokens=4096`.
- No blanket creation of token-budget settings for agents that never demonstrated output truncation.
- No judge-budget change; judge-side output-limit remediation is a separate scope from the target-agent audit.
- No replacement of the existing retry policy.
- No evaluation-rubric/case/gate adjustment to improve scores.
- No unrelated refactor of large agent modules.
- No provider switch or reasoning-effort tuning unless a later separately approved evidence-backed amendment explicitly calls for it.

## Plan Self-Review Checklist

Before execution, the controller must confirm:

- Every confirmed transferable Planner issue maps to a task or an explicit no-change rationale.
- RC-A is fixed at the shared boundary rather than copied across agents.
- The historical fix-log statement about Source Evaluator/Synthesizer has been reconciled with current no-ReAct architecture.
- Planner-local wrapper removal happens only after shared RED/GREEN proof.
- Provider fallback diagnostics retain finite typed information but not provider/error text.
- Partial/fallback semantics are explicitly tested for all five non-Planner agents.
- Token-budget changes are conditional on typed output-limit evidence and isolated to one operation.
- ReAct and judge budgets remain global 4096.
- Paid commands are behind immediate human confirmation gates.
- Frozen evaluation inputs are clearly listed.
- No task requires changing a case/rubric/gate to pass.
- Every tracked task ends with a test/review/commit boundary appropriate for a fresh subagent.
- The permanent fix log is part of completion, so future agents do not have to reconstruct this campaign from chat history.
