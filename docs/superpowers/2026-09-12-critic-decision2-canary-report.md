# Report — Decision-2 Canary Outcome and a Newly Surfaced `react_decision` Failure

Date: 2026-09-12. Repository: `deep-research`. Branch:
`codex/cross-agent-planner-fix-parity`, tip `78c0179` (in sync with the remote).
Companion to `2026-09-12-critic-calibration-recommendation-request.md` and
`2026-09-12-critic-calibration-review-prompt.md`. Fix-log sections 89 and 90.

If you are reading this without repository access: it is self-contained. Section 9
is the ask.

---

## 1. Status of your recommendation

| Your instruction | Status |
| --- | --- |
| 1. Clarify Option B in `CRITIQUE_INSTRUCTION`, including that contrary verdicts or spot-check evidence override bare attribution | **Done** (`12a5fbe`) |
| 2. Run the separately authorized 3-repetition canary; treat as regression/directional, not calibration | **Done** — result below |
| 3. If the fixture remains 4–6/refine, update it to case v2 with that band and route | **Not applied** — the precondition holds, but see section 4 |
| 4. Run the separate case-v2 canary; if the first canary moved consistently to 7/end, pause Decision 1 | **Moot** — it did not move |

The fixture did **not** move: all three repetitions again returned **score 5 with
`should_continue = True`**, so by your step 3 the case-v2 change would be next. I
have **not** applied it, and section 4 explains why.

---

## 2. What was implemented

`CRITIQUE_INSTRUCTION` (`src/deep_research/agents/prompts.py:207`) now reads:

> `unsupported_claims`: statements presented as fact that are neither clearly
> attributed to one of the report's cited sources nor backed by a verified claim.
> Do not mark a cited statement unsupported solely because it lacks a separate
> verified-claim entry: the claim digest is deliberately partial, so absence from
> it is not evidence of unsupportedness. A contrary claim verdict or spot-check
> evidence still makes a statement unsupported, however it is cited. Quote or
> closely paraphrase each one.

Two tests added, as you asked:

- a **structural prompt test** pinning all three parts of the distinction (lenient
  default, the "digest is partial" rationale, and the contrary-evidence override)
  and asserting the superseded strict wording is gone;
- the **exact Critic target-fingerprint pin**, plus a second test that *verifies*
  the shared-module scope rather than assuming it. Your qualification is recorded
  in the test's comment: the pin is a **drift alarm, not an attribution
  mechanism** — `git_commit` explains a clean committed revision via its diff, so
  attribution is lost only for a dirty tree with no retained snapshot.

Fingerprint moved `bf86f19981a6 → 2c0bd1210e21`. Judge fingerprint unchanged
(`74b9cddfbbee`). Offline gate at the tip: **588 passed**, ruff clean.

---

## 3. The canary result

`agent critic --tier live`, three repetitions. Cost: **13 target-agent calls** — 3,
3 and 7 — not the ~20 I estimated, because the two failing runs aborted the
spot-check phase after two attempts each. **These are target calls only.** Each
repetition also invoked the judge once (every repetition has `judge.status =
scored` with zero diagnostics, so no judge repair occurred), making the batch
**16 model requests in total**. The earlier batch (section 88) was 20 target calls
plus 3 judge calls = 23 requests, not the 20 I reported. Paid-run accounting should
use the total.

| Rep | Status | Gates | Deterministic | Judge | Aggregate | Fallback | ReAct stop | Model calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | **FAILED** | 15/15 | 0.75 | 0.6825 | **0.7095** | **PRESENT** | **provider_error** | 3 |
| 2 | REVIEW REQUIRED | 15/15 | 1.00 | 0.8225 | **0.8935** | **PRESENT** | **provider_error** | 3 |
| 3 | **FAILED** | 15/15 | 0.75 | 0.5650 | **0.6390** | none | finished | 7 |

The comparison batch (fix-log section 88, before this change) was 3 of 3 above the
0.75 threshold with no fallback and all `finished`. So this batch is **worse**, and
your step 2 canary did not produce a clean read on Decision 2 either.

Failed deterministic metric in reps 1 and 3: `no_spurious_gaps` (the same
heuristic discussed in the brief, now 2 of 3 rather than 1 of 3).

---

## 4. The cause is the ReAct decision call, not the change

The fallback diagnostics on reps 1 and 2:

```
operation = react_decision      kind = schema_output
attempt 1: category=json_invalid, field_paths=['$']
attempt 2: category=json_invalid, field_paths=['$']
```

Both attempts of the **ReAct decision** call returned text that was not JSON, so
the spot-check phase ended in a typed fallback. `judge_quality` is the weighted sum
of the six common dimensions, and the judge is instructed to score a run carrying a
fallback as a fallback rather than as the review it could not produce — which is
what puts reps 1 and 2 below threshold.

A detail that sharpens it: the failing runs made **zero tool calls** (trace
`run_type` counts are `{'llm': 3, 'chain': 4}` with no `tool`), against three tool
calls in the healthy run. So in two of three repetitions the Critic produced its
critique **without performing any spot check at all**, and still scored the report
5 and routed to continue.

### Your change is exonerated, and this was checked before it was concluded

`CRITIQUE_INSTRUCTION` reaches the ReAct decision call **nowhere**:

- It has exactly one use site — `critique_messages` (`src/deep_research/agents/critic.py:431`),
  the *review* request. Every other occurrence is an import or a comment.
- Rendering the Critic's ReAct surface (system prompt plus
  `_render_spot_check_guidance`) contains **neither** the new clause **nor** the
  superseded strict wording.

I record this because the opposite conclusion was tempting and would have been
wrong: the change sits between two canary batches, and the regression appeared in
the second. The mechanism was disconfirmed first.

---

## 5. This failure mode predates the change — **corrected**

**Correction accepted.** My first version of this section claimed the mode was
pre-existing on the strength of `cccc139-r3`. That artifact's ReAct fallback was
`kind = output_limit` with **no diagnostics** — an output-limit failure from the
4096-token configuration era, **not** the current `schema_output` / `json_invalid`
mode. It shows that *a* ReAct fallback predates this change; it does **not** show
that *this* failure mode does. Before this batch, `react_decision` +
`json_invalid` had never been observed.

### Enumeration rule and totals — published so they can be reconciled

Rule used: every `results.json` under `output/evaluations/**` in this worktree with
`agent_name == "critic"`, counting one entry per repetition inside each
`cases[].repetitions[]` list.

| | My scan | Your scan |
| --- | --- | --- |
| Pre-D2 live repetitions | **20** | 22 |
| `critic_report_review` fallbacks (pre-D2) | **6** | 7 |
| `react_decision` fallbacks (pre-D2) | **1** (`cccc139-r3`, `output_limit`) | 1 |

**I cannot reproduce 22 / 7 / 1.** The worktree holds exactly 23 live critic
repetitions (20 pre-D2, 3 in this batch) and 3 controlled ones; the main checkout's
`output/evaluations/` contains **zero** critic repetitions. If your 22 comes from a
superset — LangSmith experiment history, a deleted artifact, another worktree — the
two extra repetitions and the seventh review fallback are worth naming, because
they would change the pre-D2 denominator for the review-call rate. The corrected
conclusion does not depend on it: the specific `json_invalid`-on-ReAct mode has
**0 occurrences in 20 pre-D2 repetitions** under my rule and 0 in 22 under yours.

### The rest of the scan

Across all 23 live critic repetitions in this worktree, fallbacks by operation:
`critic_report_review` 6 (all pre-D2, the section-82 family, now fixed) and
`react_decision` 3 (`cccc139-r3` = `output_limit` pre-D2; this batch's r1 and r2 =
`json_invalid`).

---

## 6. What this batch does and does not establish

**Established**

- All 15 hard gates passed in 6 of 6 canary repetitions across both batches.
- The section-82 defect stays fixed: no `critic_report_review` fallback in either
  batch.
- The fixture remains in band 4–6 with `should_continue = True`, 3 of 3 here and
  3 of 3 in section 88 — six consecutive observations.
- A `react_decision` fallback is a real, run-costing failure mode.

**Not established**

- Whether Decision 2 changed the unsupported-claim count or the Critic's score.
  Two of three runs were degraded, so the batch cannot separate the change from the
  fallbacks.
- The rate of the ReAct failure, and its cause.
- Anything about calibrated score distributions — three runs cannot support that,
  as you noted.

---

## 7. Consequence for the sequencing you set

Your step 3 was conditional on a clean canary read. The precondition (fixture stays
4–6/refine) is met six times over, but the batch that would justify a case-v2 change
is confounded: applying Decision 1 now would stack a case change on top of two
degraded runs, and would also make it impossible to attribute a later score shift
between the case change and the ReAct failure.

I have therefore stopped rather than continuing down your sequence, and I am not
re-running the canary to fish for a clean batch — that would cost calls without
teaching us about the failure mode.

---

## 8. What I got wrong this round

- I wrote a summary line reporting "no fallback" for a batch while the next batch
  contained two; I should have framed that as batch-specific from the start.
- I estimated the Decision 2 canary at ~20 calls; the actual was 13, because the
  failing runs abort early. Estimates should be labelled as such or measured.
- Before you flagged them, my brief generalised one repetition's unsupported-claim
  count ("four") to all three (actual 4, 8, 5), used the judge as corroboration it
  could not provide, and asserted a fixed HEAD a committed document cannot keep.

---

## 9. The ask

1. **A recommendation on the next step**, choosing between:
   - **(a)** diagnose the `react_decision` failure with a content-free shape probe —
     replay that exact request N times and record only length, first/last
     character, whether markup identifiers appear, and whether the text parses as
     JSON. This is the method that diagnosed the review call in section 85. I would
     propose 30 calls, authorised separately;
   - **(b)** re-run the Decision 2 canary hoping for a fallback-free batch (~20
     calls or fewer), which costs calls and does not explain the failure;
   - **(c)** proceed to Decision 1 anyway, accepting the confound.
   My lean is (a), because the failure mode now costs runs on its own and is
   upstream of any calibration conclusion.
2. **Whether you disagree with the exoneration reasoning** in section 4. If
   `CRITIQUE_INSTRUCTION` does reach the ReAct call through a path I have not
   found, that changes the conclusion materially and I would want to know before
   spending anything.
3. **Whether the ReAct finding changes your sequencing.** Your steps 3 and 4
   assumed the canary would read cleanly on Decision 2; it did not, for a reason
   unrelated to Decision 2.

Constraint reminders: one change at a time, each paid batch authorised separately
with a stated call count before it runs, and any change to `prompts.py` moves the
recorded target fingerprint for **all six agents** because
`agent_prompt_fingerprint` hashes the shared module.

---

## 10. Update — the shape probe ran, reproduced the failure, and names the mechanism

You chose **(a)**, the exact-request-shape probe. It has run.

### What was sent

30 independent **first-attempt** requests, no repair retry, so exactly 30 HTTP
calls (the SDK client has retries disabled and the probe calls it directly).
Built through the provider's own helpers rather than reconstructed by hand, and
verified before spending:

| Property | Value |
| --- | --- |
| Schema | `ReActDecision` |
| Wire messages | `system`, `user`, `system` (provider-appended schema) |
| Reasoning effort | **`max`** — the probe refuses to run otherwise, because loading the repo `.env` sets the global effort to `high` and only the evaluation runtime raises it to `max` |
| Model | `deepseek-v4-flash` |
| `max_output_tokens` | 32768 |
| Temperature | **not sent** (thinking mode) |
| Tool descriptors | `web_search`, `query_memory` |
| Request keys | `model`, `reasoning`, `input`, `max_output_tokens`, `text` — **no `tools`** |

### Result

| Measure | Value |
| --- | --- |
| `not_json_start` (markup) | **17 / 30 (57%)** |
| `starts_with_brace` (valid JSON) | 13 / 30 (43%) |
| Schema validation | 17 invalid / 13 valid |
| Finish reason | **`stop` — 30 / 30** |
| Response length | 90–818 chars (markup 90–818; valid JSON 368–895) |
| Markup markers | every failing response contains `DSML`, `invoke`, `parameter` and a tool name; first char `<`, last char `>` |
| Truncation | none; 2 of 30 had unbalanced braces |

### The mechanism

**Every failing response is DSML tool-call markup emitted as plain text**, with
`finish_reason = stop` — so it is not truncation, not a budget problem, and not a
parse quirk. This is the **section-82 mechanism exactly**, on the other call path:
the request describes tools in prompt prose while the API request carries **no
`tools` parameter**, so the model tries to invoke a tool and the markup lands in
message text.

The review call was fixed in section 82 by *removing* the tool language, because
that call needs no tools. **That fix is unavailable here**: the ReAct decision call
is the tool-invoking loop. Its design expresses tool use through the structured
`action` / `tool_name` / `tool_input_json` fields, while the prompt also presents a
tool catalogue — so the model has two conventions available and sometimes picks the
native one.

### Why the canary only fails occasionally

Production wraps this call in the one-repair flow, so the observed per-run fallback
rate is roughly the first-attempt rate times the repair's failure rate. The two
canary runs that fell back had **both** attempts return `json_invalid`. A 57%
first-attempt rate therefore does not imply a 57% run-failure rate — but it does
mean **the ReAct decision call depends on the repair retry to function**, and pays
for it in requests. That reframes the section-88/§3 comparison: the two fallbacks
were expected at this first-attempt rate, not bad luck.

### Next hypothesis, per your gate

You asked that the structural signatures determine the next hypothesis before any
further canary. They point at the tool-convention collision above, and the
candidate remedies are prompt-level or architectural:

- **(i)** state in the ReAct contract that tool use must be expressed only through
  `action` / `tool_name` / `tool_input_json`, and that no tool-call markup may
  appear in the response;
- **(ii)** change how the tool catalogue is presented so it does not read as a
  native API tool list;
- **(iii)** pass `tools` for real and consume native tool calls — an architecture
  change to the ReAct loop, since it currently consumes a decision object.

I have **not** run another canary and have not changed any prompt. My lean is (i)
first, because it is the smallest change that addresses the mechanism directly, and
because (iii) would replace a measured design rather than repair it.

Revised ask: **which remedy, and is (i) acceptable as the next single change to
validate?** A canary for it would be about 23 requests (3 repetitions × 7–8 target
calls plus 3 judge calls), authorised separately.
