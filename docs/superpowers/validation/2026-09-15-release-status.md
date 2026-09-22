# Release status — partial result (plan Tasks 4-21)

**Status: NOT RELEASED.** No live run has cleared the ten Step 7 release
criteria. This is the explicit partial result the objective calls for: the
shortfall is named with its evidence, and **no criterion was relaxed, no
threshold lowered, and no fixture adjusted** to make a run appear to pass.

## 0. Current run pointer (updated 2026-09-16)

Every historical entry in this document is retained. The only edits in this
revision are this new section, the §1 heading (which now names the run it was
written from instead of calling it "the most recent"), and the dated update at
the end of §6. **The current run is `417fa9338e10450784b459f89af98b1c`**
(evidence pooling, iteration 3, historical application candidate `2bc6665`).
Its frozen artifact/commit inventory, SHA-256 hashes, per-agent `results.json`
inventory, F1 payload findings, and defect-to-task matrix are in
[the output-quality baseline](2026-09-16-output-quality-baseline.md); the binding
analysis of that run is
[the last-run agent trace review](2026-09-16-last-run-agent-trace-review.md).

| Measure | Current run `417fa933` | §1 run `ce8911ee` |
| --- | --- | --- |
| Terminal quality | `partial` | `partial` |
| Critic | **5/10** | 3/10 |
| Topics covered | **4/6 (67%)** | 3/6 (50%) |
| Verified / contradicted claims | 5 / 0 | 2 / 0 |
| Cited / scored sources | 8 / 8 | 5 / 5 |
| Integrity (dupes / source rows / uncited) | 0 / 0 / 0 | 0 / 0 / 0 |
| CLI exit | 4 (`--require-quality`) | 4 (`--require-quality`) |
| Whole-report judge (criterion 9) | **NOT MEASURED** — 0 occurrences of `judge` in the run log | NOT MEASURED |

The intervening runs — agent baseline, agent fixes, corroboration, resource,
publisher retention, and corroboration criterion — are inventoried with their
session IDs, hashes, and measured outcomes in the baseline document.

**No criterion changed state in a way that clears the release bar.** The critic
rose 3/10 → 5/10 against a ≥7/10 requirement, and coverage moved 3/6 → 4/6
against a ≥0.80 requirement. Criterion 9 was reached in neither run.
**NOT READY stands.**

### 0.1 Controlled validation pointer (added 2026-09-22)

Every entry above is retained verbatim. This is a new subsection, not a
revision of any line before it.

[The controlled real-agent validation](2026-09-16-real-agent-controlled-validation.md)
now exists. It certifies the **Task 12 evaluation harness** at candidate
`27c45b9c1b6e2e32e26420c72b4befd16eb48da5` on a confirmed-clean tree, and it
carries the verdict **`CONTROLLED READY / LIVE NOT VALIDATED`**.

**This does not move the release bar and it does not change any criterion in
§1.** That document certifies a harness — its inventory, case versions, test
counts, fingerprints, reviewed snapshots, and socket-layer network isolation.
It certifies no agent: no live run has cleared the ten Step 7 criteria, and, as
its own §0 states prominently, the six new individual-agent evaluation cases
have never been executed against a real model. Its `CONTROLLED READY` half is a
statement about the apparatus; its `LIVE NOT VALIDATED` half is why
**NOT READY still stands** here.

Two limitations are also recorded in that document rather than only in the
ledger: the `content_version_changed`/`content_hash_changed` cache-provenance
shape remains unit-tested only, and three Minor/Informational findings from the
whole-branch re-review remain open and are explicitly non-blocking per that
review.

## 1. The ten criteria, against the run this document was written from

Run: `ce8911eef28847d7ac739be450430ca8`, candidate `9d87360`, 26.4 minutes,
exit **4** (`--require-quality` failed). Record:
`docs/superpowers/validation/2026-09-15-scraper-diagnosis.md`.

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Terminal `accepted` quality | **FAIL** | `partial` — critic **3/10** |
| 2 | Coverage ≥ 0.80, every topic accounted for | **FAIL** | **3 of 6 topics = 50%** |
| 3 | Zero duplicate claims / source rows / unresolved citations | **PASS — exact-fingerprint only, see below** | 0 / 0 / 0 uncited settled points |
| 4 | Every cited source scored; settled points claim-linked | **PASS** | 5 cited, **5 scored** |
| 5 | Verification passages provenance-bearing, independently published | **NOT MET** | only 2 verified claims; the report itself carries sections titled *"Not addressed by independent sources"* and *"Insufficient independent evidence"* |
| 6 | Reader report ≤ 8,000 words | **PASS here — not enforced in production** | **1,629 words**, but the ceiling exists only as an *evaluation* metric (`e2e_evaluation/evaluators.py:34`, scored `1.0 if 40 <= words <= 8_000 else 0.35`). The CLI does not check it, and a sibling session published a **15,567-word** report with nothing objecting |
| 7 | Backmatter ≤ 35% of reader-report characters | **PASS here — not implemented anywhere** | **16.9%** by my own measurement (boundary: the `## Methodology` heading). The string `backmatter` does not appear anywhere in `src/`; the criterion is a design goal in the spec (§ line 80) with **no code that measures it** |
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

**Update, 2026-09-16.** That paragraph is retained as written. Seven further
authorizations have since been spent — agent baseline, agent fixes, corroboration,
resource, publisher retention, corroboration criterion, and evidence pooling — so
the standing authorization is **exhausted at its declared tenth run** by the
predeclarations' own numbering (see the baseline document §3.2, where the declared
ordinals are also shown to conflict at slot 4). The heat-pump run declared as the
seventh was **never authorized and never run** — its own predeclaration is marked
*"PARKED — NOT AUTHORIZED, NOT RUN"* at the user's direction, with no candidate
SHA frozen — so no artifact for it exists or is expected. **No further live run is
authorized by this document.** Task 13 of the current plan is paid/live and
requires a separately reconciled allowance and an explicit stage confirmation;
the historical ~US$100 / approximately ten-run authorization is not a fresh
allowance.
