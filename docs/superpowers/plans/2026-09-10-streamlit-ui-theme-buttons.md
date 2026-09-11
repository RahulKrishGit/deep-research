# Streamlit UI Theme and Button States Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Streamlit UI follow the browser/OS dark preference coherently while preserving the approved light canvas and finishing explicit button-state/readability coverage.

**Architecture:** Keep the native Streamlit shell and existing state flow. Extend the centralized `src/deep_research/ui/styles.py` token/CSS boundary with a dark media-query override and project-owned button states. Keep presentation markup semantic by moving the running-subtopic tint from inline HTML to a class in `src/deep_research/ui/components.py`; do not change action callbacks or session behavior.

**Tech Stack:** Python 3.11+, Streamlit 1.49+, static CSS, pytest/AppTest, Ruff, and the existing offline deterministic UI harness.

## Global Constraints

- Keep the light Editorial Research Canvas palette as the default.
- Select dark colors only through `@media (prefers-color-scheme: dark)`; add no manual toggle or session-state theme key.
- Keep all production changes in presentation/interaction affordances; do not change runner, controller, history, polling, providers, persistence, or research workflow behavior.
- Use native Streamlit controls, no JavaScript, no external fonts/assets, no new dependencies, and no generated Emotion-class selectors.
- Keep Streamlit `>=1.49`, Python `>=3.11`, existing canvas widths, radii, spacing rhythm, and no-routine-shadow rules.
- Every production behavior change has a test that fails before implementation and passes afterward.
- Verify light and dark at 1280x720, 768x1024, and 390x844; preserve the existing tablet CTA fix.

---

### Task 1: Add automatic dark tokens and explicit button states

**Files:**
- Modify: `src/deep_research/ui/styles.py` (`COLORS`, a dark token map, `STATIC_CSS`)
- Test: `tests/test_ui/test_styles.py`

**Interfaces:**
- Consumes: existing light `COLORS`, `STATIC_CSS`, native Streamlit 1.49 test-id/kind selectors.
- Produces: semantic light/dark variables for all theme-sensitive surfaces and explicit primary/secondary/link/disabled button state contracts consumed by the rendered UI and Task 2.

- [ ] **Step 1: Add failing static contracts.**

  Extend `tests/test_ui/test_styles.py` with focused tests that fail against the current light-only CSS:

  ```python
  def test_static_css_defines_light_and_dark_theme_contracts() -> None:
      css = styles.STATIC_CSS
      assert ".stApp" in css and "color-scheme: light;" in css
      assert "@media (prefers-color-scheme: dark)" in css
      for token in (
          "--dr-background: #0E1117",
          "--dr-surface: #171C22",
          "--dr-text: #F2F4F3",
          "--dr-text-muted: #A9B4BA",
          "--dr-border: #5F6C73",
          "--dr-active: #5CC8BE",
          "--dr-on-active: #0E1117",
      ):
          assert token in css
      dark_block = css[css.index("@media (prefers-color-scheme: dark)") :]
      assert "color-scheme: dark;" in dark_block

  def test_theme_sensitive_rules_use_semantic_variables() -> None:
      css = styles.STATIC_CSS
      for selector in (
          '[data-testid="stAppViewContainer"] header',
          ".dr-readonly-field",
          ".dr-control-label",
          ".dr-next-steps",
      ):
          assert selector in css
      assert "background: var(--dr-background);" in css
      assert "background: var(--dr-surface);" in css
      assert "color: var(--dr-text);" in css
      assert "border-bottom: 1px solid var(--dr-border);" in css

  def test_button_states_have_project_owned_theme_rules() -> None:
      css = styles.STATIC_CSS
      for state in (":hover", ":active", ":focus-visible"):
          assert state in css
      assert "--dr-active-hover" in css
      assert "--dr-active-pressed" in css
      assert "--dr-disabled-bg" in css
      assert "button[kind=\"secondary\"]:hover" in css
      assert "button:disabled" in css
  ```

  Add a small contrast helper over the exported light and dark token maps and
  assert body text, muted text, active/link text, success, warning, error, and
  primary button foreground meet the chosen AA threshold on their intended
  background. Update assertions that currently require theme-sensitive literal
  colors inside rule bodies so they require variables instead.

- [ ] **Step 2: Run the focused style tests and verify RED.**

  Run:

  ```text
  python -m pytest tests/test_ui/test_styles.py -q
  ```

  Expected: the new dark-contract/state assertions fail because the current
  CSS forces `color-scheme: light`, has no dark media block, and has no
  project-owned hover/pressed/disabled palette.

- [ ] **Step 3: Implement the minimal token/CSS contract.**

  Keep current light values and add these dark values:

  ```text
  background #0E1117       surface #171C22       text #F2F4F3
  text_muted #A9B4BA       border #5F6C73        active #5CC8BE
  active_tint #163A37      success #72D49A        success_tint #173826
  warning #F2C66D          warning_tint #3A2D12   error #FF8A80
  error_tint #431F1F
  ```

  Add auxiliary variables for both themes: `--dr-on-active`,
  `--dr-active-hover`, `--dr-active-pressed`, `--dr-focus`,
  `--dr-primary-focus`, `--dr-disabled-bg`, `--dr-disabled-text`, and
  `--dr-disabled-border`. Put the dark values in one
  `@media (prefers-color-scheme: dark)` token block and set `html`, `body`,
  and `.stApp` to `color-scheme: dark` there; retain the light base contract.
  Apply variables to body, canvas, header, sidebar, fields, read-only fields,
  labels, borders, selected surfaces, status tints, focus rings, links, and
  primary/secondary/link/disabled buttons. Define primary normal/hover/active,
  secondary normal/hover, visible focus, and disabled states with variables and
  keep the 44px minimum height. Treat the LangSmith link button as a secondary
  action using a stable Streamlit 1.49 test-id selector if browser inspection
  proves one is needed; do not use generated classes.

- [ ] **Step 4: Run the focused style tests and Ruff.**

  ```text
  python -m pytest tests/test_ui/test_styles.py -q
  python -m ruff check src/deep_research/ui/styles.py tests/test_ui/test_styles.py
  ```

  Expected: all style contracts pass with no lint errors.

- [ ] **Step 5: Commit the theme/button foundation.**

  ```text
  git add src/deep_research/ui/styles.py tests/test_ui/test_styles.py
  git commit -m "fix: add coherent streamlit dark theme"
  ```

### Task 2: Make running-state presentation theme-safe and preserve button behavior

**Files:**
- Modify: `src/deep_research/ui/components.py` (`_render_subtopic_sequence`)
- Test: `tests/test_ui/test_app.py`
- Depends on: Task 1 semantic tokens and `.dr-subtopic-row--running` CSS rule.

**Interfaces:**
- Consumes: `STATIC_CSS` theme variables and existing `UiSubTopicProgress` rendering.
- Produces: running-subtopic markup with no inline light colors and regression coverage that existing navigation/start actions remain wired.

- [ ] **Step 1: Add failing AppTests.**

  Add a running-state regression that finds the running subtopic Markdown and
  asserts it contains `dr-subtopic-row--running` but no `style=` attribute. Add
  or strengthen the existing New/History navigation test so clicking New
  Research clears a selected session/start error and returns the New view. Do
  not add duplicate AppTests for native hover, Deploy/menu, or browser-only
  increment/decrement behavior; those remain in the browser matrix.

- [ ] **Step 2: Run the focused AppTests and verify RED.**

  ```text
  python -m pytest tests/test_ui/test_app.py -k "running or new_research or history" -q
  ```

  Expected: the running-markup assertion fails because the current renderer
  emits an inline `style=` string; any navigation assertion must fail only if
  the existing contract is not already covered.

- [ ] **Step 3: Implement the minimal component change.**

  Remove the `COLORS`, `RADII`, and `SPACING`-assembled inline style from the
  running subtopic. Emit only the semantic class pair:

  ```python
  row_class = f"dr-subtopic-row dr-subtopic-row--{topic.status}"
  st.markdown(
      f'<div class="{row_class}">'
      f'<span class="dr-subtopic-icon" aria-hidden="true">{icon}</span>'
      f'<span class="dr-subtopic-title">{escape(topic.title)}</span>'
      f"</div>",
      unsafe_allow_html=True,
  )
  ```

  Put the running background/radius/padding in `STATIC_CSS` using semantic
  variables. Preserve all buttons, callbacks, keys, state transitions,
  disabled states, and session behavior exactly.

- [ ] **Step 4: Run focused tests, the UI suite, Ruff, and diff checks.**

  ```text
  python -m pytest tests/test_ui/test_app.py -k "running or new_research or history" -q
  python -m pytest tests/test_ui -q
  python -m ruff check .
  git diff --check
  ```

- [ ] **Step 5: Commit the component/test change.**

  ```text
  git add src/deep_research/ui/components.py tests/test_ui/test_app.py
  git commit -m "fix: make running streamlit state theme safe"
  ```

## Final Verification

- [ ] Run `python -m pytest tests/test_ui -q` and record the exact output.
- [ ] Run `python -m pytest -q` and record the exact output.
- [ ] Run `python -m ruff check .` and `git diff --check`.
- [ ] Run the production UI and deterministic harness from the exact branch
  head; inspect light and dark preferences at 1280x720, 768x1024, and 390x844.
- [ ] Exercise New Research, recent Open, Session history, Start Research,
  increment/decrement, History search/filter/Open, detail disclosures,
  LangSmith, native Deploy/menu, and Starting disabled controls. Confirm every
  visible product action has a working handler, readable label, obvious focus,
  and coherent normal/hover/pressed/disabled state where applicable.
- [ ] Review the final diff for accidental backend/session/provider changes.
