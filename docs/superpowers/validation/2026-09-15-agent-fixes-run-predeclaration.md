# Agent-fixes verification run — predeclaration

Status: **DECLARED, NOT YET RUN.** Committed before the run it authorizes.

## 1. Purpose

The baseline run (`083f77bf…`, job `pwsh-10`) **died at the planner** with
`graph_planning_failed` — the second such failure in four runs — so it produced
no agent evaluation at all. Three changes have since been made, each with its
own evidence. This run measures whether they work, and is the first run in which
the researcher's reading behaviour can be compared against a measured baseline
(183 searches : 12 reads : 0 scrapes, every loop pinned at its 10-call budget).

This is **not** a release gate. It is a verification run for specific changes.

## 2. Question — unchanged, for comparability

> What are the current constraints on grid-scale battery storage deployment?

Every previous run used this question, so search behaviour and coverage are
directly comparable.

## 3. Candidate

- **Candidate commit: `c1dba552d57ab5d49bd07051dc8db57fdf0c7a62`** — the tip at the
  moment this document was frozen, and the commit the run starts from.
- Refused unless `git rev-parse HEAD` equals the candidate, **or** HEAD is a
  descendant whose entire diff is this document and the run's own record.
- Refused if `git status --short` shows anything staged or modified. Only the
  known unrelated untracked paths `.deepseek-runs/` and `tools/` are tolerated.

## 4. What changed since the baseline, and what each change is expected to do

| Change | Commit | Expected effect, and how it is measured |
| --- | --- | --- |
| Planner structured-validation diagnostic surfaced into `PlanningError.problems` | `07352e6` | If the planner fails again, the run's own error details name the failing field and category. **Measured: does `graph_planning_failed` details carry a bounded field path?** |
| `SubTopicDraft` tolerates a lone `str` where `list[str]` is declared | *(this run)* | The planner survives the one-bracket wobble that killed two runs. **Measured: does the run get past planning at all?** |
| Researcher prompt requires reading before re-searching; `web_scraper` description is capability-first | *(this run)* | Reads rise relative to searches. **Measured: `tool.web_scraper` count > 0, and the search:read ratio falls below the baseline's 15:1.** |

**Configuration is unchanged** — `reasoning_effort: high`, `agents.tool_budget: 10`,
`llm.timeout: 60.0`, `graph.max_iterations: 3`, `config.yaml` byte-identical to
base. The per-loop tool budget is deliberately **not** raised in this run: the
baseline showed every researcher loop ending at exactly 10 calls, so the budget
is binding, and keeping it fixed while the reading instruction changes isolates
*how the budget is spent* from *how large it is*. If reads rise at a fixed
budget, the rebalance worked; if they do not, the next experiment is the budget
itself.

## 5. Declared ceilings

| Provider | Ceiling | `stop_fraction` |
| --- | ---: | ---: |
| DeepSeek | 700 | 1.0 |
| OpenAI | 60 | 1.0 |
| Tavily | 450 | 1.0 |

Baseline actuals for comparison: DeepSeek 7 (run died early), Tavily 0. The last
*successful* run used DeepSeek 251 / Tavily 352.

## 6. Stop conditions

| # | Condition | Action |
| --- | --- | --- |
| S1 | CLI exits **3** (attempt limit) | Record actuals, do not rerun. |
| S2 | CLI exits **4** (quality gate) | Expected. The agent measurements are the point; extract them regardless. |
| S3 | Pre-provider failure | No spend; fix and rerun. |
| S4 | Wall clock exceeds **45 minutes** | Kill; record as failed. |
| S5 | Tavily refuses with the account usage limit | Count the lost searches explicitly — it silently under-reports coverage. |

## 7. What will be measured, and from where

Per agent, from the run's trace (`.superpowers/sdd/analyze_agents.py`) and
per-loop (`.superpowers/sdd/analyze_loops.py`), both scratch tools written for
this work:

- LLM calls, ReAct iterations, tool calls by tool, tool failures by class;
- **per-loop tool sequences**, to see whether reading now interleaves with
  searching inside the same 10-call budget;
- the evidence funnel: searches → reads → sources scored → claims verified →
  topics covered;
- whole report: coverage, critic score, integrity counts, word count.

## 8. Artifacts

- Run log: `output/cli-canary-<timestamp>-agentfixes.log`
- Reader report and evidence ledger: the CLI's own output paths.
- Evaluation: appended to this document's companion record after the run.

## 9. Credentials

Supplied through the repository dotenv launcher by path. No credential value is
printed, logged or recorded.

## 10. Authorization

Fourth run of the standing authorization. A one-search Tavily probe was made
earlier in this session and confirmed the account serves requests.
