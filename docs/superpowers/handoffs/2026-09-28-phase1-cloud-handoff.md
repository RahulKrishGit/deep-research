# Handoff: carry out Phase 1 (live progress and step briefs)

**For:** a Claude Code **cloud** session (Opus 5.5). **From:** the local session that ran the brainstorm, wrote the spec, and ran the plan review. **Date:** 2026-09-28.

Everything in this document and in `cloud-session/` is for the cloud session only. None of it applies to, or is loaded by, the owner's local sessions.

> **Time is of the essence, and accurate results matter just as much.**
> - **Keep moving.** Don't pause between tasks for confirmation. Batch independent reads. Don't re-verify what a passing test already proves.
> - **Never trade correctness for speed.** Every review loop runs to clean, every "Expected" output is checked, and every capture is looked at.
> - **Be honest in reports.** If something fails or was skipped, say so plainly.

## Step 0: set up the session (do this first)

1. From the repo root, run `bash cloud-session/setup.sh`. It is a no-op unless `CLAUDE_CODE_REMOTE=true`. In the cloud it:
   - copies the superpowers skills, five agent definitions and the cloud routing rules into `.claude/`, and hides them from git;
   - creates `.venv` (Python ≥ 3.11) with the dev extras;
   - runs `npm ci` in `web/`;
   - installs Playwright's chromium.
2. Check the result:
   - `ls .claude/agents` shows `cloud-implementer`, `cloud-task-reviewer`, `cloud-branch-reviewer`, `spec-plan-author` and `spec-plan-reviewer`;
   - `.venv/bin/python -c "import deep_research"` succeeds;
   - `web/node_modules` exists.

   Fix any `WARNING` the script printed before continuing.
3. Read `.claude/CLAUDE.md` (the routing table and working rules) and follow it for the whole session.
4. If the superpowers skills do not appear in your skill list, because they were added after launch, read and follow them directly from `.claude/skills/<name>/SKILL.md`. Start with `using-superpowers`, then `subagent-driven-development`. If the agent types are not dispatchable, say so and stop. Never substitute other models.

## Your job

Carry out the Phase 1 implementation plan task by task, using the superpowers `subagent-driven-development` skill:
- a fresh `cloud-implementer` for each task;
- a `cloud-task-reviewer` review after each task, with fix → re-review until clean;
- one `cloud-branch-reviewer` whole-branch review at the end, again with fix → re-review until clean.

Stop when:
- Task 12 (full verification and the final visual review) passes;
- the whole-branch review is clean;
- everything is pushed.

Then report back. **Do not start Phase 2 or Phase 3. Do not open or merge a PR.**

## Where everything is

| What | Path |
|---|---|
| Branch | `feat/live-briefs-and-reader-notes`. Check out its latest commit. |
| Spec (approved; its §2 decisions are closed) | `docs/superpowers/specs/2026-09-28-live-briefs-and-reader-notes-design.md` |
| Plan (the thing you carry out) | `docs/superpowers/plans/2026-09-28-live-briefs-phase1-live-progress-and-briefs.md` |
| Approved visual picks (1A, 2C, 3B, phone) | `docs/design/running-stage-picks/`: `theme.css`, `Main.dc.html` (1A), `Briefs.dc.html` column C (2C), `Handoff.dc.html` column B (3B), `PhoneBrief.dc.html`. The two `Hitl*` files are for Phases 2 and 3; ignore them. |
| The original design brief | `docs/design/running-stage-picks/BRIEF.md` |
| Design record the UI already follows | `docs/design/DESIGN.md`, `docs/design/reference/*.png` |

**Path remap.** If the plan still mentions `.superpowers/pick-sheet/project/…` or `.superpowers/claude-design-handoff/BRIEF.md`, read those files from `docs/design/running-stage-picks/` instead, because `.superpowers/` does not exist in your checkout. Where the picks and the spec §4.3 tables differ, **the spec wins**. In particular, all topics research at the same time, so the checklist uses waiting / reading / done wording.

## Commands on this Linux VM

The plan's commands were dry-run on Windows. Translate them for the VM as follows:
- **Python:** `PY="$PWD/.venv/bin/python"`, then the plan's `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest …`.
- **`.env`:** there is no `.env`. Never create, read or print one. Add `--deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config` to full-suite runs and expect one fewer pass. The plan's "Conventions" section describes this case.
- **Playwright:** always `export DEEP_RESEARCH_PYTHON="$PY"`. Playwright starts the API in replay mode itself.
- **Test counts** may differ by a few from the plan's "Expected" lines. Explain any count mismatch, and treat any failure as a stop-and-fix.

## Working rules (from the owner; also in `.claude/CLAUDE.md`)

- **Push as you go.** After every commit, `git push`. Never push to `main`, never force-push, never skip hooks.
- **No paid or live model runs.** Use pytest and Playwright against the API in `--mode replay` only.
- **Review loop:** review → fix → re-review until clean. Use no external or GPT models.
- **Stick to the plan.** Implement exactly what the plan and spec say. If a plan step is wrong or impossible here, fix it minimally, record why in the task summary and the commit message, and continue. If it would change a spec decision (§2), stop and ask.
- **Visual checks are required.** At every capture step (Tasks 7, 9, 10 and 12), open the 1252 and 390 px images full height and compare them with the picks and the spec.
- **Theme rules** (spec D17):
  - tokens only;
  - `web/app/globals.css` lines 1–1131 stay verbatim (`web/scripts/check-css-verbatim.mjs`);
  - purple appears only on the single primary control;
  - no nested boxes.

## Known open item

**O1.** Reviewing's outcome "Sent back to fill {k} gaps" can never be seen. The route event resets Reviewing to pending, and pending rows show no outcome. Researching's reopen line shows the same fact. Implement as the plan says, and add no UI for it.

## Report back with

- the commits, with a one-line summary each;
- the final pytest, Vitest and Playwright counts;
- the final capture set (`web/visual/P1-T12-final/`), with a one-paragraph verdict per capture;
- the whole-branch review verdict;
- every deviation from the plan and its reason.
