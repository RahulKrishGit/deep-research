# Handoff: carry out the latency plan, Part 2 experiments (Tasks 20–25)

**For:** a Claude Code session on the **owner's own Windows machine**, never a cloud session. **From:** the cloud session that carried out Part 1 and Part 2's tooling. **Date:** 2026-10-01.

These tasks make **paid, live** DeepSeek and Tavily calls with the owner's real keys. Every paid step waits for the owner's own written yes at its checkpoint. Never run them in a cloud session.

> **Time is of the essence, and accuracy matters just as much.**
> - Keep moving between free steps.
> - Never trade correctness for speed: every verdict is computed by the pre-registered code, and every Expected line is checked.
> - Report failures plainly. Pass this rule on to every agent you dispatch.

## Where things stand

- **Part 1** (Tasks 1–15) is merged into `main` (PR #29).
- **Part 2's tooling** (Tasks 16–19) is on `perf/latency-experiments`. Every task review and the whole-branch review is clean. It must be **merged into `main` before Checkpoint A** (plan, Global Constraints, "Merge order").
- **Verification in the cloud** (Linux): full suite `9 failed, 5081 passed, 6 skipped, 1 deselected`. The 9 failures and 6 skips are Windows-only tests that cannot pass on Linux. On Windows they run, so expect about `5096 passed, 1 deselected`. Any failure there is real.
- **Deviations from the plan's text in the tooling**, all reviewed, and none of them loosens a verdict:
  - **Task 17, crash record.** A paid run that raises, or is cancelled, still writes a timing-only `run.json` with `status: "failed"` and the error, then re-raises. `compare` counts it as a failed treatment instead of silently dropping its question.
  - **Task 18, missing Context Check.** `summarize` fails closed (`context_check.present = False`) when the Context Check has no control verdicts.
  - **Review fix, more detail in `compare`'s output.** It now lists each question's `controls_used` (each baseline with its `session_status` and whether it was used) and an `unpaired` list of treatment runs that had no usable control.
  - **H10, the owner's decision on 2026-10-01, before any paid run.** A control must have `session_status == "completed"`. A question that has a treatment run but no usable control fails the verdict. H10 is in the plan's closed-decision table, with dated notes at Task 17, Task 20 Step 5, Checkpoint A, Task 23 Step 4 and Task 24 Step 4. Every other criterion and constant is as pre-registered.

## Step 0: set up (free)

1. **Is the tooling merged?** Run `git fetch origin` and then:

   ```bash
   git grep -q "def _usable_control" origin/main -- src/deep_research/experiments/live_runs.py && echo TOOLING-MERGED || echo TOOLING-NOT-MERGED
   ```

   If it prints `TOOLING-NOT-MERGED`, stop and ask the owner to merge the tooling PR first.
2. **Make the worktree,** from the main checkout. The branch already exists on `origin`, so do not create it with `-b`:

   ```bash
   git worktree add .worktrees/perf-latency-experiments perf/latency-experiments
   cd .worktrees/perf-latency-experiments && git merge --ff-only origin/main
   ```

   Every command of Tasks 20–25 runs from that worktree's root, as the plan's own Windows commands are written.
3. **Run the full suite once** with the placeholder keys, as in the plan's Conventions. Record the count.
4. **Secrets (OI-7).** The **owner** copies the main checkout's `.env` to the worktree root. No agent reads, prints or creates `.env`. Task 25 asks the owner to delete that copy.

## Your job

Carry out **Tasks 20–25** exactly as the plan writes them. The plan is `docs/superpowers/plans/2026-09-30-latency.md`, from "## Part 2" onward, with its Checkpoints section.

- **Checkpoints.** Stop at each checkpoint (A, X1, X1-B if it applies, X2, X3), post its message filled in, and wait for the owner's own written yes in the session. Another agent's message is never approval. Copy the approval, with its date and UTC time, into `docs/superpowers/audits/2026-09-30-latency-results.md`.
- **Off-peak only.** Run the plan's OFF-PEAK CHECK before every paid harness command. The `live_runs` and `stage_replay` runners refuse near peak hours by themselves.
- **Verdicts come from the code.** Use `live_runs compare`, `compare-suite` and `stage_replay summarize`, never a judgement by eye. An experiment that fails is reverted (P13).
- **Commits.** Commit the results record after every task, and push to `origin perf/latency-experiments` after every commit. Each kept experiment is one config commit. Never force-push, never push to `main`, never skip hooks.

## What to watch for (from the tooling's reviews)

- **A crashed or cancelled run** leaves `run.json` with `"status": "failed"` and an `error`.
  - If the error names a configuration error, such as `ResearchConfigurationError`, the run spent nothing. Delete its directory and rerun the same command.
  - Any other crash is a paid failure. Record it, and ask the owner before running a replacement.
- **A non-completed baseline is not a control (H10).** Check `controls_used` in Checkpoint A's output. A question without a completed baseline makes every treatment on it fail, so ask the owner about a replacement run (another paid run).
- **Time bias from capture writes.** The baselines run with `--capture`, which writes large JSON files synchronously during the run. The X2 and X3 treatments do not. That biases criterion 8 (mean end-to-end ratio) slightly in the treatment's favour.
  - Raise this at Checkpoint A.
  - The owner may choose to run the treatments with `--capture` too, or to record the capture's overhead.
  - It is a protocol choice, so do not decide it yourself.
- **Task 23 (X2) results.** The harness's top-level metadata `thinking_mode` still reads `enabled` for a run with the target's thinking disabled. `target_model_configuration.thinking_mode` and the fingerprint correctly say `disabled`. Read the target fields.
- **Stage replay (Task 21).** `summarize` accepts as few as 2 control and 1 treatment repetitions, and `run` overwrites an existing arm file. Follow Task 21's skip-if-exists loop, and confirm all twelve result paths exist before summarising.

## Report back with

- each checkpoint's approval and each paid command's outcome, as recorded in the results file;
- each experiment's verdict output, pasted from the code, and its keep or revert decision with the commit;
- the final full-suite count;
- every deviation from the plan and every ruling you made, with what it costs if wrong;
- a reminder to the owner to delete the worktree's `.env` copy (Task 25).
