# Task 17 implementation report

## Changed files

- `src/deep_research/ui/components.py`
  - Removed the undocumented `max_chars=500` restriction from the New Research question field.
  - Shows `No source details recorded.` and `No claim details recorded.` for genuinely zero-count completed summaries.
  - Retains the local-history compaction wording when summary counts are nonzero but detail payloads are absent.
- `tests/test_ui/test_app.py`
  - Added an offline AppTest for a question longer than 500 characters reaching the controller intact.
  - Updated zero-count detail-copy coverage and added nonzero-count compacted-history coverage.
  - Removed the stale `streamlit>=1.37` compatibility assertion while retaining the `streamlit>=1.49` keyed-container contract checks.
- `README.md`
  - Updated the Phase 4 summary to mark the Streamlit UI complete.
- `docs/superpowers/plans/2026-09-08-streamlit-ui.md`
  - Recorded that the earlier “no `recent_activity`” rule was superseded by stopping-point remediation, while preserving the raw-data and secret minimization prohibitions.

The existing user change in `.superpowers/sdd/2026-09-08-streamlit-ui/progress.md` was not modified or staged.

## Test-first evidence

### Red

Command:

```text
python -m pytest tests/test_ui/test_app.py -k "longer_than_500 or reopened_quality_details" -v
```

Result: 2 failed, 1 passed, 97 deselected. The long-question test observed the old 500-character truncation, and the zero-count detail test observed the false local-history-retention copy. The compacted-history test passed against the existing behavior.

### Green

Command:

```text
python -m pytest tests/test_ui/test_app.py -k "longer_than_500 or reopened_quality_details" -v
```

Result: 3 passed, 97 deselected after the minimal implementation. The first post-implementation run exposed only a trailing-space mismatch caused by the existing form normalization; the test fixture was adjusted to use a cleanly terminated question, then the focused suite passed.

## Verification

- `python -m pytest tests/test_ui -v` — 202 passed, 2 skipped. The skipped tests are the existing Windows symlink cases.
- `python -m ruff check src/deep_research/ui/components.py tests/test_ui/test_app.py` — passed: `All checks passed!`
- `git diff --check` — passed with exit code 0. Git emitted only existing LF-to-CRLF working-copy warnings.
- No live providers, paid services, or network-dependent tests were run.

## Commits

- Implementation: `ff3d86106020fb62dceff09ce590fda23235e2fb` (`fix: remove streamlit question cap and clarify detail history`)
- This report is committed separately after the implementation commit.

## Visual/renderer limitation

No browser or visual renderer QA was run. Verification was offline and AppTest-driven as required; the UI changes are limited to question acceptance and completed-detail copy.
