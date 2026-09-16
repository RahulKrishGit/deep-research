# Corroboration run — predeclaration

Status: **DECLARED, NOT YET RUN.** Committed before the run it authorizes.

## 1. Purpose

The previous run (`b600945d…`) moved coverage from 3/6 (50%) to **6/7 (86%)** —
clearing the ≥0.80 criterion — but the critic stayed at **3/10** and the CLI
still exited 4. The critic's own words locate the remaining failure:

> *"Every supply-chain and critical-mineral claim supplied this pass, including
> those drawn from the IEA Global Critical Minerals Outlook 2025 (source score
> 0.82), was graded insufficient evidence."*

The mechanism is in the code: `fact_checker.independent_domains` refuses to
corroborate a claim with a page on the claim's own publisher's domain, and a
claim with no independent source is recorded `insufficient_evidence`. But the
plan only ever asked for *"at least one success criterion describing what
evidence would settle it"* — never for a second, independent source. **The plan
under-specified what verification requires, so the researcher satisfied the
criteria it was given and stopped.**

This run tests the fix for that.

## 2. Question — unchanged, for comparability

> What are the current constraints on grid-scale battery storage deployment?

## 3. Candidate

- **Candidate commit: `9e8f03dc8fbaddc4a78781923afc6a36d008ccc8`**
- Refused unless `git rev-parse HEAD` equals the candidate, **or** HEAD is a
  descendant whose entire diff is this document and the run's own record.
- Refused if `git status --short` shows anything staged or modified. Only the
  known unrelated untracked paths `.deepseek-runs/` and `tools/` are tolerated.

## 4. The single change under test

Both prompts now require independent corroboration:

- `PLAN_INSTRUCTION` — every load-bearing number or finding in a sub-topic needs
  a second source from a different publisher, and each sub-topic's queries must
  include one aimed at an independent second source for its key facts.
- The researcher's system prompt — a sub-topic is not finished when its key
  facts come from a single publisher; spend a remaining call on that second
  source rather than another query for the same one.

**Configuration is unchanged**: `reasoning_effort: high`,
`agents.tool_budget: 10`, `llm.timeout: 60.0`, `graph.max_iterations: 3`,
`config.yaml` byte-identical to base. **One change, so attribution is clean.**

## 5. Declared ceilings

| Provider | Ceiling | `stop_fraction` |
| --- | ---: | ---: |
| DeepSeek | 700 | 1.0 |
| OpenAI | 60 | 1.0 |
| Tavily | 450 | 1.0 |

Previous run's actuals: DeepSeek 268, Tavily 361. Tavily is the nearer bound and
the account's own usage limit also applies.

## 6. Stop conditions

| # | Condition | Action |
| --- | --- | --- |
| S1 | CLI exits **3** (attempt limit) | Record actuals, do not rerun. |
| S2 | CLI exits **4** (quality gate) | Expected unless the fix worked. Extract the evidence regardless. |
| S3 | Pre-provider failure | No spend; fix and rerun. |
| S4 | Wall clock exceeds **45 minutes** | Kill; record as failed. |
| S5 | Tavily refuses with the account usage limit | Count the lost searches explicitly. |

## 7. What will be measured

Against the previous run, per agent (trace tools
`.superpowers/sdd/analyze_agents.py` and `analyze_loops.py`):

| Measure | Previous | What would show the fix worked |
| --- | --- | --- |
| Topics covered | 6/7 (86%) | Holds or improves — the fix must not cost coverage |
| Claims **verified** | 2 | **Rises** — this is the point of the change |
| Claims `insufficient_evidence` | most | Falls |
| Distinct source domains per topic | ~1 | **Rises** — corroboration needs a second domain |
| Cited sources | 6 | Rises |
| **Critic score** | **3/10** | **Rises; ≥ the terminal bar is the goal** |
| Integrity (dupes / rows / uncited) | 0/0/0 | Must stay 0 |
| Tavily actuals | 361/450 | Held inside the ceiling |
| Reader report length | 1,758 words | Must stay ≤ 8,000 |

**A null result is a finding too**: if the prompts demand corroboration and the
critic does not move, then the constraint is the evidence actually available on
this question — several of the strongest sources refuse automated reads
(`emp.lbl.gov` 403 four times, and a browser User-Agent does not change that) —
and the next change belongs in sourcing, not in verification.

## 8. Artifacts

- Run log: `output/cli-canary-<timestamp>-corroboration.log`
- Reader report and evidence ledger: the CLI's own output paths.
- Result: a companion record committed after the run.

## 9. Credentials

Supplied through the repository dotenv launcher by path. No credential value is
printed, logged or recorded.

## 10. Authorization

Fifth run of the standing authorization.
