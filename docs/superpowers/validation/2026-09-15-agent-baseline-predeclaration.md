# Agent-performance baseline run — predeclaration

Status: **DECLARED, NOT YET RUN.** Written and committed before the run it
authorizes, so nothing here can be adjusted afterwards to suit the outcome.

## 1. Purpose

The goal is to evaluate **how each of the six agents is doing** and how good the
report is, then change what the evidence implicates. This run is the baseline for
that evaluation. It uses the **unchanged** configuration, so it is directly
comparable to the two spent canaries (Q1 and the scraper-diagnosis run) and any
later changed run can be measured against it.

This is **not** a release gate. The release criteria are recorded separately in
`2026-09-15-release-status.md`; this run exists to produce per-agent evidence.

## 2. Question — unchanged, for comparability

> What are the current constraints on grid-scale battery storage deployment?

## 3. Candidate

- **Candidate commit: `ab7a90f52acaf3d4b9034bfa74187c0ad0cf4313`** — the tip at the
  moment this document was written; the run starts from it.
- The run is refused unless `git rev-parse HEAD` equals the candidate, **or** HEAD
  is a descendant of it whose entire diff is this document and the run's own
  record (the same rule the previous predeclaration used, and for the same
  reason: a predeclaration cannot contain its own commit's SHA).
- The run is refused if `git status --short` shows anything staged or modified.
  The only tolerated entries are the known unrelated untracked paths
  `.deepseek-runs/` and `tools/`, which are never staged.

## 4. Configuration — deliberately unchanged

`reasoning_effort: high`, `agents.tool_budget: 10`, `llm.timeout: 60.0`,
`graph.max_iterations: 3`, and `config.yaml` byte-identical to the base commit.
**No setting is changed for this run**, because its whole value is being a clean
baseline: the previous run left `agents.tool_budget` at 10 and therefore never
tested the lever the evidence points at, and a changed run must be compared
against an unchanged one.

## 5. Declared ceilings — enforced by the `RequestBudget`

| Provider | Ceiling | `stop_fraction` | Rationale |
| --- | ---: | ---: | --- |
| DeepSeek | 700 | 1.0 | The previous run used 251. Headroom for a longer run without permitting a runaway. |
| OpenAI | 60 | 1.0 | Declaratory: the production path is DeepSeek-only (0 attempts last run). |
| Tavily | 450 | 1.0 | The previous run used 352. **The account's own usage limit also applies** and tripped mid-run last time — see §7. |

`stop_fraction` stays at 1.0: an evidence run must reach its natural end.

## 6. Stop conditions

| # | Condition | Action |
| --- | --- | --- |
| S1 | CLI exits **3** (attempt limit reached) | Record actuals, extract evidence, **do not rerun**. A ceiling that fires is a finding. |
| S2 | CLI exits **4** (quality gate not met) | Expected, given the last two runs. Extract the per-agent evidence regardless. |
| S3 | Pre-provider failure (credentials, tracing, environment, probe) | No spend occurred; fix and rerun. |
| S4 | Wall clock exceeds **45 minutes** | Kill and record as failed. The last two runs took 21.0 and 26.4 minutes. |
| S5 | Tavily refuses with `exceeds your plan's set usage limit` | **Record the count of lost searches explicitly** rather than silently degrading: it under-reports coverage for reasons unrelated to the pipeline. A 1-result probe before this run confirmed the account is currently serving requests. |

## 7. Per-agent evaluation — what will be measured, and from where

The artifact-level metrics alone cannot say which agent is underperforming, so
this run is evaluated **per agent**, primarily from the run's LangSmith trace
(which records every agent, iteration, tool call and LLM call with its outcome)
and secondarily from the evidence ledger and the run log.

For each of the six agents, recorded with counts:

- iterations run, and how each terminated (`completed`, `tool_budget_exhausted`,
  `max_iterations`, error);
- LLM calls, tool calls by tool name, and **tool failures by class**;
- for the researcher: subtopics attempted vs completed, searches vs **reads**
  (the funnel that produced only 17 reads against 352 searches last time);
- for the source evaluator: sources seen vs scored vs dropped, and why;
- for the fact checker: claims extracted, malformed, verified, contradicted,
  insufficient;
- for the synthesizer: sections drafted vs rejected;
- for the critic: score, gaps raised, and whether each gap was actionable;
- **every `agent_tool_budget_exhausted` event with the number of provider-requested
  calls it dropped** — the single most likely mechanism behind thin coverage, and
  the thing the next change would target.

Plus whole-report metrics: coverage, critic score, integrity counts, word count,
backmatter share, and the evidence funnel.

## 8. Artifacts

- Run log: `output/cli-canary-<timestamp>-agentbaseline.log`
- Reader report and evidence ledger: the CLI's own output paths for the session.
- Per-agent evaluation: appended to this file's companion record after the run.

## 9. Credentials

Supplied through the repository dotenv launcher by path. No credential value is
printed, logged or recorded.

## 10. Authorization

Within the standing authorization. Three runs are already spent (Q1, its paid
planner-failure attempt, and the scraper-diagnosis canary); this is the fourth.
A Tavily probe costing one search was made immediately before writing this, to
confirm the account serves requests again.
