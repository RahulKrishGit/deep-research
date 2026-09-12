# Recommendation Request — Critic Score Calibration and the `unsupported_claims` Definition

Date: 2026-09-12. Repository: `deep-research` (Python 3.12 multi-agent research
system). Branch: `codex/cross-agent-planner-fix-parity`. HEAD: `5745704`.

This is a request for a **recommendation on two open decisions**, with the full
evidence behind them. It is not a request to re-open the defect that was fixed, and
not a request for a redesign.

---

## 1. The exact ask

Give a recommendation that:

1. **Picks an option for Decision 1** (the live Critic case's reference
   expectation) and **an option for Decision 2** (the `unsupported_claims`
   definition in the Critic's response contract), with the reasoning stated.
2. **States what evidence would change your mind** on each — i.e. what measurement
   would falsify your recommendation.
3. **Names any test, metric, or prompt change** your recommendation requires, and
   what it would invalidate (see the constraint envelope in section 8).
4. **Flags anything in my analysis you disagree with**, including anything I have
   asserted that the evidence does not support.

---

## 2. Context: what this system is and what happened

The project runs a multi-agent research pipeline (planner → researcher → source
evaluator → fact checker → synthesizer → critic) with an evaluation harness that
runs one agent at a time against a frozen case. Each repetition produces:

- a **target** result (the agent under test),
- **15 deterministic hard gates**,
- a **deterministic quality** score from 4 weighted metrics,
- an **LLM-as-a-judge** verdict (6 weighted common dimensions plus unweighted
  agent-specific dimensions),
- an **aggregate** = `0.6 × judge_quality + 0.4 × deterministic_quality`.

The live acceptance threshold is **aggregate ≥ 0.75**.

The Critic was failing live calls. Root cause (recorded in the fix log as section
82): its *structured review* request offers no tools, but the system prompt told
the model to use `web_search` and `query_memory`. The model obeyed and emitted
DeepSeek's native tool-call markup (DSML) as plain message text, which local JSON
validation rejected — `json_invalid`, 16 of 30 first attempts. That meant no
critique, so the judge never ran and the repetition was unscorable.

**That defect is fixed and verified.** What remains are two calibration questions
that the fix exposed. They are the subject of this request.

---

## 3. Evidence inventory

### 3.1 The Critic review call (request shape)

| Measurement | Old prompt | Revised prompt |
| --- | --- | --- |
| `prose_no_json` first attempts | **16 / 30 (53%)** | **0 / 30** |
| Valid first attempts | 14 / 30 | **30 / 30** |
| Response length when valid | 3,837–5,191 chars | 2,233–3,662 chars |

Both measured at `reasoning_effort: high` on `deepseek-v4-flash`, thinking
enabled. The effort was held constant, so the improvement is attributable to the
prompt revision. The revision: a tool-free review prompt, a marked report fence,
two bracketing JSON examples, and a 1–10 score band table.

### 3.2 The judge (LLM-as-a-judge)

The judge uses a different transport (DeepSeek Responses API with a native
`json_schema`), which **does not enforce** schema constraints: sending
`strict: true` returns **HTTP 400** on 3 of 3 attempts, while an otherwise
identical control request without the flag was accepted.

Measured over 30 production judge requests at `max` effort, under three
configurations:

| First attempts | Baseline | After the prompt revision | After also tolerating extra keys |
| --- | --- | --- | --- |
| `string_too_long` (rationale length) | 5 / 30 | **0 / 30** | **0 / 30** |
| Extra top-level key added | 0 / 30 | **11 / 30** | 9 / 30 (tolerated) |
| `json_invalid` (unparseable) | 0 / 30 | 0 / 30 | 2 / 30 (both repaired) |
| **Terminal failures (unscorable)** | 0 / 30 | **3 / 30 (10%)** | **0 / 30** |
| Rationale length median | 1,735 | 1,716 | 1,724 |

Two negatives worth noting: the rationale median did **not** move, so the length
improvement comes from widened enforcement rather than from the prompt's length
instruction; and 2 `json_invalid` first attempts appeared under the final
configuration, both repaired, unexplained.

### 3.3 The Critic live canary — 3 repetitions

Command: `python -m deep_research.evaluation agent critic --tier live`
(runs the target agent, all gates, and the judge; no other agent). Cost: 20 model
calls measured from the recorded traces (7, 6, 7 per repetition).

| Rep | Status | Gates | Deterministic | Judge | Aggregate | Fallback | ReAct stop |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | REVIEW REQUIRED | 15/15 | 0.75 | 0.7755 | 0.7653 | none | finished |
| 2 | REVIEW REQUIRED | 15/15 | 1.00 | 0.7520 | 0.8512 | none | finished |
| 3 | REVIEW REQUIRED | 15/15 | 1.00 | 0.8150 | 0.8890 | none | finished |

**3 of 3**: no provider fallback, `react_stop_reason = finished`, judge status
`scored` with **zero diagnostics**, `prohibited_call_count = 0`, aggregate above
the 0.75 threshold.

Three clean runs are weak evidence alone: against the prior live rate of 4 of 17
repetitions carrying `json_invalid` (≈24%), three clean runs occur with
probability ≈0.44. Combined with the 30-request probe above — **33 consecutive
clean Critic review calls** — the same prior gives ≈1 in 8,000. I do **not** claim
the rate is exactly zero.

### 3.4 The two anomalies the canary surfaced

**(a) Intermittent deterministic metric.** Rep 1's `deterministic_quality` is 0.75
because `no_spurious_gaps` returned 0 (1 of 3 repetitions).
`_no_spurious_gaps_passes` (`evaluators.py:2109`) fails a critique when a gap's
token set contains all tokens of a reference theme *and* no single clause of the
report both carries those tokens and an "unresolved" marker. The trigger was a
gap containing the theme `commercial-scale deployment` whose actual point was that
the deployment is *unquantified* — the fixture names no plant, company, country or
capacity figure, which is factually true of it. The token-subset heuristic cannot
separate "the theme is missing" from "the theme is present but unquantified". The
judge scored `gap_precision` 0.85–0.88 on those same gaps. **My assessment:
heuristic false positive, unproven.**

**(b) The Critic's score disagrees with the case's reference expectation.**
All three repetitions returned **score 5 with `should_continue = True`**, while
the case's expectation is route `end` with `minimum_score: 7`.

---

## 4. Current state of the branch

Commits on the branch, in order:

| Commit | Contents |
| --- | --- |
| `479065d` | `fix(critic)`: tool-free review prompt, bracketing examples, 1–10 band table |
| `7460952` | docs: the 0/30 shape-probe record |
| `f51301d` | docs: probe-effort correction and the judge schema-failure diagnosis |
| `f8944d6` | `fix(judge)`: response contract, calibration bands, explicit weights |
| `bc3f472` | docs: the judge revision and its three measurements |
| `5745704` | docs: the Critic live canary and its calibration finding |

Fix-log: `docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md`,
sections **83–88** (83: Critic revision; 84: report fence; 85: shape probe;
86: judge diagnosis; 87: judge revision; 88: the canary).

Provenance fingerprints, both of which move on any prompt or schema change:
`target_prompt_fingerprint = bf86f19981a6` (was `66a04109745c`);
judge prompt fingerprint = `74b9cddfbbee` (was `77a0898f4267`, which is the value
recorded on the older live artifacts).

---

## 5. Decision 1 — is the live case's reference expectation stale?

### The expectation

`src/deep_research/evaluation/cases/critic.py:1012-1017`:

```python
expectations=CaseExpectations(
    reference={
        "expected_route": "end",
        "minimum_score": 7,
        "reference_themes": list(_LIVE_THEMES),
    },
    ...
```

### Evidence that it is stale

1. **Provenance.** `minimum_score: 7` and the fixture report `_LIVE_REPORT`
   (`cases/critic.py:810`) both date from `13de53b` — the original case creation.
   The Critic's band table (`_CRITIQUE_SCORE_BANDS`, `critic.py:137`) landed in
   `479065d`, **this session**. When the expectation was written, the Critic's
   entire scale was *"1 is unusable; 10 answers the question completely from
   strong, diverse, well-cited sources"* — no middle at all.
2. **The fixture has no quantitative content.** The report answers a question
   about *evidence for performance at scale* with five prose sections and three
   named sources (GCGA roadmap, IEA, Nature review) and **no numbers at all** — no
   emissions percentages, no CO₂ intensity, no capacity, no tonnage, no price. The
   band table states explicitly that band 4–6 is *"a partial answer whose key
   numbers, mechanisms, or trade-offs are missing or unsupported"*. So 5 is the
   band-consistent reading of this report.
3. **It gates nothing.** `minimum_score` and `expected_route` appear **nowhere**
   in the source tree except the case definitions. No gate, evaluator, or routing
   decision reads them; they reach the judge as `reference_expectations` context
   only.

### Evidence pointing the other way

The case was constructed so the report covers all six `_LIVE_THEMES`, and the
adjacent comment says the themes are *"derived from this fixed report: the live run
critiques this text, it does not discover it"*. The case therefore appears
designed to exercise the "Critic accepts a good report" path, and a Critic that
routes to `continue` no longer exercises it.

### Why this matters quantitatively — and a correction I owe

`judge_quality` is the weighted sum of the **six common dimensions only**;
agent-specific dimensions are reported and never weighted (this is stated in the
judge prompt itself). Verified arithmetically against rep 1:

```
0.15(0.82) + 0.20(0.75) + 0.25(0.80) + 0.15(0.70) + 0.15(0.85) + 0.10(0.70) = 0.7755
```

So the low agent-specific scores in the canary (`scoring_calibration` 0.70/0.60/0.80,
`score_groundedness` 0.75/0.60/0.80) have **zero** effect on the aggregate. I
earlier described the stale expectation as penalising the run "through the
agent-specific dimensions"; that was wrong. The only path from the expectation to
the aggregate is *indirect* — if the judge lets the reference expectation colour
the common six — and I have no evidence that it does.

### Options

- **A. Update the expectation** to match the calibrated bands (for example
  `minimum_score: 5` or `6`, `expected_route: "continue"`), or replace the scalar
  with a range. Removes an inconsistency that no longer describes the system.
- **B. Leave it and document** that the fixture is expected to score below 7 under
  the calibrated bands, with a comment beside it explaining why. Zero behavioural
  risk.
- **C. Change the fixture report** to carry quantitative content, so that ≥7 is
  genuinely warranted. This is the largest change: `_LIVE_THEMES` are derived from
  the report, `no_spurious_gaps` is judged against those themes, and
  `known_source_urls` are tied to it, so the case's expectations would need
  re-deriving too.

### The question for you

Given that the scalar gates nothing but does inform the judge, which option — and
if A, what value, and on what basis rather than taste?

---

## 6. Decision 2 — what does `unsupported_claims` mean?

This is the more consequential of the two, because it affects **every** Critic run,
not one fixture.

### The definition

`CRITIQUE_INSTRUCTION`, `src/deep_research/agents/prompts.py:207`, renders:

> `unsupported_claims`: statements the report makes that no cited source or
> verified claim backs. Quote or closely paraphrase each one.

### What the Critic actually did

In the canary it returned four `unsupported_claims`, and every one reasons from
the **verified claim set** rather than from the report's own citations — for
example *"specific operational claims outside the two verified claims"*, and
*"neither is among the verified claims"*.

But the fixture report attributes those very statements inline to named sources
("The roadmap records that these plants have run for several years…", "The IEA
notes that premiums remain material…"). Only **two** claims were independently
verified in the case state, while the report cites three sources for considerably
more than two statements.

### The ambiguity

"no cited source or verified claim backs" permits two readings:

- **Reading 1 (lenient):** a statement is unsupported only if it is *neither*
  attributed to a cited source *nor* independently verified. On this reading the
  Critic over-reports: those statements carry inline citations.
- **Reading 2 (strict):** a statement is unsupported unless an *independent*
  verification backs it; a bare attribution is not evidence. On this reading the
  Critic is correct, and the report is making four unverified assertions.

The Critic resolved it strictly. Both readings are defensible: a URL attribution
is weak backing, and the Critic's whole purpose is to notice when prose outruns
its evidence — but the wording as written admits the lenient reading, and the
choice measurably changes the unsupported-claim count and therefore the score.

### Consequence of leaving it

A Critic that treats every non-verified assertion as unsupported will
systematically over-report, depress scores, and route to `continue` more often
than intended — on real reports, not only this fixture. That is the risk of the
status quo. The opposite risk is that a lenient reading lets a genuine
attribution-laundering failure pass.

### Options

- **A. Reword to the strict reading explicitly** — e.g. "statements that no
  independently verified claim backs, even when the report attributes them to a
  source." Makes current behaviour intentional and testable.
- **B. Reword to the lenient reading explicitly** — a statement counts as
  supported when the report attributes it to a cited source; report only
  unattributed assertions. Would reduce the unsupported-claim count and raise
  scores.
- **C. Split the field** into `unsupported_claims` (nothing backs it) and
  `unverified_claims` (attributed but not independently verified), which
  reproduces the distinction the Critic is already making without forcing one
  reading. Costs a schema change, a downstream consumer change, and a new
  fingerprint.

### The question for you

Which reading is intended for this system, and does the answer differ between a
production research run (where a wrongly-accepted claim is expensive) and an
evaluation harness (where the goal is to measure the Critic, not to research)?

---

## 7. My own lean, offered as input rather than as a conclusion

- **Decision 1:** option A, `minimum_score` reduced to match band 4–6 and
  `expected_route` left as `end` only if a re-derived fixture warrants it.
  Rationale: the scalar gates nothing, so the cost is near zero, and leaving a
  documented expectation that the system contradicts on 3 of 3 runs trains readers
  to ignore it.
- **Decision 2:** option A or C, not B. Rationale: the strict reading is the safer
  default for a Critic whose job is to catch prose outrunning evidence, and C is
  strictly more informative than either reword if the schema cost is acceptable.
  I would want your view on whether the extra field earns its cost.

I hold both loosely. If the evidence above supports a different reading, say so.

---

## 8. Constraint envelope any recommendation must respect

1. **The fixed defect must not regress.** 3 of 3 green canaries and 33 consecutive
   clean review calls are the baseline. Any prompt change to the Critic requires a
   fresh canary to re-establish it.
2. **Prompt and schema changes move fingerprints.** `target_prompt_fingerprint`
   (recorded from `runtime.prompt_fingerprint`,
   `src/deep_research/evaluation/config.py:500`) covers the agent prompt in force;
   the judge fingerprint (`judge_prompt_fingerprint`, `judging.py:287`) covers the
   judge prompt **and** `JudgeVerdict.model_json_schema()`. Scores recorded before
   and after such a change are deliberately not comparable.
   **Caveat worth knowing when reviewing a recommendation:** only the *judge*
   fingerprint is pinned by a test
   (`test_judging.py::test_the_judge_prompt_fingerprint_supersedes_the_pre_fallback_value`).
   The Critic's `target_prompt_fingerprint` is recorded on artifacts but pinned
   nowhere, so a future Critic prompt change would move it silently. I noticed
   this while checking the claims in this document; it is an open gap, not a
   deliberate decision.
3. **Paid-run discipline.** Every live run is explicitly authorised with a stated
   request count in advance. One canary repetition costs 5–8 model calls
   (measured); a 3-repetition confirmation costs about 20. A 30-request
   request-shape probe costs 30.
4. **Judge design decisions already made and deliberately paired with tests:**
   the prompt states a hard 2000-character rationale limit while 20000 is enforced
   locally and **not declared in the transmitted schema** (the request must carry
   exactly one length signal); `JudgeVerdict` tolerates additive extra keys
   (`extra="ignore"`) while `scores` and `rationale` remain required so drift
   still fails.
5. **Do not re-litigate** the DSML/`json_invalid` root cause, the tool-free review
   prompt, or the report fence — these are measured and closed.
6. **One change at a time.** Each prior step was validated in isolation; bundling
   made attribution impossible once already and cost a 10% terminal-failure
   regression that only measurement caught.

---

## 9. Known weaknesses in this analysis — please weigh these

1. **Three canary repetitions is a small sample.** It establishes that the defect
   class no longer reproduces; it does not establish a failure rate. The
   "1 in 8,000" figure depends on treating the 30-request probe and the 3 canaries
   as one sample of 33, which assumes the probe's request is representative of the
   live one. The probe uses a reconstructed judge input and a synthetic
   `ReActRun`, so it is close to but not identical with the live path.
2. **The `no_spurious_gaps` diagnosis is my reading, not a measurement.** I read
   the triggering gap from the recorded trace and judged it a false positive. No
   one has independently agreed.
3. **The hypothesis that the band table shifted the Critic's calibration is
   unproven.** It rests on the commit dates plus 3 of 3 runs scoring 5. I have not
   measured the Critic's score distribution before and after the bands on a
   controlled set.
4. **The rationale-length guidance did not do what it was written to do.** The
   median barely moved across the revision, so the bands and examples may improve
   score *consistency* (untested) but did not shorten rationales.
5. **Two unexplained `json_invalid` first attempts** appeared in the final judge
   probe, both repaired. No cause identified.
6. **Judge *quality* is unmeasured.** Nothing here tests whether the judge's
   scores are well calibrated; only that it returns valid verdicts reliably.

---

## 10. What this request is not asking for

- Not a re-open of the fixed defect, and not a claim that the Critic is
  "production ready" in an unqualified sense.
- Not a system redesign, a new transport, or a change of model.
- Not a request to relax the acceptance threshold to make runs pass.

---

## Appendix — where everything lives

| Path | What it is |
| --- | --- |
| `docs/superpowers/2026-09-08-...-fix-log.md` §§83–88 | The full measurement record |
| `src/deep_research/agents/prompts.py:192` | `CRITIC_REVIEW_SYSTEM_PROMPT` (tool-free) |
| `src/deep_research/agents/prompts.py:207` | `CRITIQUE_INSTRUCTION` — the `unsupported_claims` wording |
| `src/deep_research/agents/critic.py:109,137` | Example scores and `_CRITIQUE_SCORE_BANDS` |
| `src/deep_research/agents/critic.py:84` | `_report_fence` (variable-length report fence) |
| `src/deep_research/evaluation/cases/critic.py:810` | `_LIVE_REPORT` — the fixture |
| `src/deep_research/evaluation/cases/critic.py:1012-1017` | The reference expectation |
| `src/deep_research/evaluation/evaluators.py:2109` | `_no_spurious_gaps_passes` |
| `src/deep_research/evaluation/models.py:585,588,611` | Judge rationale bound, `JudgeVerdict`, `extra="ignore"` |
| `src/deep_research/evaluation/judging.py:85,86,179,287` | Judge guidance constants, prompt template, fingerprint |
| `src/deep_research/evaluation/config.py:500` | Where `target_prompt_fingerprint` is recorded |
| `output/evaluations/live-critic-canary/critic/*/results.json` | The three canary artifacts |
