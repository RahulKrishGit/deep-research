# Wave 1 branch remediation report

Date: 2026-09-09
Branch: `codex/streamlit-ui`
Starting edited-worktree HEAD: `e901cf9198bbc4d19b996f44f0ae3f59fa3e6b97`
Scope: Wave 1 only; Waves 2-4 were not touched.

## Remediated findings

- Streamlit compatibility: retained the declared `streamlit>=1.37` contract and routed container rendering through a signature-aware helper that omits the post-1.37 `key` and `gap` keywords on legacy runtimes. The other production APIs used by the UI (`st.columns` gap/alignment, `st.link_button`, `st.fragment`, forms, and existing widgets) are available in the declared minimum. The installed verification runtime was Streamlit 1.63.0.
- Safe bootstrap: invalid/missing/malformed configuration and invalid configured output paths now leave the app renderable, preserve the New Research form, and expose only project-owned guidance. Strict start-time secret validation remains sanitized at the UI boundary.
- Initial history persistence: a failure writing the initial compact history record now removes the unstarted in-memory session and raises an owned `history_unavailable` error with an actionable, sanitized message. The Streamlit start path preserves the draft and never renders the raw exception.

## Changed files

- `src/deep_research/runtime/errors.py`
- `src/deep_research/ui/components.py`
- `src/deep_research/ui/runner.py`
- `tests/test_ui/test_app.py`
- `tests/test_ui/test_runner.py`

## Fresh verification

- `python -m pytest tests/test_ui/test_app.py tests/test_ui/test_runner.py -q` — PASS: **78 passed**, 0 failed, 20.86s.
- `python -m pytest tests/test_ui -q` — PASS: **144 passed, 2 skipped**, 0 failed, 21.03s.
- `python -m pytest -q` — PASS: **2025 passed, 2 skipped, 1 deselected**, 0 failed, 2 warnings, 48.47s. Warnings are dependency deprecations from LangSmith AST handling and Starlette/httpx test-client integration.
- `python -m ruff check .` — PASS: `All checks passed!`
- `git diff --check` — PASS; Git emitted only existing LF/CRLF normalization warnings for the five edited text files.

No live-provider/evaluation job was run or awaited. No push, merge, branch-wide review, or later wave work was performed.

## Wave 2 — Truthful Progress

Date: 2026-09-09
Branch: `codex/streamlit-ui`
Starting edited-worktree HEAD: `759f27b49a25c7925cd7800dd4827817fb60185e`
Wave 2 commit: `35c9a13`
Scope: Wave 2 only; Waves 3-4 were not touched.

### Remediated findings

- Finding 2: macro iteration now accepts `iteration` only from the allowlisted graph lifecycle events (`graph.node.*`, `graph.refinement.started`, `graph.route.decided`, and `graph.session.completed`). Researcher tool-call/ReAct iterations cannot overwrite the outer-loop value. Graph transitions still preserve the current agent and use deterministic agent-specific action text.
- Finding 3: planned subtopic count is carried separately from titled actual rows. Unknown titles do not create rows or placeholder names. Research completion and later graph-agent handoff remove non-completed research rows so skipped/unvisited work does not remain visibly queued. The running UI renders a phase percentage only when the known row indexes cover the planned denominator; partial/unknown data omits it and reports the known/planned distinction.
- Regression coverage includes mixed graph/ReAct iteration events, partial/unknown titles, fewer processed topics, research-phase completion, later-agent transition, no fabricated titles, no unsupported phase percentage, and preservation of five real titled subtopics.

### Changed files

- `src/deep_research/ui/components.py`
- `src/deep_research/ui/models.py`
- `src/deep_research/ui/progress.py`
- `src/deep_research/ui/runner.py`
- `tests/test_ui/fakes.py`
- `tests/test_ui/test_app.py`
- `tests/test_ui/test_progress.py`
- `tests/test_ui/test_runner.py`
- `.superpowers/sdd/2026-09-08-streamlit-ui/branch-remediation-report.md`

### Fresh verification

- `python -m pytest tests/test_ui/test_progress.py tests/test_ui/test_runner.py tests/test_ui/test_app.py -q` — PASS: **110 passed**, 0 failed, 21.50s.
- `python -m pytest tests/test_ui -q` — PASS: **149 passed, 2 skipped**, 0 failed, 22.01s.
- `python -m pytest -q` — PASS: **2030 passed, 2 skipped, 1 deselected**, 0 failed, 2 warnings, 48.66s. Warnings are the existing LangSmith `ast.Str` deprecation and Starlette/httpx test-client integration deprecation.
- `python -m ruff check .` — PASS: `All checks passed!`
- `git diff --check` — PASS; Git emitted only LF-to-CRLF normalization warnings for the eight edited source/test files.

### Concerns / deferred work

- No live-provider/evaluation job or visual renderer comparison was run; Wave 2 is limited to truthful progress projection and offline tests. The handoff-required branch-wide review remains halted, and Waves 3-4 remain deferred.
- The full suite retains the two dependency deprecation warnings listed above. No test or lint blocker remains.

No push, merge, publish, or branch-wide review was performed.

## Wave 3 — Restart-safe history and lifecycle truth

Date: 2026-09-09
Branch: `codex/streamlit-ui`
Starting edited-worktree HEAD: `35c9a13c0d1a257dc09c2f290872ca69022d11be`
Scope: Wave 3 only; Wave 4 error and visual work was not started.

### Product and lifecycle choice

Finding 8 is resolved with the multiple-running-sessions contract already
supported by the controller plan. `LocalResearchController` keeps an ID-keyed
registry of sessions, and `is_session_active(session_id)` is the lifecycle
source of truth for every history row. The selected Streamlit session key does
not determine whether another run is active. A persisted `running` record with
no matching active in-memory session is normalized to `incomplete` after a
restart; genuinely active sessions remain `running`, including when more than
one session is running at once. Terminal statuses are never rewritten as
`running`.

### Remediated findings

- Finding 6: compact history metadata now retains current/last agent, planned
  subtopic count, actual titled subtopics and their statuses, last subtopic,
  research-phase completion, and at most three recent meaningful activities.
  The controller persists this projection after each published event and at
  terminal completion, so a fresh controller can reopen failed, incomplete,
  max-iteration, and completed sessions without rerunning them. History still
  excludes raw events, tool calls, report bodies, provider payloads, prompts,
  full responses, stack traces, and error details.
- Finding 7: the history store and controller now load the complete archive by
  default; the explicit `limit` parameter remains available for callers that
  intentionally want a cap. The UI requests the complete archive, reports the
  true archive total before search/filtering, preserves newest-first ordering,
  and applies case-insensitive question search and status filters across all
  records.
- Historical reopen reconstructs the retained terminal presentation from the
  compact history record and safely reads a report only through the existing
  output-root path check. A report path alone never causes report rendering.

### Changed files

- `src/deep_research/ui/components.py`
- `src/deep_research/ui/history.py`
- `src/deep_research/ui/models.py`
- `src/deep_research/ui/progress.py`
- `src/deep_research/ui/runner.py`
- `tests/test_ui/fakes.py`
- `tests/test_ui/test_app.py`
- `tests/test_ui/test_history.py`
- `tests/test_ui/test_models.py`
- `tests/test_ui/test_progress.py`
- `tests/test_ui/test_runner.py`
- `.superpowers/sdd/2026-09-08-streamlit-ui/branch-remediation-report.md`

### Fresh verification

- `python -m pytest tests/test_ui/test_models.py tests/test_ui/test_history.py tests/test_ui/test_progress.py tests/test_ui/test_runner.py tests/test_ui/test_app.py -q` — PASS: **163 passed, 2 skipped**, 0 failed, 24.51s.
- `python -m pytest tests/test_ui -q` — PASS: **165 passed, 2 skipped**, 0 failed, 24.59s.
- `python -m pytest -q` — PASS: **2046 passed, 2 skipped, 1 deselected**, 0 failed, 2 warnings, 50.90s. Warnings are the existing LangSmith `ast.Str` deprecation and Starlette/httpx test-client integration deprecation.
- `python -m ruff check .` — PASS: `All checks passed!`
- `git diff --check` — PASS; Git emitted only existing LF-to-CRLF normalization warnings for the eleven edited source/test files.

### Concerns / blockers

- No implementation blocker remains for Wave 3. No live-provider/evaluation job,
  visual renderer comparison, push, merge, or branch-wide review was performed.
- The full suite retains the two dependency deprecation warnings listed above.
- Wave 4 error diagnostics and visual remediation remain explicitly deferred.

No push, merge, publish, branch-wide review, or Wave 4 work was performed.

## Wave 4 — Sanitized execution errors and visual-contract remediation

Date: 2026-09-09
Branch: `codex/streamlit-ui`
Starting clean-worktree HEAD: `b9a33d0f85dd102565525ec061489f6f3ecbddaa`
Scope: Wave 4 only; no engine, provider, tracing, live-provider, or branch-wide review changes.

### Remediated findings

- Finding 9: added a typed `UiExecutionErrorPresentation` and a complete
  project-owned mapping for known graph, agent, researcher, source-evaluation,
  fact-check, synthesis, review, tool, document, and UI failure categories.
  The UI selects static category/message/effect/recovery copy by `error_type`
  and `recoverable`; it never renders `ResearchError.message`, arbitrary
  detail values, traces, provider configuration, secrets, or raw payloads.
  Safe diagnostics are one level deeper, collapsed by default, and limited to
  a known stage, bounded counts, allowlisted response status, safe exception
  class names, and enumerated reasons. Limitations remain a separate labeled
  section from execution errors. Running, terminal, completed-with-nonfatal,
  and failed-partial states use the same sanitized presentation contract.
- Finding 10: New Research now renders inside the keyed
  `dr-new-research-column`; the form and report column are bounded to the
  720–820px editorial width contract. Completed quality summaries now expose
  explicit Source credibility rows (High, Moderate, Low, Unrated) and
  Fact-check rows (Verified, Unverified, Contradicted, Insufficient evidence)
  with visible labels plus non-color icons/shapes and semantic colors. The
  report path is rendered with `st.code(..., language=None)` for selection.
  Completed reports use the keyed `dr-report-column`, and divider spacing was
  restored to the editorial rhythm.

### Changed files

- `src/deep_research/ui/components.py`
- `src/deep_research/ui/models.py`
- `src/deep_research/ui/styles.py`
- `tests/test_ui/test_app.py`
- `.superpowers/sdd/2026-09-08-streamlit-ui/branch-remediation-report.md`

### TDD and focused verification

- `python -m pytest tests/test_ui/test_app.py -k "semantic_quality or execution_error or nonfatal or terminal_execution or failed_partial_report" -v` — RED checkpoint before implementation: collection stopped with `ImportError` for the not-yet-defined `execution_error_presentation`, 0 tests executed.
- `python -m pytest tests/test_ui/test_app.py -k "semantic_quality or execution_error or nonfatal or terminal_execution or failed_partial_report or failed_snapshot or failed_partial_snapshot" -v` — PASS: **7 passed**, 69 deselected, 0 failed, 4.69s.
- `python -m pytest tests/test_ui/test_app.py -k "report_quality or completed_without_execution_errors or execution_error or nonfatal_error or terminal_error or failed_partial_report or failed_snapshot or failed_partial_snapshot" -q` — PASS: **8 passed**, 69 deselected, 0 failed, 4.04s. This is the final focused run after the report-column regression assertion was added.
- `python -m pytest tests/test_ui -q` — PASS: **171 passed, 2 skipped**, 0 failed, 27.75s.
- `python -m pytest -q` — PASS: **2052 passed, 2 skipped, 1 deselected**, 0 failed, 2 warnings, 54.82s. Warnings are the existing LangSmith `ast.Str` deprecation and Starlette/httpx test-client integration deprecation.
- `ruff check . --output-format concise` — PASS: `All checks passed!`
- `git diff --check` — PASS; Git emitted only LF-to-CRLF normalization warnings for the four edited source/test files.

### Offline harness and visual comparison evidence

- `streamlit run tests/test_ui/manual_mock_app.py --server.headless true --server.port 8505 --server.address 127.0.0.1` — PASS: Uvicorn started on `127.0.0.1:8505`; the process was stopped cleanly after the health check.
- `$health = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8505/_stcore/health; "health_status=$($health.StatusCode) body=$($health.Content)"; $page = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8505; "app_status=$($page.StatusCode) content_length=$($page.Content.Length)"` — PASS: `health_status=200 body=ok`; `app_status=200 content_length=7459`.
- Offline AppTest coverage of `tests/test_ui/manual_mock_app.py` remains PASS from the UI suite: the six selectors are New, Running, Completed, History, Max iterations, and Failed/partial, and each renders its expected heading. No live provider was invoked.
- Reference figures were inspected from `.superpowers/sdd/2026-09-08-streamlit-ui/evidence/docx-assets/word/media/image1.png` through `image4.png`. Browser executable probes (`chrome`, `msedge`, `firefox`) and `soffice`/`libreoffice` probes returned no command, so no browser screenshot or LibreOffice-rendered DOCX comparison was possible. Conclusions below are direct offline structural/content comparisons, not pixel-perfect claims.

Visual checklist conclusions:

- Figure 1 / New Research — PASS for offline structure: the question-first heading hierarchy, bounded keyed editorial column, bounded form, two-column configuration, ready/start action, and three-step “What Happens Next” structure are present. Screenshot/pixel confirmation is unavailable without a browser or renderer.
- Figure 2 / Running — PASS for offline structure: Wave 1–3 lifecycle and progress behavior remains intact; current agent/subtopic/action hierarchy, macro iteration, phase progress, health cue, recent activity, and secondary details rail remain available. Screenshot/pixel confirmation is unavailable.
- Figure 3A / Completed — PASS for offline structure: report Markdown remains dominant on the base canvas; the report column is keyed/bounded; report path is selectable code; source credibility and fact-check semantics are explicit and visually non-color-dependent; limitations and execution errors are distinct sections. Screenshot/pixel confirmation is unavailable.
- Figure 3B / Max iterations and incomplete — PASS for offline structure: partial report, explicit paused/incomplete status, stopping-point context, quality summaries, and limitations remain distinct; no unsupported completion claim or ETA was introduced. Screenshot/pixel confirmation is unavailable.
- Figure 3C / Failed and failed-partial — PASS for offline structure and sanitization: a retained partial report remains visible, execution errors are separately labeled, effects and recovery hints are actionable, safe diagnostics are collapsed, and raw exception/message/trace/payload/configuration content is not disclosed. Screenshot/pixel confirmation is unavailable.
- Figure 4 / History — PASS for offline structure: the persistent history heading, search/filter controls, newest-first rows, explicit status cues, selected-row treatment, and Open actions remain unchanged by Wave 4. Screenshot/pixel confirmation is unavailable.

### Blockers and scope boundary

- No implementation blocker remains for Wave 4.
- Browser-based screenshot comparison is blocked because no browser executable or browser automation package is available in this environment.
- LibreOffice/`soffice` rendering is unavailable; DOCX visual re-rendering is therefore unverified. Structural/accessibility evidence does not substitute for that visual check.
- No push, merge, publish, branch-wide review, or unrelated evaluation job was performed.

## Task 11 — Residual re-review remediation and rendered acceptance

Date: 2026-09-09
Branch: `codex/streamlit-ui`
Starting clean-worktree HEAD: `26acd1591a4313615f5790031c3ad52f9da05a22`
Scope: the four Important residual findings, the Starting-state design gap,
and the Figure 1–4 rendered acceptance check. No engine, provider, tracing,
live-provider, or branch-wide review changes.

### Remediated findings

- Terminal polling now has a separate `_LIVE_SESSION_KEY`. When the selected
  live snapshot first becomes terminal, the recurring fragment clears only the
  poll target and triggers one full rerun; the selected session remains
  preserved, and the stable terminal route owns the completed screen.
- Selecting any session that `LocalResearchController.is_session_active`
  reports as active makes that session the live-view target. The lifecycle
  registry remains independent, so another running worker is not stopped or
  relabeled when the user opens an older active session.
- The package floor is now `streamlit>=1.49`, matching the keyed/gapped
  `st.container` contract used by the CSS selectors. The installed verification
  runtime was Streamlit 1.63.0.
- Terminal sessions without a non-empty report now use a retained
  progress/stopping-point screen. The completed report layout and source/fact
  quality rail are rendered only when a real report exists; unavailable counts
  are not represented by zero-valued quality summaries.
- Starting now renders the teal `Preparing research plan` cue and disables
  duplicate submission while the start operation is in flight.

### Changed files

- `pyproject.toml`
- `src/deep_research/ui/app.py`
- `src/deep_research/ui/components.py`
- `tests/test_ui/test_app.py`
- `.superpowers/sdd/2026-09-08-streamlit-ui/branch-remediation-report.md`

### Regression coverage

- Terminal fragment transition from Running to terminal, including one-time
  poll-target clearing and selected-session preservation.
- Live viewing of an older session while multiple sessions remain active.
- Keyed container arguments and the Streamlit dependency floor.
- Incomplete/no-report retained progress without source or fact-check rails.
- Visible Starting state and disabled duplicate submission.

### Fresh verification

- `python -m pytest tests/test_ui -q` — PASS: **176 passed, 2 skipped**, 0
  failed, 29.62s.
- `python -m pytest -q` — PASS: **2057 passed, 2 skipped, 1 deselected**, 0
  failed, 2 warnings, 58.05s. Warnings are the existing LangSmith
  `ast.Str` deprecation and Starlette/httpx test-client integration
  deprecation.
- `python -m ruff check .` — PASS: `All checks passed!`
- `git diff --check` — PASS; Git emitted only LF-to-CRLF normalization
  warnings for edited text files.

### Rendered Figure 1–4 acceptance

The four reference images were inspected from
`.superpowers/sdd/2026-09-08-streamlit-ui/evidence/docx-assets/word/media/image1.png`
through `image4.png`. The offline mock was served with Streamlit on
`127.0.0.1:8506` and inspected in the in-app browser at the New, Running,
Completed, and History selector states. Fresh DOM snapshots and screenshots
confirmed the following rendered surfaces:

- Figure 1 / New Research: question-first heading, bounded editorial form,
  configuration controls, ready/start action, and the three-step next-actions
  rail.
- Figure 2 / Running: selected-session tint, active subtopic row, current
  researcher/action hierarchy, progress bar, health cue, recent activity, and
  secondary details rail.
- Figure 3 / Completed: report-dominant reading surface, report path,
  limitations and execution-error sections, plus explicit source-credibility
  and fact-check rails.
- Figure 4 / History: persistent heading, search and status filters,
  newest-first rows, selected-row treatment, statuses, and Open actions.

This is a direct rendered comparison at the browser's default viewport and
the reference captures' visual structure; exact pixel identity is not claimed
because the reference images and live browser chrome use different viewport
dimensions. LibreOffice/`soffice` remains unavailable, so the DOCX itself was
not re-rendered. No live provider was invoked.

### Scope boundary

No merge or branch-wide review was run. The branch is ready for the user's
separate re-review after the commit is pushed.

## Task 12 — Final re-review remediation

Date: 2026-09-10
Branch: `codex/streamlit-ui`
Starting clean-worktree HEAD: `fc78f050e0e6f0dd93e204203cf9cb47bf1d1d6c`
Scope: the remaining Sol High re-review findings. No engine, provider,
tracing, live-provider, or branch-wide review changes.

### Remediated findings

- Sidebar status refresh is now keyed by each visible session ID. Selecting
  an older active session updates that row from its own controller snapshot,
  while other active sessions retain their independent Running status and
  lifecycle.
- Starting is now a two-phase UI flow: submission persists a normalized
  pending request and reruns into a read-only form with the teal preparation
  cue before consuming the request once. Duplicate submission is disabled.
- The completed quality rail now includes a deterministic credibility
  distribution explanation after the High, Moderate, Low, and Unrated counts.
- The execution plan now declares `streamlit>=1.49`, matching the keyed and
  gapped `st.container` contract used by the visual implementation.
- The primary submit control has an explicit 44px minimum height in both
  generic and primary-form CSS rules.

### Design record and changed files

The approved design decision is recorded in
`docs/superpowers/specs/2026-09-10-streamlit-ui-final-remediation-design.md`
(commit `36f11c8`). The implementation changes cover:

- `docs/superpowers/plans/2026-09-08-streamlit-ui.md`
- `src/deep_research/ui/app.py`
- `src/deep_research/ui/components.py`
- `src/deep_research/ui/styles.py`
- `tests/test_ui/test_app.py`
- `tests/test_ui/test_styles.py`
- `.superpowers/sdd/2026-09-08-streamlit-ui/branch-remediation-report.md`

### TDD and fresh verification

- The focused RED run produced the expected failures for the not-yet-
  implemented pending-start, keyed-status, distribution-summary, plan-floor,
  and 44px contracts.
- `python -m pytest tests/test_ui -q` — PASS: **184 passed, 2 skipped**, 0
  failed, 29.61s.
- `python -m pytest -q` — PASS: **2065 passed, 2 skipped, 1 deselected**, 0
  failed, 2 warnings in 57.30s. Warnings are the existing LangSmith
  `ast.Str` deprecation and Starlette/httpx test-client integration
  deprecation.
- `python -m ruff check .` — PASS: `All checks passed!`
- `git diff --check` — PASS; Git emitted only LF-to-CRLF normalization
  warnings for edited text files.

### Rendered verification

The local Streamlit mock was restarted after the final CSS change and checked
in the in-app browser. The primary form submit control measured
`height: 44px` and `min-height: 44px`. The four-state New, Running, Completed,
and History surfaces were structurally checked against the DOCX reference
figures during the preceding acceptance pass; exact pixel identity is not
claimed because the reference captures and browser viewport dimensions
differ. LibreOffice/`soffice` remains unavailable, so the DOCX itself was not
re-rendered. No live provider was invoked.

### Scope boundary

No merge or branch-wide review was run. The branch is ready for the user's
separate re-review after the final commits are pushed.
