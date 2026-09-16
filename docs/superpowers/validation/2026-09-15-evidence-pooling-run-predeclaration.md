# Evidence-pooling run — predeclaration

Status: **DECLARED, NOT YET RUN.** Committed before the run it authorizes.

## 1. Purpose

`2bc6665` widens the admissible verification evidence to the union of the
verification loop's own reads and the URLs the run read **upstream**. It was the
one change the diagnosis identified as blocking **9 of 12** non-verified claims:
evidence the researcher had already read was discarded by
`valid_verification_passages`, after which `resolve_verdict` returned
`insufficient_evidence` with confidence 0 and made **no judgment at all**.

Unit tests prove the admissibility change works. They do not prove it moves the
gate. This run is the first live test of it.

No threshold, independence rule, contradiction precedence or acceptance score
changed — only which read URLs are admissible, never what counts as support.

## 2. Question — the standing baseline (unchanged by user direction)

> What are the current constraints on grid-scale battery storage deployment?

## 3. Candidate

`2bc6665de59d0cec3d76f0bd0cdc5bd92ba3b180`. Refused unless `git rev-parse HEAD`
equals it, **or** HEAD is a descendant whose entire diff is this document and the
run's own record. Refused if `git status --short` is non-empty apart from the
known unrelated untracked `.deepseek-runs/` and `tools/`.

## 4. Configuration — unchanged

`reasoning_effort: high`, `agents.tool_budget: 10`, `llm.timeout: 60.0`,
`graph.max_iterations: 3`. One change only.

## 5. Ceilings

DeepSeek 700, OpenAI 60, Tavily 450, `stop_fraction` 1.0. Last actuals: DeepSeek
268, Tavily 312.

## 6. Stop conditions

Exit 3 → record, no rerun. Exit 4 → expected. Pre-provider failure → no spend.
Wall clock > 45 min → kill. Tavily account limit → count lost searches.

## 7. Measured, and the pre-committed interpretation

| Measure | Last run | Success |
| --- | --- | --- |
| Critic | 4/10 | Rises |
| Claims verified | 6 of 18 | **Rises materially** |
| `insufficient_evidence` claims | 9 | Falls |
| Topics covered | 6/6 (100%) | Holds |
| Integrity (dupes/rows/uncited) | 0/0/0 | Holds |

**Pre-committed null result.** If verified stays ~6 and insufficient stays ~9
while the `Reason` column shows the previously-blocked claims were already
admissible under the old rule, then pooling did not address the measured
population, and the next lever is the per-loop tool budget (66-74 dropped
provider-requested calls per run) or the model-declines-`verified` cases — the
remaining diagnosis items 2 and 3.

## 8. Artifacts

Log `output/cli-canary-<timestamp>-evidencepooling.log`; evaluation via
`.superpowers/sdd/evaluate_run.py` and `.superpowers/sdd/audit_reasons.py`.

## 9. Authorization

Tenth run of the standing authorization.
