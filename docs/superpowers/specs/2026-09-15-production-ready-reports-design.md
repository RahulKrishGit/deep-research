# Production-Ready Report Generation — Design

**Status:** approved design, pending spec review
**Date:** 2026-09-15
**Branch:** `codex/cross-agent-planner-fix-parity`
**Amends:** `docs/superpowers/plans/2026-09-14-cli-report-quality-and-agent-output-integrity.md` and
`docs/superpowers/plans/2026-09-15-cli-live-canary-amendment.md`

---

## 1. Problem

Two completed plans made the CLI pipeline **correct** and **measurable**. Neither made it
**good**, and the difference is now the whole remaining gap.

- The report-quality plan (2026-09-14) fixed the pathologies of a committed CLI artifact: canonical
  replace-merged snapshots keyed by stable identity, real read-provenance on verdicts, deterministic
  quality gates the model score cannot override, one terminal finalizer, and a CLI whose numbers come
  from typed state. Its Tasks 1–9 are complete and review-clean.
- The amendment plan (2026-09-15) exists because the first live canary run failed, and the failure was
  **undiagnosable** — `planning_failed_error` recorded only the exception class name and discarded
  `PlanningError.problems`. It preserves that failure as evidence, adds safe diagnostics, and builds
  run-scoped request ceilings that are reserved at the real transport boundaries, because the
  controller's declared ceilings were neither correctly derived nor observable and a run **breached
  them by ~11%** with nothing stopping it.

The second canary run then completed the entire production pipeline and produced a report that
**passed every integrity gate** — 0 duplicate claims, 0 duplicate source rows, 0 uncited settled
points, all six required sections, every listed source cited, 92% smaller than the committed baseline
— and still **failed the release criteria**: terminal quality `partial`, coverage **67%** (4 of 6
topics), whole-report critic **4/10**, and only **4 cited sources with 2 verified claims**.

That is the gap this design closes. The pipeline is honest, bounded, and well-tested; it does not yet
gather enough evidence to answer a research question acceptably.

## 2. Goal

**A single live CLI run clears every release criterion in §4.**

"Production ready" is defined by the existing criteria, already agreed, not by new ones invented here.
The plan's output is one live run whose record shows all ten criteria met, verified by an independent
review.

## 3. Architecture

Three sequential phases on the existing branch. Phase 1 is already written and partly executed;
Phases 2 and 3 are new.

```
Phase 1  Legibility + bounded spend      (Tasks 1–11, existing; 1–3 done)
              ↓  failures are diagnosable, spend cannot silently overrun
Phase 2  Evidence + reliability          (Tasks 13–19, new)
              ↓  scrapes work, budgets fit the work, effort is per-agent
Phase 3  Convergence                     (Tasks 20–21, new)
              ↓  iterate predeclared canaries until one clears all ten criteria
         Production-ready report
```

**Why one plan and not two.** The phases are not independent subsystems: Phase 2's diagnostics depend
on Phase 1's classification work, and Phase 3's iterations depend on Phase 1's request ceilings to be
safe to run at all. Splitting them would mean two specs arguing from the same evidence and a Phase 3
that cannot start until Phase 1's infrastructure exists.

**Why Phase 2 must observe before it fixes.** Task 3 shipped safe classification of scraper failures.
Before it, the failure reason was never populated, so the **58% scrape failure rate**
(14 of 24 calls) has no known cause. It could be timeouts, robots denials, content-type rejections, or
something else; each implies a different fix. A plan that pre-commits to a scraper fix would be
guessing, so Task 15 is a diagnosis whose outcome determines Task 16.

## 4. Success criteria (verbatim from the prior plan, Task 10 Step 7)

A single authorized canary run must show all of:

- terminal `accepted` quality;
- coverage ≥ 0.80 and every planned topic accounted for;
- zero duplicate claims and zero duplicate source rows, and zero unresolved citations;
- every cited source genuinely scored and every settled point claim-linked;
- all verification passages provenance-bearing and independently published;
- reader report ≤ 8,000 words unless the question explicitly requests longer;
- backmatter ≤ 35% of reader-report characters;
- no unexplained agent error and no generic-only limitation;
- whole-report judge score ≥ 0.80;
- CLI summary exactly matching artifacts and state.

## 5. Phase 1 — Legibility and bounded spend (existing, structure preserved)

Tasks 1–11 of the amendment plan stand **unchanged in content**. Only the plan's header (goal,
architecture) and its global constraints change, because the plan now argues toward a quality goal
rather than a measurement goal.

| Task | State |
| --- | --- |
| 1 Track the spent Q1 as a failed validation | complete, reviewed, pushed (`21df64c`) |
| 2 Preserve safe `PlanningError.problems` | complete, reviewed, pushed (`e772966`) |
| 3 Classify scraper failures safely | complete after 1 fix round, reviewed, pushed (`9252263`) |
| 4 Project bounded scraper diagnostics into agent records | not started |
| 5 Request-budget primitive and configuration | not started |
| 6 Count every DeepSeek transport attempt and token response | not started |
| 7 Count every OpenAI transport attempt and token response | not started |
| 8 Count Tavily attempts, preserve hard-limit pass-through | not started |
| 9 Share one budget through runtime, halt the graph | not started |
| 10 Request-scoped CLI controls and safe live/terminal output | not started |
| 11 Offline gate and the user-owned broad review packet | not started |
| 12 Draft the next blocked canary after a clean review | **retired — superseded by Task 20** |

**Task 12 is retired, not renumbered.** The amendment plan's Task 12 exists to draft the next blocked
canary once the amendment's own review is clean. Under this design that intent is absorbed into
Task 20, which does exactly that on every iteration and no longer treats the canary as a one-off to be
drafted after a single review. Retiring it keeps the existing task numbering stable for the tasks that
are already written and reviewed, rather than shifting references in a plan other sessions are reading.

**Task 11 is not the final review.** Task 11 gates the *amendment* phase: it runs the offline gate and
prepares an exact-head packet for the user-owned broad review of the legibility-and-bounded-spend work.
Task 21 is the **final** validation record for the converged SHA plus the final review packet. They are
different review points at different SHAs with different scopes.

## 6. Phase 2 — Evidence and reliability (new Tasks 13–19)

### Task 13 — Close the `web_search` telemetry leak
`src/deep_research/tools/web_search.py:174-182` holds a helper **AST-verified byte-identical** to the
pre-fix `web_scraper` helper, and publishes `str(error)` as the public failure message; its details
also lack `retries`. Same class of defect, same fix shape as Task 3, and the same bounded-detail
contract (`attempts` 1..3, `retries` 0..2, `status_code` 100..599 when in range, lower-case ASCII
media type ≤ 64 chars or `unknown`).

### Task 14 — Close the central `str(error)` forwarding
`src/deep_research/tools/base.py:119-127,134` builds `ToolExecutionError(str(error) or
type(error).__name__, ...)` for **any** non-`ToolExecutionError`, and publishes it at `:134`. So every
tool that lets a raw exception escape publishes exception text into `ToolError.message`, which
`agents/react.py:53` folds into the model-visible observation summary. Fixing this centrally closes
the **class** rather than one instance per tool; per-tool static messages alone would leave the next
tool exposed. Requires an invariant test that a hostile exception from an arbitrary tool cannot reach
`ToolError.message`.

### Task 15 — Scraper-diagnosis canary (evidence only, no code change)
Predeclare and run **one** live canary whose only purpose is to read the classified failure reasons
now available. Deliverable: a diagnosis listing the observed failure classes with counts. **No
production change in this task.** Its outcome decides Task 16's content — and if the diagnosis shows
the failures are not a defect at all (for example, expected 404s on dead links), that is a valid and
plan-changing result.

### Task 16 — Fix scraper reliability, guided by Task 15
Content determined by the diagnosis. Candidate causes and their distinct fixes: per-request timeouts
against slow hosts (`httpx` timeout configuration, **not** `scraper timeout_s` alone); robots
disallowing the sources the Researcher chooses (a sourcing problem, not a scraper problem);
content-type rejections (a source-classification problem). Must preserve every retry decision and call
count unless the diagnosis specifically implicates the retry policy.

### Task 17 — Per-agent reasoning effort in production
Production exposes one global `llm.reasoning_effort`. The approved cutover baseline is per-agent
(`planner`, `fact_checker`, `synthesizer`, `critic` at `max`; `researcher`, `source_evaluator` at
`high`), and today it exists only in `evaluation.target_reasoning_effort_overrides`, which applies to
**evaluation targets**. Implement the equivalent for production. **Risk to test explicitly:** the
controller's earlier global `max` override broke the planner outright, so this must be validated
per-agent rather than assumed to be a strict improvement.

### Task 18 — Agent tool budgets
`agent_tool_budget_exhausted` fired **51 times in the researcher, 34 in the fact_checker and 8 in the
critic**, repeatedly with up to five provider-requested calls dropped at once. The provider
consistently wants more work than `agents.tool_budget: 10` allows per ReAct loop, which is the most
likely direct cause of 67% coverage. Decide — on Task 15's evidence plus this count distribution —
between a global increase and per-agent budgets (the distribution suggests the researcher is the
outlier). This task is where the plan's former prohibition on changing `tool_budget` is lifted.

### Task 19 — Evidence-volume experiments
Only 4 sources were cited and 2 claims verified. The prior plan already specifies these experiments
with acceptance criteria; this task promotes them from deferred to planned, **one at a time**:

| Experiment | Current | Candidate | Accept only if |
| --- | --- | --- | --- |
| Tavily search depth | `basic` | `advanced` | primary-source retrieval and coverage improve enough to justify latency/cost |
| Tavily results | 5 | 8 | source quality/coverage improves without increasing unused-source noise |
| Research observation summary | 200 chars | 600 chars | tool-loop completion and source-read selection improve without prompt overflow |
| Graph refinement budget | 3 | unchanged first | increase only if targeted refinements close gaps and cost per accepted report stays acceptable |

## 7. Phase 3 — Convergence (new Tasks 20–21)

### Task 20 — Iterate predeclared canaries to the bar
Each iteration is predeclared with its own request ceilings, run under the US$100 authorization, and
evaluated against **all ten** criteria in §4. Every shortfall produces a **diagnosis and an
amendment** — never an ad-hoc prompt, threshold, or fixture edit. This inherits the prior plan's
Step 6 rule: run once, do not rerun a failed gate without an amendment.

**Stopping rules.** Task 20 stops when:
1. one run clears all ten criteria; **or**
2. the iteration budget in §8 is exhausted; **or**
3. two consecutive runs fail the same criterion — at which point the plan produces a documented
   diagnosis and a proposed amendment instead of another run.

**Honest-failure path.** If coverage cannot reach 0.80 within budget, the plan's output is an explicit
partial result naming the shortfall and the evidence for it. **A plan that can only report success is
not a plan**, and no criterion may be relaxed, no threshold lowered, and no fixture adjusted to make a
run appear to pass.

### Task 21 — Final validation record and review packet
The validation record for the **converged** SHA (distinct from Task 11's amendment-phase packet): it
attests only to criteria actually met, with artifact hashes, token and request actuals, and the exact
candidate SHA. Then the user-owned broad whole-branch review.

### Note on the two evidence-gated tasks (16 and 18), and why they are not placeholders

Task 16's content and Task 18's chosen value depend on evidence that does not exist yet — Task 15's
diagnosis for the former, and the exhaustion distribution plus that diagnosis for the latter. Writing
a specific fix now would be inventing a cause. Both tasks instead state the candidate causes and their
**distinct** fixes, so the implementer has a decision procedure rather than a blank page:

- Task 16: per-request timeouts against slow hosts → `httpx` timeout configuration (not
  `scraper timeout_s` alone); robots disallowing chosen sources → a sourcing problem, fixed in the
  Researcher, not the scraper; content-type rejections → a source-classification problem.
- Task 18: the exhaustion counts are dominated by the researcher (51 of 93), so a global increase and
  a per-agent increase are genuinely different choices with different cost profiles.

If Task 15 shows the failures are **not a defect** — expected 404s on dead links, say — then Task 16
is legitimately reduced to that finding, and that is a valid plan-changing result rather than a
failure to deliver.

## 8. Constraint changes

| Prior constraint | Change | Why |
| --- | --- | --- |
| "Do not change production reasoning effort (`high`), `agents.tool_budget` (`10`), `llm.timeout` (`60.0`), scraper `timeout_s` (`10.0`), or scraper retry policy" | **Replaced.** Changes are permitted where the task carries evidence for the specific change, and must preserve fail-closed behaviour, the retry/repair contracts, and response clearing. | The prohibition was correct for a measurement-only plan. Leaving it would create a plan that forbids its own success. |
| "No live external call is authorized by this plan" | **Extended.** Live calls are permitted **only** inside predeclared canary tasks, under that task's declared ceilings, within the §9 authorization. Every other task stays offline. | Reaching the §4 bar requires observation. |
| No live budget stated | **Added.** One authorization covering ~10 iteration runs inside a US$100 currency ceiling, enforced by Phase 1's `RequestBudget` at the transport boundaries plus per-run predeclared request ceilings. | The prior run breached an unenforceable ceiling; the enforcement now exists. |
| Content-free telemetry | **Unchanged.** Public state, errors, progress and CLI output carry only static project-authored text and bounded categorical values. Never exception messages, URLs, page content, prompts, responses, tool arguments, credentials, or environment values. | Load-bearing; Tasks 13–14 exist to extend it. |
| Strict RED → minimal → GREEN; exact-path staging; one reviewable commit per task; review before the next task | **Unchanged.** | Working well. |

## 9. Authorization

- **~10 live iteration runs inside a US$100 currency ceiling**, granted 2026-09-15.
- Each run is predeclared before it happens: candidate SHA, question set, repetitions, DeepSeek /
  OpenAI / Tavily request ceilings, stop conditions, artifact locations.
- The provider-side spend cap is the enforced bound; the request ceilings are the operational bound.
- LangSmith tracing stays **on** (`langsmith.tracing_enabled: true`, project `deep-research-dev`),
  explicitly authorized.
- No criterion in §4 may be waived to reduce spend.

## 10. Evidence index

Every claim this design rests on, with its source:

| Claim | Value | Source |
| --- | --- | --- |
| Integrity gates pass in a live run | 0 duplicates, 0 duplicate rows, 0 uncited points | Canary Q1 report, session `50cbf5926e8b479ea90793312396e440` |
| Report structure correct | 6/6 H2 sections; 4 markers used of 4 listed; 10,724 B / 1,568 words vs the 127,872 B baseline | Same |
| Coverage shortfall | 4/6 topics = 67%, below the 0.80 gate | Same |
| Critic verdict | 4/10, terminal `partial` | Same |
| Evidence thinness | 4 cited sources, 2 verified, 0 contradicted | Same |
| Tool-budget starvation | 51 researcher, 34 fact_checker, 8 critic exhaustions; up to 5 dropped calls each | Canary Q1 log |
| Scrape failure rate | 14 of 24 `web_scraper` calls failed (58%), cause unknown | Canary Q1 log; reason becomes visible only after Task 3 |
| Ceiling breach | `web_search` 365 tool invocations vs a declared ≤330 ceiling, abort at 297 | Canary Q1 log; the controller's derivation error |
| Run cost | 703,427 tokens (569,718 in / 133,709 out), 21.0 minutes | Canary Q1 log |
| Failed run cost | 22,593 tokens; planner `PlanningError`, cause was the controller's forced `max` | Failed-run log and diagnosis |
| `web_search` sibling defect | byte-identical to the pre-fix helper, AST-verified | Task 3 review |
| Central `str(error)` forwarding | publishes exception text for any non-`ToolExecutionError` | Task 3 review |
| `urlsplit` leak reachability | bracketed host and NFKC netloc embed caller-supplied text; reaches the model-visible observation | Task 3 review; reproduced independently by the controller |

## 11. Non-goals

- **Not** a redesign of the six-agent graph or the provider transport.
- **Not** a re-litigation of the Step 7 criteria. They are adopted as given.
- **Not** a general refactor. Each task touches what its evidence requires.
- **Not** unbounded live spending: the §8 ceiling is a stop condition, not a target.
- **Not** a prompt-tuning exercise. If a task cannot pass a gate without editing prompt text to suit
  it, that is a finding, not a fix.

## 12. Open unknowns

1. **The cause of the 58% scrape failure rate.** Unknown until Task 15; Task 16's content depends on it.
2. **Whether coverage ≥ 0.80 is reachable at all** at this model, effort and budget. The canary
   produced 4 sources and 2 verified claims; nothing yet proves that can triple.
3. **Whether the per-agent effort split helps or hurts.** The controller's global `max` override broke
   the planner; `high`-for-all works. Raising only some agents to `max` is untested at that seam.
4. **Whether the tool budget is the binding constraint** or merely the most visible one. The
   exhaustion counts are strong evidence, not proof.

Each unknown has a task that resolves it, and none is resolved by assumption.
