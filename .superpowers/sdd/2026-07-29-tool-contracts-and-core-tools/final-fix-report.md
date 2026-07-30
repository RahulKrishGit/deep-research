# Final Fix Wave Report

## Scope

Implemented only the two plan-aligned merge blockers:

1. Structured `ToolExecutionError.error_type` now flows to local tool metrics, completed span events, and remote span-completion metadata. Ordinary exceptions continue to use their exception class name.
2. `SaveToMemoryTool` and `QueryMemoryTool` now accept the required memory backend as the second positional constructor argument while retaining keyword compatibility.

The user-directed exclusions remain excluded: redirect-aware robots/SSRF hardening and response-size limits were not changed.

## TDD evidence

Tests were changed before the corresponding production code:

- `test_failing_execution_returns_recoverable_error_and_records_metric` now expects the `TimeoutError` semantic type and completed event metadata.
- `test_remote_tool_span_completion_preserves_structured_error_type` covers remote run completion metadata, local metric, and span-completion event metadata.
- Memory save/query delegation tests instantiate their tools with positional backends. Existing validation-metric expectations were corrected to the structured `ValidationError` contract.

Attempted the red focused command before production changes:

```text
python -m pytest tests/test_tools/test_base.py tests/test_tools/test_memory_tools.py -q
python : The term 'python' is not recognized as the name of a cmdlet, function, script file, or operable program.
```

Post-change focused/full pytest and Ruff could not be run for the same reason. `where.exe python`, `where.exe python3`, `where.exe py`, and `where.exe ruff` each reported no executable found. `git diff --check` exited successfully.

## Changed files

- `src/deep_research/observability/tracker.py`
- `src/deep_research/tools/memory_tools.py`
- `tests/test_observability_tracker.py`
- `tests/test_tools/test_base.py`
- `tests/test_tools/test_memory_tools.py`

## Self-review

- `_semantic_error_type` has a narrow fallback: only a non-empty string `error_type` overrides the exception class name.
- The helper is used for both local span finalization and remote completion metadata, so semantic types cannot diverge between those paths.
- Constructor keyword calls remain valid because `memory` remains a named parameter; removing the keyword-only marker additionally enables the planned positional API.
- No unrelated source files or the untracked plan document were touched.

## Constraints / concerns

Runtime verification is blocked by the absence of Python and Ruff executables on PATH. The next validation step is to run:

```text
python -m pytest tests/test_tools/test_base.py tests/test_tools/test_memory_tools.py tests/test_observability_tracker.py -q
python -m pytest -q
python -m ruff check src tests
```

## Correction: E305 spacing

Scoped re-review identified `E305` at `src/deep_research/observability/tracker.py:81`: the top-level `_SENSITIVE_TERMINAL_SEGMENTS` constant directly followed `_semantic_error_type`. Added the two required blank lines only and amended the fix-wave commit.

Static verification after the correction:

```text
git diff --check
git show --check --stat HEAD
```

Both commands exited successfully. Python and Ruff remain unavailable on PATH, so Ruff and pytest could not be run; the prior executable-discovery results remain applicable.

## Correction: focused test fixtures

Scoped re-review found two defects in the newly added test coverage; production behavior remained correct, so this correction changes tests and report only.

- Wrapped the remote tool-span regression in `tracker.session_span("session-1", "question")`, ensuring the `ToolExecutionError` is the failure being tested under a valid trace context.
- Replaced the fragile final-event assertion in the base-tool regression with a lookup for the completed tool span event. The enclosing session's successful completion is no longer mistaken for the tool completion event.
- Updated the remote test to use `RecordingTraceFactory` and identify the tool run explicitly, so the enclosing session's remote completion is not mistaken for the tool run when asserting remote metadata.

Attempted the focused post-correction test command:

```text
python -m pytest tests/test_tools/test_base.py tests/test_observability_tracker.py -q
python : The term 'python' is not recognized as the name of a cmdlet, function, script file, or operable program.
```

`git diff --check` and `git show --check --stat HEAD` remain the available static checks. Python and Ruff are still unavailable on PATH, preventing pytest and Ruff execution.
