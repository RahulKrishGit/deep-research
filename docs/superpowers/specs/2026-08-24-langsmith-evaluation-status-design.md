# LangSmith Evaluation Status Design

**Status:** Approved for implementation

**Date:** 2026-08-24

## Goal

Expose the individual-agent evaluation harness's final status in the
LangSmith experiment view so a reviewer can identify a failed evaluation
without reconstructing the result from multiple gate columns or opening the
local artifact.

## Context

The harness currently computes the authoritative final status locally after
all repetitions have been evaluated:

- `REVIEW REQUIRED` means every case passed the automated checks and needs
  human approval.
- `FAILED` means at least one case failed a hard gate, score threshold, or
  repetition requirement.
- `INFRASTRUCTURE FAILURE` means setup or tracing failed.

LangSmith already receives row-level gate, deterministic-quality, judge, and
judge-status feedback. It also receives the final aggregate status as
experiment-level feedback, but live verification showed that the current
Experiments view does not render that project feedback as a table field. The
same two authoritative values must therefore also be written to the
experiment project's metadata, which the Experiments view does render.

## Decision

Add an experiment-level LangSmith summary evaluator named
`evaluation_status`. It returns a categorical value with exactly the same
status literals as the local `EvaluationStatus` contract. The summary
evaluator runs after the target and row-level evaluators, when the runner's
per-case repetition results are complete.

The summary evaluator also emits a second experiment-level feedback key,
`evaluation_failure_reason`, containing a short, deterministic, secret-safe
summary. Examples:

- `all cases passed automated checks; human review required`
- `focused-decomposition repetition 2 failed prioritized_subtopics`
- `trace:langsmith_unavailable`

Failure summaries use only case IDs, repetition numbers, gate IDs, and typed
internal failure reasons. They never include raw provider errors, judge
rationales, prompts, outputs, or secret-bearing text.

After evaluation completes, publish the same two values as project metadata
using the public `evaluation_results.experiment_id`. Read the existing project
metadata, merge the two status keys over it, and update the project. Preserve
all unrelated LangSmith metadata; only `evaluation_status` and
`evaluation_failure_reason` are replaced. This metadata projection exists to
make the status visible in the Experiments view; it is not a second source of
status authority.

## Data flow

1. `aevaluate` executes the target and existing row-level evaluators.
2. The summary evaluator reads the runner's already-populated repetition
   results and computes the same case results and status used for the local
   `ExperimentResult`.
3. LangSmith stores the two summary feedback values against the experiment
   project, not against an individual target run.
4. The runner reads the completed experiment project's metadata, merges the
   same two status values, and updates the project through the injected
   LangSmith client. A missing project ID or metadata update failure is
   recorded as a verdict-neutral observability error.
5. The local artifact and CLI continue to use the same status calculation and
   remain the source of the full structured report.

No numeric gate or judge metric changes. No individual trace is marked as an
error merely because the aggregate quality decision is `FAILED`.

## Error handling

If summary feedback submission fails, the evaluation result remains valid and
the failure is recorded as a safe tracing/infrastructure error using the
existing runner error path. A missing LangSmith summary must not change the
local quality verdict.

If project metadata publication fails, the evaluation result remains valid and
the failure is recorded as `trace:langsmith_project_metadata_unavailable`.
Metadata publication errors, like summary-upload and URL errors, are excluded
from `decide_status`; they must never rewrite a quality verdict or exit code.

If `aevaluate` fails before summary evaluation can run, the existing local
`INFRASTRUCTURE FAILURE` behavior remains authoritative; the implementation
will not fabricate a successful summary.

## Testing

Offline tests will verify:

- the summary evaluator returns the exact local status for passing, quality
  failure, and infrastructure-failure inputs;
- project metadata publication uses the same status and reason values while
  preserving unrelated metadata;
- project metadata read/update failures are recorded and verdict-neutral;
- failure summaries contain only safe deterministic identifiers;
- `run_agent_evaluation` passes the summary evaluator to `aevaluate`;
- existing row-level metrics and local artifacts remain unchanged; and
- all existing tests plus Ruff remain clean.

No test will call LangSmith, DeepSeek, OpenAI, Tavily, or another live
provider.

## Out of scope

- Changing gate definitions, judge weights, thresholds, or exit codes.
- Duplicating the final status onto every target or judge row.
- Changing the LangSmith project names or endpoint.
- Adding a dashboard, alert, or automation beyond the experiment feedback and
  project-metadata columns.
