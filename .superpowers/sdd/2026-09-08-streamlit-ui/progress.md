# SDD ledger — plan: docs/superpowers/plans/2026-09-08-streamlit-ui.md

Setup: worktree created from origin/main at 3de6839; plan-owned workspace initialized with native PowerShell because WSL is unavailable.
Approved visual handoff available: YES (`docs/superpowers/plans/deep-research-streamlit-ui-design-handoff.docx`).
Routing override received from human: use GPT-5.6 Luna High for Tasks 1-10 implementation/fix waves and GPT-5.6 Luna Max for each task-scoped review/re-review. Whole-branch review remains halted before dispatch.

Todo ledger:
- [ ] Task 1: presentation contracts
- [ ] Task 2: progress and quality projection
- [ ] Task 3: safe local session history
- [ ] Task 4: non-blocking research controller
- [ ] Task 5: Editorial Research Canvas shell
- [ ] Task 6: New Research screen
- [x] Task 7: Running screen and fragment refresh
- [ ] Task 8: Completed report-first screen
- [ ] Task 9: searchable Session History screen
- [ ] Task 10: offline visual acceptance and full verification

Task 1: in progress
Task 1: fix round 1 in progress — reviewer reported unredacted ResearchError.details in history compaction, possible error aliasing, and incomplete string-stripping coverage.
Task 1: fix round 1/5 — 2 addressed, 1 open (history sanitizer bypass for deque/generator inputs); commits 7e2add5..0e49485.
Task 1: fix round 2 in progress — make error-detail sanitization cover every Pydantic-accepted iterable input, including deque/generator, with regression tests.
Task 1: fix round 2/5 — 1 addressed, 0 open; commits 0e49485..97835c3.
Task 1: complete (commits 3de6839..97835c3, review clean after 2 fix rounds).
- [x] Task 1: presentation contracts
Task 2: in progress
Task 2: complete (commit 97835c3..c5bb93a, review clean).
- [x] Task 2: progress and quality projection
Task 3: in progress
Task 3: fix round 1 in progress — reviewer found storage-boundary detail leakage, mixed-timezone sorting failure, and symlink/junction escape risk.
Task 3: fix round 1/5 — 3 Important findings addressed; 1 Minor evidence gap deferred (file-symlink tests skipped/no concurrency test); 1 new Important finding open (embedded-NUL report_path raises ValueError); commits 48b69b6..e699a77.
Task 3: fix round 2 in progress — fail closed on model-valid embedded-NUL report paths.
Task 3: fix round 2/5 — 1 addressed, 0 open; 1 Minor test-evidence gap deferred (file symlink tests skipped with Windows WinError 1314; no concurrency test); commits e699a77..b56f29d.
Task 3: complete (commits c5bb93a..b56f29d, review clean, 1 deferred Minor).
- [x] Task 3: safe local session history
Task 4: in progress
Task 4: fix round 1 in progress — reviewer found terminal history-write failures can strand sessions as running, and late configuration error reasons are not allowlisted; worker-cardinality test gap is Minor.
Task 4: fix round 1/5 — 2 addressed, 0 open; 1 Minor worker-cardinality test gap deferred; commits 3d50ef8..f32cd1c.
Task 4: complete (commits b56f29d..f32cd1c, review clean, 1 deferred Minor).
- [x] Task 4: non-blocking research controller
Task 5: in progress
Task 5: fix round 1 in progress — reviewer found ineffective cross-element selected-row tint, unreliable bottom-pinned history action, and brittle raw HTML wrapper styling.
Task 5: fix round 1/5 — 3 Important findings addressed; 2 Minor coverage concerns addressed; commits 709b558..ea06921.
Task 5: complete (commits f32cd1c..ea06921, Luna Max re-review clean, no open findings).
- [x] Task 5: Editorial Research Canvas shell
Task 6: in progress
Task 6: fix round 1/5 — 2 findings addressed; commits aaf90f3..21bb24a.
Task 6: complete (commits ea06921..21bb24a, Luna Max re-review clean, no open findings).
- [x] Task 6: New Research screen
Task 7: fix round 1/5 — 4 findings addressed; commits 118cd14..b33c4a2.
Task 7: complete (commits 21bb24a..b33c4a2, Luna Max re-review clean, no open findings).
- [x] Task 7: Running screen and fragment refresh
Task 8: in progress
Task 8: fix round 1/5 — 2 findings addressed; commits 9a150fe..20b845c.
Task 8: complete (commits b33c4a2..20b845c, Luna Max re-review clean, no open findings).
- [x] Task 8: Completed report-first screen
Task 9: in progress
Task 9: fix round 1/5 — 3 findings addressed; commits 746c1c1..4a3a4a4.
Task 9: complete (commits 20b845c..4a3a4a4, Luna Max re-review clean, no open findings).
- [x] Task 9: searchable Session History screen
Task 10: in progress
Task 10: complete (offline harness, AppTests, README, UI/repository/Ruff verification complete; screenshot visual QA limited by missing browser/renderer; no live smoke; no whole-branch review)
- [x] Task 10: offline visual acceptance and full verification

All implementation tasks complete: Tasks 1-10
Implementation model: GPT-5.6 Luna High per task
Task reviewer: GPT-5.6 Luna Max per task
Approved visual handoff available: YES
Offline UI tests: PASS — 132 passed, 2 skipped
Repository tests: PASS — 2013 passed, 2 skipped, 1 deselected, 2 warnings
Ruff: PASS
Offline manual visual review: PASS structurally/content-compared against Figures 1, 2, 3A, 3B, 3C, and 4; browser-rendered screenshot QA unavailable because no browser automation or LibreOffice/soffice is installed
Figures reviewed: 1, 2, 3A, 3B, 3C, 4
Live paid-provider smoke: NOT RUN unless separately authorized
Whole-branch review: HALTED by explicit human instruction

IMPLEMENTATION: COMPLETE
TASK-SCOPED REVIEWS: COMPLETE
VISUAL HANDOFF REVIEW: COMPLETE (structural/content comparison; renderer limitation recorded)
FULL OFFLINE VERIFICATION: COMPLETE
WHOLE-BRANCH REVIEW: HALTED / NOT DISPATCHED
FINISHING-A-DEVELOPMENT-BRANCH: NOT INVOKED
MERGE/PUSH/PUBLISH: NOT PERFORMED

Task 11: in progress
Ruling: Task 11 uses task-11-brief.md as the sole requirements source, per the human instruction; the historical plan and prior task ledger remain context only. Cost if wrong: a task-specific requirement omitted from the brief would need a follow-up remediation.

Task 12: final re-review remediation in progress — focused pass covers the failed pending-start lifecycle regression for configuration, history, and unexpected start failures; the approved handoff path correction; and this append-only ledger update. Whole-branch review remains halted.
Task 12: final re-review remediation complete — the three scoped follow-ups are implemented and verified: failed pending starts now persist safe errors, clear in-flight state, and trigger one full rerun; the plan points to the committed handoff; and this ledger retains an append-only completion record. Focused UI regressions: 3 passed; UI suite: 187 passed, 2 skipped; Ruff: pass. Whole-branch review remains halted.
