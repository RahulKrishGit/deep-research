# Handoff: carry out the latency plan, Part 2 tooling (Tasks 16–19)

**For:** a Claude Code **cloud** session (Opus 5.5). **From:** the cloud session that carried out Part 1. **Date:** 2026-10-01.

Everything in this document and in `cloud-session/` is for the cloud session only. None of it applies to, or is loaded by, the owner's local sessions.

> **Time is of the essence, and accuracy matters just as much.**
> - **Keep moving.** Don't pause between tasks for confirmation. Batch independent reads. Don't re-verify what a passing test already proves.
> - **Never trade correctness for speed.** Every review loop runs to clean and every "Expected" output is checked.
> - **Be honest in reports.** If something fails or was skipped, say so plainly.
> - **Pass this rule on to every agent you dispatch.**

> **Keep the remote branch up to date at all times.** The owner follows progress from `origin`, so local-only work counts as not done.
> - `git push` to `origin perf/latency-experiments` right after **every** commit: by implementers, by fixes after a review, and by you.
> - Before starting each task, before each review and before reporting back, run `git status -sb`. It must show no `ahead` count and no uncommitted work you mean to keep.
> - If a push fails, fix the cause and push again. Never force-push, never push to `main`, never skip hooks. If it still fails, stop and report it.

## Step 0: set up the session (do this first)

1. **Part 1 must be on `main`.** Run `git fetch origin main` and then:

   ```bash
   git grep -q "class ToolGate" origin/main -- src/deep_research/agents/react.py && echo PART1-MERGED || echo PART1-NOT-MERGED
   ```

   This test checks content, not commits, so it also works after a squash merge. If it prints `PART1-NOT-MERGED`, stop and report. Part 2 starts only from a `main` that holds Part 1 (plan, Part 2 "Where").
2. **Create this work's branch from that `main`** (or check it out if it already exists on `origin`):

   ```bash
   git fetch origin && (git checkout perf/latency-experiments 2>/dev/null && git pull --ff-only) || git checkout -b perf/latency-experiments origin/main
   ```

   A cloud session has no worktree. The plan's "worktree root" is the repo root.
3. **Run the setup script.** From the repo root, run `bash cloud-session/setup.sh`.
4. **Check the setup.** All of these must hold, and any `WARNING` the script printed must be fixed before you continue:
   - `ls .claude/agents` shows `cloud-implementer`, `cloud-task-reviewer`, `cloud-branch-reviewer`, `spec-plan-author` and `spec-plan-reviewer`.
   - Those five are listed as dispatchable agent types. If they are not, stop and report the contents of `/root/cloud-setup.log` and `ls ~/.claude/agents .claude/agents`. Never substitute other models or model overrides.
   - `.venv/bin/python -c "import deep_research"` succeeds.
5. **Read `.claude/CLAUDE.md`** (the routing table and working rules) and follow it for the whole session.
6. **If the superpowers skills are missing from your skill list,** read them from `.claude/skills/<name>/SKILL.md`. Start with `using-superpowers`, then `subagent-driven-development`.
7. **Is the notes branch merged?** Check whether `feat/notes-progress-report-stop` is merged into `main`. If it is, the plan's anchors in `graph/nodes.py`, `agents/evidence_verifier.py` and the pin file may have moved. Re-anchor on the merged text, as the plan's Compatibility section says, and record each re-anchor.

## Your job

Carry out **Part 2's tooling, Tasks 16–19**, task by task, with the superpowers `subagent-driven-development` skill:
- **Pre-flight scan first.** Run the conflict scan of Tasks 16–19 against the code on `main` before dispatching Task 16. Use `spec-plan-reviewer`, and have it write a table in the ledger. In Part 1, the scan found a plan defect the Windows dry run could not see: Task 12 would have duplicated a test block. Rule on every finding before Task 16 starts.
- **One implementer per task.** Use a fresh `cloud-implementer` for each task.
- **One review per task.** Run a `cloud-task-reviewer` review after each task, then fix and re-review until clean.
- **One whole-branch review at the end.** Run a single `cloud-branch-reviewer` review of the whole branch against the plan and the audit, then one fix wave and one re-review.

Stop when:
- Task 19 Steps 1–7 pass;
- the whole-branch review is clean;
- everything is pushed.

At Task 19 Step 8 ("Hand the tooling over"), **push only; do not open the PR.** Put the PR title and body that the step describes into your report.

**Do not start Task 20 or anything after it.** Tasks 20–25 are paid live runs. They need the owner's secrets and the owner's own written yes at each checkpoint, and they run only on the owner's machine, never in a cloud session. Nothing in Tasks 16–19 may call a model or a search.

## Where everything is

| What | Path |
|---|---|
| Branch | `perf/latency-experiments`, created from `main` after Part 1 merged |
| Requirements (the audit is the spec) | `docs/superpowers/audits/2026-09-30-latency-audit.md` |
| Plan (Part 2 = Tasks 16–25; this handoff covers 16–19) | `docs/superpowers/plans/2026-09-30-latency.md`, from "## Part 2" onward, plus its Global Constraints, Decisions and Compatibility sections |
| Part 1's handoff and status | `docs/superpowers/handoffs/2026-09-30-latency-cloud-handoff.md` |

## What Part 1 taught (carry these into every dispatch)

- **One rules file for every agent.** Write the session's rules once, in the SDD workspace (for example `session-rules.md`), and point every implementer and reviewer at it. Keep dispatch prompts short: the task brief path, the earlier tasks' interfaces, any rulings, and the report path.
- **Commands on the Linux VM.**
  - **Python:** every plan `PY="/c/Users/…/.venv/Scripts/python.exe"` becomes `PY="$PWD/.venv/bin/python"`, run from the repo root. Keep the `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1` prefixes.
  - **Keys:** keep the placeholder keys exactly where the plan sets them.
  - **`.env`:** there is none. Never create, read or print one.
- **Platform baseline.** On this VM the full suite always shows **9 failed and 6 skipped** that are Windows-only:
  - the 9 failures are `tests/test_evaluation/test_config.py -k windows` (`_extended_windows_path`);
  - the 6 skips are Windows long-path tests in `tests/test_evaluation/test_runner*.py`.

  Record them at the start of the session, confirm they also fail on the untouched base, and treat them as known. Any other failure, or any change to that set, is real. Read every Expected `N passed` as `N-15 passed, 9 failed, 6 skipped`.
- **Proxy.** The VM exports `HTTPS_PROXY`, so `shared_connection_pool()` returns `None` unless a test monkeypatches `urllib.request.getproxies`. Never drop such monkeypatches, and never unset the proxy variables to make something pass.
- **No network in tests, ever.** That includes the ONNX embedding model.
  - In Part 1, one fix agent ran a test that downloaded the 79.3 MB model into `/root/.cache/chroma`. Do not repeat that.
  - When proving a guard test, break the code and show that the guard fails without a download.
  - If `/root/.cache/chroma` exists at the start of the session, report it: an unguarded test would then pass from that cache.
- **Pins.**
  - **Fingerprint pins.** Re-pin with `tests/pin_fingerprint.py` (the B-pin rule), which reads the value in the file and never a typed literal. At Step 0, record the five current values as this session's baselines. Task 16 moves the `evidence_verifier` pin.
  - **Replay pins.** Task 16 must leave the replay-digest guard (`tests/test_e2e_evaluation/test_request_digests.py`) and the reader-notes and planner-packet pins unchanged: the capture writes nothing unless bound. If any of them moves, stop and report.
  - **Re-pinning after a merge.** A move in the `full` field alone is not proof enough. Pair the requests from before and after, and accept only acquisition-state lines.
- **Copy plan code byte-exactly,** including its lint quirks, because pins hash the agent modules. Record plan-mandated lint as deferred minors rather than fixing it.
- **Clock-dependent code.** Task 17's runners refuse to start near DeepSeek's peak hours. Their tests must not depend on the real wall clock. If one fails only at certain times of day, it is a defect to fix: inject the clock, and never wait it out.
- **Unverified Expected lines.** Task 19's last two command steps were never run in planning (`[not verified in planning]`). Treat their outcome as unknown and report it plainly.
- **Commit trailer.** Use the attribution lines your own session's system reminder gives, and give every implementer that exact text in the rules file so the whole branch is consistent.
- **Delegate concurrently where safe.** Task reviews take about a minute. Implementers must still run strictly one at a time, because they edit the same files.

## Working rules (from the owner; also in `.claude/CLAUDE.md`)

- **Push as you go.** After every commit, `git push`. Never push to `main`, never force-push, never skip hooks, and never open or merge a PR.
- **No paid or live model runs, and no live search.** Tasks 16–19 need none.
- **Review loop.** Review, fix, then re-review until clean. Use no external or GPT models.
- **Stick to the plan.** Implement exactly what it says.
  - If a step is wrong or impossible here, fix it minimally, record why in the task summary and the commit message, and continue.
  - If the fix would change an approved decision (H1–H9, P1–P14, or the pre-registered criteria constants of Task 17, P10), stop and ask.
- **No UI change.** Nothing under `web/` changes, and no event name or order changes.

## Report back with

- the final `git status -sb` line, showing the branch level with `origin/perf/latency-experiments`;
- the commits, with a one-line summary each;
- the final full-suite count, explained against the platform baseline, and the outcome of Task 19's two unverified steps;
- the agent pins, from this session's Step 0 baseline to the final values;
- confirmation that the replay-digest guard did not move;
- the whole-branch review's verdict;
- the tooling PR's title and body from Task 19 Step 8, ready to paste;
- every deviation from the plan, with its reason, and every ruling you made, with what it costs if wrong.
