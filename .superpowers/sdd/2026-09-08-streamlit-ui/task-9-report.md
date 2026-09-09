# Task 9 Report — Searchable local Session History

Date: 2026-09-09
Branch: `codex/streamlit-ui`
Worktree: `.worktrees/streamlit-ui`

## Scope

Implemented Task 9 only. Changed:

- `src/deep_research/ui/components.py`
- `src/deep_research/ui/app.py`
- `tests/test_ui/test_app.py`

No engine, provider, graph, persistence-contract, or later Task 10 files were changed.

## Implementation

- Replaced the history-route scaffold with the Figure 4 editorial archive:
  `Research sessions`, quiet session count, search input, compact status filter,
  `QUESTION / NEWEST FIRST` header, native row containers, hairline dividers,
  and one explicit `Open` action per row.
- Preserved full question text, newest-first ordering, and wrapped question-led
  row hierarchy. Search is a case-insensitive question substring match.
- Added `All`, `Running`, `Completed`, and `Issues` filters. `Issues` includes
  `max_iterations`, `incomplete`, and `failed` exactly as specified.
- Added explicit visible status shape/wording through the existing status
  presenter. Persisted `running` entries are normalized to `Incomplete` unless
  the session is active in the current in-memory Streamlit session.
- Added selected-row styling through the existing native container mechanism and
  a visible `Selected session` cue that does not depend on color.
- Added safe historical reopen behavior. An active session uses its controller
  snapshot; a historical session is reconstructed from compact metadata and its
  report is read through `read_history_report`. The history Open action never
  calls `start()` and therefore cannot rerun a report.
- Preserved same-shell navigation: selecting a history row moves to `current`,
  leaves the persistent sidebar in place, and the existing `Session history`
  action returns to the archive with selection retained.
- Tightened divider spacing after offline visual review so the archive matches
  Figure 4’s compact row rhythm while retaining native Streamlit dividers.

## TDD evidence

RED was run before the implementation:

```text
python -m pytest tests/test_ui/test_app.py -k "history or search or filter" -v
```

The pre-implementation run collected 13 selected tests, with 7 expected
feature-missing failures: the route was still the scaffold, and the history
controls/rows/open actions did not exist. Existing unrelated navigation tests
continued to pass.

GREEN was then verified with the same focused command. The final focused run
passed all 13 selected tests, with 35 tests deselected.

## Verification

Focused Task 9 AppTests:

```text
13 passed, 35 deselected in 5.68s
```

Full UI suite:

```text
126 passed, 2 skipped in 18.33s
```

The two skipped tests are the existing Windows symlink-permission cases in the
history-store suite; they are unrelated to Task 9 UI behavior.

Full offline repository suite, with this worktree’s `src` first on
`PYTHONPATH`:

```text
PYTHONPATH=<worktree>\src python -m pytest -q
2007 passed, 2 skipped, 1 deselected, 2 warnings in 45.31s
```

The warnings are existing dependency deprecations from LangSmith and FastAPI;
no live-provider test was run.

Ruff:

```text
python -m ruff check .
All checks passed!
```

Diff whitespace check:

```text
git diff --check
RESULTS: diff=0
```

## Offline Figure 4 visual comparison

Compared the local reference image at:

`.superpowers/sdd/2026-09-08-streamlit-ui/evidence/docx-unzipped/word/media/image4.png`

against a temporary evidence-only Streamlit harness populated with deterministic
local history entries. The review verified:

- persistent left identity/sidebar shell;
- editorial `Research sessions` heading and quiet count;
- search/filter row with no live calls;
- question-leading wrapped rows;
- explicit status word plus shape/icon and semantic color;
- subordinate `Open` actions;
- hairline-separated archive rather than a dataframe, grid, or card wall;
- restrained selected-row treatment with a non-color cue;
- stale persisted running entry displayed as `Incomplete` in the absence of an
  active controller record.

The first screenshot comparison identified excessive native divider margins;
the divider treatment was tightened and the updated screenshot was recaptured.
The temporary harness was removed and is not part of the product changes.

## Commit

`746c1c1 feat: add searchable streamlit session history`

## Fix round 1 — Luna Max findings

Review findings addressed without expanding into Task 10:

1. **P1 selected-row styling hook:** history rows retain the keyed native
   containers `dr-history-row-*` / `dr-history-row-selected-*`; the centralized
   CSS now targets both history and sidebar row key families. A regression test
   asserts the selected history container and matching CSS hook.
2. **P2 compact filter:** the history screen now uses `st.segmented_control`
   when available. A narrowly scoped `getattr`/callability fallback retains the
   same `selectbox` values only for supported Streamlit installations that do
   not expose the newer widget. Existing tests exercise the exact All/Running/
   Completed/Issues semantics through the segmented control.
3. **P3 traceability:** the original commit reference is corrected to the
   reviewed base commit `746c1c1`; the fix-round commit is listed below after
   commit creation.

### Fix-round TDD evidence

RED was run after adding the regression expectations and before the fixes:

```text
python -m pytest tests/test_ui/test_app.py -k "history or search or filter" -v
3 expected failures, 10 passed, 35 deselected
```

The failures were the missing segmented-control widget and missing
`st-key-dr-history-row-selected-` CSS hook.

GREEN was then verified with the same command:

```text
13 passed, 35 deselected
```

### Fix-round verification

- Focused history/search/filter AppTests: `13 passed, 35 deselected`.
- Full UI suite: `126 passed, 2 skipped`.
- Full offline repository suite with this worktree `src` first on
  `PYTHONPATH`: `2007 passed, 2 skipped, 1 deselected, 2 warnings`.
- `python -m ruff check .`: `All checks passed!`.
- `git diff --check`: clean.
- Streamlit runtime check: `1.63.0`; `st.segmented_control` available.
- Offline Figure 4 comparison recaptured after both fixes. All four filter
  options are visible at the desktop width, the selected history row has the
  pale-teal treatment and visible selected cue, and the archive remains
  question-led with divider-separated native rows. No live provider was used.

### Actual commit trace

- Base Task 9 implementation: `746c1c1 feat: add searchable streamlit session history`.
- Fix round 1: `1931ebe fix: align session history review findings`.
