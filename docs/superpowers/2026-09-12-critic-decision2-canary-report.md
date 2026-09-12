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

`agent critic --tier live`, three repetitions. Cost: **13 model calls** — 3, 3 and
7 — not the ~20 I estimated, because the two failing runs aborted the spot-check
phase after two attempts each.

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

## 5. This failure mode predates the change

Every recorded live Critic repetition, by fallback operation:

| Operation | Repetitions with a fallback | Which |
| --- | --- | --- |
| `critic_report_review` | 6 | the section-82 family, now fixed |
| `react_decision` | 3 | `cccc139-r3` (**before** this change), plus reps 1 and 2 here |

So `react_decision` fallbacks occurred once in the 20 live Critic repetitions
preceding this batch (about 5%) and twice in three here. **Two in three does not
establish a rate.** What it does establish is that the mode exists independently of
this change and can cost a run outright.

It is also the **same failure family as the section-82 defect** — a structured call
whose response is not JSON — but on the ReAct path instead of the review path, and
undiagnosed. The retained traces carry telemetry only (`configured_max_tokens`,
`finish_reason_category`, `request_attempt`, `structured_attempt`, `usage`) and no
text, so the response shape cannot be classified from what was kept.

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
