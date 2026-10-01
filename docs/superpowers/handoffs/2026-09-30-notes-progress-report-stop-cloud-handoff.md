# Handoff: carry out Phases D, A, C and B (Stop, reader notes, the report, live progress)

**For:** a Claude Code **cloud** session (Opus 5.5). **From:** the local session that ran the brainstorm, the spec, the four plans and their Fable reviews. **Date:** 2026-09-30.

Everything in this document and in `cloud-session/` is for the cloud session only. None of it applies to, or is loaded by, the owner's local sessions.

> **Time is of the essence, and accurate results matter just as much.**
> - **Keep moving.** Don't pause between tasks for confirmation. Batch independent reads. Don't re-verify what a passing test already proves.
> - **Never trade correctness for speed.** Every review loop runs to clean, every "Expected" output is checked, and every capture is looked at.
> - **Be honest in reports.** If something fails or was skipped, say so plainly.

> **Keep the remote branch up to date at all times.** The owner follows progress from `origin`, so local-only work counts as not done.
> - `git push` to `origin feat/notes-progress-report-stop` right after **every** commit: by implementers, by fixes after a review, and by you.
> - Before starting each task, before each review, at the end of each phase and before reporting back, run `git status -sb`. It must show no `ahead` count and no uncommitted work you mean to keep. If it does, commit (if needed) and push first.
> - If a push fails, fix the cause and push again. Never force-push, never push to `main`, never skip hooks. If it still fails, stop and report it.

A second cloud session is carrying out the latency plan on the branch `perf/latency` at the same time. **Never touch that branch.** Every plan here was written and reviewed to apply cleanly whichever branch merges first.

## Step 0: set up the session (do this first)

1. **Check out this work's branch:**

   ```bash
   git fetch origin feat/notes-progress-report-stop && git checkout feat/notes-progress-report-stop && git pull --ff-only
   ```

   The environment's Setup script may have checked out an older branch; that only installed the agents and dependencies, which are identical on every branch.
2. From the repo root, run `bash cloud-session/setup.sh`. It is a no-op unless `CLAUDE_CODE_REMOTE=true`. In the cloud it:
   - copies the superpowers skills, the five agent definitions and the cloud routing rules into `.claude/`, and hides them from git;
   - creates `.venv` (Python ≥ 3.11) with the dev extras;
   - runs `npm ci` in `web/`;
   - installs Playwright's chromium.
3. Check the result:
   - `ls .claude/agents` shows `cloud-implementer`, `cloud-task-reviewer`, `cloud-branch-reviewer`, `spec-plan-author` and `spec-plan-reviewer`;
   - `.venv/bin/python -c "import deep_research"` succeeds;
   - `web/node_modules` exists.

   Fix any `WARNING` the script printed before continuing.
4. Read `.claude/CLAUDE.md` (the routing table and working rules) and follow it for the whole session.
5. **The agent types must be dispatchable.** A cloud session loads agent types only at launch, so they come from the environment's Setup script (`cloud-session/environment-setup-script.sh`). Confirm that the five agents above are listed as agent types. If they are not, stop and report the full contents of `/root/cloud-setup.log` (or that it does not exist) and `ls ~/.claude/agents .claude/agents`. Never substitute other models or model overrides.
6. If the superpowers skills do not appear in your skill list, read and follow them directly from `.claude/skills/<name>/SKILL.md`. Start with `using-superpowers`, then `subagent-driven-development`.
7. **The Playwright browser** should install from `cdn.playwright.dev`. If the download is blocked, you may point Playwright at the machine's pre-installed Chromium with `PLAYWRIGHT_BROWSERS_PATH` and a symlink folder; list it as a deviation. If a motion-timing or visual assertion then fails and could plausibly be caused by the browser version, stop and report instead of adjusting the test.

## Your job

Carry out the four phase plans **in this order: D → A → C → B**, each task by task, using the superpowers `subagent-driven-development` skill:
- a fresh `cloud-implementer` for each task;
- a `cloud-task-reviewer` review after each task, with fix → re-review until clean;
- a `cloud-branch-reviewer` review **at the end of each phase**, scoped to that phase's commits against the spec and that phase's plan, with fix → re-review until clean, before starting the next phase;
- one final `cloud-branch-reviewer` whole-branch review after Phase B, against the spec and all four plans, again with fix → re-review until clean.

Each later phase's plan anchors on the exact text the earlier phases leave, and its Task 1 checks that. If a Task 1 pre-flight or anchor check reports a problem, stop and report it; do not hand-patch anchors.

Stop when:
- Phase B's final verification task passes;
- the final whole-branch review is clean;
- everything is pushed.

Then report back. **Do not open or merge a PR. Do not touch `perf/latency`.**

## Where everything is

| What | Path |
|---|---|
| Branch | `feat/notes-progress-report-stop`. Check out its latest commit. |
| Spec (approved; its §2 decisions D1–D40 are closed) | `docs/superpowers/specs/2026-09-30-notes-progress-report-stop-design.md` |
| Plan 1 — Phase D, Stop (10 tasks) | `docs/superpowers/plans/2026-09-30-phase-d-stop.md` |
| Plan 2 — Phase A, reader notes (11 tasks) | `docs/superpowers/plans/2026-09-30-phase-a-notes.md` |
| Plan 3 — Phase C, the report (13 tasks) | `docs/superpowers/plans/2026-09-30-phase-c-report.md` |
| Plan 4 — Phase B, live progress (15 tasks) | `docs/superpowers/plans/2026-09-30-phase-b-progress.md` |
| Approved visual picks | `docs/design/progress-picks/` (see its `README.md` for which option on each artboard was picked) |
| Latte Key-figures fixture, generated on the owner's machine | `docs/superpowers/handoffs/assets/latte-key-figures.json` |
| Latency audit (context only; the other branch implements it) | `docs/superpowers/audits/2026-09-30-latency-audit.md` |
| Design record the UI already follows | `docs/design/DESIGN.md` |

**Path remaps.** `.superpowers/` is git-ignored, so most of it does not exist in your checkout:
- **`.superpowers/progress-canvas/project/…`** → read from **`docs/design/progress-picks/`** (same file names). Where an artboard and the spec differ, **the spec wins** (for example, Reviewing shows five criteria, not seven).
- **`.superpowers/reviews/…`** files cited in the plans' review-resolution sections are the review history; the plans already contain every resolution. Ignore them.
- **`.superpowers/sdd/2026-09-30-phase-c/…`** helper scripts are *created* by Phase C's Task 1 and Task 6; create them as the plan says (the folder is git-ignored, so they stay uncommitted).
- **Phase C, Task 6, the latte fixture.** Its source, `output/report-a02a75fd75d44d8481f34953a4ff52e1-0-quality.json`, exists only on the owner's machine, so do **not** run `make_latte_fixture.py`. Instead run:

  ```bash
  cp docs/superpowers/handoffs/assets/latte-key-figures.json tests/fixtures/latte-key-figures.json
  sha256sum tests/fixtures/latte-key-figures.json | cut -c1-16
  ```

  Expected: `ec72d82581618e7f`, the exact value the plan's generator step expects (the owner's machine ran the plan's own generator to produce this file). Then continue with Task 6 Step 2. Record this as a deviation in the task summary and the commit message.

## Commands on this Linux VM

The plans' commands were dry-run on Windows. Translate them as follows:
- **Python:** `PY="$PWD/.venv/bin/python"`; replace every `.venv\Scripts\python.exe` / `.venv/Scripts/python.exe` with `"$PY"`, and keep the plans' `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1` prefixes where given. PowerShell blocks become their bash equivalents; backslash paths become forward slashes.
- **`.env`:** there is no `.env`. Never create, read or print one. Every full-suite run keeps the plans' `--deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config`.
- **Playwright:** always `export DEEP_RESEARCH_PYTHON="$PY"`. Playwright starts the API in replay mode itself.
- **Test counts** may differ by a few from the plans' "Expected" lines (platform-only tests). Explain any count mismatch, and treat any failure as a stop-and-fix.
- **Lines marked `[not run in planning]`** (Playwright runs and captures) were never executed during planning. Run them; if one fails, investigate and fix the code or the plan step minimally, never loosen the assertion, and record what you did.

## Working rules (from the owner; also in `.claude/CLAUDE.md`)

- **Push as you go.** After every commit, `git push`. Never push to `main`, never force-push, never skip hooks.
- **No paid or live model runs, and no live search.** Use pytest and Playwright against the API in `--mode replay` only. Phase D switches web search to Tavily's async client; its tests use fakes and mocked transports only.
- **Review loop:** review → fix → re-review until clean. Use no external or GPT models.
- **Stick to the plan.** Implement exactly what the plans and spec say. If a plan step is wrong or impossible here, fix it minimally, record why in the task summary and the commit message, and continue. If it would change a spec decision (§2, D1–D40), stop and ask.
- **Visual checks are required.** At every capture step, open the 1252 and 390 px images (and Phase C's 1920 px report capture) full height and compare them with `docs/design/progress-picks/` and the spec.
- **Theme rules:** tokens only; `web/app/globals.css`'s verbatim block stays verbatim (`npm run check:css`); purple appears only on the single primary control; colour means status; reduced motion turns movement into fades.

## Known open items (already decided; implement as the plans say)

- **D, 390 px top bar.** At phone width the top bar may not fit the status chip, Stop and the replay-mode chip on one row. The plan says to report this from the capture, not to patch it.
- **D, live search transport.** The async Tavily client and its 5xx retry can only be confirmed on the owner's first live run. Note it in your report.
- **D, the stopped step** travels in the existing `SessionView.step`, not a new `stoppedStep` field (accepted by the owner).
- **B, D39.** When a review sends the run back, the Reviewing row holds open for `HANDOFF_HOLD_MS` showing its ✓/✗ and verdict, then hands over, and stays reopenable. The trigger is `run.arc !== null && run.active === ARCS[run.arc].to` (reviewed and kept).
- **C, D40.** A Key-figures row with no named item (no subject, or one starting with a pronoun) is labelled by its source, e.g. "Tripadvisor · Rating, 2024".

## Report back with

- the final `git status -sb` line, showing the branch level with `origin/feat/notes-progress-report-stop` (nothing ahead, nothing uncommitted);
- the commits per phase, with a one-line summary each;
- the final pytest, Vitest and Playwright counts after each phase;
- the final capture set, with a one-paragraph verdict per capture;
- each phase review's verdict and the final whole-branch review's verdict;
- every deviation from the plans and its reason (including the latte-fixture copy and anything about the 390 px top bar).
