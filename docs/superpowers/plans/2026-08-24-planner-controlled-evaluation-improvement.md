# Planner Controlled Evaluation and Improvement Campaign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evaluate and iteratively improve only `PlannerAgent` until a clean full controlled evaluation reports `REVIEW REQUIRED`, then produce a secret-safe human review packet and stop before live-tier evaluation.

**Architecture:** Run the existing evaluation harness as the authoritative evidence source from a dedicated Planner branch and linked worktree. Preserve the initial full controlled run as an immutable baseline, diagnose failures from strict artifacts and visible traces before editing, apply one evidence-backed repair at a time with offline TDD, and require a three-repetition focused retest before the final nine-repetition controlled validation. Because the actual defect is unknowable until the baseline provider run completes, the plan contains a mandatory evidence-gated repair amendment: no source edit is allowed until that amendment supplies the literal test, implementation, commands, and rollback for the diagnosed root cause.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest, pytest-asyncio, Ruff, Git worktrees, PowerShell, DeepSeek V4 Flash, LangSmith evaluation and tracing, and the existing fake-driven controlled dependency bundles.

## Global Constraints

- The approved design at `docs/superpowers/specs/2026-08-24-agent-evaluation-improvement-workflow-design.md` is authoritative.
- Scope is `PlannerAgent` only. Do not evaluate or improve Researcher, Source Evaluator, Fact Checker, Synthesizer, or Critic.
- Do not run `python -m deep_research.evaluation suite`, end-to-end graph evaluation, or any `--tier live` command.
- `python -m pytest` and Ruff remain offline and fake-driven. Controlled evaluation intentionally makes real target-LLM, judge-LLM, and LangSmith calls.
- Use branch `codex/evaluate-improve-planner`, worktree `.worktrees/agent-improvement-planner`, and ledger `.superpowers/sdd/agent-improvement-planner/progress.md`.
- At execution start, freeze the exact HEAD of `codex/agent-evaluation-improvement-workflow` as `$ApprovedBase`; that commit must contain this plan and approved specification and must descend from specification commit `2f1d4473a1648a12e7775e83413f02a7f66ffaa9`.
- Freeze the target model at `deepseek-v4-flash`.
- Freeze the judge model at `deepseek-v4-flash` and judge reasoning effort at `max`.
- The Planner baseline target reasoning effort is `max`. A lower Planner-specific effort may be tested only if trace evidence supports a falsifiable over-deliberation or trajectory-control hypothesis after prompt and code causes are ruled out. Never silently change the model.
- Freeze controlled repetitions at `3`, repetition floor at `0.65`, case-average threshold at `0.80`, maximum concurrency at `1`, dataset version at `1`, and rubric version at `1`.
- Freeze the canonical cases `focused-decomposition`, `ambiguous-scope`, and `planning-tool-failure`.
- Freeze evaluation cases, dependency scenarios, rubrics, gates, metric weights, thresholds, judge prompt, and evaluator logic during Planner repair loops.
- A demonstrated harness or evaluator defect stops Planner tuning. Repair it under a separate approved plan and establish a new immutable baseline.
- Do not edit Planner code until the ledger contains a complete evidence-backed root-cause record and literal repair amendment.
- One repair cycle addresses one root-cause ID with one cohesive change set.
- After three unsuccessful focused repairs for the same root-cause ID, write an escalation packet and stop.
- Continue using `LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com`.
- Never print, commit, upload, or quote credentials, hidden chain-of-thought, raw provider payloads, or unredacted exceptions.
- Diagnose only from typed artifacts, concise judge rationale, visible trajectory summaries, tool-call summaries, outputs, state updates, and sanitized target/evaluator traces.
- Do not copy `.env` into a worktree. Use inherited shell variables or the ignored secret-safe launcher defined in Task 1 to load the repository-root `.env` with `override=False`.
- Do not commit `.env`, `.superpowers/`, `output/`, evaluation artifacts, review packets, or escalation packets.
- Do not merge, push, open a pull request, deploy, begin live-tier evaluation, or begin another agent campaign.
- Record the ledger immediately before and after each provider operation so interrupted work resumes without repeating valid runs.
- Report coverage as `Not measured` unless a coverage command is explicitly added and run.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `.superpowers/sdd/agent-improvement-planner/progress.md` | Ignored recovery ledger: state, frozen baseline, diagnoses, attempt counters, commands, commits, artifacts, reviews, and next action. |
| `.superpowers/sdd/agent-improvement-planner/run_with_repo_env.py` | Ignored launcher that loads the repository-root `.env` without overriding inherited variables or printing values, then executes one command. |
| `output/evaluations/planner/*/results.json` | Existing authoritative immutable baseline and candidate artifacts. |
| `output/evaluations/planner/*/review.md` | Ignored final human review packet beside the successful final artifact. |
| `output/evaluations/planner/*/escalation.md` | Ignored conditional evidence packet after three unsuccessful repairs for one root cause. |
| `src/deep_research/agents/planner.py` | Conditional Planner prompt, validation, finalization, tool-policy, or state-update repair target. |
| `tests/test_agents/test_planner.py` | Conditional primary offline regression tests for Planner behavior. |
| `tests/test_agents/test_planner_researcher_seam.py` | Conditional compatibility test only when Planner state output affects the downstream seam. |
| `config.yaml` | Conditional Planner-specific reasoning-effort override only after the reasoning-configuration evidence gate. |
| `tests/test_evaluation/test_config.py` | Conditional configuration test for a Planner reasoning override. |

The following files are frozen verification inputs during agent repair loops:

```text
src/deep_research/evaluation/cases/planner.py
src/deep_research/evaluation/evaluators.py
src/deep_research/evaluation/runner.py
src/deep_research/evaluation/reporting.py
src/deep_research/evaluation/config.py
src/deep_research/evaluation/models.py
src/deep_research/evaluation/judging.py
src/deep_research/evaluation/dependencies.py
src/deep_research/evaluation/targets.py
tests/test_evaluation/test_cases_planner.py
tests/test_evaluation/test_evaluators_agents.py
tests/test_evaluation/test_runner.py
tests/test_evaluation/test_reporting.py
```

For Tasks 2-8, every fresh worker must reconstruct the campaign context before using later command blocks:

```powershell
$Repository = 'C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research'
$CampaignWorktree = Join-Path $Repository '.worktrees\agent-improvement-planner'
Set-Location -LiteralPath $CampaignWorktree

$LedgerPath = '.superpowers\sdd\agent-improvement-planner\progress.md'
$RunWithEnv = '.superpowers\sdd\agent-improvement-planner\run_with_repo_env.py'
$RepoEnv = Join-Path $Repository '.env'
$EnvSource = if (Test-Path -LiteralPath $RepoEnv) { $RepoEnv } else { '-' }

function Get-LedgerValue([string] $Name) {
    $Match = Select-String -LiteralPath $LedgerPath -Pattern "^- $([regex]::Escape($Name)): (.+)$" |
        Select-Object -Last 1
    if ($null -eq $Match) {
        throw "Ledger value is missing: $Name"
    }
    return $Match.Matches[0].Groups[1].Value.Trim()
}

$ApprovedBase = Get-LedgerValue 'Approved base'
if ($ApprovedBase -notmatch '^[0-9a-f]{40}$') {
    throw 'Approved base is not a literal 40-character Git SHA.'
}
```

This context block performs no provider calls and loads no credentials. Task-specific values are read from exact ledger records only after the task that creates them.

---

### Task 1: Create or Resume the Isolated Planner Campaign

**Files:**

- Create, ignored: `.superpowers/sdd/agent-improvement-planner/progress.md`
- Create, ignored: `.superpowers/sdd/agent-improvement-planner/run_with_repo_env.py`
- Verify: approved specification, plan, worktree, and Git state

**Interfaces:**

- Consumes: the clean HEAD of `codex/agent-evaluation-improvement-workflow`, containing the approved specification and this plan.
- Produces: a clean `codex/evaluate-improve-planner` worktree, an ignored recovery ledger, and a secret-safe command launcher.

- [ ] **Step 1: Resolve and verify the approved base**

```powershell
$Repository = 'C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research'
$SpecWorktree = Join-Path $Repository '.worktrees\agent-evaluation-improvement-workflow'
$CampaignWorktree = Join-Path $Repository '.worktrees\agent-improvement-planner'
$CampaignBranch = 'codex/evaluate-improve-planner'
$SpecCommit = '2f1d4473a1648a12e7775e83413f02a7f66ffaa9'

if ((git -C $SpecWorktree branch --show-current).Trim() -ne 'codex/agent-evaluation-improvement-workflow') {
    throw 'The specification worktree is on the wrong branch.'
}
if (git -C $SpecWorktree status --porcelain) {
    throw 'The specification worktree is dirty.'
}

$ApprovedBase = (git -C $SpecWorktree rev-parse HEAD).Trim()
git -C $SpecWorktree merge-base --is-ancestor $SpecCommit $ApprovedBase
if ($LASTEXITCODE -ne 0) {
    throw 'The approved base does not contain the approved specification commit.'
}

git -C $SpecWorktree ls-tree -r --name-only $ApprovedBase -- `
    'docs/superpowers/specs/2026-08-24-agent-evaluation-improvement-workflow-design.md' `
    'docs/superpowers/plans/2026-08-24-planner-controlled-evaluation-improvement.md'
```

Expected: both files are listed and `$ApprovedBase` is an exact clean commit descending from `2f1d447`.

- [ ] **Step 2: Create or verify the campaign worktree**

```powershell
git -C $Repository worktree list --porcelain

if (Test-Path -LiteralPath $CampaignWorktree) {
    if ((git -C $CampaignWorktree branch --show-current).Trim() -ne $CampaignBranch) {
        throw 'The campaign path belongs to a different branch.'
    }
} else {
    if (git -C $Repository branch --list $CampaignBranch) {
        throw 'The campaign branch exists without the expected worktree; inspect before continuing.'
    }
    git -C $Repository worktree add `
        -b $CampaignBranch `
        $CampaignWorktree `
        $ApprovedBase
}

Set-Location -LiteralPath $CampaignWorktree
if ((git branch --show-current).Trim() -ne $CampaignBranch) {
    throw 'Planner campaign branch verification failed.'
}
if (git status --porcelain) {
    throw 'Planner campaign worktree is dirty before setup.'
}
git rev-parse HEAD
```

Expected: `codex/evaluate-improve-planner` at `$ApprovedBase` with a clean tracked state. Never delete or recreate an unexpected worktree or branch automatically.

- [ ] **Step 3: Install development dependencies**

```powershell
python -m pip install -e ".[dev]"
```

Expected: editable installation completes successfully from the Planner worktree.

- [ ] **Step 4: Create the ignored ledger directory**

```powershell
New-Item -ItemType Directory -Force `
    -Path '.superpowers\sdd\agent-improvement-planner' | Out-Null

git check-ignore -v `
    '.superpowers/sdd/agent-improvement-planner/progress.md' `
    '.superpowers/sdd/agent-improvement-planner/run_with_repo_env.py'
```

Expected: `.gitignore` or `.git/info/exclude` ignores both paths. Stop if either path is not ignored.

- [ ] **Step 5: Create the secret-safe environment launcher**

Use `apply_patch` to create `.superpowers/sdd/agent-improvement-planner/run_with_repo_env.py` with exactly:

```python
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from dotenv import load_dotenv


def main() -> int:
    arguments = sys.argv[1:]
    if len(arguments) < 2:
        raise SystemExit(
            "usage: run_with_repo_env.py <env-file-or-> <command> [args ...]"
        )

    env_source = arguments[0]
    if env_source != "-":
        env_file = Path(env_source).resolve()
        if not env_file.is_file():
            raise SystemExit("repository environment file is unavailable")
        load_dotenv(dotenv_path=env_file, override=False)
    completed = subprocess.run(
        arguments[1:],
        env=os.environ.copy(),
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
```

Expected: the launcher emits no environment values and preserves already-set shell/CI variables because `override=False`. Passing `-` skips dotenv loading and uses inherited variables only.

- [ ] **Step 6: Create the recovery ledger**

Use `apply_patch` to create `.superpowers/sdd/agent-improvement-planner/progress.md` with:

```markdown
# Planner Agent Improvement Campaign

## Identity

- Workflow state: BASELINE_REQUIRED
- Agent: planner
- Branch: codex/evaluate-improve-planner
- Worktree: C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\agent-improvement-planner
- Specification: docs/superpowers/specs/2026-08-24-agent-evaluation-improvement-workflow-design.md
- Plan: docs/superpowers/plans/2026-08-24-planner-controlled-evaluation-improvement.md
- Ledger: .superpowers/sdd/agent-improvement-planner/progress.md
- Evaluation root: output/evaluations/planner/

## Frozen Evaluation Contract

- Controlled cases: focused-decomposition, ambiguous-scope, planning-tool-failure
- Controlled repetitions per case: 3
- Repetition floor: 0.65
- Case-average threshold: 0.80
- Target model: deepseek-v4-flash
- Baseline target reasoning effort: max
- Judge model: deepseek-v4-flash
- Judge reasoning effort: max
- Dataset version: 1
- Rubric version: 1
- Maximum concurrency: 1
- LangSmith endpoint: https://eu.api.smith.langchain.com
- Live evaluation: prohibited until explicit human authorization

## Offline Baseline

## Immutable Controlled Baseline

## Failure Inventory

## Root-Cause Records

## Repair Amendments

## Repair Attempts

## Focused Retests

## Full Controlled Validation

## Human Review

## Next Action

Install development dependencies and run the complete offline baseline.
```

After creating the file, insert `- Approved base: ` followed by the literal 40-character value of `$ApprovedBase` under `## Identity`. Do not write variable syntax or an unresolved marker into the saved ledger.

- [ ] **Step 7: Run the complete offline baseline**

```powershell
python -m pytest -q
python -m ruff check .
```

Expected: every non-`live` test passes and Ruff reports `All checks passed!`. Record exact counts, warnings, and exit codes in the ledger. Any failure blocks controlled provider execution.

- [ ] **Step 8: Confirm campaign cleanliness and ignored paths**

```powershell
git status --short
git check-ignore -v `
    '.superpowers/sdd/agent-improvement-planner/progress.md' `
    '.superpowers/sdd/agent-improvement-planner/run_with_repo_env.py' `
    'output/evaluations/planner/example/results.json'
```

Expected: no tracked changes; the ledger, launcher, and evaluation output paths are ignored.

---

### Task 2: Establish the Immutable Full Controlled Baseline

**Files:**

- Update, ignored: `.superpowers/sdd/agent-improvement-planner/progress.md`
- Create through existing harness: `output/evaluations/planner/*/results.json`
- Do not modify source, test, configuration, or evaluator files

**Interfaces:**

- Consumes: `_default_agent_runner(...)`, `build_runtime_config(...)`, `run_agent_evaluation(...)`, the frozen Planner cases, and the ignored environment launcher.
- Produces: one preserved three-case, nine-repetition Planner controlled baseline or a typed infrastructure-blocked record.

- [ ] **Step 1: Verify the repository-root environment without revealing values**

```powershell
$RepoEnv = Join-Path $Repository '.env'
$RunWithEnv = '.superpowers\sdd\agent-improvement-planner\run_with_repo_env.py'
$EnvSource = if (Test-Path -LiteralPath $RepoEnv) { $RepoEnv } else { '-' }

python $RunWithEnv $EnvSource python -c `
    "import os; required=('DEEPSEEK_API_KEY','LANGSMITH_API_KEY','LANGSMITH_PROJECT'); missing=[name for name in required if not os.getenv(name)]; assert not missing, missing; assert os.getenv('LANGSMITH_ENDPOINT') == 'https://eu.api.smith.langchain.com'; print('Provider environment present; values not displayed.')"
```

Expected: a safe confirmation line whether credentials came from the repository-root `.env` or inherited shell/CI variables. The command prints variable names only if required values are missing; it never prints secret values.

- [ ] **Step 2: Verify the frozen configuration without provider calls**

```powershell
python -c "from deep_research.utils.config import load_config; s=load_config('config.yaml', strict=False); e=s.evaluation; assert e.controlled_repetitions == 3; assert e.controlled_repetition_floor == 0.65; assert e.controlled_case_average_threshold == 0.80; assert e.max_concurrency == 1; assert e.dataset_version == 1; assert e.rubric_version == 1; assert e.target_model == 'deepseek-v4-flash'; assert e.target_reasoning_effort == 'max'; assert e.judge_model == 'deepseek-v4-flash'; assert e.judge_reasoning_effort == 'max'; print('Frozen Planner evaluation configuration verified.')"
```

Expected: all assertions pass without network activity.

- [ ] **Step 3: Record the pre-run identity**

```powershell
git rev-parse HEAD
git status --porcelain
python $RunWithEnv $EnvSource python -m deep_research.evaluation list `
    --config config.yaml
```

Expected: clean SHA and exactly these controlled Planner cases:

```text
focused-decomposition
ambiguous-scope
planning-tool-failure
```

Record the SHA, clean status, frozen configuration, and explicit notice that the next command makes real target, judge, and LangSmith calls.

- [ ] **Step 4: Run the full controlled baseline**

Immediately before execution, use `apply_patch` to record the command, start time, current SHA, expected provider-call scope, and next recovery action in the ledger.

```powershell
$ExistingBaselineArtifacts = @(
    Get-ChildItem -LiteralPath 'output\evaluations\planner' -Filter 'results.json' -Recurse -ErrorAction SilentlyContinue |
        ForEach-Object { $_.FullName }
)

python $RunWithEnv $EnvSource python -m deep_research.evaluation agent planner `
    --config config.yaml `
    --experiment-prefix planner-campaign-baseline `
    --verbose

$BaselineExit = $LASTEXITCODE
if ($BaselineExit -notin 0, 1, 3) {
    throw "Unexpected evaluation exit code: $BaselineExit"
}
```

Expected control-flow outcomes:

- Exit `0`: harness status `REVIEW REQUIRED`; complete Steps 5-6, record the baseline artifact under both `Baseline results` and `Final results`, then proceed directly to Task 8.
- Exit `1`: harness status `FAILED`; proceed to Task 3.
- Exit `3`: harness status `INFRASTRUCTURE FAILURE`; apply Task 3's infrastructure branch.

- [ ] **Step 5: Resolve and strictly validate the new baseline artifact**

The production experiment name is `<prefix>-planner-controlled-<UTC timestamp>-<git SHA>`, so resolve the artifact with:

```powershell
$BaselineResults = Get-ChildItem `
    -LiteralPath 'output\evaluations\planner' `
    -Filter 'results.json' `
    -Recurse |
    Where-Object {
        $_.Directory.Name -like 'planner-campaign-baseline-planner-controlled-*' -and
        $_.FullName -notin $ExistingBaselineArtifacts
    } |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1

if ($null -eq $BaselineResults -or $BaselineResults.Count -ne 1) {
    throw 'The controlled baseline did not produce exactly one new results.json.'
}

$env:PLANNER_RESULTS_PATH = $BaselineResults.FullName
python -c "import os; from pathlib import Path; from deep_research.evaluation.models import ExperimentResult; p=Path(os.environ['PLANNER_RESULTS_PATH']); r=ExperimentResult.model_validate_json(p.read_text(encoding='utf-8')); assert r.agent_name == 'planner'; assert r.tier == 'controlled'; assert {c.case_id for c in r.cases} == {'focused-decomposition','ambiguous-scope','planning-tool-failure'}; assert all(len(c.repetitions) == 3 for c in r.cases); print(r.status, r.experiment_name, len(r.cases), sum(len(c.repetitions) for c in r.cases))"
Remove-Item Env:PLANNER_RESULTS_PATH
```

Expected: strict parsing succeeds. A valid full execution records exactly three cases and nine repetitions even when its quality status is `FAILED`.

- [ ] **Step 6: Freeze the baseline in the ledger**

Record:

- baseline Git SHA and clean status;
- exact `results.json` path;
- status, experiment name and URL;
- dataset name and URL when available;
- configuration, prompt, and judge fingerprints;
- target and judge model/effort settings;
- all case and repetition counts;
- exact command, provider-call scope, exit code, and run time.

Use `apply_patch` to append a `Baseline results` ledger entry whose value is the literal output of `$BaselineResults.FullName`. The value must be an absolute path to the validated artifact, not variable syntax or a path pattern.

When the baseline status is `REVIEW REQUIRED`, append an identical `Final results` entry because no repair or second full run is necessary.

Never overwrite, delete, rename, or relabel this artifact.

---

### Task 3: Validate the Verdict and Build the Failure Inventory

**Files:**

- Update, ignored: `.superpowers/sdd/agent-improvement-planner/progress.md`
- Read only: immutable baseline artifact and sanitized LangSmith target/evaluator traces

**Interfaces:**

- Consumes: `ExperimentResult`, `CaseResult`, `RepetitionResult`, `GateReport`, `JudgeFeedback`, and trace URLs.
- Produces: a valid agent-quality diagnosis, `INFRASTRUCTURE_BLOCKED`, a harness-defect stop, or immediate readiness.

- [ ] **Step 1: Extract typed, secret-safe baseline evidence**

```powershell
$BaselineResults = Get-Item -LiteralPath (Get-LedgerValue 'Baseline results')
$env:PLANNER_RESULTS_PATH = $BaselineResults.FullName
python -c "import json, os; from pathlib import Path; from deep_research.evaluation.models import ExperimentResult; r=ExperimentResult.model_validate_json(Path(os.environ['PLANNER_RESULTS_PATH']).read_text(encoding='utf-8')); rows=[{'case_id':c.case_id,'case_average':c.average_quality,'case_passed':c.passed,'repetition':p.repetition,'completed':p.completed,'failed_gates':p.gates.failed_ids,'deterministic':p.deterministic_quality,'judge':None if p.judge is None else p.judge.judge_quality,'aggregate':p.aggregate_quality,'judge_status':None if p.judge is None else p.judge.status,'judge_not_run_reason':None if p.judge is None else p.judge.not_run_reason,'common_dimensions':None if p.judge is None or p.judge.verdict is None else p.judge.verdict.scores.model_dump(),'agent_dimensions':None if p.judge is None or p.judge.verdict is None else p.judge.verdict.agent_specific,'judge_rationale':None if p.judge is None or p.judge.verdict is None else p.judge.verdict.rationale,'trace_url':p.trace_url,'errors':[e.model_dump() for e in p.errors]} for c in r.cases for p in c.repetitions]; print(json.dumps(rows, indent=2))"
Remove-Item Env:PLANNER_RESULTS_PATH
```

Expected: one safe row per repetition. Do not redirect this output into a tracked file.

- [ ] **Step 2: Validate verdict integrity**

Confirm and record:

- artifact fingerprints match the intended baseline;
- exactly the intended controlled cases ran;
- each full-run case has three repetitions;
- every evaluable output has `JudgeFeedback`;
- target trace URLs and required evaluator trace/source links exist;
- no setup, trace, artifact, provider, or judge incident invalidates quality attribution;
- configuration and Git provenance are stable; and
- no known secret appears in the artifact or reviewed trace metadata.

Set the ledger to `DIAGNOSING` only when the evidence is valid.

- [ ] **Step 3: Handle infrastructure evidence without editing Planner**

If the baseline is `INFRASTRUCTURE FAILURE`, record the exact typed reason and perform one clean confirmation:

```powershell
$ExistingConfirmationArtifacts = @(
    Get-ChildItem -LiteralPath 'output\evaluations\planner' -Filter 'results.json' -Recurse -ErrorAction SilentlyContinue |
        ForEach-Object { $_.FullName }
)

python $RunWithEnv $EnvSource python -m deep_research.evaluation agent planner `
    --config config.yaml `
    --experiment-prefix planner-campaign-infrastructure-confirmation `
    --verbose

$ConfirmationExit = $LASTEXITCODE
if ($ConfirmationExit -notin 0, 1, 3) {
    throw "Unexpected confirmation exit code: $ConfirmationExit"
}

$ConfirmationResults = Get-ChildItem `
    -LiteralPath 'output\evaluations\planner' `
    -Filter 'results.json' `
    -Recurse |
    Where-Object {
        $_.Directory.Name -like 'planner-campaign-infrastructure-confirmation-planner-controlled-*' -and
        $_.FullName -notin $ExistingConfirmationArtifacts
    } |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1

if ($null -eq $ConfirmationResults -or $ConfirmationResults.Count -ne 1) {
    throw 'Infrastructure confirmation did not produce exactly one new results.json.'
}
```

Expected:

- If the same infrastructure failure persists, set the ledger to `INFRASTRUCTURE_BLOCKED`, preserve both artifacts, report the evidence, and stop.
- If the confirmation is valid, strictly validate it using Task 2 Step 5, preserve the failed run, and use `apply_patch` to replace the `Baseline results` ledger entry with the literal absolute `$ConfirmationResults.FullName`.
- This confirmation does not count as an agent repair attempt.

- [ ] **Step 4: Separate harness defects from agent defects**

Classify a case, gate, metric, judge adapter, runner, artifact, or status rule as defective only when offline evidence proves it contradicts its declared contract.

Expected: no Planner edit. Record the proof, stop the Planner campaign, and request a separate harness-fix specification/plan followed by a new immutable baseline.

- [ ] **Step 5: Build the complete failure inventory**

For every failed repetition, record:

- failed general and Planner gate IDs and details;
- deterministic metric values;
- common and Planner-specific judge scores;
- concise judge rationale;
- aggregate score and applicable threshold;
- typed target, judge, trace, and artifact failures;
- visible tool sequence, safe observations, and stop reason;
- output and state fields tied to the symptom; and
- comparison with the other two repetitions.

Planner-specific gates:

```text
subtopic_count
distinct_subtopics
valid_subtopics
prioritized_subtopics
question_preserved
```

Controlled deterministic metrics:

```text
focused-decomposition:
  subtopic_count, distinct_titles, priority_ordering,
  query_quality, question_preserved

ambiguous-scope:
  subtopic_count, distinct_titles,
  balanced_coverage, no_invented_constraints

planning-tool-failure:
  plan_still_valid, failure_recorded, bounded_recovery
```

- [ ] **Step 6: Inspect the lowest and failed sanitized traces**

Open the lowest-scoring target trace for each case plus every target/evaluator trace tied to a failed gate, missing judge, or low judge dimension. Record only:

- visible tool choices and ordering;
- safe observation summaries;
- structured result and state update;
- visible stop reason and error types;
- judge source, structured score, and concise rationale; and
- whether the trace corroborates or contradicts the artifact.

Never copy hidden reasoning, raw provider payloads, or credentials into the ledger.

- [ ] **Step 7: Write one root-cause record before any edit**

The record must contain:

- stable root-cause ID;
- case and affected repetitions;
- baseline artifact and experiment;
- category and confidence;
- exact failed gates and low dimensions;
- artifact and trace references;
- cross-repetition pattern;
- falsifiable hypothesis and relevant counterevidence;
- smallest proposed repair;
- predicted gates/dimensions that should improve;
- non-target behavior that must not regress;
- current attempt count; and
- rollback strategy.

Append these machine-readable lines with literal evidence values:

```text
- Active case ID: ambiguous-scope
- Active root-cause ID: planner-ambiguous-scope-001
- Active attempt: 1
```

The shown values demonstrate the required format. Use the actual diagnosed case, stable root-cause ID, and attempt number; all later commands read the final occurrence of each key.

A qualitative diagnosis requires at least two independent signals. If that standard is not met, continue diagnosis without editing.

---

### Task 4: Create the Evidence-Gated Literal Repair Amendment

**Files:**

- Update, ignored: `.superpowers/sdd/agent-improvement-planner/progress.md`
- Do not edit production or tests in this task

**Interfaces:**

- Consumes: one complete root-cause record.
- Produces: a literal, independently reviewed TDD amendment for exactly one repair cycle.

- [ ] **Step 1: Select the smallest permitted repair route**

| Evidence category | Permitted target | Required offline test |
| --- | --- | --- |
| Runtime or contract bug | `src/deep_research/agents/planner.py` | `tests/test_agents/test_planner.py`; include the seam test only if state compatibility is involved. |
| Instruction ambiguity | `PLANNER_SYSTEM_PROMPT`, `PLAN_INSTRUCTION`, or `plan_messages(...)` | Static/message-rendering regression in `tests/test_agents/test_planner.py`. |
| Tool or trajectory policy | Planner-specific prompt, `allowed_tools`, `run`, `finalize`, or `state_update` | Scripted `PlannerAgent` regression in `tests/test_agents/test_planner.py`. |
| Reasoning configuration | `evaluation.target_reasoning_effort_overrides.planner` in `config.yaml` | `tests/test_evaluation/test_config.py`; allowed only after simpler causes are ruled out. |
| Harness/evaluator defect | No Planner file | Stop and request a separate approved plan. |
| Transient infrastructure | No Planner file | One confirmation run, then block if persistent. |

Do not modify shared `BaseAgent`, shared prompt helpers, evaluation cases, gates, metrics, judge logic, thresholds, or controlled dependency scripts in this campaign.

- [ ] **Step 2: Append the literal repair amendment**

The amendment must use the writing-plans task format and include:

- exact files and interfaces;
- complete failing test code;
- exact red command and expected failure;
- complete minimal implementation patch;
- exact focused and neighboring green commands;
- review brief;
- exact staged paths and commit message;
- exact focused case command and expected pass criteria; and
- exact rollback command.

Also append one comma-separated `Repair files` ledger entry containing only the exact repository-relative paths authorized by the amendment. For example, a prompt-only amendment records:

```text
- Repair files: src/deep_research/agents/planner.py, tests/test_agents/test_planner.py
```

This is not deferred implementation. Source editing remains prohibited until the saved amendment contains literal code, has no unresolved markers or vague instructions, and covers every relevant branch.

- [ ] **Step 3: Review the diagnosis and amendment**

Use a fresh task reviewer to verify:

- the hypothesis is supported by artifact and trace evidence;
- the proposed edit is causal and Planner-scoped;
- the test reproduces the observed behavior rather than evaluator internals;
- frozen evaluation criteria remain untouched;
- non-target regression expectations are explicit; and
- provider calls are not needed to prove the offline red phase.

Resolve review findings in the amendment before continuing.

Set the ledger workflow state to `REPAIRING` and record the exact next action.

---

### Task 5: Execute One Offline TDD Repair Cycle

**Files:**

- Modify only files named by the reviewed literal amendment

**Interfaces:**

- Consumes: one approved root-cause amendment.
- Produces: one reviewed, committed candidate SHA associated with one root-cause ID and attempt number.

- [ ] **Step 1: Add the focused regression test**

Use the literal test from the amendment. Name evidence-derived tests with the prefix `test_planner_regression_`.

- [ ] **Step 2: Run the exact focused test and verify red**

Run the node ID or `-k` expression from the amendment, for example:

```powershell
python -m pytest tests/test_agents/test_planner.py `
    -k 'planner_regression' `
    -v
```

Expected: the new regression fails for the diagnosed defect, not because of malformed setup. Record the failure text in the ignored ledger.

If no honest offline reproduction exists, do not fabricate one. The amendment must explain why and name the existing tests that establish the pre-edit behavior.

- [ ] **Step 3: Apply the smallest reviewed implementation**

Use `apply_patch` and the literal amendment. Do not bundle prompt, code, tool-policy, and configuration experiments.

- [ ] **Step 4: Run focused and neighboring offline tests**

```powershell
python -m pytest `
    tests/test_agents/test_planner.py `
    tests/test_agents/test_planner_researcher_seam.py `
    tests/test_evaluation/test_cases_planner.py `
    tests/test_evaluation/test_evaluators_agents.py `
    tests/test_evaluation/test_dependencies_controlled.py `
    tests/test_evaluation/test_targets.py `
    tests/test_evaluation/test_config.py `
    -q

python -m ruff check `
    src/deep_research/agents/planner.py `
    tests/test_agents/test_planner.py `
    tests/test_agents/test_planner_researcher_seam.py `
    tests/test_evaluation/test_config.py

git diff --check
```

Expected: selected tests pass, Ruff passes, and the diff has no whitespace errors. Do not pass `config.yaml` to Ruff.

- [ ] **Step 5: Obtain scoped code review before provider calls**

Use `requesting-code-review` against the root-cause record and literal amendment. Resolve every relevant finding and rerun Step 4.

- [ ] **Step 6: Commit one cohesive repair**

Read the active repair identity and exact file allow-list from the ledger. Stage only files permitted by the amendment:

```powershell
git status --short
$RootCauseId = Get-LedgerValue 'Active root-cause ID'
$Attempt = [int](Get-LedgerValue 'Active attempt')
$AmendmentFiles = @(
    (Get-LedgerValue 'Repair files').Split(',') |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ }
)
$ModifiedFiles = @(git diff --name-only)
$AllowedRepairFiles = @(
    'src/deep_research/agents/planner.py',
    'tests/test_agents/test_planner.py',
    'tests/test_agents/test_planner_researcher_seam.py',
    'config.yaml',
    'tests/test_evaluation/test_config.py'
)
$UnexpectedFiles = @($ModifiedFiles | Where-Object { $_ -notin $AllowedRepairFiles })
if ($UnexpectedFiles) {
    throw "Repair changed files outside the allowed scope: $($UnexpectedFiles -join ', ')"
}
$UnapprovedFiles = @($ModifiedFiles | Where-Object { $_ -notin $AmendmentFiles })
if ($UnapprovedFiles) {
    throw "Repair changed files outside the reviewed amendment: $($UnapprovedFiles -join ', ')"
}
if (-not $ModifiedFiles) {
    throw 'Repair produced no tracked changes to commit.'
}

git add -- $ModifiedFiles
git diff --cached --check
git diff --cached --name-only
git diff --cached
git commit -m "fix(planner): $RootCauseId attempt $Attempt"

$CandidateCommit = (git rev-parse HEAD).Trim()
```

Expected: one causal repair commit. Use `apply_patch` to record `- Candidate commit: ` followed by the literal 40-character `$CandidateCommit`, plus the diff summary, exact tests, review result, and rollback point in the ledger.

---

### Task 6: Run the Three-Repetition Focused Controlled Retest

**Files:**

- Update, ignored: recovery ledger
- Create through existing harness: one focused controlled `results.json`

**Interfaces:**

- Consumes: one clean candidate commit and one canonical failing case ID.
- Produces: candidate-versus-baseline evidence for all three repetitions.

- [ ] **Step 1: Verify the provider-run gate**

```powershell
if (git status --porcelain) {
    throw 'Tracked worktree must be clean before focused controlled evaluation.'
}
git rev-parse HEAD
```

Confirm the ledger contains the root-cause record, reviewed literal amendment, offline red/green evidence, candidate SHA, configuration fingerprints, `$CaseId`, `$RootCauseId`, and `$Attempt`.

Load the exact active values before constructing the experiment prefix:

```powershell
$CaseId = Get-LedgerValue 'Active case ID'
$RootCauseId = Get-LedgerValue 'Active root-cause ID'
$Attempt = [int](Get-LedgerValue 'Active attempt')
$CandidateCommit = Get-LedgerValue 'Candidate commit'
if ((git rev-parse HEAD).Trim() -ne $CandidateCommit) {
    throw 'HEAD does not match the candidate commit recorded in the ledger.'
}
```

- [ ] **Step 2: Run the canonical focused case**

Set the ledger workflow state to `FOCUSED_RETEST`. Immediately before execution, use `apply_patch` to record the command, start time, candidate SHA, configuration fingerprints, expected provider-call scope, and next recovery action.

```powershell
$FocusedPrefix = "planner-candidate-$RootCauseId-a$Attempt"
$ExistingFocusedArtifacts = @(
    Get-ChildItem -LiteralPath 'output\evaluations\planner' -Filter 'results.json' -Recurse -ErrorAction SilentlyContinue |
        ForEach-Object { $_.FullName }
)

python $RunWithEnv $EnvSource python -m deep_research.evaluation agent planner `
    --config config.yaml `
    --case $CaseId `
    --experiment-prefix $FocusedPrefix `
    --verbose

$FocusedExit = $LASTEXITCODE
if ($FocusedExit -notin 0, 1, 3) {
    throw "Unexpected focused evaluation exit code: $FocusedExit"
}
```

Expected: three target runs and three judge evaluations are requested. This is controlled evaluation, not live-tier evaluation.

- [ ] **Step 3: Resolve and strictly validate the focused artifact**

```powershell
$FocusedResults = Get-ChildItem `
    -LiteralPath 'output\evaluations\planner' `
    -Filter 'results.json' `
    -Recurse |
    Where-Object {
        $_.Directory.Name -like "$FocusedPrefix-planner-controlled-*" -and
        $_.FullName -notin $ExistingFocusedArtifacts
    } |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1

if ($null -eq $FocusedResults -or $FocusedResults.Count -ne 1) {
    throw 'Focused controlled evaluation did not produce exactly one new results.json.'
}

$env:PLANNER_RESULTS_PATH = $FocusedResults.FullName
python -c "import os; from pathlib import Path; from deep_research.evaluation.models import ExperimentResult; r=ExperimentResult.model_validate_json(Path(os.environ['PLANNER_RESULTS_PATH']).read_text(encoding='utf-8')); assert r.agent_name == 'planner'; assert r.tier == 'controlled'; assert len(r.cases) == 1; assert len(r.cases[0].repetitions) == 3; print(r.status, r.cases[0].case_id, r.cases[0].average_quality)"
Remove-Item Env:PLANNER_RESULTS_PATH
```

Expected: strict parsing succeeds for exactly one case and three repetitions.

- [ ] **Step 4: Compare focused candidate with baseline**

Record without intermediate rounding:

- gate transitions;
- deterministic, judge, and aggregate deltas;
- every common and Planner-specific dimension delta;
- score spread;
- new error types;
- latency and token deltas when supplied; and
- predicted non-target behavior.

The focused case passes only if:

- all three repetitions complete;
- every hard gate passes;
- each aggregate score is at least `0.65`;
- the case average is at least `0.80`;
- each evaluable repetition has scored judge feedback;
- required target and evaluator traces exist;
- no severe, secret, artifact, or infrastructure failure occurs; and
- no unrelated behavior regresses.

- [ ] **Step 5: Route the result**

If the case passes, mark the repair successful and either diagnose the next baseline failure or proceed to Task 7.

If it fails:

- return to Task 3 with the new evidence;
- increment the same root-cause counter only when the symptom is materially unchanged;
- create a new root-cause ID only for a demonstrably different cause; and
- revert a disproved candidate before an independent hypothesis:

```powershell
git revert --no-edit $CandidateCommit
```

After three failed focused repairs for one root cause, use `apply_patch` to create `escalation.md` beside the latest artifact with all hypotheses, commits, diffs, experiments, deltas, evidence, current diagnosis, and recommended options. Set the ledger to `ESCALATED` and stop.

Infrastructure failures do not count as repair attempts. Confirm once, then set `INFRASTRUCTURE_BLOCKED` if persistent.

---

### Task 7: Run Final Offline and Full Controlled Validation

**Files:**

- Verify: complete tracked repository and frozen evaluation files
- Create through existing harness: final full controlled `results.json`

**Interfaces:**

- Consumes: all successful focused repairs on one clean candidate commit.
- Produces: the authoritative final nine-repetition controlled result.

- [ ] **Step 1: Run complete offline verification**

```powershell
python -m pytest -q
python -m ruff check .
git diff --check
git status --short
```

Expected: all non-`live` tests pass, Ruff passes, no diff errors, and no uncommitted tracked changes. Record exact counts and warnings. Record `Coverage: Not measured`.

- [ ] **Step 2: Audit scope and frozen evaluation criteria**

```powershell
git diff --name-only "$ApprovedBase...HEAD"
git diff "$ApprovedBase...HEAD" -- `
    src/deep_research/evaluation/cases/planner.py `
    src/deep_research/evaluation/evaluators.py `
    src/deep_research/evaluation/runner.py `
    src/deep_research/evaluation/reporting.py `
    src/deep_research/evaluation/config.py `
    src/deep_research/evaluation/judging.py `
    src/deep_research/evaluation/dependencies.py `
    src/deep_research/evaluation/targets.py
```

Expected: the second command is empty. Any evaluator/harness change blocks validation and requires separate approval and a new baseline.

- [ ] **Step 3: Scan the tracked diff for known secrets**

```powershell
$env:PLANNER_BASE_SHA = $ApprovedBase
python $RunWithEnv $EnvSource python -c "import os, subprocess; from deep_research.evaluation.config import contains_secret, known_secret_values; base=os.environ['PLANNER_BASE_SHA']; diff=subprocess.run(['git','diff',f'{base}...HEAD'],capture_output=True,text=True,check=True).stdout; hits=contains_secret(diff, known_secret_values(os.environ)); assert not hits, hits; print('Tracked diff contains no known secret values.')"
Remove-Item Env:PLANNER_BASE_SHA
```

Expected: safe confirmation only.

- [ ] **Step 4: Obtain final whole-branch review**

Use `requesting-code-review` against:

- approved specification and this plan;
- immutable baseline;
- all root-cause records and literal amendments;
- all repair commits and rollbacks;
- offline verification;
- frozen evaluation criteria; and
- secret and scope checks.

Resolve findings and repeat Steps 1-3 before provider execution.

- [ ] **Step 5: Run the full controlled Planner validation**

Set the ledger workflow state to `FULL_VALIDATION`. Immediately before execution, use `apply_patch` to record the command, start time, current SHA, configuration fingerprints, expected provider-call scope, and next recovery action.

```powershell
$ExistingFinalArtifacts = @(
    Get-ChildItem -LiteralPath 'output\evaluations\planner' -Filter 'results.json' -Recurse -ErrorAction SilentlyContinue |
        ForEach-Object { $_.FullName }
)

python $RunWithEnv $EnvSource python -m deep_research.evaluation agent planner `
    --config config.yaml `
    --experiment-prefix planner-campaign-final `
    --verbose

$FinalExit = $LASTEXITCODE
if ($FinalExit -notin 0, 1, 3) {
    throw "Unexpected final evaluation exit code: $FinalExit"
}
```

Expected success: nine target runs, nine judge evaluations, exit `0`, and harness status `REVIEW REQUIRED`.

- [ ] **Step 6: Resolve and validate the final artifact identity**

```powershell
$FinalResults = Get-ChildItem `
    -LiteralPath 'output\evaluations\planner' `
    -Filter 'results.json' `
    -Recurse |
    Where-Object {
        $_.Directory.Name -like 'planner-campaign-final-planner-controlled-*' -and
        $_.FullName -notin $ExistingFinalArtifacts
    } |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1

if ($null -eq $FinalResults -or $FinalResults.Count -ne 1) {
    throw 'Final controlled validation did not produce exactly one new results.json.'
}

$CurrentShortSha = (git rev-parse --short=7 HEAD).Trim()
if (-not $FinalResults.Directory.Name.EndsWith("-$CurrentShortSha")) {
    throw 'Final artifact does not belong to the current candidate SHA.'
}
```

Expected: the selected artifact belongs to the final prefix and current candidate SHA. Use `apply_patch` to append `- Final results: ` followed by the literal absolute `$FinalResults.FullName` to the ledger.

- [ ] **Step 7: Route any full-run regression**

- `REVIEW REQUIRED`: proceed to Task 8.
- `FAILED`: return to Task 3; focused success never overrides another failing case.
- `INFRASTRUCTURE FAILURE`: perform one clean confirmation; block if persistent.
- Never run `--tier live`.

---

### Task 8: Produce the Planner Human Review Packet and Stop

**Files:**

- Create, ignored: `review.md` beside `$FinalResults`
- Update, ignored: `.superpowers/sdd/agent-improvement-planner/progress.md`

**Interfaces:**

- Consumes: immutable baseline, final `ExperimentResult`, trace links, repair history, Git history, reviews, and offline verification.
- Produces: a secret-safe human review packet and workflow state `REVIEW_REQUIRED`.

- [ ] **Step 1: Strictly validate final readiness**

```powershell
$FinalResults = Get-Item -LiteralPath (Get-LedgerValue 'Final results')
$env:PLANNER_CANDIDATE_SHA = (git rev-parse HEAD).Trim()
$env:PLANNER_RESULTS_PATH = $FinalResults.FullName
python -c "import os; from pathlib import Path; from deep_research.evaluation.models import ExperimentResult; r=ExperimentResult.model_validate_json(Path(os.environ['PLANNER_RESULTS_PATH']).read_text(encoding='utf-8')); assert r.agent_name == 'planner'; assert r.tier == 'controlled'; assert r.status == 'REVIEW REQUIRED'; assert r.metadata.get('git_commit') == os.environ['PLANNER_CANDIDATE_SHA']; assert len(r.cases) == 3; assert sum(len(c.repetitions) for c in r.cases) == 9; assert not r.errors; assert all(c.passed for c in r.cases); assert all(p.completed and p.gates.passed and p.aggregate_quality is not None and p.aggregate_quality >= 0.65 and not p.errors for c in r.cases for p in c.repetitions); assert all(c.average_quality is not None and c.average_quality >= 0.80 for c in r.cases); assert all(p.judge is not None and p.judge.status == 'scored' and p.judge.evaluator_trace_url and p.judge.evaluator_source_url for c in r.cases for p in c.repetitions); assert all(p.trace_url for c in r.cases for p in c.repetitions); assert all(c.lowest_scoring_trace_url for c in r.cases); print('Planner controlled readiness contract verified.')"
Remove-Item Env:PLANNER_RESULTS_PATH
Remove-Item Env:PLANNER_CANDIDATE_SHA
```

Expected: every assertion passes.

- [ ] **Step 2: Write `review.md` beside the final artifact**

Use `apply_patch`. The packet must contain actual evidence under:

```markdown
# Planner Controlled Evaluation Human Review Packet

## Readiness Status
## Baseline and Candidate Identity
## Frozen Evaluation Configuration
## Controlled Provider-Call Scope
## Offline Verification
## Case and Repetition Results
## Lowest-Scoring Traces
## Gate Transitions
## Deterministic Score Deltas
## Judge and Aggregate Score Deltas
## Judge-Dimension Deltas
## Root Causes and Repair Attempts
## Code and Configuration Changes
## Failed Candidates and Rollbacks
## Cost and Usage Limitations
## Residual Risks and Shared-Change Impact
## Human Review Gate
```

Required facts:

- final status `REVIEW REQUIRED`;
- baseline and candidate SHAs;
- baseline and final experiment, dataset, artifact, target trace, evaluator trace, and evaluator source links;
- lowest-scoring trace for every controlled case;
- all gate, deterministic, judge, aggregate, and dimension deltas;
- every diagnosis, amendment, repair attempt, failed candidate, and rollback;
- exact test and Ruff commands and results;
- `Coverage: Not measured`;
- model, effort, prompt, configuration, dataset, rubric, and Git fingerprints;
- provider-run counts and known cost limitations;
- residual risks and any shared-path impact; and
- exact statement `Live evaluation: Not run; awaiting human approval`.

Never include raw provider payloads, hidden reasoning, credentials, or unredacted exceptions.

- [ ] **Step 3: Validate review packet structure and secrets**

```powershell
$ReviewPath = Join-Path $FinalResults.Directory.FullName 'review.md'

$RequiredContent = @(
    '# Planner Controlled Evaluation Human Review Packet',
    '## Readiness Status',
    '## Baseline and Candidate Identity',
    '## Offline Verification',
    '## Case and Repetition Results',
    '## Lowest-Scoring Traces',
    '## Gate Transitions',
    '## Judge-Dimension Deltas',
    '## Root Causes and Repair Attempts',
    '## Residual Risks and Shared-Change Impact',
    'Live evaluation: Not run; awaiting human approval'
)

foreach ($Item in $RequiredContent) {
    if (-not (Select-String -LiteralPath $ReviewPath -SimpleMatch $Item)) {
        throw "Review packet is missing required content: $Item"
    }
}

$env:PLANNER_REVIEW_PATH = $ReviewPath
python $RunWithEnv $EnvSource python -c "import os; from pathlib import Path; from deep_research.evaluation.config import contains_secret, known_secret_values; text=Path(os.environ['PLANNER_REVIEW_PATH']).read_text(encoding='utf-8'); hits=contains_secret(text, known_secret_values(os.environ)); assert not hits, hits; print('Review packet contains no known secret values.')"
Remove-Item Env:PLANNER_REVIEW_PATH
```

Expected: every section exists and the secret scan passes.

- [ ] **Step 4: Close at the human gate**

Update the ledger with exact values:

```text
Workflow state: REVIEW_REQUIRED
Next action: Await human review of controlled Planner evidence.
Live evaluation: Not run.
Other agent campaigns: Not started.
```

Report:

- exact final SHA, branch, and worktree;
- exact pytest and Ruff results;
- baseline and final artifact paths;
- final experiment URL;
- review packet path;
- coverage status;
- controlled provider-call scope; and
- live evaluation not run.

Stop without merging, pushing, opening a pull request, running live-tier evaluation, or starting another agent.
