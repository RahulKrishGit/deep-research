# Task 15 implementation report

## Change

Restored persisted historical telemetry when `_snapshot_for_selection()`
reconstructs a `UiSessionSnapshot` after a controller restart. The
reconstruction now carries both `SessionHistoryEntry.token_usage` and
`SessionHistoryEntry.trace_url`, while the existing `None` defaults preserve
omission semantics when telemetry is unavailable.

## Changed files

- `src/deep_research/ui/components.py`
  - Added `token_usage=entry.token_usage` and `trace_url=entry.trace_url` to
    historical snapshot reconstruction.
- `tests/test_ui/test_app.py`
  - Added an offline Streamlit `AppTest` regression using
    `RestartedFakeController`, persisted nonzero token usage, and a valid
    trace URL; the reopened completed view asserts both telemetry elements.
- `.superpowers/sdd/2026-09-08-streamlit-ui/task-15-report.md`
  - This implementation report.

## Red-green evidence

1. Added the regression before changing production code.
2. Red run:

   ```text
   python -m pytest tests/test_ui/test_app.py::test_restarted_history_reopens_persisted_telemetry_in_completed_view -q
   F                                                                        [100%]
   1 failed in 2.05s
   ```

   The failure was the expected missing `app.metric[0]`: historical
   reconstruction omitted persisted telemetry.

3. Added the two-field production fix.
4. Green run:

   ```text
   python -m pytest tests/test_ui/test_app.py::test_restarted_history_reopens_persisted_telemetry_in_completed_view -q
   .                                                                        [100%]
   1 passed in 1.85s
   ```

## Verification

- `python -m pytest tests/test_ui/test_app.py tests/test_ui/test_models.py tests/test_ui/test_history.py -q`
  - `137 passed, 2 skipped in 37.41s`
- `python -m ruff check src/deep_research/ui/components.py tests/test_ui/test_app.py`
  - `All checks passed!`
- `git diff --check`
  - Passed with no whitespace errors.
- No live providers, network calls, or paid services were used.

## Commits

- Base: `42623fe497b7c572453f75a25c47c651163e33c6`
- Implementation and regression: `fe9bfbc8d35601df5b63fe8d15179db744aee867`

## Remaining concerns

None identified for the scoped task. The pre-existing modification to
`.superpowers/sdd/2026-09-08-streamlit-ui/progress.md` was preserved and not
included in the task commits.
