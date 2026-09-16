# Re-source run — predeclaration

Status: **DECLARED, NOT YET RUN.** Committed before the run it authorizes.

## 1. Purpose

The previous run tested whether *asking* for corroboration would produce it. It
did not, and that null result is the useful part:

```
claims: 13 -> 2 verified, 1 unverified, 10 insufficient_evidence   (unchanged)
distinct domains per claim: [1,1,1,1,1,1,1,1,1,1,1,1,1]
claims with >= 2 independent domains: 0                            (unchanged)
web_scraper: 37 attempts, 24 FAILED (status_code=403 x10, 404 x8)
document_reader: 30 attempts, 2 failed
```

**The prompt was not the constraint.** The pipeline cannot *read* what it finds:
24 of 37 page reads were refused. Tracing the actual URLs explains why, and it
is not host blocking:

| Tool | Host | Outcome |
| --- | --- | --- |
| `web_scraper` | `emp.lbl.gov` HTML pages | **failed 7 times** (403) |
| `document_reader` | `emp.lbl.gov` PDFs | **succeeded 6 times** |
| `document_reader` | `eta-publications.lbl.gov` PDFs | succeeded 14 times |
| `web_scraper` | assorted publisher HTML pages | 15 of 37 succeeded |

The authoritative material for this question **is** reachable — as documents.
The pipeline burns its budget retrying HTML pages that refuse it, because the
failure said only *"the page request failed with an HTTP error status"* and told
the agent nothing about what to do next.

## 2. Question — unchanged, for comparability

> What are the current constraints on grid-scale battery storage deployment?

## 3. Candidate

- **Candidate commit: `87b0050215fb6d03fe59d24d2ef3777c91fb0192`**
- Refused unless `git rev-parse HEAD` equals the candidate, **or** HEAD is a
  descendant whose entire diff is this document and the run's own record.
- Refused if `git status --short` shows anything staged or modified. Only the
  known unrelated untracked paths `.deepseek-runs/` and `tools/` are tolerated.

## 4. The changes under test

1. **An access denial is now actionable and bounded.** `web_scraper` returns a
   distinct static sentence for 401/402/403/451: *"the publisher refused
   automated access to this page; read the same material from a document or
   another publisher."* Other HTTP failures keep the existing generic sentence.
   Details are unchanged (`attempts`, `retries`, `status_code`).
2. **The researcher is told not to retry a refused host**, and to read the same
   material as a document (`document_reader` handles PDFs and data files) or from
   a different publisher.
3. **Diagnostic only, no behavioural effect:** the evidence ledger now publishes
   the details of `researcher_sub_topic_skipped` (locally-stamped `coverage_id`,
   integer `priority`, enumerated `reason`, summarised title), so a lost
   sub-topic says *which* one and *why*. The previous run lost three and the
   artifact could not say.

**Configuration is unchanged**: `reasoning_effort: high`,
`agents.tool_budget: 10`, `llm.timeout: 60.0`, `graph.max_iterations: 3`,
`config.yaml` byte-identical to base.

## 5. Declared ceilings

| Provider | Ceiling | `stop_fraction` |
| --- | ---: | ---: |
| DeepSeek | 700 | 1.0 |
| OpenAI | 60 | 1.0 |
| Tavily | 450 | 1.0 |

Previous actuals: DeepSeek 248, Tavily 341. The account's own usage limit also
applies (S5).

## 6. Stop conditions

| # | Condition | Action |
| --- | --- | --- |
| S1 | CLI exits **3** (attempt limit) | Record actuals, do not rerun. |
| S2 | CLI exits **4** (quality gate) | Expected unless the fix worked. |
| S3 | Pre-provider failure | No spend; fix and rerun. |
| S4 | Wall clock exceeds **45 minutes** | Kill; record as failed. |
| S5 | Tavily refuses with the account usage limit | Count the lost searches. |

## 7. What will be measured

| Measure | Previous | What success looks like |
| --- | --- | --- |
| **Failed page reads** | 24 of 37 | **Falls** — refused hosts are dropped, not retried |
| **Successful reads** | 15 scrapes + 28 docs | Holds or rises |
| **Claims with ≥2 independent domains** | **0** | **Rises above 0** — the point of the change |
| Claims verified | 2 | Rises |
| **Critic score** | **3/10** | Rises |
| Topics covered | 5/6 (83%) | Holds ≥ 0.80 |
| Integrity | 0/0/0 | Stays 0/0/0 |
| Report length | 2,099 words | Stays ≤ 8,000 |

**Pre-committed null result:** if refused reads fall but the independent-domain
count stays 0, then reading volume is not the constraint either, and the next
change belongs in the *per-loop tool budget* — already measured as binding, with
65–74 provider-requested calls dropped in each of the last two runs.

## 8. Artifacts

- Run log: `output/cli-canary-<timestamp>-resource.log`
- Evaluation: `.superpowers/sdd/evaluate_run.py <session>` plus the per-agent and
  per-loop trace tools.

## 9. Credentials

Supplied through the repository dotenv launcher by path. No credential value is
printed, logged or recorded.

## 10. Authorization

Sixth run of the standing authorization.
