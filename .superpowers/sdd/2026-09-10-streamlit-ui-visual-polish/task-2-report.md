# Task 2 implementation report

Date: 2026-09-10
Task: Fix New/Starting form hierarchy and next-step spacing
Worktree: `codex/streamlit-ui-polish`

## Files changed

- `src/deep_research/ui/components.py`
  - Added visible `.dr-control-label` markup above the research question,
    maximum iterations, and output format controls.
  - Kept the native text area and number input labels semantically present with
    `label_visibility="collapsed"`, preserving their existing keys, ranges,
    form, and disabled state.
  - Replaced the output-format input-like presentation with a non-interactive
    `.dr-readonly-field` that visibly identifies Markdown as `Fixed · read-only`
    and exposes the same meaning through its accessible label.
  - Separated the next-step eyebrow, subsection heading, and three step columns
    inside the keyed `dr-next-steps` container while retaining the existing
    content.
- `tests/test_ui/test_app.py`
  - Added focused assertions for visible control labels, the fixed Markdown
    surface, the absence of an editable output-format widget, and separated
    next-step structure.
  - Extended the Starting regression to assert readable labels and no queued or
    duplicate controller start.
- `.superpowers/sdd/2026-09-10-streamlit-ui-visual-polish/task-2-report.md`
  - This report.

## TDD evidence

### Red

Command:

```text
python -m pytest tests/test_ui/test_app.py -k "new_research or starting or next_step or output_format" -q
```

Outcome: failed as expected with 2 failures and 6 passed, 97 deselected.
The failures were the missing `.dr-control-label` markup and missing
`.dr-next-steps` structure. Test collection completed without unrelated errors.

### Green

Command:

```text
python -m pytest tests/test_ui/test_app.py -k "new_research or starting or next_step or output_format" -q
```

Outcome: `8 passed, 97 deselected in 4.77s`.

Additional regression command:

```text
python -m pytest tests/test_ui/test_app.py -q
```

Outcome: `105 passed in 33.37s`.

## Verification

Command:

```text
python -m ruff check src/deep_research/ui/components.py tests/test_ui/test_app.py
```

Outcome: `All checks passed!`

Command:

```text
git diff --check
```

Outcome: no whitespace errors. Git emitted only LF-to-CRLF working-copy
normalization warnings for the two modified source/test files.

## Concerns

No functional concerns. The implementation is presentation-only and preserves
the atomic form, existing keys and ranges, blank validation, pending-request
and Starting flow, duplicate prevention, Markdown forwarding, and backend/session
behavior. The repository's existing Windows line-ending normalization warnings
remain when Git inspects the working copy.
