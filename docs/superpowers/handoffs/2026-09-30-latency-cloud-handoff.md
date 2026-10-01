# Handoff: carry out the latency plan, Part 1 (steps 0–4)

> **Status (2026-10-01): Part 1 is done. Do not run it again.**
> - **Branch.** Tasks 1–15 are on `perf/latency`, up to `59c4e4a`, all pushed.
> - **Reviews.** Every task review is clean. The whole-branch review asked for three fixes, which are in `59c4e4a`, and its re-review is clean.
> - **Verification on the cloud VM** (Linux):
>   - Full suite: `9 failed, 5028 passed, 6 skipped, 1 deselected`. The 9 failures and the 6 skips are Windows-only tests in `tests/test_evaluation/` that fail and skip the same way on `main`.
>   - Replay matrix: 35/35 rows accepted, with zero network.
>   - `web/` and `agents/prompts.py`: unchanged.
> - **Agent pins** (Task 1 → final):
>
>   | Agent | Task 1 | Final |
>   |---|---|---|
>   | planner | `d1ba46ce147f` | `584e0a466031` |
>   | researcher | `a8c9528f0c20` | `f0378c699608` |
>   | source_evaluator | `24809aa975a3` | `eceba70b743d` |
>   | evidence_verifier | `4a3d56fab932` | `34e275cf57f9` |
>   | report_writer | `6e1aedc2888e` | `879b0e4e2530` |
>
> - **Deviation from the plan.** Task 12's `tests/test_agents/test_react.py` replacement would have re-added the "Tool timings" header and `_SlowEchoTool`, which Task 2 already adds. They were left out, and no test was lost.
> - **Merging.** PR #29 tracks this branch but still carries the audit's title. The owner retitles it to `perf: latency steps 0-4 — instrumentation, scheduling and the ToolGate (D9 amended)` with the prepared body, or opens a new PR, and merges it before any Part 2 work.
> - **Waiting on the owner.** None of these is a regression against `main`:
>   - **robots.txt lock.** Concurrent reads of a host whose robots.txt times out wait one after another (Task 14). The suggested fix keeps P6: waiters share the fetch in flight.
>   - **One download per requested URL, not per page.** A redirect source and its target requested at once both download (Task 12).
>   - **404s on unparsed formats.** A `.doc`, `.docx`, `.xls` or `.xlsx` link that would have returned 404 is now recorded as `unsupported_document_format`, not `not_found` (Task 10; OI-11 does not mention it).
>   - **Transient robots.txt errors.** A 408 or 429 on robots.txt settles "no rules" for the whole run, as P6 says.
> - **Next.** Part 2's tooling (Tasks 16–19) follows `docs/superpowers/handoffs/2026-10-01-latency-part2-tooling-cloud-handoff.md`, only after Part 1 has merged.

**For:** a Claude Code **cloud** session (Opus 5.5). **From:** the local session that ran the latency audit, the plan and its Fable reviews. **Date:** 2026-09-30.

Everything in this document and in `cloud-session/` is for the cloud session only. None of it applies to, or is loaded by, the owner's local sessions.

> **Time is of the essence, and accurate results matter just as much.**
> - **Keep moving.** Don't pause between tasks for confirmation. Batch independent reads. Don't re-verify what a passing test already proves.
> - **Never trade correctness for speed.** Every review loop runs to clean and every "Expected" output is checked.
> - **Be honest in reports.** If something fails or was skipped, say so plainly.

> **Keep the remote branch up to date at all times.** The owner follows progress from `origin`, so local-only work counts as not done.
> - `git push` to `origin perf/latency` right after **every** commit: by implementers, by fixes after a review, and by you.
> - Before starting each task, before each review and before reporting back, run `git status -sb`. It must show no `ahead` count and no uncommitted work you mean to keep. If it does, commit (if needed) and push first.
> - If a push fails, fix the cause and push again. Never force-push, never push to `main`, never skip hooks. If it still fails, stop and report it.

A second cloud session is carrying out the notes / progress / report / Stop plans on the branch `feat/notes-progress-report-stop` at the same time. **Never touch that branch.** This plan was written and reviewed to apply cleanly whichever branch merges first.

## Step 0: set up the session (do this first)

1. **Check out this work's branch:**

   ```bash
   git fetch origin perf/latency && git checkout perf/latency && git pull --ff-only
   ```

   The environment's Setup script may have checked out an older branch; that only installed the agents and dependencies, which are identical on every branch.
2. From the repo root, run `bash cloud-session/setup.sh`. It is a no-op unless `CLAUDE_CODE_REMOTE=true`. In the cloud it copies the superpowers skills, the five agent definitions and the cloud routing rules into `.claude/` (hidden from git), creates `.venv` (Python ≥ 3.11) with the dev extras, runs `npm ci` in `web/` and installs Playwright's chromium.
3. Check the result: `ls .claude/agents` shows `cloud-implementer`, `cloud-task-reviewer`, `cloud-branch-reviewer`, `spec-plan-author` and `spec-plan-reviewer`; `.venv/bin/python -c "import deep_research"` succeeds. Fix any `WARNING` the script printed before continuing.
4. Read `.claude/CLAUDE.md` (the routing table and working rules) and follow it for the whole session.
5. **The agent types must be dispatchable.** A cloud session loads agent types only at launch, so they come from the environment's Setup script (`cloud-session/environment-setup-script.sh`). Confirm the five agents above are listed as agent types. If they are not, stop and report the full contents of `/root/cloud-setup.log` (or that it does not exist) and `ls ~/.claude/agents .claude/agents`. Never substitute other models or model overrides.
6. If the superpowers skills do not appear in your skill list, read and follow them directly from `.claude/skills/<name>/SKILL.md`. Start with `using-superpowers`, then `subagent-driven-development`.

## Your job

Carry out **Part 1 of the latency plan: Tasks 1–15**, task by task, using the superpowers `subagent-driven-development` skill:
- a fresh `cloud-implementer` for each task;
- a `cloud-task-reviewer` review after each task, with fix → re-review until clean;
- one `cloud-branch-reviewer` whole-branch review at the end of Part 1, against the audit, the plan and its accuracy guard (no model-visible change beyond what each task proves), with fix → re-review until clean.

Stop when:
- Task 15 Steps 1–5 pass;
- the whole-branch review is clean;
- everything is pushed.

At Task 15 Step 6 ("Hand the branch over"), **push only; do not open the PR.** Instead, put the PR title and body the step describes into your report, so the owner can open it.

**Do not start Part 2 (Tasks 16–25).** By the plan's own design, Part 2 starts on a new branch only after Part 1 has merged into `main`, and its experiments (Tasks 20–25) are paid live runs that need the owner's secrets and a go-ahead at each checkpoint. Neither is allowed in this session.

## Where everything is

| What | Path |
|---|---|
| Branch | `perf/latency`. Check out its latest commit. |
| Requirements (the audit is the spec) | `docs/superpowers/audits/2026-09-30-latency-audit.md` |
| Plan (the thing you carry out; Part 1 = Tasks 1–15) | `docs/superpowers/plans/2026-09-30-latency.md` |
| The other branch's spec (context for the plan's "Compatibility" section only) | on `origin/feat/notes-progress-report-stop`: `docs/superpowers/specs/2026-09-30-notes-progress-report-stop-design.md` (read with `git show`, never check it out) |

## Commands on this Linux VM

The plan's commands were dry-run on Windows. Translate them as follows:
- **Python:** every `PY="/c/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"` becomes `PY="$PWD/.venv/bin/python"`, run from the repo root (the plan's "worktree root" is your repo root). Keep the plan's `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1` prefixes. Any other `C:\…` or `/c/Users/…` path maps to the same path relative to the repo root.
- **`.env`:** there is no `.env`. Never create, read or print one. Where the plan sets `DEEPSEEK_API_KEY=placeholder` (and the other placeholder keys) for a test run, keep exactly that; never use a real key.
- **Test counts** may differ by a few from the plan's "Expected" lines (platform-only tests). Explain any count mismatch, and treat any failure as a stop-and-fix.
- **Pins.** The plan's B-pin rule applies: each fingerprint re-pin starts from the baseline its Task 1 records, never a typed value. A move in the replay-digest guard is acceptable only as each task's own proof describes; any other move is a stop-and-report.

## Working rules (from the owner; also in `.claude/CLAUDE.md`)

- **Push as you go.** After every commit, `git push`. Never push to `main`, never force-push, never skip hooks.
- **No paid or live model runs, and no live search.** Part 1 needs none: pytest and the replay matrix with the network denied.
- **Review loop:** review → fix → re-review until clean. Use no external or GPT models.
- **Stick to the plan.** Implement exactly what the plan says. If a plan step is wrong or impossible here, fix it minimally, record why in the task summary and the commit message, and continue. If it would change an approved decision (the plan's H1–H9, or the D9 amendment), stop and ask.
- **No UI change.** Part 1 must not change any event name or order the web app depends on, nor anything under `web/` (Task 15 Step 3 checks this).

## Approved decisions to keep in mind

- **D9 amended (owner-approved).** Task 12 replaces the run-wide tool lock with the `ToolGate`: a lock per URL plus a run-wide commit lock, one shared HTTP client, and robots.txt fetched once per host per run. It touches only the lock's own lines in `agents/researcher.py`; never the `_research_one(index, task, tool_lock)` call.
- **Not in scope:** the audit's fourth tool-lock part (several tool calls from one turn at once), per-topic pipelining, research on a draft plan, and lower writer or reviewer effort.

## Report back with

- the final `git status -sb` line, showing the branch level with `origin/perf/latency` (nothing ahead, nothing uncommitted);
- the commits, with a one-line summary each;
- the final full-suite count and Task 15's replay-matrix lines (`Suite: …`, `Network: …`);
- the five agent pins: Task 1 baseline → final;
- every replay-digest row that moved, and the task whose proof covers it;
- the whole-branch review's verdict;
- the PR title and body from Task 15 Step 6, ready to paste;
- every deviation from the plan and its reason.
