# Release status — partial result (plan Tasks 4-21)

**Status: NOT RELEASED.** No live run has cleared the ten Step 7 release
criteria. This is the explicit partial result the objective calls for: the
shortfall is named with its evidence, and **no criterion was relaxed, no
threshold lowered, and no fixture adjusted** to make a run appear to pass.

## 1. The ten criteria, against the most recent live run

Run: `ce8911eef28847d7ac739be450430ca8`, candidate `9d87360`, 26.4 minutes,
exit **4** (`--require-quality` failed). Record:
`docs/superpowers/validation/2026-09-15-scraper-diagnosis.md`.

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Terminal `accepted` quality | **FAIL** | `partial` — critic **3/10** |
| 2 | Coverage ≥ 0.80, every topic accounted for | **FAIL** | **3 of 6 topics = 50%** |
| 3 | Zero duplicate claims / source rows / unresolved citations | **PASS** | 0 / 0 / 0 uncited settled points |
| 4 | Every cited source scored; settled points claim-linked | **PASS** | 5 cited, **5 scored** |
| 5 | Verification passages provenance-bearing, independently published | **NOT MET** | only 2 verified claims; the report itself carries sections titled *"Not addressed by independent sources"* and *"Insufficient independent evidence"* |
| 6 | Reader report ≤ 8,000 words | **PASS** | **1,629 words** (11,389 chars) |
| 7 | Backmatter ≤ 35% of reader-report characters | **PASS** | Methodology + References = **16.9%** (boundary: the `## Methodology` heading) |
| 8 | No unexplained agent error; no generic-only limitation | **FAIL** | status `max_iterations`: *"the refinement budget was exhausted before the critic accepted the report"*; many `agent_tool_budget_exhausted` events |
| 9 | Whole-report judge ≥ 0.80 | **NOT MEASURED** | the judge step was never reached — 0 occurrences of `judge` in the run log, as in Q1 |
| 10 | CLI summary matches artifacts and state | **PASS (after a fix)** | `855b883` removed a `getattr` fallback that could print no budget section for a run whose ceilings *were* enforced |

**3 pass, 1 already met in substance, 4 fail, 2 not measured.** Integrity and
enforcement are consistently green; quality and coverage are consistently not.

## 2. What is verified and durable

- **Branch `codex/cross-agent-planner-fix-parity`, HEAD = origin = `f227646`**, tree clean.
- **Offline suite 3049 passed / 0 failed**, ruff clean, `compileall` exit 0,
  `git diff --check` exit 0 at the frozen candidate, and the network-zero
  controlled campaign **3/3 accepted** (coverage 1.00, judge 0.81–1.00).
- **Fourteen plan tasks complete, reviewed and pushed:** 1, 2, 3, 4 (fix re-reviewed
  Approved), 5, 6 (Approved), 7 (Approved), 8 (its one review blocker — a
  `wait_for` race that could silently drop a ceiling refusal — fixed), 9, 10,
  13, 14 (its two required changes both already landed, independently
  corroborated), plus 11's offline gate.
- **The request budget is enforced end-to-end in production and, for the first
  time in this project's history, measured:** DeepSeek 251/700, OpenAI 0/60,
  Tavily 352/450. No ceiling was reached and no attempt was refused.

## 3. What is not done

Tasks 12 (retired), 15 (diagnosis — delivered, see §4), **16, 17, 18, 19, 20,
21**. The bar-clearing iteration run (Task 20) has not happened.

## 4. The three findings that matter for whoever continues

1. **`web_scraper` needs no code change.** All 8 of its failures in the live run
   are one class — `the page request failed with an HTTP error status` — i.e.
   404/403/5xx from real hosts. That is the plan's own *"failures are not a
   defect"* branch, and it selects Task 16's last row.
2. **The Tavily plan quota is a real constraint and it was exhausted mid-run.**
   All 8 `web_search` failures are
   `ForbiddenError("This request exceeds your plan's set usage limit")`. The run
   used **352 of its own declared 450**, comfortably inside its bound, while the
   *account's* quota ran out underneath it. **The declared ceiling and the plan
   quota are different limits, and only one was ever enforced or observed.**
   Check the quota before every future run.
3. **Per-loop tool-budget exhaustion is the untested variable.** The transport
   ceiling never bound; `agents.tool_budget` was **identically 10** in both live
   runs because `config.yaml` is unchanged, so Task 18's hypothesis is neither
   confirmed nor refuted. Meanwhile the run made **352 searches but only 17
   reads**, and the log shows 1–4 provider-requested calls dropped per
   exhaustion event. Dropped *reads* are a plausible route to the coverage
   shortfall, and the next run is the first that would actually test it.

**The evidence funnel that produces 3/6 coverage:** 352 searches → 17 read
attempts (8 lost to host status) → **5 sources scored** → 2 verified claims. The
five sources it did score are strong (IEA 0.84, LBNL 0.80, RFF 0.74). The
pipeline finds quality material and cannot convert it into enough independently
corroborated, citable sources to cover six topics.

## 5. Corrections recorded against the controller, not the agents

- **The background job reported exit 0; the CLI exited 4.** A wrapper's exit code
  is not the program's. Reading it as one would have turned a failed release gate
  into a passing one.
- **A mid-run grep for failure records returned zero and was reported as
  promising.** The finished log carries many. A live grep of a stream that does
  not carry a category is not evidence the category is empty.
- **I called the budget-starvation hypothesis "falsified" and it was not** — I
  conflated the new *transport* ceiling with the unchanged *per-loop tool*
  budget. Corrected in the diagnosis document and the ledger.

## 6. Budget

Three of ~10 authorized live runs are spent (Q1, Q1's paid planner-failure
attempt, this canary). Roughly seven remain, subject to the Tavily plan quota.
