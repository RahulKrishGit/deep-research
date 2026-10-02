# Latency experiments: results

**Status** complete. The protocol and the pass rules are `docs/superpowers/plans/2026-09-30-latency.md` Tasks 17-24; nothing here changes them. The deviations, each recorded where it happened:
- the owner's holiday off-peak ruling;
- the parallel suite arms;
- X3's suite gate run before X2's decision;
- the runner's quality-path fix.

## Checkpoints

| Checkpoint | Approved by the human (their words, date, time UTC) |
|---|---|
| A (baseline) | "1", then, asked to make it explicit: "Yes, 4 runs" (Checkpoint A, 4 baseline runs; OI-4's two extra runs not approved) and "(a) Capture on treatments too" (the X2 and X3 treatment runs also pass `--capture`, so both arms carry the capture's overhead). 2026-10-01 19:58 UTC. Secrets: the owner asked on 2026-10-01 that the env file be used for the live runs' keys, so the orchestrator copied the main checkout's `.env` to the worktree root without reading or printing it (OI-7). |
| X1 | "you can run it now. We still ahve some time", 2026-10-02 about 00:18 UTC, in reply to the Checkpoint X1 message. The owner did not answer the Latte-capture choice, so the plan's own `latte-baseline-1` capture is used. |
| X1-B | "Yes, run X1-B", 2026-10-02 about 02:25 UTC, in reply to the filled Checkpoint X1-B message (X1-A's failed checks, the 12 replays on `tamil-baseline-1` and `latte-baseline-1`, `verifier_batch_figures: 12` and the live case if they pass, and that a rejection reverts the config and the code). |
| X2 | "Yes, run X2 after X1-B", 2026-10-02 about 02:25 UTC, in reply to the filled Checkpoint X2 message (both suite arms, then 3 paired runs with `--capture` per Checkpoint A's choice (a) if the suite gate passes). |
| X3 | Standing approval, 2026-10-02 about 02:30 UTC: "dont wait for my approval. Continue till you execute the entire plan". This covers Checkpoint X3 and any replacement run the plan would otherwise ask about. The final PR still waits for the owner's word (their instruction at the start of this session: "open the final PR only when I say so"). |

## Baseline (Task 20)

| Run | Status | Quality | Seconds | Planner s | Researcher s | Verifier s | Review | Required coverage | lock_wait_s median / p95 / max |
|---|---|---|---|---|---|---|---|---|---|
| latte-baseline-1 | incomplete | None | 1118 | 316 | 297 | 90 | None | 0 | 0.0 / 0.0 / 1.873 |
| latte-baseline-2 | completed | accepted | 1675 | 239 | 302 | 90 | 0.8428571428571429 | 1.0 | 0.0 / 0.0 / 2.308 |
| rome-baseline-1 | completed | accepted | 1472 | 170 | 588 | 93 | 0.8357142857142856 | 1.0 | 0.0 / 0.0 / 1.381 |
| tamil-baseline-1 | completed | accepted | 1403 | 245 | 296 | 185 | 0.8714285714285713 | 1.0 | 0.0 / 0.0 / 0.0 |
| tamil-baseline-2 | completed | accepted | 1470 | 259 | 284 | 211 | 0.8285714285714285 | 1.0 | 0.0 / 0.0 / 0.0 |

The rows above are Step 6's script output, verbatim, run on 2026-10-02 at about 00:01 UTC.
- `latte-baseline-1`'s row shows `None` and `0` because its run-time quality merge hit the path defect below, and `requality` refuses an incomplete run. Its real figures, read from its quality record, are given in "`latte-baseline-1` did not complete" below. It is not a control (H10).
- The controls are `tamil-baseline-1`, `tamil-baseline-2`, `rome-baseline-1` and `latte-baseline-2`, all `session_status completed` and `accepted`.
- `latte-baseline-2` is the owner-approved replacement: started 23:31:50 UTC (`OK Thu 23:31Z`), ended 23:59:52 UTC, exit 0, metrics merged at run time on `c42231ed`. Its other figures: 59 verified-or-corrected findings, 1 dropped (rate 0.0025), 23 cited sources, 20 publishers, 0 refused sentences. Its 1675 s is mostly the writer (460 s) and the reviewer (527 s).
- Other figures of the remaining controls:

| Run | Verified + corrected | Dropped (rate) | Cited sources | Publishers | Refused |
|---|---|---|---|---|---|
| tamil-baseline-1 | 72 | 0 (0.0) | 32 | 19 | 1 |
| tamil-baseline-2 | 163 | 2 (0.0074) | 17 | 11 | 5 |
| rome-baseline-1 | 37 | 2 (0.0030) | 21 | 18 | 2 |

**Captures (Step 6).**
- `tamil-baseline-1/capture`: `evidence_verifier-01.json` and `statement_check-001` to `-005`.
- `latte-baseline-1/capture`: `evidence_verifier-01.json` and `statement_check-001` to `-006`.
- `latte-baseline-2/capture`: `evidence_verifier-01.json` and `statement_check-001` to `-007`.
- All three can serve X1.

**O4's live observation (Ambiguity A6; an observation, not a gate).**
- `lock_wait_s` median / p95 / max per run:
  - tamil-baseline-1: 0.0 / 0.0 / 0.0 (104 timed tool calls), researcher 296 s;
  - tamil-baseline-2: 0.0 / 0.0 / 0.0 (99), researcher 284 s;
  - rome-baseline-1: 0.0 / 0.0 / 1.381 (162), researcher 588 s;
  - latte-baseline-1: 0.0 / 0.0 / 1.873 (161), researcher 297 s;
  - latte-baseline-2: 0.0 / 0.0 / 2.308 (121), researcher 302 s.
- The audit's pre-O4 figures (§1.3): Tamil's researcher stage took 304.2 s, with two all-topic stalls after gaps of 11.2 s and 15.4 s; Latte's took 401.2 s.
- After Part 1, no call waited for the tool gate at the median or p95, and the longest wait in any run was 2.3 s. Tamil's researcher stage is 284–296 s, against 304.2 s. Its time is generation, not lock waits.

**Aborted attempt (orchestrator error, 2026-10-01).** The first `tamil-baseline-1` command started at 19:58:17 UTC after the off-peak check printed `OK Thu 19:58Z`. It was launched under the session tool's default 300 s command deadline, which would have killed it partway through, so the orchestrator cancelled it at about 19:58:45 UTC. Its `events.jsonl` ends at `planner.memory.recalled`, so the planner's first provider call was in flight. No `run.json` was written and nothing was captured. The cost is at most that one partial planner call. Every later paid command runs with the deadline disabled.

**Second aborted attempt (orchestrator error, 2026-10-01).** The owner approved moving the first attempt aside (now `tamil-baseline-1.aborted-1958Z`, which has no `run.json`, so `compare` does not read it) and rerunning: "Yes, move it aside and rerun", 2026-10-01 about 20:15 UTC. The rerun started at 20:16:46 UTC, again under the 300 s default deadline. The orchestrator cancelled it at about 20:16:50 UTC, with the planner's first call just sent. No `run.json` was written. This directory is now `tamil-baseline-1.aborted-2016Z`.

**Third and fourth aborted attempts (orchestrator error, 2026-10-01).** The owner then ruled: "Yes, rerun; launch mistakes are pre-approved" (2026-10-01 about 20:30 UTC). A run the orchestrator cancels within seconds because it was launched wrongly, with no `run.json`, is moved aside, recorded and rerun without asking. Real crashes still go to the owner. The orchestrator launched the run twice more under the same 300 s default deadline and cancelled each within seconds, during the planner's first call:
- started 20:31:23 UTC, now `tamil-baseline-1.aborted-2031Z`;
- started 20:31:39 UTC, now `tamil-baseline-1.aborted-2031Z-b`.

Neither has a `run.json`. All four aborted attempts together cost at most four partial planner calls. From here on, every paid command is started fully detached from the session tool by a launcher under the gitignored `output/latency-experiments/` (`launch.ps1` starting `paid.sh`). The launcher runs the plan's OFF-PEAK CHECK and refuses unless it prints `OK`, so no tool deadline can cut a paid run short.

**Stopped by the owner (2026-10-01).** The detached `tamil-baseline-1` run started at 20:33:33 UTC after `OK Thu 20:33Z`. At about 20:35 UTC the owner wrote "stop the run now", and the orchestrator force-stopped its processes at 20:35:46 UTC. Its `events.jsonl` ends at `planner.memory.recalled` (20:33:37 UTC), still inside the planner's first call. No `run.json` was written and nothing was captured. Its directory stays at `live/tamil-baseline-1` until the owner says what to do with it. A new run of that name stops with `FileExistsError` until the directory is moved. No baseline run has completed.

**Baselines moved onto the merged code (2026-10-01).** The owner merged PR #31 (notes, live step progress, the report, Stop) into `main` as `2bd16abc` and wrote: "the changes are merged to main. So you might have to rebase and then start the testing because I need all these changes to be present while testing". The branch was already pushed, so it was merged rather than rebased, because the handoff forbids force-pushing. `origin/main` was merged into `perf/latency-experiments` as `1bf6f313` and pushed. The branch's only difference from `main` is the handoff document. The merge changes nothing under `src/deep_research/experiments/`, `tests/test_experiments/` or `config.yaml` (`git diff 917909cf origin/main` is empty for those paths). The stopped directory is now `live/tamil-baseline-1.stopped-2033Z`. The owner's message is taken as the go-ahead to run Checkpoint A's approved plan (4 runs, treatments captured too) on the merged code.

**Merged-code checks (2026-10-01, free).** On `1bf6f313`: the full suite gave `5283 passed, 1 deselected`, and that count replaces Step 0's 5096 as the reference for Task 25. The replay matrix gave `Suite: accepted (3 repetitions per case, 35/35 rows)` and `Network: zero (socket layer denied; 0 attempts recorded)`. The seven thresholds printed unchanged (`REVIEW_MARGIN_FLOOR 0.03`, `KEPT_RELATIVE_FLOOR 0.15`, `REFUSED_ABSOLUTE_FLOOR 2`, `REFUSED_RELATIVE 0.25`, `DROPPED_RATE_MARGIN 0.05`, `SUITE_CASE_MARGIN_FLOOR 0.05`, `PEAK_LOOKAHEAD_MINUTES 50`). The secrets check printed `present`, then `config OK`.

**Runs on the merged code.** `tamil-baseline-1` started at 22:00:04 UTC (`OK Thu 22:00Z`) and ended at 22:23:33 UTC with exit 0 and `"status": "completed"`. A sequencer then started `latte-baseline-1` (22:23:42 UTC, `OK Thu 22:23Z`), `rome-baseline-1` and `tamil-baseline-2` in turn, each after its own off-peak check.

**Defect found in the runner (Task 17 tooling).** `tamil-baseline-1`'s `run.json` has no accuracy metrics: `quality_error: "FileNotFoundError: [Errno 2] No such file or directory: 'report-9b132201272f4a26b25db15af4c57a64-0-quality.json'"`.
- `write_document` returns a path relative to `settings.output.directory` (`src/deep_research/tools/write_document.py:80`, unchanged by the merge), but `run_one` read it relative to the working directory. Every real run therefore lost its metrics, and `compare` would have treated every baseline as no control. The runner's tests passed absolute paths, so they never caught it.
- Nothing paid is lost: the quality record exists at `output/report-9b132201272f4a26b25db15af4c57a64-0-quality.json`.
- Fixed in `c42231ed` (`fix(experiments): resolve the published quality record against the output root; add requality`), implemented test-first. The new tests failed before the fix and pass after it, and the full suite gave `5290 passed, 1 deselected`, which is 5283 plus the 7 new tests and is now the reference count for Task 25. The per-task review returned APPROVE with no findings and confirmed that no verdict function or pre-registered constant changed. The branch was fast-forwarded to `c42231ed` and pushed at 22:36 UTC.
- `tamil-baseline-1` was repaired with `live_runs requality --run-dir output/latency-experiments/live/tamil-baseline-1 --config config.yaml` (exit 0, no provider call). Its metrics now read `session_status completed`, `quality_status accepted`, review 0.8714, required coverage 1.0, 72 verified-or-corrected findings, 0 dropped, 32 cited sources, 19 publishers, 1 refused sentence.
- `latte-baseline-1` started on the old code before the fix and gets the same repair when it ends. `rome-baseline-1` and `tamil-baseline-2` start on `c42231ed`.

**`latte-baseline-1` did not complete (2026-10-01).** It started at 22:23:42 UTC (`OK Thu 22:23Z`) and ended at 22:42:26 UTC with exit 0 and `"status": "incomplete"`. Its quality record (`output/report-cb0b00d69b56497aa134f71ea997d9a6-0-quality.json`) reads `session_status incomplete` and `quality_status partial`. Under H10 it is not a control, and `requality` refuses it (`REFUSE: ... has status 'incomplete', not 'completed'.`), as designed.
- The cause is that the review did not pass. The report has no hard failure, no material defect, required coverage 1.0 and every statement `supported`. The reviewer's seven dimensions are 0.8, 0.85, 0.85, 0.85, 0.75, 0.75 and 0.75, whose exact mean is 0.80, the acceptance bar (`SEMANTIC_REVIEW_MEAN = 0.80`, `src/deep_research/utils/types.py:1140`). In floating point, `sum(...)/7` gives `0.7999999999999999`, and `semantic_review_passes` (`src/deep_research/agents/report_reviewer.py:463-468`) compares it with `>= 0.80`, so the report is not accepted.
- This is a floating-point defect in the product's acceptance gate. It is not part of the latency plan and is not fixed on this branch, which would change the code under the arms. It is reported to the owner.
- Other metrics (from `quality_metrics` on that file): review 0.80, required coverage 1.0, 62 verified-or-corrected findings, 1 dropped (rate 0.0019), 30 cited sources, 21 publishers, 1 refused sentence; 1118 s in total.
- The owner's rulings (2026-10-01, about 22:50 UTC):
  - "Run latte-baseline-2 tonight": one replacement paid run, after `tamil-baseline-2`, on the same code;
  - on the floating-point bug: "Yes, separate branch/PR after the experiments". So it is not fixed here, and the code stays identical across all arms.

## X1-A: verifier batch size 5 -> 2 (Task 21)

- 2026-10-02 00:19:15 UTC: Step 2's loop was started detached (`output/latency-experiments/replay-x1a.sh`, the plan's block verbatim). The first `stage_replay run` refused by itself (`REFUSE: DeepSeek peak hours start within 50 minutes; nothing was run.`, exit 2). The OFF-PEAK CHECK printed `REFUSE Fri 00:19:15Z`. Nothing was spent, and no replay file or directory was written. The last allowed start before Friday's 01:00 window was 00:09 UTC.

**Owner ruling: Chinese public holidays are off-peak (2026-10-02, about 00:25 UTC).** "yes since it is national holiday, override it and run the experiments".
- DeepSeek's pricing page (`api-docs.deepseek.com/quick_start/pricing`, read 2026-10-02) says: "Peak hours are 01:00 - 04:00 and 06:00 - 10:00 UTC, Monday through Friday, excluding Chinese public holidays. All other hours are off-peak, including weekends and Chinese public holidays in full."
- China's National Day holiday runs 1-7 October 2026, Thursday to Wednesday (china-briefing.com's 2026 holiday schedule, timeanddate.com, trip.com).
- This amends OI-14 for those dates only. The runners' peak refusal gains an opt-in list of exempt UTC dates (`DEEP_RESEARCH_OFFPEAK_DATES`), with every other rule unchanged, and the change goes through implementation and review before any paid command uses it.
- The 04:00 UTC scheduler started earlier was stopped (no replay process left).
- The exemption is `3d351442` (`fix(experiments): honour owner-declared off-peak dates (Chinese public holidays) in the peak refusal`), implemented test-first.
  - The full suite gave `5317 passed, 1 deselected` (5290 plus 27 new tests), the new reference count for Task 25.
  - The review returned APPROVE with no findings. It confirmed that `PEAK_HOURS_UTC`, `PEAK_LOOKAHEAD_MINUTES`, every threshold, every verdict function and the REFUSE text are unchanged, that the check stays per minute in UTC, and that an unset variable changes nothing.
  - It was pushed at about 01:26 UTC.
- Paid commands now run with `DEEP_RESEARCH_OFFPEAK_DATES=2026-10-01..2026-10-07`. Each prints `OFF-PEAK DATES (owner ruling): 2026-10-01, ..., 2026-10-07`.
- 2026-10-02 01:26:20 UTC: Step 2's loop was restarted under the ruling. Its first line was the `OFF-PEAK DATES` line, and the replays began.
- 2026-10-02 02:18:24 UTC: the loop ended with exit 0. All twelve result files exist (`replay-x1a/{tamil,latte}/{control,treatment}-{1,2,3}.json`, each checked before summarising).

**Verdicts (Step 3; `stage_replay summarize`, pasted from the code).**

| | Tamil | Latte |
|---|---|---|
| exit | **1** (`"passed": false`) | **1** (`"passed": false`) |
| Context Check agreement: control floor / treatment | 0.5948 / 0.659 | 0.8793 / 0.8793 |
| Context Check seconds, median: control / treatment | 253.068 / 193.937 | 169.658 / 180.951 |
| Statement Check agreement: control floor / treatment | 0.9804 / 0.9412 | 0.9 / 0.8978 |
| Statement Check seconds, median: control / treatment | 65.242 / 47.21 | 101.095 / 92.168 |
| `context_check.agreement` | true | true |
| `context_check.figures_dropped` | true | true |
| `context_check.findings_kept` | true | true |
| `context_check.figures_unchecked` | true | true |
| `context_check.faster` | true | **false** |
| `statement_check.agreement` | **false** | **false** |
| `statement_check.inconsistent` | **false** | true |
| `statement_check.unjudged` | true | true |

- The Statement Check fails on both captures.
  - Tamil: 1, 0 and 0 inconsistent sentences of 51 in the control repetitions, against 3, 1 and 2 in the treatment, which also corrected 2 in one repetition. Treatment agreement is 0.9412, below the control floor of 0.9804.
  - Latte: treatment agreement is 0.8978, below the floor of 0.9.
- Latte's Context Check was also slower at batch size 2 (median 180.951 s against 169.658 s).
- The Context Check's accuracy checks all pass on both captures.

**Decision (Step 4): X1-A is rejected.** Nothing was changed: no config edit, no commit of `verifier_batch_size`. Steps 5-8 are skipped and the experiment goes to Task 22 (X1-B).

## X1-B: batches bounded by figure count (Task 22)

**Code (Steps 1-7, free).** `08b231ac` `feat(verifier): optional figure-bounded Context Check batches (latency X1-B)`, the plan's blocks applied exactly. Every anchor occurred exactly once.
- Red: `ImportError: cannot import name 'context_check_batches'`. Green: `tests/test_agents/test_evidence_verifier.py` 136 passed. The plan says 122; the 14 extra tests came in with the notes merge.
- Pin: `evidence_verifier: 3df423028612 -> 16bd62b73e1b`. The plan quotes `31bcab803a6a -> e7009e529434`; the notes merge had re-pinned the old value first. `tests/test_evaluation/test_config.py` gave 80 passed.
- Step 6, with the bound unset: the guard gave 79 passed (the plan says 77; the difference is the notes merge). The replay matrix gave `Suite: accepted (3 repetitions per case, 35/35 rows)` and `Network: zero (socket layer denied; 0 attempts recorded)`.
- Full suite after the commit: `5320 passed, 1 deselected`.
- Review: APPROVE, no findings. The diff matches the plan character for character apart from the pin value. A 20,000-case randomized check of `context_check_batches` showed no item lost or duplicated, order kept, and every multi-item batch within 12 figures. The override path from `stage_replay --override` to `verify` was traced. The Statement Check is untouched.
- Pushed at about 03:09 UTC.

**Replays (Step 9, PAID).** The plan's loop was started detached (`output/latency-experiments/replay-x1b.sh`) at 03:09:18 UTC under the holiday ruling. Its first line was the `OFF-PEAK DATES (owner ruling): 2026-10-01, ..., 2026-10-07` line.
- 2026-10-02 03:53:21 UTC: the loop ended with exit 0. All twelve result files exist (each checked before summarising).

**Verdicts (`stage_replay summarize`, from the code; the full JSON is in `output/latency-experiments/replay-x1b/{tamil,latte}-summary.json`).**

| | Tamil | Latte |
|---|---|---|
| exit | **1** (`"passed": false`) | **1** (`"passed": false`) |
| Context Check agreement: control floor / treatment | 0.6121 / 0.568 | 0.8563 / 0.8352 |
| Context Check seconds, median: control / treatment | 123.778 / 139.109 | 97.325 / 136.57 |
| Statement Check agreement: control floor / treatment | 0.9608 / 0.9782 | 0.88 / 0.9311 |
| Statement Check seconds, median: control / treatment | 48.76 / 55.678 | 90.654 / 41.797 |
| Context Check figures dropped, per repetition: control / treatment | 14, 0, 0 / 0, 0, 0 | 3, 1, 2 / 0, 0, 22 |
| Statement Check inconsistent, per repetition: control / treatment | 0, 0, 0 / 1, 0, 0 | 0, 0, 0 / 0, 0, 0 |
| `context_check.agreement` | **false** | **false** |
| `context_check.figures_dropped` | true | **false** |
| `context_check.findings_kept` | true | **false** |
| `context_check.figures_unchecked` | true | true |
| `context_check.faster` | **false** | **false** |
| `statement_check.agreement` | true | true |
| `statement_check.inconsistent` | **false** | true |
| `statement_check.unjudged` | true | **false** |

- The figure bound made the Context Check slower on both captures: Tamil's median rose from 123.778 s to 139.109 s, and Latte's from 97.325 s to 136.57 s.
- Agreement fell below each control floor.
- One Latte treatment repetition dropped 22 figures.

**Decision (Step 10): X1-B is rejected.** The code commit was reverted with `git revert --no-edit 08b231ac`, giving `890d1ac4`, pushed at about 03:54 UTC. `git diff --stat 08b231ac~1 HEAD -- src tests` is empty, so the code and the pins are exactly as before X1-B. No config was changed. Steps 11-12 are skipped.
- X1 overall: neither X1-A nor X1-B is kept. The X2 and X3 arm overrides therefore use the "none kept" row (P14).
- The X1 controls' own variance was large: Tamil's control Context Check agreement floor was 0.5948 in X1-A and 0.6121 in X1-B.
- Full suite after the revert: `5317 passed, 1 deselected`, the reference count from before X1-B.

## X2: researcher with thinking disabled (Task 23)

**Tier 3, both arms (Step 2, PAID).** Both arms started detached at 03:54:05 UTC: `lat-x2-control` (as configured) and `lat-x2-treatment` (`--target-thinking-mode disabled`).
- Each was preceded by the OFF-PEAK CHECK. The plan's every-day check printed `REFUSE Fri 03:54Z`; the owner's holiday ruling printed `OK Fri 03:54Z with 2026-10-01..2026-10-07`.
- Deviation (time, owner's "time is of the essence"): the two arms run at the same time, not one after the other. Their gate, `compare-suite`, judges only quality averages and the harness verdict, never time, so concurrency cannot move it. Cost if wrong: none to the verdict.
- Experiments: `lat-x2-control-researcher-controlled-20261002T035407Z-890d1ac-dd844497` and `lat-x2-treatment-researcher-controlled-20261002T035407Z-890d1ac-6820f773`.
- Both arms ended at 04:01:47 UTC with harness exit 1:
  - control: `Cases: 0/4 passed`, `Mean score: 0.49`, `Status: FAILED`, `Results: output/evaluations/researcher/lat-x2-control-researcher-controlled-20261002T035407Z-890d1ac/results.json`;
  - treatment: `Cases: 0/4 passed`, `Mean score: 0.55`, `Status: FAILED`, `Results: output/evaluations/researcher/lat-x2-treatment-researcher-controlled-20261002T035407Z-890d1ac/results.json`.

**The tier-3 gate (Step 3, `live_runs compare-suite`, from the code): exit 1.**

| Case | Control average | Treatment average | Margin | Within margin | Treatment passed |
|---|---|---|---|---|---|
| multi-source-coverage | 0.4965 | 0.3045 | 0.342 | true | false |
| conflicting-evidence | 0.3108 | 0.6178 | 0.3835 | true | false |
| partial-search-failure | 0.5497 | 0.4085 | 0.4055 | true | false |
| read-bearing-acquisition | 0.5571 | 0.7012 | 0.434 | true | false |

`harness_passed: false`, `within_control: true`, `passed: false`.

**Why both arms fail.** The control, which is production's own researcher, fails the harness just as the treatment does. Every case fails the `no_prohibited_calls` gate:
- The researcher asked to read URLs the controlled cases do not script (for example `http.get https://sciencedirect.com/cold-climate-trial-results`, `https://nber.org/four-day-self-selection`, `https://eia.gov/us-battery-storage-capacity-2025`).
- The controlled tier's scripted client records any unscripted URL as prohibited (`src/deep_research/evaluation/dependencies.py:603-638`). These are not real network calls.
- This is not new. Every earlier controlled researcher result on this machine fails the same way: `cross-agent-planner-fix-parity-{baseline,confirmation,repaired-baseline,repaired-confirmation}` (2026-09-09/10) are each `FAILED 0 / 3` with 17-48 prohibited calls.
- So the researcher suite has not passed since before the latency work, and X2's tier-3 gate, which requires the harness's own verdict to pass, cannot pass on this codebase for any treatment.

**Decision (Step 3/6): X2 is rejected, by the pre-registered verdict.** Nothing was changed, and the paired runs (Step 4) were not made.
- The criterion was not changed after seeing the data (P10). The record notes that the gate carried no information about the treatment: every case is within the controls' very wide margins (0.34-0.43), and the treatment's mean score (0.55) is above the control's (0.49).
- Re-examining X2 needs a working researcher suite first (its scripted URLs, or the prohibited-call rule), then the owner's decision on the gate. That is outside this plan.

## X3: planner at high (Task 24)

**Tier 3, both arms (Step 2, PAID), run early.** Both arms started detached at 03:55:00 UTC: `lat-x3-control` (`--reasoning-effort max`) and `lat-x3-treatment` (`--reasoning-effort high`). The checks printed `REFUSE Fri 03:55Z` (plan, every day) and `OK Fri 03:55Z with 2026-10-01..2026-10-07` (owner's holiday ruling).
- Deviation from H3's order (X3 last), taken for time under the owner's standing approval:
  - The planner's suite gate runs alongside X2's suite gate, before any X2 paired run.
  - The gate compares the planner at `max` with `high` and judges quality only. It depends on neither X1's nor X2's decision, and no timed run overlaps it.
  - X3's paired runs and its keep decision still come after X2's decision, with X2's pin in the override (P14).
  - Cost if wrong: none to any verdict.
- Experiments: `lat-x3-control-planner-controlled-20261002T035502Z-890d1ac-92166f01` and `lat-x3-treatment-planner-controlled-20261002T035502Z-890d1ac-7f5615c1`.
- Both arms ended with these results:
  - control (`max`): 05:12:07 UTC, `Cases: 3/4 passed`, `Mean score: 0.87`, `Status: FAILED`, `Results: output/evaluations/planner/lat-x3-control-planner-controlled-20261002T035502Z-890d1ac/results.json`;
  - treatment (`high`): 04:37:45 UTC, `Cases: 4/4 passed`, `Mean score: 0.89`, `Status: REVIEW REQUIRED`, `Results: output/evaluations/planner/lat-x3-treatment-planner-controlled-20261002T035502Z-890d1ac/results.json`.
- The control arm took 77 minutes and the treatment 43, running at the same time.

**The tier-3 gate (Step 3, `live_runs compare-suite`, from the code): exit 0.**

| Case | Control average | Treatment average | Margin | Within margin | Treatment passed |
|---|---|---|---|---|---|
| focused-decomposition | 0.9139 | 0.9171 | 0.05 | true | true |
| ambiguous-scope | 0.7635 | 0.9 | 0.05 | true | true |
| planning-tool-failure | 0.9065 | 0.9025 | 0.05 | true | true |
| scoped-evidence-targets | 0.875 | 0.8662 | 0.0615 | true | true |

`harness_passed: true`, `within_control: true`, `passed: true`.

**Tier 4, three paired runs (Step 4, PAID).** Override (no earlier experiment kept): `{"llm": {"model_overrides": {"planner": {"reasoning_effort": "high", "timeout": 1800.0}}}}`, with `--capture` (Checkpoint A choice (a)). The chain (`output/latency-experiments/x3-runs.sh`) started `tamil-x3-1` at 05:12:26 UTC, followed by `latte-x3-1` and `rome-x3-1`, each after the off-peak check (`REFUSE Fri 05:12Z` by the plan's every-day check, `OK` by the holiday ruling).
- While `tamil-x3-1` ran, the conditional keep edit (Step 6) was prepared in a separate worktree (`.worktrees/x3-keep`, branch `prep/x3-keep`, not merged). Its full suite and replay matrix ran on this machine for about 6 minutes.
  - That adds local CPU load during one treatment run and none during the baselines. It can only make the treatment slower, so it biases the time verdict against keeping X3, the conservative direction. Recorded as a known deviation.
  - The edit is merged only if Step 5's verdict says keep.

- The runs:
  - `tamil-x3-1`: 05:12:26-05:39:17 UTC, exit 0;
  - `latte-x3-1`: 05:39:17-06:01:45 UTC, exit 0;
  - `rome-x3-1`: 06:01:45-06:23:49 UTC, exit 0.
  - Each `run.json` records the override under `"overrides"`.

| Run | Status | Quality | Seconds | Planner s | Review | Required coverage | Verified + corrected | Cited sources / publishers | Refused |
|---|---|---|---|---|---|---|---|---|---|
| tamil-x3-1 | completed | accepted | 1604 | 201 | 0.8286 | 1.0 | 183 | 34 / 16 | 0 |
| latte-x3-1 | completed | accepted | 1342 | 126 | 0.8714 | 1.0 | 110 | 31 / 19 | 3 |
| rome-x3-1 | completed | accepted | 1319 | 75 | 0.8714 | 1.0 | 54 | 28 / 20 | 1 |

**The verdict (Step 5, `live_runs compare --treatment x3 --stage planner`, from the code): exit 1.** The full JSON is in `output/latency-experiments/x3-verdict.json`.
- `review_margin` 0.0429, `kept_margin` 0.5583.
- `accuracy_passed: false`, `time_passed: true`, `passed: false`.
- `stage_faster_on: 3`, `mean_seconds_ratio: 0.9378`, `unpaired: []`.
- `controls_used`:
  - latte: repetition 1 (`session_status` null, used false), repetition 2 (`completed`, used true);
  - rome: repetition 1 (`completed`, used);
  - tamil: repetitions 1 and 2 (`completed`, both used).

| Question | Planner s: treatment / control mean | `seconds_ratio` | Failed checks |
|---|---|---|---|
| tamil-1 | 201.363 / 251.894 | 1.1161 | none |
| latte-1 | 126.313 / 239.118 | 0.8013 | **`refused_sentences`** |
| rome-1 | 75.21 / 170.272 | 0.896 | none |

Every other check is true on all three questions: completed, no hard failures, no unjudged sentences, no unresolved citations, review scored, accepted if controls were, review score, material defects, required coverage, not found, verified plus corrected, cited sources, publishers, dropped rate.

**Decision (Step 6): X3 is rejected, by the pre-registered verdict.** Nothing was changed.
- The prepared keep commits on `prep/x3-keep` (`72a240d4`, plus `d2a10ff0`, which fixed two comments its review found still saying `max`) were never merged. The branch and its worktree were deleted. `config.yaml`, `src`, `tests` and `scripts` are as at `f99b283e`.
- What failed: Latte's treatment refused 3 sentences in the Statement Check. Its only usable control, `latte-baseline-2`, refused 0, so the allowed maximum is max(2, ⌈25% × 0⌉) = 2.
- `latte-baseline-1` refused 1 sentence, but it is not a control under H10 (incomplete, from the 0.80 rounding defect). With it, the allowed maximum would have been 3 and the check would have passed. The rule was applied as fixed.
- Time: the planner stage was faster on all three questions, by 50-114 s, and the mean end-to-end ratio was 0.9378.

## Decisions

| Experiment | Decision | Commit |
|---|---|---|
| X1-A: `verifier_batch_size` 5 -> 2 | rejected (stage replay: Tamil and Latte both exit 1) | none |
| X1-B: Context Check batches bounded at 12 figures | rejected (stage replay: Tamil and Latte both exit 1); its code `08b231ac` reverted by `890d1ac4` | none |
| X2: researcher thinking disabled | rejected (tier-3 gate exit 1; the researcher suite fails the control too, see X2) | none |
| X3: planner `reasoning_effort` high | rejected (paired runs exit 1: Latte `refused_sentences` 3 > 2; time passed) | none |

**What the plan delivered on latency.**
- No experiment was kept, so the shipped configuration is Part 1's: steps 0-4 of the audit.
- The baseline's mean Tamil time is **1,436.5 s (23.9 min)**, from `tamil-baseline-1` (1,403 s) and `tamil-baseline-2` (1,470 s), against the audit's pre-Part-1 1,504.7 s graph time.
- The audit projected 22-23 min for steps 0-4 alone, and 18-20 min with X1 and X2, or 16-19 min with X3 as well. Neither later band applies: no experiment was kept, so there is no measured stage saving.
- Within the rejected X3, the planner stage ran 50-114 s faster per question.

**Follow-ups for the owner, outside this plan.**
1. **The 0.80 rounding defect in the review gate** (`semantic_review_passes`, `src/deep_research/agents/report_reviewer.py:463-468`). An exact 0.80 mean computes as 0.7999999999999999 and fails `>= 0.80`. The owner ruled it a separate branch and PR after the experiments. It also decided X3's verdict indirectly, because it took `latte-baseline-1` out of the controls.
2. **The researcher's controlled suite fails 0/N on `no_prohibited_calls`** in every recorded run since 2026-09-09: the researcher asks for URLs the cases do not script. Until it is repaired, no researcher experiment (X2 included) can pass a tier-3 gate.
3. **X3 was rejected on a single Latte control with 0 refusals** (OI-4's second control per question was not approved at Checkpoint A). If the owner wants X3 re-examined, more Latte controls would give that check a measured spread. That needs a new approval.

**Final configuration check (Task 25 Step 2, 2026-10-02 about 06:30 UTC, at `1f25651a` plus this record).**
- Full suite: `5317 passed, 1 deselected`.
  - That is Step 0's 5096 on the pre-merge branch, plus the notes merge (5283), the runner's quality-path fix (+7: 5290) and the holiday exemption (+27: 5317).
  - X1-B's three tests were reverted with it.
- Replay matrix: `Suite: accepted (3 repetitions per case, 35/35 rows)` and `Network: zero (socket layer denied; 0 attempts recorded)`.

**Secrets.** The orchestrator copied the main checkout's `.env` to this worktree's root on the owner's instruction. The owner should delete that copy (OI-7).
