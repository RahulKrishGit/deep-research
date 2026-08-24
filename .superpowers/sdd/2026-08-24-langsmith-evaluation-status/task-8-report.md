# Task 8 report: publish evaluation status metadata

## Status

Implemented Task 8 in `src/deep_research/evaluation/runner.py` only. The
change uses the public `evaluation_results.experiment_id` and the original
injected LangSmith client to read project metadata, preserve unrelated keys,
replace only `evaluation_status` and `evaluation_failure_reason`, and update
the project after successful evaluation.

## Production changes

- Added `build_evaluation_status_values`; summary feedback delegates to this
  shared status/reason projection.
- Added pure `merge_evaluation_status_metadata` and
  `publish_evaluation_status_metadata` helpers.
- Added a separate, secret-safe metadata publication error list. Missing
  experiment IDs and read/update failures become
  `trace:langsmith_project_metadata_unavailable` errors without affecting
  `decide_status`.
- Kept URL extraction, summary upload, local result metadata, optional
  `langsmith_client=None`, gates, thresholds, exit behavior, row feedback,
  artifacts, project names, and endpoint behavior unchanged.
- Failure-reason selection checks typed repetition errors before gate-only
  failures so a provider failure is not obscured by unrelated offline trace
  availability noise.

## Verification

All verification was offline and fake-driven. No live LangSmith call or model
provider call was made; no DeepSeek runtime was used.

- Focused metadata/status tests: `4 passed, 36 deselected`
- Runner tests: `40 passed, 1 warning`
- Ruff: `All checks passed!`
- `git diff --check`: passed; Git emitted only its normal LF/CRLF conversion
  warning for the edited Python file.

## Review notes

The existing test environment emitted one dependency deprecation warning from
LangSmith (`ast.Str` on Python 3.14); it is outside this change. The report
does not include credentials, raw provider errors, or hidden reasoning.

## UUID review-fix evidence (2026-08-24)

- Root cause reproduced offline: a UUID-valued fake
  `evaluation_results.experiment_id` was rejected before `read_project`, so
  the result recorded `langsmith_project_metadata_unavailable` and skipped the
  UI metadata update.
- Updated `runner.py` to accept the public SDK's `str | UUID` experiment ID
  shape and pass it through unchanged to the public `read_project` and
  `update_project` calls. No URL parsing or private fields are used.
- Added `test_project_metadata_accepts_uuid_experiment_id`; the existing
  string-ID merge test remains unchanged.
- Focused metadata tests: `5 passed, 36 deselected, 1 warning`.
- Runner tests: `41 passed, 1 warning`.
- Ruff: `All checks passed!`.
- `git diff --check`: passed; Git emitted only its normal LF/CRLF conversion
  warnings for the edited files.
- Verification remained offline and fake-driven. No live LangSmith or model
  provider call was made; no DeepSeek runtime was used.
