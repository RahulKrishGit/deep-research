# Agent-fixes verification run — result record

Status: **RUN COMPLETED — quality gate still failed, but coverage now clears the
bar.** This is the result of the predeclaration at
`2026-09-15-agent-fixes-run-predeclaration.md`, committed before the run.

## 1. Run identity

| | |
| --- | --- |
| Candidate | `c1dba552d57ab5d49bd07051dc8db57fdf0c7a62` |
| Session | `b600945da4d54379943f6f81b31d84da` |
| Log | `output/cli-canary-20260915-194430-agentfixes.log` |
| Wall clock | 27.7 minutes |
| Ceilings | DeepSeek 700, OpenAI 60, Tavily 450, `stop_fraction` 1.0 |
| CLI exit | **4** — the report was not accepted by the terminal quality gates |

## 2. What changed, measured against the previous run

| Metric | Before (`ce8911ee…`) | This run |
| --- | --- | --- |
| **Topics covered** | 3/6 (50%) | **6/7 (86%)** — clears the ≥0.80 criterion |
| **`web_scraper` calls** | **0** | **23** |
| researcher reads (scraper + document_reader) | 12 | **29** |
| researcher searches | 183 | 185 (**flat**) |
| Finding subsections in the report | 2 | **5** |
| Reader report length | 1,629 words | 1,758 words (limit 8,000) |
| Cited sources | 5 | 6 |
| Duplicate claims / rows / uncited points | 0 / 0 / 0 | **0 / 0 / 0** |
| Critic | 3/10 | **3/10 — unchanged, still the blocker** |
| DeepSeek / Tavily actuals | 251 / 352 | 268 / 361 (of 700 / 450) |
| Tokens | 749,230 | 820,821 |

**The reading rebalance is confirmed, and it is isolated from budget size.** At
the *same* `agents.tool_budget: 10`, the researcher's reads rose 12 → 29 while
its searches stayed flat. The model was never short of budget for reading; it
spent the budget on discovery because nothing told it to alternate between the
two. That is now the second falsified hypothesis in this line of work — the
first being that the *transport* ceiling was the constraint.

**The run is also the first in this project diagnosable by class from its own
artifacts.** The evidence ledger's `Run errors` table carries
`status_code=403, tool=web_scraper`, `status_code=404`, and each
`agent_tool_budget_exhausted` event with its dropped-call count. Earlier runs
could not say any of that.

## 3. A hypothesis that was killed by measuring rather than assuming

14 of 23 page reads failed, so the natural suspect was the scraper's identity:
`tools/web_scraper.py:72` sends `User-Agent: deep-research/0.1`. Probing the
failing hosts **twice each** — current UA vs a conventional browser UA, status
codes only, no page content read — gives:

| Host | current UA | browser UA |
| --- | --- | --- |
| `emp.lbl.gov` | 403 | **403** |
| `www.utilitydive.com` | 403 | **403** |
| `www.iea.org` | 403 | **403** |
| `www.klgates.com` | 200 | 200 |

**The User-Agent is not the cause.** These publishers refuse this client for
reasons a UA cannot fix, so "set a browser User-Agent" would have been a commit
that changed no behaviour while looking like progress. The failures are not
random either: `emp.lbl.gov` failed **4 times** — LBNL's Energy Markets & Policy
site, *the* authoritative source for the interconnection-queue topic this
question is about.

## 4. Where the shortfall now sits

The critic's objections, quoted from the report's own uncertainty section:

- *"Every supply-chain and critical-mineral claim supplied this pass, including
  those drawn from the IEA Global Critical Minerals Outlook 2025 (source score
  0.82), was graded insufficient evidence, so no supply-chain constraint is
  settled here."*
- *"the high-scoring PNNL-37956 source on local zoning ordinances for energy
  storage (score 0.88) appears in the evidence ledger but was never used to
  produce a checked claim."*
- Safety/fire-code/insurance and project-economics returned **no checked claim
  at all**.

The funnel: **361 searches → 23 scrapes (14 refused) → 9 sources assessed → 6
cited, 3 reviewed-but-uncited** including the 0.88 PNNL report.

**So the binding constraint is no longer retrieval, budget, or fetching
identity. It is the number of *readable, independent* sources per topic.**
Claims are graded "insufficient evidence" because independent corroboration
needs more than one readable source per topic, and several of the strongest
sources available for this question refuse automated reads outright.
