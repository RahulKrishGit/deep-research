# Task 19: Browser-safe New Research submission

## Result

Fixed the New Research form so its Start Research button remains enabled whenever a start is not in flight. Blank or whitespace-only submissions now use the existing safe validation message and do not queue or start a controller run. Starting-state behavior remains unchanged and still disables the form to prevent duplicate submission.

## Files

- `src/deep_research/ui/components.py`
- `tests/test_ui/test_app.py`

## Verification

- Focused UI tests: `4 passed, 96 deselected`
- Ruff: `All checks passed!`
- `git diff --check`: passed; Git emitted only expected LF-to-CRLF working-tree warnings.

## Commit

`857ee01` — `fix: keep research submit available in browser`

## Caveat

The regression is covered through Streamlit AppTest's submit path. No live-provider run was performed.
