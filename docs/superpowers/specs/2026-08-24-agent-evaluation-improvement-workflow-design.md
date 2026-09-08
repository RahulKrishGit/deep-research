# Agent Evaluation and Iterative Improvement Workflow Design

**Status:** Design approved; pending written-spec review

**Date:** 2026-08-24

## Goal

Define a Codex-driven workflow that evaluates and improves each production
agent independently until its controlled evaluation reaches the existing
`REVIEW REQUIRED` status. The workflow must identify the exact gates and
quality dimensions responsible for a failure, establish a defensible root
cause, apply the smallest relevant agent change, and retest without weakening
the evaluation criteria.

The six agents are handled one at a time in production pipeline order:

1. Planner
2. Researcher
3. Source Evaluator
4. Fact Checker
5. Synthesizer
6. Critic

Codex pauses for human review after each agent reaches `REVIEW REQUIRED`.
It does not automatically start that agent's live evaluation or proceed to the
next agent.

## Context

The repository already has an individual-agent evaluation harness with:

- three controlled cases and one live case per agent;
- three repetitions per controlled case;
- deterministic general and agent-specific hard gates;
- deterministic quality metrics;
- a structured LLM judge with common and agent-specific dimensions;
- aggregate repetition and case thresholds;
- LangSmith target and evaluator traces;
- local strict JSON artifacts;
- experiment and suite status reporting; and
- the final statuses `FAILED`, `REVIEW REQUIRED`, and
  `INFRASTRUCTURE FAILURE`.

This design does not replace that harness or create a self-modifying runtime.
It defines how Codex uses the existing harness as the authoritative source of
evaluation evidence while making ordinary, reviewable repository changes.

## Approved Decisions

- The workflow is driven by Codex during development, not by a new autonomous
  application feature.
- Each agent is handled in a dedicated branch and linked worktree.
- Agents are handled sequentially in production pipeline order.
- Codex stops for human trace review when the current agent reaches controlled
  `REVIEW REQUIRED`.
- Live evaluation always requires explicit human authorization.
- Codex may change the current agent's prompts, agent-specific code, tool-use
  behavior, and reasoning configuration.
- The configured target model remains frozen during an agent campaign.
- Evaluation cases, datasets, rubrics, gates, weights, and thresholds remain
  frozen during repair loops unless they are demonstrably defective.
- A harness or evaluator defect is repaired separately and requires a new
  baseline before agent tuning resumes.
- Three unsuccessful focused repairs for the same root cause trigger a human
  escalation rather than unbounded tuning.
- Pytest and Ruff remain offline. Controlled evaluation intentionally makes
  real target-LLM, judge-LLM, and LangSmith calls.

## Scope

### In Scope

- A repeatable agent-by-agent development lifecycle.
- Isolated worktrees and branches.
- Immutable per-agent controlled baselines.
- Failure inventory and root-cause analysis.
- Prompt, code, tool-policy, and reasoning-effort repairs.
- Offline regression tests for reproducible defects.
- Focused three-repetition controlled retests.
- Full nine-repetition controlled validation.
- Persistent local recovery ledgers.
- Secret-safe human review packets.
- Explicit invalidation when shared changes affect previously reviewed agents.

### Out of Scope

- Automatic live evaluation.
- End-to-end graph evaluation.
- Automatic merge, push, pull request, or deployment.
- Changing the configured target model during a campaign.
- Automatically changing datasets, rubrics, gates, weights, or thresholds to
  make an agent pass.
- Training or fine-tuning model weights.
- Using hidden chain-of-thought as diagnostic evidence.
- A new production scheduler, daemon, dashboard, or self-editing CLI.
- Treating `REVIEW REQUIRED` as human approval.

## Campaign Decomposition

The six-agent effort is a sequence of six development campaigns rather than a
single mutable branch. Each campaign begins from the latest human-approved
base and ends at a human gate.

Recommended branch and worktree names are:

```text
branch:   codex/evaluate-improve-<agent>
worktree: .worktrees/agent-improvement-<agent>
ledger:   .superpowers/sdd/agent-improvement-<agent>/progress.md
```

The ignored ledger is operational state, not a source-of-truth replacement for
Git or evaluation artifacts. It exists so another Codex session can resume
without repeating completed provider runs.

## Workflow State Machine

The recovery ledger uses workflow states separate from the evaluation
harness's `EvaluationStatus` contract:

1. `BASELINE_REQUIRED`
2. `DIAGNOSING`
3. `REPAIRING`
4. `FOCUSED_RETEST`
5. `FULL_VALIDATION`
6. `REVIEW_REQUIRED`
7. `ESCALATED`
8. `INFRASTRUCTURE_BLOCKED`

`REVIEW_REQUIRED` is entered only when the existing harness also reports
`REVIEW REQUIRED` for a clean full controlled run.

### 1. Isolate the Agent

Create a dedicated worktree and branch from the latest approved base. Before
making changes, record:

- agent name;
- branch and worktree paths;
- base and current Git SHA;
- dirty/clean status;
- dataset, case, and rubric versions;
- gate, weight, and threshold configuration;
- target and judge model configuration fingerprints;
- target and judge reasoning settings; and
- output and ledger paths.

Install the development dependencies and establish a clean offline baseline
with the complete pytest suite and Ruff.

### 2. Establish the Immutable Controlled Baseline

Run the current agent's complete controlled evaluation:

```powershell
python -m deep_research.evaluation agent <agent>
```

This runs three controlled cases with three repetitions each. Preserve the
resulting local artifact and LangSmith experiment as the campaign baseline.
Never overwrite it or relabel a later result as the baseline.

Record:

- experiment and dataset identifiers and URLs;
- artifact path;
- case and repetition completion;
- every hard-gate result;
- deterministic, judge, and aggregate scores;
- all common and agent-specific judge dimensions;
- lowest-scoring trace per case;
- typed errors and judge-not-run reasons;
- model, prompt, rubric, configuration, and Git fingerprints; and
- latency and token usage when supplied.

If the baseline reaches `REVIEW REQUIRED`, skip repair and prepare the human
review packet. It is still not automatically approved.

### 3. Validate the Verdict

Before attributing a result to agent quality, Codex confirms that the evidence
is valid:

- requested fingerprints match the artifact and experiment;
- the intended dataset and cases ran;
- required target and judge traces are available;
- evaluable output has judge feedback;
- no LangSmith transport failure invalidated a trace requirement;
- no artifact write or parse failure hid completed results; and
- no provider or dependency incident explains the symptom.

A failed verdict-integrity check moves the workflow to
`INFRASTRUCTURE_BLOCKED`; it does not authorize an agent change.

### 4. Build a Failure Inventory

For each failing repetition, record:

- failed hard-gate IDs and details;
- deterministic metric values;
- common and agent-specific judge scores;
- judge rationale;
- aggregate score and applicable threshold;
- typed target, judge, trace, and artifact errors;
- relevant visible trajectory and safe tool summaries;
- output and state fields tied to the failure; and
- comparison with the other two repetitions.

Codex groups repeated symptoms without collapsing distinct root causes. A hard
gate and a low judge dimension may describe the same defect, but this must be
demonstrated from evidence rather than assumed.

### 5. Produce a Root-Cause Record

No agent edit is allowed until the ledger contains a root-cause record with:

- a stable root-cause ID;
- agent, case, repetitions, and baseline experiment;
- root-cause category and confidence;
- exact failed gates and low dimensions;
- artifact and trace references;
- cross-repetition pattern;
- a falsifiable hypothesis;
- relevant counterevidence;
- the smallest proposed change;
- predicted gates and dimensions that should improve;
- non-target behavior that must not regress;
- repair-attempt count; and
- rollback strategy.

A deterministic invariant can support a high-confidence diagnosis by itself.
Qualitative diagnoses normally require at least two independent signals, such
as a judge dimension plus matching trajectory evidence, or the same symptom in
multiple repetitions.

## Root-Cause Taxonomy

### Runtime or Contract Bug

The agent mishandles typed state, validation, budgets, errors, or deterministic
domain behavior. Repair agent code and add an offline regression test before
the implementation change.

### Instruction Ambiguity

The prompt omits, obscures, or conflicts with an observable requirement.
Change the smallest responsible prompt clause and preserve unrelated behavior.

### Tool or Trajectory Policy

The agent selects, sequences, budgets, or recovers from tools incorrectly.
Change only the relevant tool policy or agent orchestration behavior.

### Capability or Reasoning-Configuration Ceiling

Evidence shows that correct instructions and code are insufficient at the
current agent-specific reasoning setting. A reasoning-effort change is allowed
only after simpler fixes are ruled out. It creates a newly fingerprinted
candidate. The configured model is not changed.

### Harness or Evaluator Defect

The dataset, case, gate, deterministic evaluator, judge adapter, artifact
builder, or status calculation is wrong or internally inconsistent. Stop agent
tuning, prove the defect with offline evidence, repair it separately, and
create a new immutable baseline. Never count this as an agent repair attempt.

### Transient Provider or Infrastructure Failure

The evidence points to a temporary provider, network, tracing, or service
failure rather than agent behavior. Perform one clean confirmation run. If it
persists, move to `INFRASTRUCTURE_BLOCKED` and diagnose it separately. Do not
count it as an agent repair attempt.

## Repair Selection

Choose the smallest causal lever in this order:

1. correctness or contract handling;
2. prompt instructions;
3. tool-use or trajectory policy; and
4. agent-specific reasoning effort.

One focused repair cycle contains one documented hypothesis and one cohesive
change set. Do not bundle speculative prompt, code, tool, and configuration
changes merely to increase the chance of a score improvement.

When a reproducible code or contract defect is involved:

1. add a focused offline test that fails for the observed defect;
2. run it and record the expected failure;
3. implement the smallest repair;
4. run the focused test and neighboring agent tests; and
5. obtain scoped code review before spending provider calls on reevaluation.

Prompt-only or reasoning-only changes still require static and existing prompt
or configuration tests where applicable. If no honest offline reproduction is
possible, record that limitation rather than inventing a fake test.

## Focused Retest

Rerun the affected canonical controlled case with all three configured
repetitions:

```powershell
python -m deep_research.evaluation agent <agent> --case <case-id>
```

The focused case passes only when:

- every repetition completes;
- every hard gate passes;
- every repetition's aggregate score is at least `0.65`;
- the three-repetition case average is at least `0.80`;
- every evaluable repetition has judge feedback;
- required target and evaluator traces are available;
- no severe or secret-redaction error occurs; and
- predicted non-target behavior does not regress.

Compare the candidate with the immutable baseline without intermediate score
rounding. Record gate changes, every judge-dimension delta, deterministic,
judge, and aggregate deltas, score spread, latency, token usage, and new error
types.

A passing focused case advances to full validation. A failure returns to
diagnosis. A genuinely different root cause receives its own stable ID and
attempt counter.

## Three-Cycle Escalation

After three unsuccessful focused repairs for the same root-cause ID, Codex
enters `ESCALATED` and pauses. The escalation packet contains:

- all three hypotheses and changes;
- exact commits and diffs;
- focused experiment and artifact links;
- gate and score deltas for every attempt;
- evidence supporting or disproving each hypothesis;
- current best root-cause assessment; and
- recommended next options with cost and risk.

Codex does not evade the limit by renaming an unchanged symptom. The counter
resets only for a materially different, evidence-backed root cause.

## Full Controlled Validation

After all focused failures are repaired:

1. establish a clean candidate commit;
2. run the complete offline pytest suite;
3. run Ruff over the repository;
4. run any relevant secret and diff checks;
5. obtain scoped review of the complete agent change;
6. run all three controlled cases with three repetitions each; and
7. write and validate the final local artifact.

The agent is ready for human review only when the existing harness reports
`REVIEW REQUIRED`. This requires:

- all nine repetitions to complete;
- all hard gates to pass;
- every repetition to meet the `0.65` floor;
- every case average to meet the `0.80` threshold;
- all required judge feedback and traces to exist;
- no severe, infrastructure, or secret-redaction failure; and
- no artifact integrity failure.

A full-run regression returns to `DIAGNOSING`; focused success never overrides
a failure in another case.

## Test and Provider-Call Boundary

### Offline Verification

The following make no LLM, LangSmith, Tavily, web, cloud-memory, or other live
provider calls:

- `python -m pytest`;
- `python -m ruff check .`;
- static prompt and configuration checks;
- artifact-model round trips using fakes; and
- secret and diff checks.

Repository tests remain fully fake-driven.

### Controlled Evaluation

Controlled evaluation intentionally makes real calls to:

- the configured target LLM;
- the configured judge LLM; and
- LangSmith for datasets, experiments, feedback, and traces.

Controlled dependencies such as external search, persistent memory, and other
application services remain scripted or isolated according to the existing
harness.

A focused case requests at least three target-agent runs and three judge
evaluations. A full agent run requests at least nine target-agent runs and nine
judge evaluations. Actual provider completion counts can be higher because an
agent run may include multiple ReAct iterations, structured-output repair, or
provider retries.

The six-agent suite is not used by this workflow because agents are reviewed
one at a time.

### Live Evaluation

Live evaluation uses real applicable dependencies in addition to real target
and judge LLM calls. It is never started automatically. After reviewing the
controlled packet, the user may authorize a separate live run for the current
agent.

## Evidence and Recovery Artifacts

### Existing Machine Artifact

The harness's existing artifact remains authoritative:

```text
output/evaluations/<agent>/<experiment-id>/results.json
```

### Recovery Ledger

The ignored recovery ledger is:

```text
.superpowers/sdd/agent-improvement-<agent>/progress.md
```

It records:

- current workflow state;
- branch, worktree, and Git SHAs;
- frozen baseline configuration and artifact;
- completed offline and provider commands with exact results;
- failure inventory and root-cause records;
- repair attempt counters;
- candidate commits and rollback points;
- focused and full experiment links;
- review findings and fix loops;
- pending invalidations; and
- the single next action.

On resume, Codex must inspect the worktree, branch, Git status, and ledger
before dispatching work or rerunning an evaluation. Completed provider runs are
not repeated unless their evidence is invalid or the relevant candidate
changed.

### Human Review Packet

When ready, write a secret-safe Markdown packet beside the final artifact:

```text
output/evaluations/<agent>/<experiment-id>/review.md
```

It contains:

- final `REVIEW REQUIRED` status;
- baseline and candidate Git SHAs;
- baseline and final experiment, dataset, and artifact links;
- the lowest-scoring target trace for each controlled case;
- evaluator trace and source links where required;
- failed-gate-to-passing-gate transitions;
- baseline-to-candidate deterministic, judge, aggregate, and dimension deltas;
- every diagnosed root cause and repair attempt;
- exact code and configuration changes;
- exact offline verification results and coverage status;
- current model and reasoning fingerprints;
- provider-call scope and known cost limitations;
- residual risks, shared-change impact, and invalidations; and
- the explicit statement `Live evaluation: Not run; awaiting human approval`.

The packet never includes raw secrets, hidden chain-of-thought, complete raw
provider payloads, or unredacted exceptions.

## Error Handling

### Offline Baseline Failure

Do not start controlled provider evaluation from a failing repository
baseline. Diagnose the failure or ask the user whether the known baseline
failure should block the campaign.

### Agent Evaluation Failure

Preserve every completed result. Continue remaining repetitions and cases when
the existing harness says infrastructure is trustworthy, then diagnose from
the complete evidence set.

### Judge Failure

A required judge result that is absent prevents readiness. Determine whether
the cause is target output, judge provider behavior, schema validation, or
evaluator infrastructure before editing the agent.

### LangSmith or Artifact Failure

A required missing trace or invalid artifact prevents readiness. Verdict-
neutral metadata-publication errors retain their existing semantics, but the
review packet must report them honestly.

### Secret Detection

Stop immediately. Do not upload, commit, or quote affected content. Redact it,
verify containment, and only resume after the evidence path is safe.

### Interruption

Update the ledger before long provider operations and after every completed
phase. A resumed session continues from the last verified state rather than
starting the campaign again.

## Anti-Overfitting and Evaluation Integrity

- Freeze canonical evaluation cases, datasets, rubrics, gates, weights,
  thresholds, and target model for the campaign.
- Never change an evaluator merely because it exposes an agent weakness.
- Require proof of an evaluator defect before changing evaluation code.
- Add offline regression tests to reproduce defects, not to replace the
  canonical controlled cases.
- Require full controlled validation after every focused repair sequence.
- Track every judge dimension, not only the aggregate score.
- Treat a score improvement accompanied by a new gate failure or unrelated
  regression as a failed candidate.
- Preserve immutable baseline and failed-candidate artifacts for comparison.

## Shared-Change Invalidation

A change to a shared base agent, prompt helper, tool contract, state contract,
provider adapter, or other shared behavior may invalidate a previously
reviewed agent.

Before the current agent can reach `REVIEW REQUIRED`, Codex identifies every
previously reviewed agent that can execute the changed path. Each affected
agent returns to full controlled validation on the new candidate base. The
review packet lists the invalidation and its replacement evidence.

If scope is uncertain, invalidate rather than assume isolation.

## Human Review Gate

When the current agent satisfies the readiness definition, Codex:

1. updates the ledger to `REVIEW_REQUIRED`;
2. writes and validates the review packet;
3. reports the exact offline test and Ruff results;
4. presents the baseline/final comparisons and required traces; and
5. stops.

The user may then:

- request another diagnosis or repair;
- approve controlled evidence and authorize the live run; or
- stop the campaign.

Codex does not integrate the branch or begin the next agent until the user
explicitly approves the current agent's next step.

## Security

- Continue using `LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com` for
  this workspace.
- Load ignored environment configuration without printing credential values.
- Never commit `.env`, local evaluation artifacts, recovery ledgers, or normal
  research memory.
- Never include hidden chain-of-thought in traces, diagnoses, review packets,
  datasets, evaluator inputs, or commits.
- Use only visible trajectory summaries and concise structured judge rationale
  for diagnosis.
- Scan outgoing evaluation metadata and local review packets for known secret
  values.
- Preserve the existing controlled dependency isolation and unique evaluation
  session paths.

## Verification Strategy

### Before Any Controlled Baseline

- Verify the exact worktree and branch.
- Verify a clean Git state.
- Install development dependencies.
- Run the complete offline test suite.
- Run Ruff.

### Before Every Focused Retest

- Confirm the root-cause record is complete.
- Confirm the repair is agent-scoped or record shared invalidations.
- Run the new regression test and neighboring agent tests.
- Run scoped review and resolve findings.
- Confirm the candidate Git SHA and configuration fingerprints.

### Before Full Controlled Validation

- Commit the candidate.
- Verify a clean Git state.
- Run the complete offline test suite.
- Run Ruff and relevant secret/diff checks.
- Confirm canonical dataset, rubric, gates, weights, thresholds, and model are
  unchanged.

### Before Claiming Ready

- Validate the final JSON artifact against strict models.
- Confirm all nine repetitions and every required judge result and trace.
- Recompute readiness from unrounded values.
- Confirm the harness reports `REVIEW REQUIRED`.
- Confirm the review packet is complete and secret-safe.
- Report live evaluation as not run.

## Acceptance Criteria

- Each agent campaign uses a dedicated linked worktree and branch.
- The campaign begins from a clean offline baseline.
- The initial complete controlled run is preserved as an immutable baseline.
- Every edit has a prior root-cause record and one causal hypothesis.
- Harness or infrastructure failures are not misclassified as agent defects.
- Canonical evaluation criteria and the target model remain frozen during
  repair loops.
- Focused retests use all three configured repetitions of the failing case.
- A full nine-repetition controlled run follows focused success.
- Three failed attempts for one root cause trigger a human escalation.
- No live evaluation starts without explicit user approval.
- `pytest` and Ruff make no live provider calls.
- Controlled evaluation makes explicit real target, judge, and LangSmith calls.
- The final agent status is `REVIEW REQUIRED` before human review begins.
- The review packet contains exact evidence, comparison data, test results,
  coverage status, and residual risks without secrets or hidden reasoning.
- Shared changes trigger controlled reevaluation of affected previously
  reviewed agents.
- Codex pauses after each agent rather than automatically continuing through
  the six-agent sequence.

## First Execution Unit

After this specification is reviewed, the first implementation plan covers the
Planner campaign only:

1. create the Planner campaign worktree, branch, and recovery ledger;
2. establish offline and controlled baselines;
3. diagnose Planner failures;
4. run evidence-driven repair loops under the approved limits;
5. complete full controlled validation; and
6. stop with the Planner review packet.

Researcher planning begins only after the Planner human gate is resolved.
