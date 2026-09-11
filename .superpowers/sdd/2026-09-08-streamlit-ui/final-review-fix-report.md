# Final whole-branch review fix wave report

Date: 2026-09-10
Worktree: `C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\streamlit-ui-polish`
Branch: `codex/streamlit-ui-polish`
Fix base: `44c6915838e3c9133d4e0bcd18b5c6bb30011cd1`

The target report file was absent at the start of this fix wave, so this file
contains the initial appended report entry.

## Scope

Implemented both Important findings from
`final-review-fix-brief.md` without changing the Streamlit UI, controller
lifecycle, history behavior, or unrelated provider behavior:

- OpenAI structured-output validation now projects only bounded schema paths
  and finite diagnostic categories. Repair instructions contain only the
  schema name plus that sanitized category/path summary. Both attempts are
  retained in `StructuredOutputError.diagnostics`, and the public error is
  raised outside the validation handler so provider-bearing exception context
  and prompt-bearing locals are not retained.
- ReAct iteration span outputs now contain metadata only: agent name,
  iteration, action, allow-listed tool identifier, success, bounded error
  type, and error count. Thought and observation prose remains in the local
  `ReActStep` and is absent from the span output and remote `run.end` payload.
  Tracker-side filtering provides a second remote-trace boundary.

## Test-first red evidence

The first focused run was discovered to import the package from an unrelated
editable Codex worktree. It was not used as repository evidence. The package
was reinstalled from the required checkout before the valid red run:

```text
python -m pip install -e ".[dev]"
Successfully built deep-research
Successfully installed deep-research-0.1.0
```

Import-path verification after installation:

```text
python -c "import deep_research.providers.openai_provider as m; print(m.__file__)"
C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\streamlit-ui-polish\src\deep_research\providers\openai_provider.py
```

Command run before production changes:

```text
python -m pytest -q tests/test_openai_provider.py::test_openai_structured_failure_keeps_bounded_diagnostics_without_provider_data tests/test_agents/test_react.py::test_each_iteration_emits_metadata_only_span_outputs_and_preserves_local_step tests/test_observability_tracker.py::test_remote_react_iteration_outputs_are_metadata_only
```

Red result:

```text
FFF                                                                      [100%]
3 failed in 1.75s
```

The failures were the intended ones: the OpenAI repair payload contained the
sentinel through the validation string, ReAct span output contained thought and
observation sentinels, and the remote `run.end` output forwarded those fields.

## Implementation and green evidence

Focused regression tests after the fix:

```text
python -m pytest -q tests/test_openai_provider.py::test_openai_structured_failure_drops_provider_data tests/test_agents/test_react.py::test_each_iteration_emits_metadata_only_span_outputs_and_preserves_local_step tests/test_observability_tracker.py::test_remote_react_iteration_outputs_are_metadata_only
...                                                                      [100%]
3 passed, 1 warning in 1.65s
```

Focused provider/agent/tracker files:

```text
python -m pytest -q tests/test_openai_provider.py tests/test_agents/test_react.py tests/test_observability_tracker.py
129 passed, 1 warning in 1.70s
```

The warning in both focused runs is the existing LangSmith dependency warning
about `ast.Str` deprecation.

Full UI suite:

```text
python -m pytest -q tests/test_ui
231 passed, 2 skipped in 39.54s
```

Complete offline repository suite (the repository default excludes `live`):

```text
python -m pytest -q
2117 passed, 2 skipped, 1 deselected, 2 warnings in 67.10s (0:01:07)
```

The two full-suite warnings are the existing LangSmith `ast.Str` deprecation
warning and Starlette's existing `httpx`/`httpx2` deprecation warning.

Ruff:

```text
ruff check src tests
All checks passed!
```

Whitespace check:

```text
git diff --check
exit code 0
```

Git printed normal LF-to-CRLF working-copy conversion warnings for changed
Python files; no whitespace errors were reported.

No paid OpenAI, DeepSeek, or LangSmith provider call was made. All tests used
the repository's existing SDK fakes and local trace fakes.

## Changed files

- `src/deep_research/providers/contracts.py` — expanded the shared finite
  structured-diagnostic category contract.
- `src/deep_research/providers/validation.py` — added the bounded provider-
  neutral validation projection and safe repair-summary renderer.
- `src/deep_research/providers/openai_provider.py` — replaced raw validation
  text retention with typed diagnostics, sanitized repair guidance, two-attempt
  accumulation, and safe final error propagation.
- `src/deep_research/agents/react.py` — removed thought/observation prose from
  iteration span outputs while preserving local ReAct steps.
- `src/deep_research/observability/tracker.py` — filtered remote ReAct outputs
  to the metadata allow-list before `run.end`.
- `tests/test_openai_provider.py` — adversarial sentinel, diagnostic,
  two-attempt, and evaluation-projection regression coverage.
- `tests/test_agents/test_react.py` — sentinel coverage for local step
  preservation and metadata-only iteration outputs.
- `tests/test_observability_tracker.py` — captured remote run-end payload
  coverage for ReAct output filtering.

## Commit SHA

- `d93faeb` — `fix: sanitize structured diagnostics and react traces`

The report entry is committed separately after this implementation/test
commit.

## Concerns

- No functional concerns remain from the requested offline verification.
- Existing third-party deprecation warnings remain as noted above; they are
  outside this fix wave.
- Live provider behavior was intentionally not exercised because the brief
  prohibits paid calls.
