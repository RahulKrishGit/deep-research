# Publisher-retention run — predeclaration

Status: **DECLARED, NOT YET RUN.** Committed before the run it authorizes.

## 1. Purpose

Two fixes landed since the last run, both targeting the corroboration bottleneck
that keeps the critic at 4/10 while coverage is already 100%:

1. **`bound_sub_topic_findings` now groups by publisher, not by URL.** Its
   docstring promised that "one verbose publisher cannot fill the whole
   allowance and push an independent second source out of the report", and the
   code did not do that: four pages from one publisher were four groups and
   could take all four retention slots. Downstream a claim can only be verified
   by a publisher other than the ones that made it, so losing an independent
   publisher is a corroboration loss, not a tidiness one.
2. **The fact checker's verification prompt now demands reads over searches.**
   Its loops were making ~146 searches against ~31 reads, and a claim whose
   verification loop read nothing independent is recorded
   `insufficient_evidence` with **no verdict call at all** — so searches spent
   without reading cost the claim its verdict outright.

**One thing this run is NOT testing, deliberately.** A reviewer flagged an
evidence-handoff mismatch: extraction merges same-fact findings and attaches
every supporting URL, and the fact checker then excludes all attached
publishers, so a claim built from A+B could only be verified by a third
publisher C. **That mechanism is real in the code but latent in these runs**:
all 18 claims in the last run carry exactly one URL on one domain (cells are
31-180 characters against a 240-character render limit, so nothing is
truncated). Nothing was excluded that could have counted. The merge is
model-side — the code only validates that attached URLs come from the findings
— so with one publisher per fact there is nothing to merge. Fixing the handoff
is still worthwhile as a guard, but it is not what this run measures, and
changing verification semantics is on hold pending the reviewer's answer on
what the intended rule is.

## 2. Question — the standing baseline

> What are the current constraints on grid-scale battery storage deployment?

Unchanged by user direction: the battery question stays the baseline until the
agents are fixed.

## 3. Candidate

- **Candidate commit: `0391c579b4119e7d9b21113fb63077aabe51ea90`**
- Refused unless `git rev-parse HEAD` equals the candidate, **or** HEAD is a
  descendant whose entire diff is this document and the run's own record.
- Refused if `git status --short` shows anything staged or modified. Only the
  known unrelated untracked paths `.deepseek-runs/` and `tools/` are tolerated.

## 4. Configuration — unchanged

`reasoning_effort: high`, `agents.tool_budget: 10`, `llm.timeout: 60.0`,
`graph.max_iterations: 3`, `config.yaml` byte-identical to base.

## 5. Declared ceilings

DeepSeek 700, OpenAI 60, Tavily 450, `stop_fraction` 1.0. Previous actuals:
DeepSeek 258, Tavily 352.

## 6. Stop conditions

S1 exit 3 (attempt limit) → record, do not rerun. S2 exit 4 (quality gate) →
expected. S3 pre-provider failure → no spend, rerun. S4 wall clock > 45 min →
kill. S5 Tavily account usage limit → count the lost searches explicitly.

## 7. What will be measured, and the pre-committed interpretation

| Measure | Last run | Success looks like |
| --- | --- | --- |
| Topics covered | 6/6 (100%) | Holds |
| Integrity (dupes / rows / uncited) | 0 / 0 / 0 | Stays 0 |
| **Scrape failure rate** | 18 of 41 (44%) | Falls |
| **Claims with ≥2 independent domains** | **0 of 18** | **Rises above 0 — the point** |
| Claims verified | 3 of 18 | Rises |
| **Critic score** | **4/10** | Rises toward 7 |
| Report length | 2,332 words | ≤ 8,000 |

**Pre-committed null result.** If distinct *publishers per sub-topic* rise but
claims still show one domain each, then the retention cap was never the
constraint either, and the remaining explanation is that the researcher reads
one source per fact and stops — which points at the plan's success criteria and
the per-loop tool budget (61 dropped provider-requested calls last run), not at
retention or prompts.

## 8. Artifacts

Log `output/cli-canary-<timestamp>-publisherretention.log`; evaluation via
`.superpowers/sdd/evaluate_run.py` plus the per-agent and per-loop trace tools.

## 9. Credentials

Via the repository dotenv launcher by path. No credential value is printed,
logged or recorded.

## 10. Authorization

Eighth run of the standing authorization.
