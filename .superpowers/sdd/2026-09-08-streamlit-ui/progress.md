# SDD ledger — plan: docs/superpowers/plans/2026-09-08-streamlit-ui.md

Setup: worktree created from origin/main at 3de6839; plan-owned workspace initialized with native PowerShell because WSL is unavailable.
Approved visual handoff available: YES (`docs/superpowers/plans/deep-research-streamlit-ui-design-handoff.docx`).
Routing override received from human: use GPT-5.6 Luna High for Tasks 1-10 implementation/fix waves and GPT-5.6 Luna Max for each task-scoped review/re-review. Whole-branch review remains halted before dispatch.

Current state summary (2026-09-10): Tasks 1-10 implementation and task-scoped reviews are complete; Tasks 11-12 are retained historical follow-up records. Task 14 is the current branch-wide integration remediation pass for the six Important and three Minor findings supplied by the human reviewer. No new whole-branch review is to be dispatched after this pass; the next branch review remains human-owned.

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

Task 14: branch-wide integration remediation complete — the supplied review findings for max-iteration parity, live health issue projection, refinement-safe quality summaries, authoritative terminal history state, selected-session sidebar promotion, worker-start rollback, history copy/comment cleanup, and the current-state ledger summary are implemented and verified. Whole-branch re-review remains human-owned.

Task 15: in progress — restore persisted token usage and LangSmith trace when a historical session is reconstructed after controller restart; add restarted-controller/AppTest coverage before implementation.
Task 15: complete (commits 42623fe..c5d0049, review clean; Luna Max approved spec/design and code quality).
Task 16: in progress — make refinement claim identity stable end to end; latest normalized claim-text judgments must drive both UI summaries and synthesis/report rendering.
Task 16: fix round 1 in progress — Luna Max found lower-level synthesis can pass duplicate sources to provider input and report a raw source count that disagrees with the effective source appendix; fix scope is `synthesizer.py` plus regression coverage.
Task 16: fix round 1/5 — 2 addressed, 0 open; commits d2cfde7..53bcc53; Luna Max scoped re-review returned ALL FINDINGS ADDRESSED.
Task 16: complete (commits c5d0049..53bcc53, review clean after 1 fix round; Luna Max approved the effective claim/source invariant and lower-level synthesis boundary).
Task 17: in progress — remove the undocumented 500-character question cap, distinguish genuinely empty quality details from compacted history, and update README/plan compatibility documentation.
Task 17: complete (commits 53bcc53..6dd8ba2, review clean; Luna Max approved spec/design and code quality).
Task 18: in progress — fresh full-suite verification exposed the missing agents-package export for latest_scored_sources; add the minimal public-surface export and rerun the verification gate.
Task 18: complete (commits 6dd8ba2..419e6ba, review clean; Luna Max approved the public export remediation).

Task 19: complete — browser verification exposed a Streamlit form-event incompatibility; the final adjudication restored atomic form submission, safe blank validation, stale-guidance removal, and explicit Task 6 plan documentation (commits 857ee01, dc9dab2, 9d1adb6; Luna Max scoped review: REVIEW CLEAN).

Fresh verification (2026-09-10):
Offline UI suite: PASS — 205 passed, 2 skipped.
Repository suite: PASS — 2089 passed, 2 skipped, 1 deselected, 2 warnings in 60.88s.
Ruff: PASS — python -m ruff check .
git diff --check: PASS — only the expected Windows line-ending warning for this ledger.
Browser functional verification: PASS — fresh production app blank submission showed safe validation without a provider run; fresh offline app accepted direct type-to-click valid submission; 963-character question remained visible and wrapped without a frontend cap; clearing input restored the validation state.
Browser visual verification: PASS — agent-browser screenshots of New Research, Running, Completed, and History were compared against the extracted approved DOCX Figures 1–4; hierarchy, editorial width, sidebar, status cues, progress rail, report/quality rail, and history search/filter matched the handoff structure.
DOCX rendered-page verification: LIMITED — LibreOffice/soffice.exe was unavailable in the bundled workspace runtime; approved figure assets were extracted from the DOCX and directly compared with the live browser screenshots.
Live paid-provider smoke: NOT RUN.
Whole-branch review: HALTED by explicit human instruction.
FINISHING-A-DEVELOPMENT-BRANCH: NOT INVOKED.
MERGE/PUSH/PUBLISH: NOT PERFORMED.
