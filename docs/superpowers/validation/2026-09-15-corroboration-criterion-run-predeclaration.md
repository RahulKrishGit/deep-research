# Corroboration-criterion run — predeclaration

Status: **DECLARED, NOT YET RUN.** Committed before the run it authorizes.

## 1. Purpose

The last run isolated the blocker precisely: with **64 document reads and 11
scored sources**, the pipeline still produced **zero** claims with a second
independent publisher — because a sub-topic is finished as soon as one source
answers it. The previous wording asked for corroboration *beside* the success
criteria, which the Researcher could satisfy and then stop anyway.

`3441ca2` moves the demand **into the criterion the Researcher stops on**: every
success criterion must require that at least two sources from different
publishers state each load-bearing fact, and say how a reader would recognise
the second one. This run tests whether that changes what the run holds.

## 2. Question — the standing baseline

> What are the current constraints on grid-scale battery storage deployment?

## 3. Candidate

`3441ca210e7857caf6ed4c0c4b1136d771c46c86`. Refused unless `git rev-parse HEAD`
equals it, **or** HEAD is a descendant whose entire diff is this document and the
run's own record. Refused if `git status --short` is non-empty apart from the
known unrelated untracked `.deepseek-runs/` and `tools/`.

## 4. Configuration — unchanged

`reasoning_effort: high`, `agents.tool_budget: 10`, `llm.timeout: 60.0`,
`graph.max_iterations: 3`. One change only, so attribution stays clean.

## 5. Ceilings

DeepSeek 700, OpenAI 60, Tavily 450, `stop_fraction` 1.0. Last actuals: DeepSeek
268, Tavily 312.

## 6. Stop conditions

Exit 3 → record, no rerun. Exit 4 → expected. Pre-provider failure → no spend,
rerun. Wall clock > 45 min → kill. Tavily account limit → count lost searches.

## 7. Measured, and the pre-committed interpretation

| Measure | Last run | Success |
| --- | --- | --- |
| Critic | 4/10 | Rises |
| **Claims with ≥2 independent domains** | **0 of 19** | **Rises above 0** |
| Claims verified | 4 | Rises |
| Topics covered | 5/6 (83%) | Holds ≥ 0.80 |
| Integrity (dupes/rows/uncited) | 0/0/0 | Holds |
| Report length | — | ≤ 8,000 words |

**Pre-committed null result.** If corroboration is still 0 while the plan now
demands it and the researcher reads documents reliably, then sourcing is not
constrained by instruction at all, and the remaining levers are structural: the
per-loop tool budget (**70 provider-requested calls dropped last run, the
highest yet**) and the fact checker's verification rule, which the reviewer has
now specified as pooled evidence with ≥2 distinct publishers **and** ≥2 distinct
works.

## 8. Artifacts

Log `output/cli-canary-<timestamp>-corroborationcriterion.log`; evaluation via
`.superpowers/sdd/evaluate_run.py`.

## 9. Authorization

Ninth run of the standing authorization.
