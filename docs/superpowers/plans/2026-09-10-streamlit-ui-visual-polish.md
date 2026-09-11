# Streamlit UI Visual Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the merged Streamlit research UI readable, well-spaced, and visually calm across its New, Starting, Running, Completed, Incomplete/Failed, and History states without changing research behavior.

**Architecture:** Keep the existing native Streamlit layout and state flow. Concentrate visual rules in `styles.py`, use small semantic presentation classes in `components.py`, and extend existing AppTest/static-CSS contracts rather than adding a new design system or frontend layer.

**Tech Stack:** Python 3.11+, Streamlit 1.49+, native Streamlit widgets, static CSS, pytest/AppTest, Ruff, and the existing offline deterministic UI harness.

**Spec:** `docs/superpowers/plans/deep-research-streamlit-ui-design-handoff.docx` plus the Sol/High visual review recorded in this plan's execution brief.

## Global Constraints

- Change presentation and interaction affordances only; do not change runner, history persistence, models, polling/session lifecycle, orchestration, providers, or backend contracts.
- Preserve Streamlit `>=1.49`, Python `>=3.11`, native controls, and the existing 1120px canvas, 720–820px editorial width, palette, radii, and no-routine-shadow rule.
- Do not add JavaScript, external fonts, network assets, animation, dependencies, or brittle custom responsive routing.
- Every production change must have a test that fails before the implementation and passes afterward.
- Preserve semantic status words/icons, safe error text, atomic form validation, selected-session behavior, and existing History/report information architecture.
- Verify the browser at 1280x720, 768x1024, 390x844, and 200% zoom; use the production app for New/Starting and the offline harness for deterministic terminal states.

---

### Task 1: Establish readable visual tokens and native chrome treatment

**Files:**
- Modify: `src/deep_research/ui/styles.py` (`STATIC_CSS`)
- Test: `tests/test_ui/test_styles.py`

**Interfaces:**
- Consumes: existing CSS token values and Streamlit 1.49 DOM/test-id selectors.
- Produces: stable `.dr-control-label`, `.dr-readonly-field`, `.dr-readonly-meta`, `.dr-screen-eyebrow`, `.dr-subsection-heading`, `.dr-next-steps`, focus, disabled-control, and header/chrome rules consumed by Tasks 2–3.

- [ ] **Step 1: Write failing CSS contract tests.**

  Extend `tests/test_ui/test_styles.py` with assertions that `STATIC_CSS` contains readable native-header foreground/background rules, desktop/tablet/mobile top padding (`64px`, `56px`, `48px`), visible `:focus-visible` rules with the teal ring and dark primary-button outline, explicit disabled-control rules with `opacity: 1`, read-only surface rules, and the new semantic spacing classes. Keep the existing assertions forbidding JavaScript, animation, external fonts, routine shadows, and network assets.

- [ ] **Step 2: Run the style tests and verify the new assertions fail for the missing selectors/rules.**

  Run:

  ```text
  python -m pytest tests/test_ui/test_styles.py -q
  ```

  Expected: failure naming the missing header, focus, disabled, read-only, and spacing rules; existing style tests must still collect normally.

- [ ] **Step 3: Implement the minimum shared CSS.**

  In `STATIC_CSS`, add the following behavior using the existing palette:

  ```css
  /* Native Streamlit chrome: keep Deploy/menu visible and readable. */
  [data-testid="stAppViewContainer"] header,
  [data-testid="stHeader"] {
    background: #FCFCFA;
    color: #172126;
    border-bottom: 1px solid #DCE2DF;
  }

  [data-testid="stHeader"] button,
  [data-testid="stHeader"] svg {
    color: #172126;
    fill: #172126;
    opacity: 1;
  }

  [data-testid="stMainBlockContainer"] {
    padding-top: 64px;
  }

  .dr-control-label { margin: 0 0 8px; color: #172126; font-size: 13px; font-weight: 600; line-height: 1.35; }
  .dr-readonly-field { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 44px; padding: 10px 12px; background: #F5F7F6; border: 1px solid #DCE2DF; border-radius: 6px; color: #172126; }
  .dr-readonly-meta { color: var(--dr-text-muted); font-size: 12px; }
  .dr-screen-eyebrow { margin: 0 0 8px; }
  .dr-subsection-heading { margin: 0; }
  .dr-next-steps { margin-top: 32px; padding-top: 16px; border-top: 1px solid #DCE2DF; }
  .dr-next-steps > div { padding-top: 8px; }

  button:focus-visible, a:focus-visible, input:focus-visible,
  textarea:focus-visible, [role="radio"]:focus-visible {
    outline: 3px solid #0F6F68;
    outline-offset: 2px;
  }

  button[kind="primary"]:focus-visible {
    outline-color: #172126;
  }

  input:disabled, textarea:disabled, button:disabled {
    opacity: 1;
    cursor: not-allowed;
  }

  @media (max-width: 900px) { [data-testid="stMainBlockContainer"] { padding-top: 56px; } }
  @media (max-width: 640px) { [data-testid="stMainBlockContainer"] { padding-top: 48px; } }
  ```

  Use the repository's existing selectors/tokens where they are more stable; the required visual outcomes and Streamlit 1.49 compatibility take precedence over copying the snippet literally.

- [ ] **Step 4: Run the style tests and Ruff.**

  ```text
  python -m pytest tests/test_ui/test_styles.py -q
  python -m ruff check src/deep_research/ui/styles.py tests/test_ui/test_styles.py
  ```

  Expected: all style tests pass and Ruff reports no issues.

- [ ] **Step 5: Commit the shared visual foundation.**

  ```text
  git add src/deep_research/ui/styles.py tests/test_ui/test_styles.py
  git commit -m "fix: improve streamlit visual contrast and focus"
  ```

### Task 2: Fix New/Starting form hierarchy and next-step spacing

**Files:**
- Modify: `src/deep_research/ui/components.py` (`_render_new_research_content` and adjacent presentation helpers)
- Test: `tests/test_ui/test_app.py` (existing New/Starting tests, plus focused regressions)

**Interfaces:**
- Consumes: Task 1 CSS classes; existing form keys and atomic-submit behavior.
- Produces: clearly labeled Research question, Maximum iterations, and Output format fields; a visibly fixed Markdown surface; structurally separated next-step content; unchanged submission state machine.

- [ ] **Step 1: Write failing AppTests for the visual contracts.**

  Add or update focused tests in `tests/test_ui/test_app.py` to assert that the New screen emits visible `Research question`, `Maximum iterations`, and `Output format` labels; the fixed output surface includes `Markdown` and `Fixed`/read-only copy without creating an editable widget; the next-step heading and first step are separate blocks; and Starting keeps the question/max-iteration controls readable but disabled while showing `Preparing research plan` and preventing duplicate submission. Keep blank validation and Markdown forwarding assertions unchanged.

- [ ] **Step 2: Run the focused AppTests and verify the new assertions fail.**

  ```text
  python -m pytest tests/test_ui/test_app.py -k "new_research or starting or next_step or output_format" -q
  ```

  Expected: failures for the missing explicit labels, fixed affordance, or separated next-step blocks; no unrelated test collection errors.

- [ ] **Step 3: Implement the minimum presentation-only markup.**

  In `_render_new_research_content()`:

  - Keep the native widget labels semantically present with `label_visibility="collapsed"` and render one visible `.dr-control-label` immediately above each field.
  - Replace the input-like Markdown box with a non-interactive `.dr-readonly-field` containing `Markdown` and `.dr-readonly-meta` text such as `Fixed` or `Read-only`; expose the fixed meaning in the visible text and accessible label where the existing markup helper supports it.
  - Keep the maximum-iterations number input in its existing form/key/range and place it in the same two-column row as the fixed output surface so both labels and controls share the same top baseline.
  - Render the next-step divider/eyebrow, heading, and three step columns in separate containers using `.dr-next-steps`; do not emit one raw HTML/Markdown block that combines the heading and first step.
  - Preserve the existing form submit button, pending request, Starting rerun, duplicate prevention, and validation behavior exactly.

- [ ] **Step 4: Run the focused AppTests and Ruff to verify the implementation.**

  ```text
  python -m pytest tests/test_ui/test_app.py -k "new_research or starting or next_step or output_format" -q
  python -m ruff check src/deep_research/ui/components.py tests/test_ui/test_app.py
  ```

  Expected: focused tests pass and Ruff reports no issues.

- [ ] **Step 5: Commit the form and next-step fix.**

  ```text
  git add src/deep_research/ui/components.py tests/test_ui/test_app.py
  git commit -m "fix: clarify streamlit research form hierarchy"
  ```

### Task 3: Harmonize cross-state rhythm, reduce noise, and expand visual harness coverage

**Files:**
- Modify: `src/deep_research/ui/components.py` (`_render_running_snapshot`, `_render_subtopic_sequence`, `_render_recent_activity`, `_render_stopping_point`, `_render_retained_progress_snapshot`, `_render_completed_report_body`, `_render_quality_rows`, `_render_history_row`, and `render_history_view`)
- Modify: `tests/test_ui/test_app.py`
- Modify: `tests/test_ui/manual_mock_app.py`
- Modify: `tests/test_ui/fakes.py`

**Interfaces:**
- Consumes: Task 1 shared eyebrow/subsection/focus/disabled rules and Task 2 form layout.
- Produces: consistent readable hierarchy across Running, Completed, Incomplete/Failed, and History, with deterministic browser-selectable Starting/no-report scenarios and no workflow changes.

- [ ] **Step 1: Write failing cross-state and harness tests.**

  Extend existing AppTests rather than duplicating state fixtures to assert that Running, Completed, Incomplete, Failed, and History use the shared semantic rhythm classes; Completed no longer shows a duplicate report-path code block on the main canvas while Session metadata retains the path; no-report Incomplete hides the quality rail; long questions/history rows/report content remain representable without horizontal overflow; and the deterministic harness exposes Starting, Incomplete/no-report, and Failed/no-report modes for browser verification.

- [ ] **Step 2: Run the focused cross-state tests and verify the new assertions fail.**

  ```text
  python -m pytest tests/test_ui/test_app.py -k "running or completed or incomplete or failed or history or harness" -q
  ```

  Expected: failures identify the duplicated report path, missing shared classes, or missing deterministic modes; existing behavior tests must still collect normally.

- [ ] **Step 3: Implement the minimum cross-state presentation changes.**

  - Apply `.dr-screen-eyebrow` to the top state label in Running, Completed, Incomplete, and Failed views, and `.dr-subsection-heading` to sequence/activity/stopping-point/quality headings without globally changing sidebar spacing.
  - Keep the current semantic status text, icons, selected-row cue, quality meanings, history ordering, search, filters, Open actions, and report content intact.
  - Remove only the duplicate main-canvas report-path code block from `_render_completed_report_body()`; retain the path in the collapsed Session metadata rail.
  - Add deterministic harness selectors for Starting, Incomplete without a report, and Failed without a report using the existing `DemoController` snapshot machinery; do not add provider calls or alter production controller state.
  - Ensure responsive CSS uses native Streamlit stacking at 900px/640px breakpoints and keeps long text wrapping within its column.

- [ ] **Step 4: Run the complete UI suite and lint.**

  ```text
  python -m pytest tests/test_ui -q
  python -m ruff check .
  python -m pytest -q
  python -m ruff check .
  git diff --check
  ```

  Expected: all tests pass, Ruff passes, and `git diff --check` is clean.

- [ ] **Step 5: Verify the rendered UI in the browser.**

  - Run the production app for New and Starting at `http://127.0.0.1:8503`.
  - Run the offline harness for New, Starting, Running, Completed, Incomplete/no-report, Failed/no-report, and History.
  - Inspect each at 1280x720, 768x1024, 390x844, and 200% zoom.
  - Confirm: native Deploy/menu controls are readable; there is clear space below the chrome; every editable field is labeled; Markdown is visibly fixed/read-only; no heading or step overlaps; focus rings are obvious; disabled controls remain legible; Running/Completed/terminal rails do not clip; History search/filter/selection/Open behavior remains usable; long content wraps without horizontal overflow.

- [ ] **Step 6: Commit the cross-state polish.**

  ```text
  git add src/deep_research/ui/components.py tests/test_ui/test_app.py tests/test_ui/manual_mock_app.py tests/test_ui/fakes.py
  git commit -m "fix: polish streamlit state presentation"
  ```

## Final Verification

- [ ] Run `python -m pytest tests/test_ui -q` and record the exact result.
- [ ] Run `python -m pytest -q` and record the exact result.
- [ ] Run `python -m ruff check .` and `git diff --check`.
- [ ] Reopen the live production UI and deterministic harness; inspect all target viewports/states listed in Task 3.
- [ ] Review the final diff for accidental backend/session/provider changes.
- [ ] Request a Luna Max task-scoped review of each task's diff before proceeding to the next task and a final follow-up review before pushing.
