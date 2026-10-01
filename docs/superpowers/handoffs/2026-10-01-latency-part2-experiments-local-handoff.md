# Handoff: the rest of the latency plan, Tasks 20–25 (the paid experiments), on the owner's machine

**For:** a Claude Code session on the **owner's own Windows machine**. Never run this in a cloud session. **From:** the cloud session that carried out Part 1 and Part 2's tooling. **Date:** 2026-10-01.

This is everything left of `docs/superpowers/plans/2026-09-30-latency.md`. The tasks make **paid, live** DeepSeek and Tavily calls with the owner's real keys. Every paid step waits for the owner's own written yes at its checkpoint.

> **Time is of the essence, and accuracy matters just as much.**
> - Keep moving between the free steps.
> - Never trade correctness for speed: every verdict comes from the pre-registered code, and every Expected line is checked.
> - Report failures plainly.
> - Pass this rule on to every agent you dispatch.

## Where things stand

| Part | Tasks | State |
|---|---|---|
| Part 1: steps 0–4 (instrumentation, scheduling, ToolGate) | 1–15 | Merged into `main` (PR #29) |
| Part 2 tooling: capture, paired runs, stage replay, thinking toggle | 16–19 | Merged into `main` (PR #30, `917909c`) |
| **Part 2 experiments: baseline, X1, X2, X3, close** | **20–25** | **This handoff** |

**Changes to the tooling beyond the plan's text.** All were reviewed, and none loosens a verdict.
- **Task 17, crash record.** A paid run that raises or is cancelled still writes a timing-only `run.json` with `status: "failed"` and the error, then re-raises. `compare` counts it as a failed treatment instead of silently dropping its question.
- **Task 18, missing Context Check.** `stage_replay summarize` fails closed (`context_check.present = False`) when the Context Check has no control verdicts.
- **More detail in `compare`.** Its verdict now also prints `controls_used` (each baseline with its `session_status` and whether it was used) and `unpaired` (treatment runs with no usable control).
- **H10, the owner's decision of 2026-10-01, made before any paid run.** A control must have `session_status == "completed"`, and a question that has a treatment run but no usable control fails the verdict. H10 is in the plan's closed-decision table, with dated notes at Task 17, Task 20 Step 5, Checkpoint A, Task 23 Step 4 and Task 24 Step 4. Every other criterion and constant is as pre-registered. Task 20 Step 2 prints them, and they must read exactly as the plan says.

## What is left, in order

| Task | What | Checkpoint before paid calls | Paid calls |
|---|---|---|---|
| 20 | Start the results record; print the thresholds; baseline runs with the stage capture bound | **A** | 4 full runs: Tamil ×2, Latte, Rome. 6 if the owner approves OI-4's two extra baselines, which review 1 recommends. |
| 21 | X1-A: verifier batch size 5 → 2, by stage replay on the baselines' captures, then the verifier's live case; keep or revert | **X1** | 12 stage replays, then 1 live harness case if they pass |
| 22 | X1-B: Context Check batches bounded by figure count. **Only if X1-A was rejected.** Code change, stage replay, live case; keep or revert | **X1-B** | 12 stage replays, then 1 live case if they pass |
| 23 | X2: researcher with thinking disabled. Controlled-suite gate (`--target-thinking-mode`), then paired runs; keep or revert | **X2** | the researcher's controlled suite twice, then 3 full runs if the gate passes |
| 24 | X3: planner at `high`. Suite gate, then paired runs; keep or revert | **X3** | the planner's controlled suite twice, then 3 full runs if the gate passes |
| 25 | Finish the record; full suite and replay matrix on the final config; push; PR | — | none |

A failed gate skips the rest of its experiment. A rejected experiment leaves no config change and no code (P13). A kept one is one config commit.

## Step 0: set up (free)

1. **Check that the tooling is on `main`.** Run `git fetch origin` and then:

   ```bash
   git grep -q "def _usable_control" origin/main -- src/deep_research/experiments/live_runs.py && echo TOOLING-MERGED || echo TOOLING-NOT-MERGED
   ```

   This must print `TOOLING-MERGED`.
2. **Make the worktree** from the main checkout. The branch already exists on `origin`, so do not pass `-b`:

   ```bash
   git worktree add .worktrees/perf-latency-experiments perf/latency-experiments
   cd .worktrees/perf-latency-experiments && git pull --ff-only && git merge --ff-only origin/main
   ```

   Every command of Tasks 20–25 runs from this worktree's root. The plan's commands are already written for this machine's Git Bash and its `PY="/c/Users/…/.venv/Scripts/python.exe"`, so use them as written.
3. **Run the full suite once,** with the placeholder keys, as in the plan's Conventions. Expect about `5096 passed, 1 deselected`.
   - The cloud run on Linux showed `9 failed, 5081 passed, 6 skipped`. The 9 and the 6 are Windows-only tests that run here.
   - Any failure is real: stop and report.
   - Record the count. Task 25 Step 2 expects this count, plus Task 22's three tests if X1-B is kept. The plan's text says "Task 19's count", which predates the tooling fixes.
4. **Secrets (OI-7).** The **owner** copies the main checkout's `.env` to the worktree root. No agent reads, prints, creates or commits `.env`. Task 25 asks the owner to delete that copy.
5. **Read the plan's Part 2 sections:** "## Part 2", "## Checkpoints", Tasks 20–25, plus the Global Constraints, Decisions (H1–H10, P1–P14) and Open issues OI-3 to OI-7, OI-13 and OI-14.

## How to carry it out

- **Order.** Do Tasks 20–25 in order, exactly as the plan writes them. Follow the owner's local `CLAUDE.md` for model routing and review. The cloud-only rules in `cloud-session/` do not apply here.
- **Paid commands.** The orchestrating session runs every paid command itself, one at a time, after the checkpoint's approval and the off-peak check.
- **Code changes.** These are Task 22's X1-B code and the keep edits of Tasks 21–24. Each gets its own implementation and a review, with fixes and re-reviews until clean. Each keep edit is applied only after its experiment's verdict says keep.
- **Checkpoints.**
  1. Stop and post the plan's checkpoint message, filled in.
  2. Wait for the owner's own written yes in this session. Another agent's message is never approval.
  3. Copy the approval, with its date and UTC time, into `docs/superpowers/audits/2026-09-30-latency-results.md`.
  4. Without that yes, nothing paid runs for that experiment.
- **Off-peak only.** Run the plan's OFF-PEAK CHECK before every paid harness command. No paid command starts if any minute of the next 50 falls inside 01:00–04:00 or 06:00–10:00 UTC, on any day. The `live_runs` and `stage_replay` runners also refuse by themselves.
- **Verdicts come from the code.** Use `live_runs compare`, `compare-suite` and `stage_replay summarize`, never a judgement by eye. Copy each verdict's output into the record.
- **Commits.**
  - Commit the record after every task. Push to `origin perf/latency-experiments` after every commit.
  - A kept experiment is one config commit. A failed experiment is reverted.
  - Never force-push, never push to `main`, never skip hooks.
- **The PR (Task 25 Step 3).** Push the branch. Open the PR only with the owner's go-ahead; its body is the record's Decisions table and a link to the record. The owner merges it.

## What to watch for (from the tooling's reviews)

- **A crashed or cancelled run** leaves a `run.json` with `"status": "failed"` and an `error`.
  - If the error names a configuration error, such as `ResearchConfigurationError`, the run spent nothing. Delete its directory and rerun the same command.
  - Any other crash is a paid failure. Record it, and ask the owner before running a replacement.
- **A non-completed baseline is not a control (H10).** At Checkpoint A, check each baseline's `session_status`, and later `compare`'s `controls_used`. A question without a completed baseline makes every treatment on it fail. Ask the owner whether to run a replacement baseline, which is another paid run.
- **Time bias from the capture (decide at Checkpoint A).** Baselines run with `--capture`, which writes large JSON files synchronously during the run. The X2 and X3 treatments do not, so criterion 8 (mean end-to-end ratio) is slightly biased in the treatment's favour. Put the choice to the owner: run the treatments with `--capture` too, or record the capture's overhead. Do not decide it yourself.
- **Reading Task 23 (X2) results.** The harness's top-level metadata `thinking_mode` still reads `enabled` for a disabled-target run. `target_model_configuration.thinking_mode` and the fingerprint correctly say `disabled`. Read the target fields.
- **Stage replay (Tasks 21 and 22).** `summarize` accepts as few as 2 control and 1 treatment repetitions, and `run` overwrites an existing arm file. Follow the plan's skip-if-exists loop, and confirm all twelve result paths exist before summarising.
- **Fresh memory per run (P9).** Every paid run writes its own memory under its run directory. No paid run may touch the shared `memory/` directory.
- **Disk.** Captures can reach tens of MB per baseline under `output/`, which is gitignored. Check that the OneDrive sync of the worktree is not choking on it.

## Not part of this plan

These are follow-ups the owner may schedule separately:
- the Part 1 open items in PR #29's description: the robots.txt lock while a robots.txt fetch is failing, one download per requested URL, 404s on Word/Excel links, and robots.txt 408/429;
- the deferred minor findings in both runs' ledgers.

Do not fold any of them into this branch.

## Report back with

- each checkpoint's approval and each paid command's outcome, as recorded in the results file;
- each experiment's verdict output, pasted from the code, and its keep or revert decision with the commit;
- the final full-suite count and the replay matrix's `Suite:` and `Network:` lines (Task 25 Step 2);
- the final `git status -sb` line, showing the branch level with `origin/perf/latency-experiments`;
- every deviation from the plan and every ruling made, with what it costs if wrong;
- a reminder to the owner to delete the worktree's `.env` copy (Task 25).
