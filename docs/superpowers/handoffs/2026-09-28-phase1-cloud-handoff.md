# Handoff: carry out Phase 1 (live progress and step briefs)

**For:** a Claude Code cloud session (Opus 5.5). **From:** the local session that ran the brainstorm, wrote the spec, and ran the plan review. **Date:** 2026-09-28.

## Your job

Carry out the Phase 1 implementation plan task by task, using the superpowers `subagent-driven-development` skill. That means a fresh implementer subagent for each task, followed by that task's review.

Stop when Task 12 (full verification and the final visual review) passes, the whole-branch review is clean, and everything is pushed. Then report back. **Do not start Phase 2 or Phase 3. Do not open or merge a PR.**

## Where everything is

| What | Path |
|---|---|
| Branch | `feat/live-briefs-and-reader-notes`, cut from `origin/main` `196dd3f1`. Check out the latest commit. |
| Spec (approved; its §2 decisions are closed) | `docs/superpowers/specs/2026-09-28-live-briefs-and-reader-notes-design.md` |
| Plan (the thing you carry out) | `docs/superpowers/plans/2026-09-28-live-briefs-phase1-live-progress-and-briefs.md` |
| Approved visual picks (1A, 2C, 3B, phone) | `docs/design/running-stage-picks/`: `theme.css`, `Main.dc.html` (1A), `Briefs.dc.html` column C (2C), `Handoff.dc.html` column B (3B), `PhoneBrief.dc.html`. The two `Hitl*` files are for Phases 2 and 3; ignore them. |
| The original design brief | `docs/design/running-stage-picks/BRIEF.md` |
| Design record the UI already follows | `docs/design/DESIGN.md`, `docs/design/reference/*.png` |

**Path remap.** The plan refers to `.superpowers/pick-sheet/project/…` and `.superpowers/claude-design-handoff/BRIEF.md`. Those folders are git-ignored and do not exist in your checkout, so read those files from `docs/design/running-stage-picks/` instead. Where the picks and the spec §4.3 tables differ, **the spec wins**. In particular, all topics research at the same time, so the checklist uses waiting / reading / done wording.

## Your environment differs from the one the plan was written on

The plan's commands were written and dry-run on Windows. Translate them for Linux as follows:

1. **Python.** The plan sets `PY=".../.venv/Scripts/python.exe"`. Instead:
   - run `python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"` (Python ≥ 3.11, from `pyproject.toml`);
   - use `PY="$PWD/.venv/bin/python"`.

   Everything else in the plan's pytest lines stays the same: `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest …`.
2. **`.env`.** There is no `.env`, and you must never create or read one. Add `--deselect tests/test_config.py::test_the_evidence_verifier_pipeline_config` to full-suite runs and expect one fewer pass. The plan's "Conventions" section describes exactly this worktree case.
3. **Web.** Run `cd web && npm ci && npx playwright install --with-deps chromium` once. Always export `DEEP_RESEARCH_PYTHON="$PY"` for Playwright; it starts the API in replay mode itself.
4. **Test counts** may differ by a few from the plan's "Expected" lines if the platform changes collection. Treat a count mismatch as something to explain, not something to paper over. Any failure is a stop-and-fix.

## Working rules (from the human; follow exactly)

- **Push as you go.** After every commit, `git push` the branch. Never push to `main`, never force-push, never skip hooks.
- **No paid or live model runs.** Use pytest and Playwright against the API in `--mode replay` only. Never run the live CLI, and never call a model provider.
- **Reviews.**
  - After each task, run its spec-compliance and code-quality review as the skill describes.
  - At the end, run one whole-branch review with a fresh Opus reviewer at high effort, using no external or GPT models.
  - The loop is always review → fix → re-review until clean. Never skip a re-review.
- **Quality over speed, but no gold-plating.** Implement exactly what the plan and spec say. If a plan step is wrong, or impossible in this environment, fix it minimally, record why in the task summary and the commit message, and continue. If it would change a spec decision (§2, D1–D17), stop and ask instead.
- **Visual checks are required.** At every capture step (Tasks 7, 9, 10 and 12), open the images at 1252 and 390 px, full height. Compare them with the picks and the spec, as the plan describes.
- **Theme rules** (spec D17):
  - tokens only, with no colour literals;
  - `web/app/globals.css` lines 1–1131 stay verbatim (`web/scripts/check-css-verbatim.mjs`), and new rules go in the app-only section;
  - purple appears only on the single primary control;
  - no nested boxes.

## Known open item

**O1.** Reviewing's outcome "Sent back to fill {k} gaps" can never be seen. The route event resets Reviewing to pending, and pending rows show no outcome. The plan computes the string anyway, and Researching's reopen line ("Going back to research {k} gaps the review found") shows the same fact. Implement as the plan says, and don't add UI to show it.

## Report back with

- the commits, with a one-line summary each;
- the final pytest, Vitest and Playwright counts;
- the final capture set (`web/visual/P1-T12-final/`), with your one-paragraph verdict per capture;
- the whole-branch review verdict;
- every deviation from the plan and its reason.
